const puppeteer = require('puppeteer-core');
const pdf = process.argv[2];
const waitSec = +(process.argv[3] || 240);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--no-sandbox']});
  const p = await b.newPage();
  await p.setViewport({width: 1400, height: 860});
  p.on('pageerror', (e) => console.log('  [js error]', String(e).slice(0, 120)));
  const t0 = Date.now();
  await p.goto('http://127.0.0.1:9012/kk/pdfjs/web/viewer.html?file=' + encodeURIComponent(pdf), {waitUntil: 'domcontentloaded', timeout: 120000});
  let st = null;
  for (let i = 0; i < waitSec * 2; i++) {
    st = await p.evaluate(() => {
      const app = window.PDFViewerApplication;
      if (!app) return { s: 'no-app' };
      const pg = app.pdfViewer && app.pdfViewer._pages && app.pdfViewer._pages[0];
      const c = document.querySelector('canvas');
      let ink = 0;
      if (c && c.width) {
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        for (let k = 0; k < d.length; k += 4 * 373) if (d[k] < 240) ink++;
      }
      return { rs: pg ? pg.renderingState : -1, pages: app.pagesCount, ink, loading: !!document.querySelector('#loadingIconContainer') && getComputedStyle(document.querySelector('#loadingIconContainer')).display !== 'none' };
    }).catch(() => null);
    if (st && st.rs === 3) break;
    if (i % 20 === 0 && i) console.log('  ' + (Date.now() - t0) + 'ms ' + JSON.stringify(st));
    await sleep(500);
  }
  console.log('[结果] ' + ((Date.now() - t0) / 1000).toFixed(1) + 's  ' + JSON.stringify(st) + '   (renderingState 3=FINISHED)');
  await p.screenshot({path: 'wait_' + (process.argv[4] || 'x') + '.png'});
  await b.close();
})();
