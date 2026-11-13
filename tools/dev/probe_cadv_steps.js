// 采样矢量页加载层的阶段列表
const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox'] });
  const p = await b.newPage();
  await p.setViewport({ width:1300, height:820 });
  p.on('pageerror', e => console.log('  PAGEERR', String(e).slice(0,140)));
  await p.goto(BASE + '/cadv/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded', timeout:90000 });
  const seen = [];
  for (let i = 0; i < 40; i++) {
    const t = await p.evaluate(() => {
      const box = document.getElementById('ldSteps');
      const ld = document.getElementById('ld');
      const hidden = ld && ld.className.indexOf('hide') >= 0;
      return (hidden ? '[已隐藏] ' : '') + (box ? box.innerText.replace(/\n+/g, ' | ') : '');
    });
    if (seen[seen.length-1] !== t) seen.push(t);
    if (t.indexOf('[已隐藏]') === 0) break;
    await sleep(700);
  }
  console.log('  阶段变化：');
  seen.slice(0, 8).forEach(x => console.log('    ' + x));
  await p.screenshot({ path: 'cadv_steps.png' });
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
