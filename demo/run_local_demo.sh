#!/usr/bin/env bash
# 本地 venv 演示（不依赖 Docker）：启动示例站 + API，跑完整收件流程。
# 用法: ./demo/run_local_demo.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
SITE_PORT="${SITE_PORT:-8080}"
API_PORT="${API_PORT:-8000}"
DB="$ROOT/demo/.local-intake.db"

if [[ ! -x "$PY" ]]; then
  echo "缺少虚拟环境：先创建 python3 -m venv .venv，并安装依赖" >&2
  echo "  .venv/bin/pip install -r app/requirements.txt" >&2
  exit 1
fi
"$PY" -c 'import fastapi, uvicorn, bs4, httpx' >/dev/null 2>&1 || {
  echo "虚拟环境缺少依赖：$PY -m pip install -r $ROOT/app/requirements.txt" >&2
  exit 1
}

rm -f "$DB" "$DB-wal" "$DB-shm"
cleanup() {
  [[ -n "${SITE_PID:-}" ]] && kill "$SITE_PID" 2>/dev/null || true
  [[ -n "${API_PID:-}" ]] && kill "$API_PID" 2>/dev/null || true
  rm -f "$DB" "$DB-wal" "$DB-shm"
}
trap cleanup EXIT

# 1) 示例公告站（纯标准库）
"$PY" -I "$ROOT/site/server.py" \
  --host 127.0.0.1 --port "$SITE_PORT" --batch v1 \
  >"$ROOT/demo/site.log" 2>&1 &
SITE_PID=$!

# 2) 收件 API
(
  cd "$ROOT/app"
  INTAKE_DB_PATH="$DB" "$PY" -I -m uvicorn app.main:app \
    --host 127.0.0.1 --port "$API_PORT"
) >"$ROOT/demo/api.log" 2>&1 &
API_PID=$!

for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:$API_PORT/health" >/dev/null \
     && curl -sf "http://127.0.0.1:$SITE_PORT/index.html" >/dev/null; then
    break
  fi
  sleep 0.5
done

# 3) 完整演示流程
python3 -I "$ROOT/demo/flow.py" \
  --api-base "http://127.0.0.1:$API_PORT" \
  --site-base "http://127.0.0.1:$SITE_PORT" \
  --index-url "http://127.0.0.1:$SITE_PORT/index.html"

echo
echo "演示结束（日志: demo/site.log, demo/api.log）。"
