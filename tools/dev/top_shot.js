// 顶栏排版检查：菜单是否一行放得下、刷新状态是否独立成行且字号更小
const puppeteer = require('puppeteer-core');
const url = process.argv[2] || 'http://127.0.0.1:9012/';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  for (const [w, h, tag] of [[1440, 900, 'wide'], [1280, 800, 'narrow']]) {
    const p = await b.newPage();
    await p.setViewport({ width: w, height: h });
    await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await sleep(2200);
    const info = await p.evaluate(() => {
      const nav = document.querySelector('.tabs');
      const bar = document.querySelector('.statusbar');
      const tabs = Array.from(document.querySelectorAll('.tab'));
      const tops = new Set(tabs.map(t => Math.round(t.getBoundingClientRect().top)));
      const lu = document.getElementById('lastUpdate');
      const cs = getComputedStyle(lu);
      return {
        tabRows: tops.size,
        tabCount: tabs.length,
        navH: Math.round(nav.getBoundingClientRect().height),
        navScroll: nav.scrollWidth > nav.clientWidth + 1,
        barH: Math.round(bar.getBoundingClientRect().height),
        inNav: !!nav.querySelector('#lastUpdate'),
        inBar: !!bar.querySelector('#lastUpdate'),
        updateFont: cs.fontSize,
        updateColor: cs.color,
        bodyFont: getComputedStyle(document.body).fontSize,
        updateText: lu.textContent.trim(),
        barTop: Math.round(bar.getBoundingClientRect().top),
        navBottom: Math.round(nav.getBoundingClientRect().bottom),
      };
    });
    console.log(tag + ': ' + JSON.stringify(info));
    await p.screenshot({ path: 'top_' + tag + '.png', clip: { x: 0, y: 0, width: w, height: 320 } });
    await p.close();
  }
  await b.close();
})();
