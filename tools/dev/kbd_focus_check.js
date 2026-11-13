/* iframe 嵌套下的键盘焦点自检：模拟业务系统内嵌预览页，验证"不点页面也能用键盘"。
   步骤：外层点一下 → iframe 失焦 → 按 Q → 看 3D 转没转；再点画布 → 按 Q → 再看。
   用法：node tools/dev/kbd_focus_check.js <预览页 URL> */
const puppeteer = require('puppeteer-core');
const EXE = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const url = process.argv[2];
const OUTER = 'file:///C:/Users/example/AppData/Local/Temp/ifr/outer.html';

(async () => {
  const b = await puppeteer.launch({ executablePath: EXE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] });
  const p = await b.newPage();
  // 无头浏览器里 document.hasFocus() 恒为 true，测不出 iframe 失焦场景，
  // 这里把它固定成 false 来模拟真实业务的 iframe 嵌套（焦点在外层系统页）。
  await p.evaluateOnNewDocument(() => {
    try { Object.defineProperty(Document.prototype, 'hasFocus', { value: () => false }); } catch (e) {}
  });
  await p.setViewport({ width: 1400, height: 900 });
  await p.goto(OUTER + '#' + encodeURIComponent(url), { waitUntil: 'domcontentloaded' });

  const frame = await (async () => {
    for (let i = 0; i < 60; i++) {
      const f = p.frames().find(x => x.url().includes('/stepview/'));
      if (f) return f;
      await new Promise(r => setTimeout(r, 500));
    }
    throw new Error('iframe 没加载出来');
  })();
  await frame.waitForFunction(() => window.__stepDebug && window.__stepDebug().children > 0,
    { timeout: 40000 });
  await new Promise(r => setTimeout(r, 1500));

  const cam = () => frame.evaluate(() => {
    const d = window.__stepDebug();
    return { cam: d.cam.map(v => +v.toFixed(2)), up: d.up.map(v => +v.toFixed(3)) };
  });
  const moved = (a, c) => Math.hypot(a.cam[0]-c.cam[0], a.cam[1]-c.cam[1], a.cam[2]-c.cam[2]);

  // ① 外层先点一下，让 iframe 失焦（模拟"刚打开业务系统页面"）
  await p.click('#blank');
  await new Promise(r => setTimeout(r, 1600));
  const tip1 = await frame.evaluate(() => {
    const t = document.getElementById('keyTip');
    return { shown: t.classList.contains('on'), text: t.textContent.trim().slice(0, 28) };
  });
  console.log('无焦点时的提示：%j', tip1);
  if (process.env.SHOT_TIP) await p.screenshot({ path: process.env.SHOT_TIP });
  const a0 = await cam();
  await p.keyboard.press('q');
  await new Promise(r => setTimeout(r, 400));
  const a1 = await cam();
  const d1 = moved(a0, a1);
  console.log('未点 iframe 内，按 Q：相机位移 %s → %s', d1.toFixed(2),
    d1 > 1 ? '键盘生效 ✓' : '键盘无效 ✗（仍需先点页面）');

  // ② 点一下 iframe 里的画布，再按 Q
  const fr = await (await p.$('#fr')).boundingBox();
  const cr = await frame.evaluate(() => {
    const r = document.getElementById('cv').getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  });
  const box = { x: fr.x + cr.x, y: fr.y + cr.y };
  await p.mouse.click(box.x, box.y);
  await new Promise(r => setTimeout(r, 500));
  const tip2 = await frame.evaluate(() =>
    ({ shown: document.getElementById('keyTip').classList.contains('on') }));
  console.log('点过画面后提示：%j（应 shown:false）', tip2);
  const b0 = await cam();
  await p.keyboard.press('q');
  await new Promise(r => setTimeout(r, 400));
  const b1 = await cam();
  const d2 = moved(b0, b1);
  console.log('点过画布后，按 Q：相机位移 %s → %s', d2.toFixed(2),
    d2 > 1 ? '键盘生效 ✓' : '键盘无效 ✗');

  // ③ A / W / D 也试一下，确认三组旋转都能用
  const c0 = await cam();
  await p.keyboard.press('a');
  await p.keyboard.press('w');
  await p.keyboard.press('d');
  await new Promise(r => setTimeout(r, 500));
  const c1 = await cam();
  console.log('A/W/D 三键：相机位移 %s、up 变化 %s',
    moved(c0, c1).toFixed(2),
    Math.hypot(c0.up[0]-c1.up[0], c0.up[1]-c1.up[1], c0.up[2]-c1.up[2]).toFixed(3));

  // ④ 在部件搜索框里打字，不该触发旋转
  await p.mouse.click(box.x, box.y);
  await frame.evaluate(() => { document.getElementById('treeBtn').click(); });
  await new Promise(r => setTimeout(r, 400));
  await frame.evaluate(() => document.getElementById('tfind').focus());
  const d0 = await cam();
  await p.keyboard.type('qwe');
  await new Promise(r => setTimeout(r, 400));
  const d1b = await cam();
  console.log('搜索框输入 qwe：相机位移 %s、输入内容 %j → %s',
    moved(d0, d1b).toFixed(2),
    await frame.evaluate(() => document.getElementById('tfind').value),
    moved(d0, d1b) < 0.01 ? '没有误触发旋转 ✓' : '误触发 ✗');

  await b.close();
})();
