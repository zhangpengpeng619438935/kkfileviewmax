const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox'] });
  const p = await b.newPage();
  await p.setViewport({ width:1400, height:900 });
  p.on('pageerror', e => console.log('  PAGEERR', String(e).slice(0,140)));
  await p.goto(BASE + '/cadv/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded', timeout:90000 });
  await p.waitForFunction(() => {
    const ld = document.getElementById('ld');
    return ld && ld.className.indexOf('hide') >= 0;
  }, { timeout: 120000 });
  await sleep(1200);
  const info = await p.$eval('#info', el => el.textContent);
  console.log('  信息栏:', info);
  await p.screenshot({ path:'cadv_1.png' });
  // 量尺寸
  await p.click('#meas'); await sleep(300);
  const box = await p.$eval('#stage', el => { const r=el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; });
  await p.mouse.click(box.x + box.w*0.55, box.y + box.h*0.45); await sleep(400);
  await p.mouse.click(box.x + box.w*0.55, box.y + box.h*0.62); await sleep(600);
  const tip = await p.$eval('#tip', el => el.textContent);
  console.log('  量尺寸:', tip);
  await p.screenshot({ path:'cadv_2.png' });
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
