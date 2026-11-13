const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox'] });
  const p = await b.newPage();
  await p.goto(BASE + '/health', { waitUntil:'domcontentloaded' });
  const h = await p.evaluate(async (fk, kkt) => {
    const res = await fetch('/cadvec/' + fk, { headers: { 'X-Preview': kkt } });
    const o = {};
    res.headers.forEach((v, k) => { o[k] = v; });
    return o;
  }, FK, KKT);
  console.log(JSON.stringify(h, null, 1));
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
