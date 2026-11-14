# Oracle: instalare și actualizare după push pe main

Imaginea folosește implicit `python:3.12-slim-trixie`. În testul comparativ pe
Mac ARM64, linkul TikTok verificat a eșuat în imaginea Bookworm și a expus 8
variante în imaginea Trixie, cu aceeași versiune yt-dlp 2026.10.26. Testul a
verificat extracția metadatelor. Accesul și livrarea pe Oracle se verifică separat
după actualizare. Schimbarea bazei modifică mai multe biblioteci de sistem și
nu izolează OpenSSL drept singura cauză.

Aceste instrucțiuni sunt pentru VM-ul **existent**, alături de celălalt proiect.
Nu au fost executate pe server. Nu trebuie repornit VM-ul pentru actualizări.

GitHub Actions rulează testele offline, apoi publică imaginea
`ghcr.io/imatryx/tt-dowland:main` pentru AMD64 și ARM64. Un timer de pe server
verifică imaginea la fiecare minut după încheierea verificării precedente.
Serverul recreează numai serviciul `bot` din proiectul Compose `tt-dowland`.
Publicarea folosește `GITHUB_TOKEN` automat; nu adăuga token Telegram sau cheie SSH
în GitHub. Actualizarea începe după ce imaginea a fost publicată cu succes, nu
imediat după push. Push-urile rapide pot fi reunite la ultima versiune disponibilă.

## 1. Verifică serverul existent

În consola Oracle: **Compute → Instances → instanța existentă → Public IP**.
Ai nevoie de cheia SSH cu care te conectezi deja la acest server, nu de una nouă.
Pe Mac, în Terminal, setează valorile tale; păstrează acest terminal deschis:

```bash
export OCI_HOST='IP_PUBLIC_SERVER'
export OCI_USER='ubuntu'
export OCI_KEY="$HOME/.ssh/cheia-ta.key"
chmod 600 "$OCI_KEY"
ssh -i "$OCI_KEY" "$OCI_USER@$OCI_HOST"
```

Pentru Ubuntu utilizatorul implicit este `ubuntu`, pentru Oracle Linux este `opc`.
Verifică amprenta SSH înainte să accepți o conexiune nouă.
[Referință Oracle](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/connect-to-linux-instance.htm).

În terminalul conectat la **server**:

```bash
cat /etc/os-release
uname -m
free -h
df -h /
sudo docker compose version
sudo docker ps
systemctl --version
command -v flock
```

Continuă dacă ai Linux cu systemd, Docker și Compose **minimum 2.24**, `flock`,
arhitectură `aarch64`/`arm64` sau `x86_64`, spațiu pentru imagini și resurse libere
pentru încă un container. Configurația limitează botul la 1 GiB RAM și 1,5 CPU;
lasă resurse pentru OS și proiectul existent. Nu mări concurența fără măsurători.

**Dacă Docker/Compose lipsește sau apare o eroare, oprește-te aici și trimite
rezultatele de mai sus.** Instalarea depinde de distribuție și de configurația
celuilalt proiect. Nu dezinstala Docker/containerd de pe un server folosit deja.
[Instalare oficială Ubuntu](https://docs.docker.com/engine/install/ubuntu/).

Nu schimba firewall-ul proiectului existent. Botul folosește long polling și nu
publică porturi; are nevoie de ieșire DNS/HTTPS spre Telegram, surse și GHCR.

```bash
exit
```

## 2. Publică fișierele în repository

Pe **Mac**, din proiect:

```bash
cd /Users/user/Desktop/Projects/Dowland-TT
git branch --show-current
git status --short
git ls-files .env
```

Ramura trebuie să fie `main`. Ultima comandă trebuie să nu afișeze nimic.
`.env` este ignorat de Git și exclus din imagine; nu folosi `git add -f .env`.
Nu publica tokenuri, chei sau backupuri. Dacă un token a ajuns vreodată într-un
commit, revocă-l în BotFather; ștergerea din ultimul commit nu îl elimină din istoric.

După revizuirea modificărilor:

```bash
git add .github/workflows/publish.yml compose.deploy.yml deploy tests/test_deploy_update.py DEPLOY_AUTO.md DEPLOY_ORACLE.md README.md docs/superpowers/plans/2026-11-14-oracle-auto-update.md
git commit -m "ci: publish bot image and prepare isolated Oracle updates"
git push origin main
```

Deschide [Actions](https://github.com/iMatryx/TT-Dowland/actions) → **Test and publish bot**.
Așteaptă să fie verzi ambele joburi: `test` și `publish`. Dacă Actions este dezactivat,
activează-l din repository. Politicile repository-ului trebuie să permită publicarea
de packages. Dacă un job este roșu, deschide pasul eșuat înainte să continui.
Nu există încă deployment: în acest moment se construiește doar imaginea.

Workflow-ul folosește runner GitHub găzduit. Verifică limitele Actions/Packages ale
contului; pentru a evita cheltuieli, nu activa consum plătit peste cotele incluse.
Nu este nevoie de abonament Docker Hub sau de un serviciu nou plătit.

## 3. Trimite numai fișierele de instalare

Pe **Mac**, în același terminal cu variabilele OCI:

```bash
ssh -i "$OCI_KEY" "$OCI_USER@$OCI_HOST" 'mkdir -p ~/tt-dowland-install && chmod 700 ~/tt-dowland-install'
scp -i "$OCI_KEY" compose.deploy.yml .env.example "${OCI_USER}@${OCI_HOST}:tt-dowland-install/"
scp -i "$OCI_KEY" -r deploy "${OCI_USER}@${OCI_HOST}:tt-dowland-install/"
ssh -i "$OCI_KEY" "$OCI_USER@$OCI_HOST"
```

De aici, comenzile sunt pe **server**:

```bash
sudo install -d -m 755 /opt/tt-dowland
sudo install -m 644 ~/tt-dowland-install/compose.deploy.yml /opt/tt-dowland/compose.deploy.yml
sudo install -m 755 ~/tt-dowland-install/deploy/update.sh /usr/local/sbin/tt-dowland-update
sudo install -m 644 ~/tt-dowland-install/deploy/tt-dowland-update.service /etc/systemd/system/
sudo install -m 644 ~/tt-dowland-install/deploy/tt-dowland-update.timer /etc/systemd/system/
sudo test -f /opt/tt-dowland/.env || sudo install -m 600 ~/tt-dowland-install/.env.example /opt/tt-dowland/.env
sudo chmod 600 /opt/tt-dowland/.env
sudo nano /opt/tt-dowland/.env
```

Fișierele instalate sunt deținute de root. În editor completează **doar pe server**:

```dotenv
BOT_TOKEN=TOKENUL_TAU_DIN_BOTFATHER
ADMIN_USER_IDS=ID_UL_TAU_NUMERIC_TELEGRAM
```

Păstrează celelalte valori implicite. Compose activează separat restartul controlat.
Salvezi în nano cu `Ctrl+O`, `Enter`, apoi ieși cu `Ctrl+X`.
Tokenul rămâne în fișierul protejat de pe server; administratorii serverului și
persoanele cu acces root/Docker îl pot accesa. Nu partaja ieșirea `docker inspect`
sau `docker compose config` fără `--quiet`.

## 4. Permite descărcarea imaginii

Pachetul GHCR poate fi privat chiar dacă repository-ul este public. Pentru un
pachet **privat**, creează în GitHub → Settings → Developer settings → Personal
access tokens → Tokens (classic), un token cu **read:packages**, fără drepturi de
scriere. Contul trebuie să aibă acces la pachet. Alege o expirare și reînnoiește-l
înainte de expirare. Pe server:

```bash
sudo docker login ghcr.io -u iMatryx
```

La `Password` lipești tokenul GitHub, **nu tokenul Telegram**. Nu este afișat în
terminal. Docker păstrează credențialele pentru root pe server; fără un credential
helper nu sunt criptate. Protejează contul root și fișierul `/root/.docker/config.json`.

Dacă pachetul este deja public, login-ul nu este necesar. Nu face repository-ul
sau imaginea publice doar pentru instalare dacă vrei să rămână private.
[Autentificare GHCR](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

## 5. Verifică și pornește o singură instanță

Oprește orice copie locală/veche a **aceluiași bot**, cu același token, înainte de
pornirea pe server. Nu opri proiectul diferit care rulează deja pe Oracle.

```bash
cd /opt/tt-dowland
sudo docker compose -p tt-dowland -f compose.deploy.yml config --quiet
sudo docker compose -p tt-dowland -f compose.deploy.yml pull bot
sudo docker compose -p tt-dowland -f compose.deploy.yml run --rm --no-deps -e BOT_TOKEN= bot python -m bot.main --check
sudo docker compose -p tt-dowland -f compose.deploy.yml run --rm --no-deps -e BOT_TOKEN= bot ffprobe -version
```

Acestea sunt verificări offline ale aplicației și utilitarelor după descărcarea
imaginii. Dacă trec, pornești botul:

```bash
sudo docker compose -p tt-dowland -f compose.deploy.yml up -d --no-deps bot
sudo docker compose -p tt-dowland -f compose.deploy.yml ps --all
sudo docker compose -p tt-dowland -f compose.deploy.yml logs --tail=50 bot
```

Verifică apoi manual în Telegram `/start`, alegerea limbii și un link public.
Acest test verifică tokenul și conexiunile reale. Noi nu l-am executat.

## 6. Activează actualizarea automată

După ce prima pornire funcționează:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tt-dowland-update.timer
sudo systemctl start tt-dowland-update.service
sudo systemctl list-timers tt-dowland-update.timer
sudo journalctl -u tt-dowland-update.service -n 30 --no-pager
```

Din acest moment: modifici codul → commit → push pe `main` → trec testele →
se publică imaginea → serverul o preia. Nu trebuie reboot Oracle. Utilizatorii
primesc versiunea nouă după recrearea containerului. Există o scurtă întrerupere;
cererile în curs pot fi anulate, iar linkurile trebuie retrimise. Limba salvată și
cache-ul SQLite rămân în volume. Nu există actualizare fără nicio întrerupere.

Updaterul nu recreează containerul dacă imaginea este aceeași. O descărcare sau
verificare offline eșuată lasă botul existent în funcțiune. Dacă noul proces cade
în primele aproximativ 20 secunde, încearcă revenirea la imaginea precedentă și
nu reîncearcă acea imagine până la un nou build. Verificarea procesului nu dovedește
conectivitate Telegram, iar rollback-ul nu restaurează automat baza de date.
Modificările viitoare de schemă necesită backup și migrare compatibilă.

Actualizarea automată se aplică imaginii aplicației. Modificările de `.env`,
Compose, updater și timer se aplică manual, repetând transferul/instalarea
fișierelor necesare. Nu suprascrie `.env` cu exemplul la actualizări.

## 7. Comenzi de administrare și revenire

Pe server, loguri și consum:

```bash
cd /opt/tt-dowland
sudo docker compose -p tt-dowland -f compose.deploy.yml logs --tail=100 -f bot
# Ctrl+C oprește afișarea logurilor, nu botul.
sudo docker stats --no-stream
df -h /
sudo journalctl -u tt-dowland-update.service -n 50 --no-pager
```

Oprire intenționată, inclusiv actualizările:

```bash
sudo systemctl disable --now tt-dowland-update.timer
sudo systemctl stop tt-dowland-update.service
sudo docker compose -p tt-dowland -f compose.deploy.yml stop bot
```

Pornire din nou:

```bash
sudo docker compose -p tt-dowland -f compose.deploy.yml start bot
sudo systemctl enable --now tt-dowland-update.timer
```

Pentru rollback manual, oprește mai întâi timerul/serviciul cum este descris mai sus,
verifică compatibilitatea bazei și înlocuiește `COMMIT_COMPLET` cu commitul bun:

```bash
sudo env BOT_IMAGE=ghcr.io/imatryx/tt-dowland:sha-COMMIT_COMPLET docker compose -p tt-dowland -f /opt/tt-dowland/compose.deploy.yml up -d --no-deps bot
```

Păstrează timerul oprit până când versiunea corectată este publicată pe `main`,
altfel va instala din nou imaginea `main`. După o actualizare există și tag-ul local
`ghcr.io/imatryx/tt-dowland:previous-local` pentru imaginea anterioară.

## 8. Date, backup, spațiu și probleme frecvente

Datele sunt în volumele `tt-dowland_cache-data` și `tt-dowland_download-work`,
separate de celălalt proiect. Nu folosi `down -v`, `docker system prune` sau
`docker volume prune` ca întreținere obișnuită pe acest server.

Pentru backup SQLite consistent, cât timp botul rulează, folosește un director
privat în home-ul utilizatorului SSH (nu în `/opt`, unde numai root poate scrie):

```bash
mkdir -p "$HOME/tt-dowland-backups"
chmod 700 "$HOME/tt-dowland-backups"
sudo docker compose -p tt-dowland -f /opt/tt-dowland/compose.deploy.yml exec -T bot python - <<'PY_BACKUP'
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
backup_path="$HOME/tt-dowland-backups/cache-${backup_stamp}.sqlite3"
sudo docker compose -p tt-dowland -f /opt/tt-dowland/compose.deploy.yml cp bot:/app/data/cache.backup.sqlite3 "$backup_path"
sudo chown "$(id -u):$(id -g)" "$backup_path"
chmod 600 "$backup_path"
```

Nu executa simultan două backupuri. Verifică integritatea copiei și păstreaz-o
împreună cu un backup privat al `.env` în afara VM. Procedura de restaurare este în
[DEPLOY_ORACLE.md, secțiunea 7](DEPLOY_ORACLE.md#7-backup-și-restaurare).
Updaterul nu face backup automat; pentru schimbări de schemă, dezactivează timerul
înainte de push.

Imaginile vechi rămân disponibile pentru revenire și ocupă spațiu; verifică
periodic `sudo docker system df`. Elimină manual numai imagini vechi ale
`ghcr.io/imatryx/tt-dowland`, după ce ai păstrat versiunea curentă și una pentru
rollback. Nu șterge imagini sau volume ale celuilalt proiect. În GHCR, versiunile
cu tag `sha-...` ocupă de asemenea stocare: păstrează doar istoricul necesar.

- `unauthorized` la pull: verifică login-ul **cu sudo**, accesul și expirarea PAT.
- `no matching manifest`: verifică arhitectura și jobul multiarchitecture `publish`.
- Conflict Telegram/getUpdates: alt proces folosește același token.
- Botul nu răspunde: verifică logurile aplicației, tokenul, DNS/HTTPS și resursele.
- Actualizarea nu apare: verifică jobul `publish`, timerul și jurnalul updaterului.
- Restart dezactivat: verifică `ADMIN_USER_IDS` în `.env`; acest Compose activează
  `RESTART_ENABLED` și politica Docker de repornire, fără reboot de VM.

Nu trimite în chat conținutul `.env`, cheia SSH, PAT sau ieșiri care conțin secrete.

Referințe: [publicare prin GitHub Actions](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images),
[recrearea unui serviciu Compose](https://docs.docker.com/reference/cli/docker/compose/up/).
