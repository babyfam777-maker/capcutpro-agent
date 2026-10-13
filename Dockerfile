FROM python:3.12-slim-bookworm

ARG NODE_VERSION=24.14.1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production \
    DATA_DIR=/tmp/cupcat-data \
    WHISPER_MODEL=tiny \
    CUPCAT_LITE_MODE=1 \
    WORKER_URL=http://127.0.0.1:8787 \
    OPENAI_BASE_URL=http://127.0.0.1:11434/v1 \
    OPENAI_API_KEY=ollama \
    MODEL=qwen2.5:3b \
    LLM_ALLOW_REMOTE=0 \
    LLM_TIMEOUT=15 \
    OMP_NUM_THREADS=1 \
    OMP_THREAD_LIMIT=1 \
    OPENBLAS_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils ffmpeg fonts-noto-core fonts-noto-color-emoji \
      libegl1 libgles2 libgl1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install a Node.js LTS release alongside Python for the combined free service.
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
      | tar -xJ --strip-components=1 -C /usr/local \
    && npm install --global pnpm@10.18.0

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

RUN python -m venv /opt/cupcat-venv \
    && /opt/cupcat-venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/cupcat-venv/bin/pip install --no-cache-dir -r worker/requirements.txt

ENV PATH="/opt/cupcat-venv/bin:${PATH}"

EXPOSE 10000
CMD ["bash", "/app/start-render.sh"]
