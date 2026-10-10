# Redigeringsmotor: från bevis till berättelse

Det här är en design, inte en kodändring. PR #2 ligger kvar som säkerhetsbas och ska inte mergas av det här dokumentet. Modellen förblir `qwen2.5:3b`. Worker-konfiguration, FFmpeg-anrop och serverstart rörs inte.

## Vad som är fel idag, med vanliga ord

PR #2 gör en viktig sak: om modellen ber om en sekund ur en film på femton sekunder, och den sekunden slänger nästan allt som sägs, exporteras inte den sekunden. Programmet bygger i stället ett klipp av analysen.

Ägaren tittade på resultatet från Met Gala-filmen. Längden var rätt, ungefär hela källan. Men klippet var i praktiken hela filmen, med öppningen flyttad först, plus automatiska undertexter och färgade ord. En rad blev "BEAUTIFUL FOR ON". Det är inte en berättelse. Det är en omkastad kopia med text ovanpå.

Referensen är en Short på 24 sekunder (`n6j3LUHNbl4`). Vanlig film från en modevisning blir en meet-cute i sju tag: någon letar en plats, hälsar, kramas, en pinsam tystnad, en oväntad pryl, och till sist payoff. Emojin syns ungefär 0,3–0,4 sekunder innan meningen. Pilen följer föremålet. Tankar ligger i en bubbla. Frågor används i stället för påståenden.

Nästa motor ska kunna göra den sortens val på vilken film som helst. Den får inte ha specialregler för en viss person eller en viss röd matta.

## Säkerhetsbasen som redan finns

PR #2 gäller fortfarande, på den väg som finns idag:

- Filens längd läses från filen innan någon klippplan skapas.
- En sekunds resultat är ogiltigt om källan inte själv är ungefär en sekund.
- En modellplan som slänger det mesta av talet, eller missar de starka ögonblicken, avvisas med ett svenskt fel.
- Svaret får inte säga att det lyckades om planen byttes ut.
- Text som inte hinner läsas (till exempel "Wow!" i 0,2 sekunder) nekas.
- Originalets ljud behålls. Ingen bakgrundsmusik.

Den nya motorn ligger ovanpå det. Den ersätter inte de kontrollerna på den gamla vägen. För den nya vägen, storyboard, byts "behåll minst halva talet" ut mot en annan regel: varje sekund som är kvar måste tillhöra ett valt berättarsteg, och borttagen tal tid listas. En omkastning av nästan hela filmen är inte en storyboard.

## Så ska en Short kännas

Tittaren ska förstå en liten historia utan att någon förklarar den med påhittade fakta.

1. **HOOK.** Det starkaste som faktiskt händer, först, inom första sekunden.
2. **SETUP.** Vem som är i bild, bara om ett spår visar det.
3. **EVENT.** En synlig händelse: en vändning, en gest, ett föremål, en replik.
4. **REACTION.** Ett svar som analysen har belägg för: ett leende, ett skratt i ljudet, en öppen mun.
5. **PAYOFF.** Det ögonblick berättelsen leder till. Här får bilden zooma eller ringa in.
6. **LOOP.** En fråga på slutet, så klippet kan börja om. Ingen uppmaning att prenumerera.

Ett steg som saknar bevis tas bort. Motorn hittar inte på en pinsam tystnad för att mallen har en ruta för det. En film kan ha fyra steg. Har den färre än två steg med bevis ska ingen video exporteras. Då kommer ett tydligt fel i stället.

Grafik är det en människa skulle peka på, inte en dekoration varje sekund. Guiden har en äldre regel om något nytt var 0,7 sekund och text eller pil varje sekund. Den regeln används inte här. Referensfilmen själv har luckor på över en sekund, och ägarens genomgång sa att "grafik varje sekund" är fel känsla. Luckor är tillåtna när inget händer.

Stil som gäller hela tiden, från guiden:

- Vitt och en röd accent, `#FF2D2D`. Inget guld.
- Safe area: 220 pixlar från toppen, 320 från botten, 60 från sidorna, på en bild i 1080×1920.
- Ingenting ritas över ett ansikte.
- Ingen sexualisering. Inga falska fakta om verkliga personer.
- Frågor, tankebubblor och scenanvisningar om det som syns. Inte påhittad dialog som om personen sagt den.
- Inga gissade namn. Ett namn får bara synas om användaren skrev det.

---

## 1. Analys: vad programmet måste se innan det får klippa

Analysen är en lista av bevis. Varje bevis har ett id. Inget senare steg får påstå något som inte pekar på ett sådant id.

Programmet läser längden med ffprobe först, samma regel som i PR #2. Sedan körs stegen ett i taget. På en MacBook Air med 8 GB får inte Whisper, språkmodellen och en bildmodell ligga i minnet samtidigt. Varje tung modell laddas, används, och släpps.

| Steg | Verktyg | Vad som kommer ut | Tid och minne på 8 GB | Ärligt omdöme |
|---|---|---|---|---|
| Längd, storlek, ljudspår | ffprobe, redan i workern | sekunder, bredd, höjd, fps, om ljud finns | under en sekund, försumbart | Krävs. Ingen plan utan detta. |
| Scenklipp | PySceneDetect, eller ffmpeg `select=gt(scene,0.3)` på en liten proxy | tider där bilden byter tagning | några sekunder | Klart genomförbart. |
| Rörelse | OpenCV Farneback på bilder nedskalade till 320 pixlar, var 0,25 s | energi och riktning per ruta | ungefär 5–20 s för en film på 15 s | Klart genomförbart. Räcker för "något rör sig hitåt". |
| Ansikte och personspår | MediaPipe Face Landmarker, ett spår i taget, ihopkopplat med överlapp mellan rutor (IoU) | spår-id, ruta per tid, huvudets mitt | en film på 15 s vid 8 bilder/s: tiotals sekunder, modellen är några megabyte | Klart genomförbart om den körs ensam. Haar som finns idag duger inte: den ser inte leende eller profil tillförlitligt. |
| Mun och leende | Samma Face Landmarker, blendshapes `mouthSmile` och `jawOpen` | bevis "leende" eller "öppen mun" med säkerhet | ingår i steget ovan | Genomförbart. Ett leende är inte ett skratt. |
| Händer och gester | MediaPipe Hand Landmarker, efter att ansiktsmodellen släppts | pekande, hand mot mun, hand som håller något | samma storleksordning som ansikten | Genomförbart för korta klipp. Om steget hoppas över får ingen pil betyda "pekar". |
| Kropp | MediaPipe Pose Landmarker | axlar och höfter, för att hålla utsnittet | valfritt, samma kostnad igen | Inte i första fasen. Ansikte och händer räcker för att rama in. |
| Talets ord | faster-whisper `base`, temperatur 0, som nu | ord med start och slut | sekunder till en halv minut | Behåll `base`. En större whisper-modell tränger undan språkmodellen. |
| Fraser | vanlig kod | hela meningar eller korta fraser, inte lösa ord | omedelbart | Krävs. Det är så "BEAUTIFUL FOR ON" försvinner. |
| Tal mot musik | energin i ljudet plus en liten klassificerare | "tal", "musik", "tystnad" per halvsekund | billigt om klassificeraren är en liten ONNX-fil | Genomförbart. |
| Skratt, applåd, skrik, slag | YAMNet som ONNX eller TFLite, inte hela TensorFlow | en klass, en tid, en säkerhet | modellen är liten. Hela TensorFlow-paketet är det inte. Räkna med några sekunder ljudtid | Genomförbart bara med ett lätt runtime-paket. Saknas det: de bevisen finns inte, och motorn får inte gissa. |
| Röst mot bakgrund | Demucs | två ljudspår | flera GB och lång tid | Inte i den här motorn. Originaljudet ska behållas, inte delas isär. |
| Vem personen är | InsightFace eller liknande | ett namn | tungt, och farligt | Görs inte. Inga gissade kändisnamn. |
| Föremål med fri text | YOLO-World, Grounding DINO | "en Switch", "en ring" | för tungt bredvid övrigt på 8 GB | Görs inte som standard. En hand som håller något får heta "föremål i handen", inte ett produktnamn. |
| Bildmodell som beskriver rutor | en liten VLM, till exempel en 2B-modell, högst 4 bilder | en mening per ruta | bara efter att `qwen2.5:3b` laddats ur. Ofta en extra minut | Av som standard. Även då är texten ett bevis med låg tillit, inte ett frikort att hitta på. |

### Beviset

Varje hittad sak blir ett bevis:

- `id`, till exempel `ev_14`
- `type`, en typ från en fast lista (leende, pekning, fras, scenklipp, skratt, applåd, rörelsetopp, och så vidare)
- `t0` och `t1`, tider i källfilmen
- `trackId`, vilket spår det gäller, om det gäller en person eller en hand
- `confidence`, 0 till 1
- `source`, vilket verktyg som såg det

Inget bevis, inget påstående. "Hon skrattar" kräver ett skratt-bevis i ljudet, inte bara ett leende och inte bara att någon sa ordet skratt. "Pekar" kräver ett handbevis. En låttext är ord som hördes, inte en beskrivning av bilden.

## 2. Nya datastrukturer, i klartext

Allt lagras som JSON bredvid projektet. Den gamla planen (version 1, den som PR #2 renderar) får ligga kvar. Den nya planen är version 2 och används bara när användaren kör den nya vägen.

**VideoAnalysis.** Filens fakta, spår, bevis, fraser, scener, rörelse, ljudklasser. Sparas en gång. En chatfråga läser den igen, den räknar inte om Whisper.

**Track.** Ett spår är en person, ett ansikte, en hand eller ett föremål över tid. Varje prov har en tid och en ruta (x, y, bredd, höjd, 0–1 av bilden). Spåret har inget namn om inte användaren gav det.

**Evidence.** Beviset ovan.

**PhraseSegment.** En fras som hör ihop: texten, start, slut, vilka ord som ingår. En undertext får bara vara en hel fras, eller en hel fras som redan är kort. Den får inte limma sista orden i en fras mot första orden i nästa.

**Storyboard.** Berättelsen innan grafiken ritas. Varje steg har en roll (HOOK, SETUP, EVENT, REACTION, PAYOFF, LOOP), ett källintervall, en lista av bevis-id, och en avsikt på en mening. Avsikten får bara nämna sådant bevisen tillåter.

**EditPlan v2.** Det renderaren läser.

- Klipp på tidslinjen, med källintervall och en utsnittsbana (zoom och mittpunkt över tid).
- Grafik, varje sak kopplad till ett spår och ett bevis.
- Undertexter som fras-enheter, inte som ett glidande fönster av lösa ord.
- Ljudeffekter bundna till ett bevis. Ingen musik. Originaljudet på.
- En hänvisning till storyboarden och till analysens version.

**Versioner.** Varje export är fortfarande en ny mapp, v1, v2, v3. Originalfilen skrivs inte över. Till v2-planen hör en diff: vilken instruktion som tillämpades, vilka id som ändrades, och en kopia av planen före ändringen. Ångra byter aktiv version, det raderar inte filen.

Scheman och ett exempel utan kändisar finns i bilagan.

## 3. Från film till plan

Kedjan är medvetet uppdelad så att den lilla modellen inte får hitta på tider.

1. **Läs längden från filen.** Misslyckas det stoppas allt.
2. **Analys.** Bygger bevis och fraser. Sparas.
3. **Kandidater, utan språkmodell.** Koden föreslår steg. Ett steg är ett intervall runt ett eller flera bevis, utökat så att det inte kapar en fras mitt i. Starkaste ögonblicket, med minst två sorters stöd (tal, ansikte, gest, scen, ljudhändelse), blir hook-kandidat. Övriga kandidater sorteras i källordning och får en föreslagen roll utifrån bevisets typ: en fras kan vara EVENT, ett leende eller ett skratt kan vara REACTION, sista starka ljudet eller gesten kan vara PAYOFF. SETUP är bilden före händelsen om den visar samma spår. LOOP är en kort återkomst till hook-ansiktet, bara om det spåret finns i slutet.
4. **Modellen väljer, den uppfinner inte.** `qwen2.5:3b` får en kort lista: kandidat-id, tider, bevis-id, tillåtna ord. Den får välja vilka kandidater som är med, ordningen, och korta texter från de tillåtna meningarna. Den får inte skicka in egna tider. Den får inte lägga till ett bevis.
5. **Kontroll.** Varje steg pekar på bevis som finns. Inga otillåtna verb. Längden är inte en ensam sekund ur en lång film. Fraserna är hela. Grafik ligger i safe area och inte över ansikten. Originaljud på, musik av.
6. **Lagning, sedan reserv.** Ett försök till får modellen rätta exakt de fel kontrollen listade. Misslyckas det bygger koden planen själv: hook först, sedan de övriga kandidaterna i tidsordning, texter från de färdiga mallfrågorna, grafik bara där en regel i avsnitt 4 träffar. Reservplanen renderas, och svaret säger att modellen inte fick välja texterna. Blir det färre än två giltiga steg är svaret ett fel, och ingen MP4 skrivs.

En omkastning av nästan hela källan (över 85 procent, och det enda som hänt är att en bit lagts först) godkänns inte som viral Short. Då stryks kandidater som inte bär ett eget bevis, tills bågen är en riktig urvalsberättelse. Tal som stryks listas som borttaget, med frasens text, så inget försvinner i tystnad.

## 4. Var grafiken får sitta

Grafik tänds av ett bevis. Saknas beviset ritas den inte.

| Typ | Vilket bevis | Hur den sitter | När |
|---|---|---|---|
| Stor hook-text | hook-stegets bevis | centrerad, versaltopp kring y 262, nedflyttad om ett ansikte är i vägen, aldrig under y 640 | från första bilden, minst så länge att den hinner läsas |
| Undertext av en replik | en fras som valts som nyckelreplik | en rad, hela frasen, i lane under hook-texten | frasens egen tid, efter att tidslinjen räknats om |
| Ring | ett personspår som är stegets ämne, och bara då tittaren ska hitta den personen | runt rutans mitt, radie = rutans halva plus 20 px | medan personen är ämnet, inte på varje ansikte i bild |
| Pil | riktning: rörelse, blick är inte pålitlig nog, pekande hand, eller ett föremål i en hand | spetsen 20–40 px från målet, följer spåret | 0,3 s innan textraden som förklarar den |
| Emoji | reaktion: leende, skratt, öppen mun tillsammans med ett ljudbevis | underkanten 30 px över huvudet, följer spåret | 0,3 s innan sin textrad. Ägarens intervall är 0,3–0,4 s |
| Tankebubbla | inget faktabevis krävs för känslan, men bubblan måste sitta på ett personspår som är i bild, och texten måste vara en fråga eller en uppenbar tanke | bredvid eller över huvudet, svansen mot huvudet, aldrig över ansiktet | minst 1,2 s |
| Zoom eller markering | payoff- eller hook-beviset | utsnittet följer det spåret | bara de stegen, inte en långsam zoom över hela filmen |

Regler som alltid gäller:

- Max en emoji per person, max två i bild.
- Max en markör (pil eller ring, inte båda) per ämne åt gången.
- Hook-text och en till textrad får synas samtidigt. Inte tre textlager.
- Ny grafik högst ungefär var 1,2:e sekund, utom när två bevis verkligen överlappar.
- Ingen grafik för att fylla en lucka.
- Ansiktsrutan plus 12 procent är förbjuden yta. Krock flyttar föremålet: hook-text nedåt, emoji åt sidan, bubbla till andra sidan. Går det inte, tas föremålet bort och det skrivs i planen.
- Rött `#FF2D2D` för första ämnets pil, vit pil med röd kant om ett andra ämne också måste pekas ut. Inget guld.
- Aldrig ring, pil eller zoom på bröst, ben eller kjol. Aldrig på ett spår som bedömts som barn. Den bedömningen är försiktig: osäkert betyder att spåret inte får grafik.
- Emoji väljs från listan som redan finns i `worker/app/safety.py`. Förbjudna emojis är redan listade där.

Vilken emoji som hör till vilket bevis står i bilagan. Ett leende får inte bli 😂. 😂 kräver skratt i ljudet.

## 5. Vad modellen får säga

`qwen2.5:3b` fyller ett litet JSON-svar. Den ser inte hela analysen. Den ser kandidater och tillåtna meningar.

Tillåtna sätt att uttrycka sig, per bevis:

| Bevis | Får sägas | Får inte sägas |
|---|---|---|
| Fras ur talet | frasen själv, som undertext | att bilden "betyder" frasen, om ljudet klassats som musik |
| Leende | "did they smile?", "*a smile*", emoji ☺️ eller 🤭 | "she's laughing", "they're happy together" |
| Skratt i ljudet | "did you hear that?", emoji 😂 eller 😅 | vem de skrattar åt |
| Öppen mun utan skratt | inget känslopåstående | "scream", "shock" |
| Pekande hand | "*points*", "where are they pointing?", en pil | ett mål som inte har ett spår |
| Hand mot mun | "*hand to mouth*", 🫢 | "she's shocked" som faktum |
| Rörelse åt ett håll | en pil åt det hållet | ett motiv för rörelsen |
| Scenklipp | får vara ett klippställe | ingen egen textrad |
| Applåd | "*the room reacts*", fråga | att en viss person applåderar, om inte händerna syns |
| Inget av ovan | ingenting | varje verb om handling eller känsla |

Alla texter är på engelska, som guidens undertexter. Frågor och tankar är lowercase. Scenanvisningar står i asterisker och beskriver bara beviset. Hook-raden är versaler, ett till tre ord, högst 18 tecken, och exakt ett ord får vara rött. Det röda ordet måste finnas i raden.

Innehållsreglerna i `safety.py` körs på varje rad, titel och bubbla. Träff betyder att raden stryks, inte att den skrivs om till en snarlik påståenderad.

Anropet är ett. Temperatur 0. Svaret tvingas till JSON, helst med Ollamas schema i `format`. Om den Ollama-versionen bara kan säga "svara med JSON", gäller samma schema i prompten och kontrollen efteråt. `num_predict` hålls lågt, runt 400 tokens, för att anropet ska hinna på 8 GB. Ett reparationsanrop får samma tak. Sedan tar koden över.

## 6. Renderaren

Renderaren läser bara EditPlan v2. Den hittar inte på klipp.

För varje bildruta:

1. Räkna om tidslinjetid till källtid via klippets start och hastighet.
2. Hämta spårets ruta vid den källtiden. Utsnittet är en mjukad bana mot den rutan, så bilden inte hoppar. Zoomen kommer från planens nyckelbilder, som mest ungefär 1,6 gånger på en 1080-källa i de längre stegen, och kortare topp på hook om källan tål det. Över 2,3 gånger används inte. En källa på 608 pixlars bredd får lägre tak, annars blir bilden gröt.
3. Rita lagren i ordning: ring och pil, emoji och bubbla, frasrad, hook-text. Grafiken ska inte få filmens skärpning som en andra gång.
4. Lägg originaljudet under de nya klippen, i samma ordning som bilden. Ljudeffekter bara där ett bevis har en effekt i planen, svagt. Ingen musik.
5. Mät till cirka −14 LUFS, som idag.

Förhandsvisning efter en chattändring ritas i 540×960. Slutfilen är 1080×1920. Analysen ligger kvar på disk, så en ändring av textstorlek inte kör Whisper igen.

Minnesregeln: en modell i taget. Whisper släpps innan språkmodellen tillfrågas. En valfri bildmodell släpps innan språkmodellen laddas tillbaka. Bildrutor för en lång tagning strömmas. De hålls inte alla i minnet, vilket redan är en risk i dagens renderare på en lång 1080-källa.

## 7. När du skriver en uppföljning

En mening i chatten ska ändra en sak.

Modellen klassar meningen till en av några få operationer. Den skriver inte en ny plan.

| Du skriver | Operation | Vad som får ändras |
|---|---|---|
| "Gör hooken större" | `scale_text` med mål `hook` | hook-textens storlek |
| "Gör texten större" | `scale_text` med mål `captions` | frasradernas storlek, samma texter |
| "Ta bort pilen" | `remove_graphic` typ `arrow` | en pil, den senaste om du inte pekar ut en annan |
| "Zooma in på personen" | `zoom_track` mot stegets ämnesspår | zoomnycklarna på det klipp som redan följer spåret |
| "Ångra" | `undo` | aktiv version, inget annat |

Saknas målet ("pilen" men det finns ingen pil) blir svaret ett fel. Ingen ny export.

Sedan: applicera patchen på en kopia, kontrollera kopian, skriv en ny version. Diffen listas. Testet är att alla andra id har samma text, tider och spår som före patchen. v1-filen ligger kvar.

## 8. Sidan

Sidan finns redan: `app/page.tsx` visar `app/_components/editor.tsx`. Där kan man ladda upp, skriva i chatten, se förlopp, spela en version och ladda ner MP4. Originalet kan visas. Ångra finns. Det ska fortsätta fungera.

Tillägg, utan att byta ut det flödet:

- En knapp, **Gör min Short**, syns när en video är uppladdad och innan första storyboard-versionen. Den skickar texten "Gör den här till en viral Short" och ber om motor v2. Fritextfältet är kvar.
- Förloppet återanvänder raden som redan finns. Nya stegnamn: läser filen, analyserar, bygger storyboard, skriver texter, kontrollerar, renderar. Gamla stegnamn sitter kvar så att v1-vägen fortfarande visar text.
- Förhandsvisningen är samma spelare. Ny version, ny knapp v2, v3, som idag.
- Uppföljning skrivs i samma fält. Förslagschippar kan bli "Gör hooken större", "Ta bort pilen", "Zooma in på personen", "Gör texten större". De anropar samma meddelandeväg.
- API:t för meddelanden får ett valfritt fält `engine`. Utan fältet körs dagens väg, alltså PR #2. Med `storyboard` körs den nya kedjan. Gamla klienter går inte sönder.

Ingen tidslinje i standardvyn. Ingen leverans till Drive i den här designen. Guidens Drive-steg är en senare produkt, inte en del av motorn.

## 9. Faser, tester, omfattning, risker

Kalender tid anges inte. Omfattningen är vilka delar som rörs.

| Fas | Vad som byggs | Omfattning | Risk |
|---|---|---|---|
| 1. Bevis och fraser | analyssteg och sparad VideoAnalysis | medel, mest `worker/app/analysis.py` | MediaPipe eller ONNX går inte att installera på just den Macen. Då saknas de bevisen, och motorn måste neka grafik i stället för att gissa. |
| 2. Kandidater och kontroll | storyboard utan att modellen väljer tider | medel, ny modul bredvid `auto_edit.py` | För hård klippning, eller att 85-procentsregeln råkar krocka med en film som redan är tät. Testa på flera filmer innan regeln låses. |
| 3. Modellens text | ett litet anrop, sedan reserv | liten | 3B-modellen hittar på ett verb. Kontrollen måste släppa igenom reserven, inte texten. |
| 4. Grafik som följer spår | placering i renderaren, de sprites som redan finns i `gfx.py` | medel | Ansikte täcks. Prestanda på 1080. Förhandsvisning i 540 först. |
| 5. Chattens patch | avsiktsklassning och diff | liten till medel | En mening raderar andra rader. Det är buggdefinitionen, och testet nedan är spärren. |
| 6. Knappen | `editor.tsx` och det valfria fältet | liten | v1-chatten slutar fungera om fältet blir obligatoriskt. Det får inte bli det. |

PR #2 rörs inte i de här faserna förrän en fas uttryckligen byggs ovanpå den branchen. Serverstart, `config.py`, FFmpeg-filter och modellnamnet `qwen2.5:3b` lämnas som de är. Nya bibliotek läses in från koden som en valfri import, inte genom att ändra standardvärdena för modellen eller Whisper.

### Acceptanstester

Samma svit körs på minst fyra filmer: Met Gala-klippet, Zendaya-klippet, och två andra filmer som inte är de klippen. Testerna får inte innehålla namn, tider eller repliker från en viss film. De tittar på strukturen.

- Varje storyboard-steg har minst ett `evidenceId`, och det id:t finns i analysen och överlappar stegets källtid.
- Ingen textrad innehåller ett verb som bevisen inte tillåter. "laugh" utan skratt-bevis failar. "point" utan handbevis failar.
- Ingen undertext är en skarv av två fraser. Ordens id ska vara ett sammanhängande spann i en fras. "BEAUTIFUL FOR ON" är just en sådan skarv och ska faila.
- Varje grafik har ett spår eller ett bevis, och spårets ruta finns under grafikens tid.
- Utgångslängden är inte ungefär 1 sekund om källan är klart längre.
- Planen är inte en omkastning av mer än 85 procent av källan.
- `music` är false och `keepOriginal` är true.
- "Gör texten större" ändrar bara storlek. Samma frastexter, samma klipp, samma grafik-id.
- "Ta bort pilen" tar bort pil-id:n och lämnar övriga id orörda.
- "Gör hooken större" ändrar bara hook-textens storlek.
- En film där analysen inte kan bygga två steg ger ett fel och ingen ny MP4.
- v1-filen finns kvar efter v2.

---

# Appendix. Technical specification

Normative for implementation. The Swedish sections above are the product meaning. If they disagree, the Swedish product rules win on pacing and content, and this appendix wins on field names.

PR #2 remains the v1 path. Engine v2 runs only when the request sets `engine: "storyboard"`. v1 coverage rules are not applied to a v2 storyboard. v2 has its own arc rules. Both paths still require a probed file duration before any plan exists, reject an unreadable text span, keep original audio, and set music to false.

## A. Evidence vocabulary

`Evidence.type` is one of:

`scene_cut`, `motion`, `face_track`, `smile`, `mouth_open`, `point`, `hand_to_mouth`, `held_object`, `speech_phrase`, `speech_word`, `silence`, `music`, `laughter`, `applause`, `scream`, `onset`

`source` is one of:

`ffprobe`, `scenedetect`, `farneback`, `mediapipe_face`, `mediapipe_hand`, `whisper`, `phrase_segmenter`, `energy_vad`, `yamnet`, `vlm`

A VLM sentence is stored as evidence type `motion` only if it was not used to invent a verb. Prefer not to store VLM prose as an event the model can quote. If stored, `source` is `vlm` and `confidence` is capped at 0.4. No graphic may rely on a `vlm` evidence item alone.

Thresholds, initial and tunable, not film-specific:

- smile: `mouthSmile` mean above 0.45 for at least 0.20 s
- mouth_open: `jawOpen` above 0.40 for at least 0.15 s, and not overlapping a `speech_phrase`
- point: index finger extended, other fingers folded, wrist-to-tip direction stable for 0.20 s
- laughter / applause / scream: classifier score at least 0.35 for at least 0.20 s
- motion peak: optical-flow magnitude above the clip's 90th percentile
- phrase boundary: punctuation, or a gap between words of at least 0.45 s, or a cap of 6 words

## B. JSON Schemas

### B.1 Evidence

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://capcutpro.local/schema/evidence.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["id", "type", "t0", "t1", "confidence", "source"],
  "properties": {
    "id": {"type": "string", "pattern": "^ev_[a-z0-9]+$"},
    "type": {"enum": ["scene_cut", "motion", "face_track", "smile", "mouth_open", "point", "hand_to_mouth", "held_object", "speech_phrase", "speech_word", "silence", "music", "laughter", "applause", "scream", "onset"]},
    "t0": {"type": "number", "minimum": 0},
    "t1": {"type": "number", "minimum": 0},
    "trackId": {"type": ["string", "null"]},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "source": {"type": "string"},
    "payload": {"type": "object"}
  }
}
```

`payload` may hold `text` for a phrase, `dx`/`dy` for motion, or `scoreName` for a classifier. It may not hold a claim about a real person.

### B.2 Track

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://capcutpro.local/schema/track.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["id", "kind", "samples"],
  "properties": {
    "id": {"type": "string", "pattern": "^trk_[a-z0-9]+$"},
    "kind": {"enum": ["person", "face", "hand", "object"]},
    "label": {"type": ["string", "null"]},
    "samples": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["t", "bbox"],
        "properties": {
          "t": {"type": "number"},
          "bbox": {
            "type": "array",
            "items": {"type": "number", "minimum": 0, "maximum": 1},
            "minItems": 4,
            "maxItems": 4
          }
        }
      }
    }
  }
}
```

`label` is null unless the user typed a name. The analyzer never writes a celebrity name.

### B.3 PhraseSegment

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://capcutpro.local/schema/phrase.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["id", "text", "t0", "t1", "wordEvidenceIds"],
  "properties": {
    "id": {"type": "string", "pattern": "^ph_[a-z0-9]+$"},
    "text": {"type": "string"},
    "t0": {"type": "number"},
    "t1": {"type": "number"},
    "wordEvidenceIds": {"type": "array", "items": {"type": "string"}, "minItems": 1}
  }
}
```

### B.4 Storyboard

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://capcutpro.local/schema/storyboard.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["version", "beats", "dropped"],
  "properties": {
    "version": {"const": 1},
    "beats": {
      "type": "array",
      "minItems": 2,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "role", "src", "evidenceIds", "intent"],
        "properties": {
          "id": {"type": "string", "pattern": "^bt_"},
          "role": {"enum": ["HOOK", "SETUP", "EVENT", "REACTION", "PAYOFF", "LOOP"]},
          "src": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
          "evidenceIds": {"type": "array", "items": {"type": "string"}, "minItems": 1},
          "intent": {"type": "string", "maxLength": 140},
          "candidateId": {"type": "string"}
        }
      }
    },
    "dropped": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["src", "reason"],
        "properties": {
          "src": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
          "reason": {"type": "string"},
          "phraseId": {"type": "string"}
        }
      }
    }
  }
}
```

### B.5 EditPlan v2

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://capcutpro.local/schema/edit-plan-v2.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["engine", "revision", "analysisId", "storyboard", "clips", "graphics", "captions", "sfx", "audio", "style"],
  "properties": {
    "engine": {"const": "storyboard"},
    "revision": {"type": "integer", "minimum": 1},
    "analysisId": {"type": "string"},
    "storyboard": {"$ref": "https://capcutpro.local/schema/storyboard.json"},
    "clips": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "beatId", "src", "speed", "reframe"],
        "properties": {
          "id": {"type": "string"},
          "beatId": {"type": "string"},
          "src": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
          "speed": {"type": "number", "minimum": 0.5, "maximum": 1.25},
          "reframe": {
            "type": "array",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "required": ["t", "zoom", "trackId"],
              "properties": {
                "t": {"type": "number"},
                "zoom": {"type": "number", "minimum": 1, "maximum": 2.3},
                "trackId": {"type": "string"},
                "ease": {"enum": ["linear", "easeOutCubic"]}
              }
            }
          }
        }
      }
    },
    "graphics": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "type", "t", "evidenceIds"],
        "properties": {
          "id": {"type": "string"},
          "type": {"enum": ["headline", "caption", "bubble", "emoji", "arrow", "circle"]},
          "t": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
          "text": {"type": "string", "maxLength": 48},
          "accent": {"type": "string"},
          "emoji": {"type": "string"},
          "evidenceIds": {"type": "array", "items": {"type": "string"}, "minItems": 1},
          "trackId": {"type": ["string", "null"]},
          "anchor": {"enum": ["above", "beside", "lane", "center"]}
        }
      }
    },
    "captions": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "phraseId", "text", "t"],
        "properties": {
          "id": {"type": "string"},
          "phraseId": {"type": "string"},
          "text": {"type": "string"},
          "t": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
          "fontSize": {"type": "integer", "minimum": 48, "maximum": 96}
        }
      }
    },
    "sfx": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "type", "t", "evidenceIds"],
        "properties": {
          "id": {"type": "string"},
          "type": {"enum": ["whoosh", "pop", "thump"]},
          "t": {"type": "number"},
          "evidenceIds": {"type": "array", "items": {"type": "string"}, "minItems": 1},
          "gainDb": {"type": "number", "maximum": -8}
        }
      }
    },
    "audio": {
      "type": "object",
      "additionalProperties": false,
      "required": ["keepOriginal", "music", "targetLufs"],
      "properties": {
        "keepOriginal": {"const": true},
        "music": {"const": false},
        "targetLufs": {"const": -14}
      }
    },
    "style": {
      "type": "object",
      "additionalProperties": false,
      "required": ["fill", "accent", "safeTop", "safeBottom", "safeSide"],
      "properties": {
        "fill": {"const": "#FFFFFF"},
        "accent": {"const": "#FF2D2D"},
        "ink": {"const": "#0A0A0A"},
        "safeTop": {"const": 220},
        "safeBottom": {"const": 320},
        "safeSide": {"const": 60}
      }
    }
  }
}
```

Captions are phrase units. A graphic of type `caption` is not a second copy of the words. The `captions` array is what the renderer draws for speech. Headlines, bubbles, emoji, arrows, and circles live in `graphics`.

### B.6 Plan diff

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["op", "changedIds", "unchangedIds"],
  "properties": {
    "op": {"type": "string"},
    "changedIds": {"type": "array", "items": {"type": "string"}},
    "unchangedIds": {"type": "array", "items": {"type": "string"}},
    "note": {"type": "string"}
  }
}
```

## C. Generic example

A 12-second clip of two people at a noisy table. No names. Person track `trk_a` turns. Person track `trk_b` smiles. A phrase "look at that" is heard. Applause follows. This example is invented to show the shape. It is not a fixture for a real file.

```json
{
  "engine": "storyboard",
  "revision": 1,
  "analysisId": "an_example",
  "storyboard": {
    "version": 1,
    "beats": [
      {
        "id": "bt_hook",
        "role": "HOOK",
        "src": [6.4, 7.3],
        "evidenceIds": ["ev_smile", "ev_face_b"],
        "intent": "Open on the smile that the face track actually shows.",
        "candidateId": "cd_3"
      },
      {
        "id": "bt_event",
        "role": "EVENT",
        "src": [2.0, 3.4],
        "evidenceIds": ["ev_phrase", "ev_turn"],
        "intent": "The heard phrase, kept whole, over the turn.",
        "candidateId": "cd_1"
      },
      {
        "id": "bt_reaction",
        "role": "REACTION",
        "src": [6.4, 7.6],
        "evidenceIds": ["ev_smile"],
        "intent": "Question about the smile. Not a claim that anyone laughed.",
        "candidateId": "cd_3"
      },
      {
        "id": "bt_payoff",
        "role": "PAYOFF",
        "src": [9.8, 11.2],
        "evidenceIds": ["ev_applause"],
        "intent": "The room sound is the button. Zoom only here.",
        "candidateId": "cd_4"
      }
    ],
    "dropped": [
      {"src": [0.0, 1.6], "reason": "No evidence stronger than presence. Not part of the arc."},
      {"src": [7.6, 9.8], "reason": "Silence between the smile and the applause."}
    ]
  },
  "clips": [
    {"id": "cl_1", "beatId": "bt_hook", "src": [6.4, 7.3], "speed": 1, "reframe": [{"t": 0, "zoom": 1.4, "trackId": "trk_b", "ease": "easeOutCubic"}]},
    {"id": "cl_2", "beatId": "bt_event", "src": [2.0, 3.4], "speed": 1, "reframe": [{"t": 0, "zoom": 1.1, "trackId": "trk_a", "ease": "linear"}]},
    {"id": "cl_3", "beatId": "bt_payoff", "src": [9.8, 11.2], "speed": 1, "reframe": [{"t": 0, "zoom": 1.3, "trackId": "trk_b", "ease": "easeOutCubic"}]}
  ],
  "graphics": [
    {"id": "g_hook", "type": "headline", "t": [0.0, 1.1], "text": "THAT LOOK", "accent": "LOOK", "evidenceIds": ["ev_smile"], "trackId": null, "anchor": "center"},
    {"id": "g_emoji", "type": "emoji", "t": [0.0, 1.1], "emoji": "☺️", "evidenceIds": ["ev_smile"], "trackId": "trk_b", "anchor": "above"},
    {"id": "g_bubble", "type": "bubble", "t": [1.4, 2.8], "text": "did they smile?", "evidenceIds": ["ev_smile", "ev_face_b"], "trackId": "trk_b", "anchor": "beside"}
  ],
  "captions": [
    {"id": "cap_1", "phraseId": "ph_look", "text": "look at that", "t": [1.1, 2.5], "fontSize": 64}
  ],
  "sfx": [
    {"id": "sx_1", "type": "pop", "t": 0.0, "evidenceIds": ["ev_smile"], "gainDb": -10}
  ],
  "audio": {"keepOriginal": true, "music": false, "targetLufs": -14},
  "style": {"fill": "#FFFFFF", "accent": "#FF2D2D", "ink": "#0A0A0A", "safeTop": 220, "safeBottom": 320, "safeSide": 60}
}
```

The emoji starts with the hook. The question bubble starts about 0.3 s after the emoji becomes readable, which is the lead the owner asked for. The caption is the whole phrase `ph_look`. Nothing joins it to the next words.

## D. Candidate generation

```python
def candidates(analysis, duration):
    phrases = analysis.phrases
    evid = [e for e in analysis.evidence if e.confidence >= MIN[e.type]]
    raw = []
    for e in evid:
        if e.type in {"silence", "music", "speech_word", "face_track"}:
            continue
        span = snap_to_phrases(e.t0, e.t1, phrases)  # expand, never cut inside a phrase
        if span[1] - span[0] < 0.4:
            continue
        raw.append(Candidate(span, evidence_ids=[e.id], role=ROLE_OF[e.type], track_id=e.trackId))
    merged = merge_overlaps(raw)  # union evidence ids, keep the stronger role
    hook = best_hook(merged)      # highest score with at least two evidence families
    if hook is None:
        return Error("Inget ögonblick är starkt nog för en hook. Ingen video exporteras.")
    ordered = [hook] + [c for c in merged if c is not hook and c.role != "HOOK"]
    # Do not keep a candidate merely because speech exists inside it.
    return ordered

ROLE_OF = {
    "smile": "REACTION",
    "laughter": "REACTION",
    "mouth_open": "REACTION",
    "speech_phrase": "EVENT",
    "point": "EVENT",
    "hand_to_mouth": "EVENT",
    "held_object": "EVENT",
    "motion": "EVENT",
    "applause": "PAYOFF",
    "scream": "PAYOFF",
    "scene_cut": None,  # a cut point, not a beat
}
```

`snap_to_phrases` extends the span to the phrase edges that overlap it. It does not pull in the next phrase. That is the rule that forbids a caption like "BEAUTIFUL FOR ON".

Hook score is the count of distinct families inside a 1.2 s window: speech, face-expression, hand, scene_cut, audio-event, motion. A window needs two families. Ties are broken by confidence, then by earlier time. The code records the families on the candidate so the reply can cite them.

SETUP is added by code, not by the model, only when a face track from the hook is also visible at least 0.8 s earlier. LOOP is added only when that same track is visible in the last 1.5 s of the source. If either condition fails, the role is absent.

After the model selects a subset, `cover_fraction(selected) > 0.85` and the edit is only "move the hook first" means the selector kept too much. Drop the lowest-scoring non-hook candidate and validate again. Stop when the arc is under 0.85 or only the hook and one other beat remain. If that still cannot be formed, return an error.

A result whose timeline is about 1 second is rejected unless the probed duration is under 2 seconds. "About 1 second" means under 2.5 seconds while the source is at least 5 seconds.

## E. Constrained Qwen call

The prompt is the entire contract. Keep it short. Send at most eight candidates. Do not send raw word lists, face boxes, or the user video.

System text:

```text
You fill a storyboard for a vertical short. You do not invent events, times, or names.
Reply with JSON only, matching the schema. Temperature is fixed by the caller.

Rules:
- Use only candidateId values from CANDIDATES.
- Every chosen beat must copy that candidate's evidenceIds. Do not add ids.
- text.kind is "headline", "bubble", or "caption".
- headline is 1 to 3 uppercase words from ALLOWED for those evidence ids. One accent word, and that word is inside the headline.
- bubble is a lowercase question or inner thought from ALLOWED, or empty if none fit.
- caption text must equal the phrase text already on the candidate. Do not rewrite it.
- If you cannot say it from ALLOWED, omit the text. Do not paraphrase.
- Never use: laugh, laughing, point, pointing, scream, dating, pregnant, drunk, or a person's name, unless that exact word is in ALLOWED.
- At most one headline, one bubble, and one caption.
```

User payload:

```json
{
  "prompt": "Gör den här till en viral Short",
  "duration_s": 12.0,
  "candidates": [
    {
      "candidateId": "cd_3",
      "role": "REACTION",
      "src": [6.4, 7.3],
      "evidenceIds": ["ev_smile", "ev_face_b"],
      "trackId": "trk_b",
      "allowed": {
        "headline": ["THAT LOOK", "THE SMILE"],
        "bubble": ["did they smile?", "wait… that smile"],
        "emoji": ["☺️", "🤭"]
      }
    },
    {
      "candidateId": "cd_1",
      "role": "EVENT",
      "src": [2.0, 3.4],
      "evidenceIds": ["ev_phrase", "ev_turn"],
      "trackId": "trk_a",
      "phraseId": "ph_look",
      "phraseText": "look at that",
      "allowed": {"caption": ["look at that"]}
    }
  ]
}
```

Response schema, passed as Ollama `format` when the installed Ollama accepts a schema object. Otherwise `format: "json"` and this schema is validated locally.

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["selected", "texts"],
  "properties": {
    "selected": {
      "type": "array",
      "maxItems": 6,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["candidateId"],
        "properties": {"candidateId": {"type": "string"}}
      }
    },
    "texts": {
      "type": "array",
      "maxItems": 4,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["candidateId", "kind", "text"],
        "properties": {
          "candidateId": {"type": "string"},
          "kind": {"enum": ["headline", "bubble", "caption"]},
          "text": {"type": "string", "maxLength": 48},
          "accent": {"type": "string"}
        }
      }
    }
  }
}
```

Call parameters: `temperature=0`, `num_predict=400`, model `qwen2.5:3b`, one repair call. The repair user message is the validator errors plus the previous JSON, and it says "fix only the listed errors". If the second reply still fails, discard both replies and build the fallback. The chat reply must say that the texts came from the fallback, not that the model succeeded.

Allowed-line tables are generated by code from evidence types, not written by the model. Example rows:

| Evidence present | headline choices | bubble choices | emoji |
|---|---|---|---|
| smile | THAT LOOK, THE SMILE | did they smile? | ☺️ 🤭 |
| laughter | THE LAUGH, HEAR THAT | did you hear that? | 😂 😅 |
| point | THE POINT, LOOK THERE | where are they pointing? | none |
| applause | THE ROOM, HEAR THAT | did the room react? | none |
| speech phrase | none (caption is the phrase) | none | none |
| smile without laughter | THAT LOOK | did they smile? | ☺️ only, never 😂 |

`laugh`, `point`, and `scream` are absent from every string unless the matching evidence type is on that candidate. The validator also rejects those stems with a word-boundary regex if they appear outside `allowed`.

## F. Validator

```python
def validate(plan, analysis, probed_duration):
    errors = []
    if probed_duration <= 0:
        errors.append("Filens längd är inte läst.")
    for beat in plan.storyboard.beats:
        if not beat.evidenceIds:
            errors.append(f"{beat.id} saknar bevis.")
        for eid in beat.evidenceIds:
            ev = analysis.evidence_by_id(eid)
            if ev is None or not overlaps(beat.src, (ev.t0, ev.t1)):
                errors.append(f"{beat.id} citerar {eid} som inte finns på den tiden.")
        if cuts_inside_phrase(beat.src, analysis.phrases):
            errors.append(f"{beat.id} kapar en fras.")
    if timeline_duration(plan) < 2.5 and probed_duration >= 5:
        errors.append("Resultatet är för kort för den här filen.")
    if source_cover(plan) > 0.85 and is_hook_plus_remainder(plan):
        errors.append("Planen är nästan hela filmen, bara omkastad.")
    for cap in plan.captions:
        phrase = analysis.phrase(cap.phraseId)
        if phrase is None or cap.text != phrase.text:
            errors.append(f"{cap.id} är inte en hel fras.")
    for g in plan.graphics:
        if unsupported_verb(g.text, evidence_types(g.evidenceIds, analysis)):
            errors.append(f"{g.id} påstår något bevisen inte visar.")
        if g.trackId and face_overlap(g, analysis):
            errors.append(f"{g.id} hamnar över ett ansikte.")
    if plan.audio.music or not plan.audio.keepOriginal:
        errors.append("Originaljudet ska vara kvar, utan musik.")
    return errors
```

`unsupported_verb` checks the text against the allow table for the cited types and against `safety.lint_text`. A question mark does not make a banned topic legal. "is she pregnant?" fails the existing blocklist.

Phrase integrity for captions: the caption's word evidence ids must equal the phrase's `wordEvidenceIds` in order. A window that takes the tail of phrase A and the head of phrase B fails even if the words are contiguous on the output timeline. That is the "BEAUTIFUL FOR ON" check, and it does not mention those words in the test. The test builds two phrases and asserts a joined window is rejected.

## G. Graphics placement

```python
SAFE = dict(top=220, bottom=320, side=60)  # pixels at 1080x1920
FACE_PAD = 0.12

def place(graphic, tracks, faces, t):
    if graphic.type == "headline":
        y = 262
        box = measure(graphic)
        if intersects(box, inflate(faces, FACE_PAD)):
            y = min(560, y + 40)  # step until clear or give up
        return clamp_safe(box.at(y), SAFE)
    track = tracks.at(graphic.trackId, t)
    if graphic.type == "emoji":
        # bottom of emoji is 30 px above head top; head top is face.top - 0.35*face.h
        anchor = head_top(track) - 30
        box = measure(graphic).above(anchor)
    elif graphic.type == "arrow":
        box = arrow_toward(track.center, tip_gap_px=30)
    elif graphic.type == "circle":
        box = circle_around(track.bbox, pad_px=20)
    elif graphic.type == "bubble":
        box = bubble_beside(track, tail_toward=track.center)
    if intersects(box, inflate(faces, FACE_PAD)) or outside(box, SAFE):
        box = flip_side(box)
    if intersects(box, inflate(faces, FACE_PAD)) or outside(box, SAFE):
        return None  # omit, record in the plan diff
    return box
```

Emoji lead: if a caption or bubble explains the same evidence, the emoji interval starts 0.3 s before that text and not before the beat starts. Arrow lead uses the same 0.3 s. Density: reject a new graphic whose start is under 1.2 s after the previous graphic start, unless both evidence intervals overlap.

SFX is bound the same way. `pop` only on an emoji, arrow, or circle that survived placement. `whoosh` only on a cut between beats. `thump` only on a headline. No sound for a graphic that was omitted. No music bed.

## H. Feedback patch

```python
OPS = {
    "scale_text": {"target": ["hook", "captions"], "factor": [1.15, 1.35]},
    "remove_graphic": {"type": ["arrow", "circle", "emoji", "bubble"]},
    "zoom_track": {"amount": [0.15, 0.35]},
    "undo": {},
}

def apply_followup(plan, analysis, user_text):
    op = classify(user_text)  # qwen, temperature 0, schema = one of OPS
    if op.name not in OPS:
        return Error("Jag kan inte göra just den ändringen utan att röra resten.")
    nxt = deepcopy(plan)
    changed = []
    if op.name == "scale_text" and op.target == "hook":
        g = single(nxt.graphics, type="headline")
        g.fontSize = clamp(g.fontSize * op.factor)
        changed = [g.id]
    elif op.name == "scale_text" and op.target == "captions":
        for c in nxt.captions:
            c.fontSize = clamp(c.fontSize * op.factor)
            changed.append(c.id)
    elif op.name == "remove_graphic":
        g = last_of(nxt.graphics, op.type)
        if g is None:
            return Error("Det finns ingen sådan grafik att ta bort.")
        nxt.graphics.remove(g)
        changed = [g.id]
    elif op.name == "zoom_track":
        clip = clip_for_subject(nxt)
        for key in clip.reframe:
            key.zoom = min(key.zoom + op.amount, zoom_cap(analysis.source))
        changed = [clip.id]
    errors = validate(nxt, analysis, analysis.source.duration)
    if errors:
        return Error(errors)
    diff = diff_ids(plan, nxt, changed)
    assert set(diff.unchangedIds).isdisjoint(changed)
    return nxt, diff
```

`classify` sees the user sentence and a list of existing ids with types, not the whole plan. The implementation test freezes a plan, runs each of the four Swedish sentences, and asserts deep equality on every object whose id is not in `changedIds`.

Undo restores `activeVersion` to the previous rendered version. It does not delete `versions/vN`.

## I. Renderer contract

```python
def render(plan, analysis, src, out, preview=False):
    size = (540, 960) if preview else (1080, 1920)
    for frame_i, out_t in frames(plan, fps=30):
        src_t = map_time(plan.clips, out_t)
        cam = reframe_at(plan, analysis.tracks, src_t)
        img = warp(read_frame(src, src_t), cam, size)
        for layer in order(plan, out_t):  # circle/arrow, emoji/bubble, caption, headline
            box = place(layer, analysis.tracks, faces_at(src_t), out_t)
            if box:
                img = composite(img, sprite(layer), box)
        write_frame(img)
    mix = concat_original_audio(src, plan.clips)
    mix = add_sfx(mix, plan.sfx)          # never a music stem
    master = loudnorm(mix, I=-14, TP=-1.2)
    mux(video, master, out)
```

Preview is the path used after a follow-up. The full 1080 file is the version the download button points at, rendered after validation of the same plan. If a follow-up only changes font size, the preview may ship first. The version directory still ends as 1080×1920 H.264, AAC, about −14 LUFS, matching what the current renderer already promises. This design does not change the FFmpeg filter graph. It adds a crop window before the existing scale, implemented as input to the current frame path, not as a new filter-script flag.

## J. UI mapping

Existing pieces, unchanged in role:

- `app/page.tsx` renders `Editor`.
- Upload posts to `/api/worker/projects`.
- Chat posts to `/api/worker/projects/:id/messages`.
- Versions and the original are separate URLs. Undo posts to `/undo`.
- Progress is already a `status` event with a `step` string, labeled in `STEP_LABELS`.

Add:

- Button label `Gör min Short`. It calls `send` with the exact prompt `Gör den här till en viral Short` and body `{ text, engine: "storyboard" }`.
- The fetch in `editor.tsx` gains an optional argument. The default body stays `{ text }` so a suggestion chip on the v1 path does not flip the engine.
- New `STEP_LABELS` keys: `storyboard`, `wording`, `validate`. Do not remove the current keys.
- Follow-up chips: `Gör hooken större`, `Ta bort pilen`, `Zooma in på personen`, `Gör texten större`. They send `engine: "storyboard"` only when the active plan's engine is already `storyboard`.

v1 projects keep rendering through the current plan. A missing `engine` field means v1.

## K. What this design deliberately does not do

- No per-creator or per-celebrity branches.
- No guessed names, no face recognition.
- No background music, no Demucs stem that replaces the original.
- No graphics inserted only to satisfy a 0.7-second quota.
- No VLM in the default path.
- No change to `qwen2.5:3b`, `worker/app/config.py`, the FFmpeg filter invocation, or the start scripts.
- No merge of PR #2 as part of writing this document.
