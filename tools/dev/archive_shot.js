// 压缩包清单页 + 点开内部文件的截图
// 用法: node tools/archive_shot.js "<previewUrl>" "<条目名>" <tag>
const puppeteer = require('puppeteer-core');
const url = process.argv[2];
const target = process.argv[3] || '';
const tag = process.argv[4] || 'arc';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const b = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const p = await b.newPage();
  await p.setViewport({ width: 1400, height: 900 });
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e).slice(0, 120)));
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await sleep(1500);
  const info = await p.evaluate(() => ({
    title: document.title,
    rows: document.querySelectorAll('#tb tr').length,
    links: Array.from(document.querySelectorAll('a.f')).slice(0, 3)
      .map((a) => decodeURIComponent((a.href.split('p=')[1] || '').split('&')[0])),
    hasSearch: !!document.getElementById('q'),
  }));
  console.log('清单页: ' + JSON.stringify(info));
  await p.screenshot({ path: 'arc_' + tag + '_list.png' });

  if (target) {
    const ok = await p.evaluate((t) => {
      const a = Array.from(document.querySelectorAll('a.f'))
        .find((x) => decodeURIComponent(x.href).includes(t));
      if (!a) return false;
      a.click();
      return true;
    }, target);
    console.log('点击 ' + target + ' -> ' + ok);
    await sleep(2500);
    await p.screenshot({ path: 'arc_' + tag + '_view.png' });
    console.log('落点: ' + p.url().replace(/^https?:\/\/[^/]+/, '').slice(0, 70));
  }
  if (errs.length) console.log('错误: ' + errs.slice(0, 3).join(' | '));
  await b.close();
})();
