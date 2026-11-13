# -*- coding: utf-8 -*-
"""从一个大 STEP 里按引用闭包切出子集，用来定位"整份解析失败但单件应该能解析"的问题。

为什么要它：X6M 兼容L460标定图.STEP（192MB，281 个实体、322 个子装配实例）用
occt-import-js 解析时返回 6708 个网格但**每个顶点都是 0** —— 结构读出来了，
三角化一个都没成。要判断是"文件里的几何本身不行"还是"整份太大"，最直接的办法
就是把其中一个零件连同它引用的实体切出来单独解析。

STEP 的实体是按 `#id = TYPE(...);` 定义的引用图，切子集 = 取闭包 + 原样输出
（id 可以跳号，标准允许）。这里一行一个实体（这份文件满足；不满足的先报错）。

用法:
    python3 step_subset.py --list /tmp/x6m.step                 # 列出可切的产品
    python3 step_subset.py --from 1234 --out /tmp/part.step /tmp/x6m.step
    python3 step_subset.py --from 1234 --from 5678 --out ... /tmp/x6m.step
"""
import argparse
import re
import sys

RE_ENTITY = re.compile(r"^#(\d+)\s*=\s*(.*?)\s*;?\s*$", re.S)
RE_REF = re.compile(r"#(\d+)")
RE_PRODUCT = re.compile(r"^#(\d+)\s*=\s*PRODUCT\s*\(\s*'([^']*)'", re.S)


def _split_entities(body):
    """把 DATA 段拆成 [(id, 正文)]。

    **不能按行切**：STEP 允许一个实体跨多行（这份 192MB 文件有约 25 万行续行，
    全是超长的 B_SPLINE_SURFACE 之类）。按行切出来的子集会丢掉续行，
    读回去报 `Incorrect Syntax` + `Unresolved Reference` —— 我第一次就是这么把
    一个本来能解析的零件切坏的。

    做法：绝大多数实体是"一行以 ; 结尾且括号配平"，走快路径；
    否则进入续行累积，按"括号深度 0 且不在引号里遇到 ;"判定结束。
    """
    out = []
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
            # 快路径：单行完整实体
            if line.endswith(";") and line.count("(") == line.count(")") \
                    and line.count("'") % 2 == 0:
                m = re.match(r"#(\d+)\s*=\s*(.*);$", line, re.S)
                if m:
                    out.append((int(m.group(1)), m.group(2).strip()))
                    continue
            buf = line
            depth = 0
            in_str = False
            start = line
        else:
            buf += "\n" + line
        # 逐字符更新括号/引号状态，找终结符
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
            m = re.match(r"#(\d+)\s*=\s*(.*?)\s*;?\s*$", buf, re.S)
            if m:
                out.append((int(m.group(1)), m.group(2).rstrip().rstrip(";").strip()))
            buf = None
    if buf is not None:
        print("警告：末尾有未闭合实体，已忽略", file=sys.stderr)
    return out


def load(path):
    """返回 (header 文本, {id: 实体正文})。支持跨行实体。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    m = re.search(r"HEADER\s*;(.*?)ENDSEC\s*;", text, re.S)
    header = m.group(1).strip() if m else ""
    body_start = text.find("DATA;")
    if body_start < 0:
        raise SystemExit("找不到 DATA 段")
    body = text[body_start + 5:]
    end = body.rfind("ENDSEC")
    if end >= 0:
        body = body[:end]
    ents = dict(_split_entities(body))
    return header, ents


def closure(ents, roots):
    seen, stack = set(), [r for r in roots if r in ents]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for ref in RE_REF.findall(ents[cur]):
            r = int(ref)
            if r in ents and r not in seen:
                stack.append(r)
    return seen


def write(path, header, ents, ids):
    with open(path, "w", encoding="utf-8") as f:
        f.write("ISO-10303-21;\nHEADER;\n")
        f.write(header + "\n")
        f.write("ENDSEC;\nDATA;\n")
        for i in sorted(ids):
            f.write("#%d=%s;\n" % (i, ents[i]))
        f.write("ENDSEC;\nEND-ISO-10303-21;\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--from", dest="roots", action="append", type=int, default=[])
    ap.add_argument("--out")
    a = ap.parse_args()

    header, ents = load(a.src)
    print("实体总数 %d，header %d 字符" % (len(ents), len(header)))

    if a.list:
        # 产品名 + 每个产品对应的 shape 表示（用于切子集）
        prods = {}
        for i, body in ents.items():
            m = RE_PRODUCT.match("#%d=%s" % (i, body))
            if m:
                prods[i] = m.group(2)
        print("产品 %d 个，前 15 个：" % len(prods))
        for i, (pid, name) in enumerate(sorted(prods.items())):
            if i >= 15:
                break
            print("  #%d %s" % (pid, name[:40]))
        return 0

    if not a.roots or not a.out:
        print("需要 --from <id> 和 --out <文件>", file=sys.stderr)
        return 2
    ids = closure(ents, a.roots)
    if not ids:
        print("闭包为空，检查 id 是否存在", file=sys.stderr)
        return 2
    write(a.out, header, ents, ids)
    import os
    print("子集 %d 个实体 → %s（%.2f MB）"
          % (len(ids), a.out, os.path.getsize(a.out) / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
