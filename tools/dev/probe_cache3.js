// 对比：带自定义头 vs 令牌放 query，看哪种能命中缓存（用小文件 /cadvec）
const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox'] });
  const p = await b.newPage();
  await p.goto(BASE + '/health', { waitUntil:'domcontentloaded' });
  const run = (label, mode) => p.evaluate(async (fk, kkt, mode) => {
    const out = [];
    for (let i = 0; i < 2; i++) {
      const url = mode === 'hdr' ? '/cadvec/' + fk : '/cadvec/' + fk + '?kkt=' + encodeURIComponent(kkt);
      const opt = mode === 'hdr' ? { headers: { 'X-Preview': kkt } } : {};
      const res = await fetch(url, opt);
      const buf = await res.arrayBuffer();
      out.push(res.status + '/' + buf.byteLength);
    }
    return out.join('  →  ');
  }, FK, KKT, mode);
  console.log('  带头部:   ' + await run('hdr', 'hdr'));
  console.log('  带 query: ' + await run('q', 'q'));
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
