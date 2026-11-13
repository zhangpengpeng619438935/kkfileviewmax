// 幻灯片页结构探测：截图 + 量画布/页面/侧栏的尺寸与位置
// 用法: node tools/slide_probe.js "<previewUrl>" <tag> [waitSec]
const puppeteer = require('puppeteer-core');

const url = process.argv[2];
const tag = process.argv[3] || 'slide';
const waitSec = +(process.argv[4] || 30);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const p = await b.newPage();
  await p.setViewport({ width: 1500, height: 860 });
  const errs = [];
  p.on('pageerror', (e) => errs.push('PAGEERR ' + String(e).slice(0, 160)));
  p.on('console', (m) => { if (m.type() === 'error') errs.push('CONS ' + m.text().slice(0, 160)); });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 }).catch((e) => errs.push('NAV ' + e.message));

  let st = null;
  for (let i = 0; i < waitSec * 2; i++) {
    st = await p.evaluate(() => {
      const cs = Array.from(document.querySelectorAll('canvas')).filter((c) => c.width > 100 && c.height > 100);
      return {
        url: location.pathname,
        canvases: cs.map((c) => ({
          w: c.width, h: c.height,
          cw: Math.round(c.getBoundingClientRect().width),
          ch: Math.round(c.getBoundingClientRect().height),
          x: Math.round(c.getBoundingClientRect().left),
        })),
        mask: (document.getElementById('mask') || {}).className || '',
        txt: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 60),
      };
    }).catch(() => null);
    if (st && st.canvases.length && !/on/.test(st.mask || '')) break;
    await sleep(500);
  }
  console.log(`[${tag}] 落点=${st && st.url} mask=${st && st.mask} txt=${st && st.txt}`);
  console.log('canvas:', JSON.stringify(st && st.canvases));

  // 结构：所有 data-u-comp 的尺寸
  const parts = await p.evaluate(() => {
    const out = [];
    document.querySelectorAll('[data-u-comp]').forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) return;
      out.push({
        comp: el.getAttribute('data-u-comp'),
        x: Math.round(r.left), y: Math.round(r.top),
        w: Math.round(r.width), h: Math.round(r.height),
        cls: (el.className || '').toString().slice(0, 60),
      });
    });
    return out;
  });
  console.log('parts:', JSON.stringify(parts).slice(0, 1800));

  // 图片元素（幻灯片每页就是一张 img/背景图）
  const imgs = await p.evaluate(() => Array.from(document.querySelectorAll('img'))
    .map((i) => ({ src: i.src.slice(-60), nw: i.naturalWidth, w: Math.round(i.getBoundingClientRect().width) }))
    .filter((i) => i.w > 50).slice(0, 6));
  console.log('imgs:', JSON.stringify(imgs));

  if (errs.length) console.log('错误:', errs.slice(0, 5).join(' | '));
  await p.screenshot({ path: 'slide_' + tag + '.png' });
  await b.close();
})();
