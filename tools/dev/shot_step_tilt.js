const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage',
      '--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1280, height: 800 });
  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT),
    { waitUntil: 'domcontentloaded', timeout: 90000 });
  await p.waitForFunction(() => (document.getElementById('info') || {}).textContent &&
    (function(){var t=(document.getElementById('info')||{}).textContent||'';return t.indexOf('面片')>=0||t.indexOf('线框')>=0;})(),
    { timeout: 180000 });
  await sleep(800);
  const box = await p.$eval('#cv', el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  const snap = async (n) => { await p.screenshot({ path: 'tilt_' + n + '.png', clip: { x: box.x, y: box.y, width: box.w, height: box.h } }); console.log('saved tilt_' + n); };
  await snap('1_init');
  for (let i = 0; i < 6; i++) { await p.click('#tiltU'); await sleep(150); }
  await sleep(400); await snap('2_up90');
  await p.click('#reset'); await sleep(600);
  for (let i = 0; i < 6; i++) { await p.click('#tiltD'); await sleep(150); }
  await sleep(400); await snap('3_down90');
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
