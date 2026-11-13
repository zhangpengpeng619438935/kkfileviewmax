// DWG → SVG 的后台任务入口：下载源文件、渲染、落盘 <fk>.cad.svg.gz
// 用法: node cad_vec.mjs <fileKey> <fileUrl>
// 由 tools/step_worker.py 的 cad 队列调用（node 和 npm 包只装在宿主机，容器里没有）
import fs from 'fs';
import zlib from 'zlib';
import { execFileSync } from 'child_process';
import { dwgToSvg } from './dwg2svg.mjs';

const SHEETS = process.env.KK_SHEETS || '/opt/kkfileview-service/data/gateway/sheets';
const [fk, url] = process.argv.slice(2);

function die(msg) {
  console.log('FAIL ' + msg);
  process.exit(3);
}

if (!fk || !url) die('缺少参数');

/* 阶段进度写进 Redis，网关的 /cadvec 会带给前端，页面上分阶段显示
   "下载源文件 / 解析 DWG / 生成矢量图" 各花多久。
   跑在宿主机上，只能通过 docker exec 找 kk-redis。 */
const PG = 'kk:cad:vec:pg:' + fk;
function setProg(obj) {
  try {
    execFileSync('docker', ['exec', 'kk-redis', 'redis-cli', '-n', '3', 'set', PG,
                            JSON.stringify(obj), 'EX', '600'],
                 { stdio: 'ignore', timeout: 5000 });
  } catch (_) { /* 进度上报失败不影响渲染 */ }
}

const t0 = Date.now();
const tmp = `/tmp/cadvec_${fk.slice(0, 12)}_${Date.now()}.dwg`;
setProg({ stage: 'dl', ms: 0 });
try {
  const res = await fetch(url, { redirect: 'follow' });
  if (!res.ok) die(`下载失败 HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  if (buf.length < 1024) die(`源文件太小 ${buf.length} 字节`);
  fs.writeFileSync(tmp, buf);
  const tDl = Date.now() - t0;
  setProg({ stage: 'parse', dlMs: tDl, bytes: buf.length });

  const t1 = Date.now();
  let parseMs = 0;
  const { svg, stats } = await dwgToSvg(tmp, {
    onStage: (st) => {
      parseMs = Date.now() - t1;
      setProg({ stage: st.stage, dlMs: tDl, bytes: buf.length,
                entities: st.entities, parseMs });
    },
  });
  const tRender = Date.now() - t1;

  fs.mkdirSync(SHEETS, { recursive: true });
  const out = `${SHEETS}/${fk}.cad.svg.gz`;
  // 先写临时文件再 rename：网关可能正在读旧版本，不能半截覆盖
  const part = out + '.part';
  fs.writeFileSync(part, zlib.gzipSync(Buffer.from(svg), { level: 6 }));
  fs.renameSync(part, out);

  const size = fs.statSync(out).size;
  setProg({ stage: 'done', dlMs: tDl, bytes: buf.length, parseMs,
            renderMs: Math.max(0, tRender - parseMs), totalMs: Date.now() - t0,
            entities: stats.entities, drawn: stats.drawn });
  console.log(JSON.stringify({
    ok: 1, bytes: size, raw: svg.length,
    dlMs: tDl, renderMs: tRender,
    ...stats,
  }));
} catch (e) {
  setProg({ stage: 'fail', error: String(e && e.message ? e.message : e).slice(0, 120) });
  die(String(e && e.message ? e.message : e).slice(0, 200));
} finally {
  try { fs.unlinkSync(tmp); } catch (_) {}
}
