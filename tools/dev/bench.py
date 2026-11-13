#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""并发压测: 验证网关是否扛得住 60 并发/秒。

用法:
  python3 bench.py                    # 默认 60 并发, 持续 60 秒, 命中已转码文件
  python3 bench.py --conc 60 --dur 60 --url http://127.0.0.1:8899/hello.txt

依赖: 仅标准库。
"""
import argparse
import http.client
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections import Counter

BASE = "http://127.0.0.1:9012"
B_HOST = "127.0.0.1"
B_PORT = 9012
APP_ID = "kk-demo"
APP_SECRET = "your-app-secret"
EMP = "EMP0001"


def http_json(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_token():
    body = json.dumps({"appId": APP_ID, "appSecret": APP_SECRET}).encode()
    r = http_json(BASE + "/api/v1/auth/token", body, {"Content-Type": "application/json"})
    return r["data"]["accessToken"]


class Bench:
    def __init__(self, conc, dur, file_url):
        self.conc = conc
        self.dur = dur
        self.file_url = file_url
        self.token = get_token()
        self.costs = []
        self.errors = Counter()
        self.err_samples = []
        self.lock = threading.Lock()
        self.stop = False

    def worker(self, idx):
        """每个线程模拟一个独立用户(工号不同), 避免单工号限流影响结果。"""
        emp_no = "EMP%03d" % idx
        conn = http.client.HTTPConnection(B_HOST, B_PORT, timeout=30)
        headers = {"X-App-Id": APP_ID, "Authorization": "Bearer " + self.token,
                   "Content-Type": "application/json"}
        body = json.dumps({"fileUrl": self.file_url, "empNo": emp_no,
                           "empName": "压测"}).encode()
        while not self.stop:
            t0 = time.perf_counter()
            try:
                conn.request("POST", "/api/v1/preview/url", body=body, headers=headers)
                resp = conn.getresponse()
                resp.read()
                code = resp.status
                cost = (time.perf_counter() - t0) * 1000
                with self.lock:
                    if code == 200:
                        self.costs.append(cost)
                    else:
                        self.errors["HTTP %s" % code] += 1
            except (http.client.HTTPException, OSError) as e:
                with self.lock:
                    if len(self.err_samples) < 5:
                        self.err_samples.append("%s: %s" % (type(e).__name__, e))
                    self.errors[type(e).__name__] += 1
                try:
                    conn.close()
                    conn = http.client.HTTPConnection(B_HOST, B_PORT, timeout=30)
                except OSError:
                    pass

    def run(self):
        # 预热: 确保文件已转码, 后续请求走缓存命中路径
        headers = {"X-App-Id": APP_ID, "Authorization": "Bearer " + self.token,
                   "Content-Type": "application/json"}
        http_json(BASE + "/api/v1/preview/url",
                  json.dumps({"fileUrl": self.file_url, "empNo": EMP}).encode(), headers)
        print("预热完成, 开始压测: 并发=%s 持续=%ss" % (self.conc, self.dur))
        threads = [threading.Thread(target=self.worker, args=(i,), daemon=True)
                   for i in range(self.conc)]
        t0 = time.time()
        for t in threads:
            t.start()
        time.sleep(self.dur)
        self.stop = True
        for t in threads:
            t.join(timeout=10)
        wall = time.time() - t0
        self.report(wall)

    def report(self, wall):
        if not self.costs:
            print("无成功请求"); return
        s = sorted(self.costs)
        n = len(s)

        def pct(p):
            return s[min(n - 1, int(n * p / 100))]
        print("\n===== 压测结果 =====")
        print("完成请求    : %d" % n)
        print("耗时        : %.2f s" % wall)
        print("实际吞吐    : %.1f req/s" % (n / wall))
        print("平均延迟    : %.1f ms" % statistics.mean(s))
        print("最小延迟    : %.1f ms" % s[0])
        print("P50         : %.1f ms" % pct(50))
        print("P95         : %.1f ms" % pct(95))
        print("P99         : %.1f ms" % pct(99))
        print("最大延迟    : %.1f ms" % s[-1])
        print("错误        : %s" % (dict(self.errors) or "无"))
        if self.err_samples:
            print("错误样例    : %s" % self.err_samples[:3])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--conc", type=int, default=60)
    ap.add_argument("--dur", type=int, default=60)
    ap.add_argument("--url", default="http://127.0.0.1:8899/hello.txt")
    a = ap.parse_args()
    Bench(a.conc, a.dur, a.url).run()
