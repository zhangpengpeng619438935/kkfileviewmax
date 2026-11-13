// 点开"告警记录"页并截图，检查卡片/图表/表格是否都渲染出来
const puppeteer = require('puppeteer-core');
const url = process.argv[2];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const p = await b.newPage();
  await p.setViewport({ width: 1440, height: 1000 });
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e).slice(0, 160)));
  p.on('console', (m) => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 160)); });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sleep(2500);
  await p.evaluate(() => {
    const t = document.querySelector('.tab[data-view="alerts"]');
    if (t) t.click();
  });
  await sleep(3500);
  const info = await p.evaluate(() => ({
    cards: document.querySelectorAll('#alertCards .card').length,
    rows: document.querySelectorAll('#tableAlerts tbody tr').length,
    cols: document.querySelectorAll('#tableAlerts thead th').length,
    hint: (document.getElementById('alertHint') || {}).textContent || '',
    pageInfo: (document.getElementById('aPageInfo') || {}).textContent || '',
    charts: [document.getElementById('chartAlertDay').querySelectorAll('canvas').length,
             document.getElementById('chartAlertKind').querySelectorAll('canvas').length],
    firstCard: (document.querySelector('#alertCards .card .value') || {}).textContent || '',
  }));
  console.log(JSON.stringify(info));
  if (errs.length) console.log('错误: ' + errs.slice(0, 4).join(' | '));
  await p.screenshot({ path: 'alerts_view.png' });
  // 滚到表格
  await p.evaluate(() => { const el = document.getElementById('tableAlerts'); el && el.scrollIntoView({ block: 'center' }); });
  await sleep(800);
  await p.screenshot({ path: 'alerts_table.png' });
  await b.close();
})();
