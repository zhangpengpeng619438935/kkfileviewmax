// 服务告警页 + 预览失败页渲染检查（卡片/图/表）
const puppeteer = require('puppeteer-core');
const url = process.argv[2] || 'http://127.0.0.1:9012/';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const p = await b.newPage();
  await p.setViewport({ width: 1440, height: 1100 });
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e).slice(0, 200)));
  p.on('console', (m) => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sleep(2500);

  // --- 服务告警
  await p.evaluate(() => {
    const t = document.querySelector('.tab[data-view="alerts"]');
    if (t) t.click();
  });
  await sleep(3500);
  const sa = await p.evaluate(() => ({
    cards: document.querySelectorAll('#saCards .card').length,
    cardValues: Array.from(document.querySelectorAll('#saCards .card .value')).map(e => e.textContent.trim()),
    rows: document.querySelectorAll('#tableSa tbody tr').length,
    cols: document.querySelectorAll('#tableSa thead th').length,
    headers: Array.from(document.querySelectorAll('#tableSa thead th')).map(e => e.textContent.trim()),
    kinds: Array.from(document.querySelectorAll('#saKind option')).map(o => o.textContent),
    firstRow: Array.from(document.querySelectorAll('#tableSa tbody tr:first-child td')).map(e => e.textContent.trim()),
    status: document.querySelectorAll('#tableSa .resTag').length,
    charts: ['chartSaDay', 'chartSaKind'].map(id => (document.getElementById(id) || {}).querySelectorAll
      ? document.getElementById(id).querySelectorAll('canvas').length : -1),
    hint: (document.getElementById('saHint') || {}).textContent || '',
  }));
  console.log('服务告警: ' + JSON.stringify(sa, null, 1));
  await p.screenshot({ path: 'sa_top.png' });
  await p.evaluate(() => { const el = document.getElementById('tableSa'); el && el.scrollIntoView({ block: 'center' }); });
  await sleep(800);
  await p.screenshot({ path: 'sa_table.png' });

  // --- 预览失败
  await p.evaluate(() => {
    const t = document.querySelector('.tab[data-view="failures"]');
    if (t) t.click();
  });
  await sleep(3000);
  const fl = await p.evaluate(() => ({
    cards: document.querySelectorAll('#alertCards .card').length,
    rows: document.querySelectorAll('#tableAlerts tbody tr').length,
    firstRow: Array.from(document.querySelectorAll('#tableAlerts tbody tr:first-child td')).map(e => e.textContent.trim()),
    charts: ['chartAlertDay', 'chartAlertKind'].map(id => document.getElementById(id).querySelectorAll('canvas').length),
  }));
  console.log('预览失败: ' + JSON.stringify(fl, null, 1));
  await p.screenshot({ path: 'fail_view.png' });

  if (errs.length) console.log('页面错误: ' + errs.slice(0, 5).join(' | '));
  else console.log('无页面错误');
  await b.close();
})();
