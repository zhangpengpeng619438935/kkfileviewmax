const puppeteer = require('puppeteer-core');
const BASE = process.argv[2], FILEURL = process.argv[3];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const api = async (p, b, t) => (await fetch(BASE + p, { method:'POST',
    headers:{'Content-Type':'application/json','X-App-Id':'kk-demo',...(t?{Authorization:'Bearer '+t}:{})},
    body: JSON.stringify(b) })).json();
  const tk = (await api('/api/v1/auth/token',{appId:'kk-demo',appSecret:'your-app-secret'})).data.accessToken;
  const d = (await api('/api/v1/preview/url',{fileUrl:FILEURL,empNo:'SYS_TEST'},tk)).data;
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox'] });
  const p = await b.newPage();
  await p.setViewport({ width:1400, height:900 });
  await p.goto(d.previewUrl, { waitUntil:'domcontentloaded', timeout:90000 });
  await sleep(3000);
  console.log('  落地页:', p.url().replace(/\?.*/,''));
  await p.waitForFunction(() => {
    const ld = document.getElementById('ld');
    return ld && ld.className.indexOf('hide') >= 0;
  }, { timeout: 60000 }).catch(() => console.log('  (等加载超时)'));
  await sleep(1000);
  await p.screenshot({ path:'cadv_final.png' });
  console.log('  信息栏:', await p.$eval('#info', el => el.textContent));
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
