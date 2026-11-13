// 查 POLYLINE2D / MTEXT / DIMENSION 的字段结构
import fs from 'fs';
import { LibreDwg } from '@mlightcad/libredwg-web';

const lw = await LibreDwg.create();
const b = fs.readFileSync(process.argv[2] || 't.dwg');
const db = lw.dwg_read_data(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength), 0);
const arr = Object.values(lw.convert(db).entities);

for (const want of ['POLYLINE2D', 'MTEXT', 'DIMENSION']) {
  const e = arr.find(x => x.type === want);
  if (!e) { console.log('---', want, '无'); continue; }
  console.log('---', want, '字段:', Object.keys(e).filter(k => !/xdata|owner|handle/i.test(k)).join(','));
  const out = {};
  for (const k of Object.keys(e)) {
    if (/xdata|owner|handle/i.test(k)) continue;
    const v = e[k];
    if (v && typeof v === 'object') out[k] = Array.isArray(v) ? `[${v.length}]` : JSON.stringify(v).slice(0, 60);
    else out[k] = v;
  }
  console.log('   ', JSON.stringify(out).slice(0, 460));
}
