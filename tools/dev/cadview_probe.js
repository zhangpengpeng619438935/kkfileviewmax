const fs = require('fs');
const puppeteer = require('puppeteer-core');
const KEY = '18c460c8e0e6ae14901948c83bf7739311ad85f328d83e84bdfe6ccc30136875';
const TOK = fs.readFileSync('./cadtok.txt', 'utf8').trim();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--no-sandbox']});
  const p = await b.newPage();
  await p.setViewport({width: 1500, height: 900, deviceScaleFactor: 1});
  const t0 = Date.now();
  const reqs = [];
  p.on('requestfinished', (r) => {
    const t = r.timing ? r.timing() : null;
    if (t && t.responseEnd && /cadpng/.test(r.url())) reqs.push({u: r.url().replace(/^.*\?/, ''), end: t.responseEnd});
  });
  await p.goto(`http://127.0.0.1:9012/cadview/${KEY}?kkt=${encodeURIComponent(TOK)}`, {waitUntil: 'networkidle2', timeout: 90000});
  let st = null;
  for (let i = 0; i < 80; i++) {
    st = await p.evaluate(() => {
      const im = document.getElementById('img');
      return {nat: im.naturalWidth, w: im.style.width, complete: im.complete, ttl: document.querySelector('.ttl').textContent};
    });
    if (st.nat > 0 && st.complete) break;
    await sleep(200);
  }
  console.log('[1] 图纸可见 ' + (Date.now() - t0) + 'ms  原图宽=' + st.nat + 'px  显示=' + st.w);
  await p.screenshot({path: 'cadview_1.png'});
  // 点 200% 看高清档
  const t1 = Date.now();
  for (const btn of await p.$$('#bar button[data-z]')) {
    const z = await p.evaluate((e) => e.getAttribute('data-z'), btn);
    if (z === '2') { await btn.click(); break; }
  }
  await sleep(2500);
  const st2 = await p.evaluate(() => ({nat: document.getElementById('img').naturalWidth, w: document.getElementById('img').style.width}));
  console.log('[2] 200% 后 原图宽=' + st2.nat + ' 显示=' + st2.w + '  ' + (Date.now() - t1) + 'ms');
  await p.screenshot({path: 'cadview_2.png'});
  console.log('[3] 位图请求:', reqs.map((r) => r.u + '@' + r.end.toFixed(0) + 'ms').join(' | '));
  await b.close();
})();
