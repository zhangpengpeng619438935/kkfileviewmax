// 预览加载时间线：逐资源拆解，找出时间花在哪
// 用法: node tools/speed_probe.js "<previewUrl>" <tag>
const puppeteer = require('puppeteer-core');
const url = process.argv[2];
const tag = process.argv[3] || 'x';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const ctx = await b.createBrowserContext();
  const p = await ctx.newPage();
  await p.setViewport({ width: 1600, height: 900 });
  await p.setCacheEnabled(true);

  const t0 = Date.now();
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
  const domReady = Date.now() - t0;

  // 首屏可见：canvas 画出来 / 大图出现
  let seen = 0;
  for (let i = 0; i < 300; i++) {
    const ok = await p.evaluate(() => {
      const cs = Array.from(document.querySelectorAll('canvas')).filter((c) => c.width > 100);
      const imgs = Array.from(document.querySelectorAll('img'))
        .filter((e) => e.naturalWidth > 200);
      return cs.length > 0 || imgs.length > 0;
    }).catch(() => false);
    if (ok) { seen = Date.now() - t0; break; }
    await sleep(50);
  }

  const res = await p.evaluate(() => performance.getEntriesByType('resource').map((r) => ({
    n: r.name.replace(/^https?:\/\/[^/]+/, '').replace(/\?.*$/, ''),
    ms: Math.round(r.duration),
    kb: Math.round((r.encodedBodySize || 0) / 1024),
    start: Math.round(r.startTime),
  })));
  res.sort((a, b) => b.ms - a.ms);
  const total = res.reduce((s, r) => s + r.kb, 0);
  console.log(`[${tag}] DOM ${domReady}ms  首屏可见 ${seen}ms  资源 ${res.length} 个 / ${total}KB`);
  console.log('最慢 8 个:');
  res.slice(0, 8).forEach((r) => console.log(`  ${String(r.ms).padStart(5)}ms ${String(r.kb).padStart(6)}KB @${r.start}ms  ${r.n.slice(-60)}`));

  // 再开一次，看强缓存是否生效（第一次 vs 第二次）
  const p2 = await ctx.newPage();
  await p2.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await sleep(1500);
  const res2 = await p2.evaluate(() => {
    const rs = performance.getEntriesByType('resource');
    const cached = rs.filter((r) => (r.transferSize || 0) === 0).length;
    return { n: rs.length, cached, total: Math.round(rs.reduce((s, r) => s + (r.encodedBodySize || 0), 0) / 1024) };
  });
  console.log(`[${tag}] 二次打开: ${res2.n} 个资源, 走缓存 ${res2.cached} 个`);
  await b.close();
})();
