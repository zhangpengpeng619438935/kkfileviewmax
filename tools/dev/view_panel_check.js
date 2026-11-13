/* 视图面板自检：标准视角、拖拽反向、X 锁轴、重定坐标系。
   用法：node tools/dev/view_panel_check.js <pageUrl> <截图前缀> */
const puppeteer = require('puppeteer-core');
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const url = process.argv[2], pre = process.argv[3] || 'C:/Users/example/AppData/Local/Temp/vp';

const SNAP = `(() => {
  const d = window.__stepDebug();
  const dx = d.cam[0]-d.cluC[0], dy = d.cam[1]-d.cluC[1], dz = d.cam[2]-d.cluC[2];
  const L = Math.hypot(dx,dy,dz) || 1;
  return { dir: [dx/L, dy/L, dz/L].map(v=>+v.toFixed(3)),
           dist: +L.toFixed(1), up: d.up.map(v=>+v.toFixed(3)),
           rot: d.groupRot, cluC: d.cluC.map(v=>+v.toFixed(1)) };
})()`;

(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1200, height: 800 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 40000 });
  await new Promise(r => setTimeout(r, 1500));
  const snap = () => p.evaluate(SNAP);
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  console.log('初始      ', JSON.stringify(await snap()));

  await p.click('#viewBtn'); await sleep(300);
  await p.screenshot({ path: pre + '_panel.png' });
  for (const v of ['front', 'right', 'top', 'iso']) {
    await p.click(`#viewPanel button[data-v="${v}"]`); await sleep(500);
    const s = await snap();
    console.log(('视角 ' + v).padEnd(10), 'dir=' + JSON.stringify(s.dir), 'up=' + JSON.stringify(s.up));
  }
  // 俯视时再点一次确认 up 不会被 Z 带坏
  await p.click('#viewPanel button[data-v="top"]'); await sleep(400);
  await p.click(`#viewBtn`); await sleep(200);   // 关面板，免得挡画布

  // ---- 拖拽方向：先正向，再勾选反向 ----
  const drag = async () => {
    await p.mouse.move(600, 420);
    await p.mouse.down();
    await p.mouse.move(720, 420, { steps: 10 });
    await p.mouse.up();
    await sleep(350);
  };
  await p.keyboard.press('0'); await sleep(700);
  const a0 = await snap(); await drag(); const a1 = await snap();
  console.log('正向拖拽  dir', JSON.stringify(a0.dir), '→', JSON.stringify(a1.dir));

  await p.click('#viewBtn'); await sleep(250);
  await p.click('#invDrag'); await sleep(250);
  await p.click('#viewBtn'); await sleep(250);
  await p.keyboard.press('0'); await sleep(700);
  const b0 = await snap(); await drag(); const b1 = await snap();
  console.log('反向拖拽  dir', JSON.stringify(b0.dir), '→', JSON.stringify(b1.dir));

  // ---- 按住 X 拖：绕世界 X 轴转，相机的 x 坐标应不变 ----
  await p.keyboard.press('0'); await sleep(700);
  const c0 = await snap();
  await p.keyboard.down('x');
  await p.mouse.move(600, 420); await p.mouse.down();
  await p.mouse.move(700, 460, { steps: 10 }); await p.mouse.up();
  await p.keyboard.up('x');
  await sleep(400);
  const c1 = await snap();
  console.log('按住 X 拖  cluC', JSON.stringify(c0.cluC), '→', JSON.stringify(c1.cluC),
              ' 相机 x 分量 (相对中心) 应基本不变:', c0.dir[0], '→', c1.dir[0]);

  // ---- 重定坐标系 ----
  await p.keyboard.press('0'); await sleep(700);
  const d0 = await snap();
  await p.click('#viewBtn'); await sleep(250);
  await p.click('#rebaseBtn'); await sleep(900);
  const d1 = await snap();
  console.log('重定基准  rot', JSON.stringify(d0.rot), '→', JSON.stringify(d1.rot),
              '  dir', JSON.stringify(d1.dir), 'up', JSON.stringify(d1.up));
  if (pre) await p.screenshot({ path: pre + '_rebase.png' });
  await b.close();
})();
