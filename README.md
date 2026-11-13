# TikTok Ultra Downloader

Bot Telegram asincron pentru videoclipuri **publice** TikTok, YouTube Shorts și
Instagram Reels, precum și fotografii/slideshow-uri TikTok. Include butonul
**🎵 Doar audio**, cu extragere și conversie în MP3, și CLI independent.
Videoclipurile originale se păstrează fără recodare, cu selecții distincte pentru varianta
fără overlay TikTok și calitatea maximă disponibilă. Proiectul activ este rădăcina `Dowland-TT/`. Copia istorică din
`tiktok-ultra-downloader/` este păstrată local, ignorată de Git și exclusă din imagine.

Nu sunt necesare token Telegram, cont Oracle sau Docker pentru testele automate.
Deployment-ul și conectarea reală se fac ulterior, explicit.

## Funcționare și limite

1. Utilizatorul trimite un link TikTok, `youtube.com/shorts/ID`, `youtu.be/ID`
   sau `instagram.com/reel/ID/` (și `/reels/ID/`). Botul validează separat gazdele
   și rutele fiecărei platforme și elimină parametrii de tracking. Linkurile YouTube
   scurte sunt tratate ca linkuri către un singur clip; profilurile, playlisturile
   și rutele YouTube `/watch` nu sunt acceptate.
2. Workerul reutilizează extragerea paginii publice din yt-dlp pentru a identifica
   video sau `imagePost`, inclusiv linkuri `/photo/` și postări foto pe URL `/video/`.
   Pentru linkurile `/photo/`, cererea de metadate folosește ruta `/video/` a aceluiași
   ID: vizualizatorul foto poate omite `webapp.video-detail`. Tipul conținutului
   este stabilit din `imagePost`, nu din această rută.
   Shorts și Reels folosesc extractoarele yt-dlp `Youtube`, respectiv `Instagram`;
   metadatele și fluxurile se verifică pentru fiecare postare.
3. Video: mereu `quality="high"`, `watermark="without"`, inclusiv pentru utilizatorii
   cu setări vechi. Se alege cea mai înaltă rezoluție disponibilă dintre variantele
   identificate de sursă fără watermark, apoi FPS și bitrate. Nu există opțiuni
   pentru reducerea calității sau acceptarea unui watermark. Dacă lipsesc variantele
   identificate fără watermark, video-ul nu se descarcă; audio poate rămâne disponibil.
   „Ca fișier” trimite originalul variantei selectate ca document. Dimensiunile din
   meniu sunt **raportate de sursă**; caption-ul după descărcare arată rezoluția verificată.
   MP4/H.264 compatibil, cu AAC sau fără audio, se reutilizează. Pentru redarea
   automată a altor containere se verifică fluxurile reale cu ffprobe și se încearcă
   întâi remuxare MP4 fără recodare, cu `+faststart`. Recodarea H.264/AAC se folosește
   doar pentru fluxuri incompatibile sau dacă remuxarea/verificarea rezultatului eșuează. Documentul original
   rămâne fără conversie și se trimite la apăsarea „Ca fișier”. Butoanele cunoscute
   ca prea mari sunt ascunse.
4. Foto: toate imaginile se descarcă în ordinea postării. Se aplică preferințele de calitate și
   watermark; implicit se alege rezoluția maximă raportată, cu preferință pentru
   lipsa watermark-ului la egalitate.
   URL-urile alternative ale aceleiași imagini sunt încercate la erori de rețea.
   Lipsa unei imagini oprește întreaga descărcare, fără a publica un album incomplet.
5. Utilizatorul poate alege fișiere originale, album foto sau ZIP original.
   Grupurile au 2–10 elemente; ultimul element singur se trimite separat.
   Albumul foto poate fi comprimat de Telegram; documentele și ZIP păstrează octeții.
   Imaginile peste 10 MB, cu dimensiuni/raport incompatibil sau în alte formate decât
   JPEG/PNG se trimit ca documente, fără redimensionare. Toate se verifică înainte de upload.
6. Video-urile se verifică prin ffprobe; fotografiile și prin decodare FFmpeg.
   Cache-ul SQLite reutilizează `file_id` video/document cu selecția corespunzătoare.
   Fotografiile folosesc partajarea fișierelor locale între cereri simultane;
   nu există cache persistent al albumelor foto sau al ZIP-urilor. Preview-urile
   Telegram nu sunt reutilizate drept fișiere originale.
7. **🎵 Doar audio** este disponibil pentru video, cu excepția surselor despre
   care se știe că nu au sunet. Se preferă un flux audio separat; altfel se extrage
   pista din video. MP3: 192 kbps,
   fără promisiunea îmbunătățirii sursei. Preferința de watermark nu afectează audio.
   Rezultatul este verificat cu ffprobe, limitat ca dimensiune și trimis prin
   `sendAudio`; fișierele temporare sunt șterse. Cache-ul audio este separat de
   video/document/ZIP și include URL-ul platformei în identitatea selecției.
   Testele live opționale se activează cu `YOUTUBE_SHORTS_TEST_URL` și
   `INSTAGRAM_REELS_TEST_URL`, separat de fixture-urile TikTok.

Nu există upscaling, blur, crop, login, cookie-uri importate sau geo bypass.
4K/1440p/1080p/720p sunt selectate doar când dimensiunile sunt raportate; nu se
inventează rezoluție, FPS sau bitrate. Autorul este păstrat în metadate/caption.
Identificarea variantei video fără overlay TikTok se bazează pe proveniența URL-ului
(`playAddr`/`bitrateInfo.PlayAddr` din pagina publică), distinctă de `downloadAddr`.
Aceasta este o **inferență din rolul sursei**, nu verificare vizuală: watermarkurile
adăugate de creator pot exista. Formatele fără proveniență rămân necunoscute; simplul
ID „play” sau numele CDN-ului nu reprezintă dovadă. Dacă nu se identifică o variantă
curată, botul spune explicit și ascunde butonul. Pentru fotografii, câmpul „original”
nu este considerat dovadă de watermark absent.

Bot API standard documentează upload de maximum **50 MB** pentru video/document;
senderul aplică prudent **50.000.000 bytes**. `MAX_FILE_SIZE_MB` este separat,
în MiB (1024² bytes), iar ambele limite sunt respectate. Nu se reduce calitatea
pentru a încăpea în limită. Premium al utilizatorului nu mărește limita botului.
Surse: [fișiere](https://core.telegram.org/bots/api#sending-files),
[video](https://core.telegram.org/bots/api#sendvideo),
[document](https://core.telegram.org/bots/api#senddocument).

## Structură și arhitectură

```text
Dowland-TT/
├── bot/
│   ├── config.py, main.py, cli.py, telegram_config.py
│   ├── handlers/     # comenzi, linkuri, callback-uri, butoane, admitere
│   ├── models/       # VideoInfo, VideoFormat, DownloadResult, erori
│   ├── services/     # extracție, calitate, downloader, media, sender,
│   │                 # coadă/retry, cache SQLite, fișiere și runtime
│   └── utils/        # rețea/SSRF, subprocess-uri, loguri și mesaje
├── tests/
├── downloads/.gitkeep
├── .env.example, .gitignore, .dockerignore
├── pyproject.toml, constraints.txt
├── Dockerfile, docker-compose.yml
├── README.md
└── DEPLOY_ORACLE.md
```

Handlerele orchestrează servicii, fără cod de extracție în Telegram. Protocolul
`Extractor` permite implementări alternative care produc metadate compatibile
adaptorului yt-dlp. Nu sunt folosite API-uri terțe fictive sau servicii plătite.

Runtime-ul are implicit 3 descărcări simultane, o conversie și maximum 35 de
livrări admise, cu o coadă separată pentru analize; are 5 cereri/utilizator/minut,
cooldown de 2 secunde și o operație activă/utilizator. Cererile duplicate împart
fișierul local; conversațiile și tipurile de livrare rămân izolate. Cache-ul
păstrează bot/video/URL/calitate/tip/file_id/dimensiune/data și metadate tehnice,
fără token sau identificatori de utilizator/chat. Un ID video nu satisface
niciodată o cerere de document original. TTL: 7 zile; capacitate: 10.000 intrări.

Sunt verificate DNS și IP-urile efective, inclusiv pentru redirecturi/CDN-uri.
Adresele private, loopback, link-local, metadata și tranzițiile IP nesigure sunt
blocate. HTTPX fixează conexiunea pe un IP public verificat, păstrând TLS/SNI.
Workerul folosește numai transportul Python protejat, fără proxy sau pluginuri.
Sunt acceptate surse directe HTTP/S; HLS/DASH sunt excluse din acest profil.

## Cerințe și instalare macOS ARM64

Necesită CPython 3.12+, Homebrew ARM64 pentru utilitarele multimedia și acces la
internet pentru instalarea inițială. FFmpeg/ffprobe sunt incluse în imaginea Docker.
Suportul YouTube instalează și `yt-dlp-ejs` și Deno prin extras-urile
`yt-dlp[default,deno]`. Versiunile compatibile sunt fixate în `constraints.txt`.
Workerul caută Deno și lângă executabilul Python din mediul virtual; Node 22+
din PATH este o alternativă. Nu descarcă scripturi JavaScript la fiecare cerere.
Vezi [cerințele oficiale YouTube](https://github.com/yt-dlp/yt-dlp/wiki/EJS).
Pe macOS:

```bash
cd ~/Desktop/Projects/Dowland-TT
brew install python@3.12 ffmpeg
/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install -c constraints.txt -e '.[dev]'
.venv/bin/python -m pip check
```

În VS Code: `Python: Select Interpreter` → `Dowland-TT/.venv/bin/python`.
Nu selecta interpreterul copiei istorice. Comenzile folosesc explicit `.venv`,
fără a depinde de comanda globală `python` sau activarea shell-ului.

`constraints.txt` fixează versiunile runtime testate. Nu este un lock cu hashes
pentru toate platformele; build-ul Linux descarcă wheel-uri pentru platforma sa.
Testele de dezvoltare sunt declarate separat în `[project.optional-dependencies]`.

## Configurare

Toate variabilele sunt în `.env.example`. Variabilele de mediu au prioritate față
de `.env` din directorul curent. Căile relative sunt raportate la acel director;
`~` este expandat. Tokenul gol este acceptat pentru verificarea offline.

```bash
test -f .env || cp .env.example .env
chmod 600 .env
```

| Variabilă | Implicit | Semnificație |
| --- | --- | --- |
| `BOT_TOKEN` | gol | necesar numai pentru long polling real |
| `DOWNLOAD_DIR` | downloads | CLI salvează aici; botul folosește subdirectorul bot-work |
| `MAX_FILE_SIZE_MB` | 50 | limită locală MiB per video / total fotografii; senderul limitează separat uploadul |
| `MAX_RETAINED_ORIGINALS_MB` | 512 | plafon MiB al originalelor reținute; evacuare a celor mai vechi eligibile; 0 dezactivează retenția |
| `TELEGRAM_API_BASE` | gol | originea opțională HTTP(S) a unui Local Bot API Server existent |
| `TELEGRAM_LOCAL_UPLOAD_LIMIT_MB` | 2000 | limită upload local, MB zecimali, maximum 2000; ignorată în cloud |
| `MAX_CONCURRENT_DOWNLOADS` | 3 | descărcări active; analizele au separat aceeași limită |
| `MAX_CONCURRENT_CONVERSIONS` | 1 | pregătiri video (remux/recodare) și conversii MP3 simultane, independent de descărcări |
| `MAX_PENDING_REQUESTS` | 32 | așteptări pentru analiză; împreună cu limita descărcărilor, plafonează livrările admise |
| `USER_REQUESTS_PER_MINUTE` | 5 | limită per utilizator |
| `USER_COOLDOWN_SECONDS` | 2 | cooldown |
| `MAX_USER_OPERATIONS` | 1 | operații active per utilizator |
| `OPERATION_TIMEOUT_SECONDS` | 300 | deadline total, inclusiv așteptarea |
| `UPLOAD_TIMEOUT_SECONDS` | 120 | deadline Bot API, inclusiv RetryAfter; și buget total separat pentru pregătirea unui preview |
| `HTTP_MAX_CONNECTIONS` | 6 | limită separată per pool HTTP |
| `CACHE_ENABLED` | true | cache de analiză în memorie și cache Telegram SQLite |
| `ANALYSIS_CACHE_TTL_SECONDS` | 300 | TTL fix al snapshoturilor de analiză, în secunde |
| `ANALYSIS_CACHE_CAPACITY` | 512 | maximum de snapshoturi LRU în memorie |
| `CACHE_DB_PATH` | data/cache.sqlite3 | cale SQLite persistentă |
| `CACHE_TTL_SECONDS` | 604800 | retenție cache |
| `CLEANUP_INTERVAL_SECONDS` | 300 | interval curățare |
| `ABANDONED_FILE_SECONDS` | 3600 | vârstă fișiere inactive |
| `SHUTDOWN_GRACE_SECONDS` | 15 | timp de terminare înainte de anulare |
| `VERIFY_FULL_DECODE` | false | activare decodare integrală FFmpeg |
| `LOG_LEVEL` | INFO | DEBUG/INFO/WARNING/ERROR/CRITICAL |

## Testare fără Telegram

```bash
.venv/bin/python -m pytest -m 'not live' -q
.venv/bin/python -m bot.main --check
.venv/bin/python -m bot.cli --help
```

`--check` verifică configurarea și legarea handlerelor; nu creează client Bot,
nu pornește polling și nu face cereri HTTP. Nu este un healthcheck de rețea sau
un test al FFmpeg/permisiunilor volumelor. Mock-urile acoperă comenzile, URL-urile,
callback-urile, uploadul, erorile, SQLite, concurența, retry și shutdown-ul.

Testele reale sunt separate, necesită internet și FFmpeg/ffprobe, dar nu Telegram:

```bash
.venv/bin/python -m bot.cli 'LINK_PUBLIC_REAL' --timeout 300
# Numai pe server, cu URL-uri publice proprii exportate în mediu:
RUN_PLATFORM_LIVE=1 LIVE_TEST_CONTEXT=server \
.venv/bin/python -m pytest tests/test_live.py::test_public_media_live -m live -q -ra -s
```

Înlocuiește placeholder-ul cu un URL public valid. Un test live omis nu este o
probă de descărcare reușită. CLI-ul folosește aceeași politică fixă: cea mai înaltă calitate fără watermark,
fără opțiunea `--quality`. Păstrează fișierele și afișează JSON cu metadate raportate/măsurate,
fără URL-uri CDN semnate; în Telegram fișierul local se șterge după trimitere.

## Publicare sigură pe GitHub

Publică numai codul și `.env.example`, cu `BOT_TOKEN` gol. Fișierele `.env` și
variantele lor locale, directoarele `.oci`/`.ssh`/`.aws`, cheile private și
fișierele Terraform cu secrete sunt excluse prin `.gitignore`; contextul Docker
le exclude separat, inclusiv dacă ar fi plasate în `bot/`.

Botul nu folosește chei API Oracle pentru rularea pe VM. Cheile administrative
OCI și SSH rămân în afara proiectului și nu se montează în container. Tokenul
Telegram se configurează numai în mediul de execuție sau `.env` privat.

Înainte de fiecare publicare verifică `git status` și fișierele din commit;
nu folosi `git add -f` pentru credențiale. `.gitignore` nu elimină secrete deja
comise. Dacă o cheie reală a ajuns vreodată într-un commit, revoc-o/rotește-o la
furnizor înainte de publicare și curăță separat istoricul; ștergerea fișierului
din ultima versiune nu revocă cheia.

## Activare Telegram, ulterior

Creează botul în BotFather când dorești activarea și completează **local**
`BOT_TOKEN` în `.env`. Nu pune tokenul în cod, Git, imagine sau mesaje publice.
Apoi, din rădăcina proiectului:

```bash
.venv/bin/python -m bot.main
```

Botul oferă `/start`, `/help`, detectare video/foto și butoane dinamice pentru variantele disponibile. Nu cere
`/start` repetat. HTML este escapat pentru datele externe; progresul este editat,
fără procente inventate. Butoanele sunt legate de utilizator/chat/mesaj și expiră
în 10 minute sau după restart. Fără token, pornirea normală se închide cu eroare
clară; nu încearcă o conexiune reală. Nu este folosit webhook, domeniu sau HTTPS inbound.

## Meniu permanent și preferințe

După `/start` sau `/menu`, tastatura de jos oferă **📥 Descarcă video**,
**⚙️ Preferințe**, **❓ Cum folosesc botul** și **ℹ️ Limite și calitate**.
Poți trimite și direct un link. `/settings` deschide preferințele.

La un link video, botul trimite automat videoclipul redabil în chat, cu titlul
drept caption. Sub el, **📁 Ca fișier** și **ℹ️ Info** apar pe același rând.
Fișierul original se trimite doar la apăsare; audio și ZIP rămân acțiuni secundare.
**ℹ️ Info** extinde caption-ul fără să retrimită video-ul și păstrează celelalte
butoane; **🙈 Ascunde info** revine la titlu. Butoanele de fișier/audio/ZIP sunt
legate de utilizator și de mesaj și expiră după 10 minute sau după restart.
Un original incompatibil cu playerul primește o variantă MP4/H.264/AAC pentru
redare, verificată separat; „Ca fișier” livrează în continuare originalul.
Originalul descărcat este păstrat local **3 minute (180 secunde)** după trimiterea
reușită a videoclipului. „Ca fișier” reutilizează aceiași octeți în acest interval,
fără o nouă descărcare. Apăsarea butonului nu prelungește termenul. O nouă trimitere
reușită a videoclipului poate reînnoi păstrarea unui original încă disponibil.
La expirare, originalul este șters automat; un upload deja început își păstrează
fișierul până se încheie. Oprirea botului curăță și originalele păstrate.
După expirare, trimiterea poate folosi cache-ul Telegram de documente sau poate
descărca originalul din nou. Dacă videoclipul vine din cache fără un original
local disponibil, nu se face o descărcare preventivă doar pentru a-l păstra.
Postările foto păstrează alegerea livrării; înaintea descărcării afișează doar
titlul și butoanele, cu detaliile în Info.
Analiza, descărcarea și trimiterea editează același mesaj de progres. Un mesaj
nou de stare este creat doar dacă Telegram confirmă că originalul nu mai poate
fi editat. Videoclipul, fișierul sau audio-ul livrat are propriul buton Info.

Politica sursei este fixă: calitate maximă și numai variante identificate fără
watermark. Meniul ⚙️ Preferințe explică politica și nu mai oferă comutatoare.
Setările vechi sunt normalizate la `high` / `without` la citire, iar butoanele
vechi nu pot reactiva calitatea redusă sau variante cu watermark. Documentele,
ZIP și preview-urile rămân separate. Lipsa watermark-ului este evaluată din
metadatele sursei, fără verificare vizuală sau ștergerea artificială a marcajelor.

Video-urile sunt trimise automat folosind politica fixă de calitate și watermark;
fotografiile cer alegerea livrării. Dacă alegerea salvată depășește limitele
cunoscute, botul afișează opțiunile încă disponibile sau explică limita.
**❌ Anulează** închide alegerea în așteptare; `/cancel` închide toate alegerile
inactive ale utilizatorului din chatul curent. Nu întrerupe trimiterile deja începute.

Preferințele funcționează și cu `CACHE_ENABLED=false`. Tabela separată
`user_preferences` din `CACHE_DB_PATH` păstrează ID-ul Telegram al utilizatorului
și setările, fără linkuri, istoricul descărcărilor sau ID-uri de chat. Resetarea din
API restabilește aceeași politică fixă. Butoanele altui utilizator nu îi pot modifica
preferințele și nu pot anula cererile sale.

## Info video / foto, fără retrimiterea fișierului

Mesajele media noi au inițial un titlu scurt și butonul **ℹ️ Info video** sau
**ℹ️ Info foto**. La apăsare, botul editează caption-ul aceluiași mesaj și afișează
caracteristicile disponibile: rezoluție, dimensiune exactă, format, durată, FPS,
bitrate, codec video/audio, prezența audio, autor și informația despre watermark.
Valorile lipsă rămân necunoscute; rezoluțiile măsurate sunt diferențiate de cele
raportate de sursă. Butonul devine **🙈 Ascunde info**, care restabilește titlul.
Nu descarcă sau retrimite media la apăsare.

Pentru fotografii, dimensiunile și mărimea afișate descriu **originalul verificat**,
nu preview-ul posibil comprimat de Telegram. Funcționează și pentru fișiere originale,
ZIP și videoclipuri trimise din cache. Pentru albume, butoanele se atașează fiecărui
mesaj după `sendMediaGroup`, care nu acceptă `reply_markup`; se folosesc apoi
[`editMessageReplyMarkup`](https://core.telegram.org/bots/api#editmessagereplymarkup)
și [`editMessageCaption`](https://core.telegram.org/bots/api#editmessagecaption).

Tabela `media_details` din SQLite păstrează identificatorii bot/chat/mesaj și cele
două variante de caption, fără căi locale sau URL-uri CDN. Informațiile supraviețuiesc
repornirii și ștergerii fișierelor locale. Retenția urmează `CACHE_TTL_SECONDS`
(implicit 7 zile), cu maximum 10.000 mesaje; funcționează și când cache-ul media
este dezactivat. După expirare, butonul explică lipsa detaliilor. Mesajele trimise
înainte de această modificare nu primesc retroactiv butoane.

Testele verifică editările cu un transport Telegram simulat; afișarea efectivă a
butoanelor pe albume în clienții Telegram necesită un test live separat.

## Docker ARM64

Necesită Docker Engine/Desktop cu BuildKit și Compose 2.24+; pe Ubuntu 24.04 ARM64
se instalează Engine și pluginul Compose conform [ghidului Docker](https://docs.docker.com/engine/install/ubuntu/).
Imaginea folosește Python 3.12 Debian slim, compatibilă cu hostul Ubuntu; nu necesită
ca distribuția din container să fie Ubuntu. [Imaginea oficială Python](https://hub.docker.com/_/python)
include ARM64. Dockerfile folosește build separat de wheel-uri, fără toolchain în
runtime și fără a copia wheel-urile în straturile finale.

```bash
docker compose config --quiet
docker compose build --pull
docker compose run --rm --no-deps bot python -m bot.main --check
# Numai după configurarea locală a tokenului și verificare:
docker compose up -d --build
```

Containerul rulează ca UID/GID 10001, cu filesystem read-only, capabilities eliminate,
`no-new-privileges`, init și `restart: unless-stopped`. Nu publică porturi și nu
montează socket-ul Docker. Limitele implicite sunt 1 GiB RAM, 1,5 CPU și 128 PID-uri.

Volumele numite `cache-data` și `download-work` păstrează SQLite și workspace-ul
separat. `/tmp` este tmpfs limitat la 64 MiB; videoclipurile sunt pe volum de disc,
nu în tmpfs. Compose suprascrie căile din `.env` la `/app/data/cache.sqlite3` și
`/app/downloads` pentru persistență corectă. La prima creare, volumele preiau
permisiunile directoarelor imaginii. Tokenul este injectat la runtime; contextul
Docker exclude implicit tot în afara codului și fișierelor necesare build-ului.

Monitorizarea aleasă este **monitorizarea procesului**: Docker afișează starea și
repornește procesul la ieșire. Nu există un healthcheck care să pretindă că tokenul,
TikTok sau Telegram funcționează doar pentru că importurile trec. Un proces blocat
logic poate necesita intervenție; verifică starea și logurile:

```bash
docker compose ps --all
docker compose logs --tail=100 -f bot
docker stats --no-stream
```

Logurile au rotație locală 10 MiB × 3. Tokenul și antetele Authorization sunt
redactate, inclusiv în traceback-uri. Logurile conțin tipuri de erori, durate,
retry-uri, cache hit/miss și cleanup; nu conțin URL-uri semnate sau `file_id`.

## Oprire, restart și actualizare

Local, `Ctrl+C` declanșează oprirea controlată. În Docker:

```bash
docker compose stop
docker compose start
# Sau oprire/eliminare container, cu păstrarea volumelor:
docker compose down
docker compose up -d
```

SIGTERM/SIGINT opresc admiterea; operațiile primesc 15 secunde, apoi sunt anulate
și cleanup-ul este așteptat. Sunt închise subprocess-urile, HTTP și SQLite. Grace
period Docker este 90 s; păstrează-l mai mare decât grace-ul aplicației plus cleanup.
Nu utiliza `docker compose down -v` dacă dorești să păstrezi cache-ul.

Înainte de actualizare fă backup conform [DEPLOY_ORACLE.md](DEPLOY_ORACLE.md),
adu codul revizuit, apoi:

```bash
.venv/bin/python -m pip install -c constraints.txt -e '.[dev]'
.venv/bin/python -m pytest -m 'not live' -q
docker compose build --pull
docker compose up -d --no-build
```

Revizuiește separat actualizările din `constraints.txt`, în special yt-dlp și
adaptoarele interne HTTPX/httpcore. Pentru yt-dlp, compatibilitatea cu TikTok poate
necesita o versiune mai nouă și retestare; nu elimina global constrângerile fără verificare.

## Limite externe și Local Bot API

Botul este proiectat pentru un singur proces, cu lock exclusiv pe workspace.
Coada și butoanele nu sunt persistente; după restart utilizatorul retrimite linkul,
iar SQLite poate evita descărcarea. Fișierele abandonate sunt curățate la pornire
și periodic, cu protecție pentru operațiile active. CLI-ul nu este curățat automat.

Scanarea implicită ffprobe verifică structura/pachetele, nu decodabilitatea fiecărui
cadru. `VERIFY_FULL_DECODE=true` activează verificarea completă FFmpeg, cu cost CPU.
FFmpeg este folosit la unirea fluxurilor prin stream copy sau când această opțiune
este activă. Nu se re-encodează video-ul. Sursele private, restricționate, adaptive
sau neacceptate de extractor nu sunt ocolite. Nu există garanție de watermark absent,
4K sau disponibilitate permanentă TikTok/Telegram.

[Local Bot API Server](https://core.telegram.org/bots/api#using-a-local-bot-api-server)
este **configurabil**, fără a fi obligatoriu sau instalat de acest proiect:

```dotenv
TELEGRAM_API_BASE=http://telegram-bot-api:8081
TELEGRAM_LOCAL_UPLOAD_LIMIT_MB=2000
MAX_FILE_SIZE_MB=1900
UPLOAD_TIMEOUT_SECONDS=600
OPERATION_TIMEOUT_SECONDS=900
```

Endpointul trebuie să fie un server Bot API pornit și administrat separat în modul
`--local`. Pentru migrare urmează instrucțiunile Telegram privind `logOut` pe vechiul
server; botul nu migrează automat sesiunea. Sunt folosite uploaduri multipart în
flux de pe disc, deci nu este necesar un filesystem partajat. În Docker, `localhost`
este containerul botului; folosește un hostname accesibil din acel container.
Configurează numai un server de încredere, deoarece primește tokenul botului.
Golirea `TELEGRAM_API_BASE` păstrează API cloud și limita 50 MB. Nu se comprimă
originalele pentru a încăpea. Limita foto și regulile albumelor rămân aplicate prudent.
Surse: [fișiere și upload](https://core.telegram.org/bots/api#sending-files),
[albume](https://core.telegram.org/bots/api#sendmediagroup).

Adaptorul TikTok folosește metode interne din versiunea yt-dlp fixată în
`constraints.txt`; la actualizare trebuie retestate extragerea paginii și schema
`imagePost`. Sunt suportate `imageURL`, variantele original/display/watermark
cunoscute; schimbările de schemă, blocările IP, CAPTCHA, login sau restricțiile
regionale pot împiedica extracția. Nu se folosesc servicii terțe de watermark removal.
Cea mai bună variantă disponibilă din pagina publică nu garantează fișierul master
al camerei. Muzica slideshow-ului nu este inclusă în albumul foto sau ZIP.
Un eșec în mijlocul trimiterii poate lăsa grupurile deja livrate în chat; retry-ul
poate repeta acele grupuri. Nu se reîncearcă automat trimiteri cu rezultat incert.

Testele Telegram reale sunt opționale și trimit efectiv mesaje în chatul ales:

```bash
RUN_TELEGRAM_LIVE=1 TELEGRAM_TEST_CHAT_ID='ID_CHAT_TEST' \
TIKTOK_TEST_URL='LINK_VIDEO_PUBLIC_REAL' TIKTOK_TEST_PHOTO_URL='LINK_FOTO_PUBLIC_REAL' \
.venv/bin/python -m pytest tests/test_live.py::test_real_telegram_delivery -s
```

Necesită `BOT_TOKEN` configurat privat și acces al botului la chat. Fără opt-in,
acest test este omis. Testele locale includ imagini generate și verificare reală
ffprobe/FFmpeg; mock-urile de rețea nu dovedesc acces real la TikTok sau Telegram.

Instrucțiunile OCI sunt în [DEPLOY_ORACLE.md](DEPLOY_ORACLE.md). Documentația nu
înseamnă că imaginea a fost construită, că s-a făcut deployment sau că există un
test real de uptime. Raportul execuției indică separat verificările realizate.

### Măsurarea performanței

La `LOG_LEVEL=INFO`, `bot.performance` emite evenimente JSON pentru analiză,
descărcare, verificare, conversie, upload și așteptarea în coadă. Contoarele
hit/miss și `hit_rate` sunt separate pentru metadate și Telegram.
Vezi [interpretarea logurilor de performanță](docs/performance.md) pentru câmpuri,
cache-uri, concurență și exemple de agregare.


Pregătirea video și limitarea conversiilor sunt descrise în
[documentația pipeline-ului Telegram](docs/telegram-video.md), inclusiv fallback,
metadate, timeouturi, curățare și separarea cozilor.


[Cache-ul temporar de analiză](docs/analysis-cache.md) reutilizează timp de
5 minute metadatele și datele extractorului necesare descărcării. TTL-ul și
capacitatea sunt configurabile; recuperarea datelor media expirate face o
singură extracție proaspătă și o singură reîncercare de transfer.


[Retenția cu plafon de spațiu și verificările live pe server](docs/storage-and-live.md)
descriu protejarea fișierelor active, recuperarea după restart, variabilele pentru
TikTok/YouTube/Instagram și comenzile exacte de testare. Testele locale și
cazurile live omise nu confirmă accesibilitatea platformelor din rețeaua serverului.
