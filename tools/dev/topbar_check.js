/* 顶栏改版自检：三行结构、按钮完整可见、倍率徽标位置与配色、滚轮后倍率是否更新。
   用法：node tools/dev/topbar_check.js <pageUrl> [宽] [高] */
const puppeteer = require('puppeteer-core');

const url = process.argv[2];
const W = parseInt(process.argv[3] || '1400', 10);
const H = parseInt(process.argv[4] || '760', 10);

(async () => {
  const b = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: W, height: H });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  // 等网格画出来（最多 20 秒）
  try {
    await p.waitForFunction(() => window.__stepRender === 'mesh', { timeout: 20000 });
  } catch (e) { console.log('warn: mesh not rendered in 20s'); }
  await new Promise(r => setTimeout(r, 800));

  const info = await p.evaluate(() => {
    const top = document.getElementById('top');
    const bar = document.getElementById('bar');
    const z = document.getElementById('zoom');
    const inf = document.getElementById('info');
    const cs = getComputedStyle(z), ci = getComputedStyle(inf);
    const topR = top.getBoundingClientRect();
    // 每个按钮是否完整落在按钮行矩形内
    const bad = [];
    bar.querySelectorAll('button').forEach(btn => {
      const r = btn.getBoundingClientRect();
      if (r.right > bar.getBoundingClientRect().right + 0.5 || r.bottom > bar.getBoundingClientRect().bottom + 0.5) {
        bad.push(btn.id || btn.textContent.trim());
      }
    });
    const zr = z.getBoundingClientRect();
    return {
      topH: Math.round(topR.height),
      rows: [...top.children].map(c => c.className + ':' + Math.round(c.getBoundingClientRect().height)),
      barScrollW: bar.scrollWidth, barClientW: bar.clientWidth,
      clipped: bad,
      zoomText: z.textContent,
      zoomBg: cs.backgroundColor, zoomColor: cs.color, zoomFont: cs.fontSize,
      zoomRight: Math.round(window.innerWidth - zr.right), zoomTop: Math.round(zr.top),
      infoColor: ci.color, infoText: inf.textContent.slice(0, 60),
      infoRowTop: Math.round(inf.getBoundingClientRect().top),
      ver: document.getElementById('ver').textContent,
      canvasH: Math.round(document.getElementById('cv').getBoundingClientRect().height),
    };
  });
  console.log('初始状态', JSON.stringify(info, null, 1));

  // 滚轮放大两档，看徽标是否跟着变
  await p.mouse.move(W / 2, H / 2);
  await p.mouse.wheel({ deltaY: -300 });
  await new Promise(r => setTimeout(r, 600));
  const after = await p.evaluate(() => document.getElementById('zoom').textContent);
  console.log('滚轮放大后倍率:', after);

  const out = process.env.SHOT || '';
  if (out) await p.screenshot({ path: out });
  await b.close();
})();
