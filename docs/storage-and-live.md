# Retenție limitată și verificări live pe server

## Plafonul originalelor

```dotenv
MAX_RETAINED_ORIGINALS_MB=512
```

Plafonul este în **MiB** (`valoare × 1024 × 1024`), implicit 512 MiB. Valoarea
zero dezactivează retenția, fără să dezactiveze descărcarea, conversia sau uploadul.
Valorile negative sunt refuzate la configurare.

După livrarea reușită a unui preview, originalul poate fi reutilizat timp de
**180 de secunde**. O nouă livrare reușită poate reînnoi acest termen, ca înainte.
Plafonul de spațiu poate evacua un original inactiv înainte de expirare. „Ca fișier”
poate apoi folosi cache-ul Telegram sau descărca din nou originalul.

`SharedDownloads` măsoară dimensiunile efective ale fișierelor, nu estimările
extractorului. Fișierele comune sunt numărate o singură dată după cale. Pentru un
album gestionat de serviciu se numără și imaginile, și arhiva. Din prudență,
originalele complete aflate în utilizare sau în așteptarea curățării sunt incluse
în calculul bugetului, chiar dacă nu pot fi evacuate în acel moment.

La presiune de spațiu:

1. Se încearcă eliminarea celor mai vechi originale eligibile, în ordinea creării
   operațiilor lor. Reînnoirea termenului de 180 s nu mută un original la finalul
   ordinii de evacuare.
2. Un original cu utilizatori activi nu este eligibil. Referințele independente
   din `FileRegistry`, inclusiv cele pe directoare părinte sau fișiere din album,
   sunt de asemenea protejate. Referința de retenție este diferențiată de cele
   ale operațiilor active.
3. Dacă originalele active depășesc plafonul sau ștergerea eșuează, retenția nouă
   poate fi refuzată. Descărcarea, conversia și trimiterea continuă cu lease-ul
   existent; nu sunt anulate și fișierul nu este șters în timpul folosirii.
4. Eliberarea lease-urilor, timerul de expirare și întreținerea reîncearcă
   curățarea. Un fișier expirat protejat de o referință externă este verificat
   din nou la aproximativ o secundă, fără a necesita o cerere nouă.

Admiterea în retenție și alegerea victimelor sunt sincrone pe event loop;
ștergerea unui original eligibil verifică din nou referințele sub lock-ul
registrului. Cleaner-ul de fișiere abandonate folosește aceeași verificare
atomică, în locul unui snapshot care ar putea deveni învechit înainte de ștergere.
Un eșec de curățare nu transformă un upload reușit într-un upload eșuat.

Acesta este un plafon al originalelor gestionate pentru reutilizare, **nu o cotă
hard a întregului filesystem**. Operațiile active pot depăși temporar plafonul;
fișierele parțiale, outputurile conversiilor, SQLite și logurile au propriile
limite/reguli. Verificările existente pentru spațiu liber înaintea descărcării
rămân active.

## Restart și shutdown

Timer-ele monotone, lease-urile și lista de retenție sunt în memorie. După restart
nu se reiau termenele vechi și nu se reconstruiesc lease-uri din fișierele găsite.
Runtime-ul ia mai întâi lock-ul de instanță, apoi curăță fișierele abandonate din
`DOWNLOAD_DIR/bot-work`. Nu curăță fișierele CLI din directorul părinte sau
fișierele cu alte nume. Un al doilea runtime nu poate curăța workspace-ul unui
runtime care deține deja lock-ul.

La shutdown se opresc mai întâi livrările gestionate de runtime. Managerul de
fișiere nu șterge un original încă protejat de un lease; ultima eliberare face
curățarea. Dacă procesul este oprit brutal ori discul refuză ștergerea, recuperarea
se face la următoarea pornire. Cache-ul Telegram SQLite nu este eliminat de plafon.

## Verificări locale

```bash
.venv/bin/python -m pytest -m 'not live' -q
.venv/bin/python -m bot.main --check
.venv/bin/python -m pip --no-cache-dir check
```

Testele locale verifică plafonul real în bytes, evacuarea după vechime, expirarea,
fișiere mai mari decât plafonul, referințe independente, lease-uri concurente,
anulare în timpul curățării, ștergere refuzată temporar și recuperarea la restart.
Fixture-urile FFmpeg locale nu probează accesul real la TikTok, YouTube sau Instagram.

## Verificări publice live — numai din mediul serverului

Testele sunt opționale și cer simultan:

- `RUN_PLATFORM_LIVE=1`: activarea explicită a accesului la platformele publice.
- `LIVE_TEST_CONTEXT=server`: declarația operatorului că procesul rulează pe server.
  Este o declarație de context, nu o detecție automată sau o conexiune SSH făcută
  de test; nu se setează local pentru a prezenta un rezultat drept rezultat de server.
- Un URL public propriu, potrivit platformei și tipului testat.

| Caz | Variabilă | ID pytest |
| --- | --- | --- |
| TikTok video | `TIKTOK_TEST_URL` | `tiktok-video` |
| TikTok album foto, opțional | `TIKTOK_TEST_PHOTO_URL` | `tiktok-photo` |
| YouTube Shorts | `YOUTUBE_SHORTS_TEST_URL` | `youtube-shorts` |
| Instagram Reel | `INSTAGRAM_REELS_TEST_URL` | `instagram-reels` |

Fiecare caz face analiza explicită, descărcarea și o verificare independentă a
integrității: ffprobe plus decodare completă FFmpeg. Pentru video verifică
fluxul, dimensiunile și durata. Pentru foto verifică fiecare imagine și integritatea
ZIP-ului. Fișierele și cache-ul extractorului sunt închise în `finally`.
Se păstrează protecțiile URL/DNS/IP și nu se folosesc autentificare, cookie-uri,
proxy-uri de ocolire sau servicii terțe pentru a forța accesul.

Testele publice nu creează un client Telegram și nu trimit mesaje. Comenzile de
mai jos selectează exclusiv `test_public_media_live`. Testul separat
`test_real_telegram_delivery` necesită `RUN_TELEGRAM_LIVE=1`, token și destinație;
nu trebuie activat fără autorizare explicită pentru mesaje reale.

### Comenzi pentru server, într-un mediu Python separat

După conectarea la serverul pe care se execută botul și transferul codului actual,
rulează din rădăcina proiectului. Dacă proiectul este în alt director, ajustează
numai comanda `cd`. FFmpeg și ffprobe trebuie să fie instalate pe server.

```bash
cd ~/tiktok-ultra-downloader
command -v ffmpeg ffprobe
python3 -m venv .venv-live
.venv-live/bin/python -m pip install --constraint constraints.txt '.[dev]'
mkdir -p test-results
export RUN_PLATFORM_LIVE=1
export LIVE_TEST_CONTEXT=server
# Înlocuiește valorile cu URL-uri publice reale; nu folosi cookie-uri sau tokenuri.
export TIKTOK_TEST_URL='URL_PUBLIC_TIKTOK_VIDEO'
export YOUTUBE_SHORTS_TEST_URL='URL_PUBLIC_YOUTUBE_SHORTS'
export INSTAGRAM_REELS_TEST_URL='URL_PUBLIC_INSTAGRAM_REEL'
# Opțional, pentru album:
# export TIKTOK_TEST_PHOTO_URL='URL_PUBLIC_TIKTOK_PHOTO'
.venv-live/bin/python -m pytest tests/test_live.py::test_public_media_live \
  -m live -q -ra -s --junitxml=test-results/platform-live.xml
```

Pentru repetarea unei singure platforme:

```bash
.venv-live/bin/python -m pytest 'tests/test_live.py::test_public_media_live[tiktok-video]' -m live -q -ra -s
.venv-live/bin/python -m pytest 'tests/test_live.py::test_public_media_live[youtube-shorts]' -m live -q -ra -s
.venv-live/bin/python -m pytest 'tests/test_live.py::test_public_media_live[instagram-reels]' -m live -q -ra -s
```

### Același runtime Docker pe server

Imaginea de producție nu include pytest sau testele. Se poate folosi un container
**separat**, din aceeași imagine și aceeași configurație de server, fără a modifica
procesul botului. Cu URL-urile de mai sus exportate în shell, rulează:

```bash
sudo docker compose run --rm --no-deps \
  -v "$PWD/tests:/app/tests:ro" \
  -e RUN_PLATFORM_LIVE=1 -e LIVE_TEST_CONTEXT=server \
  -e TIKTOK_TEST_URL="$TIKTOK_TEST_URL" \
  -e YOUTUBE_SHORTS_TEST_URL="$YOUTUBE_SHORTS_TEST_URL" \
  -e INSTAGRAM_REELS_TEST_URL="$INSTAGRAM_REELS_TEST_URL" \
  -e TIKTOK_TEST_PHOTO_URL="${TIKTOK_TEST_PHOTO_URL:-}" \
  bot sh -eu -c '
    python -m venv --system-site-packages /tmp/live-tests
    /tmp/live-tests/bin/python -m pip install "pytest>=8,<9" "pytest-asyncio>=0.23,<1"
    probe_dir=$(mktemp -d /app/downloads/platform-probe.XXXXXX)
    /tmp/live-tests/bin/python -m pytest tests/test_live.py::test_public_media_live \
      -m live -q -ra -s -p no:cacheprovider --basetemp "$probe_dir"
  '
```

Media testelor este scrisă în volumul de downloads, într-un director temporar
separat, nu în tmpfs-ul de 64 MiB sau în workspace-ul activ `bot-work`. Scriptul nu
pornește polling, nu apelează testul Telegram și nu curăță volumele botului.

## Interpretarea rezultatelor

Un mesaj `LIVE_RESULT platform=... analysis=passed download=passed integrity=passed`
înseamnă că toate cele trei etape ale acelui caz au reușit, în acel mediu și pentru
acel URL. Nu garantează disponibilitatea tuturor postărilor platformei. Un eșec
este raportat ca eșec; nu este transformat în skip pentru blocaje regionale,
restricții de platformă sau erori de integritate.

Un caz omis (`skipped`) înseamnă **neverificat**, inclusiv dacă pytest are exit
code zero. Păstrează separat raportul local și raportul de pe server. O execuție
locală sau simpla construire a imaginii nu reprezintă validare live pe server.

## Rezultate efective — 13 noiembrie 2026

| Verificare | Rezultat observat |
| --- | --- |
| Suită locală, `-m 'not live'` | 360 teste trecute; 5 cazuri live excluse |
| Dispatcher offline, pip check, compileall, git diff --check | Trecute |
| Selectarea celor 4 cazuri publice live în mediul local, fără opt-in | 4 omise; nu este validare pe server |
| TikTok video/foto, pe server | Neverificat: lipsesc URL-urile și destinația/accesul de server configurat |
| YouTube Shorts, pe server | Neverificat: lipsesc URL-ul și destinația/accesul de server configurat |
| Instagram Reels, pe server | Neverificat: lipsesc URL-ul și destinația/accesul de server configurat |
| Mesaje Telegram live | Nu au fost trimise; testul separat nu a fost executat |

Comenzile pentru server și Docker de mai sus sunt instrucțiuni pentru operator,
nu execuții efectuate în această verificare locală.
