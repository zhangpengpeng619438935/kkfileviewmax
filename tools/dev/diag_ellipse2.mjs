// 查 ELLIPSE 字段：majorAxisEndPoint 是绝对点还是相对矢量？有没有 start/end 参数？
import fs from 'fs';
import { LibreDwg } from '@mlightcad/libredwg-web';

const lw = await LibreDwg.create();
const buf = fs.readFileSync(process.argv[2]);
const db = lw.dwg_read_data(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), 0);
const arr = Object.values(lw.convert(db).entities);
const es = arr.filter(e => e.type === 'ELLIPSE');
console.log('ELLIPSE 总数', es.length);
console.log('字段:', Object.keys(es[0]).join(','));
for (const e of es.slice(0, 5)) {
  console.log('  center', JSON.stringify(e.center));
  console.log('    major', JSON.stringify(e.majorAxisEndPoint),
              'ratio', e.axisRatio, 'a0', e.startAngle, 'a1', e.endAngle);
}
// 统计：矢量长度 vs 绝对点长度
const lens = [], absl = [];
for (const e of es) {
  if (!e.center || !e.majorAxisEndPoint) continue;
  lens.push(Math.hypot(e.majorAxisEndPoint.x, e.majorAxisEndPoint.y));
  absl.push(Math.hypot(e.majorAxisEndPoint.x - e.center.x, e.majorAxisEndPoint.y - e.center.y));
}
const q = (a, f) => { const s = [...a].sort((p, r) => p - r); return s[Math.floor(s.length * f)]; };
console.log('\n按矢量模长:  50%', q(lens, .5)?.toFixed(2), '99%', q(lens, .99)?.toFixed(2), 'max', Math.max(...lens).toFixed(2));
console.log('按绝对点距离: 50%', q(absl, .5)?.toFixed(2), '99%', q(absl, .99)?.toFixed(2), 'max', Math.max(...absl).toFixed(2));
const ratio = es.map(e => e.axisRatio).filter(v => typeof v === 'number');
console.log('axisRatio 范围', Math.min(...ratio).toFixed(3), '..', Math.max(...ratio).toFixed(3));
const sw = es.map(e => { let s = (e.endAngle || 0) - (e.startAngle || 0); while (s < 0) s += Math.PI * 2; return s; })
             .filter(v => v > 0.0001);
console.log('扫掠角 >0 的椭圆比例', (sw.length / es.length * 100).toFixed(1) + '%');
