// 找出页面里"能滚动/带滚动条"的元素，便于用 CSS 精确隐藏
const puppeteer = require('puppeteer-core');
const url = process.argv[2];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox'],
  });
  const p = await b.newPage();
  await p.setViewport({ width: 1500, height: 860 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await sleep(6000);
  const info = await p.evaluate(() => {
    const out = [];
    document.querySelectorAll('*').forEach((el) => {
      const cs = getComputedStyle(el);
      if (!/auto|scroll/.test(cs.overflowY + cs.overflowX)) return;
      if (el.scrollHeight <= el.clientHeight + 1 && el.scrollWidth <= el.clientWidth + 1) return;
      const r = el.getBoundingClientRect();
      out.push({
        tag: el.tagName.toLowerCase(),
        comp: el.getAttribute('data-u-comp') || '',
        cls: (el.className || '').toString().slice(0, 70),
        w: Math.round(r.width), h: Math.round(r.height),
        sh: el.scrollHeight, ch: el.clientHeight,
      });
    });
    return out;
  });
  console.log(JSON.stringify(info, null, 1).slice(0, 2000));
  await b.close();
})();
