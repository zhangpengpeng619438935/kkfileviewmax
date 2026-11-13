/* A/D 是否改为「绕自身竖直轴水平旋转」的自检。
   判据：点 ↺ 后，相机绕 cluC 的方位角变化约 +15°、到中心距离不变、相机高度不变；
   并与 ⟲（左右旋转）比较方向是否一致。
   用法：node tools/dev/roll_axis_check.js <pageUrl> */
const puppeteer = require('puppeteer-core');
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const url = process.argv[2];

const SNAP = `(() => {
  const d = window.__stepDebug();
  const dx = d.cam[0]-d.cluC[0], dy = d.cam[1]-d.cluC[1], dz = d.cam[2]-d.cluC[2];
  return {
    az: Math.atan2(dy, dx) * 180/Math.PI,   // 绕竖直轴的方位角
    dist: Math.hypot(dx, dy, dz),
    height: dz,                              // 相机相对中心的竖直高度
    up: d.up.map(v => +v.toFixed(3)),
  };
})()`;

const dAz = (a, b) => { let x = b - a; while (x > 180) x -= 360; while (x < -180) x += 360; return +x.toFixed(2); };

(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1200, height: 800 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 40000 });
  await new Promise(r => setTimeout(r, 1200));
  const snap = () => p.evaluate(SNAP);
  const click = async (id, n = 1) => {
    for (let i = 0; i < n; i++) { await p.click('#' + id); await new Promise(r => setTimeout(r, 120)); }
    await new Promise(r => setTimeout(r, 300));
  };

  const a0 = await snap();
  await click('rollL');
  const a1 = await snap();
  console.log('点 ↺ 一次：方位角 %s°，距离 %s→%s，高度 %s→%s',
    dAz(a0.az, a1.az), a0.dist.toFixed(1), a1.dist.toFixed(1),
    a0.height.toFixed(1), a1.height.toFixed(1));

  await click('spinL');
  const a2 = await snap();
  console.log('再点 ⟲：  方位角 %s°（与 ↺ 同向则为同号）', dAz(a1.az, a2.az));

  await click('rollR');
  const a3 = await snap();
  console.log('点 ↻ 一次：方位角 %s°（应为负）', dAz(a2.az, a3.az));

  // 多视角下重复：先翻到侧面/背面，再点 ↺，方位角变化应仍为同一方向
  console.log('--- 换视角后 ---');
  await p.keyboard.press('0'); await new Promise(r => setTimeout(r, 700));
  await click('tiltU', 6);                       // 抬到俯视 90°
  const c0 = await snap();
  await click('rollL');
  const c1 = await snap();
  console.log('俯视视角点 ↺：方位角 %s°，高度 %s→%s（绕竖直轴应不变）',
    dAz(c0.az, c1.az), c0.height.toFixed(1), c1.height.toFixed(1));

  await p.keyboard.press('0'); await new Promise(r => setTimeout(r, 700));
  await click('tiltU', 12);                      // 翻过头 180°
  const e0 = await snap();
  await click('rollL');
  const e1 = await snap();
  console.log('倒置视角点 ↺：方位角 %s°，高度 %s→%s',
    dAz(e0.az, e1.az), e0.height.toFixed(1), e1.height.toFixed(1));
  await b.close();
})();
