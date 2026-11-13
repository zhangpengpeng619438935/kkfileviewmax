#!/usr/bin/env bash
# kkFileView 预览服务 · 一键部署
# 用法: bash deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "===== 1/6 环境检查 ====="
command -v docker >/dev/null 2>&1 || { echo "未检测到 docker"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "未检测到 docker compose"; exit 1; }
echo "docker: $(docker --version | head -1)"

echo "===== 2/6 配置文件 ====="
if [ ! -f .env ]; then
  cp .env.example .env
  echo "已生成 .env (默认配置可直接使用, 生产请修改口令)"
fi
set -a; . ./.env; set +a

echo "===== 3/6 引擎镜像 ====="
if ! docker image inspect "${KK_IMAGE}" >/dev/null 2>&1; then
  echo "本地无 ${KK_IMAGE}, 从镜像源拉取 ..."
  docker pull docker.1ms.run/keking/kkfileview:latest
  docker tag docker.1ms.run/keking/kkfileview:latest "${KK_IMAGE}"
fi
docker images | grep -i kkfileview | head -3

echo "===== 4/6 启动服务 ====="
mkdir -p data/mysql data/redis data/kkfileview logs
docker compose up -d --build

echo "===== 5/6 初始化数据库 ====="
for i in $(seq 1 40); do
  if docker exec kk-mysql mysqladmin ping -uroot -p"${MYSQL_ROOT_PASSWORD}" --silent >/dev/null 2>&1; then
    echo "MySQL 已就绪"; break
  fi
  sleep 3
done
docker exec -i kk-mysql mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" \
  --default-character-set=utf8mb4 < sql/001_schema.sql
docker exec -i kk-mysql mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" \
  --default-character-set=utf8mb4 < sql/002_init_data.sql
echo "统计库与账号初始化完成"

echo "===== 6/6 健康检查 ====="
ok=0
for i in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:${GATEWAY_PORT}/health" >/dev/null 2>&1; then
    echo "网关健康: $(curl -s http://127.0.0.1:${GATEWAY_PORT}/health)"; ok=1; break
  fi
  sleep 3
done
[ "$ok" = "1" ] || { echo "网关未就绪, 查看日志: docker compose logs kk-gateway"; exit 1; }

echo
echo "部署完成:"
echo "  统计看板   http://${HOST_IP}:${GATEWAY_PORT}/"
echo "  API 基址   http://${HOST_IP}:${GATEWAY_PORT}/api/v1"
echo "  MySQL      ${HOST_IP}:${MYSQL_HOST_PORT} (用户 ${MYSQL_USER})"
echo "  预览引擎   容器内网 kkfileview:8012 (未对外暴露)"
echo
echo "下一步: bash smoke_test.sh   # 冒烟测试"
