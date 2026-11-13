#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STEP 按需补产物的消费者，分两个阶段。

**为什么要分阶段**：270MB 的装配体三角化要十几分钟，如果线框和网格在同一个队列里
串行跑，前面一个大文件就会把后面所有文件堵住（实测用户打开 0.2MB 的小零件，
干等了 12 分钟还没动静）。

现在拆成两条队列：

    kk:step:need  --(wire 阶段)-->  提线框（2~5 秒，纯文本扫描）
                        |
                        +--> kk:step:mesh --(mesh 阶段)--> 三角化（慢）

wire 阶段每个文件几秒，一轮能过好几个；mesh 阶段一次只跑一个，慢慢排。
页面 /stepstate 见到 wire_ready 就跳 3D 页，所以用户最多等一轮 cron（1 分钟）。

**为什么不在网关容器里做**：容器里没有 node 和 occt-import-js，跑不了 step2mesh.js。

用法（宿主机）:
    python3 tools/step_worker.py --stage wire --once
    python3 tools/step_worker.py --stage mesh --once
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WIRE_Q = "kk:step:need"          # 网关入队的入口队列
MESH_Q = "kk:step:mesh"
CAD_Q = "kk:cad:vec"          # DWG 矢量渲染队列（cad_vec.mjs 消费）
SHEETS = "/opt/kkfileview-service/data/gateway/sheets"          # wire 阶段完成后转进来的网格队列
STATE = "kk:step:state:%s"       # wire / done / failed
STATE_TTL = 3600
WIRE_PER_RUN = 6                 # 线框便宜，一轮多处理几个
MESH_TIMEOUT = 2400              # 单个网格最多 40 分钟（220MB 约 15 分钟）


def redis(*args):
    r = subprocess.run(["docker", "exec", "kk-redis", "redis-cli", "-n", "3"] + list(args),
                       capture_output=True, text=True)
    return (r.stdout or "").strip()


def run_wire(fk, url):
    """只出线框，然后把这个文件推进网格队列。"""
    cmd = [sys.executable, os.path.join(HERE, "step_wire.py"), "--fk", fk]
    if url:
        cmd += ["--url", url]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        ok = p.returncode == 0
    except subprocess.TimeoutExpired:
        out, ok = "线框超时", False
    print("[wire] %s %.1fs %s %s" % (fk[:12], time.time() - t0, "ok" if ok else "fail",
                                     out[-300:].replace("\n", " | ")), flush=True)
    # 成功才写 wire；失败写 failed，等待页据此提示「源文件不可达/几何异常」，
    # 否则页面会一直显示"已加入队列"干等 8 分钟（实测 RAG 需要鉴权的地址就是这样）
    redis("set", STATE % fk, "wire" if ok else "failed", "EX", str(STATE_TTL))
    # 不管线框成没成，都排进网格队列 —— step_one 那边可能还能出实体
    redis("lpush", MESH_Q, "%s|%s" % (fk, url) if url else fk)


def run_cad(fk, url):
    """DWG → SVG 矢量渲染（cadvec/cad_vec.mjs，约 4 秒）。

    node 和 libredwg 的 WASM 包只装在宿主机，所以只能在这里跑，网关容器里没有。
    产物是 <fk>.cad.svg.gz，网关 /cadvec 路由直接回它。
    """
    script = os.path.join(HERE, "cadvec", "cad_vec.mjs")
    if not os.path.exists(script):
        print("[cad] 缺少渲染脚本 %s" % script, flush=True)
        return fail_state("kk:cad:vec:st:" + fk, "no-script")
    t0 = time.time()
    try:
        r = subprocess.run(["node", script, fk, url], capture_output=True,
                           text=True, timeout=900, cwd=os.path.dirname(script))
    except subprocess.TimeoutExpired:
        print("[cad] %s 超时" % fk[:12], flush=True)
        return fail_state("kk:cad:vec:st:" + fk, "timeout")
    ok = r.returncode == 0 and os.path.exists(
        os.path.join(SHEETS, fk + ".cad.svg.gz"))
    if ok:
        redis("set", "kk:cad:vec:st:" + fk, "ok", "EX", "86400")
        print("[cad] %s %.1fs ok %s" % (fk[:12], time.time() - t0,
                                        (r.stdout or "").strip()[:120]), flush=True)
    else:
        tail = (r.stdout or r.stderr or "").strip().splitlines()[-1:] or [""]
        redis("set", "kk:cad:vec:st:" + fk, "fail:" + tail[0][:120], "EX", "3600")
        print("[cad] %s %.1fs fail %s" % (fk[:12], time.time() - t0, tail[0][:160]), flush=True)


def fail_state(key, msg):
    redis("set", key, "fail:" + msg, "EX", "3600")


def run_mesh(fk, url):
    """三角化出网格（慢）。"""
    cmd = [sys.executable, os.path.join(HERE, "step_one.py"), "--fk", fk]
    if url:
        cmd += ["--url", url]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=MESH_TIMEOUT)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        ok = p.returncode == 0
    except subprocess.TimeoutExpired:
        out, ok = "网格超时（%d 秒）" % MESH_TIMEOUT, False
    print("[mesh] %s %.0fs %s\n%s" % (fk[:12], time.time() - t0,
                                      "ok" if ok else "fail", out[-400:]), flush=True)
    redis("set", STATE % fk, "done" if ok else "failed", "EX", str(STATE_TTL))
    return ok


def pop_blocking(q, timeout=5):
    """阻塞取一个任务。cron 每分钟才扫一次，用户点开要干等 60 秒；
    常驻进程用 BRPOP，延迟降到 ~1 秒。brpop 返回两行（队列名 + 值）。"""
    r = subprocess.run(["docker", "exec", "kk-redis", "redis-cli", "-n", "3",
                        "brpop", q, str(timeout)], capture_output=True, text=True)
    lines = [x for x in (r.stdout or "").strip().splitlines() if x]
    return lines[1] if len(lines) >= 2 else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="mesh", choices=["wire", "mesh", "cad"])
    ap.add_argument("--once", action="store_true", help="处理有限个就退出（cron 用）")
    ap.add_argument("--drain", action="store_true", help="一直处理到队列空")
    ap.add_argument("--daemon", action="store_true", help="常驻：阻塞取任务，秒级响应")
    ap.add_argument("--max-seconds", type=float, default=0,
                    help="常驻模式最多跑多少秒（cron 里给 55，跟下一分钟无缝衔接）")
    a = ap.parse_args()

    q = WIRE_Q if a.stage == "wire" else (CAD_Q if a.stage == "cad" else MESH_Q)
    limit = WIRE_PER_RUN if a.stage == "wire" else 1
    n = 0

    if a.daemon:
        budget = a.max_seconds if a.max_seconds > 0 else 86400 * 365
        deadline = time.time() + budget
        print("[%s] 常驻启动（%.0f 秒），队列 %s" % (a.stage, budget, q), flush=True)
        while time.time() < deadline:
            item = pop_blocking(q, 3)
            if not item:
                continue
            fk, _, url = item.partition("|")
            fk, url = fk.strip(), url.strip()
            if not fk:
                continue
            try:
                if a.stage == "wire":
                    run_wire(fk, url)
                elif a.stage == "cad":
                    run_cad(fk, url)
                else:
                    run_mesh(fk, url)
            except Exception as exc:
                print("[%s] 处理异常 %s: %s" % (a.stage, fk[:12], exc), flush=True)

    while True:
        if n >= limit:
            break
        item = redis("rpop", q)
        if not item:
            break
        fk, _, url = item.partition("|")
        fk, url = fk.strip(), url.strip()
        if not fk:
            continue
        n += 1
        if a.stage == "wire":
            run_wire(fk, url)
        else:
            run_mesh(fk, url)
    print("[%s] 本轮处理 %d 个" % (a.stage, n), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
