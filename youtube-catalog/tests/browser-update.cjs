#!/usr/bin/env node
/* Deletion writes are permitted only on the explicit disposable server at port 8766. */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium } = require('playwright');
const { PNG } = require('pngjs');

const mainUrl = (process.env.CATALOG_URL || 'http://127.0.0.1:8765').replace(/\/$/, '');
const testUrl = (process.env.TEST_CATALOG_URL || 'http://127.0.0.1:8766').replace(/\/$/, '');
const mode = process.env.UPDATE_TEST_MODE || 'all'; // all | brand | delete
assert.ok(['all', 'brand', 'delete'].includes(mode));
for (const value of [mainUrl, testUrl]) assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(value).hostname));
assert.notEqual(new URL(mainUrl).origin, new URL(testUrl).origin, 'Disposable and main servers must differ');
assert.equal(new URL(testUrl).port, '8766', 'Mutation tests are restricted to the disposable server on port 8766');

const errors = [];
const externalRequests = [];
const blockedWrites = [];
const contrastResults = [];
const screenshots = [];
const failed = [];

function sourceManifest() {
  const source = process.env.TEST_SOURCE_DIR;
  if (!source) return null;
  const allowed = new Set(['video.json', 'METODO.md', 'transcript.json', 'transcript.txt', 'transcript.srt', 'transcript-speakers.json', 'transcript-speakers.txt', 'transcript-speakers.srt']);
  const result = {};
  for (const folder of fs.readdirSync(source, { withFileTypes: true }).filter(entry => entry.isDirectory()).sort((a, b) => a.name.localeCompare(b.name))) {
    for (const file of fs.readdirSync(path.join(source, folder.name), { withFileTypes: true }).filter(entry => entry.isFile() && allowed.has(entry.name)).sort((a, b) => a.name.localeCompare(b.name))) {
      result[`${folder.name}/${file.name}`] = crypto.createHash('sha256').update(fs.readFileSync(path.join(source, folder.name, file.name))).digest('hex');
    }
  }
  assert.ok(Object.keys(result).length, 'TEST_SOURCE_DIR contains no source artifacts');
  return result;
}

async function api(base, path, method = 'GET') {
  assert.ok(method === 'GET' || base === testUrl, 'Never mutate the real catalog');
  const response = await fetch(base + '/api' + path, { method });
  assert.ok(response.ok, `${method} ${path}: ${response.status}`);
  return response.json();
}
async function poll(check, message, timeout = 15000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  assert.fail(message);
}
async function check(name, work) {
  try { await work(); console.log(`OK     ${name}`); }
  catch (error) { failed.push({ name, error: error.message }); console.error(`FALHOU ${name}: ${error.message}`); }
}
async function contextFor(browser, base, allowWrites) {
  const context = await browser.newContext({ viewport: { width: 1600, height: 900 }, locale: 'pt-BR', reducedMotion: 'reduce' });
  await context.route('**/*', route => {
    const request = route.request(); const url = new URL(request.url());
    if (/^https?:$/.test(url.protocol) && url.origin !== new URL(base).origin) {
      externalRequests.push(request.url()); return route.abort('blockedbyclient');
    }
    if (url.pathname.startsWith('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method()) && !allowWrites) {
      blockedWrites.push(`${request.method()} ${request.url()}`); return route.abort('blockedbyclient');
    }
    return route.continue();
  });
  context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
  return context;
}
async function reader(page, base, video) {
  await page.goto(`${base}/#/video/${video.id}`);
  await page.getByRole('heading', { name: video.title, exact: true }).waitFor();
  await page.locator('.segments .segment').first().waitFor();
  await page.waitForFunction(() => document.querySelector('.segments')?.getAttribute('aria-busy') === 'false');
}
async function noOverflow(page, label) {
  const result = await page.evaluate(() => ({ viewport: innerWidth, body: document.body.scrollWidth, root: document.documentElement.scrollWidth }));
  assert.ok(Math.max(result.body, result.root) <= result.viewport + 1, `${label}: horizontal overflow ${JSON.stringify(result)}`);
}
async function visibleImages(page) {
  await page.evaluate(async () => Promise.all([...document.images].filter(image => {
    const rect = image.getBoundingClientRect(); return rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth;
  }).map(image => image.decode().catch(() => {}))));
}

function luminance(color) {
  const channels = color.slice(0, 3).map(channel => {
    const value = channel / 255; return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}
function ratio(foreground, background) {
  const a = foreground[3] ?? 1;
  const blended = foreground.slice(0, 3).map((channel, index) => channel * a + background[index] * (1 - a));
  const values = [luminance(blended), luminance(background)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

async function contrast(page, section, selectors) {
  // Read the computed text color, font and opacity. Hide only text fill briefly to
  // sample the real painted background, including gradients and thumbnail images.
  const samples = await page.evaluate(selectors => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const samples = [];
    for (const selector of selectors) {
      const element = [...document.querySelectorAll(selector)].find(element => {
        const rect = element.getBoundingClientRect(); const style = getComputedStyle(element);
        return rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.bottom <= innerHeight && style.visibility !== 'hidden';
      });
      if (!element) continue;
      const style = getComputedStyle(element);
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      const rects = [];
      while (walker.nextNode()) {
        if (!walker.currentNode.textContent.trim()) continue;
        const range = document.createRange(); range.selectNodeContents(walker.currentNode);
        for (const rect of range.getClientRects()) if (rect.width && rect.height) rects.push({ x: rect.x, y: rect.y, width: rect.width, height: rect.height });
      }
      if (!rects.length) continue;
      ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = style.color; ctx.fillRect(0, 0, 1, 1);
      const rgba = [...ctx.getImageData(0, 0, 1, 1).data]; rgba[3] /= 255;
      let opacity = 1;
      for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) opacity *= Number(getComputedStyle(ancestor).opacity);
      rgba[3] *= opacity;
      const key = `contrast-${samples.length}`;
      const originalStyle = element.getAttribute('style');
      element.dataset.contrastProbe = key;
      element.style.setProperty('-webkit-text-fill-color', 'transparent', 'important');
      element.style.setProperty('text-shadow', 'none', 'important');
      samples.push({ key, selector, originalStyle, color: style.color, rgba,
        fontSize: parseFloat(style.fontSize), fontWeight: Number(style.fontWeight) || 400, rects });
    }
    return samples;
  }, selectors);
  let background;
  try { background = PNG.sync.read(await page.screenshot()); }
  finally {
    await page.evaluate(samples => {
      for (const sample of samples) {
        const element = document.querySelector(`[data-contrast-probe="${sample.key}"]`);
        if (!element) continue;
        if (sample.originalStyle === null) element.removeAttribute('style'); else element.setAttribute('style', sample.originalStyle);
        delete element.dataset.contrastProbe;
      }
    }, samples);
  }
  assert.ok(samples.length, `${section}: no visible text samples`);
  const failures = [];
  for (const sample of samples) {
    let minimum = Infinity;
    for (const rect of sample.rects) for (const fraction of [0.2, 0.5, 0.8]) {
      const x = Math.min(background.width - 1, Math.max(0, Math.floor(rect.x + rect.width * fraction)));
      const y = Math.min(background.height - 1, Math.max(0, Math.floor(rect.y + rect.height * 0.5)));
      const at = (y * background.width + x) * 4;
      minimum = Math.min(minimum, ratio(sample.rgba, [...background.data.subarray(at, at + 3)]));
    }
    const required = sample.fontSize >= 24 || (sample.fontSize >= 18.666 && sample.fontWeight >= 700) ? 3 : 4.5;
    const caption = ['.page-eyebrow', '.nav-label', '.connection', '.card-channel', '.card-meta > span', '.duration', '.timestamp'].includes(sample.selector);
    const minimumFont = caption ? 11 : 12;
    const result = { section, selector: sample.selector, color: sample.color, fontSize: sample.fontSize, minimumFont, ratio: Number(minimum.toFixed(2)), required };
    contrastResults.push(result);
    if (minimum + 0.015 < required || sample.fontSize < minimumFont) failures.push(result);
  }
  assert.deepEqual(failures, [], `${section}: insufficient text contrast ${JSON.stringify(failures)}`);
}

async function deletionChecks(browser) {
  const context = await contextFor(browser, testUrl, true);
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  const initial = await api(testUrl, '/stats');
  const video = (await api(testUrl, '/videos?page_size=1')).items[0];
  assert.ok(video, 'Disposable collection needs at least one active video');
  const before = await api(testUrl, `/videos/${video.id}`);
  const beforeSegments = await api(testUrl, `/videos/${video.id}/segments?limit=3`);
  const originals = sourceManifest();
  let deletedByTest = false;
  try {
    await check('isolado: cancelar e Escape preservam vídeo, contadores e foco', async () => {
      await reader(page, testUrl, video);
      const trigger = page.locator('.delete-trigger');
      const dialog = page.getByRole('dialog', { name: 'Excluir do catálogo?', exact: true });
      try {
        await trigger.click();
        await dialog.waitFor();
        assert.match(await dialog.textContent(), /arquivos originais.*preservados/i);
        await page.screenshot({ path: '/tmp/youtube-catalog-delete-dialog.png' });
        screenshots.push('/tmp/youtube-catalog-delete-dialog.png');
        assert.equal(await page.evaluate(() => document.activeElement?.textContent.trim()), 'Cancelar');
        await page.keyboard.press('Escape');
        await dialog.waitFor({ state: 'hidden' });
        assert.equal(await trigger.evaluate(element => element === document.activeElement), true);
        await trigger.click(); await dialog.getByRole('button', { name: 'Cancelar', exact: true }).click();
        await dialog.waitFor({ state: 'hidden' });
        assert.equal(await trigger.evaluate(element => element === document.activeElement), true);
        assert.equal((await api(testUrl, '/stats')).videos, initial.videos);
      } finally {
        if (await dialog.isVisible()) { await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' }); }
      }
    });
    await check('isolado: excluir move para Lixeira e sobrevive à atualização', async () => {
      await reader(page, testUrl, video);
      await page.locator('.delete-trigger').click();
      const dialog = page.getByRole('dialog', { name: 'Excluir do catálogo?', exact: true });
      await dialog.getByRole('button', { name: 'Excluir do catálogo', exact: true }).click();
      deletedByTest = true;
      await poll(async () => (await api(testUrl, '/stats')).videos === initial.videos - 1, 'Active count did not decrease');
      await page.waitForFunction(() => location.hash.startsWith('#/videos'));
      assert.equal((await api(testUrl, '/stats')).deleted_videos, initial.deleted_videos + 1);
      assert.equal((await fetch(`${testUrl}/api/videos/${video.id}`)).status, 404);
      const previousScan = (await api(testUrl, '/stats')).last_scan;
      await api(testUrl, '/scan', 'POST');
      await poll(async () => { const stats = await api(testUrl, '/stats'); return !stats.scanning && stats.last_scan !== previousScan; }, 'Scan did not finish');
      assert.equal((await api(testUrl, '/stats')).videos, initial.videos - 1);
      await page.getByRole('navigation', { name: 'Navegação principal' }).getByRole('link', { name: /Lixeira/ }).click();
      await page.locator('.trash-card').filter({ hasText: video.title }).waitFor();
      assert.match(await page.locator('main').textContent(), /originais|preservad/i);
    });
    await check('isolado: restaurar devolve texto, categorias e contadores', async () => {
      await page.goto(`${testUrl}/#/trash`);
      await page.getByRole('button', { name: `Restaurar vídeo: ${video.title}`, exact: true }).click();
      await poll(async () => (await api(testUrl, '/stats')).videos === initial.videos, 'Active count did not recover');
      deletedByTest = false;
      assert.equal((await api(testUrl, '/stats')).deleted_videos, initial.deleted_videos);
      const restored = await api(testUrl, `/videos/${video.id}`);
      assert.deepEqual(restored.categories, before.categories);
      assert.equal(restored.category_override, before.category_override);
      assert.deepEqual(await api(testUrl, `/videos/${video.id}/segments?limit=3`), beforeSegments);
      await api(testUrl, '/scan', 'POST');
      await poll(async () => !(await api(testUrl, '/stats')).scanning, 'Post-restore scan did not finish');
      await reader(page, testUrl, video);
      assert.equal((await api(testUrl, '/stats')).videos, initial.videos);
    });
    await check('isolado: arquivos originais inalterados após excluir/restaurar', async () => {
      if (originals) assert.deepEqual(sourceManifest(), originals);
      else console.log('       SHA-256 não executado: informe TEST_SOURCE_DIR com a pasta de fontes isoladas.');
    });
  } finally {
    if (deletedByTest) await api(testUrl, `/videos/${video.id}/restore`, 'POST');
    await context.close();
  }
}

async function brandChecks(browser) {
  const context = await contextFor(browser, mainUrl, false);
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  const before = await api(mainUrl, '/stats');
  const video = (await api(mainUrl, '/videos?page_size=1')).items[0];
  assert.ok(video, 'Visual collection needs one active video');
  try {
    await check('rw/ai: marca, cores computadas e contraste do início', async () => {
      await page.goto(mainUrl + '/#/');
      await page.locator('.hero h2').waitFor(); await visibleImages(page);
      const fonts = await page.evaluate(async () => {
        await document.fonts.ready;
        return { manrope: document.fonts.check('700 32px Manrope'), inter: document.fonts.check('400 16px Inter') };
      });
      assert.ok(fonts.manrope && fonts.inter, `Local fonts did not load: ${JSON.stringify(fonts)}`);
      assert.match(await page.locator('.brand').textContent(), /rw\s*\/\s*ai/i);
      assert.match(await page.title(), /rw\s*\/\s*ai|rw.ai/i);
      await contrast(page, 'home', ['.page-title-row h1', '.page-title-row h1 span', '.page-description', '.page-eyebrow', '.nav-label', '.sidebar nav a.active span', '.sidebar nav a:not(.active) span', '.hero h2', '.hero-channel', '.hero-meta > span', '.hero .button.primary', '.connection']);
    });
    await check('rw/ai: contraste dos cartões, leitor e diálogos', async () => {
      await page.locator('.shelf').first().scrollIntoViewIfNeeded(); await visibleImages(page);
      await contrast(page, 'cards', ['.section-heading h2', '.section-heading p', '.card-channel', '.video-card h3', '.card-meta > span', '.duration']);
      await reader(page, mainUrl, video);
      await page.locator('.reader').scrollIntoViewIfNeeded();
      await contrast(page, 'reader', ['.segment p', '.timestamp', '.reader-note span', '.reader-toolbar > span', '.download-list a > span', '.aside-box > p']);
      await page.getByRole('button', { name: 'Editar', exact: true }).click();
      await page.getByRole('dialog', { name: 'Categorias do vídeo' }).waitFor();
      await contrast(page, 'category-dialog', ['.category-dialog h2', '.category-dialog > p', '.category-options label span', '.suggestions p', '.dialog-footer .button.primary']);
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: 'Excluir do catálogo', exact: true }).click();
      await page.getByRole('dialog', { name: 'Excluir do catálogo?', exact: true }).waitFor();
      await contrast(page, 'delete-dialog', ['.delete-dialog h2', '#delete-description', '.delete-dialog .button.secondary', '.delete-dialog .button.danger']);
      await page.keyboard.press('Escape');
    });
    for (const [width, height] of [[1920, 700], [1366, 768], [1024, 768], [900, 900], [412, 800], [1600, 900]]) {
      await check(`rw/ai: ${width}×${height}, início/lista/leitor/diálogo sem excesso horizontal`, async () => {
        await page.setViewportSize({ width, height });
        await page.goto(mainUrl + '/#/'); await page.locator('.hero h2').waitFor();
        await page.locator('.shelf .video-card').first().waitFor(); await visibleImages(page);
        await noOverflow(page, `home ${width}×${height}`);
        const file = `/tmp/youtube-catalog-update-${width}x${height}.png`;
        await page.screenshot({ path: file }); screenshots.push(file);
        await page.goto(mainUrl + '/#/videos'); await page.locator('.video-grid .video-card').first().waitFor();
        await noOverflow(page, `list ${width}×${height}`);
        await reader(page, mainUrl, video); await noOverflow(page, `reader ${width}×${height}`);
        await page.getByRole('button', { name: 'Excluir do catálogo', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: 'Excluir do catálogo?', exact: true });
        await dialog.waitFor(); await noOverflow(page, `dialog ${width}×${height}`);
        const bounds = await dialog.boundingBox();
        assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= width + 1 && bounds.y + bounds.height <= height + 1, 'Dialog escapes viewport');
        await page.keyboard.press('Escape');
      });
    }
    await check('catálogo principal inalterado e assets somente locais', async () => {
      const after = await api(mainUrl, '/stats');
      assert.equal(after.videos, before.videos); assert.equal(after.deleted_videos, before.deleted_videos);
      assert.deepEqual(externalRequests, []); assert.deepEqual(blockedWrites, []); assert.deepEqual(errors, []);
    });
  } finally { await context.close(); }
}

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    if (mode !== 'brand') await deletionChecks(browser);
    if (mode !== 'delete') await brandChecks(browser);
  } finally { await browser.close(); }
  const report = { mode, failed, errors, externalRequests, blockedWrites, contrastResults, screenshots };
  fs.writeFileSync('/tmp/youtube-catalog-update-report.json', JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  if (failed.length || errors.length || externalRequests.length || blockedWrites.length) process.exitCode = 1;
})().catch(error => { console.error(error.stack || error.message); process.exitCode = 1; });
