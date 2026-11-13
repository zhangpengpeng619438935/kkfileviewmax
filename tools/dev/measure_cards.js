const puppeteer = require('puppeteer-core');
(async () => {
  const b = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1440, height: 900 });
  await p.goto('http://127.0.0.1:9012/', { waitUntil: 'domcontentloaded' });
  await new Promise(r => setTimeout(r, 2000));
  for (const view of ['overview', 'alerts', 'failures']) {
    await p.evaluate((v) => document.querySelector('.tab[data-view="' + v + '"]').click(), view);
    await new Promise(r => setTimeout(r, 1500));
    const info = await p.evaluate(() => {
      const box = document.querySelector('.view:not(.hidden) .cards');
      if (!box) return null;
      const cs = getComputedStyle(box);
      return {
        display: cs.display, cols: cs.gridTemplateColumns,
        widths: Array.from(box.children).map(c => Math.round(c.getBoundingClientRect().width)),
      };
    });
    console.log(view + ': ' + JSON.stringify(info));
  }
  await b.close();
})();
