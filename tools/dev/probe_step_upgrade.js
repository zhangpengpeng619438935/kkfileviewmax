// 验证"线框自动升级为实体"：页面先以线框打开，网格文件出现在后台后，
// 页面应当自己换成实体（保留视角），不需要用户刷新。
// 用法: node probe_step_upgrade.js <base> <fk> <kkt> <恢复命令>
//   恢复命令：由本脚本在页面加载后执行，用来"模拟后台把网格转好"
const puppeteer = require('puppeteer-core');
const { execSync } = require('child_process');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const RESTORE = process.argv[5];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  if (!FK || !KKT || !RESTORE) { console.log('参数不全'); process.exit(1); }
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage',
      '--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1280, height: 800 });
  const errs = [];
  p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
  p.on('console', m => {
    const t = m.text();
    if (/UPGRADE|FITFAIL|ERROR/i.test(t)) console.log('  CONSOLE:', t.slice(0, 180));
  });
  p.on('response', r => {
    if (r.url().indexOf('/stepstate/') >= 0) {
      r.text().then(x => {}).catch(() => {});
    }
  });

  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT),
    { waitUntil: 'domcontentloaded', timeout: 90000 });
  await p.waitForFunction(() => {
    const t = (document.getElementById('info') || {}).textContent || '';
    return t.indexOf('线框') >= 0 || t.indexOf('面片') >= 0;
  }, { timeout: 180000 });
  const first = await p.$eval('#info', el => el.textContent);
  const isWire = first.indexOf('线框') >= 0;
  console.log('  首屏：' + first.slice(0, 70));
  console.log('  是线框：' + isWire);

  // 记录当前视角（用于确认升级后没跳）
  const cam0 = await p.evaluate(() => {
    const c = document.querySelector('#cv');
    return { w: c.width, h: c.height };
  });

  if (!isWire) { console.log('  ✗ 不是线框态，无法测升级'); await b.close(); process.exit(1); }

  const t0 = Date.now();
  console.log('  执行恢复（模拟网格转好）…');
  execSync(RESTORE, {
    cwd: process.cwd(), stdio: 'inherit', timeout: 120000,
    shell: 'C:/Program Files/Git/bin/bash.exe',   // 命令里有 POSIX 语法，必须走 bash
  });

  let ok = false;
  for (let i = 0; i < 60; i++) {          // 最多等 60 秒（页面 4 秒轮询一次）
    await sleep(1000);
    const t = await p.$eval('#info', el => el.textContent);
    if (t.indexOf('面片') >= 0) { ok = true; console.log('  升级后：' + t.slice(0, 70)); break; }
  }
  const sec = ((Date.now() - t0) / 1000).toFixed(1);
  console.log('  ' + (ok ? '✓' : '✗') + ' 线框自动升级为实体，用时 ' + sec + ' 秒');
  if (errs.length) console.log('  JS 报错: ' + errs.join(' | '));
  await p.screenshot({ path: 'upgrade_shot.png' });
  await b.close();
  process.exit(ok ? 0 : 1);
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
