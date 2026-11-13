// 连开两次同一页，看数据接口第二次是否走缓存
const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4], PATH = process.argv[5];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox','--use-gl=swiftshader','--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width:1200, height:800 });
  const hits = [];
  p.on('response', async r => {
    if (r.url().indexOf(PATH) >= 0) {
      hits.push({ status: r.status(), fromCache: r.fromCache(), bytes: r.headers()['content-length'] || 0 });
    }
  });
  p.on('requestfailed', r => { if (r.url().indexOf(PATH) >= 0) console.log('  FAILED', r.failure().errorText); });
  p.on('pageerror', e => console.log('  PAGEERR', String(e).slice(0,120)));
  const url = BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT);
  for (let round = 1; round <= 2; round++) {
    hits.length = 0;
    await p.goto(url, { waitUntil:'domcontentloaded', timeout:90000 });
    await p.waitForFunction(() => {
      const t = (document.getElementById('info')||{}).textContent||'';
      return t.indexOf('面片') >= 0 || t.indexOf('线框') >= 0;
    }, { timeout: 180000 }).catch(()=>{});
    await sleep(1500);
    const h = hits[hits.length-1] || {};
    console.log('  第' + round + '次: status=' + h.status + ' 来自缓存=' + h.fromCache +
                ' content-length=' + (h.bytes ? (h.bytes/1048576).toFixed(2)+'MB' : '0'));
  }
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
