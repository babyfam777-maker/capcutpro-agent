FROM node:24-bookworm-slim

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_WORKER_URL=https://cupcat-worker.onrender.com \
    WORKER_URL=https://cupcat-worker.onrender.com

RUN npm install --global pnpm@10.18.0

WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

ENV PORT=10000
EXPOSE 10000
CMD ["sh", "-c", "node_modules/.bin/next start --hostname 0.0.0.0 --port ${PORT:-10000}"]
