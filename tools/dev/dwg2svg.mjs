// DWG → SVG：按图层/ACI 颜色画矢量图，供 2D 预览使用（内部工具，GPL-3.0 的 libredwg WASM）
import fs from 'fs';
import { LibreDwg } from '@mlightcad/libredwg-web';

// AutoCAD 索引色（ACI）1~9 是固定色，其余按色轮近似
const ACI = { 1:'#ff0000', 2:'#ffff00', 3:'#00ff00', 4:'#00ffff', 5:'#0000ff',
              6:'#ff00ff', 7:'#000000', 8:'#808080', 9:'#c0c0c0' };
function aci(i) {
  if (ACI[i]) return ACI[i];
  if (i >= 250) return '#404040';
  const h = ((i - 10) % 240) * 1.5, s = 1, l = 0.5;   // 近似色轮
  const c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs((h / 60) % 2 - 1)), m = l - c / 2;
  let r = 0, g = 0, b = 0;
  if (h < 60) [r, g, b] = [c, x, 0]; else if (h < 120) [r, g, b] = [x, c, 0];
  else if (h < 180) [r, g, b] = [0, c, x]; else if (h < 240) [r, g, b] = [0, x, c];
  else if (h < 300) [r, g, b] = [x, 0, c]; else [r, g, b] = [c, 0, x];
  const hx = v => Math.round((v + m) * 255).toString(16).padStart(2, '0');
  return '#' + hx(r) + hx(g) + hx(b);
}
const n = v => (Math.round(v * 100) / 100);

function arcPath(cx, cy, r, a0, a1, yNeg) {
  // a0/a1 是弧度；SVG 的 y 轴向下，所以角度取反
  let sweep = a1 - a0;
  while (sweep <= 0) sweep += Math.PI * 2;
  const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0) * yNeg;
  const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1) * yNeg;
  const large = sweep > Math.PI ? 1 : 0;
  const sweepFlag = yNeg > 0 ? 0 : 1;
  return `M${n(x0)} ${n(y0)}A${n(r)} ${n(r)} 0 ${large} ${sweepFlag} ${n(x1)} ${n(y1)}`;
}

export async function dwgToSvg(file, opts = {}) {
  const lw = await LibreDwg.create();
  const buf = fs.readFileSync(file);
  const db = lw.dwg_read_data(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), 0);
  const c = lw.convert(db);
  const ents = Object.values(c.entities || {});

  /* 取景用分位数而不是 min/max：工业图纸里常有超出范围的构造线、巨大半径的
     辅助圆，用极值会把 viewBox 撑到十几米，真正的图缩成角落里一小团（实测
     bbox 被撑到 17480×12080，而 95% 的线都在 x<10515 / y<4217 的范围内）。 */
  const px = [], py = [];
  for (const e of ents) {
    if (e.startPoint && typeof e.startPoint.x === 'number') { px.push(e.startPoint.x); py.push(e.startPoint.y); }
    if (e.endPoint && typeof e.endPoint.x === 'number') { px.push(e.endPoint.x); py.push(e.endPoint.y); }
    if (!e.startPoint && e.center && typeof e.center.x === 'number') { px.push(e.center.x); py.push(e.center.y); }
    if (e.vertices) for (const v of e.vertices) if (typeof v.x === 'number') { px.push(v.x); py.push(v.y); }
    if (e.controlPoints) for (const v of e.controlPoints) if (typeof v.x === 'number') { px.push(v.x); py.push(v.y); }
  }
  px.sort((a, b) => a - b); py.sort((a, b) => a - b);
  const pct = (arr, f) => arr.length ? arr[Math.min(arr.length - 1, Math.max(0, Math.floor(arr.length * f)))] : 0;
  let minX = px.length ? pct(px, 0.02) : 0, maxX = px.length ? pct(px, 0.98) : 1;
  let minY = py.length ? pct(py, 0.02) : 0, maxY = py.length ? pct(py, 0.98) : 1;
  if (maxX - minX < 1) { minX -= 1; maxX += 1; }
  if (maxY - minY < 1) { minY -= 1; maxY += 1; }
  const pad = Math.max(1, (maxX - minX) * 0.005);
  minX -= pad; minY -= pad; maxX += pad; maxY += pad;
  const w = maxX - minX, h = maxY - minY;

  // 按颜色分组，同色合并成一条 path（12 万实体逐个 <line> 会让 SVG 膨胀十几倍）
  const groups = new Map();
  const push = (ci, d) => { if (!groups.has(ci)) groups.set(ci, []); groups.get(ci).push(d); };
  const Y = -1;   // y 翻转到 SVG 坐标系
  const ty = (y) => maxY + minY - y;   // 翻折基准

  let skipped = 0;
  for (const e of ents) {
    const ci = e.colorIndex || 7;
    switch (e.type) {
      case 'LINE': {
        const a = e.startPoint, b = e.endPoint;
        if (a && b) push(ci, `M${n(a.x)} ${n(ty(a.y))}L${n(b.x)} ${n(ty(b.y))}`);
        break;
      }
      case 'CIRCLE': {
        const c0 = e.center, r = e.radius;
        if (c0 && r) push(ci, `M${n(c0.x - r)} ${n(ty(c0.y))}a${n(r)} ${n(r)} 0 1 0 ${n(2 * r)} 0a${n(r)} ${n(r)} 0 1 0 ${n(-2 * r)} 0`);
        break;
      }
      case 'ARC': {
        const c0 = e.center, r = e.radius;
        if (c0 && r) {
          const a0 = (e.startAngle || 0), a1 = (e.endAngle || 0);
          const p = arcPath(c0.x, c0.y, r, a0, a1, -1);
          // arcPath 用的是数学坐标，这里统一翻折
          push(ci, p.replace(/M([-\d.]+) ([-\d.]+)A([-\d. ]+)([-\d.]+) ([-\d.]+)/,
            (m, x0, y0, mid, x1, y1) => `M${x0} ${n(ty(+y0))}A${mid}${x1} ${n(ty(+y1))}`));
        } else skipped++;
        break;
      }
      case 'LWPOLYLINE':
      case 'POLYLINE': {
        const vs = e.vertices || [];
        if (vs.length > 1) {
          let d = `M${n(vs[0].x)} ${n(ty(vs[0].y))}`;
          for (let i = 1; i < vs.length; i++) d += `L${n(vs[i].x)} ${n(ty(vs[i].y))}`;
          if (e.flag & 1) d += 'Z';
          push(ci, d);
        }
        break;
      }
      case 'SPLINE': {
        // 用控制点折线近似（精确 B 样条求值对预览意义不大）
        const ps = e.controlPoints || e.fitPoints || [];
        if (ps.length > 1) {
          let d = `M${n(ps[0].x)} ${n(ty(ps[0].y))}`;
          for (let i = 1; i < ps.length; i++) d += `L${n(ps[i].x)} ${n(ty(ps[i].y))}`;
          push(ci, d);
        }
        break;
      }
      case 'ELLIPSE': {
        const c0 = e.center;
        if (c0 && e.majorAxisEndPoint) {
          const mx = e.majorAxisEndPoint.x - c0.x, my = e.majorAxisEndPoint.y - c0.y;
          const rx = Math.hypot(mx, my), ry = rx * (e.axisRatio || 1);
          const ang = Math.atan2(my, mx) * 180 / Math.PI;
          push(ci, `M${n(c0.x)} ${n(ty(c0.y))}m${n(-rx)} 0a${n(rx)} ${n(ry)} ${n(ang)} 1 0 ${n(2 * rx)} 0a${n(rx)} ${n(ry)} ${n(ang)} 1 0 ${n(-2 * rx)} 0`);
        }
        break;
      }
      default: skipped++;
    }
  }

  // 线宽按图纸单位取：CAD 里 2mm 的线在 A1 图纸上就是正常粗细
  const sw = opts.strokeWidth || Math.max(0.5, Math.min(w, h) / 3000);
  let body = '';
  for (const [ci, ds] of groups) {
    // 必须用空格分隔：`200M300` 这种"数字紧接命令"会让 SVG 解析器把整段 path 丢掉
    body += `<path stroke="${aci(ci)}" fill="none" stroke-width="${n(sw)}" d="${ds.join(' ')}"/>`;
  }
  const svg = `<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="${n(minX)} ${n(minY)} ${n(w)} ${n(h)}" width="${Math.round(w)}" height="${Math.round(h)}"><rect x="${n(minX)}" y="${n(minY)}" width="${n(w)}" height="${n(h)}" fill="#fff"/>${body}</svg>`;
  return { svg, stats: { entities: ents.length, drawn: ents.length - skipped, skipped, colors: groups.size,
                         bbox: [n(minX), n(minY), n(w), n(h)], bytes: svg.length } };
}

if (process.argv[2]) {
  const r = await dwgToSvg(process.argv[2]);
  fs.writeFileSync(process.argv[3] || 'out.svg', r.svg);
  console.log(JSON.stringify(r.stats));
}
