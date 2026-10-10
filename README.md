# CapCutPro

Du laddar upp en video, skriver vad du vill, och får en riktig YouTube Short (1080×1920, mp4). Originalet rörs aldrig. Varje export är en ny version: v1, v2, v3. Ångra går tillbaka till förra versionen.

Appen har två delar:

- **Webbsidan** (Next.js) som du använder i webbläsaren.
- **Workern** på din dator, som analyserar videon, pratar med AI-modellen och renderar med FFmpeg.

Vercel kan visa webbsidan, men kan inte köra FFmpeg, Whisper eller en lokal modell. Därför körs det tunga jobbet hemma eller på en egen server.

## Starta (Mac eller Linux)

1. Installera [Docker](https://www.docker.com/products/docker-desktop/) **eller** de här programmen: Python 3.11+, Node 22, FFmpeg och [Ollama](https://ollama.com). Den gamla eve-chatten på `/s` kräver Node 24. Startsidan behöver det inte.
2. Öppna Terminal. Gå till den här mappen.
3. Kör:

```bash
bash start.sh
```

4. Öppna [http://127.0.0.1:3000](http://127.0.0.1:3000).
5. Välj en video. Skriv till exempel: `Gör den här till en viral Short`.
6. Vänta tills spelaren visar v1. Skriv sedan en ändring, till exempel `gör texten större`. Det blir v2 av samma plan, inte en ny film från noll.
7. Knappen **Ångra** (eller ordet `ångra` i chatten) går tillbaka till förra versionen.

Första gången laddas en AI-modell ner (ungefär 2 GB). Det tar en stund. Låt fönstret vara öppet.

Vill du använda Docker i stället:

```bash
bash start.sh --docker
```

## Starta (Windows)

1. Installera [Docker Desktop](https://www.docker.com/products/docker-desktop/) **eller** Python 3.11+, Node 22, FFmpeg och [Ollama](https://ollama.com).
2. Öppna PowerShell i den här mappen.
3. Kör:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

4. Öppna [http://127.0.0.1:3000](http://127.0.0.1:3000) och följ samma steg som ovan.

Docker på Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Docker
```

## Byta AI-modell

Workern pratar med vilket program som helst som har samma API som OpenAI. Standard är Ollama på den här datorn.

| Variabel | Standard | Betydelse |
|---|---|---|
| `OPENAI_BASE_URL` | `http://127.0.0.1:11434/v1` | Adress till modellen |
| `OPENAI_API_KEY` | `ollama` | Nyckel. Ollama bryr sig inte om värdet. |
| `MODEL` | `qwen2.5:3b` | Modellnamn. En större modell, till exempel `qwen2.5:7b`, följer instruktioner bättre. |
| `WORKER_URL` | `http://127.0.0.1:8787` | Adressen webbsidan använder till workern |
| `WHISPER_MODEL` | `base` | Lokal tal-till-text. `small` är noggrannare och tyngre. |

Exempel med en annan server:

```bash
export OPENAI_BASE_URL="https://din-server/v1"
export OPENAI_API_KEY="hemlig-nyckel"
export MODEL="namnet-på-modellen"
bash start.sh
```

## Köra workern på en annan dator

1. På servern: `docker compose up --build` i den här mappen.
2. Öppna bara port 8787 mot din egen dator, inte mot hela internet. Workern har inget lösenord om du inte sätter `WORKER_TOKEN`.
3. På webbsidan sätter du `WORKER_URL` till serverns adress, och samma `WORKER_TOKEN` om du använder en.

## Vad som händer

AI:n ser inte filmfilen. Den anropar verktyg som ändrar en plan (JSON). Renderaren läser planen och bygger mp4-filen. Verktygen är: `analyze_video`, `get_transcript`, `get_scenes`, `get_faces`, `select_clip`, `trim_clip`, `delete_timeline_range`, `reorder_clips`, `change_speed`, `set_zoom`, `set_focus`, `add_caption`, `replace_captions`, `add_text`, `add_emoji`, `add_arrow`, `add_circle`, `add_badge`, `add_sound_effect`, `remove_effect`, `undo`, `commit_render`.

Texten är vit med svart kant och ett rött ord (`#FF2D2D`). Inget guld. Originalets ljud behålls. Ingen bakgrundsmusik. Ljudeffekter (whoosh, pop, impact, riser) genereras i programmet. Ljudnivån siktar på −14 LUFS.

## Tester

```bash
worker/.venv/bin/pytest -q worker/tests
```

`start.sh` skapar `worker/.venv` första gången.

## Eve-agenten

Mappen `agent/` är den tidigare eve-agenten för Vercel. Den är kvar. Hemsidan du öppnar med `start.sh` använder workern, inte eve.
