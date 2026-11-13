"""诊断"PDF 为空(0 字节)"问题: 对比 源站直连 / 引擎 getCorsFile / 网关代理 三处的字节数。

用法(在网关容器内跑, 这样能直连引擎):
  docker exec -i kk-gateway python3 - < tools/diag_pdf_empty.py
"""
import base64
import sys
import urllib.parse
import urllib.request

ENGINE = "http://kkfileview:8012"
# 取自网关代理日志里最近一次 getCorsFile 的 urlPath
URL_PATH = ("aHR0cDovLzEwLjYuNi4xNjU6ODA4MC9hcGkvZmlsZS9yYXcvSDU2Q"
            "%2BmhueebrlBDQkEgUFBBUOWuoeaguOaKpeWRiijln4Pnp5HmgJ0pLnBkZj9wYXRoPTEtMSDmioDmnK"
            "%2Fmlofku7YvMDEg5oqA5pyv5paH5Lu2LzA3IOS%2Bm%2BW6lOWVhlBQQVDotYTmlpkvQy1IVUQtIEFS"
            "MDIwQSDlsprlm75INTZDIFBQQVAvSDU2Q%2BmhueebrlBQQVDlrqHmoLgvSDU2Q%2BmhueebrlBDQkEg"
            "UFBBUOWuoeaguOaKpeWRiijln4Pnp5HmgJ0pLnBkZiZleHA9MTc5MjQ5OTk0MyZzaWc9NjY1ODk5MGIw"
            "MDI2YTEwM2Q5MWM4M2FiYWJlYWY4MDk%3D")


def get(url, label, timeout=120):
    try:
        r = urllib.request.urlopen(url, timeout=timeout)
        body = r.read()
        print("%-26s HTTP %s  %10d 字节  content-type=%s"
              % (label, r.status, len(body), r.headers.get("content-type", "-")))
        return body
    except Exception as exc:
        print("%-26s 失败: %s" % (label, exc))
        return b""


# 1) 还原源站地址
b64 = urllib.parse.unquote(URL_PATH)
try:
    src = base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-8")
except Exception as exc:
    print("base64 解码失败:", exc)
    sys.exit(1)
print("源站地址:", src[:150], "...\n")

# 2) 三处对比
get(src, "① 源站直连")
get(ENGINE + "/getCorsFile?urlPath=" + URL_PATH, "② 引擎 getCorsFile")
get("http://127.0.0.1:9012/kk/getCorsFile?urlPath=" + URL_PATH, "③ 网关代理 /kk/getCorsFile")

# 3) 引擎侧缓存目录里是否落了 0 字节文件
print()
print("提示: 若②/③返回 0 字节而①正常, 说明引擎下载源站失败(通常是签名/编码问题)。")
