// 诊断 ARC 的角度单位、半径量级，以及 SPLINE 的 knots/控制点是否匹配
import fs from 'fs';
import { LibreDwg } from '@mlightcad/libredwg-web';

const lw = await LibreDwg.create();
const buf = fs.readFileSync(process.argv[2]);
const db = lw.dwg_read_data(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), 0);
const arr = Object.values(lw.convert(db).entities);

const arcs = arr.filter(e => e.type === 'ARC');
console.log('ARC 总数', arcs.length);
for (const e of arcs.slice(0, 6)) {
  console.log('  center', JSON.stringify(e.center), 'r', e.radius,
              'a0', e.startAngle, 'a1', e.endAngle);
}
// 角度绝对值统计：如果全是 <= 2π 就是弧度；大量 > 6.28 就是度
const angs = arcs.flatMap(e => [e.startAngle, e.endAngle]).filter(v => typeof v === 'number');
angs.sort((a, b) => a - b);
const q = (f) => angs[Math.floor(angs.length * f)];
console.log('角度分位: min', angs[0]?.toFixed(3), '50%', q(0.5)?.toFixed(3),
            '99%', q(0.99)?.toFixed(3), 'max', angs[angs.length - 1]?.toFixed(3));
console.log('  超过 2π 的占比', (angs.filter(v => Math.abs(v) > 6.2832).length / angs.length * 100).toFixed(1) + '%');
console.log('  超过 360 的占比', (angs.filter(v => Math.abs(v) > 360).length / angs.length * 100).toFixed(1) + '%');

const rads = arcs.map(e => e.radius).filter(v => typeof v === 'number').sort((a, b) => a - b);
console.log('半径分位: 50%', rads[Math.floor(rads.length * 0.5)]?.toFixed(2),
            '99%', rads[Math.floor(rads.length * 0.99)]?.toFixed(2),
            'max', rads[rads.length - 1]?.toFixed(2));

// SPLINE
const sp = arr.filter(e => e.type === 'SPLINE');
console.log('\nSPLINE 总数', sp.length);
let bad = 0, fitOnly = 0;
for (const e of sp) {
  const ncp = (e.controlPoints || []).length, nk = (e.knots || []).length, deg = e.degree || 3;
  if (ncp < deg + 1 || nk < ncp + deg + 1) { bad++; if ((e.fitPoints || []).length) fitOnly++; }
}
console.log('  knots 与控制点数量不匹配（走 fallback）:', bad, '其中靠 fitPoints 兜底:', fitOnly);
for (const e of sp.slice(0, 3)) {
  console.log('  deg', e.degree, 'cp', (e.controlPoints || []).length,
              'knots', (e.knots || []).length, 'fit', (e.fitPoints || []).length);
}
