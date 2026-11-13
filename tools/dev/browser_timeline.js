/**
 * 浏览器时间线：打开预览页，打印首屏绘制时间 + 每个请求的耗时，用于定位"慢在哪一步"。
 *
 * 用法: node browser_timeline.js <文件名> [等待秒数]
 */
const puppeteer = require('puppeteer-core');

const BASE = 'http://127.0.0.1:9012';
const NAME = process.argv[2] || 'cad_small.dwg';
const WAIT = Number(process.argv[3] || 15);
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
  const d = (await api('/api/v1/preview/url',
    { fileUrl: 'http://127.0.0.1:8899/' + encodeURIComponent(NAME),
      empNo: 'EMP0001', empName: '时间线' }, tk)).data;
  console.log('preview/url:', d.status, 'provisional=', d.provisional);

  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });

  const t0 = Date.now();
  const reqs = [];
  page.on('request', (r) => reqs.push({ url: r.url(), start: Date.now() - t0, end: 0 }));
  page.on('requestfinished', (r) => {
    const it = reqs.find((x) => x.url === r.url() && !x.end);
    if (it) it.end = Date.now() - t0;
  });
  page.on('requestfailed', (r) => {
    const it = reqs.find((x) => x.url === r.url() && !x.end);
    if (it) { it.end = Date.now() - t0; it.failed = true; }
  });
  page.on('console', (m) => {
    if (m.type() === 'error') console.log('  [console.error]', m.text().slice(0, 140));
  });

  await page.goto(d.previewUrl, { waitUntil: 'domcontentloaded', timeout: 120000 });
  const tDom = Date.now() - t0;

  // 首屏绘制（浏览器 paint timing）
  let paints = {};
  try {
    paints = await page.evaluate(() => {
      const o = {};
      performance.getEntriesByType('paint').forEach((p) => {
        o[p.name] = Math.round(p.startTime);
      });
      return o;
    });
  } catch (e) {}

  await sleep(WAIT * 1000);
  const dom = await page.evaluate(() => ({
    url: location.pathname,
    text: document.title,
    canvas: document.querySelectorAll('canvas').length,
    imgs: Array.from(document.images).filter((i) => i.naturalWidth > 0).length,
    svgs: document.querySelectorAll('svg').length,
    bodyLen: document.body ? document.body.innerHTML.length : 0,
  })).catch((e) => ({ err: String(e) }));

  console.log('DOMContentLoaded:', tDom + 'ms', ' 首屏绘制:', JSON.stringify(paints));
  console.log('可见内容:', JSON.stringify(dom));
  console.log('请求时间线（>150ms 的请求）:');
  reqs.filter((r) => (r.end - r.start) > 150).sort((a, b) => a.start - b.start)
    .slice(0, 14).forEach((r) => {
      console.log('  %sms 起, 耗时 %sms%s  %s', r.start, r.end - r.start,
        r.failed ? ' [失败]' : '', r.url.replace(BASE, '').slice(0, 96));
    });
  await page.screenshot({ path: 'timeline_' + NAME.replace(/\W+/g, '_') + '.png' });
  await browser.close();
})();
