// 整页截图，检查各页排版
const puppeteer = require('puppeteer-core');
const url = process.argv[2] || 'http://127.0.0.1:9012/';
const view = process.argv[3] || 'overview';
const out = process.argv[4] || 'full.png';
const w = Number(process.argv[5] || 1440);
(async () => {
  const b = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const p = await b.newPage();
  await p.setViewport({ width: w, height: 1000 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await new Promise(r => setTimeout(r, 2500));
  await p.evaluate(v => { const t = document.querySelector('.tab[data-view="' + v + '"]'); t && t.click(); }, view);
  await new Promise(r => setTimeout(r, 2500));
  await p.screenshot({ path: out, fullPage: true });
  console.log(out + ' ' + w + 'px ' + view);
  await b.close();
})();
