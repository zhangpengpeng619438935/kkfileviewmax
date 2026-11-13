#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「缓存 30 分钟有效期」: 把产物 mtime 改到 40 分钟前, 再打开文件,
应看到网关删掉旧产物、重新下载源文件并重新转换。

用法: python test_cache_ttl.py <文件名>
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
# 需要改动服务端产物 mtime 时的远程执行入口, 按实际环境指定
SSH_TOOL = os.environ.get("SSH_RUN", "ssh_run.py")
PY = os.environ.get("PYTHON", sys.executable)

name = sys.argv[1] if len(sys.argv) > 1 else "legacy113.ppt"
url = "http://127.0.0.1:8899/" + urllib.parse.quote(name, safe="")


def post(path, payload, token=None, timeout=300):
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, json.dumps(payload).encode(), h)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def remote(cmd):
    out = subprocess.run([PY, SSH_TOOL, "--sudo", cmd],
                         capture_output=True, text=True, encoding="utf-8")
    return (out.stdout or "") + (out.stderr or "")


tk = post("/api/v1/auth/token",
          {"appId": "kk-demo", "appSecret": "your-app-secret"}
          )["data"]["accessToken"]

d = post("/api/v1/preview/url",
         {"fileUrl": url, "empNo": "EMP0001", "empName": "缓存验证"}, tk)["data"]
fk = d["fileKey"]
pdf = "/opt/kkfileview-service/data/gateway/sheets/%s.pdf" % fk
sh = "/opt/kkfileview-service/data/gateway/sheets/%s.html" % fk

info = remote("stat -c '%%Y %%n' %s %s 2>/dev/null; date +%%s"
              % (pdf, sh)).strip()
print("[基线] 产物时间戳与当前时间:\n  " + info.replace("\n", "\n  "))

# 把产物改到 40 分钟前（模拟"30 分钟有效期已过"）
print("[准备] 把产物 mtime 改到 40 分钟前:")
print("  " + remote("touch -d '40 minutes ago' %s %s 2>/dev/null; "
                     "stat -c '%%y %%n' %s %s 2>/dev/null" % (pdf, sh, pdf, sh)).strip())

t0 = time.time()
d2 = post("/api/v1/preview/url",
          {"fileUrl": url, "empNo": "EMP0001", "empName": "缓存验证"}, tk)["data"]
print("[打开] status=%s provisional=%s %.2fs"
      % (d2.get("status"), d2.get("provisional"), time.time() - t0))

time.sleep(1.0)
print("[日志] 失效与重建记录:")
print("  " + remote("docker logs kk-gateway --since 2m 2>&1 | grep -E "
                     "'预览缓存已失效|本地转换完成|PPT 完整版' | tail -4")
      .strip().replace("\n", "\n  "))

for i in range(60):
    time.sleep(1)
    st = remote("stat -c '%%Y' %s 2>/dev/null" % pdf).strip()
    if st:
        print("[结果] 新 PDF mtime=%s (%.0fs 后), 与基线不同即代表重新转换过" % (st, i + 1))
        break
else:
    print("[结果] 60 秒内没有生成新 PDF")
