#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断: 文件源 URL 返回 403 的原因。"""
import urllib.request
import urllib.error
import re

RAW = ("http://127.0.0.1:8080/api/file/raw/3110314000169-%E8%9C%97%E6%9D%86-PPK.xls"
       "?path=1-1%20%E6%8A%80%E6%9C%AF%E6%96%87%E4%BB%B6/01%20%E6%8A%80%E6%9C%AF%E6%96%87%E4%BB%B6/"
       "07%20%E4%BE%9B%E5%BA%94%E5%95%86PPAP%E8%B5%84%E6%96%99/C-HUD-W024A%20%20%E4%B8%9C%E9%A3%8E"
       "%E4%B9%98%E7%94%A8%E8%BD%A6S73A%20PPAP/%E5%BB%B8%E7%94%9F%E5%A1%91%E8%83%B6%20%20"
       "%E8%9C%97%E6%9D%86%203110314000169/11-%E5%88%9D%E5%A7%8B%E8%BF%87%E7%A8%8B%E7%A0%94%E7%A9%B6"
       "%EF%BC%88Cpk%20&%20Ppk%EF%BC%89/3110314000169-%E8%9C%97%E6%9D%86-PPK.xls"
       "&exp=1792489834&sig=ea3104dc50181c4d08554882d8e33123")


def probe(name, url):
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
            print("[ %s ] HTTP %s, %s 字节, Content-Type=%s"
                  % (name, r.status, len(data), r.headers.get("Content-Type")))
            return r.status
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        print("[ %s ] HTTP %s  %s" % (name, e.code, body))
        return e.code
    except Exception as e:
        print("[ %s ] 错误: %s" % (name, e))
        return -1


print("=== 1. 原样请求 ===")
probe("原样", RAW)

print("\n=== 2. 把 path 参数值里的裸 & 编码成 %26 ===")
# 从 ? 之后到 &exp= 之前, 属于 path 参数值, 其中的 & 都是文件名的字面字符
m = re.search(r"^(.*\?path=)(.*?)(&exp=.*)$", RAW)
if m:
    head, path_val, tail = m.groups()
    fixed = head + path_val.replace("&", "%26") + tail
    print("    修正后的 path 值尾部: ..." + path_val.replace("&", "%26")[-60:])
    probe("修正&", fixed)
else:
    print("    未匹配到 path 参数")

print("\n=== 3. 只请求前 1 字节(HEAD 风格), 看是否通过签名 ===")
req = urllib.request.Request(RAW, method="GET")
req.add_header("Range", "bytes=0-0")
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        print("[ Range ] HTTP %s" % r.status)
except urllib.error.HTTPError as e:
    print("[ Range ] HTTP %s" % e.code)
except Exception as e:
    print("[ Range ] %s" % e)
