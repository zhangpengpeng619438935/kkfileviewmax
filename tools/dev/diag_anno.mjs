// 查 MULTILEADER / DIMENSION / LEADER / TOLERANCE 的可用字段
import fs from 'fs';
import { LibreDwg } from '@mlightcad/libredwg-web';

const lw = await LibreDwg.create();
const buf = fs.readFileSync(process.argv[2]);
const db = lw.dwg_read_data(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), 0);
const arr = Object.values(lw.convert(db).entities);

for (const t of ['MULTILEADER', 'DIMENSION', 'LEADER', 'TOLERANCE', 'SOLID']) {
  const list = arr.filter(e => e.type === t);
  if (!list.length) { console.log(t, '无'); continue; }
  const e = list[0];
  console.log('\n=== ' + t + ' (' + list.length + ' 个) 字段:');
  console.log(Object.keys(e).join(','));
  for (const k of Object.keys(e)) {
    if (/^(type|layer|color|lineType|lineweight|lineTypeScale|isVisible|transparency|transparencyType|xdata|handle|owner)/.test(k)) continue;
    const v = e[k];
    const s = v && typeof v === 'object' ? JSON.stringify(v) : String(v);
    if (s && s.length > 1) console.log('   ' + k + ' = ' + s.slice(0, 220));
  }
}
