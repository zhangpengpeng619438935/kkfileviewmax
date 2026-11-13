// 幻灯片顶部被遮/被裁的定位脚本
// 用法: node tools/dev/slide_top_probe.js "<previewUrl>" <tag>
const puppeteer = require('puppeteer-core');
const url = process.argv[2];
const tag = process.argv[3] || 'slide';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const p = await b.newPage();
  await p.setViewport({ width: 1440, height: 900 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await sleep(3500);

  const info = await p.evaluate(() => {
    const out = { vw: innerWidth, vh: innerHeight, dpr: devicePixelRatio, comps: [], canvases: [] };
    document.querySelectorAll('[data-u-comp]').forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.height > 0) {
        out.comps.push({
          comp: el.getAttribute('data-u-comp'),
          top: Math.round(r.top), left: Math.round(r.left),
          w: Math.round(r.width), h: Math.round(r.height),
        });
      }
    });
    document.querySelectorAll('canvas').forEach((c) => {
      const r = c.getBoundingClientRect();
      out.canvases.push({
        cls: (c.className || '').slice(0, 40),
        top: Math.round(r.top), left: Math.round(r.left),
        w: Math.round(r.width), h: Math.round(r.height),
      });
    });
    return out;
  });
  console.log('视口:', info.vw + 'x' + info.vh, 'dpr', info.dpr);
  console.log('UI 组件:');
  info.comps.sort((a, b2) => a.top - b2.top).slice(0, 10)
    .forEach((c) => console.log(`  ${String(c.top).padStart(4)}  h=${String(c.h).padStart(3)} w=${String(c.w).padStart(4)}  ${c.comp}`));
  console.log('canvas:');
  info.canvases.forEach((c) => console.log(`  top=${c.top} left=${c.left} ${c.w}x${c.h} ${c.cls}`));

  await p.screenshot({ path: `slidetop_${tag}_full.png` });
  await p.screenshot({ path: `slidetop_${tag}_top.png`, clip: { x: 0, y: 0, width: 1440, height: 260 } });
  await b.close();
})();
