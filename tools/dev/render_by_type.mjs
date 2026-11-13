// 按实体类型分别渲染，隔离"杂乱弧线"到底来自哪一类
import fs from 'fs';
import { dwgToSvg } from './dwg2svg.mjs';

const file = process.argv[2];
const groups = {
  line: ['LINE'],
  arc: ['ARC', 'CIRCLE'],
  poly: ['LWPOLYLINE', 'POLYLINE', 'POLYLINE2D'],
  spline: ['SPLINE'],
  ellipse: ['ELLIPSE'],
};
for (const [name, only] of Object.entries(groups)) {
  const r = await dwgToSvg(file, { only });
  fs.writeFileSync('only_' + name + '.svg', r.svg);
  console.log(name.padEnd(9), 'drawn', r.stats.drawn, 'skipped', r.stats.skipped,
              'bbox', JSON.stringify(r.stats.bbox), (r.stats.bytes / 1024).toFixed(0) + 'KB');
}
