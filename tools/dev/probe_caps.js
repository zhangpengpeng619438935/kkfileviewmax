// 判断这个无头浏览器到底有没有启用 HTTP 缓存（用 /health 做对照）
const puppeteer = require('puppeteer-core');
const BASE = process.argv[2];
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox'] });
  const p = await b.newPage();
  await p.setCacheEnabled(true);
  await p.goto(BASE + '/health', { waitUntil:'domcontentloaded' });
  const r = await p.evaluate(async () => {
    const one = async () => { const t0 = performance.now(); await (await fetch('/health')).text(); return Math.round(performance.now()-t0); };
    const a = await one(), c = await one();
    const es = performance.getEntriesByType('resource').filter(e => e.name.indexOf('/health') >= 0);
    return { first: a, second: c, transfer: es.map(e => e.transferSize) };
  });
  console.log('  /health 第一次 ' + r.first + 'ms  第二次 ' + r.second + 'ms  transferSize=' + JSON.stringify(r.transfer));
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
