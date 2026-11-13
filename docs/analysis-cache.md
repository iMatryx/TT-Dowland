# Cache temporar pentru analiza linkurilor

`CachedExtractor` este comun analizei afișate utilizatorului și descărcării.
Înlocuiește cache-ul anterior, care păstra doar `VideoInfo` pentru 60 de secunde.
Un link analizat poate fi descărcat fără o nouă cerere de metadate la platformă.
Cache-ul Telegram de `file_id` rămâne separat.

## Configurație

```dotenv
CACHE_ENABLED=true
ANALYSIS_CACHE_TTL_SECONDS=300
ANALYSIS_CACHE_CAPACITY=512
ANALYSIS_CACHE_MAX_MB=64
```

| Setare | Implicit | Reguli |
| --- | --- | --- |
| `ANALYSIS_CACHE_TTL_SECONDS` | 300 s (5 minute) | Număr finit, strict pozitiv. TTL fix de la finalizarea extracției; hit-urile nu îl prelungesc. |
| `ANALYSIS_CACHE_CAPACITY` | 512 | Număr de intrări între 1 și 10.000. Evacuare LRU; accesul mută intrarea la final. |
| `ANALYSIS_CACHE_MAX_MB` | 64 MiB | Bugetul snapshoturilor reținute după serializare, cu chei și rezervă pentru obiecte; strict pozitiv. |
| `CACHE_ENABLED` | true | false dezactivează retenția și metricile ambelor cache-uri. Analizele aflate simultan în lucru continuă să fie unite. |

TTL-ul folosește un ceas monoton. Intrările expirate sunt eliminate la următoarea
căutare și nu sunt reutilizate. Cache-ul este exclusiv în memorie, se golește la
shutdown și nu supraviețuiește repornirii. Capacitatea limitează numărul de
snapshoturi, iar bugetul de memorie limitează independent volumul reținut.
LRU evacuează înaintea depășirii oricărei limite. O intrare mai mare decât
întregul buget este returnată apelanților curenți, dar nu este memorată.
Contabilizarea include reprezentarea efectivă a stringurilor serializate, nu doar
fișierul original. Un JSON de extracție mai mare de 10 MiB este refuzat.

## Datele reutilizate și izolarea

Fiecare snapshot conține:

- Metadatele validate `VideoInfo`, serializate în JSON.
- JSON-ul extractorului necesar selecției și transferului yt-dlp, inclusiv URL-uri
  media și headere. Câmpurile care descriu căi de output temporare sunt eliminate.
- Un token intern de generație, folosit pentru invalidarea sigură în concurență.

Snapshotul este imuabil. Fiecare apel primește un `VideoInfo` nou, cu liste și
modele independente, plus un fișier `info.json` nou în workspace-ul apelului.
Cache-ul nu păstrează `Extraction.info_path`. Modificarea sau ștergerea acelui
fișier nu afectează intrarea din cache ori altă descărcare.

Extracția partajată folosește propriul director temporar protejat în
`FileRegistry`. JSON-ul este citit în memorie înainte de eliminarea directorului.
Acest lucru permite anularea primului apelant fără a șterge fișierele unei
extracții care încă deservește alt apelant. Fișierele per apel sunt curățate prin
mecanismele existente ale analizei și downloaderului.

## Concurență și anulare

Cheia este URL-ul canonic validat. Înainte de cache, botul păstrează rezolvarea
controlată a linkurilor scurte și validarea URL-urilor. Variantele normalizate ale
aceluiași URL împart intrarea.

O extracție în curs pentru același URL are un singur task, chiar dacă este cerută
din analiză și din descărcare. Încărcările simultane sunt limitate la
`MAX_CONCURRENT_DOWNLOADS`, iar numărul de URL-uri distincte în lucru/așteptare la
`MAX_CONCURRENT_DOWNLOADS + MAX_PENDING_REQUESTS`. Cererile duplicate pot adera la
un task existent fără a ocupa un nou loc.

Anularea unui abonat nu anulează taskul comun cât timp mai există abonați. Când
ultimul abonat se retrage, taskul este anulat și se așteaptă curățarea lui. Erorile
și anulările nu sunt memorate ca rezultate. Un task deja terminat nu este
reutilizat drept analiză în curs: o căutare nouă consultă starea actuală a
cache-ului, inclusiv invalidările produse între timp.

## URL-uri media expirate și retry

Recuperarea este limitată la transferul media care a utilizat efectiv un snapshot
din cache:

1. Workerul de transfer raportează o eroare upstream de rețea/timeout sau de
   disponibilitate, de exemplu HTTP 403/404 pentru un URL media expirat.
2. Intrarea folosită este invalidată prin tokenul generației. Un eșec întârziat
   nu poate șterge o intrare mai nouă încărcată de altă cerere.
3. Se solicită din nou extracția, apoi se reîncearcă transferul **o singură dată**.
   Cererile concurente de reîmprospătare pot împărți aceeași extracție nouă.
4. Dacă extracția proaspătă sau transferul repetat eșuează, eroarea se propagă.
   Nu se pornește încă o buclă de recuperare.

Aceeași regulă se aplică videoclipurilor, pistelor audio și URL-urilor imaginilor.
Fiecare încercare folosește alt workspace; fișierele parțiale ale celei eșuate sunt
eliminate înaintea reîncercării.

Nu se declanșează reanalizare pentru spațiu insuficient, permisiuni, fișiere locale
lipsă, erori I/O, limite de dimensiune, lipsa FFmpeg, adrese nesigure, verificarea
locală a fișierului, conversie sau upload Telegram. Anularea nu este tratată ca
URL expirat. O descărcare care începe fără date memorate are o singură încercare
și nu intră în mecanismul de recuperare pentru cache.

Retry-ul generic care învelea întreaga descărcare în runtime a fost eliminat,
pentru a evita multiplicarea celor două transferuri. Retry-urile interne yt-dlp
rămân dezactivate. Retry-ul existent pentru analiza inițială a unui link și
politica Telegram RetryAfter sunt independente; nu învelesc recuperarea unui
transfer cu date expirate. Deadline-urile existente continuă să includă extracția
proaspătă, reîncercarea și așteptarea în cozi.

## Validare de rețea și confidențialitate

Un hit nu ocolește normalizarea URL-ului. Transferurile folosesc în continuare
workerul izolat, protocoalele permise, socket guard-ul și verificarea adreselor
DNS/IP, inclusiv pentru URL-uri CDN memorate și redirecturi. Snapshoturile nu se
trimit în callback-uri și nu sunt salvate în SQLite.

URL-urile media semnate și headerele sunt necesare în snapshot, dar nu apar în
loguri. Evenimentele folosesc identificatorul aleatoriu al operației, platforma
și contoare; nu includ cheia URL, tokenul generației sau conținutul JSON.

## Metrici și verificare

Se păstrează evenimentele `cache_lookup` cu `cache=metadata`, `outcome=hit/miss`,
`hits`, `misses` și `hit_rate`. Analiza și descărcarea folosesc același contor per
platformă al extractorului. Un grup de apeluri care împarte o extracție în curs
produce o singură decizie de lookup, nu câte un miss pentru fiecare abonat.
Recuperarea unui snapshot expirat produce în mod normal un hit urmat de un miss.
Contoarele cache-ului Telegram rămân independente.

Testele din `tests/test_analysis_cache.py` verifică TTL/LRU, copii independente,
lipsa referințelor la workspace-uri șterse, coalescerea și limitele de admitere,
anularea și shutdown-ul, invalidarea concurentă, URL-uri media expirate pentru
video/foto, nerepetarea erorilor locale/upload și numărul exact de încercări.

```sh
.venv/bin/python -m pytest -m 'not live' -q
.venv/bin/python -m bot.main --check
.venv/bin/python -m pip --no-cache-dir check
```
