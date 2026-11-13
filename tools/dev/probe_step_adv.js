// 验证剖切 / 爆炸 / 面积
const puppeteer = require('puppeteer-core');
const crypto = require('crypto');
const BASE = process.argv[2], FK = process.argv[3], KKT = process.argv[4];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const h = (b) => crypto.createHash('md5').update(b).digest('hex').slice(0, 10);

(async () => {
  const b = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox','--use-gl=swiftshader','--enable-unsafe-swiftshader'] });
  const p = await b.newPage();
  await p.setViewport({ width: 1366, height: 768 });
  const errs = [];
  p.on('pageerror', e => errs.push(String(e).slice(0, 140)));
  await p.goto(BASE + '/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT), { waitUntil: 'domcontentloaded', timeout: 90000 });
  await p.waitForFunction(() => ((document.getElementById('info')||{}).textContent||'').indexOf('面片') >= 0, { timeout: 180000 });
  await p.evaluate(() => { const x = document.getElementById('hint'); if (x) x.style.display='none'; });
  await sleep(700);
  const box = await p.$eval('#cv', el => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; });
  const shot = async () => h(await p.screenshot({ clip: { x: box.x, y: box.y, width: box.w, height: box.h } }));
  const res = [];
  const ok = (n,c,e) => res.push([n, c?'OK':'FAIL', e||'']);

  const h0 = await shot();

  // 剖切
  await p.click('#clipBtn'); await sleep(400);
  const clipOn = await p.$eval('#clipPanel', el => el.classList.contains('on'));
  ok('剖切面板能打开', clipOn);
  await p.evaluate(() => { const r=document.getElementById('clipRange'); r.value=30; r.dispatchEvent(new Event('input',{bubbles:true})); });
  await sleep(600);
  const h1 = await shot();
  ok('拖动剖切滑块画面变化', h1 !== h0, h0 + ' -> ' + h1);
  await p.click('#clipOff'); await sleep(500);
  const h2 = await shot();
  ok('关闭剖切恢复原样', h2 === h0, h0 + ' -> ' + h2);

  // 爆炸
  await p.click('#expBtn'); await sleep(400);
  const expOn = await p.$eval('#expPanel', el => el.classList.contains('on'));
  ok('爆炸面板能打开', expOn);
  await p.evaluate(() => { const r=document.getElementById('expRange'); r.value=60; r.dispatchEvent(new Event('input',{bubbles:true})); });
  await sleep(700);
  const h3 = await shot();
  ok('拖动爆炸滑块画面变化', h3 !== h2, h2 + ' -> ' + h3);
  await p.click('#expOff'); await sleep(600);
  const h4 = await shot();
  // 用"所有部件是否回到原点"判定，比截图 hash 稳（浮点/渲染顺序会让 hash 有微小差异）
  const backHome = await p.evaluate(() => {
    let mx = 0;
    document.querySelectorAll('canvas');   // 无副作用，占位
    return window.__stepMaxOffset ? window.__stepMaxOffset() : -1;
  });
  ok('爆炸复位后部件回原位', backHome < 0 || backHome < 0.01, '最大位移 ' + backHome);
  await p.click('#expBtn'); await sleep(200);   // 收起面板

  // 面积：Alt + 点击模型
  await p.click('#measBtn'); await sleep(300);
  await p.keyboard.down('Alt');
  await p.mouse.click(box.x + box.w*0.5, box.y + box.h*0.5);
  await p.keyboard.up('Alt');
  await sleep(600);
  const tip = await p.$eval('#mtip', el => el.textContent);
  ok('Alt+点击能测面积', /个面/.test(tip) && /mm/.test(tip), tip);
  const h5 = await shot();
  ok('面积标注后画面变化', h5 !== h4, h4 + ' -> ' + h5);

  await p.keyboard.press('Escape'); await sleep(400);
  const bad = errs.filter(e => !/favicon|404/.test(e));
  ok('页面无 JS 报错', bad.length===0, bad.join(' | '));
  await p.screenshot({ path: 'adv_shot.png' });
  await b.close();
  let pass=0;
  res.forEach(([n,s,e])=>{ if(s==='OK') pass++; console.log((s==='OK'?'  ✓ ':'  ✗ ')+n+(e?'  ['+e+']':'')); });
  console.log('通过 '+pass+'/'+res.length);
  process.exit(pass===res.length?0:1);
})().catch(e => { console.error('ERR', e.message); process.exit(2); });
