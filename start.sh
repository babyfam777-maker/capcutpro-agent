#!/usr/bin/env bash
# Startar CapCutPro på den här datorn. Mac och Linux.
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "--docker" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker är inte installerat. Installera Docker Desktop, eller kör utan --docker."
    exit 1
  fi
  docker compose up --build
  exit 0
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "FFmpeg saknas."
  echo "Mac: brew install ffmpeg"
  echo "Ubuntu/Debian: sudo apt install ffmpeg"
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 saknas. Installera Python 3.11 eller nyare."
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "Node saknas. Installera Node 22 från https://nodejs.org"
  exit 1
fi

if [[ ! -d worker/.venv ]]; then
  echo "Installerar worker-paket (första gången)…"
  PY=$(command -v python3.12 || command -v python3.11 || command -v python3)
  py_mm=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  py_major=${py_mm%%.*}
  py_minor=${py_mm#*.}
  if (( py_major > 3 || (py_major == 3 && py_minor >= 13) )); then
    echo "Python ${py_mm} kan inte installera numpy (ingen färdig wheel för 3.13+). Installera Python 3.12."
    echo "Mac: brew install python@3.12"
    exit 1
  fi
  "$PY" -m venv worker/.venv
  worker/.venv/bin/pip install -U pip
  worker/.venv/bin/pip install -r worker/requirements.txt
fi

mkdir -p worker/assets/fonts
if [[ ! -s worker/assets/fonts/Anton-Regular.ttf ]]; then
  curl -fsSL -o worker/assets/fonts/Anton-Regular.ttf "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf"
fi
if [[ ! -s worker/assets/fonts/Montserrat-ExtraBold.ttf ]]; then
  curl -fsSL -o worker/assets/fonts/Montserrat-ExtraBold.ttf "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-ExtraBold.ttf"
fi

export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:11434/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-ollama}"
export MODEL="${MODEL:-qwen2.5:3b}"
export WORKER_URL="${WORKER_URL:-http://127.0.0.1:8787}"
export WHISPER_MODEL="${WHISPER_MODEL:-base}"

if [[ "$OPENAI_BASE_URL" == *"127.0.0.1:11434"* || "$OPENAI_BASE_URL" == *"localhost:11434"* ]]; then
  if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null; then
    if ! command -v ollama >/dev/null 2>&1; then
      echo "Ollama är inte installerat. Hämta det på https://ollama.com och kör start.sh igen."
      echo "Har du en annan modellserver, sätt OPENAI_BASE_URL innan du kör skriptet."
      exit 1
    fi
    echo "Startar Ollama…"
    ollama serve >/tmp/capcutpro-ollama.log 2>&1 &
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      curl -sf http://127.0.0.1:11434/api/tags >/dev/null && break
      sleep 1
    done
  fi
  if ! curl -sf http://127.0.0.1:11434/api/tags | grep -q "${MODEL%%:*}"; then
    echo "Laddar ner modellen ${MODEL}. Första gången kan det ta flera minuter."
    ollama pull "$MODEL"
  fi
fi

if [[ ! -d node_modules ]]; then
  echo "Installerar webbsidan (första gången)…"
  if command -v pnpm >/dev/null 2>&1; then
    pnpm install --frozen-lockfile
  elif command -v corepack >/dev/null 2>&1; then
    corepack enable && corepack prepare pnpm@10.18.0 --activate && pnpm install --frozen-lockfile
  else
    npx -y pnpm@10.18.0 install --frozen-lockfile
  fi
fi

cleanup() {
  if [[ -n "${WORKER_PID:-}" ]]; then
    kill "$WORKER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "Startar workern på ${WORKER_URL}"
worker/.venv/bin/uvicorn app.main:app --app-dir worker --host 127.0.0.1 --port 8787 >/tmp/capcutpro-worker.log 2>&1 &
WORKER_PID=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -sf http://127.0.0.1:8787/health >/dev/null && break
  sleep 0.4
done

if [[ "$(uname -s)" == "Darwin" ]]; then
  # A project unzipped into ~/Downloads is quarantined. macOS then lets the
  # process start but blocks Safari from connecting to it.
  xattr -dr com.apple.quarantine "$PWD" 2>/dev/null || true
fi

if [[ ! -f node_modules/next/dist/bin/next ]]; then
  echo "Next.js saknas i node_modules."
  exit 1
fi

echo ""
echo "Startar webbsidan. http://127.0.0.1:3000 skrivs ut först när den svarar."
echo "Stäng med Ctrl+C."
echo ""
node scripts/dev-server.mjs
