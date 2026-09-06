const { chromium } = require('playwright');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const assert = require('node:assert/strict');
// Serves only the compiled frontend; every API response is an in-memory fixture.
// No real generation, transcription, catalog mutation, or external request is allowed.
const dist = path.resolve(__dirname, '../frontend/dist');
const report = { checks: [], errors: [], postRequests: [] };
const videos = Array.from({ length: 15 }, (_, i) => ({ id: `source${String(i).padStart(5, '0')}`, title: `Source ${i + 1}: useful research`, channel: 'Research channel', channel_id: 'research', duration: 620 + i, language: 'en', published_at: '2026-06-05', added_at: '2026-09-06', deleted_at: null, purge_pending: false, purge_error: null, available: i !== 8, has_method: true, has_speakers: false, categories: [{ id: 'research', name: 'Research' }], tags: ['Research'], thumbnail_url: '/mock-thumbnail.svg' }));
let jobs = [], available = true;
const job = { id: 'skill001', title: 'Research workflow', objective: 'Build a useful research workflow.', video_ids: videos.slice(0, 3).map(v => v.id), language: 'en', status: 'completed', stage: 'completed', created_at: '2026-09-06T14:40:00Z', updated_at: '2026-09-06T14:41:00Z', progress: 100, warnings: ['Review this generated content before use.'], files: [{ path: 'SKILL.md', content: '---\nname: research\ndescription: Test package\n---\n# Research workflow\n\n## Steps\n\nRead the sources and create the result.\n\n[Evidence](references/video-source00000.md#segment-123)\n\n![Remote image](https://remote.invalid/image.png)\n<script>window.__executed=true</script>' }, { path: 'references/video-source00000.md', content: '# Source one\n\n' + 'Neutral original source text.\n\n'.repeat(1000) + '### segment-123\n\nThe cited passage is here.\n' + 'Additional source text.\n\n'.repeat(100) }, { path: 'references/sources.md', content: '# Sources\n\n[Full source](video-source00000.md)' }] };
const server = http.createServer((req, res) => { const rawPath = decodeURIComponent(new URL(req.url, 'http://localhost').pathname); const file = path.join(dist, rawPath === '/' ? 'index.html' : rawPath); if (!file.startsWith(dist + path.sep)) { res.writeHead(403).end(); return; } try { const ext = path.extname(file); res.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff': 'font/woff', '.woff2': 'font/woff2', '.svg': 'image/svg+xml' })[ext] || 'application/octet-stream'); res.end(fs.readFileSync(file)); } catch { res.writeHead(404).end(); } });
let browser;
(async () => {
  await new Promise(resolve => server.listen(18867, '127.0.0.1', resolve));
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  await context.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.pathname === '/mock-thumbnail.svg') return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="225"><rect width="400" height="225" fill="#0b253a"/><text x="28" y="130" fill="#8dbbff" font-family="Arial" font-size="42">rw / ai</text></svg>' });
    if (!url.pathname.startsWith('/api/')) { if (url.origin !== 'http://127.0.0.1:18867') { report.errors.push(`External request ${url}`); return route.abort(); } return route.continue(); }
    let result;
    const pathname = url.pathname.slice(4);
    if (pathname === '/skills/status') result = { available, model: 'qwen2.5-coder:7b', models: ['qwen2.5-coder:7b'], ...(available ? {} : { error_code: 'skill_model_unavailable' }) };
    else if (pathname === '/skills/suggestions') result = { items: [{ id: 'combination1', title: 'Complementary research', reason: 'Shared topics with distinct source perspectives.', description: 'Research methods', category: 'Research', shared_tags: ['Research'], video_ids: videos.slice(0, 3).map(v => v.id), videos: videos.slice(0, 3) }], total: 1 };
    else if (pathname === '/skills' && req.method() === 'POST') { const payload = req.postDataJSON(); report.postRequests.push(payload); result = { ...job, ...payload, status: 'reading', stage: 'reading', progress: 20, files: [] }; jobs = [result]; }
    else if (pathname === '/skills') result = { items: jobs, total: jobs.length, page: 1, page_size: 10 };
    else if (pathname === '/skills/skill001') result = jobs[0];
    else if (pathname === '/skills/skill001/cancel') { jobs = [{ ...jobs[0], status: 'cancelled', stage: 'cancelled', updated_at: '2026-09-06T14:42:00Z' }]; result = jobs[0]; }
    else if (pathname === '/skills/skill001/retry') { jobs = [{ ...jobs[0], status: 'reading', stage: 'reading', updated_at: '2026-09-06T14:43:00Z' }]; result = jobs[0]; }
    else if (pathname === '/stats') result = { videos: 15, deleted_videos: 0, channels: 1, categories: 1, methods: 15, hours: 3, languages: [{ id: 'en', name: 'English', count: 15 }], scanning: false, last_scan: null, warnings: [] };
    else if (pathname === '/categories') result = [{ id: 'research', name: 'Research', count: 15 }];
    else if (pathname === '/channels') result = [{ id: 'research', name: 'Research channel', count: 15 }];
    else if (pathname === '/videos') { const size = Number(url.searchParams.get('page_size') || 24); const page = Number(url.searchParams.get('page') || 1); result = { items: videos.slice((page - 1) * size, page * size), total: videos.length, page, page_size: size }; }
    else if (pathname.startsWith('/videos/')) { const source = videos.find(v => v.id === pathname.split('/')[2]); result = { ...source, description: '', method: '# Method', downloads: [], category_override: false, suggestions: [] }; }
    else throw new Error(`Unexpected API ${req.method()} ${pathname}`);
    return route.fulfill({ status: pathname === '/skills' && req.method() === 'POST' ? 202 : 200, contentType: 'application/json', body: JSON.stringify(result) });
  });
  const page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  await page.goto('http://127.0.0.1:18867/#/skills', { waitUntil: 'networkidle' });
  for (const [locale, heading] of [['pt', 'Gerar skill'], ['en', 'Generate skill'], ['es', 'Generar skill']]) {
    await page.locator('#interface-language').selectOption(locale);
    assert.equal(await page.locator('h1').innerText(), heading);
    assert.equal(await page.locator('#skill-language').inputValue(), locale);
    assert(await page.locator('[data-action="generate-skill"]').isDisabled());
    await page.setViewportSize({ width: 412, height: 800 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: `/tmp/youtube-catalog-skills-${locale}-mobile.png`, fullPage: true });
    await page.setViewportSize({ width: 1366, height: 900 });
    if (locale === 'en') { await page.evaluate(() => scrollTo(0, 0)); await page.screenshot({ path: '/tmp/youtube-catalog-skills-en-desktop.png' }); }
  }
  report.checks.push('PT/EN/ES initial UI, output language default, mobile width, empty selection guard');
  await page.locator('#interface-language').selectOption('en');
  await page.locator('[data-action="use-suggestion"]').click();
  assert.equal(await page.locator('[data-remove-source]').count(), 3);
  await page.locator('.skill-manual-toggle').click();
  await page.locator('#skill-search').fill('research'); await page.locator('#skill-search').press('Enter');
  assert.equal(report.postRequests.length, 0);
  for (let i = 3; i < 8; i++) await page.locator(`[data-select-source="${videos[i].id}"]`).check();
  assert.equal(await page.locator('[data-remove-source]').count(), 8);
  assert(await page.locator(`[data-select-source="${videos[8].id}"]`).isDisabled());
  await page.locator('[data-remove-source]').last().click();
  await page.locator('[data-remove-source]').last().click();
  assert.equal(await page.locator('[data-remove-source]').count(), 6);
  assert(await page.locator(`[data-select-source="${videos[8].id}"]`).isDisabled());
  await page.locator('.sidebar nav a[href="#/"]').click();
  await page.locator('.skill-home-cta').click();
  await page.waitForFunction(() => document.querySelectorAll('[data-remove-source]').length === 6);
  assert.equal(await page.locator('[data-remove-source]').count(), 6);
  report.checks.push('Editable combination, manual selection maximum8, remove, navigation persistence, Enter search does not submit');
  available = false; await page.locator('[data-action="check-skill-status"]').click();
  await page.getByText('Local AI unavailable', { exact: true }).waitFor();
  assert(await page.locator('[data-action="generate-skill"]').isDisabled());
  available = true; await page.locator('[data-action="check-skill-status"]').click();
  await page.getByText('Local AI ready', { exact: true }).waitFor();
  await page.locator('#skill-objective').fill('Build a useful research workflow.');
  await page.locator('#skill-language').selectOption('es');
  await page.locator('[data-action="generate-skill"]').click();
  await page.locator('[data-skill-id="skill001"]').waitFor();
  assert.equal(report.postRequests.length, 1); assert.equal(report.postRequests[0].video_ids.length, 6); assert.equal(report.postRequests[0].language, 'es');
  assert(await page.locator('[data-action="generate-skill"]').isDisabled());
  await page.locator('[data-skill-action="cancel"]').click();
  await page.locator('[data-skill-action="retry"]').waitFor();
  await page.locator('[data-skill-action="retry"]').click();
  await page.locator('[data-skill-action="cancel"]').waitFor();
  report.checks.push('Local AI unavailable guard; one POST with source/language/objective; queued progress; cancel/retry');
  jobs = [{ ...job, updated_at: '2026-09-06T14:44:00Z' }];
  await page.locator('[data-action="refresh-skills"]').click();
  await page.locator('.skill-markdown h1').waitFor();
  assert.equal(await page.locator('.skill-markdown h1').innerText(), 'Research workflow');
  assert.equal(await page.locator('[data-skill-action="download"]').getAttribute('href'), '/api/skills/skill001/download.zip');
  assert.equal(await page.evaluate(() => window.__executed), undefined);
  await page.getByRole('button', { name: 'Evidence', exact: true }).click();
  await page.locator('[data-package-anchor="segment-123"]').waitFor();
  assert((await page.locator('.skill-markdown').innerText()).includes('The cited passage is here.'));
  await page.waitForFunction(() => { const box = document.querySelector('[data-package-anchor="segment-123"]').getBoundingClientRect(); return box.top >= 0 && box.top < innerHeight; });
  assert(await page.locator('[data-package-file="references/video-source00000.md"]').getAttribute('aria-pressed') === 'true');
  await page.locator('[data-package-file="SKILL.md"]').click();
  await page.setViewportSize({ width: 412, height: 800 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.locator('.skill-job').evaluate(el => window.scrollTo(0, el.getBoundingClientRect().top + scrollY - 20));
  await page.screenshot({ path: '/tmp/youtube-catalog-skills-package-mobile.png' });
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.locator('.skill-file-preview').evaluate(el => window.scrollTo(0, el.getBoundingClientRect().top + scrollY - 20));
  await page.screenshot({ path: '/tmp/youtube-catalog-skills-package-desktop.png' });
  await page.goto('http://127.0.0.1:18867/#/skills?video=source00001', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.querySelectorAll('[data-remove-source]').length === 1);
  assert.equal(await page.locator('[data-remove-source]').count(), 1);
  assert.equal(await page.locator('[data-remove-source]').getAttribute('data-remove-source'), 'source00001');
  await page.goto('http://127.0.0.1:18867/#/skills?video=source00008', { waitUntil: 'networkidle' });
  await page.locator('.skill-settings .error-box').waitFor();
  assert.equal(await page.locator('[data-remove-source]').getAttribute('data-remove-source'), 'source00001');
  assert((await page.locator('.skill-settings').innerText()).includes('The selected sources are unavailable'));
  report.checks.push('Detail preselection; unavailable sources cannot be selected');
  report.checks.push('Safe Markdown package preview, no HTML or remote-image execution, internal reference anchor on long source, download link, responsive package');
  assert.deepEqual(report.errors, []);
  report.passed = true;
})().catch(error => { report.passed = false; report.error = error.stack; process.exitCode = 1; }).finally(async () => { if (browser) await browser.close(); server.close(); fs.writeFileSync('/tmp/youtube-catalog-skills-ui-report.json', JSON.stringify(report, null, 2)); console.log(JSON.stringify(report, null, 2)); });
