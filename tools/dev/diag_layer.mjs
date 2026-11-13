// 查 LAYER 表结构（BYLAYER 颜色需要）
import fs from 'fs';
import { LibreDwg } from '@mlightcad/libredwg-web';

const lw = await LibreDwg.create();
const b = fs.readFileSync(process.argv[2] || 't2.dwg');
const db = lw.dwg_read_data(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength), 0);
const c = lw.convert(db);
const L = c.tables && c.tables.LAYER;
console.log('LAYER 容器类型:', Array.isArray(L) ? 'array' : typeof L, '条目', L ? Object.keys(L).length : 0);
const first = L && Object.values(L)[0];
if (first) {
  console.log('字段:', Object.keys(first).join(','));
  const o = {};
  for (const k of Object.keys(first)) {
    const v = first[k];
    o[k] = v && typeof v === 'object' ? JSON.stringify(v).slice(0, 50) : v;
  }
  console.log(JSON.stringify(o).slice(0, 400));
}
// 列出所有图层的名字和颜色
if (L) {
  Object.values(L).slice(0, 12).forEach(l => {
    console.log('  层:', l.name || l.entryName || l.layerName, '| 色号', l.colorIndex ?? l.color, '| 冻结', l.isFrozen, '| 关', l.isOff);
  });
}
