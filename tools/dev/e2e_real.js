// 真实文件端到端耗时测量：申请 → 页面打开 → 首屏内容可见
// 用法: node tools/e2e_real.js "<fileUrl>" [文件显示名]
const puppeteer = require('puppeteer-core');
const BASE = 'http://127.0.0.1:9012';
const fileUrl = process.argv[2];
const label = process.argv[3] || 'file';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const tk = (await (await fetch(BASE + '/api/v1/auth/token', {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-App-Id': 'kk-demo'},
    body: JSON.stringify({appId: 'kk-demo', appSecret: 'your-app-secret'}),
  })).json()).data.accessToken;

  const t0 = Date.now();
  const resp = await (await fetch(BASE + '/api/v1/preview/url', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-App-Id': 'kk-demo', Authorization: 'Bearer ' + tk},
    body: JSON.stringify({fileUrl, empNo: 'EMP0001', empName: 'E2E', fileName: label}),
  })).json();
  const t1 = Date.now();
  console.log('[1] preview/url ' + (t1 - t0) + 'ms  status=' + (resp.data && resp.data.status) + ' code=' + resp.code + ' ' + (resp.msg || ''));
  if (!resp.data || !resp.data.previewUrl) { console.log(JSON.stringify(resp).slice(0, 400)); return; }

  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox'],
  });
  const page = await browser.newPage();
  await page.setViewport({width: 1600, height: 900});
  const reqs = [];
  page.on('response', (r) => {
    const u = r.url();
    if (/\/kk\/|\/cadpng|\/univer\/|pdfview|\.pdf|\/prog\//.test(u)) {
      reqs.push({u: u.slice(0, 120), s: r.status()});
    }
  });

  const t2 = Date.now();
  await page.goto(resp.data.previewUrl, {waitUntil: 'domcontentloaded', timeout: 180000});
  const t3 = Date.now();
  const finalUrl = page.url();
  console.log('[2] 页面 DOM 就绪 ' + (t3 - t2) + 'ms  落点=' + finalUrl.replace(BASE, '').slice(0, 80));

  // 轮询"首屏内容可见"：img 有尺寸 / canvas 有绘制 / 无 loading 文案
  let ok = false, info = '';
  const t4 = Date.now();
  for (let i = 0; i < 600; i++) {
    const st = await page.evaluate(() => {
      const imgs = Array.from(document.querySelectorAll('img'))
        .filter((e) => e.naturalWidth > 200 && e.naturalHeight > 200);
      const cs = Array.from(document.querySelectorAll('canvas'))
        .filter((c) => c.width > 100 && c.height > 100);
      const txt = (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 100);
      return {imgs: imgs.length, iw: imgs[0] ? imgs[0].naturalWidth : 0, cs: cs.length, txt};
    }).catch(() => ({imgs: 0, cs: 0, txt: ''}));
    if (st.imgs > 0 || st.cs > 0) { ok = true; info = JSON.stringify(st).slice(0, 140); break; }
    if (i % 10 === 9 && st.txt) info = st.txt;
    await sleep(100);
  }
  console.log('[3] 内容可见 ' + (Date.now() - t4) + 'ms（自 DOM 就绪）  ' + info);
  console.log('[T] 端到端合计 ' + (Date.now() - t0) + 'ms  可见=' + ok);
  // 等高清档位
  await sleep(2500);
  const hi = await page.evaluate(() => {
    const i = document.querySelector('#img') || document.querySelector('img');
    return i ? {w: i.naturalWidth, sw: i.style.width} : null;
  }).catch(() => null);
  console.log('[4] 2.5s 后主图 ' + JSON.stringify(hi) + ' 总耗时 ' + (Date.now() - t0) + 'ms');
  console.log('[R] 关键请求 ' + reqs.length + ' 条: ' + reqs.slice(0, 6).map((r) => r.s + ' ' + r.u).join(' | '));
  await page.screenshot({path: 'e2e_' + label.replace(/\W/g, '_').slice(0, 30) + '.png'});
  await browser.close();
})();
