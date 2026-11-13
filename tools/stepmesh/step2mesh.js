// 用 OCCT（与 O3DV 同款 WASM）在服务端解析 STEP，导出可直接喂给 three.js 的网格。
// 用法: node step2mesh.js -o <out.mesh> [--tf map.json] [--probe] <in1> [in2 ...]
//
// 多文件输入是为了 FCStd：FreeCAD 文档里一个零件一个 .brp，需要逐个解析后
// 按各自的 Placement 摆好再合并成一个网格。
// map.json: {"Part__Feature.Shape.brp": [px,py,pz,qx,qy,qz,qw], ...}
// 产物格式（自定义，简单到能直接用 BufferGeometry 拼）:
//   4 字节 magic "S3DM" + 4 字节 JSON 头长度 + JSON 头 + 二进制顶点块
const fs = require('fs');
const path = require('path');

const factory = require(path.join(__dirname, 'occt-import-js.js'));

const STEP_PARAMS = {
  linearUnit: 'millimeter',
  linearDeflectionType: 'absolute_value',
  linearDeflection: 0.1,        // 弦高误差：0.1mm，装配体够用且网格不爆炸
  angularDeflection: 0.5,
};

function movePoint(p, t) {
  const [px, py, pz, qx, qy, qz, qw] = t;
  const n = Math.hypot(qx, qy, qz, qw) || 1;
  const x = qx / n, y = qy / n, z = qz / n, w = qw / n;
  const m00 = 1 - 2 * (y * y + z * z), m01 = 2 * (x * y - z * w), m02 = 2 * (x * z + y * w);
  const m10 = 2 * (x * y + z * w), m11 = 1 - 2 * (x * x + z * z), m12 = 2 * (y * z - x * w);
  const m20 = 2 * (x * z - y * w), m21 = 2 * (y * z + x * w), m22 = 1 - 2 * (x * x + y * y);
  return [m00 * p[0] + m01 * p[1] + m02 * p[2] + px,
          m10 * p[0] + m11 * p[1] + m12 * p[2] + py,
          m20 * p[0] + m21 * p[1] + m22 * p[2] + pz];
}

// FreeCAD 的 Placement = 平移 + 四元数(x,y,z,w)。不套这层，42 个零件会全堆在原点。
function applyPlacement(arr, t) {
  const [px, py, pz, qx, qy, qz, qw] = t;
  const n = Math.hypot(qx, qy, qz, qw) || 1;
  const x = qx / n, y = qy / n, z = qz / n, w = qw / n;
  const m00 = 1 - 2 * (y * y + z * z), m01 = 2 * (x * y - z * w), m02 = 2 * (x * z + y * w);
  const m10 = 2 * (x * y + z * w), m11 = 1 - 2 * (x * x + z * z), m12 = 2 * (y * z - x * w);
  const m20 = 2 * (x * z - y * w), m21 = 2 * (y * z + x * w), m22 = 1 - 2 * (x * x + y * y);
  for (let i = 0; i < arr.length; i += 3) {
    const X = arr[i], Y = arr[i + 1], Z = arr[i + 2];
    arr[i]     = m00 * X + m01 * Y + m02 * Z + px;
    arr[i + 1] = m10 * X + m11 * Y + m12 * Z + py;
    arr[i + 2] = m20 * X + m21 * Y + m22 * Z + pz;
  }
}

function packMesh(result) {
  const meshes = [];
  const chunks = [];
  let offset = 0;
  for (const m of result.meshes) {
    const pos = m.attributes && m.attributes.position;
    if (!pos || !pos.array || !pos.array.length) continue;
    const idx = m.index && m.index.array ? m.index.array : null;
    const nrm = m.attributes.normal && m.attributes.normal.array;
    const posBuf = Buffer.from(new Float32Array(pos.array).buffer);
    const idxBuf = idx ? Buffer.from(new Uint32Array(idx).buffer) : null;
    const nrmBuf = nrm ? Buffer.from(new Float32Array(nrm).buffer) : null;
    const color = m.color ? [m.color[0], m.color[1], m.color[2]] : null;
    const item = {
      name: m.name || '',
      vertices: pos.array.length / 3,
      posOffset: offset, posBytes: posBuf.length,
      idxOffset: idxBuf ? offset + posBuf.length : -1,
      idxBytes: idxBuf ? idxBuf.length : 0,
      nrmOffset: nrmBuf ? offset + posBuf.length + (idxBuf ? idxBuf.length : 0) : -1,
      nrmBytes: nrmBuf ? nrmBuf.length : 0,
      color,
    };
    chunks.push(posBuf);
    if (idxBuf) chunks.push(idxBuf);
    if (nrmBuf) chunks.push(nrmBuf);
    offset += posBuf.length + (idxBuf ? idxBuf.length : 0) + (nrmBuf ? nrmBuf.length : 0);
    meshes.push(item);
  }
  const meta = Buffer.from(JSON.stringify({ meshes }), 'utf8');
  const head = Buffer.alloc(8);
  head.write('S3DM', 0, 'ascii');
  head.writeUInt32LE(meta.length, 4);
  return Buffer.concat([head, meta, ...chunks]);
}

async function main() {
  const args = process.argv.slice(2);
  let out = '', tfFile = '', probe = false;
  const srcs = [];
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '-o') out = args[++i];
    else if (a === '--tf') tfFile = args[++i];
    else if (a === '--probe') probe = true;
    else srcs.push(a);
  }
  if (!srcs.length) {
    console.error('usage: node step2mesh.js -o <out.mesh> [--tf map.json] <in...>');
    process.exit(2);
  }
  const tf = tfFile ? JSON.parse(fs.readFileSync(tfFile, 'utf8')) : {};

  const t0 = Date.now();
  // 这个构建是给浏览器用的，默认走 fetch 加载 .wasm，node 里会报 ERR_INVALID_URL。
  // 直接把 wasm 内容递进去（wasmBinary），跳过加载器。
  const wasmBinary = fs.readFileSync(path.join(__dirname, 'occt-import-js.wasm'));
  const occt = await factory({ wasmBinary, print: () => {}, printErr: () => {} });
  const tWasm = Date.now();

  const all = [];
  const items = [];            // {mesh, tf} 先存着，等判定要不要套位姿
  let verts = 0, tris = 0, ok = 0, bad = 0;
  for (const src of srcs) {
    const buf = fs.readFileSync(src);
    // 按扩展名选解析器：这个构建同时导出了 ReadStepFile / ReadIgesFile / ReadBrepFile，
    // 之前一律当 STEP 读，.igs/.brp 全都解析失败。
    const ext = path.extname(src).toLowerCase();
    const reader = (ext === '.iges' || ext === '.igs') ? occt.ReadIgesFile
                 : (ext === '.brep' || ext === '.brp') ? occt.ReadBrepFile
                 : occt.ReadStepFile;
    let res = null;
    try { res = reader.call(occt, new Uint8Array(buf)); } catch (e) { res = null; }
    if (!res || !res.success || !res.meshes) { bad++; continue; }
    ok++;
    const t = tf[path.basename(src)] || tf[src];
    for (const m of res.meshes) {
      const pos = m.attributes && m.attributes.position;
      if (!pos || !pos.array || !pos.array.length) continue;
      items.push({mesh: m, tf: t});
      all.push(m);
      verts += pos.array.length / 3;
      if (m.index) tris += m.index.array.length / 3;
    }
  }
  const tParse = Date.now();

  /* 位姿到底该不该套？看数据，不看命名：
     FreeCAD 的 .brp 有两种情况——几何存局部坐标（必须叠加 Placement），
     或者导入时已经烘进全局坐标（再叠就整体炸开，实测最远零件从 145mm 变成 1529mm）。
     判据：两种假设下各零件中心到质心的 RMS 距离，取更紧凑的那个。
     一次遍历就够，不用二次解析。 */
  const hasTf = items.some(function (it) { return it.tf; });
  let applied = false;
  if (hasTf) {
    function centers(useTf) {
      return items.map(function (it) {
        const a = it.mesh.attributes.position.array;
        let mn = [1e30, 1e30, 1e30], mx = [-1e30, -1e30, -1e30];
        for (let i = 0; i < a.length; i += 3) {
          for (let k = 0; k < 3; k++) {
            if (a[i + k] < mn[k]) mn[k] = a[i + k];
            if (a[i + k] > mx[k]) mx[k] = a[i + k];
          }
        }
        const c = [0, 1, 2].map(function (k) { return (mn[k] + mx[k]) / 2; });
        if (useTf && it.tf) return movePoint(c, it.tf);
        return c;
      });
    }
    function rms(pts) {
      const n = pts.length || 1;
      const c = [0, 1, 2].map(function (k) {
        return pts.reduce(function (s, p) { return s + p[k]; }, 0) / n;
      });
      const v = pts.reduce(function (s, p) {
        return s + (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2;
      }, 0) / n;
      return Math.sqrt(v);
    }
    const raw = rms(centers(false)), moved = rms(centers(true));
    applied = moved < raw;
    console.log(`位姿判定: 原样 RMS ${raw.toFixed(1)} / 叠加后 ${moved.toFixed(1)} mm ` +
                `→ ${applied ? '叠加 Placement' : '几何已是全局坐标，忽略 Placement'}`);
  }
  if (applied) {
    for (const it of items) {
      if (!it.tf) continue;
      applyPlacement(it.mesh.attributes.position.array, it.tf);
      it.mesh.attributes.normal = undefined;
    }
  }
  /* 多文件合并（FCStd 这类）不传法线：36MB 文档合并后法线要占 4MB，
     而浏览器 computeVertexNormals 对 60 万顶点只要一百多毫秒。
     实测这一步让 FCStd 从 3.06 秒降到 2 秒出头。单文件（STEP/IGES）保留原法线。 */
  if (srcs.length > 1) {
    for (const it of items) it.mesh.attributes.normal = undefined;
  }
  const final = items.map(function (it) { return it.mesh; });
  if (!ok) { console.error('全部解析失败'); process.exit(1); }
  console.log(`wasm ${tWasm - t0}ms  parse ${tParse - tWasm}ms  ` +
    `输入 ${srcs.length} 个(成功 ${ok}/失败 ${bad})  meshes ${all.length}  ` +
    `vertices ${verts}  triangles ${tris}`);
  if (probe) return;
  if (out) {
    const packed = packMesh({ meshes: final });
    fs.writeFileSync(out, packed);
    console.log(`写出 ${out}  ${(packed.length / 1048576).toFixed(2)} MB  总 ${Date.now() - t0}ms`);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
