// STEP/3D 页面到底卡在哪：抓全部网络请求（含外网），标出慢请求与失败请求
// 用法: node tools/step3d_trace.js "<fileUrl>" [等待秒]
const puppeteer = require('puppeteer-core');
const BASE = 'http://127.0.0.1:9012';
const fileUrl = process.argv[2];
const waitSec = +(process.argv[3] || 60);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const tk = (await (await fetch(BASE + '/api/v1/auth/token', {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-App-Id': 'kk-demo'},
    body: JSON.stringify({appId: 'kk-demo', appSecret: 'your-app-secret'}),
  })).json()).data.accessToken;
  const d = (await (await fetch(BASE + '/api/v1/preview/url', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-App-Id': 'kk-demo', Authorization: 'Bearer ' + tk},
    body: JSON.stringify({fileUrl, empNo: 'EMP0001', empName: 'T'}),
  })).json()).data;
  console.log('[1] status=' + d.status);

  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox'],
  });
  const p = await b.newPage();
  await p.setViewport({width: 1400, height: 900});
  const rows = [];
  const t0 = Date.now();
  const started = new Map();
  p.on('request', (r) => started.set(r, Date.now()));
  p.on('requestfinished', (r) => {
    const s = started.get(r);
    rows.push({url: r.url(), ms: s ? Date.now() - s : 0, status: r.response()?.status()});
  });
  p.on('requestfailed', (r) => {
    rows.push({url: r.url(), ms: -1, status: 'FAIL ' + (r.failure()?.errorText || '')});
  });
  await p.goto(d.previewUrl, {waitUntil: 'domcontentloaded', timeout: 120000});
  console.log('[2] 落点 ' + p.url().replace(BASE, '').slice(0, 100));

  let shown = 0;
  for (let i = 0; i < waitSec; i++) {
    await sleep(1000);
    let st = {n: 0, w: '', txt: ''};
    for (const f of p.frames()) {
      const r = await f.evaluate(() => {
        const cs = Array.from(document.querySelectorAll('canvas'));
        return {n: cs.length, w: cs.map((c) => c.width + 'x' + c.height).join(','),
                txt: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 60)};
      }).catch(() => null);
      if (r && r.n > st.n) st = r;
    }
    shown = st.n;
    if (i % 3 === 2) console.log('  [' + (i + 1) + 's] canvas=' + st.n + ' (' + st.w + ') ' + st.txt);
    if (/Vertices|Triangles/i.test(st.txt)) {
      console.log('  [模型就绪 ' + ((Date.now() - t0) / 1000).toFixed(1) + 's] ' + st.txt.slice(0, 40));
      break;
    }
  }
  console.log('--- 请求明细（按耗时倒序，前 18 条）');
  rows.sort((a, b) => (b.ms | 0) - (a.ms | 0));
  rows.slice(0, 18).forEach((r) => {
    console.log(String(r.status).padEnd(8) + String(r.ms).padStart(7) + 'ms  ' + r.url.slice(0, 130));
  });
  const ext = rows.filter((r) => !/^https?:\/\/10\.6\./.test(r.url));
  console.log('--- 外网请求 ' + ext.length + ' 条');
  ext.slice(0, 10).forEach((r) => console.log('  ' + r.status + ' ' + r.url.slice(0, 120)));
  console.log('总计 ' + (Date.now() - t0) + 'ms');
  await p.screenshot({path: 'step3d_trace.png'});
  await b.close();
})();
