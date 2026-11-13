// 抓几张不同俯仰角的画面，肉眼确认"能翻到底面"。
// 用法: node shot_step_orbit.js <base> <fileKey> <kkt> <outPrefix>
const puppeteer = require('puppeteer-core');
const BASE = process.argv[2] || 'http://127.0.0.1:9012';
const FK = process.argv[3], KKT = process.argv[4], OUT = process.argv[5] || 'orbit';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage',
      '--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1280, height: 800 });
  p.on('response', r => { if (r.status() >= 400) console.log('  HTTP', r.status(), r.url().slice(0, 90)); });
  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT),
    { waitUntil: 'domcontentloaded', timeout: 90000 });
  await p.waitForFunction(
    () => (document.getElementById('info') || {}).textContent &&
          document.getElementById('info').textContent.indexOf('面片') >= 0,
    { timeout: 120000 });
  await sleep(800);
  const box = await p.$eval('#cv', el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
  async function drag(dx, dy, steps = 20) {
    await p.mouse.move(cx, cy); await p.mouse.down({ button: 'left' });
    for (let i = 1; i <= steps; i++) { await p.mouse.move(cx + dx * i / steps, cy + dy * i / steps); await sleep(8); }
    await p.mouse.up({ button: 'left' }); await sleep(450);
  }
  const snap = async (n) => { await p.screenshot({ path: OUT + '_' + n + '.png', clip: { x: box.x, y: box.y, width: box.w, height: box.h } }); console.log('  saved', OUT + '_' + n + '.png'); };
  await snap('1_init');
  await drag(0, 260); await snap('2_down260');   // 往下拖一次
  await drag(0, 260); await snap('3_down520');   // 再往下
  await p.click('#reset'); await sleep(700);
  await drag(0, -260); await snap('4_up260');
  await drag(0, -260); await snap('5_up520');
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
