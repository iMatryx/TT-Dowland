# Repornire controlată de administrator

`/restart` este disponibil doar conturilor Telegram ale căror ID-uri numerice
sunt în `ADMIN_USER_IDS`, și numai în chat privat cu botul. Nu sunt folosite
username-ul, identitatea unui mesaj redirecționat, rolul de administrator într-un
grup sau un ID trimis ca text. Lista goală refuză accesul tuturor; primul utilizator
nu devine automat administrator. Comanda nu apare în listele publice, însă
verificarea de acces se face indiferent dacă cineva o scrie manual.

În `.env` local, completați `ADMIN_USER_IDS` cu ID-ul propriu sau o listă de ID-uri
separate prin virgulă. Nu trimiteți tokenul pentru această configurare. Nu adăugați
ID-urile reale în cod, README, `.env.example`, capturi sau loguri publice.
`.env` este ignorat de Git și exclus din Docker build; exemplul public are lista
goală. Configurația ascunde/exclude lista din reprezentări și serializarea normală.
Fișierul local a primit permisiuni `0600`. Pe server, utilizatorul care pornește
Docker trebuie să poată citi fișierul; acesta nu se montează în container.
Nu folosiți `git add -f .env` și nu încărcați fișierul manual pe GitHub.

## Confirmarea

Botul cere confirmare înainte de orice oprire. Butonul are un token aleatoriu,
este legat de cont, chat și mesaj, expiră după 60 s și poate fi consumat o singură
dată. O cerere nouă invalidează confirmarea precedentă a acelui administrator.
Identitatea este verificată din nou la apăsare. „Anulează”, butoanele falsificate,
expirate sau ale altui utilizator nu solicită repornire.

Confirmarea validă poate fi apăsată imediat după comandă, fără a fi blocată de
cooldown; această excepție se acordă doar după validarea administratorului și a
tokenului. Celelalte cereri păstrează limitarea existentă. Dacă mesajul de confirmare
a opririi nu poate fi trimis/editat, nu se solicită restart.

## Cine pornește botul din nou

Botul nu execută comenzi shell, nu accesează Docker socket, nu deschide un port
HTTP de administrare și nu primește comenzi de sistem din Telegram.
După confirmare, semnalează bucla principală să oprească polling-ul, refuză cereri
noi și închide runtime-ul cu deadline-ul existent `SHUTDOWN_GRACE_SECONDS`.
Operațiile care nu se încheie în această perioadă sunt anulate, procesele media
și fișierele temporare sunt curățate, apoi sesiunea Telegram se închide.
Procesul iese cu codul **75**. Limbile și cache-ul SQLite se păstrează;
cererile/butoanele temporare și originalele locale reținute nu supraviețuiesc.

În Docker Compose, `restart: unless-stopped` există deja și repornește containerul.
Compose setează explicit `RESTART_ENABLED=true`; fără ID-uri în allowlist,
comanda este în continuare interzisă tuturor. Nu este necesar acces privilegiat,
root sau montarea socketului Docker.

Într-o instalare systemd administrată separat, utilizați `Restart=on-failure`
și un `RestartSec` potrivit (de exemplu 5 s), apoi activați explicit
`RESTART_ENABLED=true` în configurația serviciului. Nu am instalat sau modificat
un serviciu systemd în această implementare.

La rulare manuală, `RESTART_ENABLED=false` este implicit. Nu activați funcția
fără supervisor: altfel procesul se va opri, iar nimeni nu îl va porni din nou.
Schimbările configurației administratorilor necesită repornirea procesului.
Actualizarea listelor de comenzi din Telegram se face la alegerea limbii.

## Limita protecției

Această funcție restricționează repornirea botului; nu este o configurare a
securității SSH, a conturilor GitHub sau a întregului server. Publicarea codului
fără `.env` este intenționată și nu ocolește allowlist-ul. Dacă tokenul a fost
expus anterior, el trebuie revocat/regenerat prin BotFather.

Testele folosesc exclusiv transport Telegram simulat și un dispatcher fals pentru
oprire. Nu s-a pornit botul real, nu s-au trimis mesaje și nu s-a declanșat un
restart sau deployment.
