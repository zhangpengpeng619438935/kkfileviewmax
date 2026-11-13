const puppeteer = require('puppeteer-core');
const url = process.argv[2];
const tag = process.argv[3] || 'x';
const waitSec = +(process.argv[4] || 25);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--no-sandbox']});
  const p = await b.newPage();
  await p.setViewport({width: 1400, height: 880});
  const errs = [];
  p.on('pageerror', (e) => errs.push('PAGEERR ' + String(e).slice(0, 200)));
  p.on('console', (m) => { if (m.type() === 'error') errs.push('CONS ' + m.text().slice(0, 200)); });
  const t0 = Date.now();
  await p.goto(url, {waitUntil: 'networkidle2', timeout: 60000}).catch((e) => errs.push('NAV ' + e.message));
  let st = null;
  for (let i = 0; i < waitSec * 2; i++) {
    st = await p.evaluate(() => ({
      canvases: Array.from(document.querySelectorAll('canvas')).filter((c) => c.width > 50).length,
      mask: (document.getElementById('mask') || {}).className || '',
      tip: (document.getElementById('tip') || {}).textContent || '',
      body: document.body.innerText.replace(/\s+/g, ' ').slice(0, 80),
    })).catch(() => null);
    if (st && st.canvases > 0 && !/on/.test(st.mask)) break;
    await sleep(500);
  }
  console.log(`[${tag}] ${Date.now() - t0}ms canvas=${st && st.canvases} mask=${st && st.mask}`);
  console.log(`      tip=${st && st.tip}`);
  if (errs.length) console.log('      错误:', errs.slice(0, 4).join(' | '));
  await p.screenshot({path: 'univer_' + tag + '.png'});
  await b.close();
})();
