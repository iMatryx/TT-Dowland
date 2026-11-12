# TikTok Ultra Downloader

Fundația unui bot Telegram pentru descărcarea videoclipurilor publice TikTok.

## Etapa 1

Această etapă include:

- configurare prin variabile de mediu cu `pydantic-settings`;
- logging de bază;
- modele de domeniu pentru informații, formate și rezultate de download;
- structura modulară pentru handlers, servicii, utilitare și modele;
- teste minimale pentru configurare și modele.

Motorul local de descărcare este adăugat în etapa 2, iar interfața Telegram în etapa 3.
Conectarea reală este opțională și necesită activare explicită.

## Dezvoltare locală

Necesită Python 3.12+ și FFmpeg/ffprobe instalate separat pentru etapele multimedia
ulterioare.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
pytest
```

Variabilele suportate sunt `BOT_TOKEN`, `DOWNLOAD_DIR`, `MAX_FILE_SIZE_MB`,
`MAX_CONCURRENT_DOWNLOADS`, `LOG_LEVEL` și `CACHE_ENABLED`.

## Etapa 2: motor și CLI local

Proiectul activ este în acest director `Dowland-TT/`. Copia din subdirectorul
`tiktok-ultra-downloader/` este independentă și nu este folosită de comenzile de aici.

Instalează FFmpeg și ffprobe înainte de o descărcare reală:

```bash
# macOS ARM64, Homebrew nativ
brew install python@3.12 ffmpeg
# Ubuntu ARM64 24.04+ cu Python 3.12+
sudo apt update
sudo apt install python3 python3-venv ffmpeg
```

Nu au fost adăugate dependențe Python noi. Din rădăcina acestui proiect:

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m bot.cli 'https://www.tiktok.com/@user/video/123456789'
# Înlocuiește exemplul cu un link public real; exemplul nu este o probă de funcționare.
python -m bot.cli 'https://vm.tiktok.com/TOKEN/' --timeout 300
# Alternativ, după instalarea proiectului:
tiktok-ultra-download 'https://www.tiktok.com/@user/video/123456789'
```

CLI-ul scrie în stdout un JSON cu metadatele extrase (`video_info` și toate
`formats`), varianta selectată (`format`), informațiile măsurate local
(`verified_format`), calea fișierului și avertismentele. Autorul rămâne în
`video_info.uploader`. Valorile necunoscute sunt `null`; bitrate-ul `tbr` este
în kbps, dimensiunile în pixeli, `filesize` în bytes. `filesize_estimated`
distinge estimările yt-dlp de dimensiunile declarate exacte. Fișierele verificate
rămân în `DOWNLOAD_DIR`, sub nume unice, până când utilizatorul le șterge.

### Fluxul de descărcare

1. `services/urls.py` acceptă doar hosturile exacte `tiktok.com`, `www.tiktok.com`,
   `vm.tiktok.com`, `vt.tiktok.com`. Acceptă linkuri cu/fără schemă, elimină query-ul
   și fragmentul, normalizează la HTTPS, respinge credențiale, porturi nepermise,
   profiluri și live-uri. Pentru short links, verifică fiecare redirect înainte
   de următoarea cerere, fără a descărca body-ul și fără redirecturi externe.
2. `services/extraction.py` definește protocolul `Extractor` și implementarea
   `YtdlpExtractor`. Metadatele brute circulă doar în workspace-ul privat,
   ca fișier yt-dlp JSON; un extractor alternativ poate implementa același
   contract cu formate compatibile yt-dlp. Nu există API-uri terțe fictive.
3. `services/quality.py` preferă, în ordine: fără watermark **explicit raportat**,
   status necunoscut, cu watermark raportat. În fiecare grup ordonează după
   latura scurtă, aria imaginii, FPS și bitrate. Astfel, sursele verticale și
   orizontale sunt comparate consecvent: 2160p, 1440p, 1080p, 720p sau cea mai
   bună rezoluție reală disponibilă. O sursă curată mai mică poate câștiga în fața
   uneia mai mari cu watermark. Dimensiunile necunoscute nu sunt inventate.
4. `services/ytdlp_worker.py` execută yt-dlp într-un subprocess separat, limitat
   în timp, fără cookie-uri, login sau geo bypass. Folosește extractorul TikTok
   explicit. Fișierul este scris pe disc, nu încărcat integral în RAM. Dacă sunt
   fluxuri video/audio separate, yt-dlp le unește în MKV prin FFmpeg cu stream
   copy. Pentru un fișier deja combinat nu există remuxare sau transcodare.
5. `services/media.py` verifică containerul și dimensiunile prin ffprobe, apoi
   decodabilitatea întregului fișier prin FFmpeg în ieșire `null`. Această
   verificare nu rescrie fluxurile. Nu se aplică upscaling, blur, crop sau
   eliminarea atribuirii. Rezultatul probat are prioritate față de dimensiunile
   declarate de extractor; diferențele sunt raportate.
6. `services/downloader.py` limitează concurența per instanță, verifică spațiul
   disponibil și limita de dimensiune, izolează fiecare cerere într-un director
   temporar și publică atomic doar fișierul verificat. Erorile, timeout-ul și
   anularea omoară/recoltează subprocess-urile și curăță fișierele intermediare.

`MAX_FILE_SIZE_MB` folosește MiB (1024² bytes). Motorul nu reduce automat calitatea
pentru a încăpea în limită; raportează `FileSizeLimitError`. Limita este verificată
din metadate exacte, în timpul transferului și pe fișierul final. Verificarea
integrală poate fi costisitoare; deadline-ul implicit de 240 s include toate
operațiile și așteptarea unui slot. Pentru videoclipuri mai lungi, crește `--timeout`.

### Watermark și restricții

yt-dlp nu garantează absența watermark-ului. Numai un flag explicit sau o notă
explicită a extractorului este păstrată ca dovadă în `watermark_evidence`.
Numele unui format precum `play` nu dovedește că nu are watermark. Nu există
detector vizual de watermark: chiar pentru `has_watermark=false`, avertismentul
precizează că acesta este un raport al sursei, nu o verificare vizuală.
`verified_format` confirmă proprietăți tehnice, nu absența watermark-ului.

Excepțiile din `models/exceptions.py` disting URL invalid, video privat/șters/
indisponibil, restricție regională, rețea, timeout, fișier deteriorat, spațiu
insuficient, limită de dimensiune și lipsa utilitarelor multimedia. Clasificarea
mesajelor yt-dlp folosește indicii explicite; erorile ambigue rămân
`VideoUnavailableError`. Un 403 poate însemna refuz de acces și nu declanșează
încercări de ocolire. Compatibilitatea depinde de TikTok, regiune și versiunea
yt-dlp; nu se încearcă autentificare sau accesarea conținutului privat.

Surse pentru adaptorul real:
[API yt-dlp](https://github.com/yt-dlp/yt-dlp#embedding-yt-dlp),
[extractorul TikTok](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py).

### Teste

```bash
python -m pip check
python -m pytest -q
python -m bot.cli --help
# Opțional, efectuează un download real, fără cookie-uri sau token Telegram:
TIKTOK_TEST_URL='https://www.tiktok.com/@autor/video/ID_REAL' \
  python -m pytest -m live -s
```

Testele unitare folosesc fixture-uri și mock-uri la limitele externe. Testele
de subprocess pornesc doar Python local. Testul `live` este omis fără
`TIKTOK_TEST_URL`; un skip nu reprezintă un download reușit. Nu sunt implementate
cache, deployment sau Docker. Interfața Telegram este descrisă în etapa 3 de mai jos.

## Etapa 3: interfață Telegram, fără token real pentru verificare

Codul activ rămâne în rădăcina `Dowland-TT/`, nu în copia din
`tiktok-ultra-downloader/`. Motorul etapei 2 este reutilizat fără rescriere.

### Verificare complet offline

```bash
# Din Dowland-TT/, cu Python 3.12+ disponibil:
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pip check
python -m pytest -q
python -m pytest tests/test_telegram.py tests/test_telegram_adapter.py tests/test_telegram_config.py -q
python -m bot.main --check
```

`--check` construiește dispatcherul și serviciile fără a crea un client Bot,
fără token, polling, extracție TikTok sau cereri HTTP. Testele Telegram folosesc
mock-uri, inclusiv la nivelul sesiunii aiogram, și nu trimit mesaje reale.
Testul live al motorului rămâne opțional și este omis fără `TIKTOK_TEST_URL`.

### Fluxul utilizatorului

- `/start` afișează întâmpinarea; `/help` explică utilizarea, calitatea și limita
  efectivă. Nu este necesar `/start` înainte de fiecare link sau înainte de primul link.
- Un link public declanșează analiza și verificarea disponibilității prin
  extractorul existent. Nu se descarcă fișierul video înainte de alegerea unui buton.
- Botul editează mesajul „⏳ Analizez videoclipul...” cu metadatele declarate de
  sursă și butoanele „📹 Descarcă video” / „📁 Descarcă original”.
- La apăsare, motorul revalidează și descarcă sursa. Stările sunt
  „📥 Descarc videoclipul...”, „📤 Pregătesc trimiterea...” și
  „✅ Videoclipul este gata!”. Nu sunt simulate procente.
- Caption-ul fișierului conține rezoluția și FPS confirmate local, dimensiunea
  reală, autorul și statusul watermark-ului. `Nu` înseamnă declarat explicit de
  sursă, cu precizarea că nu s-a făcut verificare vizuală; lipsa dovezilor rămâne
  `Necunoscut`. Datele externe sunt escapate pentru HTML și limitate ca lungime.
- Video este trimis pentru MP4/H.264 cu audio AAC sau fără audio. Pentru alte
  formate, alegerea video livrează originalul ca document și explică motivul.
  Alegerea original trimite întotdeauna document, fără conversie suplimentară.

### Limite și resurse

Bot API standard documentează **50 MB** pentru upload de video și document.
`CloudUploadPolicy` aplică o limită conservatoare de **50.000.000 bytes**;
`MAX_FILE_SIZE_MB` rămâne limita locală în MiB a motorului. Ambele sunt respectate.
Premium al utilizatorului nu modifică politica botului. Dimensiunea fișierului
este verificată de pe disc înainte de upload; un refuz de dimensiune al Telegram
este transformat într-un mesaj clar. Nu se comprimă automat fișierul.

Surse: [trimiterea fișierelor](https://core.telegram.org/bots/api#sending-files),
[sendVideo](https://core.telegram.org/bots/api#sendvideo),
[sendDocument](https://core.telegram.org/bots/api#senddocument).

`TelegramUploadPolicy` este o abstracție injectabilă pentru o integrare viitoare
cu Local Bot API Server. În această etapă nu există mod local, URL de server
personalizat sau activare a unei limite mărite. O astfel de integrare trebuie
să furnizeze atât politica, cât și clientul/session-ul corespunzător.

Acțiunile butoanelor au ID-uri opace și sunt legate de utilizator, chat și mesaj.
Ele expiră după 10 minute sau repornire, sunt limitate numeric și nu reprezintă
un cache de fișiere. Apăsările paralele pe aceeași acțiune sunt respinse.
Fișierele descărcate de fluxul Telegram sunt șterse după încercarea de trimitere,
inclusiv la eroare sau anulare. CLI-ul etapei 2 își păstrează comportamentul de
salvare a fișierelor. Erorile utilizatorului sunt în română, fără traceback-uri
sau detalii brute din infrastructură. Nu sunt făcute retry-uri automate ale
uploadurilor cu rezultat incert, pentru a evita trimiteri duplicate.

### Separarea componentelor

| Componentă | Rol |
| --- | --- |
| `handlers/commands.py` | `/start`, `/help` |
| `handlers/links.py` | detectare URL și analiză |
| `handlers/keyboards.py`, `handlers/callbacks.py` | butoane, autorizare acțiuni și stări |
| `services/telegram_download.py` | adaptor peste motor și registrul temporar al acțiunilor |
| `services/telegram_sender.py` | compatibilitate video, upload streaming, politica de limită |
| `utils/telegram_text.py`, `utils/telegram_progress.py` | mesaje HTML și editarea progresului |
| `telegram_config.py` | compunere dispatcher și configurare client aiogram |
| `main.py` | verificare offline sau long polling explicit |

### Conectare reală, ulterior

Nu trebuie furnizat acum un token real. Când dorești activarea, copiază
`.env.example` în `.env` în rădăcina proiectului și completează `BOT_TOKEN` local
cu tokenul BotFather. Instalează FFmpeg/ffprobe și verifică accesul la Telegram
și la videoclipurile TikTok publice. Apoi rulează:

```bash
python -m bot.main
# Echivalent după instalare:
tiktok-ultra-downloader
```

Fără token, pornirea normală se închide cu un mesaj de configurare; `--check`
funcționează fără token. Numai pornirea normală inițiază long polling. Sesiunea
clientului este închisă și la erori. Nu este creat webhook, nu este cerut domeniu
sau HTTPS și nu este implementat deployment Oracle, Mini App sau interfață web.

Referințe aiogram: [long polling](https://docs.aiogram.dev/en/latest/dispatcher/long_polling.html),
[DefaultBotProperties](https://docs.aiogram.dev/en/latest/api/defaults.html).
# TT-Dowland
