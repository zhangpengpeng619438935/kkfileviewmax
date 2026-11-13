// 验证 STEP 页面的缩放按钮与"复位视角"是否真的重绘了画面。
// 判定方式：同一相机位置渲染是确定的，截图字节应当完全一致，所以直接比 hash。
const puppeteer = require('puppeteer-core');
const crypto = require('crypto');
const BASE = process.argv[2] || 'http://127.0.0.1:9012';
const KW = process.argv[3] || 'X6M 兼容L460标定图';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const h = (buf) => crypto.createHash('md5').update(buf).digest('hex').slice(0, 10);

(async () => {
  // 第三个参数可以直接给文件地址（热点列表不一定包含它）
  let hit;
  if (/^https?:/.test(KW)) {
    hit = { file_url: KW, file_name: KW.split('/').pop().slice(0, 40) };
  } else {
    const q = await fetch(BASE + '/api/v1/stats/files?top=500').then(r => r.json());
    hit = (q.data || []).find(f => (f.file_name || '').indexOf(KW) >= 0);
  }
  if (!hit) { console.log('找不到文件: ' + KW); process.exit(1); }
  const api = async (path, body, token) => (await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-App-Id': 'kk-demo',
      ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    body: JSON.stringify(body) })).json();
  const tk = (await api('/api/v1/auth/token',
    { appId: 'kk-demo', appSecret: 'your-app-secret' })).data.accessToken;
  const d = (await api('/api/v1/preview/url',
    { fileUrl: hit.file_url, empNo: 'SYS_PREWARM' }, tk)).data;

  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage',
      '--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1280, height: 800 });
  const errs = [];
  p.on('pageerror', e => errs.push(String(e).slice(0, 140)));
  p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 120)); });

  await p.goto(d.previewUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await sleep(7000);
  const ui = await p.evaluate(() => ({
    path: location.pathname,
    info: ((document.getElementById('info') || {}).textContent || '').slice(0, 70),
    hasZin: !!document.getElementById('zin'),
    hasZout: !!document.getElementById('zout'),
    modesHidden: (function () {
      const m = document.getElementById('modes');
      return !m || getComputedStyle(m).display === 'none';
    })(),
    ld: ((document.getElementById('ldt') || {}).textContent || '').slice(0, 40),
  }));
  console.log('落点: ' + ui.path);
  console.log('信息栏: ' + ui.info);
  console.log('缩放按钮: ' + (ui.hasZin && ui.hasZout ? '有' : '没有')
    + '  实体/线框切换: ' + (ui.modesHidden ? '已隐藏（线框无意义）' : '显示'));
  console.log('加载层: ' + ui.ld);

  const shot = async (name) => {
    const buf = await p.screenshot({ clip: { x: 0, y: 36, width: 1280, height: 700 } });
    return { name, hash: h(buf) };
  };

  const A = await shot('A 初始');
  for (let i = 0; i < 3; i++) await p.click('#zout');
  await sleep(400);
  const B = await shot('B 缩小3次');
  for (let i = 0; i < 3; i++) await p.click('#zin');
  await sleep(400);
  const C = await shot('C 放大3次');
  await p.click('#reset');
  await sleep(600);
  const D = await shot('D 复位');

  // 拖拽转一下，再复位
  await p.mouse.move(640, 450);
  await p.mouse.down();
  await p.mouse.move(880, 300, { steps: 12 });
  await p.mouse.up();
  await sleep(700);
  const E = await shot('E 拖拽后');
  await p.click('#reset');
  await sleep(700);
  const F = await shot('F 再复位');

  // 滚轮缩放 + 键盘 0 复位（用户最常用的两种操作）
  await p.mouse.move(640, 450);
  for (let i = 0; i < 4; i++) { await p.mouse.wheel({ deltaY: -120 }); await sleep(180); }
  await sleep(400);
  const G = await shot('G 滚轮放大');
  await p.keyboard.press('0');
  await sleep(600);
  const H = await shot('H 按 0 复位');

  // 水平旋转按钮（⟲ ⟳）与 Q/E 快捷键
  await p.click('#spinR'); await sleep(500);
  const I = await shot('I 向右转15°');
  await p.keyboard.press('q'); await sleep(500);
  const J = await shot('J 按 Q 左转15°');

  const log = (a, b2, expectSame, label) => {
    const same = a.hash === b2.hash;
    const ok = expectSame === same;
    console.log((ok ? '通过  ' : '失败  ') + label + '  ' + a.name + '=' + a.hash
      + ' ' + b2.name + '=' + b2.hash + (same ? '（相同）' : '（不同）'));
  };
  log(A, B, false, '缩小按钮改变了画面');
  log(B, C, false, '放大按钮改变了画面');
  log(A, D, true, '复位回到初始视角');
  log(A, E, false, '拖拽旋转改变了画面');
  log(A, F, true, '拖拽后复位回到初始视角');
  log(A, G, false, '滚轮缩放改变了画面');
  log(A, H, true, '按 0 键复位回到初始视角');
  log(A, I, false, '⟳ 水平旋转改变了画面');
  log(I, J, false, 'Q 键反向水平旋转改变了画面');
  console.log(errs.length ? '页面错误: ' + errs.slice(0, 3).join(' | ') : '无页面错误');
  await p.screenshot({ path: 'step_zoom.png' });
  await b.close();
})();
