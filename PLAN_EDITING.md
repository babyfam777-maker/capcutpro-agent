# Bättre klippning utan en större AI-modell

Det här är en plan, inte en kodändring. Den beskriver varför en Short kan bli en nästan oklippt kopia av originalet, och hur programmet kan klippa bättre på en MacBook Air med 8 GB minne.

Modellen som används idag är `qwen2.5:3b`. Den är liten med flit. Den hinner svara. Den är dålig på att förstå en film och på att bygga ett tajt klipp. Lösningen är att låta vanlig kod bygga klippet, och låta modellen bara skriva de korta texterna.

## A) Vad som händer idag, och var det gick fel

Du skriver en instruktion. Workern skickar den till modellen tillsammans med den nuvarande planen. Modellen får inte röra videofilen. Den anropar verktyg. Verktygen ändrar en JSON-plan. När verktyget `commit_render` körs ritar programmet bildrutor och FFmpeg sparar en MP4. Originalet rörs inte.

I testet med den 9,4 sekunder långa röda-mattan-filmen (projekt `b3ab1905ece4`, logg `qwen25_3b_r2`) gick det så här.

**Första meddelandet** (198,5 sekunder): "Gör den här till en viral YouTube Short…"

1. Modellen anropade `analyze_video` två gånger. Andra gången var cachat. Filen är 9,381 s, 608×1080, 30 fps, med ljud.
2. Analysen hittade ett scenklipp vid 1,533 s, inga tystnader, och sångtexten "We can't stop it, not the other way…". Det är låttext, inte någon som pratar om det som syns.
3. Sex ansikten hittades med en enkel detektor (Haar), inte en som ser skratt eller vem som är vem. `face_16` syns längst (3,88 s). `face_15` syns 1,12 s och sitter till vänster.
4. Det starkaste ögonblicket enligt koden är 1,5–2,0 s (ord, rörelse, ansikte och scenklipp). Modellen använde inte det.
5. `select_clip` med start 0 och slut 120 misslyckades: "Klippet måste vara minst 0,2 sekunder." 120 tolkades som millisekunder och blev 0,12 s. Det är för kort.
6. `add_text` lade texten "Wow" från 0,2 till 0,2 s. Längden är noll, så texten ritas aldrig.
7. Första `commit_render` stoppades: "Inga klipp."
8. Nästa `select_clip` tog 0 till 10 s. Filen är bara 9,381 s, så hela filmen lades in som ett enda klipp. Zoom är 1,0 till 1,0, alltså ingen inzoomning. Fokus sattes på `face_16`.
9. En tvingad `commit_render` sparade v1: 1080×1920, H.264, cirka −14 LUFS, längd 9,37 s. Svaret påstod att den starkaste delen öppnade klippet. Planen visar att hela källan ligger kvar, i samma ordning.

Automatiska undertexter lades på vid render, grupperade ur sångorden. De täcker ungefär 0,75–8,07 s. Före 0,75 s finns ingen synlig text, eftersom "Wow" har längd noll. Efter 8,07 s finns ingen text. Vid 1,0 s i **v1** ska raden "We can't stop" (0,75–1,967) synas.

**Andra meddelandet** (103,5 sekunder): "Gör texten större och lägg en röd pil på kvinnan när hon skrattar."

1. `add_arrow` 1,12–1,41 s mot `face_15`. Det ansiktet finns, men inget i analysen säger att någon skrattar där. 1,12 s är mitt i ordet "can't".
2. `replace_captions` med större text behöll först åtta rader och satte textstorlek 100.
3. Samma verktyg anropades igen med en enda rad, "skrattar", 1,12–1,47 s. De åtta raderna försvann.
4. v2 renderades, samma längd, samma enda klipp, zoom fortfarande 1,0. Textstorleken är 100 och rubriken 156, men den enda synliga texten är "skrattar" i ungefär en tredjedels sekund. Vid 1,0 s finns ingen text: raden börjar 1,12 s och "Wow" har fortfarande längd noll.

Renderingen i sig fungerade. Klippningen följde inte instruktionen.

## B) Vad qwen2.5:3b klarar på en MacBook Air med 8 GB

Den klarar att anropa ett verktyg när den blir tillsagd, att byta textstorlek, och att peka på ett ansikts-id som redan finns i listan. Den kan skriva en kort engelsk rubrik.

Den klarar inte tillförlitligt att:

- välja vilka sekunder som ska vara kvar
- förstå att en sångtext inte beskriver bilden
- veta vem som skrattar
- hålla flera ändringar i huvudet (storlek och pil) utan att radera de andra texterna
- sätta tider som faktiskt syns (0,2–0,2 s)
- låta bli att ta med hela filmen

En större modell, till exempel `qwen3:8b`, följer instruktioner bättre men är för långsam på den här datorn när 8 GB redan används av systemet, webbläsaren och videon. Den ska inte vara standard.

## C) Förslag: koden klipper, modellen skriver text

Efter analysen bygger ett fast "auto-klipp" en bra plan innan modellen får röra den. Modellen får bara välja inriktning och skriva korta rader. Om raderna är ogiltiga försöker den igen, högst ett par gånger. En kontroll vägrar spara en plan som är hela klippet utan klipp, eller som saknar text vid en tid du tittar på.

**Ren kod, utan ny modell**

- Ta bort tydlig dödtid: före första ordet (här 0–0,75 s) och efter sista ordet (här 8,07–9,38 s). Om det inte finns tystnad mitt i, klipp inte sönder sången bara för att "göra cuts".
- Lägg det starkaste ögonblicket först. Här är det 1,5–2,0 s. Första bildrutan i Shorten ska komma därifrån, så hooken sitter inom 0,7 s.
- Zoom på det ögonblicket och vid scenklippet 1,533 s. Inte zoom 1,0 på hela filmen.
- Undertexter från ordtiderna, byte ungefär var 0,7–2 s. De första 5 sekunderna ska ha text hela tiden. En rubrik måste vara minst cirka 0,8 s, aldrig 0 s.
- Pilar, ringar och ljudeffekter bara där analysen har en händelse: scenklipp, rörelsetopp, eller ett ansikte som faktiskt syns då. Placeringen följer ansiktsrutan och håller sig innanför safe area (220 upp, 320 ner).
- Kontroll innan render: ett enda klipp som täcker nästan hela källan godtas inte om instruktionen bad om klippning och det finns dödtid att ta bort. Saknas text i första 0,7 s, eller i något 2-sekundersfönster av de första 5 s, renderas inte planen. `replace_captions` får inte ersätta alla rader med ett ord som inte finns i ljudet, om användaren bara bad om större text.

**Det som behöver bättre analys, inte en större språkmodell**

- Ansikten: MediaPipe i stället för Haar. Haar missar profiler och kan inte säga "skratt". MediaPipe ger en ruta per bildruta på CPU på några sekunder för ett klipp på 10 s.
- Rörelse var 0,25 s, inte bara ett betyg per 0,5 s. Den används för att välja hook.
- Ljudtoppar (onset) så ett punch-zoom kan sitta på ett trumslag.
- Skilj musik från tal. Whisper skrev låttexten som om någon pratade. Om ljudet är musik ska texterna inte låtsas förklara handlingen, och "när hon skrattar" ska inte gissas ur orden.
- En liten bildmodell via Ollama, till exempel moondream, kan titta på 4–6 bildrutor och säga "två personer, röd matta". Den ryms ungefär (runt 2 GB) bara om `qwen2.5:3b` laddas ur först. llava i 7B-klass ryms dåligt tillsammans med något annat på 8 GB och blir långsam. Det ska vara avstängt som standard. Utan den kan programmet inte påstå att någon skrattar.

Modellen (`qwen2.5:3b`) får ett litet JSON-uppdrag: rubrik, vilket ögonblick som ska först om flera är nästan lika starka, och korta textrader med ett accentord. Raderna kontrolleras mot innehållsreglerna (ingen sexualisering, inga påhittade påståenden om riktiga personer). "skrattar" godkänns inte om varken ljud eller bild stöder det.

## D) Minne på 8 GB

- Ha bara `qwen2.5:3b` laddad medan du chattar. Ladda inte `qwen3:8b` samtidigt.
- Spara analysen på projektet. Andra meddelandet ska inte köra Whisper och ansiktsletning igen.
- Whisper `base` räcker. `small` är tyngre och behövs inte för det här.
- Render med samma preset som nu (mycket snabb H.264, 1080×1920, 30 fps). Förhandsvisning kan vara kortare CRF-väg senare, men inte i första steget.
- Om en bildmodell används: ladda ur språkmodellen, beskriv några bildrutor, ladda ur bildmodellen, ladda språkmodellen igen. Räkna med extra tid, inte med båda i minnet.

## E) Ordning att bygga det

1. **Kontroll av planen.** Vägra längd noll på text, vägra att hela källan blir ett klipp när dödtid finns, vägra render om första 0,7 s saknar text. Liten ändring i verktygen som redan finns.
2. **Auto-klipp efter analys.** Bygg klipp, zoom, texter och effekter i kod från analysen. Modellen skriver bara texterna. Det är den stora delen: en ny funktion bredvid planen, inte ett nytt gränssnitt.
3. **Bättre mätvärden.** Rörelse var 0,25 s, ljudtoppar, musik mot tal. Fortfarande lokal kod och FFmpeg.
4. **MediaPipe-ansikten.** Byt detektor. Pilar följer rutan. Valfritt och avstängt: moondream via Ollama.
5. **Acceptanstest på det bifogade klippet.** Första bildens källa ska ligga vid det starkaste ögonblicket (omkring 1,5 s), inte vid 0. Minst ett klipp ska ha tagits bort (slutet efter sista ordet, eller början före första ordet). Text ska synas på uttagna bildrutor vid 0,3 s, 1,0 s och 3,0 s. Filen ska vara en riktig MP4, 1080×1920, H.264. "Gör texten större" ska ändra storlek och lämna kvar de andra raderna. En pil får bara läggas om ett ansikte finns vid tiden, och ordet "skrattar" ska inte hittas på om analysen inte stöder det.

## Teknisk bilaga

Flöde i koden idag:

1. `worker/app/agent.py` `run_turn` skickar ett systemmeddelande plus planen till OpenAI-kompatibel Ollama. Tidsgränsen är `LLM_TIMEOUT` (standard 180 s).
2. Verktyg i samma fil anropar `worker/app/plan_ops.py`. `select_clip` kan dela tider med 1000 om de ser ut som millisekunder. 120 mot en 9,4 s film blev 0,12 s och avvisades (händelse 17–18).
3. `add_text` kräver inte att slutet är efter starten. "Wow" 0,2–0,2 sparades (händelse 19–20) och ritas inte, eftersom render bara tar `start <= t < end`.
4. `worker/app/analysis.py` `summary_for_model` skickar scener, tystnad, ansikten (max 12 prover), ögonblick och ord. Rå rörelselista skickas inte. Ansikten kommer från OpenCV Haar vid cirka 8 fps på en 480 px bred bild (`motion_and_faces`).
5. `compile_plan` i `plan_ops.py` fyller i undertexter med `captions_from_words` om listan är tom, och lägger whoosh/pop/thump/impact. Det är därför v1 fick åtta sångtextrader fast modellen inte bad om dem.
6. `replace_captions` bytte i andra turen ut alla rader mot "skrattar" (händelse 5–6). Inget verktyg kollade att ordet finns i transkriptet.
7. `worker/app/render.py` beskär till 1080×1920, följer `focusTrack`, ritar grafik från `worker/app/gfx.py`, och kodar H.264. Ljudgrafen skickas som `-filter_complex` (filen sparas bara för felsökning). v1 och v2 i loggen är 1080×1920, yuv420p, AAC 48 kHz, loudness −14,1 LUFS.
8. `worker/app/safety.py` stoppar vissa påståenden och emojis. Det stoppar inte en tom tidslinje eller en påhittad bildtext som "skrattar".

Ögonblicket som koden redan rankar högst, och som auto-klippet ska öppna med, är 1,5–2,0 s, text "stop", skäl speech, motion, face, scene-cut.
