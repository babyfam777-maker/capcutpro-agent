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

4. Öppna [http://127.0.0.1:3000](http://127.0.0.1:3000). Använd just den adressen. På Mac lyssnar servern på 127.0.0.1, så att Safari kan öppna sidan.
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

## Ingen betald tjänst

Chatten på startsidan bestämmer tidslinjen. Den lägger inte längre in ordet LOOK, en badge eller en bubbla av sig själv. Originaljud är på och bakgrundsmusik är av, tills du ber om något annat.

Språkmodellen är lokal Ollama (`qwen2.5:3b` som standard, `OPENAI_BASE_URL=http://127.0.0.1:11434/v1`). Ingen API-nyckel behövs. Om Ollama inte svarar används en regelmotor, och svaret säger det. Workern byter inte tyst till Gemini, OpenAI, Anthropic eller Grok. En annan OpenAI-kompatibel server används bara om du själv sätter `LLM_ALLOW_REMOTE=1`.

Analys och render är lokala: FFmpeg, OpenCV, faster-whisper när paketet finns, och MediaPipe för ansikte, händer och pose. MediaPipe behöver `libegl1` och `libgl1`. Hittas inget ansikte används en kroppsruta från pose. En pil följer ett handspår bara om MediaPipe faktiskt hittade en hand. Saknas MediaPipe används OpenCV Haar, som ofta missar profil och mörka bilder.

Standardfilen är 1080×1920, H.264 High, yuv420p, så den går att spela på en telefon. `SHORT_DELIVERY=4k` gör 2160×3840 i 60 bilder per sekund och märker filen `upscaled` när källan är mindre. Det tar betydligt längre tid på en fri CPU.

Colab-anteckningen `colab/capcutpro_worker.ipynb` installerar FFmpeg och Ollama, startar workern på port 8787 och skriver ut `WORKER URL` via en Cloudflare-tunnel. Fri Colab har ungefär 12 GB RAM, bara CPU, och en session som kan dö. räkna med att qwen2.5:3b och Whisper base får plats, men inte en stor bildmodell och inte en snabb 4K60-render.

Utan Colab, på din egen dator:

```bash
bash start.sh
```

Det startar Ollama om den är installerad, workern och sidan på [http://127.0.0.1:3000](http://127.0.0.1:3000). Docker: `bash start.sh --docker`.

## Trenddata och paket

Varje render får en titel, en hooktext, en beskrivning och hashtags i chatten, byggda från det som analysen faktiskt mätte. En intern viralpoäng räknas, men den visas bara om du ber om poängen. En andra genomgång läser de renderade bildrutorna och gör om klippet bara när öppningen fortfarande är svag.

Trendfilerna ligger i `data/trending/`. Dagens YouTube-lista (`rsrishav/youtube-trending-video-dataset`) och den äldre listan med visningar (`datasnaek/youtube-new`) hämtas bara om en gratis Kaggle-token finns: `KAGGLE_USERNAME` och `KAGGLE_KEY`, eller `~/.kaggle/kaggle.json`. Utan token används en öppen titelspegel från 2019–2020. Den är märkt historisk, saknar visningar och region, och är inte dagens lista. Inga titlar kopieras från andra kanaler.

YouTube Analytics kan klistras in i chatten (views, AVD, swipes, early drop). Lärdomen sparas och används på nästa klipp: tidig tapp påverkar hooken, mitten tempot, slutet payoff.

## Byta AI-modell

Workern pratar med vilket program som helst som har samma API som OpenAI. Standard är Ollama på den här datorn.

| Variabel | Standard | Betydelse |
|---|---|---|
| `OPENAI_BASE_URL` | `http://127.0.0.1:11434/v1` | Adress till modellen |
| `OPENAI_API_KEY` | `ollama` | Ollama ignorerar värdet. Ingen riktig nyckel behövs. |
| `MODEL` | `qwen2.5:3b` | Lokal modell. En större modell följer instruktioner bättre och kräver mer minne. |
| `LLM_ALLOW_REMOTE` | av | Måste vara `1` innan en adress som inte är localhost ens får anropas. |
| `SHORT_DELIVERY` | `phone` | `4k` renderar 2160×3840 och märker uppskalning. |
| `WORKER_URL` | `http://127.0.0.1:8787` | Adressen webbsidan använder till workern |
| `WHISPER_MODEL` | `base` | Lokal tal-till-text. `small` är noggrannare och tyngre. |
| `LLM_TIMEOUT` | `180` | Hur många sekunder workern väntar på ett svar från modellen. Höj om en långsam modell hinner inte svara. |

Exempel med en annan server:

```bash
export LLM_ALLOW_REMOTE=1
export OPENAI_BASE_URL="http://127.0.0.1:11434/v1"
export MODEL="qwen2.5:3b"
bash start.sh
```

Lämna `LLM_ALLOW_REMOTE` tom om servern är Ollama på den här datorn. Sätt den bara om du medvetet pekar på en annan dator du själv driver.

## Köra workern på en annan dator

1. På servern: `docker compose up --build` i den här mappen.
2. Öppna bara port 8787 mot din egen dator, inte mot hela internet. Workern har inget lösenord om du inte sätter `WORKER_TOKEN`.
3. På webbsidan sätter du `WORKER_URL` till serverns adress, och samma `WORKER_TOKEN` om du använder en.

## GÖR MIN SHORT

Startsidan heter CAPCUTPRO / VIRAL SHORT EDITOR. Släpp vilken video som helst, skriv vad shortsen ska handla om och tryck **GÖR MIN SHORT**. Workern kör analys v2 och den befintliga `render_plan`. Chatten ändrar planen, inte en ny renderer:

| Du skriver | Planen |
|---|---|
| Gör hooken större | Större hook-text och röd ruta |
| Ta bort pilen | Pileffekten tas bort |
| Mer zoom på personen | Högre zoom på ansiktsspåret |
| Gör texten roligare | Story-raderna och bubblan skrivs om |

**RENDER IGEN** bygger nästa MP4 (v2, v3, …) från den ändrade planen. Spelaren visar filen på sidan. **Ladda ner MP4** hämtar samma fil.

Vercel kan visa sidan men inte köra FFmpeg. För en preview sätter du `NEXT_PUBLIC_WORKER_URL` till en https-adress där den här workern kör (`WORKER_HOST=0.0.0.0`). Utan den adressen pratar sidan med `/api/worker` på samma dator, vilket `start.sh` startar.

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
