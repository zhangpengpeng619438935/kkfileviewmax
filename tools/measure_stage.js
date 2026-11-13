/**
 * 分阶段测量 DWG / STEP 首开耗时：接口 → 页面 → 产物下载 → 解析/首屏。
 * 用法: node measure_stage.js <文件名> [最长等待秒]
 */
const fs = require('fs');
const puppeteer = require('puppeteer-core');

const BASE = 'http://127.0.0.1:9012';
const SRC = 'http://127.0.0.1:8899/';
const name = process.argv[2] || 'v_dwg_part.dwg';
const waitSec = +(process.argv[3] || 60);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const tk = (await (await fetch(BASE + '/api/v1/auth/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-App-Id': 'kk-demo' },
    body: JSON.stringify({ appId: 'kk-demo', appSecret: 'your-app-secret' }),
  })).json()).data.accessToken;

  const t0 = Date.now();
  const d = (await (await fetch(BASE + '/api/v1/preview/url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-App-Id': 'kk-demo', Authorization: 'Bearer ' + tk },
    body: JSON.stringify({ fileUrl: SRC + encodeURIComponent(name), empNo: 'EMP0001', empName: '计时' }),
  })).json()).data;
  const apiMs = Date.now() - t0;
  console.log(`[1] preview/url  ${apiMs}ms  status=${d.status}  key=${String(d.fileKey || '').slice(0, 12)}`);

  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const p = await b.newPage();
  await p.setViewport({ width: 1500, height: 950, deviceScaleFactor: 1 });

  // 记录所有"大块"资源的分段耗时
  const rows = [];
  p.on('request', (r) => {
    const u = r.url();
    if (/\.(js|css|woff2?|ico|png|jpg|gif|svg)\b/.test(u)) return;
    rows.push({ u, method: r.method(), start: 0, tfb: 0, end: 0, size: 0, fromCache: false, status: 0 });
  });
  p.on('response', async (r) => {
    const it = rows.find((x) => x.u === r.url() && !x.status);
    if (!it) return;
    it.status = r.status();
    it.fromCache = r.fromCache();
    const len = r.headers()['content-length'];
    it.size = len ? +len : (r.headers()['content-encoding'] ? 'gzip' : 0);
  });
  p.on('requestfinished', (r) => {
    const it = rows.find((x) => x.u === r.url() && !x.end);
    if (!it) return;
    try {
      const t = r.timing();
      it.start = t.startTime; it.tfb = t.responseStart; it.end = t.responseEnd;
    } catch (e) {}
  });

  const tNav = Date.now();
  await p.goto(d.previewUrl, { waitUntil: 'domcontentloaded', timeout: 120000 });
  const navMs = Date.now() - tNav;
  console.log(`[2] 首屏 HTML    ${navMs}ms  落点=${await p.evaluate(() => location.pathname + location.search.slice(0, 40))}`);

  // 等目标内容出现（pdf 页数 / 3D 顶点）
  let state = null;
  const tWait = Date.now();
  for (let i = 0; i < waitSec * 2; i++) {
    state = await p.evaluate(() => {
      const app = window.PDFViewerApplication;
      const canvas = document.querySelector('canvas');
      return {
        pages: app ? app.pagesCount : -1,
        page: app ? app.page : -1,
        canvas: !!canvas && canvas.width > 0,
        three: !!(window._kkInfo),
        body: document.body.innerText.replace(/\s+/g, ' ').slice(0, 60),
      };
    }).catch(() => null);
    if (state && (state.pages > 0 || state.three)) break;
    await sleep(500);
  }
  const contentMs = Date.now() - tWait;
  console.log(`[3] 内容可见    ${contentMs}ms  pages=${state && state.pages} canvas=${state && state.canvas}`);
  console.log(`    文本: ${state && state.body}`);

  // 网络分段
  console.log('[4] 关键请求（含 gzip/大小/等待/下载）:');
  rows.filter((r) => r.end > 0).sort((a, b2) => b2.end - a.end).slice(0, 8).forEach((r) => {
    const wait = Math.max(0, r.tfb - 0).toFixed(0);
    const dl = Math.max(0, r.end - r.tfb).toFixed(0);
    const sz = typeof r.size === 'number' ? (r.size / 1048576).toFixed(1) + 'MB' : r.size;
    console.log(`    ${String(r.status).padEnd(4)} ${String(wait + 'ms wait').padEnd(12)} ${String(dl + 'ms dl').padEnd(11)} ${String(sz).padEnd(8)} ${r.u.replace(BASE, '').slice(0, 96)}`);
  });

  await p.screenshot({ path: 'stage_' + name.replace(/[^\w.]/g, '_') + '.png' });
  await b.close();
})();
