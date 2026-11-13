# Pregătirea video pentru Telegram

Originalul descărcat rămâne separat de varianta pregătită pentru redare în chat.
Butonul „Ca fișier” continuă să livreze originalul. Un cache Telegram al unui
preview păstrează metadatele variantei efectiv trimise, nu metadatele originalului.
Un `file_id` video original compatibil poate satisface și o cerere de preview
pentru aceeași selecție. Reciproc, un preview remuxat/recodat nu satisface cereri
de originale; documentele, ZIP și audio folosesc în continuare tipuri separate.
TTL-urile cache-urilor și retenția originalelor nu se prelungesc prin acest lookup.
Modurile explicite de document/ZIP păstrează fișierele originale.

## Alegerea variantei

La descărcare, `verify_media` verifică fluxurile reale cu ffprobe, inclusiv codec,
format de pixeli, container, pachete, dimensiuni și durată. Datele extractorului
nu decid singure compatibilitatea. Un MP4 verificat cu H.264 și AAC (sau fără
sunet), în format de pixeli `yuv420p`/`yuvj420p`, poate fi trimis direct.

Politica este fixă pentru toate cererile: calitate maximă fără watermark. Se alege cea
mai bună variantă identificată fără watermark, comparând rezoluția (latura scurtă,
apoi suprafața), FPS și bitrate raportate. Variantele cu statut necunoscut sau
cu watermark nu sunt fallback-uri. Dacă sursa nu oferă o variantă eligibilă,
botul explică indisponibilitatea; audio rămâne separat. Nu se elimină artificial
marcaje și nu se garantează vizual metadatele sursei.

Meniul Preferințe, comanda `/settings`, modelul și API-urile preferințelor au fost
eliminate. Vechea tabelă `user_preferences` este eliminată la deschiderea SQLite,
fără a șterge cache-ul media sau detaliile Info. Pipeline-ul și CLI-ul nu acceptă
argumente pentru alegerea calității sau watermark-ului.
Audio selectează sursa cea mai bună și este livrat ca MP3 de 192 kbps.
Nu se inventează rezoluții și nu se face upscaling.

`create_preview` returnează `PreparedVideo(path, format)`. Refolosește verificarea
sursei numai când dovada locală coincide cu dispozitivul, inode-ul, dimensiunea,
mtime/ctime în nanosecunde și metadatele verificate. Dovada se creează după probe
reușite, se reasociază după publicarea atomică proprie și nu se serializează în
cache. Datele extractorului sau un obiect deserializat nu sunt o asemenea dovadă.
O modificare, înlocuire sau lipsa dovezii declanșează o probă nouă. La trimitere,
o dovadă locală devenită invalidă oprește livrarea.

Pipeline-ul de pregătire:

1. Reutilizează MP4-ul compatibil care se încadrează în limita de upload. Nu
   creează o copie și nu pornește FFmpeg.
2. Dacă fluxurile sunt compatibile, încearcă schimbarea containerului în MP4
   prin `ffmpeg -c copy -movflags +faststart`. Sunt selectate primul flux video
   real (fără cover art) și prima pistă audio, dacă există. Nu recodează fluxurile.
3. Verifică rezultatul cu ffprobe: compatibilitate, integritate, păstrarea
   sunetului și a duratei. Verificarea detectează și ieșiri trunchiate de limita
   `-fs`. Un fișier produs cu exit code zero nu este automat acceptat.
4. Recodează în H.264/AAC, `yuv420p`, cu dimensiuni pare și `+faststart`, când
   fluxurile sunt incompatibile sau încercarea de remuxare eșuează, depășește
   limita ori produce un rezultat invalid. Verifică din nou înainte de upload.

Anularea nu declanșează recodare de fallback. O eroare sau anulare elimină
fișierul parțial, iar procesele FFmpeg sunt oprite și reapate prin mecanismul
existent de grupuri de procese. Outputul trebuie să fie o cale diferită de sursă.

## Metadate și original

`VideoFormat` include `pixel_format`, `container`, `remuxed` și `transcoded`:

| Rezultat | `remuxed` | `transcoded` |
| --- | --- | --- |
| Original reutilizat | false | false |
| MP4 obținut prin copierea fluxurilor | true | false |
| MP4 recodat sau MP3 extras | false | true |

Metadatele verificate sunt asociate rezultatului trimis și se serializează în
cache-ul SQLite existent. Intrările vechi au implicit `remuxed=false`; schema
SQLite nu necesită migrare. Caption-ul extins distinge remuxarea fără recodare de
conversia H.264/AAC. Parametrii și fișierul originalului nu sunt suprascriși de
pregătirea preview-ului; fișierul temporar al acestuia este șters după upload.
Retenția de 180 s și lease-urile pentru originale rămân active; plafonul
`MAX_RETAINED_ORIGINALS_MB` poate evacua mai devreme un original inactiv. Vezi
[retenția și verificările live](storage-and-live.md).

## Configurație și cozi

```dotenv
MAX_CONCURRENT_DOWNLOADS=3
MAX_CONCURRENT_CONVERSIONS=1
MAX_PENDING_REQUESTS=32
OPERATION_TIMEOUT_SECONDS=300
UPLOAD_TIMEOUT_SECONDS=120
```

- `MAX_CONCURRENT_CONVERSIONS`: implicit **1**, valori între **1 și 32**. Este un
  buget comun al runtime-ului pentru remuxarea/recodarea preview-urilor și MP3.
  Engine-ul de descărcare și senderul primesc același `ConversionLimiter`.
  Un apel independent la utilitare poate primi explicit același limiter.
- `MAX_CONCURRENT_DOWNLOADS` limitează transferurile din downloader. Analizele
  au separat o coadă și un semafor cu aceeași concurență. Verificarea sursei și
  combinarea fără recodare a fluxurilor de către yt-dlp fac parte din obținerea
  originalului și rămân în limita de descărcare.
- Coada de livrări admite maximum `MAX_CONCURRENT_DOWNLOADS + MAX_PENDING_REQUESTS`
  joburi distincte simultan. Fiecare job admis poate aștepta propriul permis de
  descărcare sau conversie; nu ține un worker al cozii de analiză. Admiterea
  rămâne limitată pentru a limita numărul de originale și taskuri în memorie.
- Pregătirea video are loc după eliberarea permisului de descărcare. La audio,
  originalul este descărcat și verificat, apoi permisul se eliberează înainte de
  a aștepta conversia MP3. Sursa și outputul sunt înregistrate ca active pe
  durata așteptării și conversiei, inclusiv pentru intervale scurte de curățare.
- Uploadul are loc după eliberarea permisului de conversie. O conexiune Telegram
  lentă nu ocupă un permis de encoder sau de descărcare.
- Cozile păstrează deduplicarea și anularea izolată a abonaților. Shutdown-ul
  închide livrările înaintea lease-urilor și resurselor comune.

Un backlog de conversii nu ocupă permisele de descărcare: alte livrări deja
admise pot continua descărcarea și analizele pot continua separat. Dacă plafonul
livrărilor admise este atins, cererile suplimentare primesc eroarea de coadă plină
existentă. Creșterea concurenței mărește consumul CPU/disc; pentru un server mic
valoarea implicită de o conversie simultană este un punct de pornire conservator.

Bugetul total al pregătirii unui preview este `UPLOAD_TIMEOUT_SECONDS` și include
ffprobe, așteptarea conversiei, remuxare, fallback și verificări. Încercarea de
remuxare are cel mult `min(30 secunde, buget/3)`, pentru a lăsa timp fallback-ului.
Conversia audio rămâne în deadline-ul total al downloaderului (implicit 240 s,
inclusiv transferul), iar livrarea completă în `OPERATION_TIMEOUT_SECONDS`.
Uploadul are propriul buget `UPLOAD_TIMEOUT_SECONDS`. Timeoutul/anularea
eliberează permisele și curăță fișierele parțiale.

Limitele de bytes existente rămân active: `MAX_FILE_SIZE_MB` pentru descărcare,
politica cloud/locală pentru upload și `-fs` pentru output. O variantă care nu
încape după verificare nu este trimisă. Nu se micșorează automat rezoluția sau
calitatea selectată pentru a forța respectarea limitei.

## Verificare

`tests/test_video_preparation.py` folosește fixture-uri FFmpeg locale pentru
reutilizare, remuxare și validarea fluxurilor. Testele acoperă fallback-ul după
erori/timeout, output corupt, pierdere de sunet sau durată, metadatele receipt/cache,
limita comună audio/video, concurența configurată, anularea, expirarea în coadă,
protecția față de cleaner și progresul descărcărilor în timpul backlog-ului.

```sh
.venv/bin/python -m pytest -m 'not live' -q
.venv/bin/python -m bot.main --check
.venv/bin/python -m pip --no-cache-dir check
```
