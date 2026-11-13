const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox','--use-gl=swiftshader','--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1366, height: 768 });
  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded', timeout:90000 });
  await p.waitForFunction(() => ((document.getElementById('info')||{}).textContent||'').indexOf('面片') >= 0 ||
                                  ((document.getElementById('info')||{}).textContent||'').indexOf('线框') >= 0,
                          { timeout: 180000 });
  await sleep(1500);
  const info = await p.$eval('#info', el => el.textContent);
  console.log('  信息栏:', info.slice(0, 120));
  const hasBtn = await p.$('#netBtn') ? true : false;
  console.log('  测速按钮:', hasBtn);
  const t0 = Date.now();
  await p.click('#netBtn');
  await p.waitForFunction(() => ((document.getElementById('mtip')||{}).textContent||'').indexOf('MB/s') >= 0,
                          { timeout: 120000 });
  const tip = await p.$eval('#mtip', el => el.textContent);
  console.log('  测速结果:', tip, '（耗时 ' + ((Date.now()-t0)/1000).toFixed(1) + 's）');
  await p.screenshot({ path: 'net_probe.png' });
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
