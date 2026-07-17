# Fișă de interviu — WP-ETL: pipeline de analytics + point-in-time lineage

> Format §8 v2: citește fișa (~10 min), răspunde la întrebările adversariale FĂRĂ să te
> uiți la răspunsuri, apoi verifică-te. Concepte de interviu: ETL, idempotență, backfill,
> SQL analitic, data lineage (point-in-time).

## Ce e subsistemul

Kage produce telemetrie (`runs`/`run_events`, `missions`/`mission_wps`, paper trades în
`trading.db`) dar n-avea raportare — WP10 ar fi ajuns la query-uri ad-hoc. WP-ETL adaugă
pipeline-ul clasic **raw → staging → mart**, tot în SQL peste PostgreSQL (`etl.py`):

- **raw**: tabelele WP-PG (`runs`, `missions`, `mission_wps`) + `trading.db` (SQLite,
  sursă separată → ingest multi-sursă).
- **staging** (`stg_runs`, `stg_trades`, `stg_equity`): materializare tipizată, o linie
  per rând raw, cu **lineage** (`event_time`, `available_time`, `ingested_time`, `source`).
- **mart** (`mart_daily_usage`, `mart_mission_stats`, `mart_trading_daily`): agregate pe
  zi; fiecare rând poartă `dataset_snapshot_id` (rularea care l-a produs, în `etl_snapshots`).

Livrare: job nightly la 01:30 (APScheduler; devine primul DAG real la WP-AF) · endpoint
`GET /analytics/daily` (consumat de WP10) · `POST /admin/etl` (backfill/day manual) · CLI
`python -m etl [backfill | day <d> | report N]`.

## Decizia și DE CE

- **SQL, nu pandas** — `INSERT ... SELECT ... GROUP BY` cu delete-and-rewrite pe partiția
  zilei. Datele-s mici (single-user); patternul (agregare declarativă, idempotentă, lângă
  date) e ce contează, nu tool-ul. Pandas ar muta datele în proces degeaba.
- **Procesare per ZI, nu global** — unitatea pipeline-ului e o zi. Nightly = ieri + azi;
  backfill = bucla peste zilele distincte. Idempotența devine locală și trivial de
  demonstrat: `DELETE FROM mart WHERE day=X` apoi `INSERT ... WHERE day=X`. A rula de 2×
  aceeași zi dă exact același set de rânduri (test dedicat).
- **Staging separat de raw pentru lineage** — nu ating tabelele raw (hot path, best-effort
  la scriere). Lineage-ul trăiește pe copia materializată: `event_time`=când s-a produs,
  `available_time`=când a devenit vizibil (finished_at), `ingested_time`=când a intrat în
  staging. Suficient să răspundă „ce știa sistemul la momentul X" fără versionare completă.
- **`dataset_snapshot_id` per rulare de agregare** — provenance: din orice rând de mart
  ajungi la rularea care l-a scris (kind, când, câte rânduri in/out/respinse). E varianta
  ieftină de reproducibilitate: nu versionez datele, versionez *rulările*.
- **Data-quality ca reguli de intrare, nu rapoarte post-hoc:**
  - future timestamps (`event_time > ingested_time`) → respinse la staging + numărate;
  - rânduri fără `available_time` (run neterminat) → staged, dar excluse din mart-ul zilei;
  - duplicate → absorbite de cheia primară din staging (`ON CONFLICT DO UPDATE`);
  - gap-uri de zile → `find_gaps` peste zilele prezente.
- **Idempotent la staging prin cheia raw** (`run_id`, `paper:<id>`, `eq:<id>`) — re-rularea
  restampilează măsurile mutabile, nu creează dubluri; `ingested_time` rămâne primul.
- **Multi-sursă cu degradare grațioasă** — `trading.db` (SQLite) e citit cu `sqlite3`;
  lipsa lui = 0 rânduri de trading, nu excepție. Aceeași filozofie ca restul lui Kage.

## Alternative respinse

- **T3 (point-in-time lineage) ca WP separat** — punctul lui de plecare e SQL peste schema
  deja proiectată la WP-PG; separarea ar fi dublat schema design fără beneficiu. Integrat aici.
- **Agregare incrementală „doar rândurile noi"** — mai „eficientă", dar delete-and-rewrite
  pe zi e imun la re-procesare, corecturi retroactive și late-arriving data; la volumul ăsta
  eficiența incrementală n-ar cumpăra nimic măsurabil, dar ar cumpăra bug-uri de dublă numărare.
- **Materialized views Postgres** în loc de mart-uri tabel — `REFRESH` nu-ți dă control pe
  partiția zilei, nici `dataset_snapshot_id`, nici backfill selectiv. Tabelele + INSERT
  explicit sunt exact ce vrei să poți arăta și depana.
- **FK staging → raw** — staging e o copie derivată; un FK ar lega două stratură care se
  curăță/backfill-uiesc independent.

## Trade-off-uri acceptate

- **Delete-and-rewrite** rescrie toată ziua chiar dacă s-a schimbat un rând → risipă la
  volume mari; irelevant aici, iar câștigul (idempotență trivială) merită.
- **Cast TEXT→timestamptz în SQL** (`created_at::timestamptz`) — moștenit din decizia
  „timestamps ca TEXT" de la WP-PG; corect câtă vreme valorile-s ISO-8601 omogene, naive local.
- **Staging duplică storage-ul raw** — acceptabil la volumul ăsta; e prețul lineage-ului
  fără a atinge hot path-ul.
- **Nightly rulează ieri + azi** → ziua de azi se rescrie la fiecare rulare; idempotența o
  face inofensiv, dar înseamnă că „azi" e mereu parțial până a doua zi.

## Întrebări adversariale (răspunde întâi, verifică după)

1. Ce înseamnă concret idempotența aici și prin ce mecanism SQL o obții? De ce nu s-ar
   putea baza pe `INSERT ... ON CONFLICT` la nivelul mart-ului în loc de delete-and-rewrite?
2. `available_time IS NOT NULL` filtrează mart-ul. Ce clasă de erori de raportare previne
   asta și ce se întâmplă cu un run care se termină abia a doua zi?
3. La ce folosește `dataset_snapshot_id` dacă mart-ul se poate reconstrui oricând din raw?
4. Backfill-ul procesează zilele distincte din raw. Cum se comportă pentru o zi în care
   sistemul a fost oprit complet, și de ce nu apare ca rând gol în mart?
5. `event_time > ingested_time` respinge „viitorul". De ce e asta o regulă de data-quality
   și nu doar zgomot? Dă un scenariu real care o declanșează.
6. Ce ai schimba în pipeline când Kafka intră (WP-KF) ca strat de ingest?

---

## Răspunsuri (self-check — nu citi înainte să răspunzi)

1. Idempotență = a rula agregarea de N ori pe aceeași zi produce exact același set de
   rânduri. Mecanismul: `DELETE FROM mart WHERE day=X` urmat de `INSERT ... SELECT ...
   WHERE day=X` — partiția zilei e ștearsă și reconstruită atomic (autocommit per statement,
   dar delete+insert pe aceeași conexiune serializată). `ON CONFLICT` la nivel de mart ar
   cere o cheie naturală stabilă pe grain (day×tier×model, cu NULL-uri pentru tier/model
   neclasificate) — NULL-urile strică unicitatea și upsert-ul nu șterge rândurile care nu
   mai apar în noua agregare (ex. un tier care azi n-are trafic). Delete-and-rewrite n-are
   problema asta: ce nu se reinserează, dispare.
2. Previne raportarea unui run ca „terminat/costat X" înainte să se fi terminat — altfel
   costul/durata ar intra în ziua greșită sau incomplet. Un run care se termină a doua zi:
   `available_time` (finished_at) cade în ziua 2, deci intră în mart-ul zilei 2 la
   următoarea rulare (nightly rulează și „ieri"), nu în ziua 1. `event_time` rămâne ziua 1
   pentru lineage, dar contribuția la mart urmează `available_time` — corect pentru „ce a
   fost vizibil/închis în ziua asta".
3. Reconstrucția din raw dă alt rezultat dacă raw-ul s-a schimbat între timp (corecții,
   late data) SAU dacă logica de agregare s-a schimbat. `dataset_snapshot_id` spune *care
   rulare, cu ce cod, la ce moment* a produs rândul pe care l-a văzut un consumator (WP10,
   un raport). Fără el, „de ce arăta dashboard-ul altceva marțea trecută?" e nedebugabil.
   Versionez rulările, nu datele — reproducibilitate ieftină.
4. O zi fără nicio activitate nu apare în `distinct_days` (nu există rânduri raw cu acea
   zi), deci `run_day` nu se cheamă pentru ea și nu se scrie rând. Mart-ul de misiuni are
   în plus un guard `WHERE (...count...) > 0` ca să nu insereze un rând all-zero nici când
   ziua e procesată explicit dar n-are activitate. Gap-urile reale (zile lipsă între min și
   max) le raportează `find_gaps` — absența e semnalată, nu umplută cu zerouri false.
5. E data-quality fiindcă un `event_time` în viitor înseamnă că ceva e stricat la sursă:
   ceas de mașină greșit, timezone prost aplicat, sau un rând injectat/corupt. Dacă l-ai
   lăsa să intre, ar polua agregatele viitoare (o zi din 2099 cu cost real) și ar strica
   „ce știa sistemul la momentul X". Scenariu real: un test sau un backfill manual scrie un
   `created_at` cu an greșit; sau un container cu ceas nesincronizat. Regula îl prinde la
   graniță și îl numără (`rejected` în snapshot), nu îl ascunde.
6. Kafka devine stratul de ingest: producer-i în orchestrator/gateway emit evenimente
   (decizie de rutare, lifecycle de agent, paper trade), un consumer scrie în tabelele raw
   din Postgres. Pipeline-ul de aici NU se schimbă — `stg_*`/`mart_*` rămân, doar sursa
   `runs`/`run_events` e populată de consumer în loc de scrierile inline. Adică Kafka intră
   ÎNAINTE de raw, ca transport, nu ca înlocuitor al ETL-ului (exact framing-ul din decizia
   WP-KF: „transportul pipeline-ului, nu un gadget paralel").
