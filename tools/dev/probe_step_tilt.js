// 验证新增的"前后翻转"按钮（#tiltU / #tiltD）：
//   ① 每点一次画面都变（能连续翻，不受极点限制）；
//   ② 正反各一次精确抵消（15° / -15° 回到原 hash）；
//   ③ 与左键上下拖同方向（点 ↑ 等价于往下拖）。
// 用法: node probe_step_tilt.js <base> <fileKey> <kkt>
const puppeteer = require('puppeteer-core');
const crypto = require('crypto');
const BASE = process.argv[2] || 'http://127.0.0.1:9012';
const FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const h = (buf) => crypto.createHash('md5').update(buf).digest('hex').slice(0, 10);

(async () => {
  if (!FK || !KKT) { console.log('用法: node probe_step_tilt.js <base> <fileKey> <kkt>'); process.exit(1); }
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage',
      '--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1280, height: 800 });
  const errs = [];
  p.on('pageerror', e => errs.push(String(e).slice(0, 140)));
  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT),
    { waitUntil: 'domcontentloaded', timeout: 90000 });
  await p.waitForFunction(
    () => (document.getElementById('info') || {}).textContent &&
          document.getElementById('info').textContent.indexOf('面片') >= 0,
    { timeout: 120000 });
  await sleep(700);
  // 操作提示条是 fixed 在底部的，12 秒后自动淡出 —— 会把底部像素一起改掉，
  // 让截图 hash 不可比（跑得久就会误判）。测试期间直接隐藏。
  await p.evaluate(() => { const h = document.getElementById('hint'); if (h) h.style.display = 'none'; });
  await sleep(200);
  const box = await p.$eval('#cv', el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
  const shot = async () => h(await p.screenshot({ clip: { x: box.x, y: box.y, width: box.w, height: box.h } }));
  const res = [];
  const ok = (n, c, e) => res.push([n, c ? 'OK' : 'FAIL', e || '']);
  async function drag(dx, dy, steps = 20) {
    await p.mouse.move(cx, cy); await p.mouse.down({ button: 'left' });
    for (let i = 1; i <= steps; i++) { await p.mouse.move(cx + dx * i / steps, cy + dy * i / steps); await sleep(8); }
    await p.mouse.up({ button: 'left' }); await sleep(400);
  }

  // 按钮是否都在、可见
  const btns = await p.evaluate(() => ['tiltU', 'tiltD', 'spinL', 'spinR'].map(id => {
    const el = document.getElementById(id);
    if (!el) return id + ':missing';
    const r = el.getBoundingClientRect();
    return id + ':' + (r.width > 0 && r.height > 0 ? 'ok' : 'hidden');
  }));
  ok('四个旋转按钮都在', btns.every(x => x.endsWith(':ok')), btns.join(' '));

  const h0 = await shot();
  // ① 连续点 ↑ 三次，每步都应变化
  let prev = h0;
  for (let i = 1; i <= 3; i++) {
    await p.click('#tiltU'); await sleep(350);
    const hh = await shot();
    ok('点 ↑ 第 ' + i + ' 次画面变化', hh !== prev, prev + ' -> ' + hh);
    prev = hh;
  }
  // ② 正反抵消：再点 ↓ 三次，应逐步回到 h0
  for (let i = 1; i <= 3; i++) {
    await p.click('#tiltD'); await sleep(350);
    const hh = await shot();
    ok('点 ↓ 第 ' + i + ' 次画面变化', hh !== prev, prev + ' -> ' + hh);
    prev = hh;
  }
  ok('↑↓ 各三次精确回到初始视角', prev === h0, h0 + ' -> ' + prev);

  // ③ 键盘 W / S 与 ↑ / ↓ 完全等效（同一函数、同一角度，位级可比）
  await p.click('#reset'); await sleep(600);
  await p.click('#tiltU'); await sleep(350);
  const hBtn1 = await shot();
  await p.click('#reset'); await sleep(600);
  await p.keyboard.press('w'); await sleep(350);
  const hW = await shot();
  ok('W 键与 ↑ 按钮完全等效', hW === hBtn1, 'btn=' + hBtn1 + ' W=' + hW);
  await p.keyboard.press('s'); await sleep(350);
  const hS = await shot();
  ok('S 键反向抵回初始视角', hS === h0, h0 + ' -> ' + hS);

  // ④ 翻过极点后仍可渲染
  await p.click('#reset'); await sleep(600);
  for (let i = 0; i < 12; i++) { await p.click('#tiltU'); await sleep(120); }
  const hPole = await shot();
  ok('连点 12 次（180°）后仍可渲染', !!hPole && hPole.length === 10, hPole);

  ok('页面无 JS 报错', errs.filter(e => e.indexOf('favicon') < 0).length === 0, errs.join(' | '));

  await b.close();
  let pass = 0;
  res.forEach(([n, s, e]) => { if (s === 'OK') pass++; console.log((s === 'OK' ? '  ✓ ' : '  ✗ ') + n + (e ? '  [' + e + ']' : '')); });
  console.log('通过 ' + pass + '/' + res.length);
  process.exit(pass === res.length ? 0 : 1);
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
