# -*- coding: utf-8 -*-
"""把 STEP 装配体拆成零件解析、再按实例位姿拼回一个网格。

为什么需要它：这份 192MB 的标定图在 occt-import-js 里"读得出、画不出"——
`success=true`、返回 6708 个网格节点，但顶点全是 0。对照实验（2026-11-05）：
把 `NEXT_ASSEMBLY_USAGE_OCCURRENCE` / `CONTEXT_DEPENDENT_SHAPE_REPRESENTATION` /
`ITEM_DEFINED_TRANSFORMATION` 去掉后，同样的几何立刻能出 28,482 个三角形。
也就是说：**装配实例实体会让这个构建的三角化整体返回空**。

所以流程改成：
  1. 逐零件切成子 STEP（带产品链，不带装配实体）→ 每个都能三角化
  2. 自己解析装配位姿（AXIS2_PLACEMENT_3D → 4×4，沿 NAUO 父子关系自顶向下合成）
  3. 按实例复制零件网格、套矩阵，合并成一个 S3DM

位姿在这份文件里是**复合实体**写法，按类型名统计会得到 0：
  #n = ( REPRESENTATION_RELATIONSHIP(...) REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#idt)
         SHAPE_REPRESENTATION_RELATIONSHIP() );
  #idt = ITEM_DEFINED_TRANSFORMATION('','',#axis1,#axis2);
  #cdsr = CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(<NAUO 的 PDS>, #n);

用法：
    python3 step_assemble.py --report  big.step        # 只算位姿，打印统计
    python3 step_assemble.py --outdir /tmp/asm big.step  # 切件 + 解析 + 拼装
"""
import argparse
import json
import math
import os
import re
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_split as sp  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MESHER = os.path.join(HERE, "stepmesh", "step2mesh.js")


# ---------------------------------------------------------------- 基础几何

def decode_step_name(s):
    """还原 STEP 字符串里的 ISO 10303-21 转义（中文名几乎都是这种）。

    `\\X2\\76f47ebf\\X0\\` 是 UTF-16BE 的十六进制，`\\X4\\` 是 UTF-32BE，
    `\\\\` 是反斜杠本身，`''` 是单引号。不解码的话页面上就是
    "\\X2\\76f47ebf\\X0\\LMK20LUU" 这种乱码。
    """
    bs = chr(92)
    x2, x4, x0 = bs + "X2" + bs, bs + "X4" + bs, bs + "X0" + bs
    out, i = [], 0
    while i < len(s):
        if s.startswith(x2, i) or s.startswith(x4, i):
            is16 = s.startswith(x2, i)
            j = s.find(x0, i + 4)
            if j < 0:
                out.append(s[i:])
                break
            hx = s[i + 4:j]
            try:
                out.append(bytes.fromhex(hx).decode(
                    "utf-16-be" if is16 else "utf-32-be", "replace"))
            except ValueError:
                out.append(hx)
            i = j + 4
        elif s.startswith(bs * 2, i):
            out.append(bs)
            i += 2
        elif s.startswith("''", i):
            out.append("'")
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out).strip()


def args_of(body):
    """把实体括号里的参数按顶层逗号切开（保留 $ 占位，跳过字符串内的逗号）。

    为什么不能用"第几个 #id"来取参数：`AXIS2_PLACEMENT_3D` 的 axis / ref_direction
    允许写成 `$`，这时引用列表会少一项，按下标取就会把 ref_direction 当成 axis，
    构造出一个 90° 转错的位姿 —— 实测视野盒子（水平板）被立了起来，就是这个原因。
    """
    i = body.find("(")
    j = body.rfind(")")
    if i < 0 or j <= i:
        return []
    inner = body[i + 1:j]
    out, cur, depth, in_str = [], "", 0, False
    for ch in inner:
        if in_str:
            cur += ch
            if ch == "'":
                in_str = False
            continue
        if ch == "'":
            in_str = True
            cur += ch
        elif ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def ref_id(tok):
    """参数 token 里的 #id（没有就 None）。"""
    if not tok or tok == "$":
        return None
    m = re.search(r"#(\d+)", tok)
    return int(m.group(1)) if m else None


def vec3(body):
    """从 `CARTESIAN_POINT('',(x,y,z))` / `DIRECTION('',(x,y,z))` 里取三个数。"""
    i = body.find("(")
    j = body.find("(", i + 1)
    k = body.find(")", j)
    if j < 0 or k < 0:
        return None
    parts = body[j + 1:k].split(",")
    if len(parts) < 3:
        return None
    try:
        return [float(x.strip().rstrip(".")) for x in parts[:3]]
    except ValueError:
        return None


def norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 1e-12 else [0.0, 0.0, 1.0]


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def mat_identity():
    return [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def mat_from_axis(z_axis, x_ref, origin):
    """AXIS2_PLACEMENT_3D → 4×4（列向量为局部 x/y/z 轴，第四列为原点）。"""
    z = norm(z_axis or [0, 0, 1])
    xr = x_ref or [1, 0, 0]
    # 去掉 z 方向分量后归一化；退化时随便挑一个与之垂直的方向
    d = dot(xr, z)
    x = [xr[0] - z[0] * d, xr[1] - z[1] * d, xr[2] - z[2] * d]
    if math.sqrt(dot(x, x)) < 1e-9:
        x = [1, 0, 0] if abs(z[0]) < 0.9 else [0, 1, 0]
        d = dot(x, z)
        x = [x[0] - z[0] * d, x[1] - z[1] * d, x[2] - z[2] * d]
    x = norm(x)
    y = norm(cross(z, x))
    return [x[0], y[0], z[0], origin[0],
            x[1], y[1], z[1], origin[1],
            x[2], y[2], z[2], origin[2],
            0.0, 0.0, 0.0, 1.0]


def mat_mul(a, b):
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[r * 4 + c] = sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4))
    return out


def mat_inv_rigid(m):
    """刚体矩阵求逆：R^T 与 -R^T t。

    注意 m 是**行主序**（m[0..3] 是第 0 行）。旋转部分的第 0 列（局部 x 轴）是
    (m[0], m[4], m[8])，所以 R^T 的三行正好就是：
        [m[0], m[4], m[8]]
        [m[1], m[5], m[9]]
        [m[2], m[6], m[10]]
    之前这里多转置了一次（把 R^T 又转回 R），导致求出来的"逆"不是逆：
    自乘乘出来是个斜的旋转矩阵，于是每个实例都被套了一个歪掉的位姿 ——
    表现就是底面/侧面有零件错位、整体比原始点云小一圈。
    """
    rt = [[m[0], m[4], m[8]],
          [m[1], m[5], m[9]],
          [m[2], m[6], m[10]]]
    t = [m[3], m[7], m[11]]
    tt = [-sum(rt[i][k] * t[k] for k in range(3)) for i in range(3)]
    return [rt[0][0], rt[0][1], rt[0][2], tt[0],
            rt[1][0], rt[1][1], rt[1][2], tt[1],
            rt[2][0], rt[2][1], rt[2][2], tt[2],
            0.0, 0.0, 0.0, 1.0]


def apply(m, p):
    return [m[0] * p[0] + m[1] * p[1] + m[2] * p[2] + m[3],
            m[4] * p[0] + m[5] * p[1] + m[6] * p[2] + m[7],
            m[8] * p[0] + m[9] * p[1] + m[10] * p[2] + m[11]]


# ---------------------------------------------------------------- STEP 解析

class Assembly(object):
    def __init__(self, header, ents, flip=False):
        self.header = header
        self.ents = ents
        self.flip = flip

        self.axis_cache = {}
        self.idt_cache = {}

        # NAUO: (id, parent_pd, child_pd)
        self.nauo = []
        for i, b in ents.items():
            if b.split("(", 1)[0].strip() == "NEXT_ASSEMBLY_USAGE_OCCURRENCE":
                rr = sp.refs(b)
                if len(rr) >= 2:
                    self.nauo.append((i, rr[-2], rr[-1]))

        # PRODUCT_DEFINITION_SHAPE：definition 可能是 PRODUCT_DEFINITION，也可能是 NAUO
        self.pds_def = {}
        for i, b in ents.items():
            if b.split("(", 1)[0].strip() == "PRODUCT_DEFINITION_SHAPE":
                rr = sp.refs(b)
                if rr:
                    self.pds_def[i] = rr[-1]

        # CDSR 的两个参数顺序各家写法不一致：这份文件是（关系, NAUO 的 PDS），
        # 有的导出是反的。按实体类型判断，别靠位置。
        self.cdsr_of_pds = {}
        for i, b in ents.items():
            if b.split("(", 1)[0].strip() != "CONTEXT_DEPENDENT_SHAPE_REPRESENTATION":
                continue
            rr = sp.refs(b)
            if len(rr) < 2:
                continue
            pds = rel = None
            for r in rr[:2]:
                head = ents.get(r, "").split("(", 1)[0].strip()
                if head == "PRODUCT_DEFINITION_SHAPE":
                    pds = r
                else:
                    rel = r          # 复合实体（以 "(" 开头）或关系实体
            if pds is None or rel is None:
                # 兜底：沿用旧的顺序假设
                pds, rel = rr[0], rr[1]
            self.cdsr_of_pds.setdefault(pds, []).append(rel)

        self.nauo_pds = {}           # nauo id -> pds id
        for pds, definition in self.pds_def.items():
            self.nauo_pds.setdefault(definition, pds)

        self.root_pds = {}           # pd -> [pds]（产品自身的）
        for pds, definition in self.pds_def.items():
            self.root_pds.setdefault(definition, []).append(pds)

        # 产品定义 → 几何表示（复用 step_split 的索引）
        self.model = sp.Model(header, ents)
        self.pd_rep = self.model.pd_rep

    # -- 单个 AXIS2_PLACEMENT_3D
    def axis(self, aid):
        """AXIS2_PLACEMENT_3D(name, location, axis, ref_direction) → 4×4。

        axis / ref_direction 可能是 `$`（省略），必须按参数位置取、缺省用默认轴，
        不能按引用个数猜 —— 见 args_of() 的注释。
        """
        if aid in self.axis_cache:
            return self.axis_cache[aid]
        body = self.ents.get(aid, "")
        if body.split("(", 1)[0].strip() != "AXIS2_PLACEMENT_3D":
            return None
        a = args_of(body)
        if len(a) < 2:
            return None
        loc = ref_id(a[1]) if len(a) > 1 else None
        az = ref_id(a[2]) if len(a) > 2 else None
        ax = ref_id(a[3]) if len(a) > 3 else None
        origin = vec3(self.ents.get(loc, "")) if loc else [0, 0, 0]
        z = vec3(self.ents.get(az, "")) if az else [0, 0, 1]
        x = vec3(self.ents.get(ax, "")) if ax else [1, 0, 0]
        m = mat_from_axis(z, x, origin or [0, 0, 0])
        self.axis_cache[aid] = m
        return m

    def item_transform(self, idt_id):
        """ITEM_DEFINED_TRANSFORMATION('','',#axis1,#axis2) → 子件到父件的矩阵。"""
        if idt_id in self.idt_cache:
            return self.idt_cache[idt_id]
        body = self.ents.get(idt_id, "")
        rr = sp.refs(body)
        m = None
        if len(rr) >= 2:
            a1, a2 = self.axis(rr[-2]), self.axis(rr[-1])
            if a1 and a2:
                # 子件坐标系 axis2 在父件坐标系 axis1 里的位置：
                #   p_parent = M1 * inv(M2) * p_child
                m = mat_mul(a1, mat_inv_rigid(a2))
                if self.flip:
                    m = mat_mul(a2, mat_inv_rigid(a1))
        self.idt_cache[idt_id] = m
        return m

    def cdsr_matrix(self, nauo_id):
        """某个 NAUO 的位姿矩阵（子件相对父件）。"""
        pds = self.nauo_pds.get(nauo_id)
        if not pds:
            return None
        for rel in self.cdsr_of_pds.get(pds, []):
            body = self.ents.get(rel, "")
            for r in sp.refs(body):
                if self.ents.get(r, "").split("(", 1)[0].strip() \
                        == "ITEM_DEFINED_TRANSFORMATION":
                    return self.item_transform(r)
        return None

    # -- 层级
    def occurrences(self):
        """自顶向下展开，返回 [(几何表示, 4×4 世界矩阵, 实例名)]。"""
        children_of = {}
        child_pds = set()
        for nauo, parent, child in self.nauo:
            children_of.setdefault(parent, []).append((nauo, child))
            child_pds.add(child)
        roots = [pd for pd in children_of if pd not in child_pds]
        if not roots:
            roots = list(children_of.keys())[:1]

        out = []

        def walk(pd, m, depth):
            if depth > 32:
                return
            geo = self.pd_rep.get(pd)
            if geo:
                out.append((geo, m, self._name_of(pd)))
            for nauo, child in children_of.get(pd, []):
                sub = self.cdsr_matrix(nauo) or mat_identity()
                walk(child, mat_mul(m, sub), depth + 1)

        for r in roots:
            walk(r, mat_identity(), 0)
        return out

    def _name_of(self, pd):
        """PRODUCT_DEFINITION 的名字。链路：

            PRODUCT_DEFINITION  --(引用)-->  PRODUCT_DEFINITION_FORMATION...
                                 --(引用)-->  PRODUCT('名字', ...)

        三个坑，之前全踩了导致页面上全是 part44：
        ① FORMATION 引用的是 **PRODUCT**，不是 PRODUCT_DEFINITION。原来反着找，
           永远匹配不上；
        ② 实体类型实际是 PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE，
           精确匹配基础类型会一个都取不到；
        ③ 中文名是 \\X2\\<UTF-16BE>\\X0\\ 转义，必须解码。
        """
        b = self.ents.get(pd, "")
        if b.split("(", 1)[0].strip() != "PRODUCT_DEFINITION":
            return ""
        form = None
        for r in sp.refs(b):
            if self.ents.get(r, "").split("(", 1)[0].strip() \
                    .startswith("PRODUCT_DEFINITION_FORMATION"):
                form = r
                break
        if form is None:
            return ""
        for r in sp.refs(self.ents.get(form, "")):
            bb = self.ents.get(r, "")
            if bb.split("(", 1)[0].strip() != "PRODUCT":
                continue
            m = bb.find("'")
            if m >= 0:
                return decode_step_name(bb[m + 1:bb.find("'", m + 1)])
        return ""


# ---------------------------------------------------------------- S3DM 读写

def read_s3dm(path):
    with open(path, "rb") as f:
        b = f.read()
    if b[:4] != b"S3DM":
        raise SystemExit("不是 S3DM: %s" % path)
    n = struct.unpack("<I", b[4:8])[0]
    meta = json.loads(b[8:8 + n].decode("utf-8"))
    base = 8 + n
    meshes = []
    for m in meta["meshes"]:
        pos = struct.unpack("<%df" % (m["posBytes"] // 4),
                            b[base + m["posOffset"]:base + m["posOffset"] + m["posBytes"]])
        idx = None
        if m.get("idxBytes"):
            idx = struct.unpack("<%dI" % (m["idxBytes"] // 4),
                                b[base + m["idxOffset"]:base + m["idxOffset"] + m["idxBytes"]])
        nrm = None
        if m.get("nrmBytes"):
            nrm = struct.unpack("<%df" % (m["nrmBytes"] // 4),
                                b[base + m["nrmOffset"]:base + m["nrmOffset"] + m["nrmBytes"]])
        meshes.append({"name": m.get("name", ""), "pos": list(pos),
                       "idx": idx, "nrm": nrm, "color": m.get("color")})
    return meshes


def write_s3dm(path, meshes):
    chunks, items, off = [], [], 0
    for m in meshes:
        pos = struct.pack("<%df" % len(m["pos"]), *m["pos"])
        it = {"name": m["name"], "vertices": len(m["pos"]) // 3,
              "posOffset": off, "posBytes": len(pos),
              "idxOffset": -1, "idxBytes": 0, "nrmOffset": -1, "nrmBytes": 0,
              "color": m.get("color")}
        chunks.append(pos)
        off += len(pos)
        if m["idx"] and len(m["idx"]):
            idx = struct.pack("<%dI" % len(m["idx"]), *m["idx"])
            it["idxOffset"] = off
            it["idxBytes"] = len(idx)
            chunks.append(idx)
            off += len(idx)
        if m["nrm"] and len(m["nrm"]):
            nrm = struct.pack("<%df" % len(m["nrm"]), *m["nrm"])
            it["nrmOffset"] = off
            it["nrmBytes"] = len(nrm)
            chunks.append(nrm)
            off += len(nrm)
        items.append(it)
    meta = json.dumps({"meshes": items}).encode("utf-8")
    head = b"S3DM" + struct.pack("<I", len(meta))
    with open(path, "wb") as f:
        f.write(head + meta + b"".join(chunks))
    return sum(i["vertices"] for i in items)


def transform_mesh(m, mat):
    """把网格顶点/法线按 4×4 变换（法线用旋转部分，不平移）。"""
    pos = m["pos"]
    for i in range(0, len(pos), 3):
        x, y, z = pos[i], pos[i + 1], pos[i + 2]
        pos[i] = mat[0] * x + mat[1] * y + mat[2] * z + mat[3]
        pos[i + 1] = mat[4] * x + mat[5] * y + mat[6] * z + mat[7]
        pos[i + 2] = mat[8] * x + mat[9] * y + mat[10] * z + mat[11]
    if m["nrm"]:
        nr = m["nrm"]
        for i in range(0, len(nr), 3):
            x, y, z = nr[i], nr[i + 1], nr[i + 2]
            nr[i] = mat[0] * x + mat[1] * y + mat[2] * z
            nr[i + 1] = mat[4] * x + mat[5] * y + mat[6] * z
            nr[i + 2] = mat[8] * x + mat[9] * y + mat[10] * z


def bbox(meshes):
    mn = [1e30] * 3
    mx = [-1e30] * 3
    for m in meshes:
        p = m["pos"]
        for i in range(0, len(p), 3):
            for k in range(3):
                v = p[i + k]
                if v < mn[k]:
                    mn[k] = v
                if v > mx[k]:
                    mx[k] = v
    return mn, mx


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--outdir")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--flip", action="store_true", help="位姿方向反过来（用于验证）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-transform", action="store_true",
                    help="全部用单位矩阵（验证几何是否已经是全局坐标）")
    ap.add_argument("--key", default="",
                    help="例外清单里的键：默认用源文件名，线上传 fileKey")
    ap.add_argument("--exceptions", default=os.path.join(HERE, "step_exceptions.json"),
                    help="例外清单 JSON（这些零件的几何已是最终坐标，不套位姿）")
    ap.add_argument("--no-transform-for", action="append", default=[],
                    help="临时指定不套位姿的几何表示 id，可重复")
    a = ap.parse_args()

    header, ents = sp.load(a.src)
    asm = Assembly(header, ents, flip=a.flip)
    if a.no_transform:
        asm.cdsr_matrix = lambda nauo_id: mat_identity()
    occ = asm.occurrences()
    print("几何表示 %d，装配实例 %d，展开后出现 %d 次"
          % (len(asm.model.part_roots), len(asm.nauo), len(occ)))

    pts = [apply(m, [0, 0, 0]) for _, m, _ in occ]
    if pts:
        print("实例原点范围： X[%.0f, %.0f] Y[%.0f, %.0f] Z[%.0f, %.0f]"
              % (min(p[0] for p in pts), max(p[0] for p in pts),
                 min(p[1] for p in pts), max(p[1] for p in pts),
                 min(p[2] for p in pts), max(p[2] for p in pts)))
    ident = sum(1 for _, m, _ in occ if all(abs(m[i] - mat_identity()[i]) < 1e-9
                                            for i in range(16)))
    print("其中单位矩阵 %d 个（说明该实例没有自己的位姿）" % ident)
    if a.report:
        return 0

    if not a.outdir:
        print("需要 --outdir", file=sys.stderr)
        return 2
    work = os.path.join(a.outdir, "parts")
    if not os.path.isdir(work):
        os.makedirs(work)

    # 1) 每个几何表示切一份子 STEP（带产品链、不带装配实体）并三角化
    geos = []
    seen = set()
    for geo, _, _ in occ:
        if geo not in seen:
            seen.add(geo)
            geos.append(geo)
    roots_of = {g: r for g, r in asm.model.part_roots}
    cache = {}
    ok = fail = 0
    t0 = time.time()
    # 每个零件一次 node 进程，串行跑的话 114 个零件要十几分钟（实测 81.5MB 装配体
    # 13 分钟，其中 90% 是在等 node 启动+三角化）。这里按零件并发，单进程仍是
    # 单线程，靠多核把墙钟时间压下来；ASSEMBLE_WORKERS 可调，默认 6。
    work_n = max(1, int(os.environ.get("ASSEMBLE_WORKERS", "6")))

    def parse_part(k, geo):
        roots = roots_of.get(geo)
        if not roots:
            return k, geo, None, "没有产品根"
        step_p = os.path.join(work, "p%03d.step" % k)
        mesh_p = os.path.join(work, "p%03d.mesh" % k)
        sp.write_subset(step_p, header, ents,
                        asm.model.with_styles(sp.closure(ents, roots)))
        r = subprocess.run(["node", MESHER, "-o", mesh_p, step_p],
                           capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(mesh_p):
            return k, geo, None, (r.stderr or r.stdout)[-120:].strip()
        try:
            ms = read_s3dm(mesh_p)
        except SystemExit:
            return k, geo, None, "S3DM 读不出"
        if not ms:
            return k, geo, None, "空网格"
        return k, geo, ms, ""

    def report_progress(done, total):
        """把"解析到第几个零件"写进 Redis，给前端进度条用。
        每 5 个写一次：docker exec 一次约 50ms，114 个零件也就 20 多次。"""
        if not a.key:
            return
        try:
            subprocess.run(["docker", "exec", "kk-redis", "redis-cli", "-n", "3", "set",
                            "kk:step:prog:" + a.key, "%d/%d" % (done, total), "EX", "1800"],
                           capture_output=True, timeout=10)
        except Exception:
            pass

    report_progress(0, len(geos))
    with ThreadPoolExecutor(max_workers=work_n) as pool:
        futs = [pool.submit(parse_part, k, geo)
                for k, geo in enumerate(sorted(geos))]
        for f in as_completed(futs):
            k, geo, ms, err = f.result()
            if ms is None:
                fail += 1
                print("  零件 %d 解析失败: %s" % (k, err))
                continue
            cache[geo] = (k, ms)
            ok += 1
            if ok % 5 == 0 or ok == len(geos):
                report_progress(ok, len(geos))
            if ok % 20 == 0:
                print("  已解析 %d/%d 个零件（%.0f 秒）"
                      % (ok, len(geos), time.time() - t0))
    print("零件解析：成功 %d / 失败 %d，用时 %.0f 秒（并发 %d）"
          % (ok, fail, time.time() - t0, work_n))

    # 2) 按实例复制并套矩阵（例外清单里的零件用原坐标）
    skip = set()
    for x in a.no_transform_for:
        try:
            skip.add(int(x))
        except ValueError:
            pass
    if a.exceptions and os.path.exists(a.exceptions):
        try:
            cfg = json.load(open(a.exceptions, encoding="utf-8"))
            key = a.key or os.path.basename(a.src)
            ent = (cfg.get("files") or {}).get(key) or {}
            for x in ent.get("no_transform", []):
                if isinstance(x, int):
                    skip.add(x)
                elif isinstance(x, str):
                    m = re.search(r"#?([0-9]+)", x)
                    if m:
                        skip.add(int(m.group(1)))
            if skip:
                print("例外清单命中 %d 个零件（不套位姿）: %s"
                      % (len(skip), sorted(skip)))
        except Exception as exc:
            print("例外清单读取失败（忽略）: %s" % str(exc)[:100])

    out_meshes = []
    for geo, mat, name in occ:
        hit = cache.get(geo)
        if not hit:
            continue
        k, ms = hit
        for m in ms:
            nm = {"name": name or ("part%d" % k),
                  "pos": list(m["pos"]), "idx": m["idx"],
                  "nrm": list(m["nrm"]) if m["nrm"] else None,
                  "color": m.get("color")}
            if geo not in skip:
                transform_mesh(nm, mat)
            out_meshes.append(nm)
    if not out_meshes:
        print("没有可用的网格", file=sys.stderr)
        return 1

    final = os.path.join(a.outdir, "assembled.mesh")
    verts = write_s3dm(final, out_meshes)
    mn, mx = bbox(out_meshes)
    print("拼装完成：实例网格 %d 个，顶点 %d，%.2f MB"
          % (len(out_meshes), verts, os.path.getsize(final) / 1048576.0))
    print("包围盒 X[%.0f, %.0f] Y[%.0f, %.0f] Z[%.0f, %.0f]"
          % (mn[0], mx[0], mn[1], mx[1], mn[2], mx[2]))
    print(final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
