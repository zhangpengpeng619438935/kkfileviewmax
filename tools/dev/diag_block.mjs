// 查 MTEXT 文本字段、INSERT 块引用可否展开
import fs from 'fs';
import { LibreDwg } from '@mlightcad/libredwg-web';

const lw = await LibreDwg.create();
const buf = fs.readFileSync(process.argv[2]);
const db = lw.dwg_read_data(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), 0);
const c = lw.convert(db);
const arr = Object.values(c.entities || {});

const mt = arr.filter(e => e.type === 'MTEXT');
console.log('MTEXT', mt.length);
for (const e of mt.slice(0, 4)) {
  const o = {};
  for (const k of Object.keys(e)) {
    if (/^(type|layer|color|lineType|lineweight|lineTypeScale|isVisible|transparency|xdata|handle|owner|subclass)/.test(k)) continue;
    const v = e[k];
    if (v === undefined || v === null || v === '') continue;
    o[k] = v;
  }
  console.log('  ' + JSON.stringify(o).slice(0, 320));
}

const ins = arr.filter(e => e.type === 'INSERT');
console.log('\nINSERT', ins.length);
for (const e of ins.slice(0, 3)) {
  const o = {};
  for (const k of Object.keys(e)) {
    if (/^(type|layer|color|lineType|lineweight|lineTypeScale|isVisible|transparency|xdata|handle|owner|subclass)/.test(k)) continue;
    const v = e[k];
    if (v === undefined || v === null || v === '') continue;
    o[k] = v;
  }
  console.log('  ' + JSON.stringify(o).slice(0, 320));
}

console.log('\n顶层键:', Object.keys(c).join(','));
if (c.blocks) {
  console.log('blocks 类型:', Array.isArray(c.blocks) ? 'array' : typeof c.blocks);
  const ks = Object.keys(c.blocks);
  console.log('blocks 数量:', ks.length, '前几个:', ks.slice(0, 6).join(','));
  const first = c.blocks[ks[0]];
  console.log('样例结构:', JSON.stringify(first).slice(0, 300));
}
