#!/usr/bin/env bash
# 验证内嵌预览(iframe)场景: 模拟"浏览器不发送第三方 Cookie"的跨域 iframe 请求
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

BASE="http://127.0.0.1:${GATEWAY_PORT}"
FILE="http://${HOST_IP}:8899/real.docx"

TOKEN=$(curl -s -X POST "$BASE/api/v1/auth/token" -H 'Content-Type: application/json' \
  -d '{"appId":"kk-demo","appSecret":"your-app-secret"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['accessToken'])")

PV=$(curl -s -X POST "$BASE/api/v1/preview/url" \
  -H 'X-App-Id: kk-demo' -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"fileUrl\":\"$FILE\",\"empNo\":\"EMP0001\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['previewUrl'])")

KKT=$(python3 -c "
import sys, urllib.parse as u
q = u.urlparse('$PV').query
print(u.parse_qs(q).get('token', [''])[0])")
ENC=$(python3 -c "
import base64, urllib.parse
print(urllib.parse.quote_plus(base64.b64encode('$FILE'.encode()).decode()))")

echo "=== 1. /preview/page 跳转 Location 是否带 kkt ==="
LOC=$(curl -s -o /dev/null -D - "$PV" | grep -i '^location:' | tr -d '\r')
echo "$LOC" | cut -c1-120
echo "$LOC" | grep -q 'kkt=' && echo "  [通过] 令牌已写入 URL" || echo "  [失败] URL 未带令牌"

echo "=== 2. 无 Cookie + URL 带 kkt（跨域 iframe 场景，应 200）==="
code=$(curl -s -o /tmp/emb.html -w '%{http_code}' "$BASE/kk/onlinePreview?url=$ENC&kkt=$KKT")
echo "  HTTP $code，页面 $(wc -c </tmp/emb.html) 字节"
[ "$code" = "200" ] && echo "  [通过] 不依赖 Cookie 也能打开" || echo "  [失败]"

echo "=== 3. URL 不带 kkt、但 Referer 带 kkt（iframe 内子请求，应 200）==="
code=$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Referer: $BASE/kk/onlinePreview?url=$ENC&kkt=$KKT" \
  "$BASE/kk/onlinePreview?url=$ENC")
echo "  HTTP $code"
[ "$code" = "200" ] && echo "  [通过] Referer 兜底生效" || echo "  [失败]"

echo "=== 4. 无 kkt、无 Referer（应 401，防盗链）==="
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/kk/onlinePreview?url=$ENC")
echo "  HTTP $code"
[ "$code" = "401" ] && echo "  [通过] 未授权被拦截" || echo "  [失败]"

echo "=== 5. 文件流 getCorsFile（iframe 内发起，应放行）==="
code=$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Referer: $BASE/kk/onlinePreview?url=$ENC&kkt=$KKT" \
  "$BASE/kk/getCorsFile?urlPath=$ENC")
echo "  HTTP $code"
[ "$code" != "401" ] && echo "  [通过] 文件流可读取" || echo "  [失败]"

echo "=== 6. 响应头是否允许被 iframe 嵌入 ==="
hdr=$(curl -s -o /dev/null -D - "$BASE/kk/onlinePreview?url=$ENC&kkt=$KKT" | grep -i 'x-frame-options\|content-security-policy' | tr -d '\r')
if [ -z "$hdr" ]; then echo "  [通过] 未设置 X-Frame-Options / CSP，可被内嵌"; else echo "  [注意] $hdr"; fi

echo "=== 7. 静态资源（无令牌，应放行）==="
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/kk/js/jquery.min.js")
echo "  js 资源 HTTP $code"
