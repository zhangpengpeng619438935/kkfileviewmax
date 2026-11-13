/* 旋转圆心自检：确认 ⟲ ⟳ / 左键拖拽都绕"主体几何中心"cluC 而不是 ctl.target。
   判定标准：旋转前后 相机到 cluC 的距离、注视点到 cluC 的距离 都不变（刚性公转）；
   而 target 已经被右键平移带走时，target 到 cluC 的距离也应保持不变。
   用法：node tools/dev/pivot_check.js <pageUrl> */
const puppeteer = require('puppeteer-core');
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const url = process.argv[2];

(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1400, height: 800 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 30000 });
  await new Promise(r => setTimeout(r, 1500));

  const snap = () => p.evaluate(() => {
    const d = window.__stepDebug();
    const dist = (a, b) => Math.hypot(a[0]-b[0], a[1]-b[1], a[2]-b[2]);
    return {
      cam: d.cam.map(v => +v.toFixed(3)),
      tgt: d.target.map(v => +v.toFixed(3)),
      cluC: d.cluC.map(v => +v.toFixed(3)),
      dCam: +dist(d.cam, d.cluC).toFixed(3),
      dTgt: +dist(d.target, d.cluC).toFixed(3),
    };
  });
  const px = (a, b) => Math.hypot(a[0]-b[0], a[1]-b[1], a[2]-b[2]).toFixed(3);

  const s0 = await snap();
  console.log('初始      dCam=%s dTgt=%s cluC=%j', s0.dCam, s0.dTgt, s0.cluC);

  // —— 场景 1：未平移，直接点 ⟲ ——
  await p.click('#spinL');
  await new Promise(r => setTimeout(r, 350));
  const s1 = await snap();
  console.log('⟲ 后      dCam=%s dTgt=%s  cam位移=%s', s1.dCam, s1.dTgt, px(s0.cam, s1.cam));
  console.log('           dCam 变化 %s / dTgt 变化 %s （都应为 0.000）',
    Math.abs(s1.dCam - s0.dCam).toFixed(3), Math.abs(s1.dTgt - s0.dTgt).toFixed(3));

  // —— 场景 2：右键平移把视点拖开，再点旋转 ——
  await p.mouse.move(700, 430);
  await p.mouse.down({ button: 'right' });
  await p.mouse.move(880, 470, { steps: 12 });
  await p.mouse.up({ button: 'right' });
  await new Promise(r => setTimeout(r, 400));
  const s2 = await snap();
  console.log('平移后    dCam=%s dTgt=%s  target 离中心 %s',
    s2.dCam, s2.dTgt, px(s2.tgt, s2.cluC));

  await p.click('#spinR');
  await p.click('#spinR');
  await new Promise(r => setTimeout(r, 400));
  const s3 = await snap();
  console.log('平移后 ⟳×2 dCam=%s dTgt=%s', s3.dCam, s3.dTgt);
  console.log('           dCam 变化 %s / dTgt 变化 %s （刚性：都应为 0.000；'
    + '若绕 target 转，tgt 会不动而 dTgt 变）',
    Math.abs(s3.dCam - s2.dCam).toFixed(3), Math.abs(s3.dTgt - s2.dTgt).toFixed(3));

  // —— 场景 3：左键拖拽（orbitBy）同样应绕 cluC ——
  await p.mouse.move(700, 430);
  await p.mouse.down({ button: 'left' });
  await p.mouse.move(820, 500, { steps: 12 });
  await p.mouse.up({ button: 'left' });
  await new Promise(r => setTimeout(r, 400));
  const s4 = await snap();
  console.log('左键拖后  dCam=%s dTgt=%s  dCam 变化 %s',
    s4.dCam, s4.dTgt, Math.abs(s4.dCam - s3.dCam).toFixed(3));

  // —— 场景 4：复位应把 target 拉回中心 ——
  await p.keyboard.press('0');
  await new Promise(r => setTimeout(r, 600));
  const s5 = await snap();
  console.log('复位后    dCam=%s dTgt=%s （dTgt 应为 0）', s5.dCam, s5.dTgt);

  if (process.env.SHOT) await p.screenshot({ path: process.env.SHOT });
  await b.close();
})();
