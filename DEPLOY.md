# CupCat: gratis Render-deployment

CupCat runs as two separate **Free** Render web services so the Next.js interface does not share its 512 MB memory limit with Python, Whisper, and FFmpeg.

## Services
- `cupcat-free`: Next.js chat UI, using the public worker URL built into the client.
- `cupcat-worker`: FastAPI + FFmpeg + faster-whisper in low-memory mode.

## Apply changes
1. Open the Blueprint in Render.
2. Select **Manual sync** and apply the current `main` branch configuration.
3. Wait until both `cupcat-free` and `cupcat-worker` show **Live**.
4. Open `https://cupcat-free.onrender.com`.

No paid provider or API key is configured. Ollama is **not** running in Render, so unrestricted natural-language AI is not available there; the deterministic editor rules are used when the model server is absent.

## Important free-tier limitations
- Render Free has 512 MB RAM and 0.1 CPU per web service.
- The low-memory worker uses Whisper's confidence/language checks but skips MediaPipe, dense optical flow, and the separate YAMNet classifier.
- The local filesystem is temporary. A service restart, redeploy, or idle spin-down can erase uploaded projects and rendered versions.
- Large or long videos may still fail or take a long time on 0.1 CPU.
- The first worker request may take a minute or longer while a sleeping instance wakes up.

This setup aims to keep the app from crashing because the UI and renderer compete for memory. It does **not** claim that hosted rendering has been verified until a new MP4 is generated and played on the live service.
