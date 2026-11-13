const puppeteer = require('puppeteer-core');
const url = process.argv[2], out = process.argv[3];
(async () => {
  const b = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: 'new', args: ['--no-sandbox','--disable-gpu'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1400, height: 800 });
  await p.goto(url, { waitUntil: 'domcontentloaded' });
  await p.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0, { timeout: 40000 });
  await new Promise(r => setTimeout(r, 1200));
  // 滚轮放大 6 档（约 1.25^6 ≈ 3.8 倍）
  await p.mouse.move(700, 430);
  for (let i = 0; i < 6; i++) { await p.mouse.wheel({ deltaY: -200 }); await new Promise(r => setTimeout(r, 120)); }
  await p.screenshot({ path: out.replace('.png','_zoom.png') });
  // 右键平移，把关心的局部拖到中间
  await p.mouse.down({ button: 'right' });
  await p.mouse.move(880, 520, { steps: 14 });
  await p.mouse.up({ button: 'right' });
  await new Promise(r => setTimeout(r, 300));
  await p.screenshot({ path: out.replace('.png','_pan.png') });
  const s = () => p.evaluate(() => { const d = window.__stepDebug();
    const dd=(a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1],a[2]-b[2]);
    return { dCam:+dd(d.cam,d.cluC).toFixed(1), dTgt:+dd(d.target,d.cluC).toFixed(1) }; });
  console.log('放大+平移后', JSON.stringify(await s()));
  for (let i = 0; i < 3; i++) { await p.click('#spinL'); await new Promise(r => setTimeout(r, 250)); }
  console.log('⟲×3 后    ', JSON.stringify(await s()));
  await p.screenshot({ path: out.replace('.png','_rot.png') });
  await b.close();
})();
