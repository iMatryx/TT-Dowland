# Interpretarea logurilor de performanță

Cu `LOG_LEVEL=INFO` (sau `DEBUG`), loggerul `bot.performance` emite un obiect
JSON în mesajul fiecărui log. Prefixul existent cu data, nivelul și loggerul se
păstrează. Măsurătorile folosesc `time.monotonic()`, în secunde, fără dependență
de fusul orar sau de ajustările ceasului sistemului. Nu sunt necesare servicii
sau dependențe noi.

Exemplu de mesaj, după prefixul loggerului:

```json
{"event":"performance_stage","operation_id":"ad7cd572bd394b9e938ab462317b86ea","platform":"tiktok","stage":"download","duration_seconds":2.419,"result":"success"}
```

## Etape

| `stage` | Ce include |
| --- | --- |
| `queue_wait` | Așteptarea unui job acceptat până la pornirea workerului (`queue=work` pentru analize, `queue=delivery` pentru admiterea livrărilor) sau obținerea semaforului (`queue=analysis` / `download` / `conversion` / `extraction`). Se termină înaintea lucrului efectiv. |
| `analysis` | Rezolvarea linkului, extragerea metadatelor și selecția disponibilă. La descărcare, rezolvarea și reextragerea sunt segmente distincte, fără suprapunere. |
| `download` | Execuția workerului de transfer, cu pornirea procesului și finalizarea acestuia; timpul postprocesării este scăzut și raportat separat. Pentru fotografii, transferul întregului album. |
| `verification` | Verificări ale fișierelor, limitelor, ffprobe, decodare opțională și comparația duratei/audio cu sursa. Pot exista segmente separate pentru fișierul sursă, rezultatul conversiei și verificările finale; fotografiile sunt verificate individual. |
| `conversion` | Postprocesarea yt-dlp (inclusiv combinarea fluxurilor fără recodare), conversia MP3, remuxarea/recodarea preview-ului FFmpeg sau ambalarea ZIP. Verificarea rezultatului și uploadul sunt măsurate separat. |
| `upload` | O cerere Telegram de trimitere media, inclusiv trimiterea prin `file_id`, RetryAfter și backoff. Editările de caption/butoane și mesajele de progres sunt excluse. Un album mare poate produce mai multe cereri. |

`result` este `success` (succes), `error` (eroare) sau `cancelled` (anulare).
O etapă întreruptă prin anularea taskului, inclusiv de un deadline exterior,
raportează `cancelled`; un timeout tradus în excepție în interiorul etapei
raportează `error`. Succesul unei etape nu garantează succesul etapelor următoare.
Etapele care nu au fost executate nu emit durate artificiale de zero.

Identificatorul aleatoriu `operation_id` nu derivă din utilizator, URL sau ID-ul
videoclipului. Se propagă în coroutine și worker-ele asyncio. Platforma este una
dintre `tiktok`, `youtube`, `instagram`, `unknown`; apelurile directe la utilitare
care primesc doar căi de fișiere folosesc `unknown`.

Pentru a identifica blocajele, agregați secunde și percentile după `platform`,
`stage`, `result` și, pentru coadă, `queue`. Nu însumați coada în timpul de download.
Segmentele succesive cu același identificator și etapă sunt lucrări distincte,
nu evenimente repetate. Retry-urile produc segmente noi; uploadul include propriul
backoff. Suma etapelor nu reprezintă o latență completă per utilizator: există
lucru concurent, acces la SQLite, actualizări UI și operații de filesystem în afara
etapelor măsurate.

## Cache-uri și rata de utilizare

```json
{"event":"cache_lookup","operation_id":"ad7cd572bd394b9e938ab462317b86ea","platform":"tiktok","cache":"metadata","outcome":"hit","hits":3,"misses":1,"hit_rate":0.75}
```

- `cache=metadata`: snapshoturi de extracție în memorie, comune analizei afișate
  și descărcării. TTL implicit 300 s (`ANALYSIS_CACHE_TTL_SECONDS`), maximum 512
  intrări LRU (`ANALYSIS_CACHE_CAPACITY`). Se păstrează metadatele și JSON-ul
  extractorului, fără referințe la workspace-uri șterse; apelurile primesc copii
  și fișiere proprii. Erorile nu se memorează. Un transfer cu date memorate care
  eșuează upstream invalidează intrarea și încearcă o singură dată cu date proaspete.
  Vezi [cache-ul de analiză](analysis-cache.md) pentru politica de retry.
- `cache=telegram`: cache-ul SQLite existent cu identificatori `file_id`. Se
  contabilizează o singură decizie logică de lookup, chiar dacă sunt încercate mai
  multe tipuri (`video`, `document`) sau există o reverificare după intrarea în
  coadă. Reverificarea poate transforma un miss inițial într-un hit.
- `outcome` este `hit` sau `miss`. `hits` și `misses` sunt contoare cumulative
  separate pentru fiecare cache și platformă, de la crearea componentei de cache
  în runtime. Căutările pentru analiză și descărcare folosesc același contor metadata.
  `hit_rate = hits / (hits + misses)`; înmulțiți cu 100 pentru procent.
  Calculați o rată globală din sumele contoarelor sau din numărul evenimentelor,
  nu prin media ratelor. Nu adunați snapshoturile cumulative ale fiecărui log.
- Un hit Telegram înseamnă că lookup-ul a găsit o intrare eligibilă. Dacă Telegram
  respinge ulterior identificatorul, uploadul raportează eroare și intrarea se
  invalidează, urmând fallback-ul existent. Hit-ul nu garantează livrarea.
  Erorile de citire SQLite sunt miss-uri cu un warning separat.
- `CACHE_ENABLED=false` dezactivează ambele cache-uri și contabilizarea acestora.
  Fotografiile nu consultă cache-ul Telegram; reutilizarea originalelor temporare
  nu este un hit în cache-ul Telegram sau în cel de metadate.

## Concurență și protecția datelor

Joburile identice din `WorkQueue` emit o singură așteptare în coadă și o singură
analiză/decizie de cache efectivă. Descărcările partajate prin `SharedDownloads`
emit etapele o singură dată, sub identificatorul inițiatorului. Fiecare trimitere
efectivă către un destinatar rămâne un upload separat. Anularea unui singur
abonat nu marchează drept anulată operația comună cât timp mai există abonați.
Un job anulat în coadă emite un singur rezultat și nu emite etape media.

Workerul izolat scrie doar timpi monotoni și rezultate într-un fișier temporar,
pentru separarea postprocesării de transfer. Fișierul este eliminat odată cu
workspace-ul. La anularea procesului, un interval de postprocesare început este
închis de părinte. Dacă diagnosticul lipsește sau este corupt, descărcarea își
păstrează comportamentul, iar durata workerului rămâne în `download`.

Evenimentele de performanță nu conțin tokenuri, URL-uri, căi locale, titluri,
autori, identificatori Telegram sau textul excepțiilor. Filtrul global existent
elimină tokenurile și datele Authorization; acum elimină și URL-urile HTTP(S)
din mesajele bibliotecilor și traceback-uri, inclusiv URL-urile semnate.

## Verificări locale

```sh
.venv/bin/python -m pytest -m 'not live' -q
.venv/bin/python -m bot.main --check
.venv/bin/python -m pip check
```

Testele folosesc ceas controlat, cozi și cache-uri reale, verifică deduplicarea,
propagarea contextului, expirarea și izolarea metadatelor, rezultate de eroare și
anulare și separarea conversiei workerului de transfer. Testele FFmpeg existente
exercită conversia și verificarea locală; accesul la Telegram și platformele
publice rămâne în testele live opționale.

Limita `MAX_CONCURRENT_CONVERSIONS` controlează pregătirea preview-urilor și
conversia MP3; o reutilizare directă nu consumă permis de conversie. Etapele
`conversion` includ și operații fără recodare, precum ZIP și postprocesarea
surselor yt-dlp. Detalii despre limitele pipeline-ului:
[pregătirea video Telegram](telegram-video.md).


## Comparație offline reproductibilă a verificării reutilizate

Rulați `.venv/bin/python -m scripts.benchmark_preparation`. Scriptul generează
local un clip sintetic de 10 s, 640×360, 30 FPS, H.264/AAC, în MP4 și MKV.
Compară pregătirea fără dovada verificării (scanarea repetată de dinainte) cu
reutilizarea unei dovezi valide, folosind etapele existente `bot.performance`.
După încălzire, alternează ordinea și raportează mediana a 7 repetări seriale.
Nu citește configurația botului și nu folosește rețeaua sau Telegram.

La verificarea din 13 noiembrie 2026, pe Darwin arm64, fișiere locale încălzite:

| Pregătire | Verificare fără reutilizare | Verificare cu reutilizare |
| --- | ---: | ---: |
| MP4 compatibil | 37,018 ms | 0,029 ms |
| MKV → MP4 prin stream copy | 71,687 ms | 36,172 ms |

Pentru MKV, remuxarea a avut mediana 38,078 ms fără reutilizare și 37,939 ms cu
reutilizare. Proba rezultatului MP4 rămâne obligatorie. Pentru MP4 compatibil,
trimiterea directă exista deja; comparația de mai sus privește apelurile directe
la utilitarul de pregătire, nu reprezintă o accelerare nouă a uploadului MP4.
Scanarea inițială a descărcării nu este inclusă în tabel.

Aceste valori descriu exclusiv pregătirea locală a fixture-ului, fără analiză,
transfer sursă, cozi încărcate sau upload. Nu sunt estimări pentru Telegram și nu
justifică modificarea paralelismului. Limitele de descărcare, conversie, HTTP și
thread-uri rămân neschimbate. Pentru capacitatea reală sunt necesare măsurători
pe server cu aceleași etape și distribuția reală a surselor.
