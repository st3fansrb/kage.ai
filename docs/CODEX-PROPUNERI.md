# Kage — registrul propunerilor Codex

> Toate ideile din acest fișier sunt **propuneri generate de Codex**, nu implementări și nu decizii aprobate automat.
>
> Data primei consolidări: **13.07.2026**.

> **Verdict de reconciliere (14.07.2026, Stefan) — vezi `KAGE-HANDOFF.md` §4 pentru raționament complet:**
> - `accepted`: **R0** (nou WP în handoff), **T3** (integrat în WP-ETL), **G5** (integrat în WP10), **G1** (acceptat în formă minimă — G1-minim, nou WP în handoff), **G2/G6** (deja aliniate cu deciziile existente).
> - `rejected`: condiționarea WP13 de KageBench — Advisorul rămâne pe Qwen local acum, nu „TBD după benchmark".
> - `proposed` (neschimbat, rămân aici): T1, T2, T4–T8, G3, G4. Planul integrat P0–P4 de mai jos NU înlocuiește ordinea din handoff (ignoră WP-NL/WP-SD/WP-AL, decise separat) — rămâne doar ca sursă de idei pentru itemii încă `proposed`.

## Context și obiectiv

Stefan a introdus ca obiectiv explicit pregătirea pentru un **internship AI la Revolut în vara lui 2027**. Repository-ul nu conținea anterior o notă dedicată acestui obiectiv; îl tratăm aici ca obiectiv de planificare introdus de Stefan în conversație.

Propunerea Codex este ca Kage să demonstreze nu doar integrare de modele, ci și competențe pe care Stefan le poate explica la interviu: evaluare reproductibilă, sisteme agentice, reliability, cost control, observabilitate, security, human-in-the-loop și ML/quant implementat personal.

Principiul de prioritizare propus de Codex: **mai puține feature-uri orizontale, mai multă dovadă reproductibilă și un rezultat utilizat real**.

Maparea detaliată a JD-ului și a gap-urilor actuale este în [REVOLUT-INTERNSHIP-ALIGNMENT.md](REVOLUT-INTERNSHIP-ALIGNMENT.md). Concluzia propusă de Codex este să prezinte Kage mai întâi ca platformă Python/data engineering pentru API-uri, pipelines și sisteme distribuite; agenții și tradingul sunt workload-uri demonstrative peste această platformă.

## Propuneri generale

### G1 — KageBench: evaluare și replay pentru agenți

Construiește un benchmark intern din taskuri reale Kage, cu stare inițială, tools permise, criterii de acceptare și artefacte așteptate. Același task poate fi rulat pe Claude, Codex și Qwen în sandbox.

Metrici: succes complet/parțial, cost EUR, latență, turns, retry-uri, aprobări, tool calls inutile, încălcări de policy și regresie față de versiunea anterioară. Devine regression gate pentru cod și router.

**Valoare CV:** eval-driven development, model selection și trade-off-uri quality/cost/reliability.

### G2 — Executor interface și governance comun

Claude, Codex și modelele locale trebuie să treacă prin aceeași interfață și să emită aceleași evenimente: tool call, tool result, cost, approval, artifact și failure. Sandbox-ul, policy-ul, bugetul și auditul stau deasupra executorului.

### G3 — Agent security benchmark

Adaugă teste de prompt injection, tool poisoning, path traversal, secret exfiltration, symlink escape și acțiuni ireversibile fără aprobare. Raportul trebuie să includă attack success rate, policy violations, false positives și regresii.

### G4 — Workflow vertical cu utilizatori reali

Propunere de validat în manufacturing/automotive: documente de ofertare, comparații de furnizori, BOM, stoc/preț/termen și draft de ofertă sau comandă, cu aprobare umană și audit. Începe cu upload PDF/Excel și export, nu cu integrare ERP completă. Măsoară timpul înainte/după, corecțiile, erorile detectate și utilizarea săptămânală.

### G5 — Observabilitate orientată pe rezultate

Mission Control să arate rata de succes pe tipuri de task, costul mediu, approval rate, retry rate, failure taxonomy și degradarea pe versiuni de model, nu numai transcriptul.

### G6 — Priorități de amânat

Nu aș prioritiza încă wake word, încă un model doar pentru diversitate, swarm generic, A2A, RAG foarte larg sau mai multe piețe de trading înainte de evals și un workflow real.

## Propuneri trading / Quant Lab

### T1 — `HypothesisSpec` și Strategy DSL

Actorul propune, Criticul aprobă, iar ipoteza aprobată așteaptă acum implementare manuală. Propunerea este un DSL limitat la primitive auditate: funding percentile, EMA/trend, volatility ratio, event window, odds delta, spread și calendar event.

LLM-ul completează schema; un compilator determinist generează experimentul. LLM-ul nu scrie Python arbitrar și nu poate introduce un indicator neverificat.

### T2 — Evidence ladder cu holdout blocat

Fiecare ipoteză trece prin: pre-registration → data-quality check → baseline/event study → purged walk-forward → locked holdout → forward shadow → paper. Holdout-ul nu poate fi reutilizat pentru reglarea parametrilor; orice modificare devine trial nou.

### T3 — Point-in-time data lineage

Fiecare observație primește `event_time`, `available_time`, `ingested_time`, sursă, versiune și checksum. Fiecare experiment primește `dataset_snapshot_id`.

Teste obligatorii: future timestamps, duplicate/gaps, timezone, revised data, survivorship bias și feature disponibil după momentul semnalului.

### T4 — Validation v2 pentru serii dependente

Actualul bootstrap și permutation test tratează în mare parte trade-urile ca observații independente. Propunerea este: portfolio returns aliniate temporal, stationary/block bootstrap, block permutation, purge + embargo pe label windows, nested walk-forward și PBO/CSCV pe candidați aliniați temporal.

Păstrează trial count global, dar adaugă trial count pe familie de cercetare; crypto 5m, sports și prediction markets nu trebuie tratate ca aceeași populație statistică.

### T5 — Cost model per piață

- Crypto: spread, maker/taker, funding, volatility/volume-dependent slippage, partial fills, latency și borrow.
- Forex: spread pe sesiune, rollover, gaps, calendar macro și slippage la știri.
- Sports: overround, limite, linie disponibilă, CLV și settlement.
- Prediction markets: spread, lichiditate, market impact, capital lock-up și reguli de rezoluție.

Penalizarea fixă de slippage rămâne baseline stresat, nu modelul final.

### T6 — Research budget și proveniență

Limitează ipotezele deschise și deduplicatează-le semantic/structural. Etichetează sursa: `human_prior`, `llm_literature_prior`, `data_mined`, `replication` sau `novel_combination`.

O ipoteză generată de LLM nu trebuie prezentată ca descoperire nouă; dovada decisivă trebuie să vină din forward shadow/paper data.

### T7 — Adaptoare de piață, nu alpha universal

| Piață | Baseline | Metrică principală |
|---|---|---|
| Crypto | buy-and-hold + trend simplu | return/drawdown/turnover după costuri |
| Sports | probabilitate de-vig din closing odds | log loss, Brier, calibrare, CLV |
| Manifold | prețul pieței | Brier/log score și lichiditate |
| Forex | carry/trend/random | return după spread și rollover |

Sports rămâne analitic/paper-only; Manifold rămâne play-money; OANDA rămâne practice.

### T8 — Forward shadow și reconciliere

Pornește daemonul dry-run pentru date forward, dar compară backtest fill vs dry-run fill: entry delay, fill rate, spread, slippage, funding și diferența dintre semnal și execuție.

## Plan integrat propus până la vara lui 2027

Ordinea de mai jos este o propunere peste planul existent; WP-urile livrate rămân livrate, iar implementarea concretă trebuie marcată separat în roadmap.

### P0 — fundație backend/data pentru JD-ul Revolut

1. Python/API quality: contracts, typing, idempotency, pagination, rate limits, integration tests și load test.
2. PostgreSQL adapter + migrations, indexes și query tests.
3. Pipeline Kafka → consumer → PostgreSQL → analytics API, cu schema/versioning, correlation ID, retry și deduplication.
4. Airflow DAG pentru ingestie/validare/transformare/raport, cu backfill și failure recovery.
5. Docker Compose + deployment local Kubernetes și mapping documentat către GCP.
6. Pornește T1-exec dry-run ca workload de date forward, nu ca prioritate izolată de backend.

### P1 — research kernel, KageBench și reliability

1. `HypothesisSpec` + Strategy DSL.
2. KageBench/replay pentru taskuri generale și experimente trading.
3. Validation v2: block bootstrap, purge/embargo, walk-forward și holdout blocat.
4. Security benchmark pentru agenți și trading research pipeline.

### P2 — piețe și ML demonstrabil

1. Crypto forward shadow cu costuri realiste.
2. Stefan implementează ghidat meta-labeling și interpretarea rapoartelor.
3. Sports: Dixon–Coles implementat de Stefan, comparat întâi cu odds-only/de-vig baseline.
4. Manifold și forex doar după ce protocolul funcționează pe primele piețe.

### P3 — orchestrare și produs

1. WP-G2: sandbox per executor.
2. WP13 numai după KageBench, ca experiment A/B reviewer vs fără reviewer.
3. WP-CX: Codex ca executor după sandbox, cu governance comun.
4. O felie verticală manufacturing/automotive cu utilizatori reali și metrici de impact.

### P4 — pachet CV și internship

Pregătește un demo și un raport cu arhitectură/threat model, benchmark și baseline-uri, cost/latency/success trade-offs, ablations, un failure postmortem real, rezultate forward/paper și explicația personală a HMM-ului, Dixon–Coles, purged CV și meta-labeling.

Ținta nu este „bot profitabil”, ci un sistem AI care poate fi construit, evaluat, securizat și explicat sub incertitudine.

## Trasabilitate

- Orice implementare derivată din acest fișier trebuie să menționeze `Codex proposal: Gx` sau `Tx` în commit/PR.
- O propunere devine decizie numai după confirmarea lui Stefan și actualizarea roadmap-ului/handoff-ului.
- Statusurile sunt distincte: `proposed` → `accepted` → `in progress` → `done` sau `rejected`.
- Registrul se păstrează și pentru propunerile respinse.
