// 端到端：打开一个 XFD 列数的 BOM 表，确认落点是 Univer 表格（不是 PDF）、首屏有多快
const puppeteer = require('puppeteer-core');
const BASE = process.argv[2] || 'http://127.0.0.1:9012';
const KW = process.argv[3] || 'Z HUD BOM AR006C';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  // 第三个参数可以直接给文件地址（热点列表不一定包含它）
  let hit;
  if (/^https?:/.test(KW)) {
    hit = { file_url: KW, file_name: KW.split('/').pop().slice(0, 40) };
  } else {
    const q = await fetch(BASE + '/api/v1/stats/files?top=500').then(r => r.json());
    hit = (q.data || []).find(f => (f.file_name || '').indexOf(KW) >= 0);
  }
  if (!hit) { console.log('找不到文件: ' + KW); process.exit(1); }

  const api = async (path, body, token) => (await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-App-Id': 'kk-demo',
      ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    body: JSON.stringify(body) })).json();

  const tk = (await api('/api/v1/auth/token',
    { appId: 'kk-demo', appSecret: 'your-app-secret' })).data.accessToken;
  const d = (await api('/api/v1/preview/url', { fileUrl: hit.file_url, empNo: 'SYS_PREWARM' }, tk)).data;
  console.log('文件: ' + (hit.file_name || '').slice(0, 46));
  console.log('状态: ' + d.status + '  ' + d.fileType);

  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1440, height: 900 });
  const errs = [];
  p.on('pageerror', e => errs.push(String(e).slice(0, 120)));
  const t0 = Date.now();
  await p.goto(d.previewUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sleep(6000);                       // 等 Univer 画出来
  const info = await p.evaluate(() => ({
    url: location.pathname,
    canvases: document.querySelectorAll('canvas').length,
    pdfViewer: !!document.querySelector('#viewer, .pdfViewer, #pdfContainer'),
    info: ((document.getElementById('info') || {}).textContent || '').slice(0, 90),
    ld: ((document.getElementById('ld') || {}).textContent || '').slice(0, 60),
    title: document.title.slice(0, 40),
  }));
  console.log('落点: ' + info.url + '  画布 ' + info.canvases + ' 个'
    + (info.pdfViewer ? '  ← 是 PDF 查看器' : '  ← 不是 PDF'));
  console.log('信息栏: ' + info.info);
  console.log('加载层: ' + info.ld);
  console.log('首屏耗时(含固定等待): ' + ((Date.now() - t0) / 1000).toFixed(1) + 's');
  console.log(errs.length ? '页面错误: ' + errs.slice(0, 2).join(' | ') : '无页面错误');
  await p.screenshot({ path: 'bom_univer.png' });
  await b.close();
})();
