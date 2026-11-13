"""检查表格预览页: 引用资源是否可加载、降级兜底是否就位。

用法: python3 tools/check_page.py <samples 下的文件名>
"""
import json
import os
import re
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:9012"
APP, SECRET = "kk-demo", "your-app-secret"


def call(path, payload=None, token=None, timeout=180):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": APP}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def main():
    fname = sys.argv[1] if len(sys.argv) > 1 else "bom_test.xlsx"
    token = call("/api/v1/auth/token",
                 {"appId": APP, "appSecret": SECRET})["data"]["accessToken"]
    t0 = time.time()
    d = call("/api/v1/preview/url", {"fileUrl": "http://127.0.0.1:8899/" + fname,
                                     "empNo": "EMP0001"}, token)["data"]
    print("接口状态 : %s   耗时 %.2fs" % (d.get("status"), time.time() - t0))
    if not d.get("previewUrl"):
        print("未拿到预览地址:", d)
        return 1

    req = urllib.request.Request(d["previewUrl"], headers={
        "X-App-Id": APP, "Authorization": "Bearer " + token})
    resp = urllib.request.urlopen(req, timeout=120)
    final = resp.url
    page = resp.read().decode("utf-8", "replace")
    print("最终页面 : %s" % final[:90])
    print("页面字节 : %d" % len(page))
    print()

    print("--- 引用资源逐一检查 ---")
    for src in re.findall(r"""(?:src|href)=['"]([^'"]+)['"]""", page):
        if not src.startswith("/"):
            continue
        t1 = time.time()
        try:
            rr = urllib.request.urlopen(
                urllib.request.Request(BASE + src), timeout=60)
            body = rr.read()
            print("  %-44s %s  %8d 字节  %.2fs  %s"
                  % (src, rr.status, len(body), time.time() - t1,
                     rr.headers.get("Content-Encoding") or "none"))
        except Exception as exc:
            print("  %-44s 失败 %s" % (src, exc))

    print()
    print("--- 页面关键逻辑 ---")
    checks = [
        ("引入 jQuery(plugin.js)", "plugins/js/plugin.js" in page),
        ("引入 luckysheet.umd.js", "luckysheet.umd.js" in page),
        ("降级入口(手动切简易)", "btnSimple" in page),
        ("看门狗提示", "仍在初始化" in page),
        ("脚本加载失败兜底", "SCRIPT" in page),
        ("异步初始化(不阻塞首屏)", page.count("setTimeout(function") >= 3),
    ]
    for name, okv in checks:
        print("  [%s] %s" % ("OK" if okv else "!!", name))

    m = re.search(r"var DATA = (.*);", page)
    if m:
        data = json.loads(m.group(1).replace("<\\/", "</"))
        print()
        print("--- 数据结构 ---")
        for s in data:
            print("  %-24s row=%-5s column=%-4s 单元格=%-5d 合并=%d"
                  % (s["name"][:24], s.get("row"), s.get("column"),
                     len(s["celldata"]), len(s.get("config", {}).get("merge") or {})))
    print()
    print("LUCKY_URL =%s" % d["previewUrl"])
    print("SIMPLE_URL=%s?simple=1" % final.split("?")[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
