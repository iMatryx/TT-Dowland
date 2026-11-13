# Limbile interfeței

La primul mesaj și la fiecare `/start` (sau `start`), botul afișează un selector
cu **Română**, **Русский** și **English**. Alegerea este explicită: limba
aplicației Telegram nu decide limba botului. După alegere apare mesajul scurt de
întâmpinare și meniul în limba selectată. `/menu` deschide meniul fără să ceară
schimbarea limbii. Comanda `/language` deschide același selector ca `/start`.
Un nou `/start` sau `/language` cere din nou alegerea înaintea altor acțiuni.

Limba este salvată per ID de utilizator în tabela `user_languages` din SQLite,
independent de cache-ul media. Persistă după repornire și cu `CACHE_ENABLED=false`.
Nu se reintroduc preferințe de calitate sau watermark: selecția sursei rămâne
fixă la cea mai înaltă calitate identificată fără watermark.

Selectorul este legat de utilizator, chat și mesaj, expiră după 10 minute și se
consumă atomic o singură dată. Un nou `/start` invalidează selectorul precedent.
Apăsarea unui selector valid este exceptată de la pauza anti-spam pentru a putea
alege imediat după `/start`; validarea în SQLite precede această excepție.
Mesajele și butoanele care pornesc descărcări păstrează limitele existente.

Locale-ul este local taskului asyncio. Utilizatorii și livrările concurente nu
împart limba interfeței. Meniurile recunosc și etichetele din celelalte limbi,
astfel încât o tastatură mai veche să funcționeze după schimbarea limbii.
Descrierile comenzilor slash sunt înregistrate și pentru limba aleasă: pe chat
privat, respectiv pe membru de grup. Eșecul actualizării meniului de comenzi nu
anulează salvarea limbii sau afișarea meniului principal.

Traducerile locale sunt în `bot/locales/core.py` și `bot/locales/media.py`.
Mesajele, progresul, erorile, butoanele și informațiile media sunt traduse prin
șabloane explicite. Titlurile, autorii, codec-urile și alte valori venite de la
sursă sunt păstrate și escapate; nu se înlocuiesc cuvinte în textul utilizatorului.
Detaliile Info noi se salvează în toate cele trei limbi sub aceeași cheie
bot/chat/mesaj și se afișează în limba curentă, fără o nouă descărcare sau upload.
Mesajele deja trimise nu sunt rescrise automat. Intrările Info vechi, salvate
înaintea acestei funcții, rămân compatibile în forma în care au fost salvate.
TTL-urile, izolarea chatului și limitele de caption se păstrează.

Verificările sunt offline, cu transport Telegram simulat. Pentru a vedea noua
interfață pe instanța reală, actualizați codul, reporniți botul și trimiteți `/start`.
