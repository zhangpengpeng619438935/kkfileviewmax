// 完整实体类型分布（含文字/标注/块引用）
import fs from 'fs';
import { LibreDwg } from '@mlightcad/libredwg-web';

const lw = await LibreDwg.create();
const b = fs.readFileSync(process.argv[2] || 't.dwg');
const db = lw.dwg_read_data(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength), 0);
const c = lw.convert(db);
const arr = Object.values(c.entities);
const t = {};
for (const e of arr) t[e.type] = (t[e.type] || 0) + 1;
const sorted = Object.entries(t).sort((a, b) => b[1] - a[1]);
console.log('全部类型:', sorted.map(x => x[0] + '=' + x[1]).join(' '));

// 看看有没有块引用 / 标注 / 文字，以及他们的关键字段
for (const want of ['TEXT', 'MTEXT', 'DIMENSION', 'INSERT', 'ATTRIB']) {
  const e = arr.find(x => x.type === want);
  if (!e) { console.log(want, '无'); continue; }
  const keys = Object.keys(e).filter(k => !/xdata|owner|handle$/i.test(k));
  console.log(want, '字段:', keys.slice(0, 12).join(','));
  const sample = {};
  for (const k of ['text', 'textValue', 'insertPoint', 'name', 'blockName', 'startPoint', 'endPoint', 'rotation', 'height']) {
    if (e[k] !== undefined) sample[k] = e[k];
  }
  console.log('   ', JSON.stringify(sample).slice(0, 200));
}
