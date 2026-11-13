/* 横向旋转（roll）自检：点 A / D 键与 ↺ ↻ 按钮，看 camera.up 是否绕视线转、画面是否跟着转。
   用法：node tools/dev/roll_check.js <pageUrl> */
const puppeteer = require('puppeteer-core');

const url = process.argv[2];
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';

(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1400, height: 760 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 30000 });
  await new Promise(r => setTimeout(r, 1500));

  const snap = () => p.evaluate(() => {
    const d = window.__stepDebug();
    return { up: d.up.map(v => +v.toFixed(4)), cam: d.cam.map(v => +v.toFixed(2)),
             zoom: document.getElementById('zoom').textContent };
  });
  // 画面指纹：截取画布数据，判断"画面真的转了"而不是只改了变量
  const fingerprint = async () => {
    const buf = await p.screenshot({ clip: { x: 300, y: 300, width: 200, height: 200 } });
    return require('crypto').createHash('md5').update(buf).digest('hex').slice(0, 10);
  };

  console.log('初始   ', JSON.stringify(await snap()));
  console.log('指纹   ', await fingerprint());

  // 键盘 A：画面左倾 15°
  await p.keyboard.press('a');
  await new Promise(r => setTimeout(r, 350));
  console.log('按 A 后', JSON.stringify(await snap()));
  console.log('指纹   ', await fingerprint());

  // 键盘 D 两次：回到 -15°
  await p.keyboard.press('d');
  await p.keyboard.press('d');
  await new Promise(r => setTimeout(r, 350));
  console.log('按 D×2 ', JSON.stringify(await snap()));

  // 按钮 ↺ ×4 = 60°，再连续点到 90° 以上，确认不崩、up 始终与视线垂直
  for (let i = 0; i < 4; i++) {
    await p.click('#rollL');
    await new Promise(r => setTimeout(r, 120));
  }
  const s = await p.evaluate(() => {
    const d = window.__stepDebug();
    const v = [0, 1, 2].map(i => d.cam[i] - d.target[i]);
    const dot = v[0] * d.up[0] + v[1] * d.up[1] + v[2] * d.up[2];
    const len = Math.hypot(...v), ul = Math.hypot(...d.up);
    return { up: d.up.map(x => +x.toFixed(3)), dotRel: +(dot / (len * ul)).toFixed(4),
             upLen: +ul.toFixed(4) };
  });
  console.log('↺×4 后', JSON.stringify(s), '（dotRel 应≈0：up 与视线垂直；upLen 应≈1）');

  // 复位视角应把 up 拉回世界 Z
  await p.keyboard.press('0');
  await new Promise(r => setTimeout(r, 500));
  console.log('按 0 复位', JSON.stringify(await snap()));

  if (process.env.SHOT) {
    await p.click('#rollL'); await p.click('#rollL');
    await new Promise(r => setTimeout(r, 400));
    await p.screenshot({ path: process.env.SHOT });
  }
  await b.close();
})();
