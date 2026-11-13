/* A/D（横向旋转）在不同视角下的方向一致性自检。
   原理：取模型空间里一个固定点，算它在相机屏幕平面内的极角（相对视线中心）。
   点一次 ↺（rollView -15）后，这个极角应变化 +15°（画面逆时针）。
   若各视角下变化量符号/大小一致 → 方向恒定；否则说明存在姿态相关的 bug。
   用法：node tools/dev/roll_dir_check.js <pageUrl> */
const puppeteer = require('puppeteer-core');
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const url = process.argv[2];

const PROBE = `(() => {
  const d = window.__stepDebug();
  const cam = d.cam, up = d.up, tgt = d.target || d.cluC;
  const sub = (a,b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]];
  const dot = (a,b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
  const cross = (a,b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
  const norm = a => { const l = Math.hypot(a[0],a[1],a[2]) || 1; return [a[0]/l,a[1]/l,a[2]/l]; };
  const f = norm(sub(tgt, cam));      // 视线方向
  const r = norm(cross(f, up));       // 屏幕右
  const u = cross(r, f);              // 屏幕上
  const p = [d.cluC[0] + d.cluR*0.8, d.cluC[1], d.cluC[2]];  // 世界坐标里固定的点
  const dd = sub(p, cam);
  return { ang: Math.atan2(dot(dd,u), dot(dd,r)) * 180/Math.PI,
           up: d.up.map(v => +v.toFixed(3)) };
})()`;

const dAng = (a, b) => { let x = b - a; while (x > 180) x -= 360; while (x < -180) x += 360; return +x.toFixed(2); };

(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1400, height: 800 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 40000 });
  await new Promise(r => setTimeout(r, 1200));

  const click = async (id, n = 1) => {
    for (let i = 0; i < n; i++) { await p.click('#' + id); await new Promise(r => setTimeout(r, 90)); }
    await new Promise(r => setTimeout(r, 250));
  };
  const probe = () => p.evaluate(PROBE);

  const cases = [
    ['初始视角', async () => { await p.keyboard.press('0'); await new Promise(r => setTimeout(r, 700)); }],
    ['左右转 90°', async () => { await click('spinL', 6); }],
    ['前后翻 45°', async () => { await p.keyboard.press('0'); await new Promise(r => setTimeout(r, 700)); await click('tiltU', 3); }],
    ['翻过头 180°', async () => { await p.keyboard.press('0'); await new Promise(r => setTimeout(r, 700)); await click('tiltU', 12); }],
    ['转到背面 180°', async () => { await p.keyboard.press('0'); await new Promise(r => setTimeout(r, 700)); await click('spinL', 12); }],
    ['俯视（翻 80°）', async () => { await p.keyboard.press('0'); await new Promise(r => setTimeout(r, 700)); await click('tiltU', 5); }],
  ];

  console.log('视角            点↺后画面角度变化    up 向量');
  for (const [name, setup] of cases) {
    await setup();
    const a0 = await probe();
    await click('rollL');            // rollView(-15)：画面应逆时针 +15°
    const a1 = await probe();
    await click('rollR');            // 复原，避免影响下一个姿态
    console.log('%s  %s°   up=[%s]',
      name.padEnd(14, ' '), String(dAng(a0.ang, a1.ang).toFixed(2)).padStart(7),
      a1.up.join(', '));
  }

  // 连点 ↺ 一圈，看是否有跳变（每步应约 +15°）
  await p.keyboard.press('0'); await new Promise(r => setTimeout(r, 700));
  const seq = [];
  for (let i = 0; i < 8; i++) {
    const s0 = await probe();
    await click('rollL');
    const s1 = await probe();
    seq.push(dAng(s0.ang, s1.ang));
  }
  console.log('连点 ↺ 八次每步角度变化：', JSON.stringify(seq));
  await b.close();
})();
