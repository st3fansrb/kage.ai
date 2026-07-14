# QUANT_LAB_DESIGN — Laboratorul de cercetare cantitativă (WP-T, reorientat)

> Statut: **DRAFT de design — cod de producție NU se scrie până Stefan aprobă.**
> Sursă: `quant_lab_claude_code_prompt.md` (spec Stefan) + cercetare open-source (07.07.2026).
> Înlocuiește abordarea naivă „N backtests + LLM mută strategia" cu metoda științifică.
>
> **14.07.2026:** completările propuse de Codex pentru `HypothesisSpec`, lineage, validation
> temporală și cost models sunt în `CODEX-PROPUNERI.md` (T1–T8). Rămân `proposed`, în afara
> lanțului activ — vezi verdictul de reconciliere din `KAGE-HANDOFF.md` §4.

## 0. De ce reorientarea

Teza lui Stefan (confirmată): **edge-ul real nu e matematic și nu se găsește prin câteva
backtests in-sample / out-of-sample.** Un pipeline care rulează multe strategii și o alege pe
cea cu profit maxim pe istoric **produce garantat overfitting** (data snooping): cu destule
încercări, găsești mereu ceva care „a mers" pe trecut și moare pe date noi.

Consecință de design: sistemul nu e „un AI care dă semnale", ci un **laborator care respinge
zgomotul**. Valoarea lui e cât de agresiv *falsifică* ipoteze, nu câte „strategii câștigătoare"
raportează. Validarea e statistică (deflatată pe numărul de încercări), nu narativă.

## 1. Invarianți (nenegociabili — orice PR care îi încalcă se respinge)

1. **LLM-ul nu decide niciodată intrări/ieșiri.** Execuția = cod Python determinist,
   reproductibil, backtestabil. LLM-ul doar *propune ipoteze*, *cataloghează evenimente*,
   *critică mecanisme*.
2. **Pre-registration.** O ipoteză există doar dacă a fost scrisă în DB *înainte* de a putea
   fi verificată. Explicații post-hoc = storytelling, nu intră în sistem.
3. **Contor global de trial-uri.** Fiecare backtest rulat vreodată (inclusiv eșecurile) se
   numără și nu se șterge niciodată. Fără el, Deflated Sharpe și PBO sunt invalide.
4. **Costuri stresate.** Orice backtest rulează cu fees reale + **slippage dublat**. O
   strategie care nu supraviețuiește costurilor stresate nu ajunge la validare statistică.
5. **Validarea e matematică, nu LLM.** DSR, PBO, bootstrap, permutation, calibrare. LLM-ul
   NU are voie să declare o ipoteză „validă".
6. **Kill-switch determinist permanent.** Pur Python, zero LLM, independent de rest.
7. **Paper-only (moștenit din T1).** Nicio cale de cod nu plasează ordine reale; promovarea pe
   bani reali e manuală, în afara sistemului. `assert_paper_only` rămâne garda.

## 2. Ce există deja (explorarea codebase-ului, 07.07.2026)

Fundația T1 e livrată/în lucru pe branch-ul `feat/wpt-t1-crypto-foundation`:

| Fișier | Rol | Verdict față de reorientare |
|---|---|---|
| `trading/ledger.py` | `trading.db`: `experiments`, `paper_trades`, `agent_status` | **PĂSTRĂM + extindem** (tabele noi: `trials`, `hypotheses`, `predictions`, `daily_context`) |
| `trading/safety.py` | `assert_paper_only` (invariant #7) | **PĂSTRĂM** neschimbat |
| `trading/runner.py` | Punte freqtrade subprocess → ledger (backtest→experiment) | **PĂSTRĂM**, dar backtest-ul trebuie să treacă prin gata de costuri stresate + să incrementeze contorul de trial-uri |
| `trading/nocturnal.py` | Buclă: **LLM rescrie codul strategiei**, alege pe **profit in-sample max** | **REFACTOR MAJOR** — încalcă invariant #1 (LLM scrie execuția) și #3/#5 (selecție pe IS fără validare). Devine Actor→Critic pe format impus + gate de validare (vezi §5) |
| `missions/trading-nocturnal/`, `scripts/setup_trading.sh`, `trading/ft_config_dry.json` | Orchestrare misiune WP11 + venv izolat + config dry | **PĂSTRĂM**, adaptăm misiunea la noul pipeline |

**Schema ledger găsită** (de confirmat cu Stefan — vezi §9 întrebarea 1):
`experiments(id, strategy, exchange, pair, timeframe, params JSON, metrics JSON,
backtest_start, backtest_end, oos_passed, status, mission_id, notes, created_at)`;
`paper_trades(id, experiment_id, pair, side, entry/exit, amount, fee, pnl, status, created_at)`;
`agent_status(agent, status, pid, last_heartbeat, message, updated_at)`.

## 3. Arhitectura — 3 bucle pe cadențe diferite

```
┌─ Bucla 1: EXECUȚIE (permanent, ZERO LLM) ──────────────────────────┐
│ freqtrade dry-run, semnale = cod Python pur.                        │
│ Citește daily_context.json → bias direcțional ca LIMITATOR         │
│ (bias=short_only ⇒ strategiile long nu deschid poziții).           │
│ Kill-switch (cron 5 min): drawdown global > prag ⇒ flat + Telegram.│
└────────────────────────────────────────────────────────────────────┘
┌─ Bucla 2: CONTEXT ZILNIC (1×/zi, LLM mic sau deloc) ───────────────┐
│ Date: funding, OI, lichidări (Binance native / Coinalyze).         │
│ Regime detection = HMM pe volatilitate realizată + trend EMA200d.  │
│   (clasificare NON-LLM). LLM opțional doar sintetizează JSON strict.│
│ Output: daily_context.json {regime,bias,confidence,reasoning} + DB. │
└────────────────────────────────────────────────────────────────────┘
┌─ Bucla 3: RESEARCH NOCTURN (00:00–07:00, LLM mare, buget hard) ────┐
│ Actor (≤3 ipoteze/noapte, format impus, condiționat pe regim+eșecuri)│
│   → Critic (1 trecere, aprobă ≤1, filtrează plauzibilitatea)       │
│   → Backtest costuri stresate (DOAR pe cea aprobată)               │
│   → Validare statistică (§4) scrisă în ledger cu trial count curent │
│   → Raport dimineața. PROMOVARE LA PAPER = MANUALĂ, doar Stefan.    │
└────────────────────────────────────────────────────────────────────┘
```

Fiecare buclă e utilă independent, chiar dacă următoarea nu se construiește niciodată.

## 4. Modulul de validare statistică (prioritatea #1)

Rulează peste ledger-ul **EXISTENT**, înainte de orice altceva. Răspunde la o singură
întrebare: *ce a produs bucla până acum e semnal sau zgomot?*

- **Bootstrap** pe secvența de trade-uri (resampling cu replacement, ≥10.000 iterații):
  distribuția profitului ȘI a max drawdown-ului → interval de încredere, nu un număr.
- **Permutation test**: strategia pe date cu semne amestecate — dacă „câștigă" și acolo, e zgomot.
- **Deflated Sharpe Ratio** (Bailey & López de Prado) folosind **contorul global de trial-uri**.
- **PBO via CSCV** unde sunt destule date.
- Raport: verdict pe fiecare strategie din ledger → *semnal* sau *zgomot*.

**Decizie de bibliotecă:** DSR/PBO/CSCV se **scriu de la zero** (~câteva sute de linii din
papers), NU prin mlfinlab (licență „all rights reserved", comercială — inutilizabil).
Referințe open pentru verificare: [pypbo](https://github.com/esvhd/pypbo),
[The-deflated-sharpe-ratio](https://github.com/Nikhil-Kumar-Patel/The-deflated-sharpe-ratio),
paperul original [Bailey & Borwein PBO](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
Bootstrap/permutation = numpy pur. Ăsta e diferențiatorul nostru — merită cod propriu, testat.

## 5. Bucla 3 refactorizată: Actor → Critic → Validare

Înlocuiește `nocturnal.py` actual (LLM scrie cod → selecție pe profit IS).

- **Actor** (Qwen 35B local, nocturn): max 3 propuneri/noapte, condiționat pe regimul curent
  + eșecurile recente. Format IMPUS, refuză orice output incomplet:
  `{mecanism_cauzal, predictie_cu_interval, criteriu_falsificare, implementare_schita}`.
  **Nu scrie cod de strategie** — descrie un mecanism și o schiță; implementarea în cod
  determinist o face un dezvoltator (Stefan/Kage), nu se auto-execută.
- **Critic** (API ieftin capabil, ex. Haiku — 1 apel/noapte): o trecere, aprobă ≤1 propunere.
  Rol: elimină absurdul logic + întreabă „ce actor ar arbitra asta imediat?". NU validează
  statistic — doar filtrează plauzibilitatea ca să economisim compute.
- **Backtest** doar pe propunerea aprobată, cu costuri stresate (§ invariant #4).
- **Validare** (§4) → scris în ledger cu numărul curent de trial-uri.
- **Promovare la paper: MANUALĂ, doar Stefan, dimineața.** Sistemul pregătește un raport.

## 6. Registrul de ipoteze (tabele noi + joburi)

- `hypotheses(id, mecanism_cauzal, predictie_json {media, interval_80}, criteriu_falsificare,
  status, pre_registered_at, regime_at_creation, source_actor_model)`.
- `predictions(id, hypothesis_id, signal_ts, predicted_json, realized_json, horizon, created_at)`.
- `trials(id, kind, strategy_ref, params_hash, cost_profile, created_at)` — **contorul global**;
  fiecare backtest (inclusiv eșec) inserează un rând. Nimic nu se șterge.
- `daily_context(date, regime, bias, confidence, reasoning, features_json, created_at)`.
- Job săptămânal de **calibrare**: Brier score, coverage test pe intervale, plot predis-vs-realizat.
- **Baseline obligatoriu:** distribuția condiționată pe semnal vs necondiționată a pieței
  (Kolmogorov–Smirnov / permutation). Semnalul trebuie să MUTE distribuția, altfel ipoteza pică.
- **Event studies** (Fed, unlocks, expirări): aliniere la T=0, randament anormal mediu în
  fereastra [-5,+5] zile + interval. LLM cataloghează evenimentele; numpy face matematica.

## 7. Routing LLM — local vs API

Constrângere reală: Qwen 35B = o singură instanță simultană pe Mac; orchestratorul are componente
concurente. Deci:

| Task | Model | Justificare |
|---|---|---|
| Sinteză bias zilnic (JSON strict) | Qwen 8B local **sau** API ieftin | clasificare/formatare, nu raționament adânc |
| Catalogare evenimente din text | Qwen 8B local, batch | volum mare, calitate suficientă |
| Actor (generare ipoteze) | Qwen 35B local, DOAR nocturn | creativitate + context lung, când Mac-ul e liber |
| Critic (raționament pe mecanisme) | **API ieftin capabil prin OpenRouter** (ex. DeepSeek/Qwen/Gemini Flash — model cu preț/performanță mai bun decât Claude direct) | calitatea raționamentului contează cel mai mult; ~1 apel/noapte; OpenRouter = flexibilitate de model la cost mic |
| Orice validare statistică | **NICIUN LLM** | invariant #5 |

- **Critic prin OpenRouter** (decizia lui Stefan, 07.07.2026): rutat prin LiteLLM ca provider
  OpenRouter, model ales pe preț/performanță (NU direct Claude/Anthropic). Model-swappable din config.
- Buget API hard: **plafon 5–10€/lună**; contor de cost în SQLite (refolosim `#7 Budget v2` din
  Kage); jobul refuză să pornească peste plafon → fallback pe Qwen 35B local.
- Fallback: dacă API-ul pică, Criticul rulează pe Qwen 35B nocturn (calitate mai slabă acceptată,
  bucla nu moare). Watchdog termic/timp + `caffeinate` + limită de tokens/rulare.
- **Audit de utilizare a modelelor (post-proiect, Etapa 6 în backlog):** la final, inventariem
  fiecare punct unde Kage folosește un model — local (Qwen), Claude prin abonament, OpenRouter prin
  API — cu rol/volum/cost/sensibilitate la calitate, și decidem explicit unde merită **upgrade spre
  calitate**. Contorul `api_costs` (implementat la 2.3) e sursa de cost pentru partea OpenRouter.

## 8. Decizii open-source („nu reinventăm roata")

| Nevoie | Decizie | Justificare |
|---|---|---|
| Motor backtest/dry-run | **freqtrade** (avem) | native: Protections (StoplossGuard, MaxDrawdown/equity, CooldownPeriod). **Slippage NU e modelat nativ** → îl adăugăm noi (custom fee+slippage stresat). Walk-forward nu e nativ → scriptăm split-uri de `--timerange` |
| DSR / PBO / CSCV | **scriem noi** din papers | mlfinlab = comercial (evităm); pypbo/DSR repos = referință de verificare |
| Bootstrap / permutation / KS | **numpy/scipy** | matematică standard, cod propriu subțire |
| Regime detection (HMM) | **hmmlearn** (BSD) *cu rezervă* | posibil nementenat (fără release 12 luni) → alternativă `statsmodels` MarkovRegression; decidem la implementare |
| Rapoarte performanță | **quantstats** (Apache 2.0, întreținut) | tear-sheets gata făcute |
| Screening vectorizat (opțional) | **vectorbt** open (Apache+Commons Clause) | ok pentru research personal; `.pro` ($20/lună) doar dacă devine necesar |
| Date exchange (OHLCV) | **ccxt** (MIT) | unificat, deja standard |
| Funding / OI / lichidări | **Binance native** (gratis) + **Coinalyze** free tier | Coinglass API e paid; Binance fapi dă funding+OI gratis (OI istoric limitat ~30 zile) |
| Event studies | cod propriu + numpy | nu există lib clară; logica e a noastră |

Criteriu general: **biblioteci pentru matematică standard, cod propriu pentru orchestrare +
registrul de ipoteze** — ăla e diferențiatorul; restul e commodity.

## 9. Întrebări care blochează designul (răspuns înainte de cod)

1. **Schema ledger** — confirmi tabelele din §2 și migrările propuse (`trials`, `hypotheses`,
   `predictions`, `daily_context` + `experiments.trial_id`)? Vrei `paper_trades` păstrate sau
   agregat doar la nivel de experiment?
2. **Perechi + timeframe** — pe ce rulează freqtrade acum (T1: BTC/USDT, ETH/USDT, 5m)? Rămân
   astea pentru bucla de execuție, sau extindem?
3. **Plafon lunar API** (pentru Critic) — ce sumă aprobi? (propunere: 5€/lună — ~1 apel Haiku/noapte).
4. **Kill-switch**: pragul de drawdown global (propunere: −15% pe equity paper) și **N** pentru
   decay de alocare (propunere: strategie sub prag 5 zile → alocare spre zero).
5. **Ordinea**: pornim strict cu modulul de validare peste ledger-ul actual (backlog #1), sau
   întâi curățăm `nocturnal.py` (care produce în continuare experimente overfit-uite)?

## 10. Ordinea de construcție (detaliu în QUANT_LAB_BACKLOG.md)

1. **Validare statistică** peste ledger-ul existent + verdict semnal/zgomot.
2. **Kill-switch + contor global de trial-uri + bugete API.**
3. **Bucla de context zilnic** (date + HMM + `daily_context.json` + freqtrade ca limitator).
4. **Registrul de ipoteze + joburi de calibrare.**
5. **Actor–Critic refactorizat** pe formatul impus + routing LLM.

Fiecare pas e util independent, chiar dacă următorul nu se construiește niciodată.
