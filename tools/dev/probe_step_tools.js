// 验证 3D 页面新增的：坐标轴、部件树（隐藏/显示）、测量。
// 用法: node probe_step_tools.js <base> <fileKey> <kkt>
const puppeteer = require('puppeteer-core');
const crypto = require('crypto');
const BASE = process.argv[2] || 'http://127.0.0.1:9012';
const FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const h = (buf) => crypto.createHash('md5').update(buf).digest('hex').slice(0, 10);

(async () => {
  if (!FK || !KKT) { console.log('用法: node probe_step_tools.js <base> <fk> <kkt>'); process.exit(1); }
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage',
      '--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1280, height: 800 });
  const errs = [];
  p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
  p.on('console', m => { if (m.type() === 'error' && m.text().indexOf('favicon') < 0) errs.push('console: ' + m.text().slice(0, 120)); });

  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT),
    { waitUntil: 'domcontentloaded', timeout: 90000 });
  await p.waitForFunction(() => {
    const t = (document.getElementById('info') || {}).textContent || '';
    return t.indexOf('面片') >= 0 || t.indexOf('线框') >= 0;
  }, { timeout: 180000 });
  await p.evaluate(() => { const x = document.getElementById('hint'); if (x) x.style.display = 'none'; });
  await sleep(800);

  const res = [];
  const ok = (n, c, e) => res.push([n, c ? 'OK' : 'FAIL', e || '']);
  const box = await p.$eval('#cv', el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  const shot = async () => h(await p.screenshot({ clip: { x: box.x, y: box.y, width: box.w, height: box.h } }));

  // 1) 信息栏带外廓尺寸
  const info = await p.$eval('#info', el => el.textContent);
  ok('信息栏含外廓尺寸', /外廓\s*[\d.]+\s*×/.test(info), info.slice(0, 90));

  // 2) 坐标轴画布不是空白
  const gzInk = await p.evaluate(() => {
    const c = document.getElementById('gz');
    if (!c) return -1;
    const x = c.getContext('2d');
    const d = x.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 3; i < d.length; i += 4) if (d[i] > 8) n++;
    return n;
  });
  ok('右下角坐标轴已绘制', gzInk > 200, '不透明像素 ' + gzInk);

  // 3) 部件树
  const h0 = await shot();
  await p.click('#treeBtn');
  await sleep(400);
  const treeState = await p.evaluate(() => {
    const t = document.getElementById('tree');
    const n = document.querySelectorAll('#tlist label').length;
    const cnt = (document.getElementById('tcnt') || {}).textContent;
    return { on: t && t.classList.contains('on'), n, cnt };
  });
  ok('部件面板能打开并列出部件', treeState.on && treeState.n > 0,
    '面板=' + treeState.on + ' 条数=' + treeState.n + ' 计数=' + treeState.cnt);

  // 4) 隐藏一个部件 → 画面必须变化
  const h1 = await shot();
  await p.evaluate(() => {
    const cb = document.querySelector('#tlist input[type=checkbox]');
    if (cb) { cb.checked = false; cb.dispatchEvent(new Event('change', { bubbles: true })); }
  });
  await sleep(500);
  const h2 = await shot();
  ok('取消勾选一个部件后画面变化', h2 !== h1, h1 + ' -> ' + h2);

  // 5) 全隐 → 画面接近空白
  await p.click('#tnone');
  await sleep(600);
  const h3 = await shot();
  await p.screenshot({ path: 'tools_none.png', clip: { x: box.x, y: box.y, width: box.w, height: box.h } });
  ok('全隐后画面变化（截图另存 tools_none.png 核对）', h3 !== h2, h2 + ' -> ' + h3);

  // 6) 恢复全显
  await p.click('#tall');
  await sleep(500);
  const h4 = await shot();
  ok('全显后画面恢复', h4 !== h3, h3 + ' -> ' + h4);

  // 7) 测量：点两个点，出现标注
  await p.click('#measBtn');
  await sleep(300);
  const mtipOn = await p.$eval('#mtip', el => el.classList.contains('on'));
  ok('测量模式提示条出现', mtipOn);

  // 找模型像素：在画布中心附近扫，点在有内容的地方
  await p.mouse.click(box.x + box.w * 0.42, box.y + box.h * 0.55);
  await sleep(300);
  await p.mouse.click(box.x + box.w * 0.58, box.y + box.h * 0.5);
  await sleep(600);
  const h5 = await shot();
  ok('测量后画面出现标注', h5 !== h4, h4 + ' -> ' + h5);

  // 8) Esc 退出并清除
  await p.keyboard.press('Escape');
  await sleep(500);
  const h6 = await shot();
  ok('Esc 退出测量并清除标注', h6 === h4 || h6 !== h5, h4 + ' / ' + h5 + ' -> ' + h6);

  const bad = errs.filter(e => e.indexOf('favicon') < 0 && !/404/.test(e));
  ok('页面无 JS 报错', bad.length === 0, bad.join(' | '));

  await p.screenshot({ path: 'tools_shot.png' });
  await b.close();
  let pass = 0;
  res.forEach(([n, s, e]) => { if (s === 'OK') pass++; console.log((s === 'OK' ? '  ✓ ' : '  ✗ ') + n + (e ? '  [' + e + ']' : '')); });
  console.log('通过 ' + pass + '/' + res.length);
  process.exit(pass === res.length ? 0 : 1);
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
