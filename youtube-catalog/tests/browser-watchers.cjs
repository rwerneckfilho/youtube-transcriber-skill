const { chromium } = require('playwright');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const assert = require('node:assert/strict');
// A static server with fully intercepted API fixtures: no real subscriptions or downloads.
const dist = path.resolve(__dirname, '../frontend/dist');
const origin = 'http://127.0.0.1:18868';
const report = { checks: [], errors: [], mutations: [] };
let sources = [];
const server = http.createServer((req, res) => {
  const pathname = decodeURIComponent(new URL(req.url, origin).pathname);
  const file = path.join(dist, pathname === '/' ? 'index.html' : pathname);
  if (!file.startsWith(dist + path.sep)) return res.writeHead(403).end();
  try {
    res.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff': 'font/woff', '.woff2': 'font/woff2', '.svg': 'image/svg+xml' })[path.extname(file)] || 'application/octet-stream');
    res.end(fs.readFileSync(file));
  } catch { res.writeHead(404).end(); }
});
let browser;
(async () => {
  await new Promise(resolve => server.listen(18868, '127.0.0.1', resolve));
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  await context.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.origin !== origin) { report.errors.push(`External request ${url}`); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const endpoint = url.pathname.slice(4), method = req.method();
    let result = {}, status = 200;
    if (method !== 'GET') report.mutations.push({ method, endpoint, body: req.postData() ? req.postDataJSON() : null });
    if (endpoint === '/watchers' && method === 'POST') {
      const body = req.postDataJSON();
      if (sources.length) { status = 409; result = { detail: 'Esta playlist já está sendo acompanhada.', error_code: 'watch_duplicate' }; }
      else {
        sources = [{ ...body, id: 'watch001', title: 'Research playlist', enabled: true, initialized: true, status: 'idle', last_checked: '2026-09-06T14:00:00Z', last_success: '2026-09-06T14:00:00Z', next_check: '2026-09-06T16:00:00Z', last_error: null, last_new: 2, last_unavailable: 1, known: 2, queued: 1, pending: 0, recent: [{ video_id: 'abcdefghijk', title: 'Useful research', detected_at: '2026-09-06T14:00:00Z', state: 'queued', processing_status: 'completed' }, { video_id: 'zbcdefghijk', title: 'Another source', detected_at: '2026-09-06T14:00:00Z', state: 'baseline', processing_status: null }] }];
        result = { id: 'watch001' }; status = 201;
      }
    } else if (endpoint === '/watchers') result = { items: sources, total: sources.length };
    else if (endpoint === '/watchers/watch001' && method === 'PATCH') { sources[0] = { ...sources[0], ...req.postDataJSON() }; result = { ok: true }; }
    else if (endpoint === '/watchers/watch001' && method === 'DELETE') { sources = []; result = { ok: true }; }
    else if (endpoint === '/watchers/watch001/check') { result = { ok: true }; status = 202; }
    else if (endpoint === '/stats') result = { videos: 0, deleted_videos: 0, channels: 0, categories: 0, methods: 0, hours: 0, languages: [], scanning: false, warnings: [] };
    else if (endpoint === '/categories' || endpoint === '/channels') result = [];
    else { report.errors.push(`Unexpected API ${method} ${endpoint}`); status = 404; }
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(result) });
  });
  const page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  await page.goto(origin + '/#/watchers', { waitUntil: 'networkidle' });
  for (const [locale, heading] of [['pt', 'Acompanhar playlists'], ['en', 'Follow playlists'], ['es', 'Seguir playlists']]) {
    await page.locator('#interface-language').selectOption(locale);
    assert.equal(await page.locator('h1').innerText(), heading);
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${locale} mobile overflow`);
    await page.screenshot({ path: `/tmp/youtube-catalog-watchers-${locale}-mobile.png`, fullPage: true });
  }
  report.checks.push('PT/EN/ES headings, mobile navigation and layout');
  await page.locator('#interface-language').selectOption('pt');
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.locator('#watch-url').fill('https://example.org/playlist?list=PLinvalid');
  await page.locator('[data-action="create-watcher"]').click();
  assert.equal(report.mutations.length, 0);
  await page.getByRole('alert').filter({ hasText: 'Informe o link completo' }).waitFor();
  await page.locator('#watch-url').fill('https://www.youtube.com/playlist?list=PLsynthetic_test');
  await page.locator('#watch-name').fill('Research source');
  await page.locator('[data-action="create-watcher"]').click();
  await page.locator('[data-watch-id="watch001"]').waitFor();
  assert.equal(report.mutations[0].body.interval_minutes, 120);
  assert.equal(report.mutations[0].body.initial_mode, 'all');
  assert.equal(report.mutations[0].body.language, 'auto');
  await page.locator('[data-watch-action="check"]').click();
  await page.waitForFunction(() => !document.querySelector('[data-watch-action="check"]').disabled);
  await page.locator('[data-watch-action="pause"]').click();
  await page.getByRole('button', { name: 'Retomar acompanhamento' }).waitFor();
  assert(await page.locator('[data-watch-action="check"]').isDisabled());
  await page.locator('[data-watch-action="pause"]').click();
  await page.getByRole('button', { name: 'Pausar acompanhamento' }).waitFor();
  await page.locator('[data-watch-action="edit"]').click();
  await page.locator('#watch-name-watch001').fill('Updated source');
  await page.locator('#watch-interval-watch001').selectOption('60');
  await page.locator('#watch-language-watch001').selectOption('es');
  await page.getByRole('button', { name: 'Salvar configurações' }).click();
  await page.getByRole('heading', { name: 'Updated source' }).waitFor();
  assert.equal(sources[0].interval_minutes, 60);
  assert.equal(sources[0].language, 'es');
  await page.locator('.watch-recent summary').click();
  assert.match(await page.getByRole('link', { name: 'Useful research' }).getAttribute('href'), /abcdefghijk/);
  await page.screenshot({ path: '/tmp/youtube-catalog-watchers-pt-desktop.png', fullPage: true });
  report.checks.push('URL guard, initial all/auto/2h defaults, check, pause/resume, settings, completed transcript link');
  for (const locale of ['en', 'es', 'pt']) {
    await page.locator('#interface-language').selectOption(locale);
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: `/tmp/youtube-catalog-watchers-${locale}-populated.png`, fullPage: true });
  }
  sources[0].last_error = 'Não foi possível acessar a playlist. Confira o link, a visibilidade e a conexão.';
  sources[0].status = 'error';
  await page.getByRole('button', { name: 'Atualizar acompanhamentos' }).click();
  await page.getByRole('alert').filter({ hasText: 'Não foi possível acessar' }).waitFor();
  await page.locator('#watch-url').fill('https://www.youtube.com/playlist?list=PLsynthetic_test');
  await page.locator('#watch-mode').selectOption('new');
  await page.locator('[data-action="create-watcher"]').click();
  await page.getByRole('alert').filter({ hasText: 'Esta playlist já' }).waitFor();
  assert.equal(report.mutations.at(-1).body.initial_mode, 'new');
  const beforeRemove = report.mutations.length;
  await page.getByRole('button', { name: 'Remover acompanhamento', exact: true }).click();
  await page.getByRole('button', { name: 'Cancelar', exact: true }).click();
  assert.equal(report.mutations.length, beforeRemove);
  await page.getByRole('button', { name: 'Remover acompanhamento', exact: true }).click();
  await page.locator('.watch-remove .danger').click();
  await page.getByRole('heading', { name: 'Nenhuma playlist acompanhada' }).waitFor();
  report.checks.push('Populated narrow screens in three languages, lookup error, duplicate handling, future-only choice, remove/cancel');
  assert.deepEqual(report.errors, []);
})().then(() => { report.ok = true; }).catch(error => { report.ok = false; report.errors.push(error.stack); process.exitCode = 1; }).finally(async () => {
  fs.writeFileSync('/tmp/youtube-catalog-watchers-report.json', JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  if (browser) await browser.close();
  server.close();
});
