#!/usr/bin/env bash
# 冒烟测试: 登录 -> 取预览地址 -> 打开预览页 -> 查统计
set -uo pipefail
cd "$(dirname "$0")"
set -a; . ./.env; set +a

BASE="http://127.0.0.1:${GATEWAY_PORT}"
APP_ID="kk-demo"
APP_SECRET="your-app-secret"
EMP_NO="EMP0001"

echo "===== 1. 健康检查 ====="
curl -fsS "$BASE/health" | head -c 300; echo

echo "===== 2. 获取令牌 ====="
TOKEN=$(curl -fsS -X POST "$BASE/api/v1/auth/token" \
  -H 'Content-Type: application/json' \
  -d "{\"appId\":\"$APP_ID\",\"appSecret\":\"$APP_SECRET\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['accessToken'])")
echo "token 获取成功: ${TOKEN:0:24}..."

echo "===== 3. 准备样例文件 ====="
mkdir -p samples
if [ ! -f samples/hello.txt ]; then
  echo "示例公司 示例知识库 预览服务冒烟测试 - 中文显示测试" > samples/hello.txt
fi
# 从受控文件目录拷贝真实 Office 样例(若存在)
copy_sample() {
  src=$(find /cephfs_data -maxdepth 8 -iname "*.$1" -not -path "*旧版本*" 2>/dev/null | head -1)
  if [ -n "$src" ]; then cp "$src" "samples/real.$1" && echo "样例 $1: $(basename "$src")"; fi
}
copy_sample docx; copy_sample xlsx; copy_sample pdf; copy_sample pptx

# 起一个临时文件服务, 让引擎可以拉取文件
pkill -f "http.server 8899" 2>/dev/null || true
(cd samples && nohup python3 -m http.server 8899 --bind 0.0.0.0 >/tmp/kk-samples.log 2>&1 &)
sleep 2
FILE_URL="http://${HOST_IP}:8899/hello.txt"
echo "文件服务: $FILE_URL"

echo "===== 4. 申请预览地址 ====="
RESP=$(curl -fsS -X POST "$BASE/api/v1/preview/url" \
  -H "X-App-Id: $APP_ID" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"fileUrl\":\"$FILE_URL\",\"empNo\":\"$EMP_NO\",\"empName\":\"冒烟测试\"}")
echo "$RESP" | head -c 400; echo
PV=$(echo "$RESP" | python3 -c "import sys,json;d=json.load(sys.stdin)['data'];print(d.get('previewUrl',''))")

echo "===== 5. 打开预览页 ====="
if [ -n "$PV" ]; then
  CODE=$(curl -s -o /tmp/kk-preview.html -w '%{http_code}' -L "$PV")
  echo "预览页 HTTP $CODE, 大小 $(wc -c </tmp/kk-preview.html) 字节"
  grep -o -m1 'pdf.js\|viewer\|notSupported\|fileType' /tmp/kk-preview.html | head -2
fi

echo "===== 6. 统计接口 ====="
curl -fsS -H "X-App-Id: $APP_ID" -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/stats/summary?range=day" | head -c 300; echo
curl -fsS -H "X-App-Id: $APP_ID" -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/audit/logs?size=3" | head -c 400; echo

echo "===== 7. 服务状态 ====="
docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
echo
echo "看板地址: http://${HOST_IP}:${GATEWAY_PORT}/"
