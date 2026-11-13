// 加载过程中截一张图，看阶段列表的实际观感
const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4], WAIT = +(process.argv[5]||1800);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox'] });
  const p = await b.newPage();
  await p.setViewport({ width:1300, height:820 });
  await p.goto(BASE + '/cadv/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded', timeout:90000 });
  await sleep(WAIT);
  await p.screenshot({ path: 'cadv_loading.png' });
  console.log('saved');
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
