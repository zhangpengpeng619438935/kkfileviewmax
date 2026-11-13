/* 面距的"平行面"分支：点背板正面 → 转 180° → 点背面，应只报距离（板厚），无夹角。 */
const puppeteer = require('puppeteer-core');
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const url = process.argv[2], pre = process.argv[3];
(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1000, height: 760 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 40000 });
  await new Promise(r => setTimeout(r, 1500));
  const txt = () => p.evaluate(() => document.getElementById('mtip').textContent
    + ' || ' + (document.getElementById('dlTip').classList.contains('on')
                ? document.getElementById('dlTip').textContent : ''));

  await p.click('#planeBtn');
  await new Promise(r => setTimeout(r, 250));
  await p.mouse.click(400, 500);                       // 正面
  await new Promise(r => setTimeout(r, 400));
  console.log('选正面：', (await txt()).slice(0, 40));

  for (let i = 0; i < 12; i++) { await p.click('#spinL'); await new Promise(r => setTimeout(r, 110)); }
  await new Promise(r => setTimeout(r, 600));
  await p.mouse.click(600, 430);                       // 转到背面后点中心
  await new Promise(r => setTimeout(r, 500));
  console.log('选背面：', await txt());
  if (pre) await p.screenshot({ path: pre });
  await b.close();
})();
