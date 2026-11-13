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

/* B 样条求值（de Boor），每段采样 5 个点 */
function sampleBspline(cps, knots, degree) {
  const out = [];
  const nSeg = cps.length - degree;
  const STEPS = 5;
  const deBoor = (u) => {
    let k = degree;
    while (k < cps.length - 1 && u >= knots[k + 1]) k++;
    const d = [];
    for (let j = 0; j <= degree; j++) d[j] = cps[k - degree + j];
    for (let r = 1; r <= degree; r++) {
      for (let j = degree; j >= r; j--) {
        const i = k - degree + j;
        const lo = knots[i], hi = knots[i + degree + 1 - r];
        const alpha = (u - lo) / ((hi - lo) || 1);
        d[j] = { x: (1 - alpha) * d[j - 1].x + alpha * d[j].x,
                 y: (1 - alpha) * d[j - 1].y + alpha * d[j].y };
      }
    }
    return d[degree];
  };
  for (let i = degree; i < degree + nSeg; i++) {
    const t0 = knots[i], t1 = knots[i + 1];
    if (!(t1 > t0)) continue;
    for (let j = 0; j < STEPS; j++) out.push(deBoor(t0 + (t1 - t0) * j / STEPS));
  }
  out.push(deBoor(knots[degree + nSeg]));
  return out;
}

/* 圆弧采样。不用 SVG 的 A 命令：翻 y 之后 sweep-flag 的方向极易搞反
   （上一版就是翻了两遍 y，8040 个圆弧全部上下镜像、鼓向反了）。 */
function sampleArc(cx, cy, r, a0, a1) {
  let sweep = a1 - a0;
  while (sweep <= 0) sweep += Math.PI * 2;
  const steps = Math.min(64, Math.max(6, Math.ceil(sweep / (Math.PI / 16))));
  const out = [];
  for (let i = 0; i <= steps; i++) {
    const a = a0 + sweep * i / steps;
    out.push({ x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) });
  }
  return out;
}

/* 椭圆按 startAngle~endAngle 采样。这两个是**参数角**（偏心角），不是圆心角。
   必须遵守：实测 6044 个椭圆里绝大多数只占一小段（如 5.63~6.22 rad），
   全画成整圈就会得到一堆横穿图面的大环。 */
function sampleEllipse(cx, cy, rx, ry, rot, a0 = 0, a1 = Math.PI * 2) {
  let sweep = a1 - a0;
  while (sweep <= 0) sweep += Math.PI * 2;
  const steps = Math.min(72, Math.max(8, Math.ceil(sweep / (Math.PI / 24))));
  const co = Math.cos(rot), si = Math.sin(rot);
  const out = [];
  for (let i = 0; i <= steps; i++) {
    const a = a0 + sweep * i / steps;
    const x = rx * Math.cos(a), y = ry * Math.sin(a);
    out.push({ x: cx + x * co - y * si, y: cy + x * si + y * co });
  }
  return out;
}

function sampleCircle(cx, cy, r) { return sampleEllipse(cx, cy, r, r, 0); }

/* 实体在图纸坐标系里的采样点：既用于绘制，也用于"是否越界"判断。
   之前只拿圆心当探针，导致半径 34 万的圆/弧圆心在框内就照样画出来，
   一条巨弧横穿整张图。 */
function geomOf(e) {
  // 统一返回"多段折线"：[[{x,y},...], ...]。单个实体可能有多段（引线、尺寸界线）。
  switch (e.type) {
    case 'LINE':
      return (e.startPoint && e.endPoint) ? [[e.startPoint, e.endPoint]] : null;
    case 'ARC':
      return (e.center && e.radius)
        ? [sampleArc(e.center.x, e.center.y, e.radius, e.startAngle || 0, e.endAngle || 0)]
        : null;
    case 'CIRCLE':
      return (e.center && e.radius) ? [sampleCircle(e.center.x, e.center.y, e.radius)] : null;
    case 'LWPOLYLINE':
    case 'POLYLINE':
    case 'POLYLINE2D':
      return (e.vertices && e.vertices.length > 1) ? [e.vertices] : null;
    case 'SPLINE': {
      const cps = e.controlPoints || [], kn = e.knots || [], deg = e.degree || 3;
      if (cps.length > deg && kn.length >= cps.length + deg + 1) return [sampleBspline(cps, kn, deg)];
      if (e.fitPoints && e.fitPoints.length > 1) return [e.fitPoints];
      return null;
    }
    case 'ELLIPSE': {
      if (!e.center || !e.majorAxisEndPoint) return null;
      // majorAxisEndPoint 是相对中心的**矢量**（实测长度 0.5~263），不是绝对点
      const mx = e.majorAxisEndPoint.x, my = e.majorAxisEndPoint.y;
      const rx = Math.hypot(mx, my);
      // startAngle/endAngle 是参数角，不给就会画成整圈（6044 个椭圆弧变巨环的元凶）
      const a0 = typeof e.startAngle === 'number' ? e.startAngle : 0;
      const a1 = typeof e.endAngle === 'number' ? e.endAngle : Math.PI * 2;
      return [sampleEllipse(e.center.x, e.center.y, rx, rx * (e.axisRatio || 1),
                            Math.atan2(my, mx), a0, a1)];
    }
    case 'MULTILEADER': {
      // 引线顶点在 leaderSections[].leaderLines[].vertices
      const segs = [];
      for (const sec of e.leaderSections || []) {
        for (const ln of sec.leaderLines || []) {
          if (ln.vertices && ln.vertices.length > 1) segs.push(ln.vertices);
        }
      }
      return segs.length ? segs : null;
    }
    case 'LEADER':
      return (e.vertices && e.vertices.length > 1) ? [e.vertices] : null;
    case 'DIMENSION': {
      /* 线性/转角尺寸：sub1、sub2 是两条尺寸界线的起点，definitionPoint 在尺寸线上。
         尺寸线 = 把 sub1、sub2 投影到"过 definitionPoint、方向与 sub1→sub2 同向"的直线上。
         直接连 sub1-sub2 会画在零件本身上（实测实测值 34.98 明明是竖向间距，
         连出来却是一段贴在零件边缘的短线）。 */
      const p1 = e.subDefinitionPoint1, p2 = e.subDefinitionPoint2, dp = e.definitionPoint;
      if (!p1 || !p2) return null;
      const dx = p2.x - p1.x, dy = p2.y - p1.y;
      const len = Math.hypot(dx, dy);
      if (!len || !dp) return [[{ x: p1.x, y: p1.y }, { x: p2.x, y: p2.y }]];
      const ux = dx / len, uy = dy / len;
      const proj = (q) => {
        const t = (q.x - dp.x) * ux + (q.y - dp.y) * uy;
        return { x: dp.x + ux * t, y: dp.y + uy * t };
      };
      const d1 = proj(p1), d2 = proj(p2);
      return [
        [d1, d2],          // 尺寸线
        [p1, d1],          // 第一条尺寸界线
        [p2, d2],          // 第二条尺寸界线
      ];
    }
    case 'SOLID': {
      const cs = [e.corner1, e.corner2, e.corner3, e.corner4].filter(Boolean);
      return cs.length > 1 ? [cs] : null;
    }
    default:
      return null;
  }
}

/* 路径串格式化：全部用**相对坐标** l，并且省掉负号前的空格。
   这一步的收益最大 —— 126,349 个实体的图，绝对坐标是 14.3MB / gzip 3.65MB，
   改相对坐标后 6.2MB / gzip 0.70MB（gzip 小 81%）。因为相邻点是紧挨着的，
   差值都是小数字，压缩率极高；前端 parse 的字符量也少了六成。 */
const fmt = (v) => {
  const s = v.toFixed(1);
  return s.endsWith('.0') ? s.slice(0, -2) : s;
};

function pathOf(seg, close, ty) {
  let px = seg[0].x, py = ty(seg[0].y);
  let d = 'M' + fmt(px) + ' ' + fmt(py);
  for (let i = 1; i < seg.length; i++) {
    const x = seg[i].x, y = ty(seg[i].y);
    const dx = fmt(x - px), dy = fmt(y - py);
    if (dx === '0' && dy === '0') continue;   // 重合点直接跳过，不占字节
    // 负号本身就是分隔符：`l-3.5-2` 合法，不用写成 `l-3.5 -2`
    d += 'l' + dx + (dy.charAt(0) === '-' ? '' : ' ') + dy;
    px = x; py = y;
  }
  return close ? d + 'Z' : d;
}

function cleanText(t) {
  // MTEXT 带格式码：\P 换行、\fSimSun|b0; 字体、{} 分组，不清掉会满屏乱码
  return String(t)
    .replace(/\\P/g, ' ')
    .replace(/\\[A-Za-z][^;]*;/g, '')
    .replace(/\\[A-Za-z]/g, '')
    .replace(/[{}]/g, '')
    .replace(/\\\\/g, '\\')
    .trim();
}

const esc = (v) => String(v).replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export async function dwgToSvg(file, opts = {}) {
  const lw = await LibreDwg.create();
  const buf = fs.readFileSync(file);
  const db = lw.dwg_read_data(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), 0);
  const c = lw.convert(db);
  let ents = Object.values(c.entities || {});
  // 给调用方报阶段进度（前端分阶段显示"解析/生成"各花多久）
  if (opts.onStage) opts.onStage({ stage: 'parse', entities: ents.length });
  if (opts.only) ents = ents.filter(e => opts.only.includes(e.type));

  /* 图层 → 颜色号。实测 c.tables.LAYER 是 {"0":"{json}","1":"{json}"}，
     值是**未解析的 JSON 字符串**，必须 JSON.parse 才能拿到 name/colorIndex。 */
  /* 图纸单位：$INSUNITS 4=毫米、5=厘米、6=米、1=英寸…… 量尺寸时按它显示，
     否则只能报"图纸单位"，用户还得自己换算。 */
  const INS = { 0: '', 1: 'in', 2: 'ft', 3: 'mi', 4: 'mm', 5: 'cm', 6: 'm', 7: 'km',
                8: 'μin', 9: 'mil', 10: 'yd', 14: 'dm', 15: 'dam' };
  const units = INS[(c.header && c.header.INSUNITS) || 0] || '';

  const layerColor = {};
  const LT = c.tables && c.tables.LAYER;
  if (LT) {
    for (const k of Object.keys(LT)) {
      let v = LT[k];
      if (typeof v === 'string') { try { v = JSON.parse(v); } catch (_) { continue; } }
      if (v && v.name) layerColor[v.name] = v.colorIndex != null ? v.colorIndex : 7;
    }
  }

  // 先算每条实体的采样点，缓存下来（12 万实体算两遍太浪费）
  const geoms = new Map();
  for (const e of ents) {
    const g = geomOf(e);
    if (g && g.some(seg => seg.length > 1)) geoms.set(e, g);
  }

  /* 取景用分位数而不是 min/max：工业图纸里常有超出范围的构造线，用极值会把
     viewBox 撑到十几米，真正的图缩成角落一小团。 */
  const px = [], py = [];
  for (const g of geoms.values()) {
    for (const seg of g) {
      for (const p of seg) {
        if (typeof p.x === 'number' && typeof p.y === 'number') { px.push(p.x); py.push(p.y); }
      }
    }
  }
  px.sort((a, b) => a - b); py.sort((a, b) => a - b);
  const pct = (a, f) => a.length ? a[Math.min(a.length - 1, Math.max(0, Math.floor(a.length * f)))] : 0;
  let minX = px.length ? pct(px, 0.03) : 0, maxX = px.length ? pct(px, 0.97) : 1;
  let minY = py.length ? pct(py, 0.03) : 0, maxY = py.length ? pct(py, 0.97) : 1;
  if (maxX - minX < 1) { minX -= 1; maxX += 1; }
  if (maxY - minY < 1) { minY -= 1; maxY += 1; }
  const pad = Math.max(1, (maxX - minX) * 0.005);
  minX -= pad; minY -= pad; maxX += pad; maxY += pad;
  const w = maxX - minX, h = maxY - minY;

  const ty = (y) => maxY + minY - y;      // SVG 的 y 轴向下

  /* 离群实体过滤：图里偶有实体跑到几万单位外，画出来就是横穿整张图的巨线。
     按"取景外扩半个图宽"裁剪；探针用**真实采样点**，长导轨（在框内）不受影响。 */
  const clipPad = Math.max(w, h) * 0.5;
  const inBox = (p) => p.x >= minX - clipPad && p.x <= maxX + clipPad
                     && p.y >= minY - clipPad && p.y <= maxY + clipPad;

  const groups = new Map();
  const push = (gk, d) => { if (!groups.has(gk)) groups.set(gk, []); groups.get(gk).push(d); };

  let skipped = 0, dropped = 0, multiTextOnly = 0; const skipTypes = {};
  const texts = [];
  const minTextH = Math.max(0.5, h / 300);   // 文字最小高度：宽 1080px 时约 3.6px

  for (const e of ents) {
    const lay = e.layer || '0';
    let ci = (e.colorIndex == null ? 7 : e.colorIndex);
    if (ci === 256 || ci === 0) ci = layerColor[lay] != null ? layerColor[lay] : 7;
    const gk = lay + '|' + ci;              // 按"图层|颜色"分组，前端据此做图层开关

    if (e.type === 'TEXT' || e.type === 'MTEXT') {
      const ip = e.insertionPoint || e.startPoint || e.textPoint;
      const raw = e.text || e.textValue || '';
      if (ip && raw) {
        texts.push({ x: ip.x, y: ip.y, h: Math.max(e.textHeight || e.height || 2.5, minTextH),
                     ci, rot: (e.rotation || 0) * 180 / Math.PI, s: cleanText(raw) });
      }
      continue;
    }
    // 引线文字：MULTILEADER 的 textContent（实测就是 "1" 这种序号）
    if (e.type === 'MULTILEADER') {
      const raw = e.textContent || '';
      const anc = e.textAnchor || e.contentBasePosition;
      if (anc && raw) {
        texts.push({ x: anc.x, y: anc.y, h: Math.max(e.textHeight || 2.5, minTextH),
                     ci, rot: 0, s: cleanText(raw) });
      }
      // 很多多重引线的 leaderLines 只有 1 个顶点（线是"隐藏"的），这时只有文字，
      // 不能算漏画
      if (!geoms.get(e)) { multiTextOnly++; continue; }
    }
    // 形位公差：文字在 insertionPoint，带 %%v 之类的控制码
    if (e.type === 'TOLERANCE') {
      const ip = e.insertionPoint;
      const raw = cleanText(String(e.text || '').replace(/%%[a-zA-Z]/g, ' '));
      if (ip && raw) {
        texts.push({ x: ip.x, y: ip.y, h: Math.max(h / 250, minTextH), ci, rot: 0, s: raw });
      }
      continue;
    }
    // 尺寸数值：DIMENSION 的 measurement（34.98 这种），放在 textPoint
    if (e.type === 'DIMENSION' && e.measurement != null) {
      const tp = e.textPoint || e.definitionPoint;
      const raw = (e.text && String(e.text).trim()) || String(Math.round(e.measurement * 100) / 100);
      if (tp && raw && raw !== '<>') {
        texts.push({ x: tp.x, y: tp.y, h: Math.max(e.textHeight || h / 200, minTextH),
                     ci, rot: (e.textRotation || 0) * 180 / Math.PI, s: raw });
      }
    }

    const g = geoms.get(e);
    if (!g) { skipped++; skipTypes[e.type] = (skipTypes[e.type] || 0) + 1; continue; }
    // 每段单独判断越界，剪掉越界的那一段而不是整个实体
    const segs = g.filter(seg => seg.length > 1 && seg.every(inBox));
    if (!segs.length) { dropped++; continue; }

    const close = (e.type === 'LWPOLYLINE' || e.type === 'POLYLINE'
                   || e.type === 'POLYLINE2D' || e.type === 'SOLID') && (e.flag & 1 || e.type === 'SOLID');
    for (const seg of segs) {
      push(gk, pathOf(seg, close, ty));
    }
  }

  const sw = opts.strokeWidth || Math.max(0.5, Math.min(w, h) / 3000);
  let body = '';
  const layers = {};
  for (const [gk, ds] of groups) {
    const [lay, ci] = gk.split('|');
    // 段之间必须用空格分隔：`200M300` 会让 SVG 解析器把整段 d 丢掉
    body += `<path data-layer="${esc(lay)}" data-ci="${ci}" stroke="${aci(+ci)}" ` +
            `fill="none" stroke-width="${n(sw)}" d="${ds.join(' ')}"/>`;
    layers[lay] = (layers[lay] || 0) + ds.length;
  }

  let tbody = '';
  for (const t of texts) {
    if (!t.s) continue;
    const yy = ty(t.y);
    tbody += `<text x="${n(t.x)}" y="${n(yy)}" font-size="${n(t.h)}" ` +
             `fill="${aci(t.ci)}" font-family="SimSun, sans-serif"` +
             (Math.abs(t.rot) > 0.01 ? ` transform="rotate(${n(-t.rot)} ${n(t.x)} ${n(yy)})"` : '') +
             `>${esc(t.s)}</text>`;
  }

  const svg = `<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" ` +
    `data-units="${units}" ` +
    `viewBox="${n(minX)} ${n(minY)} ${n(w)} ${n(h)}" width="${Math.round(w)}" height="${Math.round(h)}">` +
    `<rect x="${n(minX)}" y="${n(minY)}" width="${n(w)}" height="${n(h)}" fill="#fff"/>${body}${tbody}</svg>`;

  const drawn = [...groups.values()].reduce((s, ds) => s + ds.length, 0);
  return { svg, stats: { entities: ents.length, drawn, skipped, dropped, multiTextOnly, skipTypes,
                         colors: groups.size, layers, texts: texts.length, units,
                         bbox: [n(minX), n(minY), n(w), n(h)], svgChars: svg.length } };
}
