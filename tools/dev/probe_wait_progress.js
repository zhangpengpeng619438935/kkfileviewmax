const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FILEURL = process.argv[3];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const api = async (p, b, t) => (await fetch(BASE + p, { method:'POST',
    headers:{'Content-Type':'application/json','X-App-Id':'kk-demo',...(t?{Authorization:'Bearer '+t}:{})},
    body: JSON.stringify(b) })).json();
  const tk = (await api('/api/v1/auth/token',{appId:'kk-demo',appSecret:'your-app-secret'})).data.accessToken;
  const d = (await api('/api/v1/preview/url',{fileUrl:FILEURL,empNo:'SYS_TEST'},tk)).data;
  console.log('  preview 返回:', d.status);
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox','--use-gl=swiftshader','--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width:1200, height:800 });
  await p.goto(d.previewUrl, { waitUntil:'domcontentloaded', timeout:90000 });
  const seen = [];
  for (let i=0;i<50;i++){
    const t = await p.evaluate(() => {
      const bar = document.getElementById('pb'), txt = document.getElementById('pt');
      const vis = bar && bar.style.display !== 'none';
      return vis ? (txt ? txt.textContent : '(条)') : '';
    });
    if (t && seen[seen.length-1] !== t) seen.push(t);
    const u = p.url();
    if (u.indexOf('/stepview/') >= 0) { console.log('  → 已跳转 3D 页'); break; }
    await sleep(1200);
  }
  console.log('  进度条文字变化：');
  seen.slice(0,10).forEach(x => console.log('    ' + x));
  await p.screenshot({ path:'wait_progress.png' });
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
