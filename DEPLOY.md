# CupCat: gratis Render-deployment

## Publicera
1. Open Render and choose **New → Blueprint**.
2. Connect this GitHub repository: `babyfam777-maker/capcutpro-agent`.
3. Select branch `main` after PR #9 is merged, then apply the Blueprint.
4. Choose the **Free** plan if Render shows a plan selector.

The Blueprint creates one Docker web service named `cupcat-free`. It serves the Next.js UI on Render's assigned `PORT` and runs the FastAPI/FFmpeg worker on loopback port 8787. The worker is not exposed publicly.

## Gratisnivåns begränsningar
- The service may spin down after inactivity and cold starts can take a while.
- Render's free filesystem is temporary. Uploaded and rendered video files may disappear after a restart or redeploy.
- Free compute has a tight memory budget. Large/long videos may fail or be killed.
- No paid AI API or API key is configured. The container does not run a local Ollama 3B model because that is too large for the free service; when Ollama is unavailable, the existing worker uses its explicit rule-based fallback. Natural-language understanding is therefore limited compared with the local Colab setup.

This deploy configuration does not claim the application has passed a hosted render test. The public service URL is assigned by Render after deployment.
