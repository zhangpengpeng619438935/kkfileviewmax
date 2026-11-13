/* 爆炸 + 复位视角自检：满档爆炸后按 0，取景应把散开的零件一起框进来（cluR 变大）。
   用法：node tools/dev/explode_fit_check.js <pageUrl> <截图前缀> */
const puppeteer = require('puppeteer-core');
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const url = process.argv[2], pre = process.argv[3] || '';

(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1400, height: 800 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 40000 });
  await new Promise(r => setTimeout(r, 1200));

  const read = () => p.evaluate(() => {
    const d = window.__stepDebug();
    const dist = Math.hypot(d.cam[0]-d.cluC[0], d.cam[1]-d.cluC[1], d.cam[2]-d.cluC[2]);
    return { cluR: +d.cluR.toFixed(1), dCam: +dist.toFixed(1),
             maxOff: +window.__stepMaxOffset().toFixed(1),
             camDistRatio: +(dist / d.cluR).toFixed(2) };
  });

  console.log('初始        ', JSON.stringify(await read()));
  await p.click('#expBtn');
  await p.evaluate(() => {
    const r = document.getElementById('expRange');
    r.value = '100'; r.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await new Promise(r => setTimeout(r, 500));
  console.log('爆炸满档    ', JSON.stringify(await read()));
  if (pre) await p.screenshot({ path: pre + '_exp.png' });

  await p.keyboard.press('0');            // 复位视角：应重新框住散开的零件
  await new Promise(r => setTimeout(r, 700));
  const s = await read();
  console.log('爆炸后按 0  ', JSON.stringify(s), '（cluR 应变大、dCam 应≈cluR*2.2）');
  if (pre) await p.screenshot({ path: pre + '_expfit.png' });

  await p.click('#expOff');
  await new Promise(r => setTimeout(r, 300));
  await p.keyboard.press('0');
  await new Promise(r => setTimeout(r, 700));
  console.log('复位爆炸+0  ', JSON.stringify(await read()), '（cluR 应回到初始值）');
  if (pre) await p.screenshot({ path: pre + '_back.png' });
  await b.close();
})();
