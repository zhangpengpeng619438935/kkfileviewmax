#!/usr/bin/env bash
# 灌入一批干净的演示数据(多工号 + 多文件类型), 用于看板验证
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

BASE="http://127.0.0.1:${GATEWAY_PORT}"
APP_ID="kk-demo"
SECRET="your-app-secret"

# 确认样例文件服务在运行
curl -s --max-time 3 "http://${HOST_IP}:8899/hello.txt" >/dev/null 2>&1 || \
  (cd samples && nohup python3 -m http.server 8899 --bind 0.0.0.0 >/dev/null 2>&1 &)
sleep 2

TOKEN=$(curl -s -X POST "$BASE/api/v1/auth/token" -H 'Content-Type: application/json' \
  -d "{\"appId\":\"$APP_ID\",\"appSecret\":\"$SECRET\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['accessToken'])")

i=0
for f in hello.txt real.pdf real.xlsx real.pptx real.docx; do
  i=$((i+1))
  emp=$(printf "E%04d" $i)
  curl -s -o /dev/null -X POST "$BASE/api/v1/preview/url" \
    -H "X-App-Id: $APP_ID" -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{\"fileUrl\":\"http://${HOST_IP}:8899/${f}\",\"empNo\":\"${emp}\",\"empName\":\"演示用户${i}\",\"dept\":\"示例事业部\"}"
  echo "已请求: $f  工号: $emp"
done

# 再模拟几个不同系统的调用
for app in oa plm kb; do
  T=$(curl -s -X POST "$BASE/api/v1/auth/token" -H 'Content-Type: application/json' \
    -d "{\"appId\":\"${app}\",\"appSecret\":\"${app}-secret\"}" \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['accessToken'])")
  for n in 1 2 3; do
    curl -s -o /dev/null -X POST "$BASE/api/v1/preview/url" \
      -H "X-App-Id: ${app}" -H "Authorization: Bearer $T" \
      -H 'Content-Type: application/json' \
      -d "{\"fileUrl\":\"http://${HOST_IP}:8899/real.docx\",\"empNo\":\"E20${n}\",\"empName\":\"用户${n}\",\"dept\":\"研发\"}"
  done
  echo "系统 ${app} 已产生 3 次调用"
done

echo "触发聚合..."
curl -s -X POST -H "X-Admin-Token: ${ADMIN_TOKEN}" "$BASE/api/v1/admin/aggregate"; echo
echo "完成"
