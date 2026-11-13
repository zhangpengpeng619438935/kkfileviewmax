const puppeteer = require('puppeteer-core');
(async () => {
  const b = await puppeteer.launch({ executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless:true, args:['--no-sandbox'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1500, height: 1000, deviceScaleFactor: 1 });
  await p.goto('file:///' + process.argv[2].replace(/\\/g,'/'), { waitUntil:'load', timeout:60000 });
  await new Promise(r => setTimeout(r, 800));
  await p.screenshot({ path: process.argv[3] });
  console.log('saved', process.argv[3]);
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
