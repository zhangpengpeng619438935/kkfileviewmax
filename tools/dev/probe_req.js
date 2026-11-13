const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox'] });
  const p = await b.newPage();
  await p.setCacheEnabled(true);   // 显式开启，排除无头模式默认禁用缓存
  p.on('request', r => {
    if (r.url().indexOf('/cadvec/') >= 0) {
      const h = r.headers();
      console.log('  REQ if-none-match=' + (h['if-none-match'] || '(无)') +
                  ' cache-control=' + (h['cache-control'] || '(无)'));
    }
  });
  p.on('response', async r => { if (r.url().indexOf('/cadvec/') >= 0) console.log('  RES ' + r.status()); });
  await p.goto(BASE + '/health', { waitUntil:'domcontentloaded' });
  await p.evaluate(async (fk, kkt) => {
    for (let i = 0; i < 2; i++) {
      const res = await fetch('/cadvec/' + fk, { headers: { 'X-Preview': kkt } });
      await res.arrayBuffer();
    }
  }, FK, KKT);
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
