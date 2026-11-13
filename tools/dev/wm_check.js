// 水印自检：元素在不在、尺寸对不对、滚动/翻页后还在不在。
// 用法: node tools/dev/wm_check.js "<previewUrl>" [tag]
//
// 覆盖的三类页面：
//   /univer/*   自研 Univer 页（xlsx/xls/pptx/ppt），水印是 #wm 固定层
//   /pdfview/*  本地 PDF + pdf.js，水印在 #viewerContainer::after
//   /kk/*       引擎页（csv、图片、引擎 pdf.js），水印由引擎 watermark.js 生成
const puppeteer = require('puppeteer-core');
const url = process.argv[2];
const tag = process.argv[3] || 'wm';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const p = await b.newPage();
  await p.setViewport({ width: 1440, height: 900 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await sleep(6000);

  const probe = () => p.evaluate(() => {
    const wm = document.getElementById('wm');
    const out = { path: location.pathname };
    if (wm) {
      const r = wm.getBoundingClientRect();
      const cs = getComputedStyle(wm);
      out.wm = {
        size: [Math.round(r.width), Math.round(r.height)],
        hasBg: (cs.backgroundImage || 'none') !== 'none',
        z: cs.zIndex, pe: cs.pointerEvents,
      };
    } else {
      out.wm = null;
    }
    // Univer 幻灯片页码 / 最高的可滚动容器
    out.label = (document.getElementById('kk-page') || {}).textContent || '';
    let best = null;
    document.querySelectorAll('*').forEach((el) => {
      const cs = getComputedStyle(el);
      if (!/auto|scroll/.test(cs.overflowY)) return;
      if (el.scrollHeight <= el.clientHeight + 1) return;
      if (!best || el.scrollHeight > best.scrollHeight) best = el;
    });
    out.scroller = best
      ? { h: best.clientHeight, sh: best.scrollHeight, top: best.scrollTop } : null;
    return out;
  });

  console.log('首屏:', JSON.stringify(await probe()));
  await p.screenshot({ path: `wm_${tag}_1.png` });

  // 有页码就翻页（Univer 幻灯片），否则把最高的可滚动容器滚到底
  for (let i = 0; i < 3; i++) { await p.keyboard.press('PageDown'); await sleep(700); }
  await sleep(1500);
  await p.evaluate(() => {
    let best = null;
    document.querySelectorAll('*').forEach((el) => {
      const cs = getComputedStyle(el);
      if (!/auto|scroll/.test(cs.overflowY)) return;
      if (el.scrollHeight <= el.clientHeight + 1) return;
      if (!best || el.scrollHeight > best.scrollHeight) best = el;
    });
    if (best) best.scrollTop = best.scrollHeight;
  });
  await sleep(2500);
  console.log('滚到底/翻页后:', JSON.stringify(await probe()));
  await p.screenshot({ path: `wm_${tag}_2.png` });
  await b.close();
})();
