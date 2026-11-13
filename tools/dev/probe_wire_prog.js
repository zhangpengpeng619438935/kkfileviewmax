const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox','--use-gl=swiftshader','--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width:1300, height:820 });
  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded', timeout:90000 });
  await p.waitForFunction(() => ((document.getElementById('info')||{}).textContent||'').indexOf('线框') >= 0 ||
                                  ((document.getElementById('info')||{}).textContent||'').indexOf('面片') >= 0,
                          { timeout: 120000 });
  const seen = [];
  for (let i=0;i<40;i++){
    const s = await p.evaluate(() => {
      const pr = document.getElementById('prog'), tx = document.getElementById('progTxt');
      const on = pr && pr.classList.contains('on');
      return on ? (tx ? tx.textContent : '') : '(未显示)';
    });
    if (seen[seen.length-1] !== s) seen.push(s);
    const info = await p.evaluate(() => (document.getElementById('info')||{}).textContent || '');
    if (info.indexOf('面片') >= 0) { seen.push('→ 已切换为实体'); break; }
    await sleep(1500);
  }
  console.log('  进度条状态变化：');
  seen.slice(0,14).forEach(x => console.log('    ' + x));
  await p.screenshot({ path:'wire_prog.png' });
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
