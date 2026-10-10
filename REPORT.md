# CapCutPro — inspektion (fas 1)

Datum: 2026-10-10. Gren: `v0/redigera-video-i-appen-0c907f4c`.
Det här är läget **innan** den lokala workern byggdes. Slutresultatet (vad som byggdes, hur man kör, tester) står i `README.md` och i pull request-beskrivningen.

## A) Vad som redan fungerade

Projektet är en Next.js 16-app (`app/`) plus en eve-agent (`agent/`). Chatten är inte en fejkad regex-bot.

- **UI:** riktig chat (`app/_components/agent-chat.tsx`) via `useEveAgent`. Uppladdning går till Vercel Blob (`app/api/upload/route.ts`, `video-upload-button.tsx`). Färdiga renderingar visas som `<video>` när verktyget `get_render_status` returnerar en `videoUrl` (`agent-message.tsx`).
- **Agent:** `agent/agent.ts` + `agent/instructions.md`. Modellen är eve-routad (`spacexai/grok-4.1-fast-reasoning`), inte en hårdkodad svarsmall.
- **Verktyg som muterar ett redigeringsförslag, inte filen:** `import_video`, `analyze_video`, `transcribe_video`, `get_edit_plan`, `save_edit_plan`, `render_video`, `get_render_status`.
- **Plan:** Zod-schema i `agent/lib/edit-plan.ts` (segment med speed/zoom/focus, captions, hook, ljud). Version + historik (max 10) i `save_edit_plan.ts`.
- **Renderkod:** `buildFilterGraph` (FFmpeg trim/scale/crop/zoompan/concat/loudnorm) och `buildAss` (ASS-undertexter). Körs i en Vercel-sandbox (`agent/sandbox.ts`) och resultatet laddas upp till Blob.
- **Analyskod:** FFmpeg `select=gt(scene)` och `silencedetect`, kontaktark, samt ett vision-anrop. Transkribering via `openai/whisper-1` (segmenttider, ord *uppskattas* jämnt inom segmentet).

Inget av det här är en dummyknapp. Det är en riktig pipeline som bara kan köras där Vercel Blob, eve-modellen, vision-gateway och sandbox finns.

## B) Vad som saknades

- Ingen lokal worker. Vercel serverless kan inte köra FFmpeg-render, faster-whisper eller en lokal LLM.
- AI:n är låst till eve/Vercel-modeller. Ingen `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `MODEL`, alltså ingen Ollama.
- Verktygen är grova (`save_edit_plan` byter hela planen). Saknade verktyg: `get_transcript`, `get_scenes`, `get_faces`, `select_clip`, `trim_clip`, `delete_timeline_range`, `reorder_clips`, `change_speed`, `set_zoom`, `set_focus`, `add_caption`, `replace_captions`, `add_text`, `add_emoji`, `add_arrow`, `add_circle`, `add_badge`, `add_sound_effect`, `remove_effect`, `undo`, `commit_render`.
- Ingen ansiktsspårning, ingen rörelseenergi, inga ords tidsstämplar från lokal whisper, inga talare, inga hook-kandidater.
- Ingen 9:16-beskärning som följer en person frame för frame. `focusX`/`focusY` är ett värde per segment.
- Grafik följer inte Unguarded v2: accentfärgen i schemat är guld `#FFE600` (förbjudet). Ingen emoji, pil, cirkel, badge, SFX, safe area 220/320, Fluent 3D.
- FFmpeg-installationen i `agent/sandbox.ts` är trasig: `dnf` först, johnvansickle-tarball, och en syntaxbugg efter `defineSandbox` (extra `` ` ``, `})` och `);`) så filen inte ens kan laddas.
- Originalfilen lever i Blob/sandbox, inte som en orörd projektfil med v1/v2-MP4 på disk.
- Ingen svensk start för en icke-utvecklare. `README.md` säger bara `eve dev`.

## C) Filer att ändra / lägga till

Behålla och bygga vidare på:

- `app/_components/*` och `components/ui/*` (utseende).
- `agent/lib/edit-plan.ts` (idén: segment, zoom, focus, loudnorm −14 LUFS).
- `agent/tools/*` (eve-vägen lämnas kvar, den anropas inte av den nya UI:n).
- `agent/sandbox.ts` (installscriptet måste lagas).

Nytt:

- `worker/` — Python-tjänst: tillstånd, analys, verktyg, agent, grafik, SFX, FFmpeg-render.
- `app/page.tsx` + `app/_components/editor.tsx` + `app/api/worker/[...path]/route.ts` — UI som pratar med workern.
- `docker-compose.yml`, `scripts/start.sh`, `scripts/start.ps1`, `README.md` (svenska).

## D) Hur AI-agenten var kopplad

1. Webbläsaren använder `useEveAgent` mot eve-kanalen `agent/channels/eve.ts` (Better Auth / Vercel OIDC).
2. Uppladdad video beskrivs som text: `[Uploaded videos] … blob pathname`.
3. eve anropar modellen i `agent/agent.ts`. Instruktionerna säger åt modellen att köra verktygen.
4. Verktygen kör FFmpeg **inne i sandboxen** och vision/whisper via Vercel AI Gateway (`google/gemini-3.5-flash`, `openai/whisper-1`).
5. Modellen ser inte råa filer, men den skickar en **hel** ny plan till `save_edit_plan` i stället för att mutera planen med små verktyg.

Det finns inga hårdkodade chattsvar och ingen regex som tolkar användarens mening.

## E) Hur videoanalys borde fungera

På originalfilen (aldrig överskriven), resultat som JSON:

- ffprobe: längd, upplösning, fps, ljud.
- Scener: FFmpeg `select='gt(scene,…)'` (PySceneDetect om det finns).
- Tystnad: `silencedetect`.
- Transkript med ordtider: faster-whisper lokalt. Kända hallucinationer ("Thank you for watching") filtreras.
- Talare: paus-separerade turer (inte biometrisk diarization; pyannote kräver token).
- Ansikten över tid: OpenCV-ansiktsdetektor, spår med IoU, positioner i 0–1.
- Rörelseenergi: bildskillnad på nedskalade frames.
- Kandidater: hook / punchline / hög energi, så klippningen kan följa innehållet i stället för ett fast intervall.

## F) Hur rendering borde fungera

1. Agenten muterar en versionerad JSON-plan.
2. `commit_render` kompilerar planen: 30 fps, 1080×1920, beskärning som följer ansiktsspåret, zoom, tempo, frys.
3. Grafik ritas med Pillow i Unguarded v2-stil (vit text, svart stroke, mjuk skugga, ett rött `#FF2D2D`-ord, safe area, Fluent 3D-emoji när filerna finns).
4. Ljud: originalet behålls, ingen musik som standard, genererade whoosh/pop/impact/riser, tvåstegs `loudnorm` mot −14 LUFS.
5. FFmpeg kodar H.264 High + AAC till en riktig MP4. Varje render är `v1`, `v2`, … Originalet rörs inte. `undo` pekar tillbaka på föregående versions plan.

## Vad som var mock / fejk / trasigt

| Sak | Dom |
|---|---|
| Hårdkodade chattsvar / regex-chat | Nej. Svar kommer från eve-modellen. |
| Fejkad export / "coming soon" | Nej. Exporten är en riktig FFmpeg-pipeline, men den kan inte köras på Vercel som den är, och sandbox-scriptet kraschar. |
| Placeholder-video som låtsas vara användarens film | Nej. |
| Guld-accent `#FFE600` | Ja, i caption-schemat. Strider mot stilguiden. |
| Ordtider | Delvis fejk: whisper-segment delas upp jämnt per tecken, inte riktiga ordtider. |
| Ansikten / emoji / pilar / SFX | Saknas (inte fejkade). |
| Vision-analys | Riktigt anrop, men bara 12 frames på ett kontaktark och bara i molnet. |
| `agent/sandbox.ts` | Trasig syntax + icke-portabel `dnf`/johnvansickle-install. |

## Konsekvens

Eve-verktygen lämnas kvar. Produkten som går att köra hemma är en worker med samma idé (plan som JSON, modellen rör inte filer) och de verktyg som saknades.

## Vad som byggdes

Lokal worker (`worker/`, FastAPI på port 8787) som Next.js pratar med via `WORKER_URL`. Modellen är valfri OpenAI-kompatibel endpoint (`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL`). Standard är Ollama `qwen2.5:3b`.

AI:n anropar verktyg som muterar en JSON-plan. Den skriver inte i videofilen. `commit_render` kör FFmpeg och sparar `versions/vN/output.mp4`. Originalet kopieras en gång och hashas. `undo` sätter aktiv version tillbaka. Filerna för senare versioner ligger kvar.

Analys: ffprobe, scener, tystnad, faster-whisper med ordtider, OpenCV-ansikten, rörelse, talarturer från pauser, hook-kandidater. Render: 1080×1920 H.264 High, AAC 48 kHz, beskärning som följer ansiktsspåret, grafik i Unguarded v2 (vit text, svart stroke, ett rött `#FF2D2D`-ord, ingen guld), originaljud, genererade SFX, loudnorm omkring −14 LUFS.

Start: `bash start.sh` (Mac/Linux) eller `start.ps1` (Windows). Docker: `bash start.sh --docker`. Svensk guide i `README.md`.

`agent/sandbox.ts` installerar FFmpeg portabelt (redan installerad, apt/dnf/yum/apk/brew, annars BtbN-bygge). Eve-schemats accent är `#FF2D2D`.

## Test 2026-10-10

`pytest worker/tests`: 15 passerade (plan, zoom i millisekunder och på uttalat ord, undo, säkerhetstext, grafik utan guld, render 1080×1920, analys med espeak, agentloop).

End-to-end mot Ollama `qwen2.5:3b` och en riktig bildruta från det bifogade klippet, med espeak-tal så att ordtider går att kontrollera (`worker/scripts/e2e.py`). Originalets sha256 oförändrad.

| Tur | Resultat |
|---|---|
| Gör den här till en viral Short | v1, 3,1 s, 1080×1920, H.264 High, yuv420p, AAC 48 kHz, −14 LUFS |
| gör texten större | v2, caption 72 → 93, headline 120 → 156 |
| första delen är för lång | v3, 3,1 s → 1,1 s |
| zooma in när han säger punchline | v4, zoom 1,0 → 1,55 / 1,25, ansiktsspår face_0 |
| undo | aktiv version tillbaka till v3, v4-filen finns kvar |

Bevis: `/opt/cursor/artifacts/v1.mp4` … `v4.mp4`, bildrutor, `vN-plan.json`, `e2e-report.json`. ffprobe på v1: h264 High 1080×1920 yuv420p, aac 48000, duration 3,1.

## Kända begränsningar

- `qwen2.5:3b` på CPU är långsam och svag. Om den bara analyserar och inte klipper, lägger workern ett innehållsbaserat klipp (tal eller starkaste ögonblicket) och renderar ändå. En större modell följer följdfrågor bättre.
- Ingen riktig talaridentifiering (bara pauser). Ansikten är OpenCV Haar, inte MediaPipe.
- Fluent 3D-emoji följer med om PNG-filer ligger i `worker/assets/emoji/`. Annars används systemets emoji-typsnitt.
- Ingen full Unguarded-QA (OCR, betyg, demucs).
- Vercel kan visa UI men inte rendera. Eve-vägen (`/s`) finns kvar och kräver fortfarande Blob och molnmodell.
- Workern lyssnar på 127.0.0.1. Sätt `WORKER_TOKEN` om den ska nås från en annan dator.
