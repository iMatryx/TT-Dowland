# Corecțiile auditului local de securitate

Aceste schimbări tratează problemele de disponibilitate și bugetare a resurselor
identificate în audit. Nu modifică politica de calitate, watermark, limitele de
upload sau paralelismul descărcărilor/conversiilor.

## Retry Telegram izolat

`TelegramRetryMiddleware` păstrează cooldown-ul pentru chatul operației respinse.
Trimiterile și editările din același chat sunt serializate și respectă același
`RetryAfter`. Celelalte chaturi, `GetUpdates` și răspunsurile la callback-uri nu
mai așteaptă după cooldown-ul acelui chat. Metodele fără `chat_id` au scope-uri
independente după metoda API, nu un cooldown comun întregii sesiuni.

Fiecare operație respinsă își respectă `retry_after`. Valoarea Telegram nu spune
singură dacă limita este globală; o limită reală la nivelul botului poate produce
în continuare răspunsuri 429 în mai multe scope-uri, fiecare cu backoff propriu.
Nu se presupune că serverul garantează rate diferite pentru toate metodele.

Starea este limitată la 4096 de scope-uri; intrările inactive expiră după 300 s,
dar cooldown-urile încă valabile și lock-urile cu titulari/așteptători nu sunt
eliminate. La epuizarea capacității se refuză admiterea prin `QueueFullError`.
Timeoutul include coada, cooldown-ul și transportul. Eșecurile de rețea cu stare
incertă a livrării nu sunt retrimise automat.

## Bugetul cache-ului de analiză

`ANALYSIS_CACHE_MAX_MB=64` limitează memoria contabilizată a snapshoturilor reținute
la 64 MiB implicit. Contabilizarea folosește dimensiunile stringurilor Python după
serializare, inclusiv extinderea caracterelor escapate, plus cheile și o rezervă
pentru obiecte. Nu este o măsurătoare sau o limită a RSS-ului întregului proces.

LRU evacuează intrări când se depășește numărul permis sau bugetul în bytes.
Expirarea, invalidarea prin token și shutdown-ul eliberează contabilizarea.
O intrare mai mare decât întregul buget servește cererea curentă, dar nu este
reținută și nu evacuează intrările mici utile. Limita individuală a JSON-ului de
intrare rămâne 10 MiB. Extracțiile identice concurente sunt în continuare partajate.

Acest buget nu înlocuiește limita de memorie a containerului pentru parser-ele
media, worker-e și datele temporare din extracțiile active.

## Ieșire de proces limitată

`run_process` citește simultan stdout și stderr în blocuri și aplică un plafon
comun de 4 MiB implicit. La depășire ridică `ProcessOutputLimitError`, fără a
include conținutul diagnosticului în mesajul erorii. Limita se aplică în timpul
citirii, nu după acumularea întregii ieșiri.

La depășire, timeout sau anulare, grupul de procese este oprit și așteptat, iar
pipe-urile sunt golite fără reținerea datelor. Crearea și curățarea sunt protejate
față de anulare. Procesele sunt pornite cu argumente separate, fără shell.
Plafonul ieșirii nu limitează memoria internă a FFmpeg/yt-dlp și nu reprezintă
izolarea completă a unui parser compromis.

## Recuperarea fișierelor preview

Curățarea periodică și cea de la pornire recunosc și directoarele `.preview-`.
Opririle forțate nu mai lasă aceste directoare în afara politicii de recuperare.
Preview-urile și ZIP-urile active sunt înregistrate în `FileRegistry` pe întreaga
pregătire și trimitere; curățarea nu le șterge cât timp sunt folosite. Protecțiile
pentru fișiere active, vârstă și symlink-uri rămân aplicate.

## Verificări

Testele locale reproduc izolarea retry-ului, păstrarea cooldown-ului după timeout,
capacitatea și anularea așteptătorilor, evacuarea după bytes, extinderea JSON la
serializare, output flood pe ambele pipe-uri și recuperarea după crash. Sunt
verificate separat directoarele preview/ZIP active și curățarea după anulare.
Nu s-au făcut cereri Telegram reale, teste de încărcare pe server sau deployment.
