import json
import sys
import urllib.error
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


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


token = call("/api/v1/auth/token", {"appId": APP, "appSecret": SECRET})["data"]["accessToken"]
fname = sys.argv[1] if len(sys.argv) > 1 else "bom_test.xlsx"
d = call("/api/v1/preview/url",
         {"fileUrl": "http://127.0.0.1:8899/" + fname, "empNo": "EMP0001"}, token)["data"]

op = urllib.request.build_opener(NoRedirect)
r = op.open(urllib.request.Request(
    d["previewUrl"], headers={"X-App-Id": APP, "Authorization": "Bearer " + token}), timeout=120)
loc = r.headers.get("Location")
print("第一跳 ->", r.status, (loc or "")[:110])

try:
    r2 = op.open(urllib.request.Request(
        BASE + loc, headers={"X-App-Id": APP, "Authorization": "Bearer " + token}), timeout=120)
    print("第二跳 ->", r2.status, len(r2.read()), "字节")
except urllib.error.HTTPError as e:
    print("第二跳 -> HTTP", e.code)
    body = e.read().decode("utf-8", "replace")
    print("detail:", body[:600])
