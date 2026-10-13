#!/usr/bin/env bash
set -Eeuo pipefail

cd /app
export DATA_DIR="${DATA_DIR:-/tmp/cupcat-data}"
export WORKER_URL="${WORKER_URL:-http://127.0.0.1:8787}"
export WHISPER_MODEL="${WHISPER_MODEL:-tiny}"
mkdir -p "$DATA_DIR"

WORKER_PORT="${WORKER_PORT:-8787}"
STOP_FILE=/tmp/cupcat-stop
WORKER_PID_FILE=/tmp/cupcat-worker.pid
rm -f "$STOP_FILE" "$WORKER_PID_FILE"

worker_supervisor() {
  local worker_pid=""
  local rc=0

  stop_worker() {
    if [[ -n "$worker_pid" ]] && kill -0 "$worker_pid" 2>/dev/null; then
      kill "$worker_pid" 2>/dev/null || true
      wait "$worker_pid" 2>/dev/null || true
    fi
    rm -f "$WORKER_PID_FILE"
    exit 0
  }
  trap stop_worker TERM INT

  while [[ ! -e "$STOP_FILE" ]]; do
    /opt/cupcat-venv/bin/uvicorn app.main:app --app-dir /app/worker --host 127.0.0.1 --port "$WORKER_PORT" &
    worker_pid=$!
    printf '%s\n' "$worker_pid" > "$WORKER_PID_FILE"
    rc=0
    wait "$worker_pid" || rc=$?
    worker_pid=""
    rm -f "$WORKER_PID_FILE"

    [[ -e "$STOP_FILE" ]] && break
    echo "CupCat worker exited with status $rc; retrying in 2 seconds. Project data stays on this instance."
    sleep 2
  done
}
worker_supervisor &
WORKER_SUPERVISOR_PID=$!
WEB_PID=""

cleanup() {
  touch "$STOP_FILE"
  if [[ -n "$WEB_PID" ]]; then
    kill "$WEB_PID" 2>/dev/null || true
  fi
  if [[ -n "$WORKER_SUPERVISOR_PID" ]]; then
    kill "$WORKER_SUPERVISOR_PID" 2>/dev/null || true
  fi
  if [[ -f "$WORKER_PID_FILE" ]]; then
    worker_pid="$(cat "$WORKER_PID_FILE" 2>/dev/null || true)"
    if [[ -n "$worker_pid" ]]; then kill "$worker_pid" 2>/dev/null || true; fi
  fi
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

ready=0
for attempt in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$WORKER_PORT/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$WORKER_SUPERVISOR_PID" 2>/dev/null; then
    echo "CupCat worker supervisor stopped during startup."
    exit 1
  fi
  sleep 1
done

if [[ "$ready" != "1" ]]; then
  echo "CupCat worker did not become ready within 60 seconds."
  exit 1
fi

node_modules/.bin/next start --hostname 0.0.0.0 --port "${PORT:-10000}" &
WEB_PID=$!
wait "$WEB_PID"
