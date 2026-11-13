# -*- coding: utf-8 -*-
"""把超大 STEP 按零件切成若干块，让 OCCT 分块三角化后再拼回来。

背景（2026-11-05）：X6M 兼容L460标定图.STEP 192MB / 203 万实体 / 281 个实体 /
322 个子装配实例，occt-import-js 一次读整份时 `success=true`、返回 6708 个网格节点，
但每个节点都是 **0 顶点** —— 三角化整体失败且不报错。拆到零件级单独解析则全部正常
（最大一件 49MB 也出了 5.4 万顶点）。所以做法是：切成若干块 → 逐块解析 → 合并。

关键点：**不手工算变换矩阵**。每个块里除了零件几何，还带上装配关系实体
（`NEXT_ASSEMBLY_USAGE_OCCURRENCE` / `CONTEXT_DEPENDENT_SHAPE_REPRESENTATION` /
`ITEM_DEFINED_TRANSFORMATION`），OCCT 读的时候自己会把实例摆到该在的位置。

STEP 实体可以跨行（这份文件约 25 万行是续行），所以解析必须按"括号深度 0 且不在
引号里遇到 `;`"判断实体结束，不能按行切 —— 按行切出来的子集会报
`Incorrect Syntax` + `Unresolved Reference`。

用法:
    python3 step_split.py --plan      big.step                 # 只看怎么切
    python3 step_split.py --outdir /tmp/k /tmp/x.step          # 切出来
    python3 step_split.py --outdir /tmp/k --chunk-mb 30 x.step
"""
import argparse
import os
import re
import sys

GEOM_REPS = (
    "ADVANCED_BREP_SHAPE_REPRESENTATION",
    "MANIFOLD_SURFACE_SHAPE_REPRESENTATION",
    "FACETED_BREP_SHAPE_REPRESENTATION",
    "GEOMETRICALLY_BOUNDED_SURFACE_SHAPE_REPRESENTATION",
)
RE_ID = re.compile(r"#(\d+)")


def _iter_entities(body):
    """按 STEP 语法切实体（支持跨行、忽略字符串里的分号）。"""
    buf = None
    depth = 0
    in_str = False
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if buf is None:
            if not line.startswith("#"):
                continue
            if line.endswith(";") and line.count("(") == line.count(")") \
                    and line.count("'") % 2 == 0:
                m = re.match(r"#(\d+)\s*=\s*(.*);$", line, re.S)
                if m:
                    yield int(m.group(1)), m.group(2).strip()
                    continue
            buf = line
            depth, in_str = 0, False
        else:
            buf += "\n" + line
        done = False
        for ch in line:
            if in_str:
                if ch == "'":
                    in_str = False
                continue
            if ch == "'":
                in_str = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == ";" and depth <= 0:
                done = True
                break
        if done:
            m = re.match(r"#(\d+)\s*=\s*(.*?)\s*;?$", buf, re.S)
            if m:
                yield int(m.group(1)), m.group(2).rstrip().rstrip(";").strip()
            buf = None


def load(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    m = re.search(r"HEADER\s*;(.*?)ENDSEC\s*;", text, re.S)
    header = m.group(1).strip() if m else ""
    i = text.find("DATA;")
    if i < 0:
        raise SystemExit("找不到 DATA 段: %s" % path)
    body = text[i + 5:]
    j = body.rfind("ENDSEC")
    if j >= 0:
        body = body[:j]
    return header, dict(_iter_entities(body))


def refs(body):
    return [int(x) for x in RE_ID.findall(body)]


def closure(ents, roots):
    seen, stack = set(), [r for r in roots if r in ents]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for r in refs(ents[cur]):
            if r in ents and r not in seen:
                stack.append(r)
    return seen


def write_subset(path, header, ents, ids):
    with open(path, "w", encoding="utf-8") as f:
        f.write("ISO-10303-21;\nHEADER;\n%s\nENDSEC;\nDATA;\n" % header)
        for i in sorted(ids):
            f.write("#%d=%s;\n" % (i, ents[i]))
        f.write("ENDSEC;\nEND-ISO-10303-21;\n")


class Model(object):
    """把切块需要的索引都建好，避免每次线性扫全表。"""

    def __init__(self, header, ents):
        self.header = header
        self.ents = ents
        self.referers = {}          # id -> [引用它的实体 id]
        for i, body in ents.items():
            for r in refs(body):
                self.referers.setdefault(r, []).append(i)

        # 几何表示 → 它的产品表示（通过 SHAPE_REPRESENTATION_RELATIONSHIP 连起来）
        self.rel_of_rep = {}
        self.rep_of_sdr = {}
        self.part_roots = []        # [(几何表示 id, [根实体])]
        for i, body in ents.items():
            head = body.split("(", 1)[0].strip()
            if head == "SHAPE_REPRESENTATION_RELATIONSHIP":
                rr = refs(body)
                if len(rr) >= 2:
                    self.rel_of_rep.setdefault(rr[-1], i)
                    self.rel_of_rep.setdefault(rr[-2], i)
            elif head == "SHAPE_DEFINITION_REPRESENTATION":
                rr = refs(body)
                if rr:
                    self.rep_of_sdr.setdefault(rr[-1], i)

        for i, body in ents.items():
            head = body.split("(", 1)[0].strip()
            if head not in GEOM_REPS:
                continue
            roots = [i]
            rel = self.rel_of_rep.get(i)
            if rel:
                roots.append(rel)
                # 关系实体的另一端是产品表示，把它的 SDR 也带上（产品链 + 单位上下文）
                for o in refs(ents[rel]):
                    if o != i and o in self.rep_of_sdr:
                        roots.append(self.rep_of_sdr[o])
                        break
            self.part_roots.append((i, roots))

        # 颜色/材质是通过 STYLED_ITEM 挂上去的，它引用几何而不是被几何引用，
        # 所以纯引用闭包会把它漏掉 —— 切件后模型就没有颜色了。
        self.styled_by_item = {}    # 被着色的实体 id → [STYLED_ITEM id]
        for i, body in ents.items():
            if body.split("(", 1)[0].strip() == "STYLED_ITEM":
                rr = refs(body)
                if rr:
                    self.styled_by_item.setdefault(rr[-1], []).append(i)

        # 实例：NAUO → 子产品定义；算位姿的实体一起带上
        self.nauo = []              # [(nauo_id, parent_pd, child_pd)]
        for i, body in ents.items():
            if body.split("(", 1)[0].strip() == "NEXT_ASSEMBLY_USAGE_OCCURRENCE":
                rr = refs(body)
                if len(rr) >= 2:
                    self.nauo.append((i, rr[-2], rr[-1]))
        self.pd_shape_of = {}       # PRODUCT_DEFINITION_SHAPE → PRODUCT_DEFINITION
        for i, body in ents.items():
            if body.split("(", 1)[0].strip() == "PRODUCT_DEFINITION_SHAPE":
                rr = refs(body)
                if rr:
                    self.pd_shape_of[i] = rr[-1]
        # CDSR: (产品定义形状, 关系)；关系里带着 ITEM_DEFINED_TRANSFORMATION
        self.cdsr = []              # [(cdsr_id, pds_id, rel_id)]
        for i, body in ents.items():
            if body.split("(", 1)[0].strip() == "CONTEXT_DEPENDENT_SHAPE_REPRESENTATION":
                rr = refs(body)
                if len(rr) >= 2:
                    self.cdsr.append((i, rr[0], rr[1]))

        # 子产品定义 → 它的产品定义形状（用于把 NAUO 和 CDSR 对上）
        self.pds_of_pd = {}
        for pds, pd in self.pd_shape_of.items():
            self.pds_of_pd.setdefault(pd, []).append(pds)

        # 产品定义 → 几何表示（沿 PDS → SDR → 产品表示 → 关系 → 几何表示）
        self.pd_rep = {}
        for pd, pdss in self.pds_of_pd.items():
            for pds in pdss:
                for child in self.referers.get(pds, []):
                    if self.ents[child].split("(", 1)[0].strip() \
                            == "SHAPE_DEFINITION_REPRESENTATION":
                        for r in refs(self.ents[child]):
                            if r != pds and r in self.ents:
                                geo = self._geo_of_product_rep(r)
                                if geo:
                                    self.pd_rep.setdefault(pd, geo)

    def _geo_of_product_rep(self, rep):
        """产品表示 → 通过关系实体找到它对面那个"几何表示"。"""
        rel = self.rel_of_rep.get(rep)
        if not rel:
            return None
        for o in refs(self.ents[rel]):
            if o == rep:
                continue
            head = self.ents.get(o, "").split("(", 1)[0].strip()
            if head in GEOM_REPS:
                return o
        return None

    def instances_of(self, geo_rep):
        """属于某个几何表示的所有实例实体（NAUO + CDSR）。"""
        out = []
        for nauo, parent, child in self.nauo:
            if self.pd_rep.get(child) != geo_rep:
                continue
            out.append(nauo)
            # 位姿：该子件的 PRODUCT_DEFINITION_SHAPE 对应的 CDSR
            for pds in self.pds_of_pd.get(child, []):
                for cdsr, cpds, crel in self.cdsr:
                    if cpds == pds:
                        out.append(cdsr)
        return out

    def with_styles(self, ids):
        """把落在这些实体上的 STYLED_ITEM 及其颜色定义一起带进来。"""
        extra = []
        for i in ids:
            extra.extend(self.styled_by_item.get(i, []))
        if not extra:
            return ids
        return ids | closure(self.ents, extra)

    def part_size(self, roots):
        return len(closure(self.ents, roots))

    def chunk_roots(self, parts):
        roots = []
        for geo, base in parts:
            roots.extend(base)
            roots.extend(self.instances_of(geo))
        return roots


def build_plan(model, chunk_entities):
    """按实体数贪心装箱（实体数 ≈ 文件体积的可靠代理）。"""
    parts = []
    for geo, roots in model.part_roots:
        n = model.part_size(roots) + len(model.instances_of(geo))
        parts.append((n, geo, roots))
    parts.sort(key=lambda x: -x[0])

    chunks, cur, cur_n = [], [], 0
    for n, geo, roots in parts:
        if cur and cur_n + n > chunk_entities:
            chunks.append(cur)
            cur, cur_n = [], 0
        cur.append((n, geo, roots))
        cur_n += n
    if cur:
        chunks.append(cur)
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--outdir")
    ap.add_argument("--chunk-mb", type=float, default=30.0,
                    help="每块大约多少 MB（按实体数折算，默认 30）")
    ap.add_argument("--plan", action="store_true")
    a = ap.parse_args()

    header, ents = load(a.src)
    print("实体 %d，文件 %.1f MB" % (len(ents), os.path.getsize(a.src) / 1048576.0))
    model = Model(header, ents)
    print("几何表示 %d 个，装配实例 %d 个" % (len(model.part_roots), len(model.nauo)))

    # 实体数 → 字节的经验系数（这份文件约 100 字节/实体）
    bytes_per_ent = os.path.getsize(a.src) / max(1, len(ents))
    chunk_entities = int(a.chunk_mb * 1048576 / max(1.0, bytes_per_ent))
    chunks = build_plan(model, chunk_entities)
    print("切成 %d 块（每块上限 %.0f MB ≈ %d 实体）"
          % (len(chunks), a.chunk_mb, chunk_entities))
    for k, ch in enumerate(chunks):
        print("  块 %d：%d 个零件，%d 实体" % (k, len(ch), sum(c[0] for c in ch)))
    if a.plan:
        return 0

    outdir = a.outdir or "."
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    made = []
    for k, ch in enumerate(chunks):
        ids = closure(ents, model.chunk_roots([(g, r) for _, g, r in ch]))
        ids = model.with_styles(ids)
        p = os.path.join(outdir, "chunk_%02d.step" % k)
        write_subset(p, header, ents, ids)
        made.append(p)
        print("  写出 %s  %.2f MB  %d 实体"
              % (p, os.path.getsize(p) / 1048576.0, len(ids)))
    print("\n".join(made))
    return 0


if __name__ == "__main__":
    sys.exit(main())
