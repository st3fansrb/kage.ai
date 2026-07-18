# Fișă de interviu — WP-PG: migrarea stării partajate + telemetriei pe PostgreSQL

> Format §8 v2: citește fișa (~10 min), răspunde la întrebările adversariale FĂRĂ să te
> uiți la răspunsuri, apoi verifică-te. Concepte de interviu: data modeling, tranzacții,
> concurență cross-proces, migrare zero-loss.

## Ce e subsistemul

Coordonarea cross-proces mergea prin fișiere cu lock (`status.json.lock`,
`scheduled_tasks.json.lock`) iar telemetria stătea într-un SQLite deschis cu
`check_same_thread=False` — funcțional, dar fragil la scriitori concurenți și inapt
pentru query-uri de raportare. WP-PG mută în **PostgreSQL 16 nativ** (brew, ~50MB RSS)
7 tabele: `usage`, `runs`/`run_events`, `missions`/`mission_wps`, `job_runs`,
`scheduled_tasks`, `status`. **Rămân în SQLite:** chat history (`messages`) + tabelele
single-proces (idempotency, approvals, agent_sessions, jobs) + ChromaDB — migrarea lor
nu stinge nicio durere.

Piese: `pg_store.py` (conexiune + retry + DDL + helpers psycopg, SQL de mână, fără ORM) ·
migrare cutover idempotentă la startup (`_migrate_state_to_pg`) · `status.json` devine
**view derivat** pentru widget (sursa de adevăr: PG) · `pg_dump` în backupul nocturn ·
Postgres în lanțul `start_all.sh`.

## Decizia și DE CE

- **O conexiune sync + RLock, autocommit** — consistent cu patternul sqlite3 înlocuit;
  fiecare operație e o tranzacție scurtă. Pool async DOAR dacă apar blocaje măsurate
  (nu s-au măsurat; single-user). Complexitatea nefolosită e datorie, nu asigurare.
- **Tranzacții explicite doar unde atomicitatea contează:** insertul misiune + WP-urile
  ei (un cititor concurent nu vede niciodată misiunea fără WP-uri) și migrarea (vezi jos).
- **Timestamps ca TEXT ISO-8601, nu TIMESTAMPTZ** — byte-compatibile cu datele migrate
  din SQLite; ISO-8601 sortează lexicografic == cronologic, deci toate filtrările
  `ts >= X AND ts < Y` rămân corecte fără nicio conversie la 41 de situri de apel.
  Tipizarea strictă se face la WP-ETL, în staging — pattern real de migrare: întâi muți
  storage-ul cu risc minim, apoi strângi tipurile.
- **Fără FOREIGN KEY `run_events → runs`** — telemetria e best-effort pe hot path
  (o scriere de ledger nu are voie să pice chat-ul); un FK transformă un orfan într-o
  eroare de scriere. Integritatea se validează în ETL, nu la ingest.
- **Cutover idempotent cu verificare în tranzacție:** per tabel — dacă PG are deja
  rânduri → skip; altfel copiază tot și numără ÎN aceeași tranzacție; mismatch →
  excepție → rollback → nimic parțial. Sursele (SQLite/JSON) nu se șterg — rămân arhive.
- **Retry cu deadline la startup + reconectare leneșă per operație** — launchd nu
  garantează ordinea de pornire; orchestratorul pornit înaintea Postgres așteaptă
  (default 30s), apoi continuă DEGRADAT și se reconectează la prima operație reușită.
- **`status.json` = view derivat, scris atomic (tmp + `os.replace`), fără lock** —
  widget-ul de menubar (venv separat) nu primește dependență de Postgres; a rămas UN
  singur scriitor, iar rename-ul POSIX garantează cititorului un JSON complet.

## Alternative respinse

- **ORM (SQLAlchemy):** ~7 tabele, query-uri simple — SQL-ul explicit E valoarea
  (și de interviu, și de debugging); un ORM ar ascunde exact ce vrem să arătăm.
- **Postgres în Docker:** un VM rezident de 3–4GB contrazice regula de RAM (WP-G2);
  nativ = ~50MB.
- **Migrarea a tot (messages + ChromaDB):** zero durere stinsă, risc gratuit pe cache-ul
  semantic. Migrezi ce doare, nu ce există.
- **Merge/upsert la cutover în loc de skip-dacă-nenul:** mai „complet", dar imposibil de
  verificat simplu (ce înseamnă „identic" pentru rânduri fără chei naturale?) și inutil:
  după cutover codul nou nu mai scrie în SQLite, deci nu apar date noi de re-migrat.
- **Păstrarea file-lock-urilor în paralel (dual-write tranzitoriu):** două surse de
  adevăr = clasa de buguri pe care WP-ul o elimină; singura excepție e status.json,
  păstrat explicit ca VIEW (unidirecțional, nu sursă).

## Trade-off-uri acceptate

- **TEXT timestamps** → funcțiile de dată din PG cer cast (`::timestamp`) — plătit la
  WP-ETL, unde oricum se construiește staging tipizat.
- **O conexiune serializată** → operațiile DB se fac pe rând; la trafic single-user e
  irelevant, iar pragul de upgrade (pool) e definit: blocaje *măsurate*, nu bănuite.
- **Fără FK** → orfani posibili în `run_events`; detectabili trivial în ETL.
- **Degradarea fără PG** → telemetria se pierde pe durata căderii (best-effort, ca
  înainte), iar misiunile/taskurile programate raportează explicit „DB indisponibil" —
  vizibil, nu silențios (notificare Telegram la boot degradat).

## Întrebări adversariale (răspunde întâi, verifică după)

1. De ce TEXT și nu TIMESTAMPTZ pentru `ts`/`created_at`? În ce condiții se strică
   sortarea lexicografică a ISO-8601 și de ce nu ne lovesc aici?
2. De ce n-are `run_events` FOREIGN KEY spre `runs`? Când ai adăuga totuși unul?
3. Verificarea numărului de rânduri la migrare rulează ÎN tranzacția de copiere.
   Ce se poate întâmpla dacă o muți DUPĂ commit? Și ce garantează rollback-ul la
   re-rularea migrării?
4. Cutover-ul face „skip dacă tabelul PG are rânduri". Construiește scenariul în care
   asta PIERDE date și explică de ce fereastra aia nu există în practică aici.
5. `status.json` se scrie acum fără lock, cu tmp + `os.replace`. De ce e corect, și ce
   ipoteză trebuie să rămână adevărată ca să rămână corect?
6. O conexiune + RLock: două threaduri scriu simultan telemetrie. Ce se întâmplă
   concret? La ce simptom măsurabil ai trece pe pool?

---

## Răspunsuri (self-check — nu citi înainte să răspunzi)

1. ISO-8601 cu lățime fixă sortează lexicografic == cronologic DOAR dacă formatul e
   omogen: aceeași precizie? nu chiar — `datetime.now().isoformat()` variază pe
   microsecunde, dar prefixul an→secundă e fix, deci ordinea rămâne corectă; se strică
   la timezone-uri mixte (`+02:00` vs naive) sau formate mixte (spațiu vs `T`).
   Aici toate valorile vin din același `datetime.now().isoformat()` naive local —
   omogen prin construcție. TIMESTAMPTZ ar fi cerut cast/conversie la toate cele ~41
   de situri în ziua cutover-ului = risc maxim exact în pasul cu cel mai mare risc.
2. Pentru că scrierile de ledger sunt best-effort pe hot path: dacă insertul run-ului
   a eșuat silențios (by design), evenimentele lui ar începe să arunce erori de FK —
   adică telemetria ar produce exact zgomotul pe care trebuie să nu-l producă. Plus
   orfanii istorici din SQLite ar fi blocat migrarea „zero pierdere". FK-ul devine
   corect când ledger-ul devine sursă de adevăr pentru decizii (nu doar observabilitate)
   — atunci scrierile lui nu mai au voie să fie best-effort deloc.
3. După commit: crash-ul ÎNTRE copiere și verificare lasă datele copiate dar
   neverificate, iar la următorul boot „tabelul are rânduri → skip" pecetluiește o
   migrare potențial parțială. În tranzacție: mismatch sau crash → rollback → tabelul
   PG rămâne gol → următoarea rulare o ia curat de la zero. Idempotența vine din
   perechea „all-or-nothing + skip-dacă-nenul".
4. Pierzi date dacă, DUPĂ un cutover reușit, ceva mai scrie în SQLite (rândurile noi
   nu se vor mai migra — skip). Fereastra ar exista doar rulând cod vechi pe același
   `cache_db/` după ce codul nou a migrat — adică un downgrade fără restore. În
   operarea normală (restart cu cod nou, sursele devin arhive) fereastra nu există;
   riscul rezidual e documentat, nu ascuns.
5. `os.replace` e atomic pe POSIX: cititorul (widget-ul) vede fie fișierul vechi
   complet, fie pe cel nou complet, niciodată un JSON pe jumătate scris. Ipoteza care
   trebuie să rămână adevărată: UN SINGUR scriitor (orchestratorul). Dacă apare al
   doilea proces scriitor, ultimul câștigă tăcut — atunci lock-ul (sau scrierea doar
   în PG) redevine necesar.
6. RLock-ul serializează: al doilea thread așteaptă câteva ms — corect, doar potențial
   lent. Bottleneck real abia când timpul de așteptare pe lock devine măsurabil în
   latența cererilor (ex. p95 pe `/api/stats` crește cu zeci de ms sub agenți paraleli).
   Atunci: `psycopg_pool.ConnectionPool` sync — NU async — fiindcă problema ar fi
   contenția pe conexiune, nu blocking-ul event loop-ului.
