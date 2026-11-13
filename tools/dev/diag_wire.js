const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox','--use-gl=swiftshader','--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1200, height: 800 });
  p.on('pageerror', e => console.log('  PAGEERR', String(e).slice(0,200)));
  p.on('console', m => { if (m.type()==='error') console.log('  CONSOLE', m.text().slice(0,160)); });
  p.on('response', async r => {
    if (r.url().indexOf('/stepdata/') >= 0 || r.url().indexOf('/stepstate/') >= 0) {
      console.log('  NET', r.url().replace(/\?.*/,''), r.status(),
        r.headers()['content-length'] || '(no len)');
    }
  });
  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil:'domcontentloaded', timeout:90000 });
  await sleep(9000);
  const st = await p.evaluate(() => {
    const i = document.getElementById('info');
    return { info: i ? i.textContent : '(no info)',
             ld: (document.getElementById('ld')||{}).className || '',
             canvas: (function(){ const c=document.getElementById('cv'); return c? c.width+'x'+c.height : 'none'; })(),
             hasDebug: typeof window.__stepMaxOffset };
  });
  console.log('  info:', st.info.slice(0,80));
  console.log('  ld:', st.ld, '| canvas:', st.canvas, '| debug:', st.hasDebug);
  await p.screenshot({ path: 'diag_v3.png' });
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
