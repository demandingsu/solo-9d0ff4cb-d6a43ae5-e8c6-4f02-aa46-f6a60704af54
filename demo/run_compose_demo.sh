#!/usr/bin/env bash
# Docker Compose 离线演示：API + 可切换批次/详情故障的本地示例站。
# 用法: ./demo/run_compose_demo.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== 清理旧容器与数据卷 =="
docker compose down -v --remove-orphans >/dev/null 2>&1 || true

echo "== 构建并启动 API 与示例站 =="
docker compose up -d --build

for _ in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8000/health >/dev/null \
     && curl -sf http://127.0.0.1:8080/index.html >/dev/null; then
    break
  fi
  sleep 1
done

curl -sf http://127.0.0.1:8000/health >/dev/null || {
  echo "API 启动超时，查看日志: docker compose logs api" >&2
  exit 1
}

# API 在容器内通过服务名访问示例站；宿主机通过 127.0.0.1 操作站点控制接口。
python3 -I "$ROOT/demo/flow.py" \
  --api-base http://127.0.0.1:8000 \
  --site-base http://127.0.0.1:8080 \
  --index-url http://notice-site:8080/index.html

echo
echo "演示结束。停止服务: docker compose down -v"
