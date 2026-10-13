#!/usr/bin/env bash
set -Eeuo pipefail

cd /app
export DATA_DIR="${DATA_DIR:-/tmp/cupcat-data}"
export WORKER_URL="${WORKER_URL:-http://127.0.0.1:8787}"
export WHISPER_MODEL="${WHISPER_MODEL:-tiny}"
mkdir -p "$DATA_DIR"

WORKER_PORT="${WORKER_PORT:-8787}"
/opt/cupcat-venv/bin/uvicorn app.main:app --app-dir /app/worker --host 127.0.0.1 --port "$WORKER_PORT" &
WORKER_PID=$!
WEB_PID=""

cleanup() {
  if [[ -n "$WEB_PID" ]]; then kill "$WEB_PID" 2>/dev/null || true; fi
  kill "$WORKER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

ready=0
for attempt in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:$WORKER_PORT/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$WORKER_PID" 2>/dev/null; then
    echo "CupCat worker stopped during startup."
    exit 1
  fi
  sleep 1
done

if [[ "$ready" != "1" ]]; then
  echo "CupCat worker did not become ready within 45 seconds."
  exit 1
fi

node_modules/.bin/next start --hostname 0.0.0.0 --port "${PORT:-10000}" &
WEB_PID=$!
wait -n "$WORKER_PID" "$WEB_PID"
