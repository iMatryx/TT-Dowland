# Deployment OCI ulterior — Ubuntu 24.04 ARM64

**Pentru VM-ul existent și actualizare la push pe `main`, folosește
[ghidul pas cu pas DEPLOY_AUTO.md](DEPLOY_AUTO.md).** Ghidul de mai jos rămâne
varianta manuală pentru un host ARM64 nou; nu aplica instalarea Docker de aici
pe un server care găzduiește deja alte proiecte fără verificarea configurației.

Acesta este un ghid pentru o activare viitoare. Niciuna dintre acțiunile Oracle,
SSH sau Telegram de mai jos nu este executată în etapa de pregătire.

## 1. Resurse și condiții înainte de creare

Ai nevoie de cont OCI, home region cu capacitate disponibilă, acces SSH prin cheie,
o imagine Ubuntu 24.04 **ARM64** și shape Ampere `VM.Standard.A1.Flex` în limita
gratuită a contului. Nu selecta shape AMD pentru acest profil Compose ARM64.

Documentația Oracle consultată la 12 noiembrie 2026 indică pentru A1 primele
1.500 OCPU-ore și 9.000 GB-ore/lună, echivalentul a 2 OCPU și 12 GB RAM pentru
tenancies Always Free. Verifică întotdeauna consola, limitele și resursele deja
utilizate în cont înainte de creare; nu presupune limitele istorice de 4 OCPU/24 GB.
Stocarea boot/volume și traficul trebuie să rămână în cotele gratuite. Nu este
necesar load balancer, bază de date gestionată, domeniu sau serviciu plătit.

Capacitatea poate lipsi temporar în regiune, iar instanțele Always Free inactive
pot fi recuperate de Oracle. Nu este garantat uptime 24/7 și nu se recomandă
încărcare artificială pentru a evita recuperarea. Păstrează backup în afara VM.
Sursa limitelor și condițiilor: [Oracle Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).

## 2. Instanță, rețea și SSH — de făcut numai la deployment

În consola OCI, verifică eticheta/eligibilitatea Always Free și costul estimat,
selectează Ubuntu 24.04 ARM64, alocă resurse în cota disponibilă și adaugă numai
cheia publică SSH. Cheia privată rămâne pe calculatorul tău.

Permite inbound TCP 22 numai din IP-ul tău administrativ, în regulile OCI și în
firewall-ul hostului. Botul nu necesită inbound 80/443 sau alte porturi publicate.
Este necesar egress DNS și HTTPS către Telegram, TikTok, registry și repository-uri;
unele surse publice/pachete pot necesita și HTTP. Nu oferi containerului acces
la credențiale OCI sau socket-ul Docker.

Dacă nu ai deja o cheie dedicată, creeaz-o local fără a suprascrie o cheie existentă:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/oci_tiktok
```

După ce VM există, înlocuiește valorile exemplificative și verifică fingerprint-ul
hostului înainte de acceptare:

```bash
ssh -i ~/.ssh/oci_tiktok ubuntu@ADRESA_PUBLICA_VM
uname -m
. /etc/os-release
printf '%s %s\n' "$ID" "$VERSION_ID"
```

Rezultatele așteptate sunt `aarch64` și Ubuntu 24.04. Folosește contul administrativ
al imaginii, de regulă `ubuntu`, nu root. Referință: [acces SSH OCI](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/accessinginstance.htm).

## 3. Docker Engine și Compose

Pe viitorul host, după verificarea distribuției/arhitecturii, configurează
repository-ul oficial Docker pentru Ubuntu Noble ARM64:

```bash
sudo apt-get update
sudo apt-get install --yes ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<'DOCKER_APT'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: arm64
Signed-By: /etc/apt/keyrings/docker.asc
DOCKER_APT
sudo apt-get update
sudo apt-get install --yes docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
```

Acest bloc presupune un host nou, fără pachete Docker conflictuale. Pentru hosturi
existente, urmează secțiunea de migrare/eliminare a conflictelor din
[instrucțiunile oficiale Docker](https://docs.docker.com/engine/install/ubuntu/).
Ubuntu 24.04 și ARM64 sunt suportate. Compose trebuie să fie cel puțin 2.24.
Comenzile de mai jos folosesc `sudo`; nu este necesar să adaugi utilizatorul în
grupul `docker`, care oferă control asupra daemonului.

## 4. Transferul proiectului

Din rădăcina proiectului local, trimite numai codul și configurația exemplu:

```bash
ssh -i ~/.ssh/oci_tiktok ubuntu@ADRESA_PUBLICA_VM 'mkdir -p ~/tiktok-ultra-downloader'
rsync -az -e 'ssh -i ~/.ssh/oci_tiktok' \
  --exclude='.git/' --exclude='.venv/' --exclude='.env' \
  --exclude='downloads/' --exclude='data/' --exclude='backups/' \
  --exclude='__pycache__/' --exclude='.pytest_cache/' --exclude='*.egg-info/' \
  --exclude='/tiktok-ultra-downloader/' \
  ./ ubuntu@ADRESA_PUBLICA_VM:~/tiktok-ultra-downloader/
```

Nu transfera vechiul scaffold, fișiere media sau SQLite live prin rsync. Nu este
necesar un registry public sau `docker push`. `rsync` nu folosește `--delete` și
nu șterge `.env` de pe server. Pentru actualizări auditate folosește același proces
sau un checkout al unei revizii verificate, fără a publica secrete.

## 5. Variabile și verificare înainte de activare

Pe server, când dorești activarea:

```bash
cd ~/tiktok-ultra-downloader
test -f .env || cp .env.example .env
chmod 600 .env
```

Editează local `.env` cu tokenul BotFather. Nu îl introduce într-o comandă care îl
afișează sau într-un build arg. Începe cu valorile implicite: 3 operații active,
32 în așteptare, cache activ, limită locală 50 MiB și politica standard Telegram.
Compose fixează căile containerului la `/app/data/cache.sqlite3` și `/app/downloads`.

Imaginea rulează ca UID/GID 10001 și folosește volume numite cu permisiuni proprii.
Nu monta directoare host cu alte ownership-uri fără a pregăti permisiunile.
Limitele runtime Compose sunt 1 GiB RAM și 1,5 CPU; ajustează-le numai după măsurare,
în interiorul resurselor gratuite ale VM. Păstrează resurse și spațiu pentru build,
OS, loguri și backup. Rulează un singur proces pentru acest bot/workspace.

```bash
sudo docker compose config --quiet
sudo docker compose build --pull
sudo docker compose run --rm --no-deps bot python -m bot.main --check
sudo docker compose run --rm --no-deps bot ffprobe -version
```

Acestea verifică configurația, build-ul și utilitarele, nu autentificarea Telegram
sau un download real. Nu rula `docker compose config` fără `--quiet` în loguri
partajate: configurația rezolvată poate conține tokenul din mediu.

## 6. Pornire, monitorizare și oprire

Numai după aprobarea deployment-ului și configurarea tokenului:

```bash
sudo docker compose up -d --build
sudo docker compose ps --all
sudo docker compose logs --tail=100 -f bot
sudo docker stats --no-stream
df -h
```

Verifică logurile pentru pornire, erori de acces, retry, cache hit/miss și cleanup.
Trimite manual `/start`, `/help` și un link public valid, apoi verifică separat
video și document original. Acesta este testul real de integrare, distinct de pytest.

Docker monitorizează procesul și aplică `restart: unless-stopped`; nu există
healthcheck de conectivitate, nici restart automat pentru un proces blocat logic.
Logurile au rotație locală 10 MiB × 3. Nu sunt configurate servicii de logging plătite.

```bash
sudo docker compose stop
sudo docker compose start
# Elimină containerul, dar păstrează volumele:
sudo docker compose down
sudo docker compose up -d
```

Oprirea folosește SIGTERM și 90 s grace Docker, mai mult decât cele 15 s implicite
ale aplicației. Dacă mărești `SHUTDOWN_GRACE_SECONDS`, mărește corespunzător și
grace-ul Docker. Cache-ul rămâne în volum; operațiile/coada/butoanele nu se reiau
automat. Fișierele abandonate sunt curățate la următoarea pornire.

## 7. Backup și restaurare

Folosește API-ul SQLite backup pentru o copie consistentă, inclusiv în modul WAL.
În directorul proiectului de pe server, când containerul rulează:

```bash
mkdir -p backups
chmod 700 backups
sudo docker compose exec -T bot python - <<'PY_BACKUP'
import os
import sqlite3
source = sqlite3.connect('file:/app/data/cache.sqlite3?mode=ro', uri=True)
target = sqlite3.connect('/app/data/cache.backup.sqlite3')
try:
    source.backup(target)
finally:
    target.close()
    source.close()
os.chmod('/app/data/cache.backup.sqlite3', 0o600)
PY_BACKUP
backup_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
sudo docker compose cp bot:/app/data/cache.backup.sqlite3 "backups/cache-${backup_stamp}.sqlite3"
sudo chown "$(id -u):$(id -g)" "backups/cache-${backup_stamp}.sqlite3"
chmod 600 "backups/cache-${backup_stamp}.sqlite3"
```

Verifică integritatea copiei (`PRAGMA integrity_check`) și transfer-o prin SSH în
afara VM. Păstrează și `.env` într-un backup privat/protejat, separat de Git și
imagine; nu afișa conținutul în terminale sau loguri partajate.

Pentru restaurare, oprește botul, verifică backup-ul, copiază-l în volumul
`cache-data` ca `/app/data/cache.sqlite3`, cu ownership 10001:10001 și mod 0600,
și elimină numai eventualele fișiere WAL/SHM ale bazei vechi, după oprire.
Nu copia o bază peste una deschisă. Pornește botul și verifică logurile/cache hit.
Nu utiliza `docker compose down -v` pentru operațiuni obișnuite: ar șterge volumele.

## 8. Actualizare și repornire automată

Fă backup, transferă codul revizuit și constrângerile, apoi:

```bash
sudo docker image tag tiktok-ultra-downloader:local tiktok-ultra-downloader:previous
sudo docker compose build --pull
sudo docker compose up -d --no-build
sudo docker compose ps --all
sudo docker compose logs --tail=100 bot
```

Nu porni două versiuni ale botului cu același token. Un rollback folosește imaginea
și backup-ul reviziei anterioare, cu botul oprit și schema verificată. Constrângerile
trebuie retestate când actualizezi yt-dlp, HTTPX/httpcore sau aiogram.

După verificarea compatibilității datelor, imaginea anterioară poate fi reactivată
local, fără registry, prin `docker compose stop`, retagarea `:previous` ca `:local`
și `docker compose up -d --no-build`. Păstrează tag-ul anterior până când noua
versiune a trecut verificările reale; nu curăța toate imaginile/volumele înainte de rollback.

Docker este activat prin systemd, iar politica `unless-stopped` permite repornire
după reboot pentru un container lăsat în funcțiune. Un container oprit explicit
rămâne oprit până la `docker compose start`/`up`. Verifică politica și starea după
reboot; nu sunt necesare cron-uri care să lanseze procese duplicate.

## 9. Ce nu este activat aici

Nu există token real, infrastructură OCI creată, deploy, registry public, webhook,
domeniu, HTTPS inbound sau Telegram Local Bot API Server. API-ul cloud rămâne la
limita standard; Premium nu o schimbă. Local Bot API ar necesita o integrare
dedicată client/politică și instrucțiuni separate, nu doar o limită mărită în `.env`.
Probele live și anduranța pe Oracle ARM64 se fac după activarea autorizată.
