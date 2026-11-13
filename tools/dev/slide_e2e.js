// 幻灯片交互与耗时：首屏可见时间、滚轮翻页、页码提示
// 用法: node tools/slide_e2e.js "<previewUrl>" <tag>
const puppeteer = require('puppeteer-core');

const url = process.argv[2];
const tag = process.argv[3] || 'sl';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const p = await b.newPage();
  await p.setViewport({ width: 1500, height: 860 });
  const t0 = Date.now();
  const notFound = [];
  p.on('response', (r) => { if (r.status() === 404) notFound.push(r.url().slice(-70)); });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });

  let seen = 0;
  for (let i = 0; i < 400; i++) {
    const st = await p.evaluate(() => {
      const c = Array.from(document.querySelectorAll('canvas')).filter((x) => x.width > 100);
      const mask = (document.getElementById('mask') || {}).className || '';
      return { c: c.length, mask };
    });
    if (st.c && !/on/.test(st.mask)) { seen = Date.now() - t0; break; }
    await sleep(100);
  }
  console.log('[1] 画布出现 ' + seen + 'ms  (' + p.url().replace(/^http:\/\/[^/]+/, '').slice(0, 40) + ')');
  await p.screenshot({ path: 'sl_' + tag + '_p1.png' });

  // 滚轮翻页（Univer 幻灯片按纵向排列，滚一格换一页）
  await p.mouse.move(750, 430);
  await p.mouse.wheel({ deltaY: 900 });
  await sleep(900);
  await p.screenshot({ path: 'sl_' + tag + '_p2.png' });
  await p.mouse.wheel({ deltaY: 900 });
  await sleep(900);
  await p.screenshot({ path: 'sl_' + tag + '_p3.png' });

  // 页面上的可见文字（页码/提示）
  const txt = await p.evaluate(() => (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 120));
  console.log('[2] 页面文字: ' + (txt || '(空)'));
  if (notFound.length) console.log('[3] 404: ' + notFound.slice(0, 3).join(' | '));
  console.log('[T] 到画布可见 ' + (Date.now() - t0) + 'ms');
  await b.close();
})();
