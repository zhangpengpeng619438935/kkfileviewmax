// 测放大能力：滚轮 N 格后的倍数徽标、能否贴近到细节、复位是否回到 1.0×
const puppeteer = require('puppeteer-core');
const crypto = require('crypto');
const BASE = process.argv[2], URL = process.argv[3];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const h = b => crypto.createHash('md5').update(b).digest('hex').slice(0, 10);
(async () => {
  const api = async (p, body, t) => (await fetch(BASE + p, { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-App-Id': 'kk-demo',
      ...(t ? { Authorization: 'Bearer ' + t } : {}) }, body: JSON.stringify(body) })).json();
  const tk = (await api('/api/v1/auth/token', { appId: 'kk-demo', appSecret: 'your-app-secret' })).data.accessToken;
  const d = (await api('/api/v1/preview/url', { fileUrl: URL, empNo: 'SYS_PREWARM' }, tk)).data;
  const b = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1280, height: 800 });
  const errs = [];
  p.on('pageerror', e => errs.push(String(e).slice(0, 120)));
  await p.goto(d.previewUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await sleep(7000);
  const badge = () => p.evaluate(() => document.getElementById('zoom').textContent);
  const shot = async () => h(await p.screenshot({ clip: { x: 0, y: 36, width: 1280, height: 700 } }));
  console.log('初始倍数: ' + await badge() + '  hash ' + await shot());

  // 光标停在画面左下（模型主结构处）连滚，模拟用户盯着一处细节放大
  await p.mouse.move(560, 470);
  for (const n of [2, 5, 10, 20, 30]) {
    // 增量滚动
    const prev = { 2: 0, 5: 2, 10: 5, 20: 10, 30: 20 }[n];
    for (let i = 0; i < n - prev; i++) { await p.mouse.wheel({ deltaY: -120 }); await sleep(90); }
    await sleep(300);
    console.log('滚 ' + String(n).padStart(2) + ' 格: 倍数 ' + await badge());
  }
  await p.screenshot({ path: 'step_zoommax.png' });

  await p.click('#reset'); await sleep(800);
  console.log('复位后倍数: ' + await badge());
  // 按钮步长
  await p.click('#zin'); await p.click('#zin'); await sleep(500);
  console.log('点两次 ＋: ' + await badge());
  console.log(errs.length ? '页面错误: ' + errs.slice(0, 2).join(' | ') : '无页面错误');
  await b.close();
})();
