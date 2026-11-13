/* 生成"点 A 前后"的对比截图，用于观察不同视角下同一操作的实际效果。
   用法：node tools/dev/roll_shots.js <pageUrl> <输出前缀> */
const puppeteer = require('puppeteer-core');
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const url = process.argv[2], pre = process.argv[3] || 'C:/Users/example/AppData/Local/Temp/rd';

(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1000, height: 700 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 40000 });
  await new Promise(r => setTimeout(r, 1200));

  const click = async (id, n = 1) => {
    for (let i = 0; i < n; i++) { await p.click('#' + id); await new Promise(r => setTimeout(r, 90)); }
    await new Promise(r => setTimeout(r, 350));
  };

  await p.keyboard.press('0'); await new Promise(r => setTimeout(r, 800));
  await p.screenshot({ path: pre + '_normal_0.png' });
  await click('rollL');
  await p.screenshot({ path: pre + '_normal_1.png' });

  await p.keyboard.press('0'); await new Promise(r => setTimeout(r, 800));
  await click('tiltU', 12);
  await p.screenshot({ path: pre + '_flip_0.png' });
  await click('rollL');
  await p.screenshot({ path: pre + '_flip_1.png' });

  console.log('已生成四张：正常视角 前/后、倒置视角 前/后');
  await b.close();
})();
