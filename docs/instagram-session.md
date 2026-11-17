# Instagram Reels pe Oracle

## Linkuri și diagnostic

Botul acceptă linkuri individuale `/reel/ID/`, `/reels/ID/` și `/p/ID/`.
Parametrii de distribuire (`igsh`, `dlrf`, `obrf`) sunt eliminați înainte de
extragere. Ruta `/p/` este păstrată: nu toate postările Instagram sunt Reels.
Sunt procesate numai videoclipurile individuale publice; albumele Instagram
și postările care conțin doar fotografii nu sunt suportate de acest pipeline.

Erorile de autentificare și HTTP 429 sunt clasificate drept restricții de acces
Instagram, inclusiv pentru prefixul istoric `[PublicInstagram]`. Erorile DNS și
întreruperile de conexiune rămân erori de rețea; cookie-urile nu repară DNS-ul.
Nu relansa repetat cererile la HTTP 429: verifică sesiunea și așteaptă ridicarea
restricției. Nu există o configurație care să garanteze permanent accesul la
orice Reel, indiferent de starea postării, a contului sau a IP-ului.

Eroarea `redirected to the login page` împreună cu `rate-limit for accessing
posts anonymously` confirmă că Instagram refuză accesul anonim de pe IP-ul
serverului. Nu indică ștergerea Reel-ului. Actualizarea codului singură nu
restabilește accesul: trebuie furnizată o sesiune Instagram validă sau așteptată
ridicarea restricției de către Instagram. Nici sesiunea autentificată nu garantează
accesul dacă Instagram restricționează contul ori IP-ul.

## Fișierul de sesiune

Exportă doar cookie-urile Instagram dintr-un browser în care ești autentificat,
în format Mozilla/Netscape. Fișierul trebuie să conțină un `sessionid` valid.
Nu trimite fișierul în chat și nu îl publica în Git. Directorul `secrets/` este
deja exclus din repository și din contextul Docker.

Referință pentru format și export:
[documentația yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp).

## Instalarea pe server

Publică și instalează mai întâi imaginea care include această modificare.
Pe server sunt necesare versiunea curentă a `compose.deploy.yml` și scriptul
`deploy/enable-instagram.sh`, în directorul botului (implicit `/opt/tt-dowland`).
Ambele fișiere Compose din proiect montează acum `./secrets` doar pentru citire
la `/run/secrets`. Directorul gol permite în continuare rularea anonimă.
Nu este necesar un override Compose, iar updaterul folosește aceeași montare.

Din directorul care conține `compose.deploy.yml`, instalează fișierul exportat
ca `secrets/instagram.txt`. Nu copia cookie-urile în `.env`.

```bash
sudo install -d -m 700 secrets
sudo install -m 600 /cale/catre/cookie-uri-exportate.txt secrets/instagram.txt
sudo bash deploy/enable-instagram.sh "$PWD"
```

Scriptul necesită Bash, Python 3, `flock` și Docker Compose pe gazda Linux. Folosește
aceeași blocare ca actualizatorul automat; dacă o actualizare rulează deja,
reîncearcă activarea după terminarea ei. Setează
proprietarul directorului și fișierului la UID/GID 10001 și permisiunile la
700, respectiv 400. Validează offline formatul Netscape și expirarea cookie-ului
`sessionid`, fără conectare la Instagram sau Telegram. Această verificare nu
confirmă că Instagram încă acceptă sesiunea.

După validare, scriptul păstrează o copie privată `.env.instagram-backup.*`,
setează `INSTAGRAM_COOKIES_FILE=/run/secrets/instagram.txt` în `.env` și recreează
numai serviciul `bot`. Celelalte setări din `.env` sunt păstrate. Dacă Docker
raportează o eroare la recreare, scriptul restaurează `.env` și încearcă repornirea
cu setările anterioare. Validarea eșuată nu modifică `.env` și nu repornește botul.

Dacă ai folosit configurația mai veche cu `secrets:` și o valoare fixă
`INSTAGRAM_COOKIES_FILE` în `services.bot.environment`, migrează acea montare la
cea curentă și elimină valoarea fixă, care are prioritate față de `.env`.
Păstrează celelalte personalizări locale din Compose.

Trimite din nou Reel-ul în bot. Testul CLI de mai jos folosește același pipeline
ca botul, inclusiv sesiunea configurată; descarcă și verifică fișierul fără upload
în Telegram:

```bash
sudo docker compose -p tt-dowland -f compose.deploy.yml exec bot \
  python -m bot.cli 'https://www.instagram.com/reel/DfsdPpwOSl7/'
```

## Comportament și reînnoire

`INSTAGRAM_COOKIES_FILE` gol păstrează accesul anonim. În afara Docker, setează
variabila la calea locală a fișierului. Cookie-urile se încarcă în memorie numai
pentru extragerea și descărcarea Instagram, filtrate la domeniile Instagram.
Fișierul original nu este rescris. TikTok și YouTube nu primesc această sesiune.
Selecția calității și fallback-ul pentru watermark necunoscut rămân active.

Dacă sesiunea expiră sau Instagram cere o verificare, autentifică-te în browser,
rezolvă verificarea și exportă un fișier nou. Repetă instalarea fișierului și
comanda `enable-instagram.sh` de mai sus. Recrearea elimină snapshoturile de
analiză din memorie; montarea directorului permite citirea fișierului înlocuit.
Botul afișează un mesaj dedicat când sesiunea lipsește, este invalidă sau Instagram
refuză accesul, fără a publica eroarea brută ori cookie-urile.
