// 验证"切后台"标记的页面脚本：打开一个真实预览页，看是否注入成功、有无 JS 报错
const puppeteer = require('puppeteer-core');
const BASE = process.argv[2] || 'http://127.0.0.1:9012';
const SRC = 'http://127.0.0.1:8899/real.pdf';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  const api = async (path, body, token) => {
    const r = await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-App-Id': 'kk-demo',
        ...(token ? { Authorization: 'Bearer ' + token } : {}) },
      body: JSON.stringify(body),
    });
    return r.json();
  };
  const tk = (await api('/api/v1/auth/token',
    { appId: 'kk-demo', appSecret: 'your-app-secret' })).data.accessToken;
  const d = (await api('/api/v1/preview/url',
    { fileUrl: SRC, empNo: 'SYS_PREWARM', waitSeconds: 20 }, tk)).data;
  if (!d) { console.log('接口返回异常'); process.exit(1); }
  console.log('预览地址: ' + d.status + ' ' + (d.previewUrl || '').slice(0, 70));

  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const p = await b.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push('pageerror: ' + String(e).slice(0, 160)));
  p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 160)); });
  await p.goto(d.previewUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sleep(4000);
  const info = await p.evaluate(() => ({
    hasBgFlag: document.documentElement.innerHTML.indexOf('bg:HID?1:0') >= 0,
    hasVis: document.documentElement.innerHTML.indexOf('visibilitychange') >= 0,
    loaded: document.querySelectorAll('canvas, embed, object, img').length,
  }));
  console.log('注入检查: ' + JSON.stringify(info));
  console.log(errs.length ? '页面错误: ' + errs.slice(0, 3).join(' | ') : '无页面错误');
  await b.close();
})();
