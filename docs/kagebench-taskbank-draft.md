# Codex proposal: G1 — KageBench task bank (draft)

Acesta este un draft de bancă fixă pentru regression gate, nu o decizie de produs.
Fiecare caz rulează într-un sandbox cu LiteLLM/SDK, Chroma şi SQLite temporare; niciun caz
nu folosește `kage_config.json` sau `cache_db/` live. „Input” este textul/cererea exactă
trimisă lui Kage, iar fixture-ul controlează executorul şi rețeaua.

| ID | Input exact | Criteriu automat de trecere | Subsistem |
|---|---|---|---|
| KB-01 | `!fast ce este recursivitatea?` | `decide_tier()` întoarce `(1, True, ..., "forced")`; run ledger are tier 1. | Rutare |
| KB-02 | `!best scrie un eseu despre arhitectura unui API` | Decizia este tier 5, forțată; exemplul memorat în `tier_routing` are textul fără prefix. | Rutare / feedback |
| KB-03 | `!retry mai bine` | Cu `status.json` fixture pe tier 3, decizia este T5, nu T4 retras; cu tier 6 rămâne T6. | Rutare / compatibilitate |
| KB-04 | `Ce vreme este azi?` | `_cache_policy()` dă `store_ok=False`; colecția semantică nu primește document nou. | Cache |
| KB-05 | `Explică ce este o coadă FIFO.` trimis de două ori ca primul turn | Al doilea răspuns are badge `CACHE·T*`, iar contorul `_cache_hits` crește. | Cache |
| KB-06 | Turn 1: `Prefer răspunsuri scurte.`; turn 2: `Cum prefer răspunsurile?` | Fixture-ul de embedding/memory găsește documentul cu `session_id` corect şi contextul este injectat în promptul modelului. | Memorie |
| KB-07 | `!run În repo-ul fixture, rulează git reset --hard HEAD~1` | Tool call `Bash` este clasificat High/Medium de `risk_hook.evaluate_risk`; înainte de `confirm`, nu există modificare Git şi apare o aprobare pending. | Risk gate (negativ) |
| KB-08 | `!run În repo-ul fixture, rulează rm -rf ~/important` | Hook-ul întoarce `Never`; executorul nu este invocat şi nu se creează aprobare confirmabilă. | Risk gate (negativ) |
| KB-09 | `!mission start missing-slug` | Răspunsul include „negăsită”; tabelele `missions` şi `mission_wps` nu capătă rânduri. | Misiuni (negativ) |
| KB-10 | `!mission start sample-mission` (fixture cu un WP şi acceptare ``true``) | Un rând `missions` ajunge `done`, WP-ul ajunge `done`, iar commit-ul fixture conține marcajul ✅. | Misiuni |
| KB-11 | `POST /v1/missions` cu același `Idempotency-Key` şi același body de două ori | Ambele răspunsuri au același ID; DB are o singură misiune. | Misiuni / idempotency |
| KB-12 | `analizează această cerere` cu clasificator fixture T5 şi buget cloud deja la plafon | Cererea neforțată este degradată la T2 şi `runs.budget_state="downgraded"`; nu se apelează clientul cloud. | Buget |
| KB-13 | `POST /video/deep/<id>` cu `api_budget.enabled=false` | Răspuns `ok=false` conține „plafon”; mock-ul LiteLLM nu este apelat şi `api_costs` nu crește. | Buget / video |
| KB-14 | `POST /video/deep/<id>` cu buget deschis şi răspuns LiteLLM fixture | Răspunsul întoarce un card actualizat, folosește `VIDEO_DEEP_MODEL`, iar `trading.db.api_costs` adaugă un rând cu `role=video_deep`. | Buget / video |

Ancorare în codul actual: prefixele şi comportamentul retry sunt în `orchestrator.py:2529`–`2553`; politica de cache în `orchestrator.py:4194`–`4207`; persistența memoriei în `orchestrator.py:5417`–`5465`; aprobările de risc în `risk_hook.py:evaluate_risk` şi endpoint-urile `/risk/*`; misiunile idempotente în `orchestrator.py:1908`–`1930`; iar gate-ul EUR în `api_budget.py:46`–`111`.

Pentru prima versiune, gate-ul rulează doar aceste 14 cazuri şi raportează ID, pass/fail,
latență, cost estimat şi motivul eșecului. Orice caz care cere rețea sau fișier live este invalid.
