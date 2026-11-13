/* 操作提示自检：模型出来时提示应自动可见（不被遮罩压住），30 秒后才淡出。
   用法：node tools/dev/hint_check.js <pageUrl> <截图路径> */
const puppeteer = require('puppeteer-core');
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const url = process.argv[2], out = process.argv[3];

(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1400, height: 800 });

  const t0 = Date.now();
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 40000 });
  const tReady = ((Date.now() - t0) / 1000).toFixed(1);
  await new Promise(r => setTimeout(r, 900));

  const st = () => p.evaluate(() => {
    const h = document.getElementById('hint');
    const cs = getComputedStyle(h);
    const ld = document.getElementById('ld');
    return { hidden: h.classList.contains('hide'), opacity: cs.opacity,
             display: cs.display, z: cs.zIndex,
             ldVisible: ld ? !ld.classList.contains('hide') : null,
             chars: h.textContent.replace(/\s+/g, ' ').trim().length };
  });
  console.log('模型就绪用时 %ss', tReady);
  console.log('刚就绪：', JSON.stringify(await st()));
  if (out) await p.screenshot({ path: out });

  await new Promise(r => setTimeout(r, 4000));
  console.log('4 秒后：', JSON.stringify(await st()), '（应仍 visible）');

  // 点 ? 应能关掉、再点打开
  await p.click('#help');
  await new Promise(r => setTimeout(r, 600));
  console.log('点 ? 后：', JSON.stringify(await st()));
  await p.click('#help');
  await new Promise(r => setTimeout(r, 700));
  console.log('再点 ?：', JSON.stringify(await st()), '（点 ? 打开的不自动淡出）');
  await b.close();
})();
