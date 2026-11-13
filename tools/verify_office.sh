#!/usr/bin/env bash
# 验证 Office 文件真实转码: 申请 -> 轮询 -> 打开预览页
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

BASE="http://127.0.0.1:${GATEWAY_PORT}"
APP_ID="kk-demo"
SECRET="your-app-secret"
FILE="${1:-http://${HOST_IP}:8899/real.docx}"

TOKEN=$(curl -s -X POST "$BASE/api/v1/auth/token" -H 'Content-Type: application/json' \
  -d "{\"appId\":\"$APP_ID\",\"appSecret\":\"$SECRET\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['accessToken'])")

echo "文件: $FILE"
RESP=$(curl -s -X POST "$BASE/api/v1/preview/url" \
  -H "X-App-Id: $APP_ID" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"fileUrl\":\"$FILE\",\"empNo\":\"EMP0001\",\"empName\":\"转码验证\"}")
echo "申请结果: $(echo "$RESP" | head -c 260)"

PV=$(echo "$RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)['data']
print(d.get('previewUrl','') or '')
")
if [ -n "$PV" ]; then
  echo "已就绪(缓存命中), 直接打开"
else
  TASK=$(echo "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['taskId'])")
  echo "任务ID: $TASK  开始轮询..."
  for i in $(seq 1 40); do
    sleep 3
    ST=$(curl -s -H "X-App-Id: $APP_ID" -H "Authorization: Bearer $TOKEN" \
         "$BASE/api/v1/preview/status?taskId=$TASK")
    STATUS=$(echo "$ST" | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['status'])")
    printf "  %2ds 状态=%s\n" $((i*3)) "$STATUS"
    if [ "$STATUS" = "ready" ]; then
      PV=$(echo "$ST" | python3 -c "import sys,json;print(json.load(sys.stdin)['data'].get('previewUrl',''))")
      break
    fi
    if [ "$STATUS" = "failed" ]; then echo "$ST"; exit 1; fi
  done
fi

if [ -z "$PV" ]; then echo "未获得预览地址"; exit 1; fi
echo "打开预览页..."
curl -s -c /tmp/cj2 -b /tmp/cj2 -L -o /tmp/office.html -w 'HTTP=%{http_code} size=%{size_download}\n' "$PV"
echo "页面特征: $(grep -o -m3 'pdf\.js\|viewer\.html\|pdfHandler\|iframe' /tmp/office.html | tr '\n' ' ')"
grep -o -m1 'src="[^"]*\.pdf"' /tmp/office.html | head -2
echo "完成"
