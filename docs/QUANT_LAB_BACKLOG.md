# QUANT_LAB_BACKLOG — taskuri ordonate (WP-T reorientat)

> Însoțește `QUANT_LAB_DESIGN.md`. Ordinea respectă principiul „fiecare pas util independent,
> chiar dacă următorul nu se construiește niciodată". Cod de producție începe DOAR după ce
> Stefan aprobă designul + răspunde la întrebările din §9 (design doc).

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
- `[ ]` **2.3 Buget API pentru Critic — AMÂNAT la Etapa 5** (Criticul nu există încă). Prin
  **OpenRouter** (decizia Stefan), plafon 5–10€/lună, contor cost SQLite; peste plafon ⇒ fallback
  Qwen 35B local. Se leagă natural când construim Actor→Critic.

## Etapa 3 — bucla de context zilnic

- `[x]` **3.1 Ingestie date derivate.** ✅ `trading/market_data.py` — funding + OI + klines zilnice
  de la Binance public, provider cu **circuit breaker** (fetcher injectabil, rate-limiting). Un
  endpoint căzut → None, nu crapă. Teste cu mock.
- `[x]` **3.2 Regime detection NON-LLM.** ✅ `trading/regime.py` — volatilitate realizată + trend
  EMA200 → `{regime, bias, confidence}`, rule-based self-contained (numpy), deterministă. `flat` la
  spike real (vol ×2 peste tipic), nu la percentila 90. **Upgrade HMM** documentat (aceeași
  interfață) — hmmlearn evitat acum (posibil nementenat + risc de dependințe).
- `[x]` **3.3 `daily_context.json` + limitator freqtrade.** ✅ `trading/daily_context.py` — scrie
  JSON + rând `daily_context` (ledger); `bias_allows(side)` = contractul pe care strategiile îl
  cheamă în `populate_entry_trend` (short_only ⇒ long nu deschide). Provider jos ⇒ nu suprascrie.
  Teste (+11 pe Etapa 3). *Rămas mic:* apelul `bias_allows` în `SampleStrategy` (fișier gitignored).

## Etapa 4 — registrul de ipoteze + calibrare

- `[ ]` **4.1 Pre-registration `hypotheses`/`predictions`.** O ipoteză se scrie ÎNAINTE de
  verificare (invariant #2), cu predicție cuantificată + interval + criteriu de falsificare.
  **Acceptare:** o predicție fără pre-înregistrare e refuzată de sistem (test).
- `[ ]` **4.2 Job săptămânal de calibrare.** Brier score, coverage pe intervale, plot predis-vs-realizat.
  **Acceptare:** raport pe `predictions` cu rezultate realizate; rulează din scheduler.
- `[ ]` **4.3 Baseline condiționat + event studies.** KS/permutation semnal vs piață; event study
  [-5,+5] zile pe evenimente discrete (LLM cataloghează, numpy calculează).
  **Acceptare:** un „semnal" care nu mută distribuția față de baseline pică; event study cu CI.

## Etapa 5 — Actor–Critic refactorizat + routing LLM

- `[ ]` **5.1 Actor** (Qwen 35B nocturn): ≤3 propuneri/noapte, format impus
  `{mecanism_cauzal, predictie_cu_interval, criteriu_falsificare, implementare_schita}`;
  refuză output incomplet. **NU scrie cod de execuție.**
  **Acceptare:** output ne-conform e respins; propunerile intră ca `hypotheses` pre-înregistrate.
- `[ ]` **5.2 Critic** (API ieftin, 1 trecere, aprobă ≤1): filtrează plauzibilitatea, întreabă
  „cine ar arbitra asta imediat?". Nu validează statistic.
  **Acceptare:** aprobă ≤1/noapte; fallback local la buget depășit; test pe format.
- `[ ]` **5.3 Pipeline complet nocturn**: Actor → Critic → backtest costuri stresate → validare
  (Etapa 1) → raport dimineața. **Promovare la paper = MANUALĂ, doar Stefan.**
  **Acceptare:** un ciclu nocturn complet fără intervenție produce un raport; NIMIC nu se
  promovează automat; pytest verde; nicio cale spre ordine reale (garda T1).

---

## Criterii de acceptare globale (moștenite din handoff T1 + spec)

- Un ciclu nocturn complet fără intervenție (propuneri → backtest → validare → raport).
- Costuri stresate (fees + slippage dublat) în ORICE backtest care ajunge la validare.
- Nicio cale de cod nu poate plasa un ordin real (fără chei live; verificat cu test).
- Validarea e matematică, nu LLM; contorul de trial-uri e global și imuabil.
- Kill-switch determinist rulează independent de LLM.
- `pytest` verde la fiecare etapă.
