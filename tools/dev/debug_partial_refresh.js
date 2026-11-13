/**
 * 调试：PPT 部分版页面的自动刷新链路到底发生了什么。
 * 打印每一次 viewer.html / partial 响应、控制台错误、定时状态与截图。
 */
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const BASE = 'http://127.0.0.1:9012';
const NAME = process.argv[2] || 'ehs_v6.pptx';
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, payload, token) {
  const h = { 'Content-Type': 'application/json', 'X-App-Id': 'kk-demo' };
  if (token) h.Authorization = 'Bearer ' + token;
  const r = await fetch(BASE + path, {
    method: payload ? 'POST' : 'GET', headers: h,
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
      empNo: 'EMP0001', empName: '调试' }, tk)).data;
  console.log('preview/url:', d.status, 'provisional=', d.provisional);

  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.on('console', (m) => console.log('  [console]', m.type(), m.text().slice(0, 160)));
  page.on('pageerror', (e) => console.log('  [pageerror]', String(e).slice(0, 200)));
  page.on('requestfailed', (r) => console.log('  [reqfail]', r.url().slice(-70),
    r.failure() && r.failure().errorText));
  page.on('response', async (r) => {
    const u = r.url();
    if (u.includes('viewer.html') || u.includes('/partial') || u.includes('/pdf/')) {
      let hasBanner = '';
      if (u.includes('viewer.html')) {
        const b = await r.text().catch(() => '');
        hasBanner = ' kkpart=' + (b.includes('kkpart')) + ' len=' + b.length;
      }
      console.log('  [resp]', r.status(), u.replace(BASE, '').split('?')[0].slice(-40),
        'cc=' + (r.headers()['cache-control'] || '-'), hasBanner);
    }
  });

  await page.goto(d.previewUrl, { waitUntil: 'domcontentloaded', timeout: 120000 });

  for (let i = 0; i < 80; i++) {
    await sleep(2000);
    const s = await page.evaluate(() => ({
      url: location.pathname + location.hash,
      kk: !!document.getElementById('kkpart'),
      pages: window.PDFViewerApplication ? PDFViewerApplication.pagesCount : -1,
      page: window.PDFViewerApplication ? PDFViewerApplication.page : -1,
      mark: window.__m === 1,
      bodyLen: document.body ? document.body.innerHTML.length : 0,
    })).catch((e) => ({ err: String(e).slice(0, 60) }));
    console.log((((Date.now() - t0) / 1000).toFixed(0) + 's'), JSON.stringify(s));
    if (s && s.pages > 0 && !s.kk) break;
    if (i === 0) {
      await page.evaluate(() => { window.__m = 1; }).catch(() => {});
    }
  }
  await page.screenshot({ path: 'debug_partial.png' }).catch(() => {});
  console.log('截图: debug_partial.png');
  await browser.close();
})();
