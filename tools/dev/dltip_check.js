/* 顶部"下载 / 网速"提示自检：字号、位置、与顶栏/进度条的关系，以及测速按钮是否也走这里。
   用法：node tools/dev/dltip_check.js <pageUrl> <截图路径> */
const puppeteer = require('puppeteer-core');
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const url = process.argv[2], out = process.argv[3];

(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1400, height: 800 });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 40000 });
  await new Promise(r => setTimeout(r, 1200));

  const measure = () => p.evaluate(() => {
    const el = document.getElementById('dlTip');
    el.classList.add('on');
    el.textContent = '正在下载实体网格 12/35 MB · 28.4 MB/s';
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const top = document.getElementById('top').getBoundingClientRect();
    const mtip = getComputedStyle(document.getElementById('mtip'));
    return {
      shown: cs.display !== 'none',
      box: { w: Math.round(r.width), h: Math.round(r.height), y: Math.round(r.top) },
      font: cs.fontSize, radius: cs.borderRadius,
      gapBelowTop: Math.round(r.top - top.bottom),
      centerOffset: Math.round((r.left + r.width / 2) - window.innerWidth / 2),
      mtipFont: mtip.fontSize,
    };
  });
  console.log('下载提示：', JSON.stringify(await measure()));
  if (out) await p.screenshot({ path: out, clip: { x: 0, y: 0, width: 1400, height: 260 } });

  // 真实点一次测速按钮，确认结果也出现在顶部这条
  await p.evaluate(() => { document.getElementById('netBtn').click(); });
  let got = '';
  for (let i = 0; i < 40; i++) {
    await new Promise(r => setTimeout(r, 500));
    got = await p.evaluate(() => {
      const el = document.getElementById('dlTip');
      return el.classList.contains('on') ? el.textContent : '';
    });
    if (got && /测速/.test(got)) break;
  }
  console.log('点测速后的顶部提示：%j', got.slice(0, 60));
  const btn = await p.evaluate(() => document.getElementById('netBtn').textContent);
  console.log('顶栏测速按钮文字：%j', btn);

  await p.evaluate(() => document.getElementById('dlTip').classList.add('on'));
  if (out) await p.screenshot({ path: out.replace('.png', '_speed.png'),
    clip: { x: 0, y: 0, width: 1400, height: 260 } });
  await b.close();
})();
