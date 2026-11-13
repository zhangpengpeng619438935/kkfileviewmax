// 找出跨度过大的实体（会画出横穿整张图的粗线）
import fs from 'fs';
import { LibreDwg } from '@mlightcad/libredwg-web';

const file = process.argv[2] || 't.dwg';
const lw = await LibreDwg.create();
const b = fs.readFileSync(file);
const db = lw.dwg_read_data(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength), 0);
const arr = Object.values(lw.convert(db).entities);

function span(pts) {
  const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
  return Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
}

const big = {};
const samples = {};
for (const e of arr) {
  let pts = [];
  if (e.startPoint && e.endPoint) pts = [e.startPoint, e.endPoint];
  else if (e.center) pts = [{ x: e.center.x - (e.radius || 0), y: e.center.y - (e.radius || 0) },
                             { x: e.center.x + (e.radius || 0), y: e.center.y + (e.radius || 0) }];
  else if (e.vertices) pts = e.vertices;
  else if (e.controlPoints) pts = e.controlPoints;
  else if (e.fitPoints) pts = e.fitPoints;
  if (pts.length < 2) continue;
  const s = span(pts);
  if (s > 2000) {
    big[e.type] = (big[e.type] || 0) + 1;
    if (!samples[e.type]) samples[e.type] = [];
    if (samples[e.type].length < 2) samples[e.type].push(e);
  }
}
console.log('跨度>2000 的实体统计:', JSON.stringify(big));

for (const [t, es] of Object.entries(samples)) {
  for (const e of es) {
    if (t === 'SPLINE') {
      const cp = e.controlPoints || [];
      console.log(`SPLINE 控制点=${cp.length} knots=${(e.knots||[]).length} degree=${e.degree} ` +
                  `fit=${(e.fitPoints||[]).length} span=${span(cp).toFixed(0)}`);
      if (cp.length) console.log('   首尾点', JSON.stringify(cp[0]), JSON.stringify(cp[cp.length-1]));
    } else if (t === 'ELLIPSE') {
      console.log(`ELLIPSE center=${JSON.stringify(e.center)} major=${JSON.stringify(e.majorAxisEndPoint)} ratio=${e.axisRatio}`);
    } else if (t === 'ARC') {
      console.log(`ARC center=${JSON.stringify(e.center)} r=${e.radius}`);
    } else if (t === 'LWPOLYLINE' || t === 'POLYLINE') {
      const vs = e.vertices || [];
      console.log(`${t} 顶点=${vs.length} span=${span(vs).toFixed(0)} 首=${JSON.stringify(vs[0])}`);
    }
  }
}
