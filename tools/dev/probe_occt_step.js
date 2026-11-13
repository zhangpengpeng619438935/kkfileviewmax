// 探针：看 OCCT 读这个 STEP 到底返回了什么（原来 step2mesh 把 printErr 全吞了）
// 用法: node probe_occt_step.js <file.step>
const fs = require('fs');
const path = require('path');
const DIR = '/opt/kkfileview-service/tools/stepmesh';
const factory = require(path.join(DIR, 'occt-import-js.js'));

const src = process.argv[2];
if (!src) { console.error('用法: node probe_occt_step.js <file>'); process.exit(2); }

(async () => {
  const t0 = Date.now();
  const wasmBinary = fs.readFileSync(path.join(DIR, 'occt-import-js.wasm'));
  const occt = await factory({
    wasmBinary,
    print: (s) => console.log('[occt] ' + s),
    printErr: (s) => console.log('[occt-err] ' + s),
  });
  console.log('wasm 就绪 ' + (Date.now() - t0) + 'ms');

  const buf = fs.readFileSync(src);
  console.log('源文件 ' + (buf.length / 1048576).toFixed(1) + ' MB');
  const u8 = new Uint8Array(buf);

  // 1) 默认参数（和 step2mesh 现状一致：其实没传 params）
  let t = Date.now();
  let res = null, err = null;
  try { res = occt.ReadStepFile(u8); } catch (e) { err = e; }
  console.log('\n--- 默认参数 ---');
  console.log('耗时 ' + (Date.now() - t) + 'ms  异常 ' + (err ? String(err).slice(0, 200) : '无'));
  dump(res);

  // 2) 显式给参数：粗一点，看是不是三角化把内存吃光了
  t = Date.now();
  res = null; err = null;
  try {
    res = occt.ReadStepFile(u8, {
      linearUnit: 'millimeter',
      linearDeflectionType: 'absolute_value',
      linearDeflection: 1.0,
      angularDeflection: 0.8,
    });
  } catch (e) { err = e; }
  console.log('\n--- 显式粗参数（1.0mm / 0.8rad） ---');
  console.log('耗时 ' + (Date.now() - t) + 'ms  异常 ' + (err ? String(err).slice(0, 200) : '无'));
  dump(res);

  function dump(r) {
    if (!r) { console.log('返回 null'); return; }
    console.log('keys: ' + Object.keys(r).join(', '));
    console.log('success: ' + r.success + '  meshes 数组长度: '
      + (r.meshes ? r.meshes.length : '无'));
    if (r.root) console.log('root: ' + JSON.stringify(r.root).slice(0, 300));
    if (!r.meshes) return;
    let total = 0, empty = 0;
    r.meshes.forEach((m, i) => {
      const p = m.attributes && m.attributes.position;
      const n = p && p.array ? p.array.length / 3 : 0;
      total += n;
      if (!n) empty++;
      if (i < 5) console.log('  #' + i + ' name=' + (m.name || '').slice(0, 24)
        + ' 顶点=' + n + ' 索引=' + (m.index ? m.index.array.length : 0));
    });
    console.log('合计顶点 ' + total + '，空网格 ' + empty + '/' + r.meshes.length);
  }
})().catch(e => { console.error('探针失败:', e); process.exit(1); });
