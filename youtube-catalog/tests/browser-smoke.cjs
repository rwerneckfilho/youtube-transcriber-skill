#!/usr/bin/env node
/* Read-only browser acceptance. Never creates or edits catalog records. */
'use strict';

const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const base = (process.env.CATALOG_URL || 'http://127.0.0.1:8765').replace(/\/$/, '');
assert.ok(['localhost', '127.0.0.1', '[::1]'].includes(new URL(base).hostname), 'Use a local catalog URL');
const failures = [];
const pageErrors = [];
const externalRequests = [];
const mutationRequests = [];
const norm = (value) => value.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();

async function api(path) {
  const response = await fetch(base + '/api' + path);
  assert.equal(response.status, 200, `API ${path}: ${response.status}`);
  return response.json();
}

async function check(name, operation) {
  try {
    await operation();
    console.log(`OK     ${name}`);
  } catch (error) {
    failures.push({ name, message: error.message });
    console.error(`FALHOU ${name}: ${error.message}`);
  }
}

async function main() {
  const stats = await api('/stats');
  assert.ok(stats.videos > 0 && !stats.scanning, 'Wait until initial catalog import has finished');
  const videos = [];
  for (let page = 1; videos.length < stats.videos; page++) {
    const result = await api(`/videos?page=${page}&page_size=100`);
    assert.ok(result.items.length, 'Incomplete API pagination');
    videos.push(...result.items);
  }
  const longest = videos.reduce((best, video) => video.duration > best.duration ? video : best);
  const methodVideo = videos.find(video => video.has_method);
  const missingMethod = videos.find(video => !video.has_method);
  const speakerVideo = videos.find(video => video.has_speakers);
  const filterVideo = videos.find(video => video.categories.length);
  assert.ok(methodVideo && filterVideo, 'Method and category examples are required');

  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'pt-BR' });
  await context.addInitScript(() => localStorage.setItem('rw-ai.locale', 'pt'));
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => pageErrors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (/^https?:$/.test(url.protocol) && url.origin !== new URL(base).origin) {
      externalRequests.push(request.url());
      return route.abort('blockedbyclient');
    }
    if (url.pathname.startsWith('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      mutationRequests.push(`${request.method()} ${request.url()}`);
      return route.abort('blockedbyclient');
    }
    return route.continue();
  });

  const navigation = () => page.getByRole('navigation', { name: 'Navegação principal' });
  async function gotoHash(path) {
    await page.goto(base + '/#' + path, { waitUntil: 'domcontentloaded' });
  }
  async function reader(video, segment) {
    await gotoHash(`/video/${video.id}${segment === undefined ? '' : `?segment=${segment}`}`);
    await page.getByRole('heading', { name: video.title, exact: true }).waitFor();
    await page.locator('.segments .segment').first().waitFor();
    await page.waitForFunction(() => document.querySelector('.segments')?.getAttribute('aria-busy') === 'false');
  }
  async function expectCount(selector, expected) {
    await page.waitForFunction(({ selector, expected }) => document.querySelectorAll(selector).length === expected, { selector, expected });
    assert.equal(await page.locator(selector).count(), expected);
  }
  async function expectResultCount(expected) {
    await page.waitForFunction(expected => Number(document.querySelector('.results-heading strong')?.textContent.replace(/\D/g, '')) === expected, expected);
    await expectCount('.video-grid .video-card', Math.min(expected, 24));
  }
  async function noHorizontalOverflow() {
    const sizes = await page.evaluate(() => ({ width: innerWidth, body: document.body.scrollWidth, root: document.documentElement.scrollWidth }));
    assert.ok(sizes.body <= sizes.width + 1 && sizes.root <= sizes.width + 1, `Horizontal overflow: ${JSON.stringify(sizes)}`);
  }
  async function waitForVisibleImages() {
    await page.evaluate(async () => {
      const images = [...document.images].filter(image => {
        const rect = image.getBoundingClientRect();
        return rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth;
      });
      await Promise.all(images.map(image => image.decode().catch(() => {})));
    });
  }

  try {
    await check('início, contador e prateleiras por categoria/canal', async () => {
      await gotoHash('/');
      await page.getByRole('heading', { name: 'Grandes ideias. Sempre à mão.' }).waitFor();
      await page.waitForFunction(count => Number(document.querySelector('.collection-counter strong')?.textContent.replace(/\D/g, '')) === count, stats.videos);
      await page.locator('.hero h2').waitFor();
      await page.locator('.shelf').first().locator('.video-card').first().waitFor();
      await noHorizontalOverflow();
      await waitForVisibleImages();
      await page.screenshot({ path: '/tmp/youtube-catalog-home.png', fullPage: false });
      await page.getByRole('button', { name: 'Por canal', exact: true }).click();
      assert.equal(await page.getByRole('button', { name: 'Por canal', exact: true }).getAttribute('aria-pressed'), 'true');
      const channels = await api('/channels');
      await page.getByRole('heading', { name: channels.find(channel => channel.count > 0).name, exact: true }).waitFor();
      await page.getByRole('button', { name: 'Por categoria', exact: true }).click();
    });

    await check('todos os vídeos, paginação e filtros combinados', async () => {
      await gotoHash('/videos');
      await page.getByRole('heading', { name: 'Todos os vídeos', exact: true }).waitFor();
      await expectResultCount(stats.videos);
      if (stats.videos > 24) {
        const firstId = await page.locator('.video-grid .video-card').first().getAttribute('href');
        await page.getByRole('button', { name: 'Próxima', exact: true }).click();
        await page.waitForFunction(() => location.hash.includes('page=2'));
        await page.waitForFunction(firstId => document.querySelector('.video-grid .video-card')?.getAttribute('href') !== firstId, firstId);
        assert.notEqual(await page.locator('.video-grid .video-card').first().getAttribute('href'), firstId);
      }
      await page.getByRole('combobox', { name: 'Canal', exact: true }).selectOption(filterVideo.channel_id);
      await page.getByRole('combobox', { name: 'Categoria', exact: true }).selectOption(filterVideo.categories[0].id);
      await page.getByRole('combobox', { name: 'Idioma', exact: true }).selectOption(filterVideo.language);
      const expected = videos.filter(video => video.channel_id === filterVideo.channel_id && video.language === filterVideo.language && video.categories.some(category => category.id === filterVideo.categories[0].id));
      await expectResultCount(expected.length);
      await page.getByRole('combobox', { name: 'Ordenar por' }).selectOption('title');
      await expectResultCount(expected.length);
      await page.getByRole('link', { name: 'Limpar', exact: true }).click();
      await expectResultCount(stats.videos);
    });

    await check('busca sem acento, trecho encontrado e destaque no leitor', async () => {
      const candidates = videos.flatMap(video => (video.title.match(/\p{L}+/gu) || []).filter(word => word.length >= 5 && norm(word) !== word.toLowerCase()).map(word => ({ video, word })));
      candidates.sort((a, b) => b.word.length - a.word.length);
      assert.ok(candidates.length, 'No accented title available');
      const { video, word } = candidates[0];
      const search = page.getByRole('textbox', { name: 'Buscar em todo o acervo' });
      await search.fill(norm(word).toUpperCase());
      await search.press('Enter');
      await page.getByRole('heading', { name: 'Encontre aquela ideia.', exact: true }).waitFor();
      await page.locator(`.video-card[href^="#/video/${video.id}"]`).waitFor();
      const result = await api(`/videos?q=${encodeURIComponent(norm(word))}&page_size=100`);
      const matching = result.items.find(item => item.match?.segment_index !== undefined && item.match?.segment_index !== null);
      assert.ok(matching, 'Search returned no transcript jump target');
      const expectedHref = `#/video/${matching.id}?segment=${matching.match.segment_index}`;
      await page.locator(`.video-card[href="${expectedHref}"]`).click();
      await page.locator(`#segment-${matching.match.segment_index}.highlighted`).waitFor();
      assert.ok((await page.locator(`#segment-${matching.match.segment_index} p`).textContent()).trim());
    });

    await check('método e diálogo de categorias sem salvar', async () => {
      await reader(methodVideo);
      await page.getByRole('tab', { name: 'Método', exact: true }).click();
      await page.getByRole('heading', { name: 'Análise de método', exact: true }).waitFor();
      assert.ok((await page.locator('.markdown').textContent()).length > 150);
      assert.equal(await page.locator('.markdown script, .markdown iframe').count(), 0);
      await page.getByRole('button', { name: 'Editar', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: 'Categorias do vídeo' });
      await dialog.waitFor();
      assert.ok(await dialog.getByRole('checkbox').count() >= stats.categories);
      await page.keyboard.press('Escape');
      await dialog.waitFor({ state: 'hidden' });
      await page.getByRole('tab', { name: 'Transcrição', exact: true }).click();
      await page.locator('.segment').first().waitFor();
    });

    await check('vídeo sem método e falantes, quando presentes no acervo ativo', async () => {
      if (missingMethod) {
        await reader(missingMethod);
        assert.equal(await page.getByRole('tab', { name: 'Método', exact: true }).count(), 0);
      } else console.log('       Sem exemplo ativo de vídeo sem método; cenário não executado.');
      if (speakerVideo) {
        await reader(speakerVideo);
        await page.locator('.segment .speaker').first().waitFor();
        assert.ok(await page.locator('.download-list a').count() >= 6);
        assert.match(await page.locator('.reader-toolbar').textContent(), /Com falantes/);
      } else console.log('       Sem transcrição com falantes no acervo ativo; cenário não executado.');
    });

    await check('vídeo mais longo, carregamento progressivo e Ver contexto', async () => {
      await reader(longest);
      await expectCount('.segments .segment', 100);
      await page.getByRole('button', { name: 'Carregar próximos 100 trechos', exact: true }).click();
      await expectCount('.segments .segment', 200);
      const source = await api(`/videos/${longest.id}/segments?offset=150&limit=1`);
      const sample = source.items[0];
      const word = (sample.text.match(/\p{L}{6,}/gu) || []).sort((a, b) => b.length - a.length)[0];
      assert.ok(word, 'No useful transcript word found');
      const transcriptSearch = page.getByRole('textbox', { name: 'Buscar nesta transcrição', exact: true });
      await transcriptSearch.fill(word);
      const button = page.getByRole('button', { name: 'Ver contexto', exact: true }).first();
      await button.waitFor();
      const articleId = await page.locator('.segments .segment').first().getAttribute('id');
      assert.ok(articleId, 'Matched segment is missing its ID');
      await button.click();
      const contextStart = Math.floor(Number(articleId.replace('segment-', '')) / 100) * 100;
      await page.waitForFunction(expected => document.querySelector('.segments .segment')?.id === `segment-${expected}` && document.querySelector('.segments')?.getAttribute('aria-busy') === 'false', contextStart);
      await page.locator(`#${articleId}.highlighted`).waitFor();
      assert.equal(await page.getByRole('textbox', { name: 'Buscar nesta transcrição', exact: true }).inputValue(), '');
      assert.ok(await page.locator('.segments .segment').count() <= 100, 'Context should load only its page');
      const contextIds = await page.locator('.segments .segment').evaluateAll(elements => elements.map(element => Number(element.id.replace('segment-', ''))));
      assert.ok(contextIds.every((index, position) => index === contextStart + position), 'Context must restore contiguous transcript segments');
      await page.waitForFunction(total => document.querySelector('.reader-toolbar')?.textContent.replace(/\D/g, '') === String(total), source.total);
      await page.screenshot({ path: '/tmp/youtube-catalog-reader.png', fullPage: false });
    });

    await check('diretório de canais e busca por canal', async () => {
      await navigation().getByRole('link', { name: 'Canais', exact: true }).click();
      await page.getByRole('heading', { name: 'Vozes que você acompanha.', exact: true }).waitFor();
      await expectCount('.directory-grid .directory-card', stats.channels);
      const channels = await api('/channels');
      const channel = channels[0];
      await page.getByRole('textbox', { name: 'Buscar canais', exact: true }).fill(channel.name);
      await page.getByRole('heading', { name: channel.name, exact: true }).waitFor();
      await page.getByRole('heading', { name: channel.name, exact: true }).click();
      await expectResultCount(channel.count);
    });

    await check('375px, teclado e ausência de rolagem horizontal', async () => {
      await page.setViewportSize({ width: 375, height: 812 });
      await gotoHash('/');
      await page.locator('.hero h2').waitFor();
      await page.locator('.shelf .video-card').first().waitFor();
      await noHorizontalOverflow();
      await waitForVisibleImages();
      await page.screenshot({ path: '/tmp/youtube-catalog-mobile.png', fullPage: false });
      await page.locator('.skip-link').focus();
      await page.keyboard.press('Enter');
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'main-content');
      await gotoHash('/videos');
      await expectResultCount(stats.videos);
      await noHorizontalOverflow();
      await reader(longest);
      await noHorizontalOverflow();
      await page.getByRole('button', { name: 'Editar', exact: true }).click();
      await page.getByRole('dialog', { name: 'Categorias do vídeo' }).waitFor();
      await noHorizontalOverflow();
      await page.getByRole('button', { name: 'Fechar', exact: true }).click();
    });

    await check('recarregar e ler com toda a rede externa bloqueada', async () => {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.locator('.segments .segment').first().waitFor();
      await navigation().getByRole('link', { name: /Todos os vídeos/ }).click();
      await expectResultCount(stats.videos);
      await reader(methodVideo);
      await page.getByRole('tab', { name: 'Método', exact: true }).click();
      await page.getByRole('heading', { name: 'Análise de método', exact: true }).waitFor();
      assert.deepEqual(externalRequests, [], 'Frontend attempted external HTTP requests');
      assert.deepEqual(mutationRequests, [], 'Read-only UI check attempted to write catalog data');
      assert.deepEqual(pageErrors, [], 'Browser JavaScript errors');
    });
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify({ failures, pageErrors, externalRequests, mutationRequests,
    screenshots: ['/tmp/youtube-catalog-home.png', '/tmp/youtube-catalog-reader.png', '/tmp/youtube-catalog-mobile.png'] }, null, 2));
  if (failures.length || pageErrors.length || externalRequests.length || mutationRequests.length) process.exitCode = 1;
}

main().catch(error => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
