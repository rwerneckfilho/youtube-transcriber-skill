#!/usr/bin/env node
/* UI acceptance with browser-only API fixtures. No downloader or worker is invoked. */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');

const base = (process.env.TEST_CATALOG_URL || 'http://127.0.0.1:8766').replace(/\/$/, '');
const endpoint = new URL(base);
assert.ok(['localhost', '127.0.0.1', '[::1]'].includes(endpoint.hostname));
assert.equal(endpoint.port, '8766', 'This fixture-driven UI test runs only on the isolated server at port 8766');
const stamp = '2026-09-05T12:00:00Z';
const category = { id: 'C1', name: 'Research', count: 1 };
const video = {
  id: 'fixture_video', title: 'Neutral fixture video', channel: 'Fixture Channel', channel_id: 'fixture-channel',
  duration: 120, language: 'en', published_at: stamp, added_at: stamp, deleted_at: null,
  purge_pending: false, purge_error: null, available: true, has_method: true, has_speakers: false,
  categories: [category], tags: ['fixture'], thumbnail_url: '/api/videos/fixture_video/thumbnail', match: null,
};
const trashed = { ...video, id: 'fixture_trash', title: 'Neutral archived fixture', deleted_at: stamp, thumbnail_url: '/api/videos/fixture_trash/thumbnail' };
const detail = { ...video, description: 'Neutral source description.', method: '# Example method\n\nA neutral source document.', category_override: false, suggestions: [],
  downloads: ['txt', 'srt', 'json'].map(extension => ({ filename: `transcript.${extension}`, label: `${extension.toUpperCase()} · original`, url: `/api/videos/fixture_video/files/transcript.${extension}` })) };
const stats = { videos: 1, deleted_videos: 1, channels: 1, categories: 1, methods: 1, hours: 2 / 60,
  languages: [{ id: 'en', name: 'English', count: 1 }], scanning: false, last_scan: stamp, warnings: [] };
const thumbnail = '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#0b253a"/><text x="40" y="200" fill="#fff" font-size="40">Fixture</text></svg>';

const state = { jobs: [], nextId: 1, requests: [], unexpected: [], external: [], pageErrors: [] };
const passed = [];
const screenshots = [];
const languages = {
  pt: { selector: 'Idioma da interface', navigation: ['Início', 'Todos os vídeos', 'Canais', 'Categorias', 'Lixeira', 'Adicionar vídeos'], token: 'EXCLUIR', running: /Transcrevendo|Transcrição em andamento|Em andamento/ },
  en: { selector: 'Interface language', navigation: ['Home', 'All videos', 'Channels', 'Categories', 'Trash', 'Add videos'], token: 'DELETE' },
  es: { selector: 'Idioma de la interfaz', navigation: ['Inicio', 'Todos los vídeos', 'Canales', 'Categorías', 'Papelera', 'Añadir vídeos'], token: 'ELIMINAR' },
};
const forbidden = {
  en: /\b(?:Início|Todos os vídeos|Canais|Categorias|Lixeira|Transcrição|Método|Carregar|Cancelar|Excluir|Restaurar|Nenhum|Próxima|Anterior|Salvar|Idioma da interface|Atualizar catálogo|Seu acervo|Seus arquivos|Sem categoria|Concluído|Concluída|Falhou|Falha|Em andamento|Na fila|Vídeos adicionados|Escolher categorias)\b/iu,
  es: /\b(?:Início|Todos os vídeos|Lixeira|Transcrição|Carregar|Excluir|Seus arquivos|Seu acervo|Sem categoria|Atualizar catálogo|Salvar|Escolher categorias|Concluído|Concluída|Falhou|Falha|Em andamento|Na fila|Buscar nesta transcrição|Digite|Arquivos originais)\b/iu,
};

function jobRecord(body) {
  const id = String(state.nextId++).padStart(32, '0');
  return { id, url: body.url, kind: body.kind, language: body.language, title: body.kind === 'playlist' ? 'Fixture playlist' : 'Fixture submission',
    status: 'queued', stage: 'queued', total: 0, completed: 0, failed: 0, skipped: 0, cancel_requested: false,
    created_at: stamp, updated_at: stamp, error: null, error_code: null, items: [], logs: [] };
}
function jobItem(job, status = 'completed') {
  return { id: `${job.id}-item`, position: 1, video_id: video.id, title: video.title, status, stage: status,
    error: null, error_code: null, updated_at: stamp };
}
function paginated(items, params) {
  const page = Number(params.get('page') || 1), page_size = Number(params.get('page_size') || 10);
  return { items: items.slice((page - 1) * page_size, page * page_size), total: items.length, page, page_size };
}
async function mockApi(route) {
  const request = route.request(), url = new URL(request.url());
  const pathname = url.pathname.slice(4), method = request.method();
  const body = request.postData() ? request.postDataJSON() : null;
  state.requests.push({ method, path: pathname, body });
  const json = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
  if (pathname === '/health') return json({ status: 'ok' });
  if (pathname === '/stats') return json(stats);
  if (pathname === '/categories' && method === 'GET') return json([category]);
  if (pathname === '/channels') return json([{ id: video.channel_id, name: video.channel, count: 1 }]);
  if (pathname === '/videos' && method === 'GET') return json(paginated([url.searchParams.get('deleted') === 'true' ? trashed : video], url.searchParams));
  if (/^\/videos\/[^/]+\/thumbnail$/.test(pathname)) return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: thumbnail });
  if (pathname === '/videos/fixture_video') return json(detail);
  if (pathname === '/videos/fixture_video/segments') return json({ items: [{ index: 0, start_ms: 0, end_ms: 5000, text: 'Neutral transcript example.', speaker: null }], total: 1, offset: 0, limit: 100 });
  if (/^\/videos\/fixture_video\/files\//.test(pathname)) return route.fulfill({ status: 200, contentType: 'text/plain', body: 'Neutral transcript example.' });
  if (pathname === '/jobs' && method === 'GET') return json(paginated([...state.jobs].reverse(), url.searchParams));
  if (pathname === '/jobs' && method === 'POST') {
    assert.ok(['auto', 'video', 'playlist'].includes(body.kind));
    assert.ok(['auto', 'pt', 'en', 'es'].includes(body.language));
    const job = jobRecord(body); state.jobs.push(job); return json(job, 202);
  }
  const jobMatch = pathname.match(/^\/jobs\/([^/]+)(?:\/(cancel|retry))?$/);
  if (jobMatch) {
    const job = state.jobs.find(job => job.id === jobMatch[1]);
    if (!job) return json({ detail: 'Fixture job not found' }, 404);
    if (method === 'GET' && !jobMatch[2]) return json(job);
    if (method === 'POST' && jobMatch[2] === 'cancel') {
      Object.assign(job, { status: 'cancelled', stage: 'cancelled', cancel_requested: true }); return json(job);
    }
    if (method === 'POST' && jobMatch[2] === 'retry') {
      Object.assign(job, { status: 'queued', stage: 'queued', cancel_requested: false, error: null, error_code: null }); return json(job);
    }
  }
  state.unexpected.push({ method, pathname });
  return json({ detail: 'Unexpected browser fixture API request' }, 404);
}
async function step(name, work) { await work(); passed.push(name); console.log(`OK     ${name}`); }
async function ready(page, route = '/add') {
  await page.goto(`${base}/#${route}`);
  await page.locator('#interface-language').waitFor();
  if (route === '/add') await page.locator('#source-url').waitFor();
  else if (route.startsWith('/video/')) await page.locator('.segment').first().waitFor();
  else if (route === '/trash') await page.locator('.trash-card').waitFor();
  else if (route === '/videos') await page.locator('.video-grid .video-card').waitFor();
  else if (route === '/channels' || route === '/categories') await page.locator('.directory-card').first().waitFor();
  else if (route === '/') { await page.locator('.hero h2').waitFor(); await page.locator('.shelf .video-card').first().waitFor(); }
}
async function noOverflow(page, label) {
  const dimensions = await page.evaluate(() => ({ width: innerWidth, body: document.body.scrollWidth, document: document.documentElement.scrollWidth }));
  assert.ok(Math.max(dimensions.body, dimensions.document) <= dimensions.width + 1, `${label}: ${JSON.stringify(dimensions)}`);
}
async function noPortugueseLeak(page, locale, label) {
  if (locale === 'pt') return;
  const content = await page.evaluate(() => {
    const attributes = [...document.querySelectorAll('[aria-label],[title],[placeholder]')].filter(element => {
      const style = getComputedStyle(element); return style.display !== 'none' && style.visibility !== 'hidden';
    }).map(element => ['aria-label', 'title', 'placeholder'].map(name => element.getAttribute(name) || '').join(' '));
    return [document.title, document.body.innerText, ...attributes].join('\n');
  });
  const match = content.match(forbidden[locale]);
  assert.ok(!match, `${label} (${locale}): untranslated Portuguese interface text: ${match?.[0]}`);
}
async function waitJob(page, job, pattern) {
  const card = page.locator(`.job-card[data-job-id="${job.id}"]`);
  await card.waitFor();
  if (pattern) await page.waitForFunction(({ id, source, flags }) => {
    const cards = [...document.querySelectorAll(`[data-job-id="${id}"]`)];
    return cards.some(card => new RegExp(source, flags).test(card.textContent));
  }, { id: job.id, source: pattern.source, flags: pattern.flags });
  return card;
}

let browser;
(async () => {
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'pt-BR', reducedMotion: 'reduce' });
  await context.addInitScript(() => { if (!localStorage.getItem('rw-ai.locale')) localStorage.setItem('rw-ai.locale', 'pt'); });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (/^https?:$/.test(url.protocol) && url.origin !== endpoint.origin) { state.external.push(url.href); return route.abort('blockedbyclient'); }
    if (url.pathname.startsWith('/api/')) return mockApi(route);
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(20000);
  page.on('pageerror', error => state.pageErrors.push(error.message));

  await step('formulário envia vídeo e playlist com idioma de transcrição independente da interface', async () => {
    await ready(page);
    await page.locator('#interface-language').selectOption('pt');
    await page.locator('#source-url').fill('https://www.youtube.com/watch?v=BaW_jenozKc');
    await page.locator('#source-kind').selectOption('video');
    await page.locator('#transcription-language').selectOption('pt');
    await page.locator('#source-url').press('Enter');
    await page.waitForFunction(() => document.querySelector('[data-job-id]'));
    assert.deepEqual(state.requests.find(request => request.path === '/jobs' && request.method === 'POST').body,
      { url: 'https://www.youtube.com/watch?v=BaW_jenozKc', kind: 'video', language: 'pt' });
    await page.locator('#source-url').fill('https://www.youtube.com/playlist?list=PL_FIXTURE_ONLY');
    await page.locator('#source-kind').selectOption('playlist');
    await page.locator('#transcription-language').selectOption('es');
    await page.locator('#source-url').press('Enter');
    await page.waitForFunction(() => new Set([...document.querySelectorAll('[data-job-id]')].map(element => element.dataset.jobId)).size === 2);
    assert.deepEqual(state.requests.filter(request => request.path === '/jobs' && request.method === 'POST')[1].body,
      { url: 'https://www.youtube.com/playlist?list=PL_FIXTURE_ONLY', kind: 'playlist', language: 'es' });
  });
  await step('fila mostra descoberta, execução, logs e resultado parcial simulados', async () => {
    const job = state.jobs[0];
    Object.assign(job, { status: 'discovering', stage: 'discovering' });
    await waitJob(page, job, /Descobr|Identific|Descoberta/i);
    Object.assign(job, { status: 'running', stage: 'transcribing', total: 3, completed: 1, items: [jobItem(job)], logs: [{ id: 1, time: stamp, stage: 'transcribing', message: 'Fixture transcription log.' }] });
    await waitJob(page, job, languages.pt.running);
    const expand = page.locator(`[data-job-id="${job.id}"][data-job-action="expand"]`);
    await expand.click();
    const log = page.locator(`.job-card[data-job-id="${job.id}"] .job-logs li`).first();
    await log.waitFor();
    assert.equal(await log.locator('time').getAttribute('datetime'), stamp);
    assert.match(await log.textContent(), /Transcrevendo/);
    Object.assign(job, { status: 'partial', stage: 'completed', total: 3, completed: 1, failed: 1, skipped: 1,
      items: [jobItem(job), { ...jobItem(job, 'failed'), id: `${job.id}-failed`, position: 2, video_id: 'fixture_missing', title: 'Unavailable fixture item', error: 'Fixture unavailable error.', error_code: 'download_failed' }] });
    await waitJob(page, job, /Concluído com falhas/i);
    await page.locator(`a[href="#/video/${video.id}"]`).first().waitFor();
  });
  await step('cancelamento e nova tentativa usam somente os endpoints simulados corretos', async () => {
    const second = state.jobs[1];
    await page.locator(`[data-job-id="${second.id}"][data-job-action="cancel"]`).click();
    await waitJob(page, second, /Cancelad/i);
    assert.ok(state.requests.some(request => request.path === `/jobs/${second.id}/cancel` && request.method === 'POST'));
    Object.assign(second, { status: 'failed', stage: 'failed', error: 'Fixture pipeline error.', error_code: 'transcription_failed' });
    await waitJob(page, second, /Falh|Erro/i);
    await page.locator(`[data-job-id="${second.id}"][data-job-action="retry"]`).click();
    await waitJob(page, second, /fila|Aguardando/i);
    assert.ok(state.requests.some(request => request.path === `/jobs/${second.id}/retry` && request.method === 'POST'));
    Object.assign(second, { status: 'completed', stage: 'completed', total: 1, completed: 1, items: [jobItem(second)] });
    await waitJob(page, second, /Concluíd|Concluid|Finalizad/i);
  });
  for (const locale of ['pt', 'en', 'es']) {
    await step(`interface ${locale}: preferência persistida, rotas e diálogos localizados`, async () => {
      await ready(page);
      await page.locator('#interface-language').selectOption(locale);
      assert.equal(await page.locator('#interface-language').getAttribute('aria-label'), languages[locale].selector);
      await page.reload(); await page.locator('#source-url').waitFor();
      assert.equal(await page.locator('#interface-language').inputValue(), locale);
      assert.equal(await page.evaluate(() => localStorage.getItem('rw-ai.locale')), locale);
      assert.ok((await page.locator('html').getAttribute('lang')).startsWith(locale));
      assert.deepEqual(await page.locator('.sidebar nav a').evaluateAll(links => links.map(link => link.getAttribute('aria-label'))), languages[locale].navigation);
      for (const route of ['/', '/videos', '/channels', '/categories', '/trash', '/video/fixture_video', '/add']) {
        await ready(page, route); await noPortugueseLeak(page, locale, route);
        const headings = await page.locator('main h1').allTextContents();
        assert.ok(headings.some(title => title.trim()), `${locale} ${route}: missing page heading`);
      }
      await ready(page, '/video/fixture_video');
      await page.locator('#method-tab').click(); await page.locator('.markdown').waitFor();
      await noPortugueseLeak(page, locale, 'method');
      await page.locator('.edit-chip').click(); await page.locator('.category-dialog').waitFor();
      await noPortugueseLeak(page, locale, 'category dialog'); await page.keyboard.press('Escape');
      await page.locator('.delete-trigger').click(); await page.locator('.delete-dialog').waitFor();
      await noPortugueseLeak(page, locale, 'soft-delete dialog'); await page.keyboard.press('Escape');
      await ready(page, '/trash');
      await page.locator('.permanent-delete-trigger').click(); await page.locator('.permanent-delete-dialog').waitFor();
      await noPortugueseLeak(page, locale, 'permanent-delete dialog');
      assert.ok((await page.locator('.permanent-confirmation').textContent()).includes(languages[locale].token));
      await page.keyboard.press('Escape');
    });
    await step(`interface ${locale}: tela móvel de adição e fila sem rolagem horizontal`, async () => {
      await page.setViewportSize({ width: 412, height: 800 });
      await ready(page); await noOverflow(page, `add ${locale}`);
      await noPortugueseLeak(page, locale, 'mobile add');
      const file = `/tmp/youtube-catalog-jobs-${locale}-mobile.png`;
      await page.screenshot({ path: file, fullPage: true }); screenshots.push(file);
      await ready(page, '/trash'); await page.locator('.permanent-delete-trigger').click();
      await page.locator('.permanent-delete-dialog').waitFor(); await noOverflow(page, `dialog ${locale}`);
      await noPortugueseLeak(page, locale, 'mobile permanent dialog'); await page.keyboard.press('Escape');
      await page.setViewportSize({ width: 1440, height: 1000 });
    });
  }
  await ready(page); await page.locator('#interface-language').selectOption('en');
  await page.screenshot({ path: '/tmp/youtube-catalog-jobs-en-desktop.png', fullPage: true });
  screenshots.push('/tmp/youtube-catalog-jobs-en-desktop.png');
  assert.deepEqual(state.unexpected, []); assert.deepEqual(state.external, []); assert.deepEqual(state.pageErrors, []);
  const writes = state.requests.filter(request => request.method !== 'GET');
  assert.ok(writes.every(request => /^\/jobs(?:\/[^/]+\/(?:cancel|retry))?$/.test(request.path)), 'Unexpected catalog mutation in fixture UI test');
  const report = { coverage: 'Browser UI with intercepted in-memory API fixtures only; no real jobs, downloads, Whisper, container persistence or source writes were exercised.',
    passed, mockedWrites: writes, pageErrors: state.pageErrors, externalRequests: state.external, unexpectedApiRequests: state.unexpected, screenshots };
  fs.writeFileSync('/tmp/youtube-catalog-jobs-report.json', JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
})().catch(error => { console.error(error.stack || error.message); process.exitCode = 1; }).finally(async () => { if (browser) await browser.close(); });
