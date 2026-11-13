const puppeteer = require('puppeteer-core');
const pdf = process.argv[2];
const waitSec = +(process.argv[3] || 90);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--no-sandbox']});
  const p = await b.newPage();
  await p.setViewport({width: 1400, height: 860});
  const t0 = Date.now();
  const big = [];
  p.on('requestfinished', (r) => {
    const t = r.timing ? r.timing() : null;
    if (!t || !t.responseEnd) return;
    const u = r.url();
    if (/\.(js|css|woff2?|png|ico)\b/.test(u)) return;
    big.push({ u: u.replace('http://127.0.0.1:9012', ''), tfb: t.responseStart, end: t.responseEnd });
  });
  await p.goto('http://127.0.0.1:9012/kk/pdfjs/web/viewer.html?file=' + encodeURIComponent(pdf), {waitUntil: 'domcontentloaded', timeout: 120000});
  console.log('[1] viewer HTML ' + (Date.now() - t0) + 'ms');

  let painted = null;
  for (let i = 0; i < waitSec * 4; i++) {
    painted = await p.evaluate(() => {
      const c = document.querySelector('canvas');
      if (!c || !c.width) return { ok: false, why: 'no-canvas' };
      const ctx = c.getContext('2d');
      let ink = 0;
      try {
        const d = ctx.getImageData(0, 0, c.width, c.height).data;
        for (let k = 0; k < d.length; k += 4 * 977) if (d[k] < 245 || d[k + 1] < 245 || d[k + 2] < 245) ink++;
      } catch (e) { ink = -1; }
      return { ok: ink > 3, why: 'ink=' + ink, w: c.width, h: c.height };
    }).catch(() => null);
    if (painted && painted.ok) break;
    await sleep(250);
  }
  const tPaint = Date.now() - t0;
  console.log('[2] 图纸可见 ' + tPaint + 'ms  ' + JSON.stringify(painted));
  big.sort((a, b2) => b2.end - a.end).slice(0, 6).forEach((r) => {
    console.log(`    ${String(r.tfb.toFixed(0) + 'ms').padEnd(9)} wait  ${String((r.end - r.tfb).toFixed(0) + 'ms').padEnd(9)} dl   ${r.u.slice(0, 90)}`);
  });
  await p.screenshot({path: 'pdfview_' + (process.argv[4] || 'x') + '.png'});
  await b.close();
})();
