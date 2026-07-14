# QUANT_LAB_BACKLOG — taskuri ordonate (WP-T reorientat)

> Însoțește `QUANT_LAB_DESIGN.md`. Ordinea respectă principiul „fiecare pas util independent,
> chiar dacă următorul nu se construiește niciodată". Cod de producție începe DOAR după ce
> Stefan aprobă designul + răspunde la întrebările din §9 (design doc).
>
> **14.07.2026:** propunerile T1–T8 din `CODEX-PROPUNERI.md` (HypothesisSpec/DSL, holdout
> blocat, validation v2 pentru serii dependente, cost models per piață) rămân `proposed` —
> rigoare quant reală, dar nu cerută de JD-ul Revolut (vezi `KAGE-HANDOFF.md` §4). Singura
> excepție e T3 (point-in-time lineage), acceptată și integrată în WP-ETL din handoff.

Legendă: `[ ]` de făcut · `[~]` parțial (fundația T1) · fiecare task are **Acceptare**.

---

## Etapa 0 — reconciliere fundație (mic, deblochează restul)

- `[~]` **0.1 Migrări schemă ledger.** Adaugă `trials`, `hypotheses`, `predictions`,
  `daily_context`; leagă `experiments.trial_id`. Non-distructiv (ALTER/CREATE IF NOT EXISTS).
  **Acceptare:** migrare idempotentă; testele T1 existente rămân verzi; schema nouă acoperită de test.
- `[ ]` **0.2 Neutralizează `nocturnal.py` naiv.** Îl marcăm deprecated/dezactivat (nu mai
  produce experimente selectate pe profit IS) până vine Actor–Critic (Etapa 5). Nu ștergem
  istoricul din ledger — e input pentru Etapa 1.
  **Acceptare:** bucla veche nu mai rulează din misiune; un test confirmă că nu se mai face
  selecție pe `profit_total` fără validare.

## Etapa 1 — modulul de validare statistică (PRIORITATE #1)

- `[ ]` **1.1 `trading/validation.py`** — pur numpy/scipy, ZERO LLM. Funcții:
  `bootstrap_pnl(trades, n=10000)` → CI profit + max drawdown; `permutation_test(returns)` →
  p-value pe semne amestecate; `deflated_sharpe(sr, n_trials, skew, kurt, n_obs)`;
  `pbo_cscv(returns_matrix)` → probabilitate de overfitting.
  **Acceptare:** teste unitare cu serii sintetice de semnal cunoscut (o strategie „câștigătoare
  fabricată din zgomot" pică permutation + DSR); pytest verde; nicio dependință comercială.
- `[ ]` **1.2 Raport verdict peste ledger existent.** Rulează 1.1 peste toate `experiments` din
  `trading.db` → tabel: strategie | Sharpe brut | DSR (deflatat pe trial count) | PBO | permutation
  p | verdict (SEMNAL / ZGOMOT).
  **Acceptare:** raport generat pe datele reale actuale; verdict onest (așteptare realistă:
  majoritatea = ZGOMOT, ceea ce validează teza); rulabil cu o comandă + secțiune în briefing.

## Etapa 2 — kill-switch + contor global de trial-uri + bugete

- `[x]` **2.1 Contor global de trial-uri.** ✅ `trials` în ledger; `runner.py` inserează la fiecare
  backtest (`record_trial`); `report.py`/DSR folosesc `count_trials()` ca N (fallback pe populația
  de Sharpe-uri). Nimic nu se șterge. Teste: `test_trading_killswitch.py`.
- `[x]` **2.2 Kill-switch determinist.** ✅ `trading/killswitch.py` pur Python, ZERO LLM:
  `current_drawdown` pe equity paper, `check()` declanșează halt la ≤ −15% + Telegram (injectabil),
  idempotent; `trip/clear/is_halted` în ledger (tabel `killswitch` singleton); `decay_candidates`
  (heuristică). CLI `python -m trading.killswitch`. Ridicarea = manuală. Teste (+8).
- `[x]` **2.3 Buget API pentru Critic.** ✅ `trading/budget.py` + tabel `api_costs` în ledger:
  `ApiBudget(cap_eur, eur_usd)` cu `over_budget()`, `record()`, `status()`; `estimate_usd` din
  tokeni × preț/milion (config `price_per_mtok_in/out`), sau cost real dacă OpenRouter îl întoarce
  în `usage.cost`. Peste plafon ⇒ Criticul cade pe fallback local (vezi 5.2). Plafon default 7€/lună
  (interval aprobat 5–10€). Teste în `test_trading_pipeline.py`.
  **Plan model Critic (decizie 09.07.2026):** `tencent/hy3:free` până pe **21.07.2026** (expiră
  gratuitatea), apoi **`deepseek/deepseek-v4-pro`** (0.435/0.87 $/M, prețuri OpenRouter
  09.07.2026 — reverifică la comutare; consum estimat ~0.10 $/lună, plafonul 7€ rămâne larg).
  La comutare: setează `model` + `price_per_mtok_in/out` în `kage_config.json` (vezi
  `_comment_model_plan` din example).

## Etapa 3 — bucla de context zilnic

- `[x]` **3.1 Ingestie date derivate.** ✅ `trading/market_data.py` — funding + OI + klines zilnice
  de la Binance public, provider cu **circuit breaker** (fetcher injectabil, rate-limiting). Un
  endpoint căzut → None, nu crapă. Teste cu mock.
- `[x]` **3.2 Regime detection NON-LLM.** ✅ `trading/regime.py` — volatilitate realizată + trend
  EMA200 → `{regime, bias, confidence}`, rule-based self-contained (numpy), deterministă. `flat` la
  spike real (vol ×2 peste tipic), nu la percentila 90. **Upgrade HMM** documentat (aceeași
  interfață) — hmmlearn evitat acum (posibil nementenat + risc de dependințe).
  **Decizie 09.07.2026:** la upgrade, HMM-ul se scrie **de la zero** (numpy, EM, 2–3 stări
  gaussiene, ~120 linii) și îl implementează **Stefan în mod ghidat** (schelet + teste de la
  model, corpul funcțiilor de la Stefan) — parte din strategia de CV; vezi decizia „mod de
  execuție pe partea ML" din `KAGE-HANDOFF.md` §4.
- `[x]` **3.3 `daily_context.json` + limitator freqtrade.** ✅ `trading/daily_context.py` — scrie
  JSON + rând `daily_context` (ledger); `bias_allows(side)` = contractul pe care strategiile îl
  cheamă în `populate_entry_trend` (short_only ⇒ long nu deschide). Provider jos ⇒ nu suprascrie.
  Teste (+11 pe Etapa 3). *Rămas mic:* apelul `bias_allows` în `SampleStrategy` (fișier gitignored).

## Etapa 4 — registrul de ipoteze + calibrare

- `[x]` **4.1 Pre-registration `hypotheses`/`predictions`.** ✅ tabele + `trading/hypotheses.py`:
  `register_hypothesis` (predicție cuantificată + interval + criteriu de falsificare validate),
  `record_prediction` refuză dacă ipoteza nu e pre-înregistrată SAU semnalul e anterior
  pre-înregistrării (`PreRegistrationError`). `resolve` + `mark_hypothesis`. Teste (+5).
- `[x]` **4.2 Job de calibrare.** ✅ `trading/calibration.py`: Brier score (probabilistice) +
  coverage pe intervale 80% peste `predictions` rezolvate; raport text + CLI. Teste (+2).
  *Rămas:* cablarea în scheduler (job săptămânal) — la integrarea Kage (supervisor).
- `[x]` **4.3 Baseline condiționat + event studies.** ✅ în `validation.py`:
  `signal_moves_distribution` (KS + permutation — semnalul trebuie să MUTE distribuția, altfel
  pică) + `event_study` (randament anormal mediu per offset + CI95). Teste (+3).

## Etapa 5 — Actor–Critic refactorizat + routing LLM

- `[x]` **5.1 Actor** (Qwen 35B nocturn). ✅ `trading/actor.py`: `propose`/`register`, format impus
  `{mecanism_cauzal, predictie_cu_interval, criteriu_falsificare, implementare_schita}`, parser
  tolerant la fence-uri markdown, `validate_proposal` refuză output incomplet (`ActorFormatError`).
  **NU scrie cod.** Propunerile valide se pre-înregistrează ca `hypotheses` (invariant #2). Teste (+6).
- `[x]` **5.2 Critic** (OpenRouter, 1 trecere, aprobă ≤1). ✅ `trading/critic.py`: `critique` aprobă
  cel mult una (index clamp-uit), înregistrează costul în buget; **peste plafon ⇒ fallback pe
  `local_chat` (Qwen local), zero cost.** Nu validează statistic (invariant #5). Teste (+5).
- `[x]` **5.3 Pipeline complet nocturn.** ✅ `trading/pipeline.py` `NightlyPipeline.run_once`:
  Actor → pre-înregistrare → Critic (≤1) → validare la **costuri stresate** (`trading/costs.py`,
  slippage dublat) → raport de dimineață. `assert PAPER_ONLY`; `promoted=False` mereu; test confirmă
  că niciun experiment nu devine `paper` automat. `trading/llm.py` = client chat injectabil. Teste (+2 pipeline).

> **Notă:** ideea de *audit de utilizare a modelelor* (local / Claude abonament / OpenRouter) e o
> preocupare Kage-globală, NU parte din WP-T. E notată în `KAGE-HANDOFF.md` (item cross-cutting).

---

## Criterii de acceptare globale (moștenite din handoff T1 + spec)

- Un ciclu nocturn complet fără intervenție (propuneri → backtest → validare → raport).
- Costuri stresate (fees + slippage dublat) în ORICE backtest care ajunge la validare.
- Nicio cale de cod nu poate plasa un ordin real (fără chei live; verificat cu test).
- Validarea e matematică, nu LLM; contorul de trial-uri e global și imuabil.
- Kill-switch determinist rulează independent de LLM.
- `pytest` verde la fiecare etapă.
