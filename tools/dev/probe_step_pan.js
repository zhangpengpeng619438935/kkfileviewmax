// 测平移：右键拖 / Shift+左键拖 / 方向键 / 中键拖，看画面是否变化
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
  await p.goto(d.previewUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await sleep(7000);
  const shot = async () => h(await p.screenshot({ clip: { x: 0, y: 36, width: 1280, height: 700 } }));
  const base = await shot();
  console.log('初始: ' + base);

  const drag = async (button, mods, dx, dy, label) => {
    for (const m of mods) await p.keyboard.down(m);
    await p.mouse.move(600, 420);
    await p.mouse.down({ button });
    await p.mouse.move(600 + dx, 420 + dy, { steps: 15 });
    await p.mouse.up({ button });
    for (const m of mods) await p.keyboard.up(m);
    await sleep(500);
    const now = await shot();
    console.log((now !== base ? '有反应  ' : '没反应  ') + label + '  ' + now);
    await p.click('#reset'); await sleep(500);
  };

  await drag('right', [], 150, 100, '右键拖（OrbitControls 默认 PAN）');
  await drag('left', ['Shift'], 150, 100, 'Shift + 左键拖');
  await drag('middle', [], 0, 0, '中键拖（默认 DOLLY）');

  // 方向键
  await p.keyboard.press('ArrowUp'); await p.keyboard.press('ArrowUp');
  await sleep(500);
  const k = await shot();
  console.log((k !== base ? '有反应  ' : '没反应  ') + '方向键 ↑↑  ' + k);
  console.log('页面内是否有右键菜单拦截: ' + await p.evaluate(() => {
    const c = document.getElementById('cv');
    const e = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
    return c.dispatchEvent(e) === false ? '是（已 preventDefault）' : '否';
  }));
  await b.close();
})();
