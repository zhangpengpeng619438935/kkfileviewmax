const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox','--use-gl=swiftshader','--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width:1200, height:800 });
  p.on('response', r => { if (/stepdata|stepview/.test(r.url())) console.log('  RES', r.status(), r.url().replace(/\?.*/,'')); });
  p.on('console', m => { if (m.type()==='error') console.log('  CONSOLE', m.text().slice(0,200)); });
  p.on('pageerror', e => console.log('  PAGEERR', String(e).slice(0,200), '\n  STACK', (e.stack||'').split('\n').slice(0,5).join(' | ')));
  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded', timeout:90000 });
  await sleep(12000);
  console.log('  url:', p.url().replace(/\?.*/,''));
  console.log('  info:', await p.evaluate(() => (document.getElementById('info')||{}).textContent || '(none)'));
  console.log('  ldt:', await p.evaluate(() => (document.getElementById('ldt')||{}).textContent || '(none)'));
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
