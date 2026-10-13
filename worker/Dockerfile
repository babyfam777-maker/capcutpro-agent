FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-noto-core fonts-noto-color-emoji ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY assets ./assets

ENV DATA_DIR=/data \
    WHISPER_MODEL=base \
    OPENAI_BASE_URL=http://ollama:11434/v1 \
    OPENAI_API_KEY=ollama \
    MODEL=qwen2.5:3b

EXPOSE 8787
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
