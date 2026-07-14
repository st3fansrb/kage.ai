# Kage — aliniere la JD-ul Revolut Python Intern

> Sursă: JD-ul Revolut furnizat de Stefan în conversație, 13.07.2026. Această mapare este o propunere generată de Codex și trebuie revizuită de Stefan.

## Ce cere JD-ul

Rolul este în primul rând software/data engineering, nu „LLM research” în abstract:

- Python 3 și SQL;
- PostgreSQL;
- Kafka;
- Airflow;
- Docker și Kubernetes;
- GCP;
- TDD;
- API-uri bine proiectate și scalabile;
- pipelines pentru reporting, analytics și data science;
- data models și data flows;
- sisteme distribuite;
- colaborare, task tracking și prezentarea rezultatelor.

## Gap-ul actual în Kage

| Cerință JD | Ce există acum | Ce trebuie demonstrat |
|---|---|---|
| Python 3 | FastAPI/orchestrator + module trading | cod modular, typing, teste, profiling și API contracts |
| SQL/PostgreSQL | SQLite pentru ledger/history | PostgreSQL adapter, migrations, indexes, transactions și query tests |
| Kafka | nu există | event ingestion cu schema/versioning, retries și idempotency |
| Airflow | APScheduler | DAG real, backfill, retry, dependency și observability |
| Docker/Kubernetes | Docker este planificat ca sandbox | local reproducible deployment, health checks, resource limits și manifests |
| GCP | nu există | deployment/documentation realistă sau un design cloud-agnostic cu mapping GCP |
| TDD | suită pytest bună | contract tests, integration tests, failure tests și CI gate |
| Scalable APIs | API FastAPI single-node | pagination, async jobs, idempotency keys, rate limits, versioning și load test |
| Data pipelines | joburi Kage/trading | raw → validated → feature/analytics layers, lineage și data-quality checks |
| Distributed systems | Mission Runner + scheduler local | event-driven boundaries, retries, deduplication, restart recovery și eventual consistency |
| Communication | Telegram/Mission Control | ADR-uri, README architecture, runbook și un demo prezentabil |

## Reorientarea propusă

Pentru aplicația Revolut, Kage trebuie prezentat astfel:

> „Kage este o platformă Python pentru orchestrarea și evaluarea unor workflow-uri AI, cu API-uri, pipeline-uri de date, event ledger, policy enforcement și execuție reproductibilă. Tradingul este un workload de research paper-only peste aceeași platformă.”

Nu este recomandat să fie prezentat ca „bot de trading” sau ca un proiect care pretinde profit.

## Track-ul CV Revolut

### R0 — Python/API quality

- mută componentele stabile spre module cu responsabilități clare;
- typing și validation consistente;
- OpenAPI contracts și versionare API;
- idempotency keys pentru joburi/taskuri;
- pagination, rate limiting și job status endpoints;
- integration/contract tests și CI;
- profiling și load test pentru endpoint-uri importante.

### R1 — Data platform slice

Construiește un flux complet, mic și reproductibil:

```text
producer → Kafka topic → consumer → PostgreSQL raw tables
         → validation → feature/analytics tables → API/report
```

Mesajele trebuie să aibă schema/version, correlation ID, retry policy și deduplication key. PostgreSQL devine opțiunea de integrare pentru un workload demonstrativ, fără a elimina SQLite-ul embedded pentru cache/local state.

### R2 — Airflow și pipeline engineering

Un DAG real pentru un workload Kage/trading:

- ingestie;
- validare schema și data quality;
- transformare;
- agregare;
- raport;
- notificare.

DAG-ul trebuie să demonstreze retry, backfill, task dependencies, idempotency și observability. APScheduler rămâne pentru joburi locale simple; nu îl prezenta ca echivalent Airflow.

### R3 — Docker/Kubernetes/GCP story

- Docker Compose pentru dezvoltare locală;
- un deployment local cu kind/minikube pentru API + worker + broker + Postgres;
- readiness/liveness checks, resource limits și graceful shutdown;
- un document cu mapping către GCP (Cloud Run/GKE, managed Postgres, Pub/Sub ca alternativă Kafka, object storage), fără să pretinzi că ai operat production GCP dacă nu ai făcut-o.

### R4 — Agent/research layer

După fundația data/backend:

- KageBench și replay;
- executor governance și security benchmark;
- Quant Lab cu `HypothesisSpec`, point-in-time data și validation temporală;
- WP13 doar cu A/B evaluation;
- Codex executor după sandbox.

## Ce implementează Stefan pentru valoare de interviu

Conform regulii deja existente în handoff, Stefan scrie și poate apăra:

- HMM-ul de regim de la zero;
- Dixon–Coles;
- purged CV și meta-labeling;
- schema de evaluare și interpretarea rapoartelor;
- o parte semnificativă din schema/data-flow design.

Codex poate ajuta la plumbing, teste de contract, fixtures, migrations, Docker/Kubernetes manifests și review, dar acestea nu trebuie să ascundă înțelegerea lui Stefan.

## Track separat: startup / utilitate

Workflow-ul manufacturing/automotive și integrarea WinMentor/SAGA rămân o pistă de startup. Este utilă pentru traction și utilizatori reali, dar nu trebuie să înlocuiască pista backend/data engineering pentru aplicația Revolut.

## Dovezi de pregătit până la aplicație

- diagramă de arhitectură și ADR-uri;
- API contract + exemplu de client;
- schema PostgreSQL și migration history;
- Kafka event schema și handling de duplicate/retry;
- Airflow DAG cu backfill demonstrat;
- Docker Compose + deployment local Kubernetes;
- teste TDD, integration și failure injection;
- benchmark de load și latență;
- un postmortem real;
- demo de 5 minute care urmărește un eveniment de la ingestie până la rezultat;
- rezultate trading strict paper-only, prezentate ca experimente și nu ca promisiuni financiare.
