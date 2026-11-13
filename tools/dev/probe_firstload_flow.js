// 首次加载全流程：接口 → 等待页 → worker 补网格 → 自动跳 3D 页并出图。
// 用法: node probe_firstload_flow.js <fileUrl>
const puppeteer = require('puppeteer-core');
const BASE = 'http://127.0.0.1:9012';
const FILE_URL = process.argv[2];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  const api = async (path, body, tok) => (await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-App-Id': 'kk-demo',
      ...(tok ? { Authorization: 'Bearer ' + tok } : {}) },
    body: JSON.stringify(body) })).json();
  const tk = (await api('/api/v1/auth/token',
    { appId: 'kk-demo', appSecret: 'your-app-secret' })).data.accessToken;
  const t0 = Date.now();
  const d = (await api('/api/v1/preview/url', { fileUrl: FILE_URL, empNo: 'SYS_TEST' }, tk)).data;
  console.log('  接口返回 ' + (Date.now() - t0) + 'ms，页面: ' + (d.previewUrl || '').replace(/\?.*/, '?…'));

  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage',
      '--use-gl=swiftshader', '--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1280, height: 800 });
  const errs = [];
  p.on('pageerror', e => errs.push(String(e).slice(0, 120)));
  await p.goto(d.previewUrl, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await sleep(600);
  console.log('  落地页: ' + p.url().replace(/\?.*/, '?…'));

  let waited = false, ok = false;
  for (let i = 0; i < 180; i++) {          // 最多等 180 秒
    const u = p.url();
    if (u.indexOf('/stepwait/') >= 0) waited = true;
    const info = await p.evaluate(() => {
      const a = document.getElementById('info'), m = document.getElementById('msg');
      return (a ? a.textContent : '') + '|' + (m ? m.textContent.replace(/\s+/g, ' ') : '');
    });
    if (info.indexOf('面片') >= 0 || info.indexOf('线框') >= 0) { ok = true; break; }
    if (info.indexOf('解析失败') >= 0) { break; }
    await sleep(1000);
  }
  const sec = ((Date.now() - t0) / 1000).toFixed(1);
  const finalInfo = await p.evaluate(() => {
    const a = document.getElementById('info');
    return a ? a.textContent : '(无)';
  });
  console.log('  经过等待页: ' + (waited ? '是' : '否'));
  console.log('  最终地址: ' + p.url().replace(/\?.*/, '?…'));
  console.log('  用时 ' + sec + ' 秒，信息栏: ' + finalInfo);
  if (errs.length) console.log('  JS 报错: ' + errs.join(' | '));
  await p.screenshot({ path: 'firstload_flow.png' });
  await b.close();
  console.log(ok ? '  ✓ 首次加载成功' : '  ✗ 未出图');
  process.exit(ok ? 0 : 1);
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
