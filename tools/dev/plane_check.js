/* 面距功能自检：进入面距模式 → 点两个面 → 读出计算出的平面距离。
   用法：node tools/dev/plane_check.js <pageUrl> [截图前缀]
   点的坐标按 1000x760 视口标定（模型居中）。 */
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

  const state = () => p.evaluate(() => ({
    btn: document.getElementById('planeBtn').classList.contains('on'),
    mtip: document.getElementById('mtip').textContent,
    mtipOn: document.getElementById('mtip').classList.contains('on'),
    dl: document.getElementById('dlTip').classList.contains('on')
      ? document.getElementById('dlTip').textContent : '',
    kids: window.__stepDebug().children,
  }));

  await p.click('#planeBtn');
  await new Promise(r => setTimeout(r, 300));
  console.log('进入面距模式：', JSON.stringify(await state()));

  const click = async (x, y, tag) => {
    await p.mouse.click(x, y);
    await new Promise(r => setTimeout(r, 450));
    console.log(tag, JSON.stringify(await state()));
  };

  await click(400, 500, '点第一个面（背板）：');
  if (pre) await p.screenshot({ path: pre + '_p1.png' });
  await click(752, 400, '点第二个面（右侧支架）：');
  if (pre) await p.screenshot({ path: pre + '_p2.png' });

  // 换一对：再点一次应重新开始
  await click(600, 470, '第三点（重开，应重新开始）：');

  await p.keyboard.press('Escape');
  await new Promise(r => setTimeout(r, 400));
  console.log('按 Esc 后：', JSON.stringify(await state()));
  await b.close();
})();
