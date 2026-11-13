const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox','--use-gl=swiftshader','--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1200, height: 800 });
  p.on('pageerror', e => console.log('  PAGEERR', String(e).slice(0,160)));
  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded', timeout:90000 });
  await sleep(8000);
  const d = await p.evaluate(() => window.__stepDebug ? window.__stepDebug() : 'no debug');
  console.log(JSON.stringify(d));
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
