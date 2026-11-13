// 用新版 occt-import-js（支持传精度参数）试解析大装配，判断"0 顶点"是不是精度/内存问题
// 用法: node probe_occt_new.js <file.step> [linearDeflection mm]
const fs = require('fs');
const DIR = process.env.OCCT_NEW || '/tmp/occtnew/package/dist';
const f = require(DIR + '/occt-import-js.js');

(async () => {
  const o = await f({
    wasmBinary: fs.readFileSync(DIR + '/occt-import-js.wasm'),
    print: s => console.log('[occt] ' + s),
    printErr: s => console.log('[err] ' + s),
  });
  console.log('新版 ReadStepFile.length =', o.ReadStepFile.length);
  const src = process.argv[2];
  const defl = Number(process.argv[3] || 2);
  const u8 = new Uint8Array(fs.readFileSync(src));
  console.log('源 ' + (u8.length / 1048576).toFixed(1) + ' MB，弦高 ' + defl + 'mm');

  const t = Date.now();
  let r = null, e = null;
  try {
    r = o.ReadStepFile(u8, {
      linearUnit: 'millimeter',
      linearDeflectionType: 'absolute_value',
      linearDeflection: defl,
      angularDeflection: 0.8,
    });
  } catch (ex) { e = ex; }
  const ms = Date.now() - t;
  if (!r) { console.log('失败 ' + (ms / 1000).toFixed(1) + 's: ' + String(e).slice(0, 200)); process.exit(1); }
  let verts = 0, tris = 0, empty = 0, maxV = 0;
  (r.meshes || []).forEach(m => {
    const p = m.attributes && m.attributes.position;
    const n = p && p.array ? p.array.length / 3 : 0;
    verts += n; if (!n) empty++; if (n > maxV) maxV = n;
    if (m.index && m.index.array) tris += m.index.array.length / 3;
  });
  console.log('成功=' + r.success + '  网格=' + (r.meshes || []).length
    + '  空网格=' + empty + '  顶点=' + verts + '（单件最多 ' + maxV + '）'
    + '  三角形=' + tris + '  用时 ' + (ms / 1000).toFixed(1) + 's');
  if (r.meshes && r.meshes.length) {
    for (let i = 0; i < Math.min(5, r.meshes.length); i++) {
      const m = r.meshes[i];
      const n = m.attributes && m.attributes.position ? m.attributes.position.array.length / 3 : 0;
      console.log('  #' + i + ' ' + (m.name || '').slice(0, 26) + ' 顶点 ' + n);
    }
  }
})();
