/**
 * 真浏览器验证：PPT 两阶段「部分版 → 完整版」是否自动切换（不需要用户按 F5）。
 *
 * 流程：
 *   1. 冷开一个 119 页 pptx（两阶段：先 15 页）
 *   2. 等页面出现进度横幅 #kkpart，并把阅读位置设到第 5 页
 *   3. 等页面自己 reload（不手动刷新）
 *   4. reload 后校验：总页数 119、仍停在第 5 页、横幅消失
 *
 * 用法: NODE_PATH=... node browser_partial_refresh.js <文件名>
 */
const puppeteer = require('puppeteer-core');

const BASE = 'http://127.0.0.1:9012';
const NAME = process.argv[2] || 'ehs_v3.pptx';
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, payload, token) {
  const h = { 'Content-Type': 'application/json', 'X-App-Id': 'kk-demo' };
  if (token) h.Authorization = 'Bearer ' + token;
  const r = await fetch(BASE + path, {
    method: payload ? 'POST' : 'GET',
    headers: h,
    body: payload ? JSON.stringify(payload) : undefined,
  });
  return r.json();
}

(async () => {
  const tk = (await api('/api/v1/auth/token',
    { appId: 'kk-demo', appSecret: 'your-app-secret' })).data.accessToken;
  const t0 = Date.now();
  const d = (await api('/api/v1/preview/url',
    { fileUrl: 'http://127.0.0.1:8899/' + encodeURIComponent(NAME),
      empNo: 'EMP0001', empName: '浏览器验证' }, tk)).data;
  console.log('[1] preview/url:', d.status, 'provisional=', d.provisional,
    ((Date.now() - t0) / 1000).toFixed(2) + 's');

  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.on('pageerror', (e) => console.log('    [pageerror]', String(e).slice(0, 120)));
  await page.goto(d.previewUrl, { waitUntil: 'domcontentloaded', timeout: 120000 });

  // 等进度横幅（中途会从内容页 reload 到 pdf.js viewer）
  let bannerAt = 0;
  while (Date.now() - t0 < 150000) {
    try {
      const has = await page.$('#kkpart');
      if (has) { bannerAt = Date.now(); break; }
    } catch (e) { /* 正在导航 */ }
    await sleep(1000);
  }
  if (!bannerAt) { console.log('!! 没等到进度横幅'); await browser.close(); process.exit(1); }
  const info1 = await page.evaluate(() => ({
    text: (document.getElementById('kkpart') || {}).innerText || '',
    pages: window.PDFViewerApplication ? PDFViewerApplication.pagesCount : -1,
  }));
  console.log('[2] 横幅出现 ' + ((bannerAt - t0) / 1000).toFixed(1) + 's，',
    'pdf 页数=' + info1.pages, '文案=', info1.text.replace(/\s+/g, ' ').slice(0, 60));

  // 等 PDF 真正加载完，再把阅读位置放到第 5 页（否则设置页码无效）
  for (let i = 0; i < 40; i++) {
    const n = await page.evaluate(() => (window.PDFViewerApplication
      ? PDFViewerApplication.pagesCount : 0)).catch(() => 0);
    if (n > 0) break;
    await sleep(500);
  }
  await page.evaluate(() => {
    window.__kkBefore = 1;
    if (window.PDFViewerApplication) PDFViewerApplication.page = 5;
  }).catch(() => {});
  const markAt = Date.now();
  const pageBefore = await page.evaluate(() => (window.PDFViewerApplication
    ? PDFViewerApplication.page : -1)).catch(() => -1);
  console.log('    已把阅读位置设为第', pageBefore, '页');

  let reloaded = false;
  while (Date.now() - t0 < 220000) {
    try {
      const alive = await page.evaluate(() => window.__kkBefore === 1);
      if (!alive) { reloaded = true; break; }
    } catch (e) { reloaded = true; break; }
    await sleep(500);
  }
  console.log('[3] 页面自动刷新:', reloaded ? '是' : '否',
    reloaded ? '(' + ((Date.now() - markAt) / 1000).toFixed(1) + 's 后)' : '');

  if (!reloaded) { await browser.close(); process.exit(1); }

  await sleep(1500);
  let r = null;
  for (let i = 0; i < 60; i++) {
    r = await page.evaluate(() => ({ pages: window.PDFViewerApplication ? PDFViewerApplication.pagesCount : 0,
      page: window.PDFViewerApplication ? PDFViewerApplication.page : 0,
      banner: !!document.getElementById('kkpart'),
      hash: location.hash, url: location.pathname })).catch(() => null);
    if (r && r.pages > 0) break;
    await sleep(1000);
  }
  await sleep(1500);              // 给 pdf.js 应用 #page hash 留点时间
  r = await page.evaluate(() => ({ pages: PDFViewerApplication.pagesCount,
    page: PDFViewerApplication.page,
    banner: !!document.getElementById('kkpart'),
    hash: location.hash, url: location.pathname })).catch(() => r);
  console.log('[4] 刷新后:', JSON.stringify(r));
  console.log('[结论]',
    r && r.pages === 119 && r.page === 5 && !r.banner
      ? '整本 119 页已自动显示，且停在第 5 页，横幅已消失'
      : '需人工核对');
  await browser.close();
})();
