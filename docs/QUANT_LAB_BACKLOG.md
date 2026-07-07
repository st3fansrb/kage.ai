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

- `[ ]` **2.1 Contor global de trial-uri.** Orice backtest (inclusiv eșec) inserează în `trials`.
  `runner.py` incrementează înainte de rulare. DSR/PBO citesc de aici. Nimic nu se șterge.
  **Acceptare:** N backtests ⇒ N rânduri `trials`; DSR folosește `count(trials)`; test.
- `[ ]` **2.2 Kill-switch determinist.** `trading/killswitch.py` pur Python, cron 5 min: citește
  ledger, drawdown global paper > prag ⇒ marchează „flat everything" + Telegram. Decay de alocare:
  strategie sub prag N zile ⇒ alocare → 0.
  **Acceptare:** test cu drawdown fabricat declanșează flat + notificare; independent de LLM;
  rulează chiar dacă restul sistemului e jos.
- `[ ]` **2.3 Buget API pentru Critic.** Refolosește `#7 Budget v2`; contor cost în SQLite;
  jobul nocturn refuză să pornească peste plafonul lunar aprobat.
  **Acceptare:** peste plafon ⇒ Critic sare pe fallback local (Qwen 35B), bucla nu moare; test.

## Etapa 3 — bucla de context zilnic

- `[ ]` **3.1 Ingestie date derivate.** funding rate + open interest (Binance native, gratis;
  Coinalyze fallback) prin provider cu circuit breaker (pattern-ul Ollama). Rate-limiting politicos.
  **Acceptare:** o citire/zi scrisă în `daily_context.features_json`; un provider căzut nu
  omoară pipeline-ul (test cu mock).
- `[ ]` **3.2 Regime detection NON-LLM.** HMM (hmmlearn sau statsmodels) pe volatilitate realizată
  + filtru trend (preț vs EMA200 daily) → `{regime, bias, confidence}`.
  **Acceptare:** clasificare reproductibilă pe date istorice; NICIUN apel LLM în calea de clasificare.
- `[ ]` **3.3 `daily_context.json` + limitator freqtrade.** LLM opțional sintetizează JSON strict;
  bucla 1 citește `bias` ca limitator (bias=short_only ⇒ long nu deschide).
  **Acceptare:** fișier + rând DB zilnic; test că bias-ul chiar blochează direcția opusă în strategie.

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
