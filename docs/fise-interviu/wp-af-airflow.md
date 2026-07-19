# Fișă de interviu — WP-AF: Airflow pentru joburile batch

> Format §8 v2: citește fișa (~10 min), răspunde la întrebările adversariale FĂRĂ să te
> uiți la răspunsuri, apoi verifică-te. Concepte de interviu: orchestrare batch, retries/
> backfill, design de scheduler, separarea batch vs safety-critical.

## Ce e subsistemul

Cron-urile batch trăiau ÎN procesul orchestratorului (APScheduler) — mureau odată cu el
(cazul din 09.07: scanul de joburi de 19:00 tăiat de un restart, pierdut tăcut până a doua
zi, peticit atunci cu un watchdog). WP-AF externalizează pe **Airflow 2.10 standalone**
(LocalExecutor, metadata în Postgres-ul WP-PG, NU SQLite-ul default) cele patru batch-uri
NON-safety-critical: agregarea nightly ETL, backupul, scanul de joburi, calibrarea
săptămânală de trading. Fiecare e un DAG (`airflow/dags/kage_batch.py`) care apelează
**endpoint-ul existent** (`POST /admin/etl`, `/admin/backup`, `/jobs/scan`,
`/admin/trading/calibration`) — Airflow orchestrează (program, retries, backfill, istoric
în UI), logica rămâne în orchestrator.

Piese: `.airflow-venv` izolat (nu spargem dependențele orchestratorului) · DAG-uri +
`kage_common.py` (helper stdlib: apel autentificat cu token din config + alertă Telegram
la eșec) · flag `airflow_batches` în orchestrator care NU mai înregistrează cele 4 cron-uri
în APScheduler când Airflow le deține · pornit din `start_all.sh` (UI :8080).

## Decizia și DE CE

- **Airflow apelează endpoint-uri, nu reimplementează** — DAG-urile-s glue de ~5 linii;
  toată logica (ETL, backup, scan) rămâne în orchestrator, testată acolo. Airflow aduce
  EXCLUSIV orchestrarea. Dacă aș fi mutat logica în DAG-uri, aș fi avut două surse de adevăr.
- **Doar batch-urile NON-safety-critical** — killswitch-ul de trading (`*/5`) și heartbeat-ul
  WP12 RĂMÂN în APScheduler, în proces. Un scheduler extern adaugă un punct de eșec (Airflow
  căzut) exact peste mecanismele fail-closed care trebuie să ruleze cel mai sigur. Un
  killswitch care depinde de Airflow ca să se declanșeze e un killswitch stricat.
- **Flag `airflow_batches`, nu ștergere pură** — spec-ul cere „nu rula de două ori". Un flag
  face asta ȘI evită un gol de acoperire: cu Airflow oprit (`false`), orchestratorul rulează
  batch-urile ca înainte. Sursă unică prin construcție: fiecare batch e în EXACT un scheduler,
  ales de flag. Ștergerea pură ar fi lăsat sistemul fără batch-uri dacă Airflow nu pornește.
- **Metadata în Postgres, nu SQLite** — SQLite-ul default al Airflow serializează pe un
  singur writer și nu suportă LocalExecutor (task-uri paralele). Refolosim clusterul WP-PG
  (deja pornit, deja backup-uit) — o bază `airflow` separată, izolată de `kage`.
- **Venv izolat `.airflow-venv`** — Airflow pinează agresiv (Flask 2.2, SQLAlchemy 1.4,
  pydantic vechi via providers) și ar intra în conflict cu stack-ul orchestratorului
  (FastAPI/pydantic 2). Același pattern ca `.jobs-venv`/`.trading-venv`.
- **`catchup=False`** — la prima pornire NU vrem să ruleze retroactiv toate zilele dintre
  `start_date` și azi. Backfill-ul e o acțiune deliberată (`airflow dags backfill`), nu un
  efect secundar al pornirii.
- **Idempotența e a endpoint-ului, nu a DAG-ului** — DAG-ul ETL cheamă `action=day` pe `{{ ds }}`;
  re-rularea aceleiași zile e sigură fiindcă agregarea face delete-and-rewrite (WP-ETL). Airflow
  poate reîncerca liniștit.
- **Token-ul de API + cel de Telegram se citesc din `kage_config.json` la runtime** — NU ajung
  în metadata-baza Airflow (unde ar fi vizibile în UI/DB). Alerta de eșec postează DIRECT la
  Telegram, ca să ajungă chiar dacă orchestratorul e căzut.

## Alternative respinse

- **Migrarea TUTUROR cron-urilor** (inclusiv killswitch/heartbeat) — ar fi pus fail-closed-ul
  la mila unui serviciu extern. Batch ≠ safety-critical; doar primul pleacă.
- **Airflow în Docker** — un VM rezident contrazice regula de RAM (WP-G2); nativ în venv.
  Fallback documentat dacă footprint-ul standalone depășește ținta: Colima pornit/oprit în
  jurul ferestrei batch.
- **Celery/Kubernetes executor** — single-user, o mașină: LocalExecutor e exact potrivit;
  Celery ar cere un broker (Redis/Rabbit) rezident degeaba.
- **DAG-uri care reimplementează logica** — dublă mentenanță + pierzi testele existente ale
  endpoint-urilor.

## Trade-off-uri acceptate

- **Endpoint-uri fire-and-forget** (`/jobs/scan`, trading `nightly`) → succesul task-ului
  Airflow înseamnă „declanșat", nu „terminat". Pentru scan e ok (digest pe Telegram la final);
  un „wait for completion" real ar cere un endpoint de status pe care nu-l are încă.
- **Airflow standalone = mai multe procese** (scheduler + webserver + triggerer) → footprint
  mai mare decât un cron. Acceptat pentru istoric/retries/UI; măsurat la pornire, cu fallback
  Colima dacă depășește.
- **Schedule-urile-s hardcodate în DAG-uri**, nu citite din config ca în APScheduler → Airflow
  devine sursa de adevăr pentru program; un DAG e cod, se schimbă în cod.

## Întrebări adversariale (răspunde întâi, verifică după)

1. De ce killswitch-ul de trading NU e mutat în Airflow, deși e „un cron"?
2. Flag-ul `airflow_batches` pe `false` cu Airflow totuși pornit — ce se întâmplă? Dar pe
   `true` cu Airflow oprit? Care din cele două e periculos și de ce l-am ales pe celălalt ca default?
3. DAG-ul ETL rulează cu `catchup=False`. Dacă mașina a fost oprită 3 zile, cum recuperez
   agregarea zilelor lipsă — și de ce nu vreau ca pornirea să o facă automat?
4. Un DAG raportează SUCCESS dar munca reală (scanul) încă rulează. Cum e posibil și când
   contează?
5. De ce metadata Airflow în Postgres și nu în SQLite-ul lui default? Ce se strică la
   LocalExecutor cu SQLite?
6. Token-ul de API nu e într-o Connection/Variable Airflow, ci citit din fișier la runtime.
   Ce câștig de securitate și ce cost operațional are alegerea asta?

---

## Răspunsuri (self-check — nu citi înainte să răspunzi)

1. Fiindcă killswitch-ul e safety-critical: rolul lui e să oprească tradingul când ceva merge
   prost. Dacă depinde de Airflow ca să ruleze, atunci o cădere a Airflow (exact genul de
   „ceva merge prost") îi dezactivează tăcut garda. Un mecanism fail-closed trebuie să ruleze
   în cel mai simplu context posibil, în proces, fără dependențe externe. Batch-urile (ETL,
   backup, scan) n-au proprietatea asta — o rulare pierdută se recuperează la următoarea.
2. `false` + Airflow pornit → dublă rulare (ambele scheduler-e trag batch-ul) — exact ce
   evită flag-ul. `true` + Airflow oprit → batch-urile nu rulează nicăieri (gol de acoperire)
   până pornește Airflow. Al doilea e periculos (muncă pierdută tăcut). De aceea DEFAULT-ul e
   `false`: o instalare fără Airflow rulează batch-urile în proces, sigur; `true` se activează
   DELIBERAT, împreună cu pornirea Airflow în `start_all.sh` (aceeași condiție care le pornește).
3. `airflow dags backfill kage_etl_daily -s <start> -e <end>` rulează explicit zilele lipsă;
   idempotența ETL (delete-and-rewrite pe zi) le face sigure. Nu vreau catchup automat la
   pornire fiindcă un `start_date` vechi + o oprire lungă ar declanșa zeci de rulări deodată
   la boot (thundering herd) — un efect secundar surprinzător al simplei porniri. Backfill-ul
   e o decizie, nu un accident.
4. Endpoint-ul `/jobs/scan` e fire-and-forget: pornește scanul într-un task de fundal și
   răspunde imediat 200. DAG-ul vede 200 → SUCCESS, deși scanul rulează încă. Contează dacă un
   DAG downstream ar depinde de rezultatul scanului — atunci aș avea nevoie de un endpoint de
   status + un sensor/poll. Pentru scanul care doar trimite un digest pe Telegram, nu contează.
5. SQLite-ul Airflow suportă doar SequentialExecutor (un task odată) — LocalExecutor rulează
   task-uri în paralel, iar SQLite serializează scrierile pe un singur writer și dă „database
   is locked" sub concurență. Postgres suportă scriitori concurenți, deci LocalExecutor merge.
   În plus refolosesc clusterul WP-PG deja backup-uit, într-o bază separată.
6. Câștig: secretele NU intră în metadata-baza Airflow (unde ar fi vizibile în UI-ul de
   Connections/Variables și în backup-urile bazei). Cost: DAG-urile presupun că rulează pe
   aceeași mașină cu `kage_config.json` (cuplare la layout-ul local) — acceptabil aici, e
   single-machine; într-un deploy distribuit aș folosi un secrets backend (Vault/GCP Secret
   Manager) prin interfața de secrets a Airflow.
