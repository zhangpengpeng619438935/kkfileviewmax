/* 进度条样式自检：量出宽度/条高/字号/位置，并截图看它是否压在顶栏下方、是否居中。
   进度条只在"实体网格后台解析"时出现，这里手动点亮它，纯验证样式。
   用法：node tools/dev/prog_check.js <pageUrl> <截图路径> */
const puppeteer = require('puppeteer-core');
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const url = process.argv[2], out = process.argv[3];

(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1400, height: 800 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 40000 });
  await new Promise(r => setTimeout(r, 1200));

  const m = await p.evaluate(() => {
    const pr = document.getElementById('prog');
    pr.classList.add('on');
    document.getElementById('progFill').style.width = '42%';
    document.getElementById('progTxt').textContent = '实体网格解析 12 / 30 个零件';
    const r = pr.getBoundingClientRect();
    const bar = pr.querySelector('.bar').getBoundingClientRect();
    const cs = getComputedStyle(document.getElementById('progTxt'));
    const top = document.getElementById('top').getBoundingClientRect();
    return {
      box: { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.left), y: Math.round(r.top) },
      bar: { w: Math.round(bar.width), h: Math.round(bar.height) },
      font: cs.fontSize,
      topH: Math.round(top.height),
      gapBelowTop: Math.round(r.top - top.bottom),
      centerOffset: Math.round((r.left + r.width / 2) - window.innerWidth / 2),
      viewport: { w: window.innerWidth, h: window.innerHeight },
    };
  });
  console.log(JSON.stringify(m, null, 1));
  if (out) await p.screenshot({ path: out, clip: { x: 0, y: 0, width: 1400, height: 260 } });
  await b.close();
})();
