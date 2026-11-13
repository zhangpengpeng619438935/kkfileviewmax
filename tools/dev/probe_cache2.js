const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox','--use-gl=swiftshader','--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded' });
  await sleep(6000);
  // 在同一页里连发两次同样的请求，看第二次是否 304
  const r = await p.evaluate(async (fk, kkt) => {
    const out = [];
    for (let i = 0; i < 2; i++) {
      const t0 = performance.now();
      const res = await fetch('/stepdata/' + fk, { headers: { 'X-Preview': kkt } });
      const buf = await res.arrayBuffer();
      out.push({ status: res.status, bytes: buf.byteLength, ms: Math.round(performance.now() - t0) });
    }
    return out;
  }, FK, KKT);
  r.forEach((x, i) => console.log('  第' + (i+1) + '次: status=' + x.status + ' 字节=' + x.bytes + ' 耗时=' + x.ms + 'ms'));
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
