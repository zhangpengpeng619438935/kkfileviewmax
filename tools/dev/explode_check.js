/* 爆炸视图位移量自检：把滑块拉满，读最大部件位移与模型半径的比值。
   用法：node tools/dev/explode_check.js <pageUrl> [截图路径] */
const puppeteer = require('puppeteer-core');
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const url = process.argv[2], out = process.argv[3];

(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1400, height: 800 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 40000 });
  await new Promise(r => setTimeout(r, 1200));

  const read = () => p.evaluate(() => ({
    max: +window.__stepMaxOffset().toFixed(1),
    cluR: +window.__stepDebug().cluR.toFixed(1),
    val: document.getElementById('expVal').textContent,
  }));

  console.log('爆炸前  ', JSON.stringify(await read()));

  await p.click('#expBtn');
  await p.evaluate(() => {
    const r = document.getElementById('expRange');
    r.value = '100';
    r.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await new Promise(r => setTimeout(r, 500));
  const s = await read();
  console.log('满档    ', JSON.stringify(s),
    '位移/半径 =', (s.max / s.cluR).toFixed(2));
  if (out) await p.screenshot({ path: out });

  // 半档应为满档一半（线性）
  await p.evaluate(() => {
    const r = document.getElementById('expRange');
    r.value = '50';
    r.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await new Promise(r => setTimeout(r, 400));
  console.log('50%%    ', JSON.stringify(await read()));

  await p.click('#expOff');
  await new Promise(r => setTimeout(r, 400));
  console.log('复位后  ', JSON.stringify(await read()), '（max 应为 0）');
  await b.close();
})();
