const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox'] });
  const p = await b.newPage();
  await p.setViewport({ width:1400, height:900 });
  p.on('pageerror', e => console.log('  PAGEERR', String(e).slice(0,140)));
  await p.goto(BASE + '/cadview/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded', timeout:90000 });
  await sleep(4000);
  const st = () => p.evaluate(() => {
    const i=document.getElementById('img'), s=document.getElementById('stage');
    return { w: i ? i.style.width : '-', sl: s ? Math.round(s.scrollLeft) : 0 };
  });
  console.log('  初始:', JSON.stringify(await st()));
  await p.mouse.move(700, 450);
  await p.mouse.wheel({ deltaY: -300 });
  await sleep(900);
  console.log('  滚轮后:', JSON.stringify(await st()));
  await p.mouse.move(700, 450);
  await p.mouse.down();
  await p.mouse.move(600, 400, { steps: 5 });
  await p.mouse.up();
  await sleep(400);
  console.log('  拖拽后:', JSON.stringify(await st()));
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
