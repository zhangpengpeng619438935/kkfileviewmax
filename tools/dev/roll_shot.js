/* 截图：点两次 ↺（A/D 组，现为绕竖直轴水平旋转）前后的画面，用于确认效果。
   用法：node tools/dev/roll_shot.js <pageUrl> <输出前缀> */
const puppeteer = require('puppeteer-core');
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const url = process.argv[2], pre = process.argv[3] || 'C:/Users/example/AppData/Local/Temp/ads';
(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1000, height: 700 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 40000 });
  await new Promise(r => setTimeout(r, 1200));
  await p.screenshot({ path: pre + '_0.png' });
  for (let i = 0; i < 4; i++) { await p.click('#rollL'); await new Promise(r => setTimeout(r, 150)); }
  await new Promise(r => setTimeout(r, 400));
  await p.screenshot({ path: pre + '_1.png' });
  console.log('done');
  await b.close();
})();
