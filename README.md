# TikTok Ultra Downloader

Bot Telegram asincron pentru videoclipuri **publice** TikTok, cu CLI independent,
calitate maximă disponibilă și preferință pentru variante explicit declarate fără
watermark. Proiectul activ este rădăcina `Dowland-TT/`. Copia istorică din
`tiktok-ultra-downloader/` este păstrată local, ignorată de Git și exclusă din imagine.

Nu sunt necesare token Telegram, cont Oracle sau Docker pentru testele automate.
Deployment-ul și conectarea reală se fac ulterior, explicit.

## Funcționare și limite

1. Utilizatorul trimite un URL `tiktok.com`, `www.tiktok.com`, `vm.tiktok.com` sau
   `vt.tiktok.com`; botul validează și normalizează URL-ul și redirecturile.
2. yt-dlp extrage disponibilitatea, autorul și formatele raportate de sursă.
3. Se preferă, în ordine: fără watermark declarat, watermark necunoscut, watermark
   declarat prezent. În fiecare grup se compară latura scurtă, aria, FPS și bitrate.
   Un format curat mai mic poate câștiga în fața unuia mai mare cu watermark.
4. Butoanele aleg trimitere video sau document original. Descărcarea începe numai
   după alegere. MP4/H.264 cu AAC sau fără audio poate fi trimis ca video; celelalte
   formate sunt trimise ca document, cu explicație, fără conversie.
5. Fișierul este verificat prin ffprobe, apoi trimis streaming. Cache-ul SQLite
   poate înlocui descărcarea cu reutilizarea unui `file_id` compatibil al aceluiași bot.

Nu există upscaling, blur, crop, login, cookie-uri importate sau geo bypass.
4K/1440p/1080p/720p sunt selectate doar când dimensiunile sunt raportate; nu se
inventează rezoluție, FPS sau bitrate. Autorul este păstrat în metadate/caption.
`Watermark: Nu` este însoțit de precizarea că sursa îl declară astfel, fără detector
vizual; în lipsa dovezilor rămâne `Necunoscut`.

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

Runtime-ul are implicit 3 operații active, 32 în așteptare, 5 cereri/utilizator/minut,
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
| `MAX_FILE_SIZE_MB` | 50 | limită locală MiB; senderul aplică și limita cloud |
| `MAX_CONCURRENT_DOWNLOADS` | 3 | operații active, inclusiv analiză/upload |
| `MAX_PENDING_REQUESTS` | 32 | capacitatea cozii |
| `USER_REQUESTS_PER_MINUTE` | 5 | limită per utilizator |
| `USER_COOLDOWN_SECONDS` | 2 | cooldown |
| `MAX_USER_OPERATIONS` | 1 | operații active per utilizator |
| `OPERATION_TIMEOUT_SECONDS` | 300 | deadline total, inclusiv așteptarea |
| `UPLOAD_TIMEOUT_SECONDS` | 120 | deadline Bot API, inclusiv RetryAfter |
| `HTTP_MAX_CONNECTIONS` | 6 | limită separată per pool HTTP |
| `CACHE_ENABLED` | true | cache SQLite |
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
TIKTOK_TEST_URL='LINK_PUBLIC_REAL' .venv/bin/python -m pytest -m live -s
```

Înlocuiește placeholder-ul cu un URL public valid. Un test live omis nu este o
probă de descărcare reușită. CLI-ul păstrează fișierul și afișează JSON cu
metadate raportate/măsurate; în Telegram fișierul local se șterge după trimitere.

## Activare Telegram, ulterior

Creează botul în BotFather când dorești activarea și completează **local**
`BOT_TOKEN` în `.env`. Nu pune tokenul în cod, Git, imagine sau mesaje publice.
Apoi, din rădăcina proiectului:

```bash
.venv/bin/python -m bot.main
```

Botul oferă `/start`, `/help`, analiză link și butoane video/original. Nu cere
`/start` repetat. HTML este escapat pentru datele externe; progresul este editat,
fără procente inventate. Butoanele sunt legate de utilizator/chat/mesaj și expiră
în 10 minute sau după restart. Fără token, pornirea normală se închide cu eroare
clară; nu încearcă o conexiune reală. Nu este folosit webhook, domeniu sau HTTPS inbound.

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
este o integrare viitoare separată, **neimplementată și neinstalată**. Ar necesita
client/session dedicat, infrastructură și o implementare `TelegramUploadPolicy`
corespunzătoare; schimbarea unui număr din `.env` nu activează un server local și
nu permite uploaduri mai mari prin API cloud.

Instrucțiunile OCI sunt în [DEPLOY_ORACLE.md](DEPLOY_ORACLE.md). Documentația nu
înseamnă că imaginea a fost construită, că s-a făcut deployment sau că există un
test real de uptime. Raportul execuției indică separat verificările realizate.
