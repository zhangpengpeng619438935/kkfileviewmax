# -*- coding: utf-8 -*-
"""压测：热路径吞吐 + 冷转换并发（容器内跑，走本机回环）。

用法:
    python3 tools/dev/loadtest.py warm  <url> <qps> <secs> [empNo]
    python3 tools/dev/loadtest.py cold  <fileKeyPrefix> <并发数>
"""
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
APP, SECRET = "kk-demo", "your-app-secret"


def post(path, body, token=None, timeout=60):
    h = {"Content-Type": "application/json", "X-App-Id": APP}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(), h)
    t0 = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0
    except Exception:
        return 0, time.time() - t0


def token():
    h = {"Content-Type": "application/json", "X-App-Id": APP}
    req = urllib.request.Request(
        BASE + "/api/v1/auth/token",
        json.dumps({"appId": APP, "appSecret": SECRET}).encode(), h)
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["data"]["accessToken"]


def pct(xs, p):
    if not xs:
        return 0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def warm(url, qps, secs, emp="EMP900"):
    tk = token()
    print("预热一次…", post("/api/v1/preview/url", {"fileUrl": url, "empNo": emp}, tk))
    lat, codes = [], {}
    lock = threading.Lock()
    stop = time.time() + secs
    total = int(qps * secs)

    def worker(n, gap):
        for _ in range(n):
            if time.time() >= stop:
                return
            st, dt = post("/api/v1/preview/url", {"fileUrl": url, "empNo": emp}, tk)
            with lock:
                codes[st] = codes.get(st, 0) + 1
                if st == 200:
                    lat.append(dt)
            time.sleep(gap)          # 匀速：否则线程会把请求一次打光

    nthreads = max(2, min(16, qps // 2))
    per = max(1, total // nthreads)
    gap = nthreads / float(qps)
    ths = [threading.Thread(target=worker, args=(per, gap)) for _ in range(nthreads)]
    t0 = time.time()
    [t.start() for t in ths]
    [t.join() for t in ths]
    el = time.time() - t0
    ok = len(lat)
    print("目标 %d QPS × %ds → 实际 %.1f QPS，成功 %d / 总 %d"
          % (qps, secs, ok / el, ok, sum(codes.values())))
    print("状态码:", dict(sorted(codes.items(), key=lambda x: -x[1])))
    if lat:
        print("延迟 ms: p50=%.0f p95=%.0f p99=%.0f max=%.0f"
              % (pct(lat, 0.5) * 1000, pct(lat, 0.95) * 1000,
                 pct(lat, 0.99) * 1000, max(lat) * 1000))


def cold(prefix, conc, ext=".docx"):
    tk = token()
    names = ["%s%d%s" % (prefix, i + 1, ext) for i in range(conc)]
    results = []
    lock = threading.Lock()

    def one(name):
        url = "http://127.0.0.1:8899/" + urllib.parse.quote(name)
        t0 = time.time()
        # 用系统工号：12 个线程轮询同一个真实工号会撞 20 QPS，被 429 打成
        # "永远未就绪"（第一版就踩了，测出 37 秒的假结果）
        st, _ = post("/api/v1/preview/url", {"fileUrl": url, "empNo": "SYS_LOADTEST"}, tk)
        d = json.loads(b"{}")
        # 轮询到 ready
        for _ in range(120):
            h = {"Content-Type": "application/json", "X-App-Id": APP,
                 "Authorization": "Bearer " + tk}
            req = urllib.request.Request(
                BASE + "/api/v1/preview/url",
                json.dumps({"fileUrl": url, "empNo": "SYS_LOADTEST",
                            "waitSeconds": 1}).encode(), h)
            try:
                r = json.loads(urllib.request.urlopen(req, timeout=30).read())
                dd = r.get("data") or {}
                # PPT 是异步返回：status=ready 且带 provisional 时其实还没转好，
                # 必须等到 provisional 消失才算"用户能看"
                if dd.get("status") == "failed" or (
                        dd.get("status") == "ready" and not dd.get("provisional")):
                    d = r
                    break
            except Exception:
                pass
            time.sleep(1.0)
        dt = time.time() - t0
        with lock:
            results.append((name, dt, (d.get("data") or {}).get("status")))

    t0 = time.time()
    ths = [threading.Thread(target=one, args=(n,)) for n in names]
    [t.start() for t in ths]
    [t.join() for t in ths]
    el = time.time() - t0
    dts = [r[1] for r in results]
    print("%d 个互不相同的文件并发冷转换：总耗时 %.1fs" % (conc, el))
    print("单文件耗时 s: p50=%.1f p95=%.1f max=%.1f"
          % (pct(dts, 0.5), pct(dts, 0.95), max(dts)))
    print("吞吐 %.1f 个/分钟" % (conc / el * 60))
    bad = [r for r in results if r[2] != "ready"]
    if bad:
        print("未就绪:", bad[:3])


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "warm":
        warm(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]),
             sys.argv[5] if len(sys.argv) > 5 else "EMP900")
    else:
        cold(sys.argv[2], int(sys.argv[3]),
             sys.argv[4] if len(sys.argv) > 4 else ".docx")
