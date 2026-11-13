"""验证表格模式的"提前放弃"预检: 过重/过慢的文件应直接走 PDF, 而不是先解析一轮。

用法: python3 tools/test_sheet_guard.py
检查项:
  1. 轻量文件(bom_test.xlsx 95KB)          -> 表格模式 /sheet/...
  2. 超重文件(APQP 7.4MB, 超 6MB 阈值)      -> 直接 PDF  /kk/onlinePreview
  3. 超重文件被记住(Redis kk:pdfpref)       -> 再次预览直接 PDF, 不再解析
  4. 任务表里的状态与耗时
"""
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
APP, SECRET = "kk-demo", "your-app-secret"
SAMPLES = "/opt/kkfileview-service/samples"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def call(path, payload=None, token=None, timeout=180):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": APP}
    if token:
        h["Authorization"] = "Bearer " + token
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(BASE + path, data=data, headers=h), timeout=timeout).read())


def final_target(preview_url, token):
    """跟随预览地址拿到最终落点: /sheet/=表格模式, /kk/onlinePreview=PDF 模式"""
    op = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(preview_url, headers={
        "X-App-Id": APP, "Authorization": "Bearer " + token})
    try:
        r = op.open(req, timeout=180)
        return r.headers.get("Location", "") or r.url, r.status
    except urllib.error.HTTPError as e:
        return (e.headers.get("Location", "") if e.headers else ""), e.code


def redis_keys():
    out = subprocess.run(
        ["docker", "exec", "kk-redis", "redis-cli", "-n", "3", "--scan",
         "--pattern", "kk:pdfpref:*"], capture_output=True, text=True).stdout
    return [k for k in out.split() if k]


def fresh(token, fname, label, expect):
    """清掉该文件的历史, 走一次完整流程。"""
    subprocess.run(["docker", "exec", "kk-gateway", "sh", "-c", "rm -f /app/sheets/*.html"],
                   capture_output=True)
    t0 = time.time()
    d = call("/api/v1/preview/url",
             {"fileUrl": "http://127.0.0.1:8899/" + urllib.parse.quote(fname),
              "empNo": "EMP0001"}, token)["data"]
    cost = time.time() - t0
    loc, code = final_target(d.get("previewUrl", ""), token)
    mode = ("表格" if "/sheet/" in loc else
            "PDF" if "/kk/onlinePreview" in loc else "?")
    ok = (mode == expect)
    print("  [%s] %-16s %8.1fKB -> %-4s (期望 %s)  接口 %.2fs"
          % ("OK" if ok else "!!", label,
             os.path.getsize(os.path.join(SAMPLES, fname)) / 1024,
             mode, expect, cost))
    return ok, mode


def main():
    token = call("/api/v1/auth/token",
                 {"appId": APP, "appSecret": SECRET})["data"]["accessToken"]
    # 清空历史, 保证是首次预览
    subprocess.run(["docker", "exec", "kk-redis", "redis-cli", "-n", "3", "flushdb"],
                   capture_output=True)
    subprocess.run(["docker", "exec", "kk-mysql", "mysql", "-uroot", "-pchange-me-root-password",
                    "kkfileview", "-e", "DELETE FROM preview_task"],
                   capture_output=True)

    light = "bom_test.xlsx" if os.path.exists(os.path.join(SAMPLES, "bom_test.xlsx")) else "real.xlsx"
    heavy = None
    for f in os.listdir(SAMPLES):
        if "X6M" in f or "APQP" in f.upper():
            heavy = f
            break

    print("=== 1. 首次预览: 轻量 vs 超重 ===")
    ok1, _ = fresh(token, light, "轻量文件", "表格")
    ok2, mode2 = fresh(token, heavy, "超重文件", "PDF") if heavy else (True, "-")
    if not heavy:
        print("  [--] samples 里没找到 APQP 大文件, 跳过超重用例")

    print()
    print("=== 2. 超重文件是否被记住 ===")
    keys = redis_keys()
    ok3 = bool(keys) if heavy else True
    print("  [%s] Redis 降级记忆: %d 条 %s" % ("OK" if ok3 else "!!", len(keys),
                                               "(7 天内同名文件直接走 PDF)" if keys else ""))

    print()
    print("=== 3. 再次预览超重文件应直接 PDF(不再解析一轮) ===")
    ok4 = True
    if heavy:
        t0 = time.time()
        d = call("/api/v1/preview/url",
                 {"fileUrl": "http://127.0.0.1:8899/" + urllib.parse.quote(heavy),
                  "empNo": "EMP0001"}, token)["data"]
        loc, _ = final_target(d.get("previewUrl", ""), token)
        mode = "表格" if "/sheet/" in loc else ("PDF" if "/kk/onlinePreview" in loc else "?")
        ok4 = (mode == "PDF")
        print("  [%s] 再次预览 -> %s  接口 %.2fs(命中记忆, 应明显更快)"
              % ("OK" if ok4 else "!!", mode, time.time() - t0))

    print()
    print("=== 4. 任务表状态 ===")
    out = subprocess.run(
        ["docker", "exec", "kk-mysql", "mysql", "-uroot", "-pchange-me-root-password",
         "--default-character-set=utf8mb4", "kkfileview", "-e",
         "SELECT LEFT(file_name,24) file, status, LEFT(err_msg,46) err, "
         "TIMESTAMPDIFF(SECOND, created_at, IFNULL(ready_at, NOW())) sec "
         "FROM preview_task ORDER BY created_at DESC LIMIT 5"],
        capture_output=True, text=True).stdout
    print("  " + "\n  ".join(l for l in out.splitlines() if l and "file_name" not in l and "Warning" not in l))

    ok = ok1 and ok2 and ok3 and ok4
    print()
    print("结论:", "全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
