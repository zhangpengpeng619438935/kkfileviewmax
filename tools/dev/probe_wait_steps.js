const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FILEURL = process.argv[3];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const api = async (p, b, t) => (await fetch(BASE + p, { method:'POST',
    headers:{'Content-Type':'application/json','X-App-Id':'kk-demo',...(t?{Authorization:'Bearer '+t}:{})},
    body: JSON.stringify(b) })).json();
  const tk = (await api('/api/v1/auth/token',{appId:'kk-demo',appSecret:'your-app-secret'})).data.accessToken;
  const d = (await api('/api/v1/preview/url',{fileUrl:FILEURL,empNo:'SYS_TEST'},tk)).data;
  console.log('  preview:', d.status);
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox','--use-gl=swiftshader','--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width:1100, height:760 });
  await p.goto(d.previewUrl, { waitUntil:'domcontentloaded', timeout:90000 });
  const seen = [];
  for (let i=0;i<45;i++){
    const t = await p.evaluate(() => {
      const s = document.querySelector('.steps');
      return (s ? s.innerText.replace(/\n+/g,' | ') : '(无阶段列表)') + ' @' + location.pathname.split('/')[1];
    });
    if (seen[seen.length-1] !== t) seen.push(t);
    if (t.indexOf('stepview') >= 0) break;
    await sleep(1200);
  }
  console.log('  阶段变化：');
  seen.slice(0,10).forEach(x => console.log('    ' + x));
  await p.screenshot({ path:'wait_steps.png' });
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
