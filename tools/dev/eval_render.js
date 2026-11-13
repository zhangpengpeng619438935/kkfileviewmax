const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox','--use-gl=swiftshader','--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1200, height: 800 });
  p.on('pageerror', e => console.log('  PAGEERR', String(e).slice(0,200)));
  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded', timeout:90000 });
  await sleep(6000);
  await p.screenshot({ path: 'before_render.png' });
  const r = await p.evaluate(() => { try { return window.__stepRender(); } catch(e){ return 'ERR '+e.message; } });
  console.log('  手动渲染:', r);
  await sleep(800);
  await p.screenshot({ path: 'after_render.png' });
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
