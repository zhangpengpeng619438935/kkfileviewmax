// 采样加载层文字，确认下载过程中能看到实时速度
const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox','--use-gl=swiftshader','--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1366, height: 768 });
  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded', timeout:90000 });
  const seen = [];
  for (let i = 0; i < 40; i++) {
    const t = await p.evaluate(() => {
      const el = document.getElementById('ldt');
      const ld = document.getElementById('ld');
      return (el ? el.textContent : '') + (ld && ld.className.indexOf('hide') >= 0 ? ' [已隐藏]' : '');
    });
    if (!seen.length || seen[seen.length-1] !== t) seen.push(t);
    if (/面片|线框/.test(await p.evaluate(() => (document.getElementById('info')||{}).textContent || ''))) break;
    await sleep(400);
  }
  console.log('加载层文字变化：');
  seen.slice(0, 12).forEach(x => console.log('  ' + x));
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
