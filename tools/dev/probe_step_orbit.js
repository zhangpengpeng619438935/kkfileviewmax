// 验证 STEP 页面"上下无限制旋转"：放开俯仰后，左键往下能一路翻到底面，不会卡在水平线。
// 判据同 probe_step_zoom：相机固定时渲染确定，直接比截图 md5。
// 用法: node probe_step_orbit.js <base> <fileKey> <kkt>
const puppeteer = require('puppeteer-core');
const crypto = require('crypto');
const BASE = process.argv[2] || 'http://127.0.0.1:9012';
const FK = process.argv[3];
const KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const h = (buf) => crypto.createHash('md5').update(buf).digest('hex').slice(0, 10);

(async () => {
  if (!FK || !KKT) { console.log('用法: node probe_step_orbit.js <base> <fileKey> <kkt>'); process.exit(1); }
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage',
      '--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1280, height: 800 });
  const errs = [];
  p.on('pageerror', e => errs.push(String(e).slice(0, 140)));
  p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 120)); });

  const url = BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT);
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await p.waitForFunction(
    () => (document.getElementById('info') || {}).textContent &&
          document.getElementById('info').textContent.indexOf('面片') >= 0,
    { timeout: 120000 });
  await sleep(600);
  const box = await p.$eval('#cv', el => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  });
  const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
  const shot = async () => h(await p.screenshot({ clip: { x: box.x, y: box.y, width: box.w, height: box.h } }));

  // 左键拖拽：分步移动，OrbitControls 按 pointermove 增量算角度
  async function drag(dx, dy, steps = 24) {
    await p.mouse.move(cx, cy);
    await p.mouse.down({ button: 'left' });
    for (let i = 1; i <= steps; i++) {
      await p.mouse.move(cx + dx * i / steps, cy + dy * i / steps);
      await sleep(8);
    }
    await p.mouse.up({ button: 'left' });
    await sleep(400);
  }

  const res = [];
  const ok = (name, cond, extra) => { res.push([name, cond ? 'OK' : 'FAIL', extra || '']); };

  const h0 = await shot();
  // 往下拖 = 相机往上升 = 看到顶面；往上拖 = 相机下沉 = 看到底面。
  // 两种方向各拖 3 次，每次 320px，看每次画面是否都变（没被 maxPolarAngle 卡住）。
  let prev = h0;
  const seq = [];
  for (let i = 1; i <= 3; i++) {
    await drag(0, 320);
    const hh = await shot();
    seq.push(hh);
    ok('上翻第 ' + i + ' 步画面变化', hh !== prev, prev + ' -> ' + hh);
    prev = hh;
  }
  // 反向：一路往下翻到另一极
  for (let i = 1; i <= 5; i++) {
    await drag(0, -320);
    const hh = await shot();
    seq.push(hh);
    ok('下翻第 ' + i + ' 步画面变化', hh !== prev, prev + ' -> ' + hh);
    prev = hh;
  }
  // 水平方向同样不应受限
  let pv = prev;
  for (let i = 1; i <= 2; i++) {
    await drag(360, 0);
    const hh = await shot();
    ok('水平旋转第 ' + i + ' 步画面变化', hh !== pv, pv + ' -> ' + hh);
    pv = hh;
  }

  // 复位：点"复位视角"按钮（比双击稳），应回到初始 hash
  await p.click('#reset');
  await sleep(800);
  const hReset = await shot();
  ok('复位回到初始视角', hReset === h0, h0 + ' -> ' + hReset);

  // 顶点附近不应崩：连续上翻 6 次后仍能渲染
  await drag(0, 320); await drag(0, 320); await drag(0, 320);
  const hPole = await shot();
  ok('翻过极点后仍可渲染', !!hPole && hPole.length === 10, hPole);

  const bad = errs.filter(e => e.indexOf('favicon') < 0);
  ok('页面无 JS 报错', bad.length === 0, bad.join(' | '));

  await b.close();
  let pass = 0;
  res.forEach(([n, s, e]) => { if (s === 'OK') pass++; console.log((s === 'OK' ? '  ✓ ' : '  ✗ ') + n + (e ? '  [' + e + ']' : '')); });
  console.log('通过 ' + pass + '/' + res.length);
  process.exit(pass === res.length ? 0 : 1);
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
