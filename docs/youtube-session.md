# Sesiune YouTube pe Oracle

`Sign in to confirm you’re not a bot` indică o verificare anti-bot. Nu confirmă
că videoclipul este privat. Botul clasifică acest refuz separat și poate încărca
opțional o sesiune YouTube prin `YOUTUBE_COOKIES_FILE`.

Fișierul Instagram nu este folosit pentru YouTube. Implicit, accesul rămâne anonim.
Nu sunt activate proxy-uri sau ocolirea restricțiilor regionale. O sesiune validă
nu garantează accesul dacă YouTube restricționează contul sau IP-ul; anumite
fluxuri pot necesita și PO Tokens, care nu sunt configurate prin această opțiune.

## Export

Folosește preferabil un cont Google separat pentru bot. yt-dlp avertizează că
folosirea contului la descărcări automate poate duce la limitare sau suspendare.

Conform [instrucțiunilor yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies):

1. Deschide o fereastră privată/incognito și autentifică-te în YouTube.
2. În aceeași filă accesează `https://www.youtube.com/robots.txt`.
3. Exportă cookie-urile **youtube.com** în format Netscape, ca `youtube.txt`.
   Extensia de export trebuie să aibă acces în modul incognito.
4. Închide fereastra privată după export, pentru a limita rotația cookie-urilor.

Nu publica fișierul și nu trimite conținutul lui în chat. Nu exporta toate
site-urile din browser. Sesiunile pot expira sau fi invalidate și trebuie atunci
reînnoite. Cookie-urile nu sunt credențiale permanente.

## Activare în Docker

Instalează mai întâi imaginea care include suportul `YOUTUBE_COOKIES_FILE`.
Transferă fișierul pe Oracle în `secrets/youtube.txt`, relativ la directorul care
conține `compose.deploy.yml`. Directorul `secrets/` este exclus din Git și Docker.

```bash
chmod 700 secrets
sudo chown 10001:10001 secrets/youtube.txt
sudo chmod 400 secrets/youtube.txt
```

În `services.bot.environment` din configurația existentă adaugă:

```yaml
      YOUTUBE_COOKIES_FILE: /run/secrets/youtube_cookies
```

În `services.bot.volumes` adaugă:

```yaml
      - ./secrets/youtube.txt:/run/secrets/youtube_cookies:ro
```

Păstrează setările și volumele existente, inclusiv cele pentru Instagram.
Apoi validează configurația și, dacă nu sunt erori, recreează botul:

```bash
sudo docker compose -p tt-dowland -f compose.deploy.yml config --quiet
sudo docker compose -p tt-dowland -f compose.deploy.yml up -d --force-recreate bot
```

Verificare fără afișarea cookie-urilor:

```bash
sudo docker compose -p tt-dowland -f compose.deploy.yml exec bot python -c "import os; from bot.config import Settings; p=Settings().youtube_cookies_file; print('Configurat:', bool(p)); print('Accesibil:', bool(p and p.is_file() and os.access(p, os.R_OK)))"
```

Ambele valori trebuie să fie `True`. Trimite din nou Short-ul în bot sau testează
pipeline-ul complet, fără upload în Telegram:

```bash
sudo docker compose -p tt-dowland -f compose.deploy.yml exec bot \
  python -m bot.cli 'https://youtube.com/shorts/16ECzizrWR8'
```

`python -m yt_dlp` rulat separat nu încarcă automat setarea botului.
La reînnoire, înlocuiește fișierul, reaplică permisiunile și recreează containerul.

## Izolarea sesiunii

Cookie-urile sunt încărcate în memorie la extragere și transfer și sunt filtrate
la domeniul YouTube și subdomeniile sale. Cookie-urile pentru Google, Instagram
sau alte site-uri nu sunt importate. Fișierul montat read-only nu este rescris.
Metadatele care raportează conținut privat, destinat membrilor sau care necesită
autentificare rămân respinse de politica existentă de conținut public.
