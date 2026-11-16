# Instagram Reels pe Oracle

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
Din directorul care conține `compose.deploy.yml`, salvează fișierul exportat ca
`secrets/instagram.txt`. Nu copia cookie-urile în `.env`.

```bash
mkdir -p secrets
chmod 700 secrets
sudo chown 10001:10001 secrets/instagram.txt
sudo chmod 400 secrets/instagram.txt
```

În fișierul **existent** `compose.deploy.yml`, adaugă sub `services.bot`
configurația `secrets` și completează blocul său `environment`. La nivelul rădăcină
al fișierului adaugă definiția secretului. Păstrează toate celelalte opțiuni și volume:

```yaml
services:
  bot:
    environment:
      INSTAGRAM_COOKIES_FILE: /run/secrets/instagram_cookies
    secrets:
      - instagram_cookies

secrets:
  instagram_cookies:
    file: ./secrets/instagram.txt
```

Acesta este un fragment de îmbinat în configurația existentă, nu un înlocuitor
pentru întregul fișier. Secretul este montat read-only. Permisiunile fișierului
gazdă permit citirea de către UID 10001 folosit de bot. Updaterul existent citește
același `compose.deploy.yml`, deci va păstra montarea la actualizările imaginii.

```bash
sudo docker compose -p tt-dowland -f compose.deploy.yml config --quiet
sudo docker compose -p tt-dowland -f compose.deploy.yml up -d bot
```

Trimite din nou Reel-ul în bot. Testul CLI de mai jos folosește același pipeline
ca botul, inclusiv sesiunea configurată; descarcă și verifică fișierul fără upload
în Telegram:

```bash
sudo docker compose -p tt-dowland -f compose.deploy.yml exec bot \
  python -m bot.cli 'https://www.instagram.com/reel/Dfy8QznNhn5/'
```

## Comportament și reînnoire

`INSTAGRAM_COOKIES_FILE` gol păstrează accesul anonim. În afara Docker, setează
variabila la calea locală a fișierului. Cookie-urile se încarcă în memorie numai
pentru extragerea și descărcarea Instagram, filtrate la domeniile Instagram.
Fișierul original nu este rescris. TikTok și YouTube nu primesc această sesiune.
Selecția calității și fallback-ul pentru watermark necunoscut rămân active.

Dacă sesiunea expiră sau Instagram cere o verificare, autentifică-te în browser,
rezolvă verificarea și exportă un fișier nou. Înlocuiește secretul, reaplică
proprietarul și permisiunile și recreează containerul cu `up -d --force-recreate bot`.
Recrearea remontează fișierul nou și elimină snapshoturile de analiză din memorie.
Botul afișează un mesaj dedicat când sesiunea lipsește, este invalidă sau Instagram
refuză accesul, fără a publica eroarea brută ori cookie-urile.
