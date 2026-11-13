// 检查 ELLIPSE / ARC 的几何字段是绝对坐标还是矢量
import fs from 'fs';
import { LibreDwg } from '@mlightcad/libredwg-web';

const lw = await LibreDwg.create();
const b = fs.readFileSync(process.argv[2] || 't.dwg');
const db = lw.dwg_read_data(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength), 0);
const arr = Object.values(lw.convert(db).entities);

const ell = arr.filter(e => e.type === 'ELLIPSE').slice(0, 4);
console.log('ELLIPSE 样例:');
for (const e of ell) {
  console.log('  center', JSON.stringify(e.center),
              'major', JSON.stringify(e.majorAxisEndPoint),
              'ratio', e.axisRatio, 'start/end', e.startAngle, e.endAngle);
}
const arc = arr.filter(e => e.type === 'ARC').slice(0, 3);
console.log('ARC 样例:');
for (const e of arc) {
  console.log('  center', JSON.stringify(e.center), 'r', e.radius,
              'angles', e.startAngle, e.endAngle);
}
