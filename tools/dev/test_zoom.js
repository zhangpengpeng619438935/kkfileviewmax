const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox'] });
  const p = await b.newPage();
  await p.setViewport({ width:1400, height:900 });
  await p.goto(BASE + '/cadv/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded', timeout:90000 });
  await p.waitForFunction(() => { const l=document.getElementById('ld'); return l && l.className.indexOf('hide')>=0; }, { timeout:120000 });
  await sleep(800);
  const tf = () => p.$eval('#wrap', el => el.style.transform);
  console.log('  初始:', await tf());
  await p.mouse.move(700, 450);
  await p.mouse.wheel({ deltaY: -300 });
  await sleep(400);
  console.log('  滚轮放大后:', await tf());
  await p.mouse.move(700, 450);
  await p.mouse.down();
  await p.mouse.move(760, 500, { steps: 6 });
  await p.mouse.up();
  await sleep(300);
  console.log('  左键拖拽后:', await tf());
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
