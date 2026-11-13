const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4], OUT = process.argv[5];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox'] });
  const p = await b.newPage();
  await p.setViewport({ width:1400, height:900 });
  await p.goto(BASE + '/cadv/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded', timeout:90000 });
  await p.waitForFunction(() => { const l=document.getElementById('ld'); return l && l.className.indexOf('hide')>=0; }, { timeout:120000 });
  await sleep(600);
  // 放大到左侧设备区
  await p.mouse.move(560, 420);
  for (let i=0;i<6;i++){ await p.mouse.wheel({ deltaY: -240 }); await sleep(180); }
  await sleep(900);
  await p.screenshot({ path: OUT });
  console.log('saved', OUT);
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
