#!/usr/bin/env node
/* Destructive UI acceptance restricted to the tagged, fake fixture on port 8766. */
'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const base = (process.env.TEST_CATALOG_URL || 'http://127.0.0.1:8766').replace(/\/$/, '');
const endpoint = new URL(base);
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname));
assert.equal(endpoint.port, '8766', 'Permanent deletion tests are restricted to disposable port 8766');
assert.ok(process.env.TEST_SOURCE_DIR, 'TEST_SOURCE_DIR must point to a fixture created by create-permanent-fixture.py');
const source = fs.realpathSync(process.env.TEST_SOURCE_DIR);
const root = path.dirname(source);
const tempRoots = [...new Set([os.tmpdir(), '/tmp'].filter(candidate => fs.existsSync(candidate)).map(candidate => fs.realpathSync(candidate)))];
assert.ok(tempRoots.some(candidate => root.startsWith(candidate + path.sep)), 'Source must resolve inside a temporary directory, never the real collection');
assert.ok(path.basename(root).startsWith('youtube-catalog-permanent-') && path.basename(source) === 'sources', 'Unrecognized disposable fixture path');
const marker = JSON.parse(fs.readFileSync(path.join(root, '.permanent-test-fixture.json'), 'utf8'));
assert.equal(marker.purpose, 'youtube-catalog-permanent-deletion-test');
assert.equal(marker.version, 1);
assert.equal(fs.realpathSync(marker.source), source);
assert.equal(marker.remove_id, 'permanent_remove');
assert.equal(marker.keep_id, 'permanent_keep');
const targetId = marker.remove_id;
const keepId = marker.keep_id;
for (const id of [targetId, keepId]) {
  const folder = path.join(source, id);
  assert.ok(fs.existsSync(folder), 'Use a fresh fixture; the prior test may already have removed its target');
  assert.equal(fs.realpathSync(folder), folder, 'Fixture videos cannot be symlinks');
  const metadata = JSON.parse(fs.readFileSync(path.join(folder, 'video.json'), 'utf8'));
  assert.equal(metadata.id, id);
  assert.equal(metadata.title, marker.videos[id]);
  assert.ok(metadata.title.startsWith('[TESTE DESCARTÁVEL]'));
}
assert.deepEqual(fs.readdirSync(source).sort(), [keepId, targetId].sort(), 'Fixture must contain only the two explicitly disposable videos');

const externalRequests = [];
const pageErrors = [];
const forbiddenRequests = [];
const permanentRequests = [];
const passed = [];
let removed = false;
let browser;

function treeManifest(directory) {
  const result = {};
  function visit(current) {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const file = path.join(current, entry.name);
      assert.ok(!entry.isSymbolicLink(), 'Disposable fixture must not contain symlinks');
      if (entry.isDirectory()) visit(file);
      else if (entry.isFile()) result[path.relative(directory, file)] = crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
    }
  }
  visit(directory);
  return result;
}
async function api(route, method = 'GET', body) {
  assert.ok(method === 'GET' || (method === 'POST' && (route === '/scan' || route === `/videos/${targetId}/restore`)), 'Unexpected direct API mutation');
  const response = await fetch(base + '/api' + route, { method, ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
  assert.ok(response.ok, `${method} ${route}: ${response.status}`);
  return response.json();
}
async function poll(condition, message, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await condition()) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.fail(message);
}
async function step(name, work) {
  await work(); passed.push(name); console.log(`OK     ${name}`);
}

(async () => {
  const originals = treeManifest(source);
  const keepOriginals = treeManifest(path.join(source, keepId));
  const initial = await api('/stats');
  const active = await api('/videos?page_size=100');
  assert.deepEqual(active.items.map(video => video.id).sort(), [keepId, targetId].sort(), 'Server must be connected to exactly this disposable fixture');
  assert.equal(initial.videos, 2); assert.equal(initial.deleted_videos, 0);
  const target = await api(`/videos/${targetId}`);
  const kept = await api(`/videos/${keepId}`);
  const keptSegments = await api(`/videos/${keepId}/segments`);
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 }, reducedMotion: 'reduce', locale: 'pt-BR' });
  await context.route('**/*', route => {
    const request = route.request(); const url = new URL(request.url());
    if (/^https?:$/.test(url.protocol) && url.origin !== endpoint.origin) {
      externalRequests.push(request.url()); return route.abort('blockedbyclient');
    }
    if (url.pathname.startsWith('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      const allowed = request.method() === 'DELETE' && [
        `/api/videos/${targetId}`, `/api/videos/${targetId}/permanent`,
      ].includes(url.pathname);
      if (!allowed) { forbiddenRequests.push(`${request.method()} ${request.url()}`); return route.abort('blockedbyclient'); }
      if (url.pathname.endsWith('/permanent')) permanentRequests.push(request.postDataJSON());
    }
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  page.on('pageerror', error => pageErrors.push(error.message));
  const dialog = () => page.getByRole('dialog', { name: 'Excluir definitivamente?', exact: true });
  const trigger = () => page.getByRole('button', { name: `Excluir definitivamente: ${target.title}`, exact: true });
  const confirm = () => dialog().getByRole('button', { name: 'Excluir definitivamente', exact: true });
  const field = () => page.locator('#permanent-confirmation');
  try {
    await step('somente a lixeira oferece exclusão definitiva', async () => {
      await page.goto(`${base}/#/video/${targetId}`);
      await page.getByRole('heading', { name: target.title, exact: true }).waitFor();
      assert.equal(await page.locator('.permanent-delete-trigger').count(), 0);
      await page.locator('.delete-trigger').click();
      const softDialog = page.getByRole('dialog', { name: 'Excluir do catálogo?', exact: true });
      await softDialog.getByRole('button', { name: 'Excluir do catálogo', exact: true }).click();
      await poll(async () => (await api('/stats')).deleted_videos === 1, 'Video did not enter trash');
      await page.goto(`${base}/#/trash`);
      await trigger().waitFor();
      assert.deepEqual(treeManifest(source), originals, 'Soft deletion touched source files');
    });
    await step('aviso, nome, foco em Cancelar e Escape preservam os arquivos', async () => {
      await trigger().click(); await dialog().waitFor();
      const content = await dialog().textContent();
      assert.ok(content.includes(target.title));
      assert.match(content, /pasta original.*apagada/i);
      assert.match(content, /TXT, SRT e JSON.*METODO\.md.*áudio ou mídia/i);
      assert.match(content, /não poderão ser restaurados/i);
      assert.equal(await page.evaluate(() => document.activeElement?.textContent.trim()), 'Cancelar');
      assert.equal(await confirm().isDisabled(), true);
      await page.screenshot({ path: '/tmp/youtube-catalog-permanent-dialog.png' });
      await page.keyboard.press('Escape'); await dialog().waitFor({ state: 'hidden' });
      assert.equal(await trigger().evaluate(element => element === document.activeElement), true);
      await trigger().click(); await dialog().getByRole('button', { name: 'Cancelar', exact: true }).click();
      await dialog().waitFor({ state: 'hidden' });
      assert.equal(await trigger().evaluate(element => element === document.activeElement), true);
      assert.deepEqual(treeManifest(source), originals);
      assert.equal((await api('/stats')).deleted_videos, 1);
    });
    await step('somente EXCLUIR exato habilita a confirmação, inclusive com Enter', async () => {
      await trigger().click(); await dialog().waitFor();
      for (const value of ['', 'excluir', 'EXCLUIR ', 'REMOVER']) {
        await field().fill(value);
        assert.equal(await confirm().isDisabled(), true, `Unexpected accepted text: ${JSON.stringify(value)}`);
        await field().press('Enter');
        assert.equal(await dialog().isVisible(), true);
      }
      assert.deepEqual(permanentRequests, [], 'Invalid confirmation submitted a permanent deletion');
      await field().fill('EXCLUIR'); assert.equal(await confirm().isEnabled(), true);
      await page.keyboard.press('Escape'); await dialog().waitFor({ state: 'hidden' });
      assert.deepEqual(treeManifest(source), originals, 'Escape after valid text must preserve files');
    });
    await step('modal mobile cabe na tela e reinicia a confirmação', async () => {
      await page.setViewportSize({ width: 412, height: 800 });
      await trigger().click(); await dialog().waitFor();
      assert.equal(await field().inputValue(), '');
      assert.equal(await confirm().isDisabled(), true);
      const bounds = await dialog().boundingBox();
      assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= 413 && bounds.y + bounds.height <= 801);
      const titleBounds = await page.locator('#permanent-delete-title').boundingBox();
      assert.ok(titleBounds.y >= bounds.y && titleBounds.y + titleBounds.height <= bounds.y + bounds.height, 'Initial Cancelar focus scrolled the warning title out of the mobile dialog');
      assert.ok(await page.evaluate(() => Math.max(document.body.scrollWidth, document.documentElement.scrollWidth) <= innerWidth + 1));
      await page.screenshot({ path: '/tmp/youtube-catalog-permanent-mobile.png' });
    });
    await step('confirmação remove a pasta completa e o item da API e da lixeira', async () => {
      await field().fill('EXCLUIR');
      const responsePromise = page.waitForResponse(response => response.request().method() === 'DELETE' && response.url() === `${base}/api/videos/${targetId}/permanent`);
      await confirm().click();
      const response = await responsePromise;
      assert.ok(response.ok(), `Permanent deletion response ${response.status()}: ${await response.text()}`);
      removed = true;
      await dialog().waitFor({ state: 'hidden' });
      await poll(async () => (await api('/stats')).deleted_videos === 0, 'Trash count did not decrease');
      await page.getByRole('heading', { name: 'Sua lixeira está vazia', exact: true }).waitFor();
      assert.equal(fs.existsSync(path.join(source, targetId)), false, 'Original folder and nested/audio artifacts must be gone');
      assert.deepEqual(permanentRequests, [{ confirmation: targetId }]);
      for (const suffix of ['', '/segments', '/files/transcript.txt', '/thumbnail']) {
        assert.equal((await fetch(`${base}/api/videos/${targetId}${suffix}`)).status, 404);
      }
      assert.equal((await api('/stats')).videos, 1);
    });
    await step('nova importação não ressuscita o vídeo e preserva o outro integralmente', async () => {
      const previousScan = (await api('/stats')).last_scan;
      await api('/scan', 'POST');
      await poll(async () => { const stats = await api('/stats'); return !stats.scanning && stats.last_scan !== previousScan; }, 'Scan did not finish');
      assert.deepEqual((await api('/videos')).items.map(video => video.id), [keepId]);
      assert.equal((await api('/videos?deleted=true')).total, 0);
      assert.equal((await fetch(`${base}/api/videos/${targetId}`)).status, 404);
      assert.deepEqual(treeManifest(path.join(source, keepId)), keepOriginals, 'Other video source bytes changed');
      const after = await api(`/videos/${keepId}`);
      for (const key of ['id', 'title', 'channel', 'duration', 'method', 'categories', 'category_override', 'added_at']) assert.deepEqual(after[key], kept[key]);
      assert.deepEqual(await api(`/videos/${keepId}/segments`), keptSegments);
      assert.equal((await fetch(`${base}/api/videos/${keepId}/files/transcript.txt`)).status, 200);
      assert.deepEqual(externalRequests, []); assert.deepEqual(pageErrors, []); assert.deepEqual(forbiddenRequests, []);
    });
  } finally {
    if (!removed && fs.existsSync(path.join(source, targetId))) {
      const trash = await api('/videos?deleted=true');
      if (trash.items.some(video => video.id === targetId)) await api(`/videos/${targetId}/restore`, 'POST');
    }
  }
  const report = { passed, source, removed, originalFileCount: Object.keys(originals).length,
    remainingFileCount: Object.keys(treeManifest(source)).length, pageErrors, externalRequests, forbiddenRequests, permanentRequests,
    screenshots: ['/tmp/youtube-catalog-permanent-dialog.png', '/tmp/youtube-catalog-permanent-mobile.png'] };
  fs.writeFileSync('/tmp/youtube-catalog-permanent-report.json', JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
})().catch(error => { console.error(error.stack || error.message); process.exitCode = 1; }).finally(async () => { if (browser) await browser.close(); });
