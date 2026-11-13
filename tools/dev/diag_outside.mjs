// 找出"画出来超出图面"的实体：按渲染时的实际几何采样，比较其包围盒
import fs from 'fs';
import { LibreDwg } from '@mlightcad/libredwg-web';

const lw = await LibreDwg.create();
const buf = fs.readFileSync(process.argv[2]);
const db = lw.dwg_read_data(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), 0);
const arr = Object.values(lw.convert(db).entities);

// 先按"所有点"算分位数当作图面范围（跟渲染器一致）
const px = [], py = [];
for (const e of arr) {
  if (e.startPoint) { px.push(e.startPoint.x); py.push(e.startPoint.y); }
  if (e.endPoint) { px.push(e.endPoint.x); py.push(e.endPoint.y); }
  if (!e.startPoint && e.center) { px.push(e.center.x); py.push(e.center.y); }
  if (e.vertices) for (const v of e.vertices) { px.push(v.x); py.push(v.y); }
  if (e.controlPoints) for (const v of e.controlPoints) { px.push(v.x); py.push(v.y); }
}
px.sort((a, b) => a - b); py.sort((a, b) => a - b);
const pct = (a, f) => a[Math.floor(a.length * f)];
const minX = pct(px, 0.03), maxX = pct(px, 0.97), minY = pct(py, 0.03), maxY = pct(py, 0.97);
console.log('图面范围 X %.0f..%.0f (%.0f)  Y %.0f..%.0f (%.0f)',
  minX, maxX, maxX - minX, minY, maxY, maxY - minY);
const span = Math.max(maxX - minX, maxY - minY);

// 每个实体画出来的点集
function pts(e) {
  switch (e.type) {
    case 'LINE': return e.startPoint && e.endPoint ? [e.startPoint, e.endPoint] : [];
    case 'ARC': {
      if (!e.center || !e.radius) return [];
      let sw = (e.endAngle || 0) - (e.startAngle || 0);
      while (sw <= 0) sw += Math.PI * 2;
      const out = [];
      for (let i = 0; i <= 12; i++) {
        const a = (e.startAngle || 0) + sw * i / 12;
        out.push({ x: e.center.x + e.radius * Math.cos(a), y: e.center.y + e.radius * Math.sin(a) });
      }
      return out;
    }
    case 'CIRCLE': {
      if (!e.center || !e.radius) return [];
      const out = [];
      for (let i = 0; i <= 12; i++) {
        const a = Math.PI * 2 * i / 12;
        out.push({ x: e.center.x + e.radius * Math.cos(a), y: e.center.y + e.radius * Math.sin(a) });
      }
      return out;
    }
    case 'LWPOLYLINE': case 'POLYLINE': case 'POLYLINE2D': return e.vertices || [];
    case 'SPLINE': return e.controlPoints || e.fitPoints || [];
    case 'ELLIPSE': {
      if (!e.center || !e.majorAxisEndPoint) return [];
      const rx = Math.hypot(e.majorAxisEndPoint.x, e.majorAxisEndPoint.y);
      const ry = rx * (e.axisRatio || 1);
      const out = [];
      for (let i = 0; i <= 12; i++) {
        const a = Math.PI * 2 * i / 12;
        out.push({ x: e.center.x + rx * Math.cos(a), y: e.center.y + ry * Math.sin(a) });
      }
      return out;
    }
    default: return [];
  }
}

const bad = {};
for (const e of arr) {
  const ps = pts(e);
  if (ps.length < 2) continue;
  const xs = ps.map(p => p.x), ys = ps.map(p => p.y);
  const ex0 = Math.min(...xs), ex1 = Math.max(...xs);
  const ey0 = Math.min(...ys), ey1 = Math.max(...ys);
  // 超出图面 30% 以上算"越界"
  if (ex1 < minX - span * 0.3 || ex0 > maxX + span * 0.3 ||
      ey1 < minY - span * 0.3 || ey0 > maxY + span * 0.3) {
    const k = e.type;
    if (!bad[k]) bad[k] = { n: 0, sample: [] };
    bad[k].n++;
    if (bad[k].sample.length < 3) {
      bad[k].sample.push({ r: e.radius, sw: e.type === 'ARC' ? (() => { let s = (e.endAngle||0)-(e.startAngle||0); while (s<=0) s+=Math.PI*2; return +(s*180/Math.PI).toFixed(1); })() : undefined,
                            x: +ex0.toFixed(0) + '..' + +ex1.toFixed(0), y: +ey0.toFixed(0) + '..' + +ey1.toFixed(0) });
    }
  }
}
console.log('\n越界实体统计:');
for (const [k, v] of Object.entries(bad)) {
  console.log('  ' + k + '  ' + v.n + ' 个');
  for (const s of v.sample) console.log('     ', JSON.stringify(s));
}
