const puppeteer = require('puppeteer-core');
const BASE = 'http://127.0.0.1:9012';
const name = process.argv[2];
const tag = process.argv[3] || 'x';
const waitSec = +(process.argv[4] || 40);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const tk = (await (await fetch(BASE + '/api/v1/auth/token', {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-App-Id': 'kk-demo'},
    body: JSON.stringify({appId: 'kk-demo', appSecret: 'your-app-secret'}),
  })).json()).data.accessToken;
  const t0 = Date.now();
  const d = (await (await fetch(BASE + '/api/v1/preview/url', {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-App-Id': 'kk-demo', Authorization: 'Bearer ' + tk},
    body: JSON.stringify({fileUrl: 'http://127.0.0.1:8899/' + encodeURIComponent(name), empNo: 'EMP0001', empName: 'univer'}),
  })).json()).data;
  console.log(`[${tag}] preview/url ${Date.now() - t0}ms status=${d.status}`);
  const b = await puppeteer.launch({executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--no-sandbox']});
  const p = await b.newPage();
  await p.setViewport({width: 1440, height: 900});
  const errs = [];
  p.on('pageerror', (e) => errs.push('PAGEERR ' + String(e).slice(0, 160)));
  p.on('console', (m) => { if (m.type() === 'error') errs.push('CONS ' + m.text().slice(0, 160)); });
  const t1 = Date.now();
  await p.goto(d.previewUrl, {waitUntil: 'domcontentloaded', timeout: 120000}).catch((e) => errs.push('NAV ' + e.message));
  console.log(`[${tag}] 落地 ${Date.now() - t1}ms → ${await p.evaluate(() => location.pathname)}`);
  let st = null;
  for (let i = 0; i < waitSec * 2; i++) {
    st = await p.evaluate(() => ({
      canvas: Array.from(document.querySelectorAll('canvas')).filter((c) => c.width > 50).length,
      mask: /on/.test((document.getElementById('mask') || {}).className || ''),
      tip: (document.getElementById('tip') || {}).textContent || '',
    })).catch(() => null);
    if (st && st.canvas > 0 && !st.mask) break;
    await sleep(500);
  }
  console.log(`[${tag}] 渲染完成 ${Date.now() - t1}ms canvas=${st && st.canvas} mask=${st && st.mask} tip=${st && st.tip}`);
  if (errs.length) console.log(`[${tag}] 错误: ${errs.slice(0, 3).join(' | ')}`);
  await sleep(1200);
  await p.screenshot({path: 'univer_' + tag + '.png'});
  await b.close();
})();
