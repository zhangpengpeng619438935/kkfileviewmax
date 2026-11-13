// 验证"线框 → 实体"升级的中断重试：第一次升级请求故意掐断，看是否自动重试并最终出图
const puppeteer = require('puppeteer-core');
const { execSync } = require('child_process');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--use-gl=swiftshader', '--enable-unsafe-swiftshader'],
  });
  const p = await b.newPage();
  await p.setViewport({ width: 1200, height: 800 });

  let seenReq = 0, aborted = 0;
  await p.setRequestInterception(true);
  p.on('request', (req) => {
    if (req.url().indexOf('/stepdata/') < 0) { req.continue(); return; }
    seenReq++;
    // 第 1 次是页面初始加载（线框），第 2 次才是"升级到实体"，掐它
    if (seenReq === 2 && aborted === 0) {
      aborted = 1;
      console.log('  ✂ 掐断第 2 次请求（升级）');
      req.abort('failed');
    } else {
      req.continue();
    }
  });
  p.on('console', (m) => { if (/UPGRADE_FAIL/.test(m.text())) console.log('  console:' + m.text().slice(0, 90)); });
  p.on('pageerror', (e) => console.log('  PAGEERR', String(e).slice(0, 120)));

  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil: 'domcontentloaded', timeout: 90000 });
  await p.waitForFunction(() => {
    const t = (document.getElementById('info') || {}).textContent || '';
    return t.indexOf('线框') >= 0 || t.indexOf('面片') >= 0;
  }, { timeout: 60000 });
  console.log('  已进入页面：' + ((await p.$eval('#info', (e) => e.textContent)) || '').slice(0, 40));

  const seen = [];
  for (let i = 0; i < 40; i++) {
    const s = await p.evaluate(() => {
      const t = document.getElementById('mtip');
      const info = (document.getElementById('info') || {}).textContent || '';
      return (t && t.className.indexOf('on') >= 0 ? t.textContent : '') + ' || ' + info.slice(0, 30);
    });
    if (seen[seen.length - 1] !== s) seen.push(s);
    if (s.indexOf('面片') >= 0) break;
    await sleep(700);
  }
  console.log('  过程：');
  seen.slice(0, 8).forEach((x) => console.log('    ' + x.replace(/\s+/g, ' ').slice(0, 110)));
  const ok = seen.some((x) => x.indexOf('面片') >= 0);
  console.log(ok ? '  ✓ 中断后仍然自动重试出图' : '  ✗ 没有恢复');
  await b.close();
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.error('ERR', e.message); process.exit(2); });
