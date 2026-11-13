// 测 stepview 页面：从 preview/url 到 #ld 隐藏（模型真正画出来）的端到端耗时
const puppeteer = require('puppeteer-core');
const BASE = 'http://127.0.0.1:9012';
const fileUrl = process.argv[2];
const label = process.argv[3] || 'step';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const tk = (await (await fetch(BASE + '/api/v1/auth/token', {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-App-Id': 'kk-demo'},
    body: JSON.stringify({appId: 'kk-demo', appSecret: 'your-app-secret'}),
  })).json()).data.accessToken;
  const t0 = Date.now();
  const d = (await (await fetch(BASE + '/api/v1/preview/url', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-App-Id': 'kk-demo', Authorization: 'Bearer ' + tk},
    body: JSON.stringify({fileUrl, empNo: 'EMP0001', empName: 'E', fileName: label}),
  })).json()).data;
  console.log('[1] preview/url ' + (Date.now() - t0) + 'ms status=' + (d && d.status));

  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox'],
  });
  const p = await b.newPage();
  await p.setViewport({width: 1400, height: 900});
  const sizes = [];
  p.on('response', async (r) => {
    if (/stepdata|three\.min|OrbitControls/.test(r.url())) {
      try {
        const len = (await r.buffer()).length;
        sizes.push(r.url().split('/').pop().slice(0, 26) + ' ' + (len / 1048576).toFixed(2) + 'MB');
      } catch (e) {}
    }
  });
  const t1 = Date.now();
  await p.goto(d.previewUrl, {waitUntil: 'domcontentloaded', timeout: 120000});
  console.log('[2] DOM ' + (Date.now() - t1) + 'ms 落点 ' + p.url().replace(BASE, '').slice(0, 60));

  let ok = false, txt = '';
  for (let i = 0; i < 600; i++) {
    const st = await p.evaluate(() => {
      const ld = document.getElementById('ld');
      const inf = document.getElementById('info');
      return {hide: ld ? ld.classList.contains('hide') : false,
              txt: inf ? inf.innerText : (document.body.innerText || '').slice(0, 40)};
    }).catch(() => ({hide: false, txt: ''}));
    if (st.hide) { ok = true; txt = st.txt; break; }
    txt = st.txt || txt;
    await sleep(100);
  }
  console.log('[3] 模型可见 ' + (Date.now() - t1) + 'ms  ' + txt);
  console.log('[T] 端到端 ' + (Date.now() - t0) + 'ms  可见=' + ok);
  console.log('[R] ' + sizes.join(' | '));
  await p.screenshot({path: 'step_' + label.replace(/\W/g, '_').slice(0, 20) + '.png'});
  await b.close();
})();
