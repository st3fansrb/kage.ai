# KAGE-HANDOFF — plan de execuție pentru sesiunile următoare

> **Addendum 14.07.2026 — reconciliere cu propunerile Codex:** o sesiune Codex separată a produs
> `docs/CODEX-PROPUNERI.md` (registru G1–G6 + T1–T8) și `docs/REVOLUT-INTERNSHIP-ALIGNMENT.md`
> (mapare JD Revolut). Verdictul complet e în §4 („Reconciliere cu propunerile Codex"); pe scurt:
> R0, T3 (integrat în WP-ETL) și G5 (integrat în WP10) acceptate, G1-minim acceptat în formă
> redusă, restul (T1/T2/T4–T8, G4) rămân `proposed` în registrul separat.

> Scris de Claude Fable 5 pe 03.07.2026 (accesul lui Stefan la Fable expiră pe 07.07.2026).
> Destinatar: o sesiune viitoare de Claude (Opus/Sonnet) în Claude Code, care implementează
> itemii din `KAGE-EVALUARE.md` §4. Acest fișier + `CLAUDE.md` conțin tot contextul necesar
> pentru execuție — evaluarea NU trebuie re-derivată, doar consultată pentru raționamente.

---

## 0. Reguli de lucru pentru modelul executor

1. **Un pachet de lucru (WP) per sesiune și per branch.** Branch din `dev`, nume
   `feat/wp<N>-<slug>`. Nu combina WP-uri; nu „profita" să repari alte lucruri în trecere.
2. **`pytest` înainte de a te apuca** (baseline 03.07.2026: 28 verzi) **și după**. Fiecare WP
   adaugă propriile teste — criteriile de acceptare de mai jos sunt contractul.
3. **Python 3.9.6** în `.venv` — nicio sintaxă 3.10+ în `orchestrator.py`/`risk_hook.py`/
   `telegram_gateway.py`/`tests/`. Doar `status_widget.py` e pe 3.12.
4. Liniile de cod citate mai jos sunt valabile la 03.07.2026 și **vor derivă** — ancorele de
   încredere sunt numele de funcții. Caută funcția, nu linia.
5. Itemii mari (#4, #5, #15B) au raționamentul în `KAGE-EVALUARE.md` §2–§3 — citește secțiunile
   referite înainte de a scrie cod.
6. Serverul rulează probabil în producție pe mașina asta. Repornirea (`start_all.sh`) e OK
   după un WP terminat, dar anunță în răspuns că ai repornit.

### Comenzi de verificare (sanity harness)

```bash
source .venv/bin/activate && pytest                       # suită completă
curl -s localhost:4001/health                             # orchestrator viu
curl -s localhost:11434/api/tags | head -c 200            # Ollama viu
# chat non-stream (după WP1; azi returnează mereu SSE):
curl -s localhost:4001/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"!status"}],"stream":false}'
# chat SSE:
curl -sN localhost:4001/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"salut"}],"stream":true}' | head -20
tail -50 .logs/orchestrator.log                           # erori recente
```

---

## 1. Harta sistemului (starea reală din cod, 03.07.2026)

**Servicii:** orchestrator FastAPI `:4001` · LiteLLM proxy `:4000` (tiers 1–2 + fallback) ·
Ollama `:11434` (qwen8b, qwen3.6:35b, nomic-embed-text). Pornire: `start_all.sh`.

**Fluxul unei cereri de chat** (`chat_completions()`, `orchestrator.py` ~876):

```text
mesaj → parsare prefixe (!fast/!best/!plan/!save/!nocache/!retry/!status/!help)
      → decide_tier() ~1012  (semantic 1-NN pe TIER_EXAMPLES ~478 → _qwen_classify → heuristic)
      → _cache_lookup (ChromaDB semantic_cache, cosine 0.92, TTL 24h)
      → _budget_check ~1164  (doar chat, doar non-forțat — 20 cloud/zi)
      → context: T1–2 _compact_messages (20 msg, compaction) | T3+ _build_conversation_context
        ~2267 (DOAR ultimele 6 msg × 600 chars!) + _memory_retrieve + _get_obsidian_context
        ~2141 + _STEFAN_BASE ~2174 (persona hardcodată)
      → rutare: T1–2 _route_litellm (fallback ~2335: LiteLLM pică → T3 cloud, OCOLEȘTE bugetul)
                T3/5/6 _route_claude_autonomous ~2533 (subprocess claude -p, deadline FIX 120s
                ~2596) | T4 gemini
      → SSE stream → history_caching_gen ~963: INSERT SQLite + _memory_store ~1319 + cache store
```

**Agenți** (`task_run()` ~796 → `_background_task_exec` ~176): subprocess one-shot
`claude -p`/`gemini -p` cu `--allowedTools Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch
--permission-mode auto --settings risk_settings.json`; output în `_active_task_queues`
(memorie — moare la restart). Confinement: `_validate_task_cwd` ~91 (doar cwd-ul de pornire).

**Risk gate:** claude spawn → PreToolUse hook `risk_hook.py` → `evaluate_risk()` ~174 →
Never/High = deny direct, Medium+autonomous = `POST /risk/register/{id}` + poll până la
aprobare (UI `/api/pending`, ntfy, Telegram). Matcher hooks: `"Bash|Write|Edit"` — **tool-urile
MCP nu trec prin gate**.

**Canale:** `kage.html` (SSE + istoric client-side; interceptează `!run` DOAR client-side, la
~608) · `telegram_gateway.py` (polling `getUpdates`; `_forward_to_orchestrator` ~214 trimite
`stream:false` și așteaptă JSON) · widget menubar · ntfy.sh.

**Date:** `cache_db/` (ChromaDB: `semantic_cache`, `tier_routing`, `long_term_memory` +
`chat_history.db` SQLite cu `session_id`) · `usage_log.jsonl` (necitit incremental — citit
INTEGRAL la fiecare poll de 10s) · `status.json` (stare globală `!retry`) ·
`scheduled_tasks.json` (APScheduler; joburi nocturne: vacuum 04:00, backup 05:00).

**Config:** `kage_config.json` (real, cu token-uri; are `allowed_task_roots =
[~/orchestrator-v2, ~/Documents/StefanBrain]`, `autonomous_mode: true`, vault
`~/Documents/StefanBrain`) · `risk_settings.json` · `ntfy_config.json`.

---

## 2. Ce e stricat AZI — nu presupune că merge

Confirmate empiric în `.logs/orchestrator.log` (detalii + dovezi: `KAGE-EVALUARE.md` §1):

1. **Chat-ul principal dă 500 din 8 iunie** — `NameError: name 're' is not defined` la
   `orchestrator.py:929` (`re.search` pe `!save`); există doar importuri locale
   `import re as _re` (~372, ~960). Orice mesaj care nu e comandă instant și nu e cache hit
   → 500. (D1)
2. **`stream:false` e ignorat** — `chat_completions` returnează întotdeauna SSE; propriul
   gateway Telegram face `resp.json()` pe corp SSE → **botul nu a livrat niciodată un mesaj**
   (~22.000 `getUpdates`, zero `sendMessage` în log). (D2)
3. **`!run`/`!swarm` din UI sunt blocate implicit** — `kage.html` nu trimite `cwd`, serverul
   pune default `Path.home()`, care nu e în `allowed_task_roots` → `[BLOCKED]`. (D4)
4. **Streaming-ul T3–6 e simulat** — `claude -p` nu emite deltas fără
   `--include-partial-messages` (verificat pe CLI 2.1.173); totul vine la final; deadline fix
   120s ucide taskuri legitime. (D5)
5. **T4 și T6 sunt de facto inaccesibile** — `TIER_EXAMPLES` are chei doar {1,2,3,5};
   `!retry` plafonat la 5. (D10)
6. **`!run` pe Telegram nu pornește nimic** — prefixele agentice sunt interceptate doar în
   `kage.html`; serverul nu are handler. (D13)
7. Risk gate: axa 2 („instrucție explicită") e cod mort; High = refuz automat (nu aprobare);
   `>\s*/dev/null` clasifică `2>/dev/null` ca High. (D6)
8. Cele **28 de teste verzi nu ating stratul HTTP** — exact golul prin care a trecut D1. (D3)

---

## 3. Capcane pentru un model nou (îți vor păcăli intuiția)

- **Cache hit iese devreme** (~909), ÎNAINTE de INSERT-ul în SQLite (~946) → schimburile din
  cache nu ajung în istoric. Orice refactor al `chat_completions` trebuie să nu perpetueze asta.
- ~~**Cheia de cache = doar ultimul mesaj user**, fără context de conversație și cu prefixele
  incluse — follow-up-uri („continuă") pot primi răspunsul altei conversații.~~ **Rezolvat în
  WP4:** cache dezactivat pentru >1 tură user + prefixe curățate din cheie.
- `_background_task_exec` **hardcodează** `_log_usage(5, agent, ...)` și nu trimite `--model`
  — toate taskurile apar ca T5 în statistici, indiferent de agent.
- **Bugetul are trei ocoliri**: prefixe forțate, tot `/task/run`, și fallback-ul
  LiteLLM→T3 din `_route_litellm` (~2335). Nu „repara" una presupunând că restul sunt gate-ate.
- `!retry` citește ultimul tier din `status.json` **global** — stare partajată între sesiuni.
- `/chat` e exceptat de la auth și **servește token-ul API injectat în HTML** — token-ul e azi
  decorativ; contează abia la expunerea prin Cloudflare.
- Logger-ul `httpx` la INFO scrie **token-ul botului Telegram** în log la fiecare `getUpdates`.
- `_sleep_response` (~1954) folosește `os.system()` blocant în event loop.
- Cod mort care derutează: `_build_chat_html` (~215 linii, UI vechi — `/chat` servește
  `kage.html` din fișier), `UNCERTAINTY_PHRASES` (~140), `MULTI_TENANT_ARCH.md`.
- CLI-ul `claude` 2.1.173: `--include-partial-messages` (deltas), `--resume` (sesiuni) și
  `--permission-mode auto` **există și sunt valide** — verificate local. Evenimentul final
  `result` conține `total_cost_usd`.
- Testele importă `orchestrator` în siguranță (startup events nu rulează la import);
  `tests/conftest.py` resetează starea globală — păstrează proprietatea asta.
- ChromaDB e folosit de 3 colecții cu roluri diferite (`semantic_cache`, `tier_routing`,
  `long_term_memory`) — nu le confunda la vacuum/migrare.
- ~~**Tier 4 (`!gemini`, `gemini_cli` local) e STRICAT — testat empiric 13.07.2026**, cauză
  externă: Google a deprecat tier-ul gratuit „Gemini Code Assist for individuals" pe care se
  baza autentificarea CLI.~~ **RETRAS COMPLET (13.07.2026, decizia lui Stefan) — vezi WP-RMG
  în §5.** Gemini CLI eliminat din tot Kage (tier 4 din router ȘI backend-ul de agent
  `!run`/`!swarm`) — nu doar de router, oriunde. Advisorul (WP13) NU mai folosește Gemini deloc
  (nici CLI, nici OpenRouter) — vezi decizia revizuită din §4.

---

## 4. Decizii deja luate — NU le redeschide

- **Open WebUI = respins** (03.07.2026, decizia lui Stefan). Direcția UI: „Kage Mission
  Control" — frontend propriu Next.js + CopilotKit pe protocolul AG-UI, plus Arize Phoenix
  pentru observabilitate (pip install, fără Docker). Detalii: `KAGE-EVALUARE.md` §3.12, #15.
- **Voice intră în scope** (răstoarnă „Ce NU facem" din ROADMAP): whisper.cpp
  `large-v3-turbo` (română!), endpoint channel-agnostic `POST /v1/audio/transcriptions`;
  TTS: Piper ro_RO sau `say -v Ioana` (Kokoro NU are română); wake word: openWakeWord
  (`hey_jarvis` pre-antrenat). §3.11, #13/#14.
- **Skills = standardul Agent Skills** (SKILL.md, folder `skills/`), NU format YAML propriu —
  itemul #10 (recipes goose) e absorbit în #12. Auto-distilarea de skills (Hermes-style) vine
  DOAR după #4/#5 și DOAR ca draft + aprobare pe Telegram. §3.7–3.9.
- **T2 rămâne Qwen3.6-35B-A3B** (MoE, 3B activi, multimodal, română). Ornith-1.0-35B devine
  relevant abia când i se publică GGUF-ul, ca model specializat de cod (#9), după benchmark
  pe taskuri reale. §3.13.
- **Docker: nu pentru deploy, DA ca sandbox on-demand** (revizuit 04.07.2026 — Stefan are
  Docker instalat): Kage + Ollama rămân **native** (Qwen are nevoie de Metal — într-un VM
  pierde GPU-ul), dar agenții `!run` pot rula în containere pornite per-task, cu limite de
  memorie/rețea (WP-G2). Alternativa mai suplă, fără daemon rezident: `apple/container`.
- **ntfy.sh se retrage; Telegram = canal unic** (04.07.2026). Tailscale devine opțional;
  accesul remote la UI vine prin Cloudflare Tunnel odată cu WP10. Vezi WP1b.
- **Fără framework mare** (LangGraph/Letta ca dependențe) — se fură mecanismele: checkpoint
  (LangGraph), sleep-time compute (Letta), buclă CodeAgent (smolagents), trace schema
  (OpenAI SDK). §3, sinteza.
- Executorul țintă pentru agenți: **Claude Agent SDK** (#4); varianta minimală acceptată:
  `--include-partial-messages` + `--resume` + parsarea tool events din stream-json.
- Single-user by design: fără multi-tenant, fără enterprise features. Buget = feature de
  produs, nu de securitate.
- **Job hunter = career-ops + JobSpy, digest + draft la cerere, FĂRĂ auto-apply**
  (05.07.2026, decizia lui Stefan; auto-apply = risc de ban pe LinkedIn). Multi-profil
  (Stefan + tatăl lui) în **aceeași instanță** — nu contrazice single-user: tatăl nu e user
  al sistemului, e un profil de căutare; Stefan operează tot. Vezi WP-J.
- **Limite Claude Code: FĂRĂ rotație de conturi** (05.07.2026) — încalcă ToS Anthropic și
  riscă suspendarea tuturor conturilor. Strategia: auto-resume la ora de reset (parsată din
  mesajul de eroare) + retry periodic ca fallback + failover opțional pe API prin LiteLLM cu
  plafon de cost. Vezi WP11.
- **Modul handoff (plan → execuție nonstop) = WP11**, construit pe WP8 (run ledger) + WP9
  (Agent SDK) — NU executor paralel improvizat pe CLI; WP8/WP9 urcă în prioritate din cauza
  lui.
- **Trading agents (05.07.2026): paper-only, local-first, freqtrade + sports betting
  analitic + Manifold + OANDA practice** — promovarea pe bani reali e exclusiv manuală;
  la sports betting agentul nu plasează pariuri nici atunci (doar găsește, Stefan decide). **Polymarket real prin VPN =
  refuzat definitiv** (lista neagră ONJN, amenzi utilizatori, ToS → fonduri înghețabile);
  echivalentul legal de testare a edge-ului = Manifold play-money. Vezi WP-T.
- **Bugetul se afișează în EUR** (05.07.2026) — intern USD, conversie doar la afișare,
  `eur_usd_rate` static în config. Vezi #7.
- **Advisor-in-the-loop (09.07.2026, Stefan):** un al doilea model, cu **context minimal**
  (nu conversația orchestratorului — un advisor care vede tot raționamentul se ancorează în
  el), contrazice argumentat orchestratorul în punctele cheie: planul de misiune, diff-ul
  unui WP înainte de ✅, și „anti-rabbit-hole" (după N eșecuri: direcția asta poate
  funcționa?). **Consultativ, nu blocant** — dezacord persistent → escaladare la Stefan pe
  Telegram, niciodată deadlock model↔model. Vezi WP13.
- **Telecomandă prin Telegram (09.07.2026, Stefan):** direcția de utilizare principală devine
  „Stefan la lucru, laptopul acasă" — misiunile se creează, aprobă și revizuiesc de pe
  telefon (Telegram + GitHub mobile). Vezi WP12; atinge trigger-ul WP-G2 (rulări zilnice
  nesupravegheate).
- **Mod de execuție pe partea ML din WP-T (09.07.2026, Stefan):** proiectul e (și) material
  de CV pentru un rol în direcția AI, iar valoarea de CV = capacitatea de a-l APĂRA la
  interviu, nu codul. De aceea părțile cu valoare de interviu le implementează **Stefan,
  ghidat** — metoda: modelul scrie scheletul + testele (contractul), Stefan scrie corpul
  funcțiilor, review de senior după, explicația variantei alternative abia DUPĂ încercare
  (niciodată „privește-mă cum fac"). Ce implementează Stefan: **HMM-ul de regim de la zero**
  (numpy, EM, 2–3 stări — NU hmmlearn), **Dixon-Coles** (T2 sports), **purged CV +
  meta-labeling**, interpretarea rapoartelor de validare. Ce rămâne la model: plumbing
  (scheduler, endpoint-uri, config, Telegram, migrări, dashboard) — zero valoare de interviu.
  Regula generală: algoritmii standard = plug-and-play (sklearn/statsmodels/numpy), stratul
  de domeniu (features, etichetare, anti-leakage, evaluare) = de mână. Framing:
  neprofitabilitatea NU e eșecul proiectului, e rezultatul lui — „N din M ipoteze LLM erau
  zgomot, demonstrat cu validare deflatată" e povestea de interviu, nu „bot profitabil".
- **Executor Codex = direct varianta multi-executor post-G2 (12.07.2026, Stefan):** al doilea
  executor (Codex CLI / GPT-5.6) intră în Kage DOAR în forma cu valoare de CV —
  **multi-executor cu governance unificat la nivel de container** (deci după WP-G2), NU prin
  duplicarea matricei de risc pe hooks: Codex nu are echivalent PreToolUse (are propriul
  sandbox OS-level, Seatbelt), iar două modele de governance în paralel = stratul greșit +
  mentenanță dublă. **Declanșator: startul abonamentului ChatGPT Plus** (luna de probă Codex +
  GPT-5.6) — notează data aici când începe: `__.__.2026`; precondiție tehnică: WP-G2 livrat.
  Până atunci Codex se folosește doar MANUAL, de Stefan, în ferestrele de rate-limit Claude
  (cote necorelate). Povestea de interviu (§8): „orchestrator multi-executor cu governance
  executor-agnostic la nivel de container". Vezi WP-CX în §5.
  **Excepție Advisor (13.07.2026, Stefan):** regula de mai sus rămâne pentru rolul de AL
  DOILEA EXECUTOR (tool-uri de scriere, cere izolarea WP-G2). Rolul de **Advisor** (WP13) e
  read-only — citește plan/diff, întoarce obiecții text, nu scrie nimic — deci apelul
  programatic Codex→Advisor NU așteaptă WP-G2; poate porni imediat ce abonamentul ChatGPT
  există, ca WP mic separat (**WP13b**, după WP13). WP13 însuși rămâne pe Gemini 3 Flash —
  nu se amână după abonament.
- **Pista data-stack pentru CV (13.07.2026, Stefan):** țintă concretă — internship Python la
  Revolut, vara 2027 (aplicații din mai 2026, recrutare iul.–dec. 2026; stack JD: Python 3,
  SQL, PostgreSQL, Kafka, Airflow, Kubernetes, Docker, GCP, TDD; rolul = „data pipelines for
  reporting, analytics & data science" + „scalable APIs"). Decizii: (a) **prioritate ridicată**
  pentru **WP-PG → WP-ETL → WP-AF** (spec-uri în §5) + **amendamentul GCP la WP-G2** (§6) —
  fiecare rezolvă o durere reală a lui Kage (file-locks cross-proces, telemetrie fără
  raportare, cron-uri batch care mor cu procesul orchestratorului, izolarea agenților), deci
  NU e resume-driven development și fiecare e apărabil la interviu; (b) **GCP în loc de AWS**
  pentru partea cloud (JD-ul cere explicit GCP; skill-urile se transferă ~1:1) — felia = doar
  execuția efemeră a agenților pe Cloud Run, NU nucleul: Ollama/memoria/ChromaDB/chat history
  rămân locale, privacy by design; (c) **Kafka (WP-KF) AMÂNAT** — justificarea tehnică onestă
  (scriitori concurenți pe file-locks) dispare după WP-PG; intră doar după WP-ETL + WP-AF
  livrate, ca transport al pipeline-ului, nu gadget paralel; (d) **K8s NU se forțează în
  Kage** — cel mult un demo separat pe kind/k3d, în afara repo-ului. Părțile cu valoare de
  interviu se implementează în modul ghidat din §8 (rânduri noi în tabelul „viitoare").
- **Telecomandă v2: conversațional + self-development + agentic loop (13.07.2026, Stefan):**
  trei dureri declarate explicit după 3 zile de folosire a WP12. (a) **Prefixele `!` sunt
  prea complicate** — Stefan scrie ce vrea, un model decide din context ce trebuie făcut
  (intent router, WP-NL); prefixele rămân doar ca escape hatch determinist. (b) **Încă nu se
  poate lucra la Kage însuși de pe Telegram** — cauza e arhitecturală, nu de UX: misiunile
  rulează cu `cwd=PROJECT_ROOT` și `_mission_git_branch` comută branch-ul checkout-ului VIU,
  deci agentul ar edita fișierele din care rulează orchestratorul, iar pytest-ul misiunii ar
  concura cu producția. Soluția = izolare pe **git worktree** per misiune (WP-SD) — asta e
  restanța reală a promisiunii WP12 („construirea lui Kage prin Telegram"). (c) **Nu există
  încă un adevărat agentic loop** — WP11 execută un plan FIX secvențial; bucla completă
  plan → act → verify → reflect → **replan** + redirecționarea misiunii din mers cu text
  liber = WP-AL, construit pe advisorul + ask_user din WP13 (nu îl redeschide, îl continuă).
  Prioritate: WP-NL + WP-SD intră ÎNAINTEA pistei data-stack — sunt mici, sunt driver-ul
  zilnic, iar WP-SD e chiar unealta cu care WP-urile de date se pot livra de pe telefon.
- **Retragere Gemini CLI + revizuire Advisor (13.07.2026, Stefan):** testat empiric (§3) —
  Tier 4 (`!gemini`) e stricat, cauză externă (Google a deprecat „Gemini Code Assist for
  individuals"). Decizii: (a) **failover-ul plătit pe misiuni RETRAS din plan** — cazul lui
  real (rate-limit Claude mid-misiune) e deja acoperit gratuit de auto-resume-ul WP11
  (`_mission_schedule_resume`); (b) **Advisorul (WP13) NU folosește Gemini prin API** —
  implicit Qwen local (0€), Codex/ChatGPT rămâne upgrade opțional (WP13b) când apare
  abonamentul; „LLM council" pentru decizii importante e doar idee capturată, neangajată;
  (c) **Gemini CLI eliminat complet din Kage** — nu doar Tier 4 din router, ci și backend-ul
  de agent pentru `!run`/`!swarm` (aceeași cauză de bază: CLI-ul autentifică pe același cont
  mort). `!swarm` dispare odată cu el (premisa lui era „Claude + Gemini paralel" — fără al
  doilea backend nu mai are sens; se reintroduce doar dacă apare un backend real, ex. Codex
  după WP-CX). Spec de implementare: **WP-RMG** în §5.
- **Reconciliere cu propunerile Codex (14.07.2026, Stefan):** o sesiune Codex separată,
  lucrând în același clone, a produs independent `docs/CODEX-PROPUNERI.md` (registru G1–G6 +
  T1–T8) și `docs/REVOLUT-INTERNSHIP-ALIGNMENT.md` (maparea JD-ului Revolut). Al doilea
  document convergea ~90% cu decizia „pista data-stack" de mai sus — semnal bun. Din registrul
  general (`CODEX-PROPUNERI.md`), verdict per item:
  - **Acceptate, intră în lanț:** **R0** (Python/API quality — idempotency keys, pagination,
    rate limits, OpenAPI contracts, load test; nou WP în §5) — ieftin, atacă direct „scalable
    APIs" din JD, incremental pe endpoint-urile existente; **T3** (point-in-time data lineage)
    — integrat ca pas în **WP-ETL**, nu WP separat, fiindcă e literalmente ETL/data-quality
    deghizat, nu rigoare de trading; **G5** (observabilitate pe rezultate: success rate,
    approval rate, failure taxonomy) — integrat ca amendament la **WP10**, nu WP separat;
    **G1-minim** (KageBench redus la 10–15 taskuri fixe cu criterii de acceptare, ca
    regression gate) — nou WP în §5, sub forma minimă, NU registrul complet din propunere
    (eval harness generalizat, replay pe 3 executori); **G2/G6** — deja aliniate cu ce era
    decis (interfața comună de executor = premisa WP-CX; lista de amânat = deja scos `!swarm`
    la WP-RMG).
  - **Menținute ca `proposed`, NU intră în lanțul activ:** T1/T2/T4–T8 (DSL de ipoteze,
    holdout blocat, validation v2, cost models per piață etc.) — rigoare quant reală, dar
    JD-ul Revolut nu o cere; rămân în `CODEX-PROPUNERI.md` pentru când WP-T ajunge la forward
    shadow. G4 (workflow vertical manufacturing) — confirmat de Codex însuși ca pistă de
    startup, separată de nucleul CV.
  - **Respinsă explicit:** propunerea Codex de a condiționa **WP13 de KageBench** („A/B
    reviewer on/off înainte de acceptare") — Advisorul rulează pe Qwen local, cost 0; nu are
    sens să aștepte un proiect de evaluare întreg. Decizia „Advisor = Qwen local acum,
    Codex/ChatGPT ca upgrade opțional (WP13b)" din blocul de mai sus RĂMÂNE, nu devine „TBD
    după KageBench" cum propunea addendumul Codex găsit stashuit.
  - **Planul P0–P4 al lui Codex NU înlocuiește ordinea de mai jos** — ignora WP-NL/WP-SD/WP-AL
    (telecomanda v2), decise explicit de Stefan cu prioritate înaintea pistei de date; lanțul
    rămas e cel din secțiunea „Reordonare" de mai jos, cu R0 și G1-minim intercalate.
  - Implementarea concretă a itemilor acceptați trebuie să menționeze `Codex proposal: Gx`/`Tx`
    în commit/PR (regula de trasabilitate din `CODEX-PROPUNERI.md`).
- **Pista de învățare v2 — hibrid defend-first (15.07.2026, Stefan):** formatul v1 din §8
  (Explică/Apără/Extinde per subsistem + „Stefan scrie corpul" pe toate părțile ML din
  decizia „mod de execuție ML" de mai sus) s-a dovedit prea scump ca timp — cere sesiuni
  dedicate la laptop, iar scrisul de cod de mână e cea mai lentă formă de învățare pe minut
  investit; valoarea de interviu vine din apărarea deciziilor, nu din tastat. Decizii:
  (a) regimul de bază devine **defend-only** — modelul construiește tot, iar la închiderea
  fiecărui WP cu valoare de interviu generează o **fișă de interviu** (`docs/fise-interviu/`,
  ~1 pagină, citită în ~10 min); (b) singura alocare fixă de timp = **o sesiune de weekend
  de 2–3h** (Apără + un exercițiu practic); (c) **scrise de mână rămân DOAR piesele-fanion:
  HMM-ul de regim de la zero și Dixon-Coles** — restul vechii liste „Stefan, ghidat"
  (purged CV + meta-labeling, interpretarea rapoartelor) trece pe defend-only + Extinde mic;
  (d) nivelul „Explică" se pliază în „Apără" (fișa citită în prealabil înlocuiește
  descrierea de la zero). Acest bloc AMENDEAZĂ decizia „mod de execuție ML" (09.07.2026);
  mecanica operațională completă = §8 (rescris).

---

## 5. Pachetele de lucru, în ordinea execuției

Ordinea (din `KAGE-EVALUARE.md` §4, raționamentul acolo):
**WP1(#1) → WP1b(canale) → WP2(#2) → WP-G1(§6) → WP3(#3) → WP4(#8) → WP5(#11) →
WP-B(backup) → WP-J(jobs) → WP-D(briefing) → WP6(#13) → WP8(#5) → #7(buget EUR) →
WP9(#4) → WP11(handoff) → WP-T(trading) → WP-G2(§6) → WP10(#15B) + WP7(Phoenix)** →
apoi #12, #14, #6, #9.
(WP-B/WP-J/WP-D sunt independente — pot fi trase oricând după WP2. Reordonări
05.07.2026: #7 tras între WP8 și WP9 — agenții WP-J/WP11 ard bani nesupravegheați,
failover-ul API din WP11 nu are sens fără plafon; WP7 Phoenix amânat lângă WP10.)

### Reordonare completă (09.07.2026) — ordinea restului, de unde suntem acum

**Stare:** WP1–WP12, WP-G1, WP-B/J/D, WP6, WP-T (bucla de cercetare, integrată în scheduler)
și #7 (plafon EUR, 12.07.2026) = livrate. Ordinea de mai jos ÎNLOCUIEȘTE ordonările
anterioare pentru tot ce a rămas:

**#7(plafon EUR) → WP12(telecomandă Telegram) → T1-exec(daemon freqtrade dry-run) →
WP13(advisor + HITL v2) → WP-G2(izolare) → WP10(dashboard + WP7 Phoenix + tab Trading T5 +
Cloudflare Tunnel) → #6(memorie v2) → #12(skills) → #9(tools locale T2) →
WP-T T2(sports betting) → T3(Manifold) → T4(forex/OANDA) → evaluare
NDX → #14(voice push-to-talk) → RAG(înainte de ian. 2027) → audit modele(post-proiect).**
**WP-V (video intel)** e în afara lanțului — ✅ **Slice 1 livrat (12.07.2026)** (flux cost-0
cap-coadă); Slice 2 (pas vizual plătit + T5 adânc) e condiționat de #7 merge-uit. **§8 (pista
de învățare)** rulează în paralel.
**WP-CX (executor Codex — multi-executor cu governance pe containere)** e condiționat și în
afara lanțului: intră abia DUPĂ WP-G2 și startul abonamentului Codex (decizia, declanșatorul
și data startului în §4). Specul se scrie abia după luna de probă — nu-l detalia acum.

Raționament:

1. **#7 primul** — e cel mai mic item (cost_usd per run se colectează deja din WP9; lipsește
   doar plafonul-gate în EUR) și e precondiția de siguranță pentru orice rulare
   nesupravegheată. Spec-ul rămâne în §Restul.
2. **WP12 imediat** — valoarea imediată cerută de Stefan: lucrul de la birou prin Telegram cu
   laptopul acasă. Construiește doar pe WP11 (există), felie mică.
3. **T1-exec devreme** fiindcă e dependent de calendar, nu de efort: criteriul „~3 luni de
   paper profitabil" înseamnă că fiecare zi fără daemon amână discuția de bani reali cu o zi.
   Independent de restul — poate rula în paralel cu WP12/WP13.
4. **WP13 înainte de rulările zilnice nesupravegheate** — advisorul + reviewer pass sunt
   plasa de calitate pentru misiuni pe care Stefan nu le mai urmărește live; **acestea ating
   trigger-ul WP-G2** (declarat în §6: „rulări zilnice autonome nesupravegheate") → WP-G2
   urmează imediat.
5. **WP10 abia după** — dashboard-ul are valoare când există date de afișat (paper trades din
   T1-exec, misiuni din WP12/13); Cloudflare Tunnel intră aici (acces remote la UI).
6. **#6 → #12** în ordinea asta: auto-distilarea de skills consumă run ledger-ul prin
   memoria consolidată; ambele înaintea fazelor noi de piață WP-T (care sunt proiecte de
   conținut, nu de infrastructură).

#### Restanțe din faze „terminate" (nu le pierde — fiecare e alocată unui WP de mai sus)

- `_briefing_missions()` întoarce încă `None` (promis „după WP11") → cablare la tabelele
  `missions`/`mission_wps` în **WP12**.
- ~~Daemonul freqtrade dry-run (Bucla 1 execuție) + apelul `bias_allows` în `SampleStrategy`~~
  ✅ **T1-exec livrat** (15.07.2026, Codex CX1, PR #36): daemon paper-only + heartbeat/equity
  în `trading.db` + `SampleStrategy_example.py` cu `bias_allows` + scripturi start/stop +
  `docs/FREQTRADE-DRY-RUN.md`. **Ceasul de paper PORNIT: 16.07.2026** (daemonul rulează cu
  strategia cu gardă; două capcane prinse la prima pornire reală, ambele reparate: scriptul
  NU suprascrie o strategie stale din slice 2 fără `bias_allows` — verifică
  `grep bias_allows trading/ft_userdata/strategies/SampleStrategy.py`; și subprocess-ul
  freqtrade avea nevoie de PYTHONPATH pentru importul `trading.*`). Daemonul NU e persistent
  prin launchd în felia asta — după reboot se repornește manual; supravegherea kill-switch
  intră odată cu `trading.enabled=true`.
- ~~`.venv-py39` — de șters (3.12 rulează stabil din 06.07)~~ ✅ șters la #7 (12.07.2026).
- Fallback-ul LiteLLM→CLI încă pe subprocess (WP9, cale rară) → oportunist, nu blochează.
- ✅ WP6 voice: whisper-cpp + model GGML (`large-v3-turbo`, în `.models/`) **instalate și
  validate** (12.07.2026) — transcrierea locală merge; precondiția WP-V e satisfăcută. Rămâne
  doar `brew install yt-dlp` pentru extracția video (ffmpeg e deja instalat).
- WP-B: `vault_git_remote` de completat în config (pas manual Stefan) — push-ul nocturn al
  vault-ului e no-op până atunci.
- WP-J: setup career-ops + `jobs.enabled` (opt-in, pas manual Stefan), dacă nu e făcut deja.
- OANDA practice: depanarea token/endpoint (eșuată la prima încercare) → intră în **T4**.
- WP11 fază 2 (agent care întreabă singur + failover Gemini la limită) → absorbit în **WP13**.
- ~~#7 e doar parțial: colectarea `cost_usd` + afișarea EUR există; **plafonul-gate
  lipsește**.~~ ✅ (12.07.2026) plafonul-gate livrat (`api_budget.py`, vezi specul #7 în §Restul).

Prompt de pornire recomandat (copy-paste, înlocuiește N):
> Citește CLAUDE.md și KAGE-HANDOFF.md (§0–§4 integral + secțiunea pachetului: §5 pentru
> WP1–WP10, §6 pentru WP-G1/WP-G2), apoi implementează pachetul WP*N* exact cum e
> specificat, pe un branch nou din dev. Rulează pytest înainte și după. Nu atinge alte
> fișiere decât cele listate. La final raportează criteriile de acceptare unul câte unul și
> aplică pașii de housekeeping din §7.

### Reordonare (14.07.2026) — pista data-stack + itemii acceptați din Codex

**Stare la zi:** WP12, WP-RMG, #7 (Budget v2) și WP-V Slice 1 livrate. Decizia „pista
data-stack pentru CV" (13.07.2026) introduce 3 WP-uri noi cu prioritate + unul amânat
(spec-uri mai jos) și amendamentul GCP la WP-G2 (§6); decizia „telecomandă v2" (aceeași zi)
adaugă WP-NL/WP-SD/WP-AL; reconcilierea cu propunerile Codex (14.07.2026, §4) adaugă **R0**
și **G1-minim**, plus T3 integrat în WP-ETL și G5 integrat în WP10 (fără WP-uri noi pentru
ultimele două). Ordinea de mai jos ÎNLOCUIEȘTE reordonarea din 13.07 pentru tot ce a rămas:

**#7(✅ livrat) → T1-exec(paralel, dependent de calendar) → WP-NL(gateway conversațional,
fără prefixe) → WP-SD(self-development pe worktree) → R0(Python/API quality) →
WP-PG(PostgreSQL) → WP-ETL(pipeline analytics + T3 point-in-time lineage) →
WP-AF(Airflow batch) → WP13(advisor pe Qwen local + HITL v2) →
WP-AL(agentic loop: replan + steering) → WP-G2(izolare, acum dual-target: Docker local +
GCP Cloud Run) → G1-minim(KageBench redus, regression gate) → WP10(dashboard + G5
observabilitate — consumă mart-urile din WP-ETL) → **Kage Terminal** (desktop macOS + DMG,
opțional, numai după ce UI-ul și startup-ul sunt stabile) → #6(memorie v2) → #12
(skills) → #9(tools locale T2) → WP-T T2(sports) → T3-Quant(Manifold) → T4(forex) → evaluare
NDX → #14(voice push-to-talk) → RAG(înainte de ian. 2027) → audit modele.**

(Notă: „T3" apare de două ori cu sensuri diferite — T3 point-in-time lineage din
`CODEX-PROPUNERI.md`, integrat în WP-ETL, vs. T3-Quant = piața Manifold din WP-T, neschimbată.)

**WP-KF (Kafka) — AMÂNAT deliberat**, în afara lanțului: intră doar după WP-ETL + WP-AF
livrate (decizia din §4). WP-V Slice 2 (condiționat de #7, acum livrat) și WP-CX (condiționat
de WP-G2 + abonament Codex) rămân în afara lanțului, neschimbate. **WP13b (Advisor →
Codex/ChatGPT)** e în afara lanțului și el — condiționat de abonamentul ChatGPT Plus, intră
oricând după WP13, nu blochează nimic din ordinea de mai sus (§4, excepția Advisor la regula
WP-CX). Failover-ul plătit pe misiuni a fost RETRAS din plan (§4, 13.07.2026) — acoperit deja
gratuit de auto-resume-ul WP11 la rate-limit. Restul propunerilor Codex (T1/T2/T4–T8 din
`CODEX-PROPUNERI.md`) rămân `proposed`, în afara lanțului. §8 rulează în paralel.

Raționament:

1. **Fereastra de recrutare Revolut e iul.–dec. 2026** — ca la T1-exec, pista e dependentă
   de calendar, nu doar de efort: WP-PG/WP-ETL/WP-AF trebuie să fie LIVRATE și APĂRABILE
   (§8) înainte de interviuri, altfel rămân „currently learning" în CV.
2. **#7 rămâne primul** — nimic din pistă nu-l blochează; rămâne precondiția de siguranță
   pentru orice rulare nesupravegheată, inclusiv cele de pe Cloud Run.
3. **WP-NL + WP-SD imediat după #7** — același argument ca la WP12 în 09.07 („valoarea
   imediată cerută de Stefan"): sunt driver-ul zilnic, felii mici, iar WP-SD e chiar
   unealta cu care restul lanțului se livrează de pe telefon — dogfooding: prima misiune
   reală pe worktree poate fi chiar WP-PG.
4. **R0 chiar înainte de WP-PG** — API quality (idempotency, pagination, rate limits,
   contracts) e ieftin și incremental pe endpoint-urile deja existente; făcut acum, WP-PG/
   WP-ETL construiesc peste API-uri deja curate în loc să moștenească datoria tehnică.
5. **WP-PG înaintea pistei de date** — e fundația: WP-ETL scrie în el, WP-AF își ține
   metadata în el, WP10 citește din el; și stinge durerea reală a lock-urilor pe fișiere
   cross-proces.
6. **T3 (lineage) intră ÎN WP-ETL, nu ca WP separat** — punctul lui de plecare (`event_time`,
   `available_time`, checksum, dataset snapshot) e SQL peste tabelele deja proiectate la
   WP-PG; separarea ar fi dublat munca de schema design fără beneficiu.
7. **WP13 alunecă după pista de date, WP-AL imediat după WP13** — până la ele, misiunile
   rulează ca azi (supravegheate prin Telegram); WP-AL e construit explicit pe piesele
   WP13 (advisor, `ask_user`, coadă), nu are sens înaintea lui; trigger-ul WP-G2 („rulări
   zilnice nesupravegheate") se atinge tot după WP13/WP-AL, deci ordinea relativă față de
   WP-G2 nu se schimbă.
8. **G1-minim după WP-G2, înaintea WP10** — regression gate-ul are nevoie de o formă de
   izolare de execuție ca să ruleze taskuri repetabile fără să atingă producția; poziția
   imediat înainte de WP10 lasă dashboard-ul să afișeze din prima zi și metricile de eval
   (G5), nu doar telemetria brută.
9. **WP10 câștigă din amânare** — dashboard-ul se construiește direct pe mart-urile din
   WP-ETL (plus metricile G1/G5), nu pe query-uri ad-hoc; argumentul din 09.07 („valoare
   când există date") se întărește.

### WP1 (#1) — Reparația fundației ✅ (04.07.2026) · efort: o seară–un weekend

**Fișiere:** `orchestrator.py`, `tests/test_e2e.py` (nou).
**Pași:**
1. `import re` global; șterge cele două `import re as _re` locale (~372, ~960), înlocuiește
   `_re.` → `re.`.
2. Ramură non-stream în `chat_completions`: dacă `body.get("stream") is False`, consumă
   generatorul intern și returnează JSON OpenAI standard
   (`{"choices":[{"message":{"role":"assistant","content":...}}],...}`).
3. Handler server-side pentru `!run|!swarm|!sysrun` în `chat_completions`, ÎNAINTE de cache:
   deleagă la logica din `task_run()` și răspunde cu confirmare + task id (streamul complet al
   taskului către Telegram vine abia la WP8 — aici doar pornirea corectă).
4. `tests/test_e2e.py`: `fastapi.testclient.TestClient` + `respx` (adaugă în
   `requirements-dev.txt`) cu mock pe LiteLLM/Ollama — un test POST normal → 200 + SSE
   parsabil; unul cu `stream:false` → schemă JSON validă; unul `!run` → task pornit.
5. `@app.exception_handler(Exception)` → `_notify("💥 Eroare internă", ...)` + log.
6. `logging.getLogger("httpx").setLevel(logging.WARNING)` — scoate token-ul din log.

**Acceptare:** pytest verde (28 + noile e2e) · curl non-stream din §0 returnează JSON valid ·
curl SSE streamează · un mesaj de pe Telegram primește răspuns real (test manual cu userul) ·
`!run pwd` de pe Telegram întoarce confirmare cu task id · liniile noi de log nu conțin
token-ul botului.

### WP1b — Consolidarea canalelor: Telegram unic, retragerea ntfy ✅ (04.07.2026) · efort: o seară · după verificarea WP1

**Decizie (04.07.2026, Stefan):** ntfy.sh se retrage — Telegram devine canalul unic de
notificări + aprobări. Tailscale devine opțional: cu Telegram ca singur canal remote,
suprafața inbound e **zero** (gateway-ul face doar polling outbound); accesul remote la UI
revine abia cu Mission Control (WP10), prin Cloudflare Tunnel (ROADMAP), nu prin Tailscale.
**Precondiție:** WP1 verificat manual — azi Telegram nu a livrat niciodată un mesaj (D2);
nu opri ntfy înainte să existe canalul de schimb.
**Pași:** (1) rutează `_notify()` prin gateway-ul Telegram (ntfy rămâne doar fallback dacă
gateway-ul e neconfigurat); (2) mută cheile încă citite din `ntfy_config.json` (`max_cloud`,
topic) în `kage_config.json`; (3) scoate linkurile cu IP-ul Tailscale din notificări;
(4) aprobările de risc: fluxul inline Telegram devine calea primară.
**Acceptare:** o alertă de buget și o aprobare de risc ajung pe Telegram · nimic nu mai
scrie către ntfy.sh · pytest verde.

### WP2 (#2) — Risk gate v2 + confinement funcțional ✅ (04.07.2026) · efort: mic

**Fișiere:** `risk_hook.py`, `risk_settings.json`, `orchestrator.py`, `kage.html`,
`tests/test_risk.py` (nou).
**Pași:**
1. `risk_hook.py` `main()`: mută **High** în fluxul de aprobare (același mecanism ca Medium:
   register + ntfy/Telegram + poll, timeout → deny). Never rămâne refuz direct.
2. Implementează axa 2: orchestratorul pune mesajul userului în env
   (`ORCHESTRATOR_USER_MSG`) la spawn-ul agenților (`_background_task_exec`,
   `_route_claude_autonomous`); hook-ul face downgrade High→Medium dacă un
   `EXPLICIT_KEYWORD` relevant apare în el.
3. Curăță pattern-urile: scoate `>\s*/dev/null` din HIGH; mută `git rebase`/`--amend` din
   Never în High.
4. `risk_settings.json`: matcher `"Bash|Write|Edit"` → extins să prindă și MCP
   (`"Bash|Write|Edit|mcp__.*"` sau `"*"`) — azi tool-urile MCP ocolesc gate-ul.
5. `orchestrator.py`: `GET /api/config` (expune `allowed_task_roots`); default `cwd` în
   `task_run` = primul element din `ALLOWED_TASK_ROOTS` (nu `Path.home()`).
6. `kage.html`: dropdown de cwd la task runner, populat din `/api/config`.

**Acceptare:** `tests/test_risk.py` acoperă: `echo x 2>/dev/null` NU e High · comandă High →
intră în flux de aprobare (mock), nu deny · keyword explicit → downgrade · `!run` din UI fără
cwd explicit pornește (nu `[BLOCKED]`) · pytest verde.

### WP3 (#3) — Router cu feedback loop ✅ (05.07.2026) · efort: o seară–un weekend

**Fișiere:** `orchestrator.py` (`decide_tier`, `_semantic_classify`), `tests/test_routing.py`.
**Pași:** (1) la `!retry`/prefix forțat, adaugă mesajul cu tier-ul corectat în colecția
`tier_routing` cu `metadata={"source":"feedback"}`; (2) `_semantic_classify`: `n_results=5`,
vot ponderat cu similaritatea (azi 1-NN); (3) plafonează colecția (vacuum la N exemple/tier);
(4) prefixe noi `!opus` (T6) / `!gemini` (T4) + `!retry` plafonat la 6, nu 5; (5) exemple seed
pentru T4/T6 în `TIER_EXAMPLES`.
**Acceptare:** teste: după un override `!best` pe un mesaj, un mesaj similar se rutează T5 cu
metoda `sem` · `!opus`/`!gemini` forțează corect · colecția nu crește nelimitat · pytest verde.

**Implementat (05.07.2026):**

- (1) `_record_routing_feedback(msg, tier)` — curăță prefixele (`_strip_routing_prefixes`),
  embed-uiește și stochează în `tier_routing` cu `{"tier":N,"source":"feedback","ts":...}`.
  Declanșat non-blocant (`asyncio.create_task`) din `chat_completions` când
  `forced and routing_method == "forced"` (deci `!fast/!best/!opus/!gemini/!retry/escaladează`,
  NU `!plan` care păstrează tier-ul clasificatorului).
- (2) `_semantic_classify` — k-NN cu `n_results=min(5, count)`, vot ponderat cu similaritatea
  peste pragul 0.6; tier = argmax al sumei de similarități, confidence = cel mai bun vecin al
  tier-ului câștigător. (Bătea 1-NN: 3 vecini slabi corecți înving 1 vecin puternic greșit.)
- (3) `_routing_vacuum()` — plafon `MAX_FEEDBACK_PER_TIER` (default 50, config
  `max_routing_feedback_per_tier`); păstrează cele mai noi per tier, seed-urile intacte.
  Rulat după fiecare feedback.
- (4) `!opus`→T6, `!gemini`→T4 în `decide_tier`; `!retry` acum `min(last+1, 6)`. Text `!help`
  actualizat. (Chip-urile UI: doar în `_build_chat_html`, cod mort — kage.html e în afara
  scope-ului WP3, neatins.)
- (5) `TIER_EXAMPLES[4]` și `[6]` adăugate; seeding refăcut idempotent per-tier
  (`_seed_routing_examples`) — T4/T6 se seamănă la restart chiar pe colecția existentă.

Baseline teste: 69 → **79 verzi**. **Necesită restart** (`start_all.sh`) ca T4/T6 să fie
seed-uite și codul nou să ruleze.

### WP4 (#8) — Cache v2 context-aware ✅ (05.07.2026) · efort: ~2 ore

**Fișiere:** `orchestrator.py` (`chat_completions`), teste noi.
**Pași:** (1) sari peste cache (lookup ȘI store) dacă conversația are >1 tură de user;
(2) nu stoca răspunsuri la mesaje cu referenți temporali (regex `azi|acum|mâine|ieri|astăzi`);
(3) curăță prefixele (`!best` etc.) din `cache_query` înainte de embedding.
**Acceptare:** teste pentru fiecare din cele 3 comportamente; pytest verde.

**Implementat (05.07.2026):**

- `_cache_policy(messages, last_user)` → `(use_cache, store_ok, cache_query)`, apelat în
  `_chat_dispatch`. Un singur punct de decizie, pur → testabil fără stratul HTTP.
- (1) `use_cache=False` când `sum(role=="user") > 1` — follow-up-uri sar și lookup și store
  (rezolvă capcana §3: cheia = doar ultimul mesaj → „continuă" putea primi alt răspuns).
- (2) `store_ok=False` dacă `_TEMPORAL_RE` (`azi|acum|mâine|ieri|astăzi` + variante fără
  diacritice) prinde în mesaj — lookup încă permis, dar nu se creează intrări noi stale.
- (3) `_clean_cache_query` scoate prefixele (`_CACHE_PREFIX_RE`) + `escaladează`, lowercase,
  colapsează spațiile → `!best explică X` și `explică X` au aceeași cheie.
- Store în `history_caching_gen` gated pe `store_ok` (era `use_cache`).
- Teste: `tests/test_cache.py` (14 teste, câte ≥1 per comportament). Baseline 79 → **93 verzi**.

### WP5 (#11) — Igienă de repo ✅ (05.07.2026) · efort: o seară

**Pași:** persona (`_STEFAN_BASE`, `project_map` din `_get_obsidian_context`,
`TIER_EXAMPLES` personale) → `kage_config.json`/vault cu default generic; șterge
`_build_chat_html`, `UNCERTAINTY_PHRASES`, `MULTI_TENANT_ARCH.md`; unifică cele două căi de
„add scheduled task"; `usage_log` → SQLite sau rotație lunară (repară și cititul integral la
fiecare poll de 10s).
**Acceptare:** grep fără date personale hardcodate în `.py` · pytest verde · dashboard
funcțional după mutarea usage.

**Implementat (05.07.2026):**

- **Persona externalizată** din cod în `kage_config.json` (real, gitignored) cu default
  generic în cod: `_PERSONA_BASE`/`_PERSONA_TIER3_EXTRA` (`persona_base`/`persona_tier3_extra`),
  `PROFILE_FILES` (`profile_files`), `PROJECT_MAP` (`project_map` — căi relative la vault),
  `TIER_EXAMPLES` tier-2 genericizat + `tier_examples_extra` merge-uit din config. `_STEFAN_BASE`
  eliminat. Label context „Obsidian StefanBrain" → „vault". Chei documentate în
  `kage_config.example.json` (`_comment_persona`).
- **Cod mort șters:** `_build_chat_html` (215 linii), `UNCERTAINTY_PHRASES`. `MULTI_TENANT_ARCH.md`
  fusese deja șters la reorganizarea repo.
- **Scheduled task unificat:** `_persist_new_task(cron, message, tier_override)` — cale unică
  (validare cron + persistă + `add_job`), folosită de `!schedule` (`_handle_schedule_command`)
  și de `POST /schedule` action=add. Ramura add scoasă din blocul de `FileLock` ca să nu
  achiziționeze lock-ul de două ori.
- **usage_log → SQLite:** tabel `usage` în `chat_history.db` (index pe `ts`).
  `_log_usage` face INSERT; `_usage_counts_today`/`_aggregate_usage`/`/health` interoghează doar
  ziua curentă (`ts >= today AND ts < tomorrow`) în loc să citească fișierul integral la fiecare
  poll de 10s. `_backfill_usage_from_jsonl` importă o singură dată `usage_log.jsonl` legacy
  (idempotent — doar dacă tabelul e gol); fișierul rămâne pe disc ca arhivă. Footer dashboard
  actualizat.
- Teste: `test_budget.py` rescris pe SQLite (`_usage_db` in-memory) + test nou `_log_usage`.
  Baseline 93 → **95 verzi**. Verificat runtime: backfill + aggregate + dashboard render.
  **Necesită restart** (`start_all.sh`) ca persona din config + tabelul usage să fie active.

### WP-B — Backup off-machine ✅ (05.07.2026) · efort: o seară · oricând după WP-G1 (recomandat cât mai devreme)

Tot sistemul trăiește pe un singur laptop — disc mort/furt = pierzi Kage + memoria +
istoricul + vault-ul. Două destinații, pe naturi diferite de date:

1. **Vault StefanBrain (git, WP-G1) → GitHub privat:** adaugă remote + `git push` în jobul
   nocturn de commit (03:00). **NU** sincroniza repo-uri git prin iCloud/Drive — sync-ul de
   fișiere corupe `.git`.
2. **Arhivele de backup (tar.gz din `_backup_cache_db()`) → iCloud Drive:** după creare,
   copiază arhiva în `~/Library/Mobile Documents/com~apple~CloudDocs/KageBackups/` cu
   aceeași rotație `backup_keep`. Zero configurare (macOS sincronizează singur), iar
   arhivele binare nu au ce căuta pe GitHub. Echivalent acceptat: rclone → Google Drive
   (15GB pe contul gmail) — doar dacă iCloud-ul e plin.
3. **Config complet în arhivă:** include `kage_config.json` în tar.gz (restore complet
   dintr-un singur fișier). Token-urile ajung astfel DOAR în iCloud, niciodată în git.

**Acceptare:** commit-ul nocturn face push pe remote · arhiva apare în folderul iCloud după
backup · restore testat dintr-o arhivă luată din iCloud · grep fără token în repo-ul remote.

**Implementat (05.07.2026):**

- **Vault → push (1):** `_vault_git_push(git_fn)` apelat din `_vault_git_commit` după commit
  (și când „nimic de comis", pentru commit-uri locale ne-împinse). Sincronizează `origin` cu
  `vault_git_remote` din config (add/set-url), `push -u origin <branch>`. No-op dacă remote gol;
  push eșuat (offline/auth) e grațios — nu pică jobul nocturn. Sufix de stare (`· push ok/eșuat`).
- **Arhive → iCloud (2):** `_copy_backup_to_icloud(archive)` apelat la finalul `_backup_cache_db`,
  copiază în `icloud_backup_dir` (default `~/Library/Mobile Documents/com~apple~CloudDocs/KageBackups`)
  cu aceeași rotație `backup_keep`. Sare grațios dacă baza CloudDocs nu există (mașină fără iCloud);
  gol/absent = dezactivat.
- **Config în arhivă (3):** `_backup_cache_db` include `kage_config.json` la rădăcina tar.gz
  (`backup_include_config`, default true) → restore complet dintr-un fișier. Token-urile ajung
  DOAR în arhivă (iCloud), niciodată în git. `_restore_cache_db` restaurează doar `cache_db/`;
  config-ul rămâne pas manual (RESTORE.md §2b).
- Config nou (real + example): `vault_git_remote`, `icloud_backup_dir`, `backup_include_config`.
  RESTORE.md: secțiune §0 (backup off-machine) + §2b (restore config din arhivă).
- Teste: `test_vault_git.py` (+3: push pe bare repo, no-push fără remote, push eșuat grațios),
  `test_backup.py` (+3: config în arhivă, copie iCloud, iCloud dezactivat), `test_restore.py`
  (+1: arhiva cu config restaurează cache_db normal). **Plasă de siguranță în conftest:**
  `ICLOUD_BACKUP_DIR=None` implicit — testele de backup nu scriu în iCloud-ul real (bug de
  izolare prins la review runtime). Baseline 95 → **102 verzi**. Verificat runtime: backup real
  → arhivă în iCloud cu config + restore din arhiva iCloud (primul backup off-machine creat).
- **Pas rămas pentru Stefan:** completează `vault_git_remote` în `kage_config.json` cu URL-ul
  repo-ului privat GitHub al vault-ului (SSH sau HTTPS cu credential helper) ca push-ul nocturn
  să funcționeze. Vault-ul se `git init`-ează automat la jobul de 03:00 (WP-G1).

### WP-J — Job hunter multi-profil (career-ops + JobSpy) ✅ (05.07.2026) · efort: un weekend · după WP2, independent de restul

**Implementat (05.07.2026):** pipeline complet în cod. Rafinare față de plan: career-ops
NU rulează în scanul automat (ar arde budget cloud la fiecare scan de 2×/zi) — scanul face
DOAR scan→dedup→pre-filtru T2 local→digest (cost cloud zero, cum cere acceptarea). Evaluarea
career-ops + CV tailoring pornește la butonul ✍️, on-demand, confinată la workspace-ul
profilului. Piese: `job_scan.py` (standalone, `.jobs-venv` 3.12) · tabel `jobs` în
`chat_history.db` · secțiunea „Job hunter" din `orchestrator.py` (pipeline + endpoint-uri
`/jobs/scan|action|apply`) · `send_job_card`/callback `job:` în `telegram_gateway.py` ·
comanda `!scan [profil]` · config `jobs` (opt-in, `enabled:false` implicit) · `tests/test_jobs.py`
(13 teste). **Setup necesar înainte de folosire:** `scripts/setup.sh` creează `.jobs-venv` +
jobspy; clonează [santifer/career-ops](https://github.com/santifer/career-ops) în
`~/career-ops/{stefan,tata}/` cu CV+context; adaugă acele workspace-uri în `allowed_task_roots`;
pune `jobs.enabled:true` + token Telegram. Fără setup, `!scan` degradează grațios (mesaj clar).

**Decizie (05.07.2026):** digest + draft la cerere, FĂRĂ auto-apply (§4). Două profiluri:
Stefan (student CS, QA intern) + tatăl lui (project manager, non-tech — Stefan operează tot,
el primește doar anunțurile relevante).

**Componente:**

1. **career-ops** ([santifer/career-ops](https://github.com/santifer/career-ops), MIT,
   activ) = stația de evaluare + CV tailoring. Rulează în Claude Code CLI → se spawnează cu
   infrastructura `!run` existentă, zero integrare nouă de executor. Un workspace per profil
   (`~/career-ops/{stefan,tata}/`) cu CV + context + criterii; workspace-urile intră în
   `allowed_task_roots`.
2. **Discovery: `python-jobspy`** (LinkedIn/Indeed/Glassdoor/Google Jobs). **Cere Python
   ≥3.10** → script separat `job_scan.py` într-un venv 3.12 (patternul `.widget-venv`),
   apelat prin subprocess dintr-un job APScheduler (2×/zi). career-ops aduce în plus
   watchlist-ul lui de companii + provideri ATS (Greenhouse/Lever/Ashby). Piața RO
   (eJobs/BestJobs) NU e în JobSpy — LinkedIn acoperă ambele profiluri la start; scraper
   dedicat = extensie ulterioară, nu în scope.
3. **Prompt injection (obligatoriu):** descrierile de joburi = conținut web complet
   ne-de-încredere care intră într-un agent cu tools — un anunț malițios poate conține
   instrucțiuni pentru agent (amenințarea din §6). Mitigare: rulările career-ops sunt
   confinate la workspace-ul profilului (`allowed_task_roots`) cu policy read-only în rest
   (WP-G1) și fără acces la rețea în afara pașilor de scan; textul scanat e tratat ca date,
   niciodată concatenat ca instrucțiuni de sistem.
4. **Pipeline:** scan → dedup (tabel `jobs` în `chat_history.db`, cheie hash
   titlu+companie) → **pre-filtru ieftin pe T2 local** (scor 1–10 față de profil; doar top-N
   merg mai departe — evaluarea career-ops arde budget cloud, gate pe budgetul existent) →
   evaluare career-ops → digest Telegram per profil, prefixat (`👔 Stefan:` /
   `👨‍💼 Tata:`), cu butoane inline: 🔖 salvează · ✍️ pregătește aplicația (career-ops
   generează CV-ul adaptat în workspace) · 🗑 ignoră (intră în dedup permanent).

**Acceptare:** un scan manual produce digest pe Telegram cu joburi reale scorate pentru
fiecare profil · al doilea scan consecutiv nu re-trimite aceleași joburi · ✍️ produce un
draft de CV adaptat în workspace-ul corect · pre-filtrul local nu consumă budget cloud ·
pytest verde.

### WP-D — Briefing zilnic pe Telegram ✅ (06.07.2026) · efort: o seară · după WP-J

Job APScheduler la 08:00 → un singur mesaj compus: joburile noi de peste noapte (WP-J, per
profil), starea misiunilor (după WP11), budgetul zilei, taskurile programate azi; opțional o
secțiune „azi din vault". Compunere pe T2 local — **zero cost cloud**. Fiecare secțiune
degradează grațios dacă sursa ei nu există încă (briefing-ul merge și înainte de WP11).
Comandă manuală: `!briefing`.

**Acceptare:** briefing-ul sosește la 08:00 cu secțiunile disponibile · `!briefing` îl
generează la cerere · zero apeluri cloud la compunere · pytest verde.

**Implementat (06.07.2026):**

- **Fapte asamblate determinist, intro pe T2 local.** Interpretarea lui „compunere pe T2":
  faptele (joburi, buget, taskuri, vault) sunt culese determinist în `_briefing_gather()` (pur,
  fără LLM/rețea — testabil izolat) ca modelul să nu stâlcească titluri/URL-uri/cifre; T2 local
  (`TIER_MODELS[2]`) scrie DOAR propoziția de intro (`_briefing_intro`). Zero apeluri cloud —
  santinelă în test (`_route_claude_autonomous` aruncă dacă e atins). `intro_llm:false` în config
  → intro static, fără niciun apel de model.
- **Secțiuni, fiecare cu degradare grațioasă:** `_briefing_new_jobs()` (joburi status
  sent/saved/applied din ultimele 24h, grupate pe profil — `{}` fără tabel/db), missions
  (`_briefing_missions()` → `None` până la WP11), buget (mereu prezent, din `_usage_counts_today`),
  `_briefing_scheduled_today()` (taskuri care se declanșează azi, calculat cu
  `CronTrigger.from_crontab` + `get_next_fire_time`), `_briefing_vault_today()` (extras din nota
  zilnică `{YYYY-MM-DD}.md` din vault, dacă există).
- **Două randări din aceleași date** (`_briefing_render(data, intro, html=)`): HTML pentru push-ul
  proactiv (`_send_briefing` → `_tg_gateway.send`, parse_mode HTML, dinamicele escape-uite) și
  markdown/plain pentru răspunsul comenzii `!briefing` (`_handle_briefing_command` → SSE), care e
  trecut prin `_escape` de gateway exact ca `!status`/`!help`.
- **Scheduler:** job `__briefing__` la `BRIEFING_CRON` (default `0 8 * * *`), gated pe
  `BRIEFING_ENABLED` (default true; no-op fără gateway Telegram). Config nou (real + example):
  bloc `briefing` (`enabled`, `cron`, `intro_llm`, `vault_section`, `vault_daily_dir`).
- Timeout intro T2 = 90s (35B rece la 08:00 ia ~50s la primul token — verificat runtime:
  „Bună dimineața, sunt Kage și îți doresc o zi liniștită și plină de realizări.").
- Teste: `tests/test_briefing.py` (20 teste — secțiuni, degradare, intro T2/fallback,
  randare md/HTML+escaping, zero-cloud, comandă + push). Baseline 122 → **142 verzi**.
  **Necesită restart** (`start_all.sh`) ca jobul de briefing + comanda `!briefing` să fie active.

### WP6 (#13) — Voice memos pe Telegram · efort: o seară–un weekend · depinde de WP1 · ✅ IMPLEMENTAT (06.07.2026)

**Fișiere:** `orchestrator.py`, `telegram_gateway.py`, `kage_config.json[.example]`.
**Pași:** (1) endpoint `POST /v1/audio/transcriptions` (OpenAI-compatible) — subprocess
whisper.cpp cu `large-v3-turbo` (instalare: `brew install whisper-cpp` sau build; modelul
~1,6GB, descărcat separat); config `whisper_bin`, `whisper_model`; (2) în gateway: ramură
pentru `message.voice` → `getFile` → download OGG → transcrie → intră în pipeline-ul normal,
cu reply „📝 Am înțeles: …"; (3) test cu fixture audio scurt.
**Acceptare:** voice memo în română pe Telegram → răspuns text corect (test manual) · endpoint
testat cu fixture · transcrierea rulează 100% local.

**Implementat:**

- Config `whisper` în `kage_config.example.json`: `bin` (default `whisper-cli`, rezolvat prin
  PATH sau cale absolută), `model` (cale GGML, gol = dezactivat), `language` (default `ro`).
- `orchestrator.py`: `_resolve_whisper_bin()`, `_transcribe_audio()` (temp file → ffmpeg
  OGG→WAV 16kHz mono dacă e disponibil → `whisper-cli -nt -np`, stdout = text; timeout 300s),
  clasa `_WhisperUnavailable` și endpoint `POST /v1/audio/transcriptions` (multipart `file`,
  OpenAI-compatible, întoarce `{"text": …}`; **503** dacă whisper/model lipsesc — degradare grațioasă).
- `telegram_gateway.py`: ramură `voice`/`audio` în `_dispatch` → `_handle_voice()`
  (getFile → download OGG → POST la endpoint cu auth → reply `📝 Am înțeles: …` → `_forward_to_orchestrator`).
  503 → mesaj „transcrierea vocală nu e configurată". `/help` menționează mesajele vocale.
- Teste noi: `tests/test_voice.py` (12) — rezolvare binar, degradare (bin/model lipsă),
  transcriere cu/fără ffmpeg, returncode ≠ 0, endpoint (400 lipsă/gol, 503 neinstalat, 200 happy).
  Suită totală: 154 verzi.
- **Setup necesar** (opt-in, nu e făcut încă): `brew install whisper-cpp ffmpeg`, descarcă modelul
  GGML (large-v3-turbo ~1,6GB) și setează `whisper.model` în `kage_config.json`. Fără el, endpoint-ul
  dă 503 și Telegram anunță că nu e configurat — restul sistemului nu e afectat.
  **Necesită restart** (`start_all.sh`) ca ramura de voce din gateway + endpoint-ul să fie active.

### WP7 (#15A) — Phoenix peste LiteLLM · efort: câteva seri · AMÂNAT (05.07.2026): se face împreună cu WP10

Motiv: observabilitatea de zi cu zi vine din run ledger (WP8); Phoenix devine valoros abia
cu trafic serios de agenți + Mission Control.

**Pași:** `pip install arize-phoenix` (venv separat dacă 3.9 face probleme — Phoenix poate
cere 3.10+; atunci rulează-l standalone: `phoenix serve`); callback Phoenix în
`litellm_config.yaml` ([integrare nativă](https://docs.litellm.ai/docs/observability/phoenix_integration));
adaugă în `start_all.sh`.
**Acceptare:** un request T1/T2 apare ca trace în UI-ul Phoenix (`localhost:6006`).

### WP8 (#5) — Run ledger + decision trace · efort: 1–2 săptămâni de seri · ✅ IMPLEMENTAT (06.07.2026)

Coloana vertebrală pentru #4, #15B și auto-skills. Schemă propusă (SQLite, în
`chat_history.db`):

```sql
CREATE TABLE runs (
  id TEXT PRIMARY KEY,              -- uuid4
  kind TEXT NOT NULL,               -- chat | task | scheduled
  session_id TEXT, channel TEXT,    -- ui | telegram | api | cron
  input TEXT, tier INTEGER, model TEXT,
  routing_method TEXT, routing_confidence REAL, routing_neighbor TEXT,
  cache_hit INTEGER DEFAULT 0, budget_state TEXT,
  status TEXT NOT NULL,             -- running|done|failed|timeout|blocked|pending_approval
  cost_usd REAL, duration_ms INTEGER,
  created_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(id),
  ts TEXT NOT NULL,
  type TEXT NOT NULL,               -- routing|cache|memory|budget|llm_delta|tool_call|
                                    -- tool_result|approval_request|approval_response|
                                    -- error|result
  payload TEXT                      -- JSON, trunchiat la ~4KB
);
CREATE INDEX idx_events_run ON run_events(run_id);
```

**Pași:** (1) helpers `_run_start/_run_event/_run_end`; (2) instrumentează `decide_tier`,
`_cache_lookup`, `_memory_retrieve`, `_budget_check`, `_background_task_exec`,
`_route_claude_autonomous`; (3) mută `pending_risk`/`pending_risk_meta` din memorie în tabel
(aprobările supraviețuiesc restartului); (4) `GET /api/runs` + tab „Runs" în dashboard;
(5) repară D9 în același refactor: la cache hit inserează perechea în SQLite înainte de
return; consumă LLM-ul într-un `asyncio.create_task` cu coadă (pattern-ul din `/task/run`),
ca disconnect-ul clientului să nu piardă răspunsul.
**Acceptare:** fiecare chat și task creează un run cu evenimente · restart nu pierde istoricul
și aprobările pending · cache hit apare în istoric SQLite · disconnect mid-stream → răspunsul
tot se salvează (test cu client anulat) · `/api/runs` întoarce JSON · pytest verde.

**Implementat:**

- Schemă: `_ensure_runs_table` (runs + run_events + indexuri) și `_ensure_approvals_table`
  (`pending_approvals`) în `chat_history.db`, create la startup.
- Helpers best-effort (nu blochează chat-ul dacă DB-ul dă eroare): `_run_start`, `_run_event`
  (payload JSON trunchiat 4KB), `_run_update` (whitelist de coloane), `_run_end`, plus
  `_channel_for` (telegram/cron/ui).
- Instrumentare `_chat_dispatch`: un run per cerere reală de chat (după shortcut-uri) cu
  evenimente `routing`/`budget`/`memory`/`cache`/`result`; `_background_task_exec` deschide un
  run `kind=task` cu `tool_call`/`result`/`error`. (`decide_tier`/`_budget_check` etc. sunt
  citite prin evenimentele emise în dispatch, nu modificate în semnătură.)
- **Aprobări persistente (§3):** `/risk/register` scrie în `pending_approvals` (`_persist_approval`),
  `/risk/respond` marchează `confirm`/`block` (`_resolve_approval`), iar la startup
  `_load_pending_approvals` repopulează `pending_risk_meta`/`risk_decisions` din DB → aprobările
  pending revin în UI și deciziile deja luate ajung la `risk_hook.py` prin `/risk/status`.
- **D9 reparat:** (a) cache hit inserează perechea user+assistant în `messages` înainte de
  return (înainte lipsea din `/api/history`); (b) `_persisting_stream` drenează generatorul SSE
  într-un `asyncio.create_task` cu coadă — dacă clientul se deconectează la mijloc, salvarea
  istoric/cache/memorie + închiderea run-ului continuă în fundal (pattern din `/task/run`).
- `GET /api/runs` (listă, `?limit`) + `GET /api/runs/{id}` (run + decision trace) + tab **🧾 Runs**
  în dashboard (fetch live la deschidere).
- Teste noi: `tests/test_run_ledger.py` (14) — helperi, canal, persistență aprobări (inclusiv
  simulare restart), `/api/runs` + detail (404), cache hit salvat în SQLite, stream
  disconnect-safe (client abandonat după 1 chunk → răspunsul tot se persistă). Suită: **168 verzi**.
- **Necesită restart** (`start_all.sh`) ca tabelele + endpoint-urile să fie active în producție.
- **Rămas pentru mai târziu:** `cost_usd` e în schemă dar populat abia de #7 (buget EUR);
  `routing_neighbor` rezervat; instrumentarea fină per-tool a agenților vine cu WP9 (SDK).

### WP9 (#4) — Executor pe Claude Agent SDK · efort: 2–4 săptămâni de seri · ✅ IMPLEMENTAT (06.07.2026)

**Pași:** (1) `agent_runner.py` nou, clasa `AgentRunner` peste `claude-agent-sdk` (sesiuni cu
resume, hook PreToolUse în-proces, event mapping → SSE chunks + run_events); (2) rescrie
`_route_claude_autonomous` și `_background_task_exec` peste el; (3) map
`session_id → sdk_session_id` în SQLite; (4) portează matricea din `risk_hook.py` ca hook
Python (păstrează scriptul pentru compatibilitate CLI); (5) timeout de inactivitate (reset la
fiecare eveniment), nu deadline fix. **Variantă minimală acceptată** dacă SDK-ul nu merge pe
3.9: rămâi pe subprocess dar adaugă `--include-partial-messages` + `--resume` + parsarea
tool events (rezolvă D5 + 2.6 cu efort mic; verifică întâi dacă SDK-ul cere Python 3.10+ —
dacă da, decide împreună cu userul: venv nou 3.12 pentru orchestrator vs varianta minimală).
**Acceptare:** deltas reale în UI (nu totul la final) · un follow-up T5 referă corect
conversația anterioară (resume) · tool calls vizibile în run ledger · fără timeout fals la
taskuri >120s active.

**Implementat (06.07.2026):**

- **Decizie: migrare completă pe Python 3.12.** `claude-agent-sdk` cere `>=3.10`; Stefan a
  ales SDK-ul oficial (nu varianta minimală). Spike de de-risking întâi: venv 3.12 + toate
  deps (ChromaDB/LiteLLM/FastAPI/APScheduler) + SDK → suita existentă verde pe 3.12 fără nicio
  modificare. Apoi swap `.venv` (3.9.6) → 3.12 (backup `.venv-py39`); `setup.sh` preferă
  `python3.12`; `requirements.txt` += `claude-agent-sdk`; constrângerea „no 3.10+ syntax" din
  CLAUDE.md ridicată.
- **`agent_runner.py` — `AgentRunner`** peste `ClaudeSDKClient` (nu `query()`: `can_use_tool`
  cere streaming mode). Emite evenimente normalizate (`text`/`tool_use`/`tool_result`/`result`/
  `error`) — dict-uri pure, mapate în SSE + `run_events`. `_map_sync` (pur, testabil) traduce
  mesajele SDK; `StreamEvent`/`content_block_delta` → delte reale (D5); `TextBlock` din
  `AssistantMessage` e SĂRIT (ar dubla textul deja streamat). `ResultMessage` aduce
  `session_id` (resume), `total_cost_usd` (bonus #7) și `duration_ms`.
- **Gate de risc in-proces** (`_make_gate` → `can_use_tool`): refolosește `risk_hook.evaluate_risk`
  (o singură sursă de adevăr). Never → deny; High/Medium-autonomous → `approval_cb`
  (`_agent_approval_cb` din orchestrator: persistă aprobarea + butoane inline Telegram + așteaptă
  `/risk/respond`, timeout → block fail-closed) — **fără roundtrip HTTP** ca la hook-ul CLI.
  `risk_hook.py` rămâne intact pentru gemini + compat CLI (rulările SDK NU pasează
  `--settings risk_settings.json`).
- **Inactivity timeout** (reset la fiecare mesaj SDK, default 180s config `agent_inactivity_timeout`)
  → `interrupt()` + eveniment `error`. Repară deadline-ul fix 120s care ucidea taskuri active.
- **Rescrise:** `_route_claude_autonomous` (chat T3+, read-only via policy) și
  `_background_task_exec` (task/sysrun, capability completă; gemini rămâne subprocess). Ambele
  emit `tool_call`/`tool_result` în run ledger și `cost_usd` real. `_route_cli` threadează
  `session_id`+`run_id`; cu resume, contextul îl ține SDK-ul (nu-l mai concatenăm manual).
- **Resume:** tabel `agent_sessions` (`session_id` ↔ `sdk_session_id`) + `_get/_save_sdk_session`.
  Un follow-up pe aceeași sesiune reia conversația SDK anterioară.
- **Kill switch (WP-G1):** `_stop_all()` cheamă și `_agent_runner.stop_all()` (interrupt pe
  clienții SDK vii, care nu-s în `_running_procs`).
- Teste: `tests/test_agent_runner.py` (19) — `_map_sync`, gate (Never/Safe/High±canal/downgrade),
  `run()` cu client mock (delte + fallback fără delte + inactivity timeout + SDK indisponibil),
  mapare sesiuni roundtrip, `_agent_approval_cb` (confirm + timeout). Suită: 171 → **190 verzi**.
- **Verificat live** (după restart pe 3.12): chat T5 real → delte reale; run ledger cu
  `cost_usd=0.069` populat; `agent_sessions` salvat; follow-up cu resume reia contextul.
- **Rămas:** fallback-ul LiteLLM→CLI (`feed()` din `_route_claude_autonomous` vechi, acum în
  altă funcție) încă pe subprocess — cale rară, nu blochează. Instrumentarea WP11 (mission
  runner) se construiește peste `AgentRunner`. `.venv-py39` de șters după câteva zile de rulare OK.

### WP11 — Mission Runner: handoff → execuție nonstop · efort: 1–2 săptămâni de seri · după WP8+WP9 · ✅ IMPLEMENTAT (06.07.2026)

Modul „îi dau planul și lucrează singur": automatizarea buclei pe care Stefan o face azi
manual cu acest fișier (plan cu WP-uri → sesiune per WP → verificare criterii → următorul).

**Implementat (06.07.2026):**

- **`mission_runner.py` — logica pură** (fără I/O, testabilă izolat): `parse_mission`
  (mission.md → WP-uri cu pași + criterii), extragerea criteriilor **verificabile** (comenzi
  shell între backtick-uri), `mark_wp_done` (marchează ✅ idempotent), `parse_rate_limit_reset`
  (ora de reset dintr-un mesaj de rate-limit → secunde). Orchestrarea cu stare e în
  `orchestrator.py`, secțiunea „Mission Runner" (pattern-ul briefing: pur vs cu stare).
- **Formatul misiunii** (1): `missions/<slug>/mission.md`, același format ca acest handoff.
  `## ` = WP; `### Acceptare` cu bullet-uri; cele între backtick-uri = verificabile. Exemplu
  rulabil în `missions/exemplu/`, `missions/README.md` documentează.
- **Runner-ul** (2): `_mission_run` — buclă peste `AgentRunner` (WP9), o **sesiune per misiune
  cu resume** (context-ul se duce între WP-uri). Rulează criteriile shell (`_mission_verify`,
  exit 0), marchează ✅ în md + **commit doar acel fișier**, avansează. Stare în SQLite
  (`missions`, `mission_wps`) → **restart reia din WP-ul corect** (`_mission_resume_on_startup`).
- **Puntea de decizii** (3): verificare picată → `_mission_ask` trimite întrebarea + opțiunile
  (retry/skip/abort) pe Telegram (`send_mission_question`, callback `mission:`), răspunsul vine
  prin `/mission/answer/{id}`. **Timeout → `paused`** (NU failed — decizie ≠ aprobare de risc).
- **Auto-resume la limită** (4): din eroarea SDK, `parse_rate_limit_reset` deduce ora de reset
  → job one-shot APScheduler (`_mission_schedule_resume`); fallback 15 min; WP-ul se reia.
- **Anti-sleep** (5): `caffeinate -s` cât timp o misiune e activă, eliberat la final/pauză/stop.
- **Kill switch** (6): `_stop_all` (`!stop`) oprește și misiunea activă; comenzi noi
  `!mission start/status/pause/resume/stop/list`; endpoints `/api/missions` + `/api/missions/{id}`.
- **Rămas (fază 2, notat):** agentul care *inițiază* singur o întrebare (tool MCP `ask_user`) —
  acum puntea se declanșează determinist la verificare picată; failover pe Gemini la limită (în
  loc de doar așteptare) — hook-ul de rate-limit e pregătit. O singură misiune activă la un moment
  dat (design intenționat, single-user).
- Teste: `tests/test_mission_runner.py` (12, logica pură) + `tests/test_mission_orchestration.py`
  (15, bucla + decizii + rate-limit + restart-resume + comenzi). Suită: 190 → **217 verzi**.
  **Necesită restart** ca tabelele + comenzile `!mission` să fie active în producție.

**Componente (specificația originală):**

**Componente:**

1. **Formatul misiunii:** `missions/<slug>/mission.md` — WP-uri cu pași + criterii de
   acceptare, exact formatul acestui fișier (deja validat pe WP1–WP4). Checklist viu:
   runner-ul marchează ✅.
2. **Runner-ul:** buclă peste `AgentRunner` (WP9) — ia următorul WP nemarcat, spawnează o
   sesiune, rulează criteriile verificabile (pytest, curl-uri), marchează ✅ + commit, trece
   mai departe. Poziția și starea trăiesc în run ledger (WP8) — restart nu pierde misiunea.
3. **Puntea de decizii:** extinde fluxul `/risk/register` cu `type: question` — agentul
   blocat pe o decizie trimite întrebarea + opțiunile pe Telegram (butoane inline sau reply
   text); răspunsul e injectat înapoi în sesiune. Timeout → misiunea trece în `paused`
   (NU deny — o decizie de design nu e o aprobare de risc).
4. **Auto-resume la limită:** mesajul de rate limit al Claude Code conține ora de reset —
   runner-ul o parsează, programează un one-shot APScheduler la reset + `--resume
   <session_id>`, notifică pe Telegram („⏸ limită atinsă, reiau la 18:00"). Fallback dacă
   parsarea eșuează: retry la 15 min. Opțional per misiune: failover pe API prin LiteLLM cu
   plafon $ (leagă de budget v2, #7). **Fără rotație de conturi** — vezi §4.
5. **Anti-sleep:** runner-ul ține un `caffeinate -s` (subprocess) cât timp există o misiune
   activă și îl eliberează la final/`paused`/`!stop` — fără el, capacul închis îngheață
   sesiunea și „nonstop" e fals la prima plecare de acasă.
6. **Kill switch:** `!stop` (WP-G1) oprește și misiunile; `!mission status/pause/resume`
   comenzi noi pe Telegram.

**Acceptare:** o misiune de test cu 2 WP-uri mici rulează cap-coadă fără intervenție · o
întrebare `type: question` ajunge pe Telegram și răspunsul deblochează sesiunea · kill pe
orchestrator mid-mission → la restart reia din WP-ul corect · limită simulată → resume
programat la ora parsată din mesaj · `!stop` oprește misiunea · pytest verde.

### WP12 — Telecomandă: construirea lui Kage prin Telegram, de la distanță · efort: o seară–un weekend · după WP11 + #7

**Scop (09.07.2026, Stefan):** fluxul de lucru principal devine „Stefan la birou, laptopul
acasă" — misiunile se creează, aprobă, urmăresc și revizuiesc integral de pe telefon
(Telegram + GitHub mobile), fără acces fizic la mașină.

**Stare: LIVRAT COMPLET (Slice 1 + 2, 10.07.2026).**

- **Slice 1** (pașii 2, 5 + checklist mașină): `!mission new <direcție>` (orchestrator
  redactează `mission.md` cu MISSION_DRAFT_MODEL prin `_agent_complete` fără tools → salvează ca
  status `draft` → card Telegram cu butoane ✅/✏️/🗑), `!mission revise` + captura reply-ului
  după ✏️ (gateway), endpoint `POST /mission/draft/{id}/{start|discard}`, `_briefing_missions()`
  cablat (draft/active/pauzate + terminate în 24h), checklist `pmset -c sleep 0` în `INSTALL.md`.
- **Slice 2 pasul 3** (branch+push per WP): misiunea rulează pe `mission/<slug>` (creat din HEAD,
  working tree neschimbat); `_mission_mark_and_commit` comite acum **întregul diff** (nu doar
  mission.md), apoi `_mission_git_push` (opt-in `remote.mission_git_push`) + notificare cu link
  de **compare GitHub** (`_github_compare_url`). Atribuirea = git config-ul repo-ului (Stefan),
  fără trailer Claude.
- **Slice 2 pasul 4** (watchdog): heartbeat extern opt-in (`remote.heartbeat_url`, dead-man's-
  switch) + mesaj `🟢 Kage online` la startup; tabel `job_runs` + `_tracked_job` peste scanul
  programat + `_recover_interrupted_jobs()` la startup — **rezolvă exact cazul din 09.07**
  (scan de 19:00 tăiat de restart, pierdut tăcut): îl detectează, re-declanșează, alertează.
- Config nou: `mission_draft_model`, bloc `remote` (mission_git_branch/push/remote,
  heartbeat_url/interval_min, startup_online_message). Teste: `tests/test_mission_remote.py`
  (28) → suită **383 verzi**. Neutralizat git-ul real în `test_mission_orchestration`.

**Pași:**

1. **Checklist mașină (manual, Stefan, ~2 min):** laptop pe priză + `sudo pmset -c sleep 0`
   (clamshell pe AC e ok). Fără asta nimic nu funcționează: `caffeinate -s` din WP11 ține
   Mac-ul treaz DOAR cât rulează o misiune; idle → sleep → polling-ul Telegram moare și
   mașina nu mai e trezibilă remote. Documentează în README/RESTORE.
2. **`!mission new <direcție>`:** T5 redactează `missions/<slug>/mission.md` în formatul
   deja validat (`## WP` + `### Acceptare` cu criterii verificabile între backtick-uri),
   trimite pe Telegram rezumatul + planul cu butoane: ✅ pornește · ✏️ revizuiește (reply
   text = instrucțiuni de modificare, T5 rescrie) · 🗑 renunță. (După WP13, draftul trece
   întâi prin advisor.)
3. **Branch + push per WP:** misiunea rulează pe branch `mission/<slug>` din `dev`; după
   fiecare WP verificat → commit **întregul diff** (azi `_mission_mark_and_commit` comite
   doar mission.md) + push → notificare Telegram cu link de compare GitHub. Stefan
   revizuiește din GitHub mobile și face merge de acolo. Atenție: autorul commit-urilor =
   emailul lui Stefan (regula de atribuire existentă), nu `kage@localhost`.
4. **Watchdog extern:** heartbeat din scheduler (ping periodic la un healthcheck extern —
   healthchecks.io sau echivalent, config opt-in) care alertează când Kage TACE (orchestrator
   mort, net picat, mașină adormită) — launchd repornește procesul, dar nu te anunță când
   nu poate. Plus mesaj „🟢 Kage online" pe Telegram la startup.
   **Caz confirmat 09.07.2026:** restart de proces la 19:04 (fără excepție în log — semnal
   extern, cauză neconfirmată) a întrerupt scanul de joburi de 19:00 la ~4 min; procesul a
   revenit singur în câteva secunde, dar jobul întrerupt a murit tăcut — fără notificare, fără
   retry azi (`next run` = mâine 07:00; scanul de seară a fost recuperat manual via
   `POST /jobs/scan`). Watchdog-ul trebuie să acopere și acest caz mai subtil, nu doar
   „orchestratorul e mort": **la startup, verifică dacă vreun job cron avea `next run` ÎN
   TRECUT** (comparat cu ora curentă) — semn de rulare întreruptă mid-execuție — și fie
   îl re-declanșează imediat, fie trimite alertă „⚠️ job X întrerupt la restart, reîncerc/
   aștept următoarea rulare".
5. **Restanță WP-D:** cablează `_briefing_missions()` (azi `None`) la tabelele
   `missions`/`mission_wps` din WP11 — briefingul de dimineață listează misiunile
   active/terminate/pauzate.

**Acceptare:** de pe telefon, fără laptop: `!mission new` → plan primit → ✏️ o revizie →
✅ pornește → notificare cu link de diff după primul WP → o întrebare de misiune primită și
răspunsă → misiunea se termină → briefingul de a doua zi o listează · mașina nu adoarme
idle (test: 1h fără activitate, polling viu) · heartbeat-ul alertează la o oprire simulată ·
commit-urile de misiune au autorul corect · pytest verde.

### WP13 — Advisor-in-the-loop + bucla completă HITL v2 · efort: un weekend+ · după WP12

**Concept (decizia din §4):** un al doilea model care se **ceartă argumentat** cu
orchestratorul — îl împiedică să omită criterii, să halucineze („am făcut X" fără dovadă)
sau să sape prea adânc într-o direcție care nu poate funcționa. Principii nenegociabile:

- **Context minimal by design:** advisorul primește DOAR obiectivul + artefactul (plan sau
  diff) + criteriile + capcanele relevante din §3 — NU conversația orchestratorului. Un
  advisor care vede tot raționamentul se ancorează în el și aprobă din inerție; unul care
  vede doar artefactul judecă artefactul.
- **Consultativ, nu blocant:** verdictul advisorului nu poate opri singur misiunea.
  Dezacord persistent (orchestratorul respinge obiecția, advisorul o menține) → ambele
  argumente merg la Stefan pe Telegram, el decide. Niciodată deadlock model↔model.
- **Model (decizia 13.07.2026, Stefan — revizuită):** **Qwen local (T2)** — zero cost,
  independent de orice API externă, suficient de diferit de Claude (executorul misiunilor)
  pentru dezacord genuin pe rolul de review. **Gemini prin OpenRouter API — RETRAS din plan**
  (nu se cheltuie pe asta acum); **T4/Gemini CLI local — eliminat complet din Kage** (§4,
  13.07.2026 — CLI-ul nu mai are cont valid, Google a deprecat tierul gratuit). Codex/ChatGPT
  rămâne upgrade opțional, condiționat de abonament (**WP13b**, mai jos). NU modele free
  OpenRouter pentru review de diff — diff-urile conțin codul proiectului, iar free-ul se
  plătește cu training pe input.
  **Idee capturată, NEangajată (13.07.2026, Stefan):** un „LLM council" — mai multe modele
  consultate în paralel — DOAR pentru decizii importante (nu pentru fiecare reviewer pass de
  rutină, cost/latență nu s-ar justifica). Nu e WP azi; dacă se construiește vreodată, atunci
  se redeschide și întrebarea „intră Gemini prin API pentru rolul ăsta specific".

**Punctele de cuplare (3):**

1. **Review de plan** — la `!mission new`, înainte de a-i trimite lui Stefan: advisorul
   primește direcția + draftul → listă de obiecții (omisiuni, criterii neverificabile,
   dependențe ignorate, scope prea mare); orchestratorul corectează sau contra-argumentează;
   rezumatul dezacordurilor nerezolvate se atașează planului trimis lui Stefan.
2. **Reviewer pass pe diff** — înainte de ✅ pe un WP: diff + criteriile de acceptare +
   capcanele §3 → verdict `ok / obiecții`; obiecțiile se întorc în sesiunea agentului (o
   singură iterație de fix, apoi escaladare). Replică ce face Stefan manual: cititul
   diff-ului, nu doar exit code-ul de la pytest.
3. **Anti-rabbit-hole** — după N încercări eșuate pe același WP (sau X minute), advisorul
   primește istoricul comprimat al încercărilor → „direcția asta poate funcționa?" →
   recomandă `continuă / pivotează / întreabă-l pe Stefan`. Oprește scenariul „ne dăm seama
   după 3 ore că lucrul ăla nu avea cum să meargă".

**Plus, în același WP (absorbite din WP11 fază 2):**

- **Tool `ask_user`** — agentul blocat pe o decizie de design întreabă SINGUR pe Telegram
  mid-WP (extinde puntea `_mission_ask` existentă; azi ea se declanșează doar determinist,
  la verificare picată). Miezul lui „human-in-the-loop la decizii".
- **Coadă de misiuni** — `!mission queue add/list/clear`; la finalul unei misiuni,
  următoarea pornește automat DOAR dacă precedenta a trecut criteriile + reviewer pass-ul,
  cu notificare la fiecare tranziție. O singură misiune ACTIVĂ rămâne invariant.
- **Propunerea următorului pas** — la final de misiune (sau coadă goală), Kage propune pe
  Telegram următoarea felie din acest handoff (ordinea din §5), Stefan confirmă cu un buton.

**Acceptare:** un plan cu un criteriu lipsă primește obiecție de la advisor înainte să
ajungă la Stefan · un WP cu diff care nu acoperă un criteriu NU primește ✅ la primul pass
(test cu fixture) · după N eșecuri simulate advisorul recomandă pivot și Stefan primește
întrebarea · agentul pune o întrebare `ask_user` mid-WP și răspunsul deblochează sesiunea ·
două misiuni în coadă rulează în serie cu notificări · dezacord persistent simulat → ambele
argumente ajung pe Telegram · pytest verde.

### WP13b — Advisor: swap Qwen local → Codex/ChatGPT · efort: o seară · CONDIȚIONAT de abonamentul ChatGPT Plus, după WP13

**Scop (13.07.2026, Stefan — revizuit):** odată creat abonamentul ChatGPT Plus (20$/lună,
pentru Codex CLI), Advisorul (WP13) trece de pe Qwen local pe Codex/GPT — diversitate reală
(alt „creier" decât Claude, spre deosebire de Qwen care e oricum folosit și ca Actor în
WP-T) pe un abonament deja plătit, deci tot cost marginal 0. Excepția de la regula WP-CX
(§4) permite asta ÎNAINTE de WP-G2, fiindcă rolul e read-only. **Nu implică Gemini în niciun
fel** — CLI-ul Gemini a fost eliminat complet din Kage (WP-RMG).

**Pași:**

1. Client Advisor pluggable: interfața rămâne cea din WP13 (obiectiv+artefact+criterii →
   verdict text); doar implementarea clientului se schimbă (Codex CLI non-interactiv în loc
   de apelul local Qwen). Verifică întâi dacă Codex CLI suportă invocare
   non-interactivă/scriptabilă echivalentă cu ce folosește deja executorul Claude — dacă nu,
   rămâi pe Qwen și reevaluează.
2. Config: model Advisor selectabil (`qwen-local` | `codex`), Qwen rămâne fallback dacă
   Codex CLI nu răspunde sau abonamentul nu e activ.
3. NU atinge rolul de al doilea executor (WP-CX) — acela tot așteaptă WP-G2.

**Acceptare:** Advisorul rulează pe Codex cu abonamentul activ · fallback pe Qwen local dacă
Codex CLI eșuează · cele 3 puncte de cuplare din WP13 (review plan, reviewer diff,
anti-rabbit-hole) funcționează neschimbate · pytest verde.

### WP-RMG — Retragere completă Gemini CLI (Tier 4 + backend agent) · efort: o seară · executat 13.07.2026

**Scop (13.07.2026, Stefan):** Gemini CLI e stricat ireversibil pentru profilul de cont
folosit (Google a deprecat „Gemini Code Assist for individuals" — `IneligibleTierError`,
confirmat empiric §3). Kage îl folosea în DOUĂ locuri independente, aceeași cauză de bază:
Tier 4 din router (`!gemini`, clasificare automată, escaladare `!retry`) și backend-ul de
agent pentru `!run`/`!swarm` (`agent="gemini"`, prefixul `gemini`, `!swarm` = „Claude +
Gemini paralel"). Decizie: eliminare completă, nu doar dezactivare — cod mort care depinde
de un binar mort nu rămâne în arbore.

**Pași:**

1. **Clamp central în `decide_tier`:** orice cale care ar produce tier 4 (clasificare
   semantică, clasificare Qwen, escaladare `!retry`) e prinsă ÎNAINTE de orice lookup în
   `TIER_MODELS` — tier 4 devine imposibil de atins, indiferent de vectori vechi rămași în
   ChromaDB (`tier_routing`) din seed-uri anterioare. `!retry` de la tier 3 sare direct la 5
   (retry crește mereu, nu coboară).
2. **Router:** scoate `!gemini` din prefixele forțate (`decide_tier`, `_ROUTING_PREFIXES`,
   `_CACHE_PREFIX_RE`, `!help`); scoate opțiunea „4 = Gemini" din promptul de clasificare
   Qwen; scoate exemplele de seed ale tierului 4 din `TIER_EXAMPLES` (nu se mai re-seedează;
   vectorii vechi din ChromaDB rămân inerți datorită clamp-ului de la pasul 1, nu se
   migrează live).
3. **Executor CLI:** șterge `_route_gemini`, `GEMINI_CLI`/`_find_cli("gemini")`, ramurile
   `provider == "gemini"` din `_route_cli`, `_generate_cli_chunks`, `_run_scheduled_task`.
   `TIER_MODELS[4]`/`TIER_SHORT[4]` rămân ca ETICHETĂ moartă („retras") — NU se șterg din
   dict, doar ca să nu crape afișarea unor rânduri istorice din DB cu `tier=4`; nimic nu mai
   scrie tier 4 de acum înainte.
4. **Backend de agent:** șterge `_swarm_task_exec`, ramura `agent == "gemini"` din
   `_background_task_exec`, detecția prefixului `gemini` din dispatcher; scoate `!swarm`
   din `!help` și din dispatch (`_prepare_and_launch_task`) — comanda dispare, nu doar tace.
5. **Curățenie cosmetică:** scoate `gemini_count` din stat-urile dashboard-ului
   (`/dashboard`), înlocuiește fallback-urile reziduale `"gemini"`/`"gemini-pro"` din
   label-uri cu un placeholder neutru.
6. **Teste:** `tests/test_routing.py` — `test_decide_tier_gemini_forces_tier4` și assertul
   pe `TIER_SHORT` cu cheia 4 se actualizează (tier 4 nu mai e selectabil, eticheta devine
   „retras").

**NU face parte din WP-ul ăsta:** migrarea/ștergerea live a vectorilor vechi din colecția
ChromaDB `tier_routing` — clamp-ul de la pasul 1 îi face inerți, nu merită riscul unei
operații pe date live pentru zero beneficiu funcțional.

**Acceptare:** `!gemini`, `!swarm`, `!run gemini ...` nu mai există ca funcționalitate ·
clasificarea (semantică + Qwen) nu poate produce niciodată tier 4 · `!retry` de la tier 3
sare la 5 · niciun cod nu mai invocă `gemini_cli` · pytest verde (suită completă, inclusiv
`test_routing.py` actualizat).

### WP-V — Video intel: analiza clipurilor trimise de pe telefon · ✅ Slice 1 livrat (12.07.2026) · independent

**✅ Slice 1 livrat (12.07.2026).** Fluxul cost-0 cap-coadă: detecție URL în gateway →
extracție (subtitrări-întâi → altfel audio → Whisper local WP6) → clasificare pe categorii +
analiză sceptică pe T2 local → card de verdict pe Telegram cu butoane adaptate categoriei.
Piese: `video_intel.py` (modul nou, frontiere subprocess injectabile), endpoint-urile
`/video/analyze|save|hypothesis|visual|deep|ignore` în orchestrator, cablarea în
`telegram_gateway.py` (`_handle_video`, `send_video_card`, `_handle_video_callback`), blocul
`video_intel` în config, 34 de teste noi (418 total, verzi). Butonul 🔬 pre-înregistrează
ipoteza de trading în `trading.db` (invariant #2, schema falsificabilă validată); 💾 scrie
notă structurată în `vault/VideoIntel/`.

**Referințe preluate (adaptate, nu verbatim — MIT):** rețetele yt-dlp/ffmpeg (subtitrări-întâi,
keyframes pe `select='gt(scene,0.3)'`) inspirate din `github.com/martinopiaggi/summarize`;
structura pattern-urilor `analyze_claims`/`extract_wisdom` din `github.com/danielmiessler/Fabric`
rescrisă în română pentru schema noastră JSON (afirmație → dovezi → red flags → verdict).

**✅ Slice 2 livrat (16.07.2026, Codex CX2, PR #37):** pasul vizual PLĂTIT = OCR/descriere
per keyframe cu **Qwen3-VL prin OpenRouter** (PNG base64; nota din spec despre Gemini era
pre-WP-RMG — alternativa OpenRouter aleasă de Codex, acceptată la review) + analiza adâncă
🔎 pe Sonnet tot prin OpenRouter. Ambele fail-closed pe plafonul EUR (#7, livrat 12.07) cu
costul estimat înregistrat în `trading.db`; auto-trigger vizual când transcriptul trădează
conținut vizual („uite aici", „graficul"…). Cu `api_budget.enabled=false` (default) apelurile
plătite sunt refuzate — activarea = decizia lui Stefan în config. **Precondiții
rezolvate (13.07.2026):** yt-dlp instalat în `.venv` (cale absolută în config); ffmpeg +
Whisper `large-v3-turbo` deja instalate; **gaura de PATH din `start_all.sh` reparată**
(launchd pornea cu PATH minimal fără `/opt/homebrew/bin` → ffmpeg/whisper/yt-dlp/ollama
picau după reboot) + auto-upgrade yt-dlp throttled la 24h (anti-bot TikTok).

**Ideea (09.07.2026, Stefan):** trimite pe Telegram, de pe telefon, link-uri video (YouTube,
TikTok, Reels, X — oameni care explică concepte de finance/AI/agents/trading) → Kage extrage
conținutul și îl analizează sceptic: e informația valoroasă și reală, sau marketing/fals?
Azi Stefan descrie manual ce vede pe TikTok — ineficient + „telefonul fără fir".

**De ce e felie mică: 80% există deja.** Gateway-ul Telegram (intrare), endpoint-ul Whisper
din WP6 (transcriere locală — construit, dar NESETAT: cere `brew install whisper-cpp` +
modelul GGML ~1,6GB → setup-ul WP6 devine precondiție, nu mai e opțional), T2 local pentru
analiză (cost 0), vault-ul (salvare), quant lab-ul (verdict pe strategii). Piesa nouă e
subțire: **yt-dlp** (open source, ~1800 site-uri) + promptul de analiză + cardul Telegram.

**Pipeline:**

1. **Detecție URL video** în gateway (`_dispatch`): domenii cunoscute (youtube/youtu.be/
   tiktok/instagram/x.com…) → intră pe fluxul video, nu pe chat.
2. **Extracție (`video_intel.py`, subprocess yt-dlp):** metadata (titlu/autor/durată/
   descriere) + **subtitrările existente ÎNTÂI** (`--write-auto-subs` — la YouTube există
   aproape mereu → zero transcriere, instant, gratuit). Fără subtitrări (TikTok, de regulă) →
   descarcă DOAR audio → endpoint-ul Whisper local (WP6) → transcript. Limită de durată în
   config (ex. ≤30 min) ca un podcast de 3h să nu blocheze pipeline-ul.
3. **Pasul vizual (extensie 09.07.2026** — multe clipuri ARATĂ, nu doar spun: grafice,
   cod pe ecran, slide-uri, demo-uri): descarcă video-ul → **ffmpeg extrage keyframes** pe
   detecție de schimbare de scenă (`select='gt(scene,0.3)'`, plafon ~20 cadre) →
   descriere + OCR per cadru, cu timestamp, îmbinate cu transcriptul.
   **Modelul vision — arbore de decizie (decis 09.07.2026 cu Stefan):**
   - **Default: `gemini-2.5-flash-lite` prin OpenRouter** (sau flash-lite-ul curent la
     implementare). Motive: Gemini e etalonul la OCR (grafice dense, cifre, text mic —
     exact ce ne trebuie), iar la volum realist (1–3 clipuri/zi cu pas vizual, NU 10) un
     clip de 20 cadre = 0.1–0.3 cenți → **~0.10–0.15 $/LUNĂ** — a „economisi" asta cu
     inferență locală = calitate mai slabă + cod mai complicat pentru nimic. Privacy nu
     joacă: clipurile sunt conținut public, nu date proprii. Cost în bugetul #7 (intră în
     sub-plafonul advisor/misc). E și mai rapid decât bucla locală per cadru.
   - **Fallback: T2 rezident (Qwen3.6-35B-A3B), un apel per cadru** — la eroare API, offline
     sau buget depășit (același pattern ca la Criticul de trading: API + fallback local).
     Notă contra-intuitivă: T2 NU e lent (MoE cu 3B activi) și memoria e deja plătită
     (rezident); capcana e contextul — cadrele se trimit UN APEL PER CADRU, nu toate odată
     (20 cadre într-un apel = 20–40k tokeni = prefill de minute). Verifică la implementare
     suportul vision prin Ollama/LiteLLM.
   - **NU se instalează al doilea model vision local** (ex. qwen3-vl-8b): +5–8GB RAM,
     decodare mai lentă decât MoE-ul cu 3B activi, și cel mai slab OCR din cele trei opțiuni.

   **Gating de cost/timp:** automat pentru clipuri scurte (≤5 min, tipic TikTok); pentru
   clipuri lungi doar la cerere — buton 🖼 „analiză vizuală" pe card — sau când transcriptul
   trădează conținut vizual („uite aici", „cum se vede pe grafic").
4. **Analiză sceptică pe T2 local (cost 0),** structurată și **conștientă de categorie**
   (extensie 09.07.2026 — nu doar trading): clasifică întâi conținutul (trading / tool sau
   framework tech / carte / lecție / decizie-framework), apoi șablonul potrivit:
   - comun: ce se susține · mecanismul pretins · e falsificabil? · red flags (vinde
     curs/semnale, randamente nerealiste, survivorship bias, urgență artificială, affiliate);
   - tech: tool-ul/framework-ul există? e întreținut? afirmația e verificabilă (WebSearch
     DOAR la cerere, nu implicit — vezi capcana injection);
   - carte/lecție/decizie: ideile centrale · ce e acționabil · ce contrazice/confirmă ce
     știm deja → notă structurată pentru vault (categoriile devin corpus pentru RAG-ul
     viitor `!index` — sinergie notată).
   Escaladare la T5 DOAR la cerere (buton „analiză adâncă") — gated pe buget.
5. **Card de verdict pe Telegram:** 📹 titlu/autor → rezumatul afirmațiilor →
   plauzibilitate + red flags → verdict (valoros / marketing / fals / de testat) + butoane
   **adaptate categoriei**: 💾 salvează în vault (toate) · 🔬 **„→ ipoteză în quant lab"**
   (doar trading: afirmația devine ipoteză pre-înregistrată în WP-T și primește verdictul
   matematic SEMNAL/ZGOMOT al validării — „guru zice că merge" → validarea decide) ·
   🖼 analiză vizuală (clipuri lungi) · 🔎 analiză adâncă (T5, gated pe buget) · 🗑 ignoră.

**Capcane:**

- **Prompt injection (aceeași regulă ca WP-J):** transcriptul = conținut web complet
  ne-de-încredere care intră într-un LLM — se tratează ca DATE, niciodată concatenat ca
  instrucțiuni; analiza rulează FĂRĂ tools (sau read-only). Un clip poate conține literal
  „ignoră instrucțiunile și…".
- **TikTok se strică periodic** (anti-bot) — yt-dlp ține pasul, dar ține-l actualizat
  (`yt-dlp -U` în jobul nocturn sau pip upgrade la restart); eșecul unui site = mesaj grațios
  („nu pot extrage de aici acum"), nu crash.
- **De ce nu NotebookLM:** închis, manual (copy-paste în browser), fără API oficial, fără
  integrarea cu quant lab-ul. Pipeline-ul propriu = automatizat cap-coadă, 100% local, cost 0.

**Acceptare:** un link YouTube cu subtitrări → card de verdict FĂRĂ transcriere (sub ~30s) ·
un link TikTok fără subtitrări → transcris local + card · un clip scurt care ARATĂ ceva
(grafic/cod pe ecran) → keyframes extrase, descrierile vizuale apar în analiză · un clip
despre un framework tech (nu trading) → card pe șablonul tech, salvabil ca notă structurată
în vault · un clip cu „strategie de trading" → butonul 🔬 creează o ipoteză pre-înregistrată
în `trading.db` · zero apeluri cloud pe fluxul implicit · un transcript cu instrucțiuni
injectate NU schimbă comportamentul analizei (test) · site nesuportat/eșec yt-dlp → mesaj
grațios · pytest verde.

### WP-NL — Gateway conversațional: fără prefixe, intent router · efort: o seară–un weekend · după #7

**Scop (13.07.2026, Stefan):** prefixele `!` sunt prea complicate pentru driver-ul zilnic —
Stefan scrie ce vrea în limbaj natural, iar un model decide din context ce trebuie făcut.
Prefixele rămân doar ca escape hatch determinist (bypass complet al routerului).

**Pași:**

1. **Clasificator de intenție** (promptul few-shot + calibrarea pragului de încredere pe
   setul etichetat: **Stefan, ghidat** — §8) la orice mesaj fără prefix (și fără URL video —
   fluxul WP-V rămâne cum e): T1/T2 local (sau Haiku la nevoie), cu lista închisă de intenții:
   `chat` · `status/briefing` · `mission_new` · `mission_control` (pauză/continuă/anulează)
   · `mission_steer` (text către misiunea activă) · `agent_run` · `jobs` · `analytics`
   (după WP-ETL) · `video`. Ieșire structurată (intenție + argumentul extras).
2. **Dispatch la handler-ele EXISTENTE** ale comenzilor `!` — routerul traduce, nu
   reimplementează. Zero logică nouă de execuție în gateway.
3. **Context-awareness:** dacă există misiune activă sau draft în așteptare, textul liber
   se interpretează ÎNTÂI în contextul ei — generalizează cazul deja existent (reply după
   ✏️ = `!mission revise`, gateway linia ~252). Asta e și punctul de cuplare cu WP-AL
   (steering).
4. **Fail-safe pe ieftin și inofensiv:** intenție ambiguă sau încredere mică → `chat`
   (comportamentul de azi). Acțiunile scumpe/cu efecte (pornit misiune, agent run) NU se
   execută direct din clasificare — merg pe cardurile cu butoane existente (draft → ✅).
   Chat-ul și query-urile read-only se execută direct.

**Capcane:** latența — clasificarea nu are voie să adauge secunde la chatul banal (de aceea
T1/T2 local, nu cloud); nu lăsa clasificatorul să devină un al doilea router semantic
paralel cu `decide_tier` — intenția decide ACȚIUNEA, tier-ul decide MODELUL, straturi
separate; fals-pozitivele pe `mission_new` ar fi enervante — pragul de încredere se
calibrează pe un set de fraze etichetate ținut în tests/.

**Acceptare:** „pornește o misiune care face X" fără prefix → card de draft cu butoane ·
„cât am cheltuit azi?" → răspuns din usage · mesaj banal → chat normal, fără regresie de
latență peste prag măsurat · toate prefixele `!` merg neschimbate · set de N fraze
etichetate în tests/ trece cu acuratețe minimă convenită · pytest verde.

### WP-SD — Self-development: Kage lucrează la Kage, de pe Telegram · efort: un weekend · după WP-NL (independent tehnic de el)

**Scop (13.07.2026, Stefan):** restanța reală a promisiunii WP12 — „construirea lui Kage
prin Telegram" are plumbing-ul (draft → branch → push → compare link), dar munca efectivă
pe repo-ul Kage e blocată arhitectural: misiunile rulează cu `cwd=PROJECT_ROOT`, iar
`_mission_git_branch` comută branch-ul checkout-ului VIU — agentul ar edita fișierele din
care rulează orchestratorul, pytest-ul misiunii ar concura cu producția, iar un restart
cerut de propriile modificări ar fi nedeterminist.

**Pași:**

1. **Izolare pe git worktree:** misiunile care țintesc repo-ul Kage rulează într-un
   worktree separat (`~/.kage-worktrees/<slug>`, branch `mission/<slug>`) — partajează
   `.git`-ul, dar NU comută checkout-ul viu; serviciile rulează neatinse pe branch-ul lor.
   Worktree-ul se creează la start de misiune și se curăță la final (păstrat la eșec,
   pentru autopsie).
2. **pytest în worktree:** cu `.venv`-ul rădăcinii dacă diff-ul nu atinge
   `requirements*.txt`; altfel venv efemer în worktree. Config de test, NU `kage_config.json`
   real și NU `cache_db/` viu.
3. **Smoke test opțional** (felie separată dacă se complică): pornește orchestratorul
   modificat din worktree pe porturi alternative (`:4101`) cu config de test → `/health` →
   raport în notificarea de WP.
4. **Fluxul de livrare:** push → PR spre `dev` → Stefan face merge din GitHub mobile →
   pas de deploy explicit: buton pe Telegram „🔄 pull + restart” → `git pull` pe checkout-ul
   viu + restart ANUNȚAT (regula „nu reporni fără să anunți userul" devine confirmare pe
   buton). Recovery-ul existent (missions/mission_wps în SQLite + watchdog WP12) acoperă
   restartul mid-misiune — de verificat cu un test, nu de reconstruit.

**Capcane:** două worktree-uri nu pot ține același branch — un slug de misiune reluat
trebuie să refolosească worktree-ul existent, nu să creeze altul; misiunea NU primește
scriere în afara worktree-ului ei (matricea de risc WP2 + confinement se aplică pe calea
worktree-ului); costul rulărilor pe repo-ul Kage e mare (context: CLAUDE.md + handoff) →
plafonul #7 obligatoriu înainte.

**Acceptare:** o misiune reală mică pe repo-ul Kage, pornită de pe telefon, livrează un PR
cu pytest verde rulat ÎN worktree, în timp ce serviciile vii rămân neatinse pe branch-ul
lor (verificat: `git -C PROJECT_ROOT branch --show-current` neschimbat pe toată durata) ·
merge + deploy cu confirmare pe buton · restart simulat mid-misiune → misiunea își reia
poziția · worktree-ul curățat la succes, păstrat la eșec · pytest verde.

### WP-AL — Agentic loop adevărat: reflect → replan + steering · efort: un weekend+ · după WP13

**Scop (13.07.2026, Stefan):** WP11 execută un plan FIX, secvențial; WP13 adaugă advisor,
`ask_user` și coadă — dar bucla rămâne „plan → execută". Un agentic loop adevărat închide
cercul: plan → act → verify → **reflect → replan**, plus capacitatea lui Stefan de a
redirecționa misiunea din mers cu text liber, fără s-o omoare și s-o refacă.

**Pași:**

1. **Replanning** (promptul de reflecție + schema de compresie a contextului: **Stefan,
   ghidat** — §8): după fiecare WP terminat (și la orice eșec de verificare), un pas de
   reflecție cu context comprimat: „planul rămas mai e valid după ce am aflat?" →
   propunere de amendament la `mission.md` (adaugă/taie/reordonează WP-uri rămase) →
   trece prin advisor (WP13 pct. 1) → card la Stefan (✅ aplică / ✏️ modifică / ⏭ ignoră) →
   `mission.md` rescris + commit pe branch-ul misiunii (istoricul planului = istoricul git).
   WP-urile deja ✅ nu se ating niciodată.
2. **Steering:** mesaj liber în timpul misiunii (rutat de WP-NL ca `mission_steer`) se
   injectează ca instrucțiune în sesiunea agentului la următoarea graniță sigură (între
   tool-calls sau la startul următorului WP) — nu întrerupe brutal execuția; confirmarea
   „am integrat: [rezumat]" vine pe Telegram.
3. **Buget de buclă:** max N replanning-uri per misiune (config); peste N → anti-rabbit-hole
   (WP13 pct. 3) decide continuă/pivotează/întreabă. Fără asta bucla reflect→replan poate
   deveni rumegare infinită pe cost.

**Acceptare:** misiune cu un WP devenit inutil pe parcurs (fixture) → agentul propune
tăierea lui, Stefan aprobă cu buton, `mission.md` + git reflectă schimbarea · un mesaj de
steering mid-misiune schimbă verificabil comportamentul următorului WP · limita N oprește
replanning-ul (test) · WP-urile deja ✅ rămân neatinse la orice amendament · pytest verde.

### R0 — Python/API quality: contracts, idempotency, pagination, rate limits · efort: un weekend · după WP-SD · Codex proposal: R0 (acceptat, §4)

**Scop (14.07.2026):** JD-ul Revolut cere explicit „well-designed, scalable APIs" — Kage are
azi endpoint-uri FastAPI funcționale, dar fără contracte versionate, fără protecție la
re-trimitere, fără paginare pe listele care cresc (usage, run ledger, missions) și fără rate
limiting. E ieftin de făcut acum, incremental peste ce există deja, și direct apărabil la
interviu fără nicio poveste — e literalmente ce scrie în JD.

**Pași:**

1. **OpenAPI contracts + versionare:** FastAPI generează deja schema; adaugă `response_model`
   explicit pe endpoint-urile principale (`/chat/completions`, `/api/missions*`, `/api/pending`,
   `/analytics/*` după WP-ETL) acolo unde lipsește, plus un prefix de versiune (`/v1/...`) pe
   ce nu e deja acolo. Nu rescrie endpoint-uri care merg — doar tipează contractul.
2. **Idempotency keys:** pe endpoint-urile care pornesc muncă (creare misiune, `!run`,
   agent task) — header `Idempotency-Key` opțional, cache scurt (SQLite/Postgres după WP-PG)
   care întoarce același răspuns la retrimitere în fereastra de idempotență. Rezolvă un caz
   real: retry de rețea de pe Telegram/mobil care ar porni misiunea de două ori.
3. **Pagination:** pe listele care cresc nemărginit (`/api/missions`, usage log, run ledger) —
   `limit`/`cursor` sau `limit`/`offset`, cu default rezonabil; azi multe din ele întorc tot.
4. **Rate limiting:** un limiter simplu (token bucket per IP/token, in-process — nu Redis
   pentru single-user) pe endpoint-urile publice ale gateway-ului; scop = robustețe
   demonstrabilă, nu apărare reală (sistemul e single-user, local).
5. **Integration/contract tests:** teste care lovesc endpoint-urile prin `TestClient` (nu doar
   funcțiile interne), verifică statusul, forma răspunsului și cazurile de eroare (400/404/429).
6. **Profiling + load test punctual:** `pytest-benchmark` sau un script simplu (`hey`/`wrk`)
   pe 2–3 endpoint-uri fierbinți (`chat/completions` non-stream, `/api/missions`); raportează
   latența p50/p95 — dovadă de „am măsurat", nu doar „am scris cod".

**Capcane:** nu introduce breaking changes pe clienții existenți (Telegram gateway, frontend,
widget) — versionarea și idempotency-ul sunt aditive; nu peste-inginerească rate limiting-ul
cu infrastructură distribuită pentru un sistem single-user local.

**Acceptare:** cel puțin 3 endpoint-uri cu `response_model` explicit + prefix de versiune ·
idempotency key funcțională pe crearea de misiuni (test: retrimitere → același rezultat, nu
misiune dublă) · paginare pe `/api/missions` și usage · rate limiter activ cu test pe limita
depășită · suite de contract tests noi · raport de latență p50/p95 pe 2–3 endpoint-uri ·
pytest verde.

### WP-PG — Migrare stare partajată + telemetrie pe PostgreSQL · efort: un weekend · după R0 · a doua din pista data-stack (§4) · ✅ IMPLEMENTAT (16.07.2026)

**Scop:** azi coordonarea cross-proces (orchestrator, gateway, mission_runner, widget, jobs)
merge prin fișiere cu lock (`status.json.lock`, `scheduled_tasks.json.lock`), iar istoricul,
usage-ul și ledger-ul stau într-un SQLite deschis cu `check_same_thread=False` — funcționează,
dar e fragil la scriitori concurenți și nu duce query-uri de raportare serioase. Postgres
rezolvă ambele și e fundația pentru WP-ETL, WP-AF și WP10.

**Pași:**

1. Postgres 16 **nativ prin Homebrew** (`brew install postgresql@16`), NU în Docker Desktop —
   un VM rezident de 3–4GB contrazice regula de RAM din WP-G2; footprint-ul nativ e ~50MB.
   Intră în lanțul launchd (`start_all.sh`), cu retry de conexiune la startup-ul
   orchestratorului (launchd nu garantează ordinea de pornire — nu muri la boot).
2. Schema (proiectată de **Stefan, ghidat** — valoare de interviu, §8): `usage`,
   `runs`/`run_events` (ledger-ul WP8), `missions`/`mission_wps`, `job_runs`,
   `scheduled_tasks`, `status`. **Chat history + ChromaDB RĂMÂN pe loc** (`cache_db/`) —
   migrarea lor nu stinge nicio durere și ar atinge degeaba cache-ul semantic.
3. Migrare cu cutover + backup: număr de rânduri verificat per tabel (zero pierdere);
   backfill-ul arhivei `usage_log.jsonl` intră la WP-ETL. Extinde backup-ul nocturn WP-B cu
   `pg_dump` în același tar.gz.
4. Acces prin psycopg, SQL de mână — NU introduce un ORM pentru ~7 tabele (SQL-ul explicit
   e chiar valoarea de interviu). Sync e consistent cu patternul sqlite3 existent; pool
   async doar dacă apar blocaje măsurate.
5. Curățenie post-cutover: șterge căile de cod pe file-lock și fișierele `.lock` migrate.

**Capcane:** `status_widget.py` (venv separat) citește `status.json` — la prima felie NU-i
adăuga dependență de Postgres: orchestratorul continuă să scrie `status.json` ca view derivat
și doar sursa de adevăr se mută. Testele care ating DB-ul au nevoie de o instanță de test
(template DB sau schema per test) — nu lăsa pytest să depindă de Postgres-ul „de producție".

**Acceptare:** scrierile de telemetrie/stare merg în Postgres · lock-urile migrate șterse
(sau documentate ca view derivat) · `pg_dump` în backupul nocturn · restart test: orchestrator
pornit înaintea Postgres nu moare · pytest verde + teste pe stratul de acces.

**Livrat (16.07.2026):** `pg_store.py` (conexiune sync + RLock, autocommit, retry cu
deadline la startup + reconectare leneșă per operație, DDL, tranzacții explicite) · cele
7 tabele rewire-uite în orchestrator (psycopg, SQL de mână, fără ORM; timestamps TEXT
ISO-8601 — tipizarea strictă vine la WP-ETL în staging) · cutover idempotent la startup
cu verificare de rânduri ÎN tranzacție — verificat live pe copia datelor reale (:4101,
DB `kage_smoke`): usage 26/26, runs 14/14, run_events 68/68, missions 1/1, mission_wps
3/3, job_runs 3/3, restart fără re-migrare, scheduler încarcă din PG · `status.json` =
view derivat scris atomic (tmp+rename; widget-ul neatins) · FileLock eliminat complet ·
`pg_dump` în arhiva nocturnă + restore documentat (RESTORE.md §2a) · Postgres pornit de
`start_all.sh` · teste: fixture PG pe DB temporar per sesiune (specul „nu Postgres-ul de
producție"), **507 verzi, +12 noi** (singurul fail = `test_push_to_bare_remote`,
pre-existent) · pornirea cu PG mort verificată live (:4102 — servește degradat +
notifică pe Telegram). Notă: „schema proiectată de Stefan, ghidat" din pasul 2 a fost
înlocuită de decizia „pista de învățare v2" (§4, 15.07.2026) — fișa de interviu:
`docs/fise-interviu/wp-pg-postgres.md`. **Cutover-ul de PRODUCȚIE = primul restart cu
codul nou** (merge → restart anunțat): `postgresql@16` instalat + pornit, baza `kage`
creată; sursele vechi (SQLite/JSON) rămân pe disc ca arhive, nu se șterg.

### WP-ETL — Pipeline de analytics peste telemetrie (+ point-in-time lineage) · efort: un weekend · după WP-PG · Codex proposal: T3 (acceptat, integrat, §4)

**Scop:** Kage generează date (cost per run, decizii de rutare, cache hit rate, `job_runs`,
paper trades) dar nu are raportare; WP10 ar ajunge să facă query-uri ad-hoc. Pipeline-ul
clasic raw → staging → mart e literal linia „data pipelines for reporting, analytics and
data science" din JD-ul deciziei §4. Propunerea Codex T3 (point-in-time lineage) e integrată
aici, nu ca WP separat — e SQL peste schema deja proiectată la WP-PG, nu rigoare de trading.

**Pași:**

1. Modelul de date (Stefan, ghidat — §8): mart-uri `daily_usage` (cost/tier/model/hit-rate
   pe zi), `mission_stats`, `trading_daily`. Agregarea = SQL idempotent pe zi
   (`INSERT ... SELECT` cu delete-and-rewrite pe partiția zilei), NU pandas — datele-s mici,
   patternul contează.
2. **Lineage (T3):** fiecare rând din tabelele raw primește `event_time` (când s-a produs),
   `available_time` (când a devenit vizibil pipeline-ului), `ingested_time` (când a intrat în
   Postgres), sursă și `dataset_snapshot_id` pe fiecare rulare de agregare — suficient să
   răspundă „ce știa sistemul la momentul X", fără versionare completă de date.
3. **Teste de data-quality pe lineage:** future timestamps (event_time > ingested_time →
   respins), duplicate/gaps pe zi, `available_time` lipsă → rândul nu intră în mart-ul zilei
   respective.
4. Backfill pe tot istoricul: SQLite-ul vechi + arhiva `usage_log.jsonl` → povestea de
   interviu „migrare + backfill idempotent, cu lineage reconstruit din timestamp-urile
   originale".
5. Job nightly rulat inițial din APScheduler; devine primul DAG real la WP-AF.
6. `GET /analytics/daily` — endpoint de raport, consumat ulterior de WP10 (+ G5).

**Acceptare:** backfill complet pe istoric · nightly idempotent (rulat de 2× pe aceeași zi =
același rezultat, demonstrat cu test) · fiecare rând din mart are `dataset_snapshot_id` ·
testele de data-quality (future timestamp, duplicate, gap) trec · endpoint-ul întoarce seria
zilnică · pytest verde.

### WP-AF — Airflow pentru job-urile batch · efort: un weekend · după WP-ETL

**Scop:** cron-urile batch trăiesc azi ÎN procesul orchestratorului (APScheduler): mor odată
cu el — exact cazul confirmat 09.07 (scanul de 19:00 pierdut tăcut), peticit cu watchdog în
WP12. Airflow externalizează batch-urile cu retries, backfill și istoric de rulări — și e
keyword explicit în JD (§4).

**Pași:**

1. `airflow standalone` cu LocalExecutor, metadata în Postgres-ul WP-PG (NU SQLite-ul
   default), pornit din lanțul launchd. Țintă de footprint: sub ~1GB rezident; dacă nu iese,
   rulează-l în Colima pornit/oprit în jurul ferestrei batch (patternul RAM din WP-G2).
2. Migrează DOAR batch-urile (DAG-urile în sine le scrie **Stefan, ghidat** — §8; instalarea
   și cablarea launchd = model): agregarea nightly (WP-ETL), backupul (WP-B), scanul de joburi
   (WP-J), calibrarea săptămânală de trading. **NU migra** cron-urile safety-critical /
   near-realtime: killswitch-ul de trading (`*/5`) și heartbeat-ul (WP12) RĂMÂN în
   APScheduler, în proces — fail-closed-ul lor nu are ce căuta într-un scheduler extern.
3. DAG-urile apelează endpoint-urile existente (`POST /jobs/scan` etc.) — Airflow
   orchestrează, nu reimplementează; logica rămâne în orchestrator.
4. Alerting pe eșec de DAG → Telegram (canalul existent).

**Capcane:** dublă programare — șterge cron-urile migrate din APScheduler ÎN ACELAȘI PR,
altfel rulează de două ori. Watchdog-ul WP12 verifică `next run` în trecut — actualizează-l
să nu alerteze fals pentru joburile mutate în Airflow.

**Acceptare:** cele 4 batch-uri rulează din Airflow (istoric în UI) · eșec simulat → retry +
alertă Telegram · killswitch-ul neatins în APScheduler · cron-urile migrate șterse din
orchestrator · pytest verde.

### WP-KF — Kafka ca transport de evenimente · AMÂNAT (13.07.2026) · doar după WP-ETL + WP-AF

**Decizie (§4):** amânat deliberat. Justificarea tehnică onestă de azi (scriitori concurenți
pe file-locks) dispare odată cu WP-PG; rămâne valoarea de CV (keyword-ul din JD cel mai greu
de „fake-uit"). Intră DOAR dacă, după WP-ETL + WP-AF livrate, mai există apetit — forma
decisă: **Redpanda single-node** (compatibil Kafka API, un singur binar, footprint mic — NU
cluster Kafka+ZooKeeper), producer-i în orchestrator/gateway (evenimente: decizie de rutare,
lifecycle de agent, alertă de buget), un consumer care scrie în Postgres = stratul de ingest
al pipeline-ului WP-ETL. Kafka devine transportul pipeline-ului, nu un gadget paralel.
Speculul complet se scrie abia la promovarea în lanț — nu-l detalia acum.

### WP-T — Laborator de trading agents (crypto / prediction / forex) · efort: incremental, pe faze · după WP11 (bucla de iterare e a lui)

**Stare T1 (07.07.2026):** fundația + REORIENTARE spre Quant Lab.

- **Slice 1 (fundația):** `trading/ledger.py` (`trading.db` în `cache_db/`) + `safety.py`
  (`assert_paper_only`). Config paper-only în `kage_config.example.json`. (PR #25)
- **Slice 2 (freqtrade):** `trading/runner.py` — punte subprocess `.trading-venv` → ledger,
  cu `assert_paper_only` înainte de rulare. **PĂSTRAT.**
- **Reorientare (spec `quant_lab_claude_code_prompt.md`, aprobat de Stefan 07.07.2026):**
  abordarea naivă „N backtests + LLM mută strategia" e înlocuită cu **metoda științifică** —
  edge-ul NU se găsește prin câteva backtests in/out-of-sample. Design:
  `docs/QUANT_LAB_DESIGN.md` (invarianți, 3 bucle, validare statistică, registru de ipoteze,
  routing LLM) + `docs/QUANT_LAB_BACKLOG.md` (5 etape).
- **`trading/nocturnal.py` NAÏV = neutralizat** (guard) — încălca invarianți (LLM scrie execuția,
  selecție pe profit IS). Se înlocuiește cu Actor→Critic→Validare (Etapa 5).
- **Etapa 1 (validare statistică — PRIORITATE #1) LIVRATĂ:** `trading/validation.py` — bootstrap,
  permutation, PSR, **Deflated Sharpe Ratio** + **PBO/CSCV** din papers (numpy +
  `statistics.NormalDist`, fără mlfinlab comercial). Verdict SEMNAL/ZGOMOT deflatat pe contorul
  de trial-uri. Teste `tests/test_trading_validation.py` (+14; testul central: cea mai bună din
  50 strategii de zgomot pur = ZGOMOT după deflatare).
- **Decizii Stefan:** Critic prin **OpenRouter** (model ieftin performant, nu Claude direct),
  buget 5–10€/lună; kill-switch −15%; perechi BTC/ETH 5m.
- **Etapa 1.2 LIVRATĂ:** `trading/report.py` — verdict peste ledger real (CLI `python -m
  trading.report`). Pe datele actuale: 0 semnale, 2 ZGOMOT (confirmă teza).
- **Etapa 2 (2.1+2.2) LIVRATĂ:** contor global de trial-uri (`trials`, folosit de DSR) +
  **kill-switch determinist** (`trading/killswitch.py`, −15% drawdown, halt+Telegram, ridicare
  manuală, zero LLM). 2.3 (buget OpenRouter) amânat la Etapa 5 (Criticul nu există încă).
- **Etapa 3 LIVRATĂ:** bucla de context zilnic — `market_data.py` (funding/OI/klines Binance +
  circuit breaker), `regime.py` (regim + bias NON-LLM: vol realizată + trend EMA200, rule-based;
  HMM ca upgrade viitor), `daily_context.py` (scrie `daily_context.json` + tabel `daily_context`;
  `bias_allows(side)` = limitatorul pentru strategii). Teste +11.
- **Etapa 4 LIVRATĂ:** registru de ipoteze cu **pre-registration** (`hypotheses`/`predictions` +
  `trading/hypotheses.py` — predicție fără ipoteză pre-înregistrată/temporal validă = refuzată,
  invariant #2), **calibrare** (`calibration.py`: Brier + coverage), **baseline** (`validation.
  signal_moves_distribution` KS+permutation) + `event_study`. Teste +10.
- **Etapa 5 LIVRATĂ (WP-T COMPLET):** bucla nocturnă **Actor→Critic→Validare** înlocuiește
  `nocturnal.py` naiv. `trading/actor.py` (Qwen local, ≤3 ipoteze în format impus, NU cod,
  pre-înregistrate — invariant #1+#2), `trading/critic.py` (OpenRouter, aprobă ≤1, **buget-gated**
  cu fallback local), `trading/budget.py` + tabel `api_costs` (**2.3**: plafon 7€/lună, contor cost),
  `trading/llm.py` (client chat injectabil), `trading/costs.py` (**costuri stresate**, slippage
  dublat — invariant #4, cuplat în `report.py --stressed`), `trading/pipeline.py`
  (`NightlyPipeline.run_once` → raport de dimineață; `promoted=False` mereu, **zero auto-promovare**).
  Config: bloc `trading.actor`/`trading.critic` + `openrouter_api_key` (secret). Teste +19 → **suita 350 verzi**.
- **Integrare LIVRATĂ (08.07.2026):** cele 3 bucle + calibrarea sunt cablate în scheduler-ul
  orchestratorului (`orchestrator.py`, activate doar când `trading.enabled`): kill-switch (`*/5`),
  context zilnic (`0 6`), research nocturn Actor→Critic (`0 3`), calibrare (`0 4 * * 1`). Trigger
  manual: `POST /admin/trading/{killswitch|context|nightly|calibration}`. `NightlyPipeline.from_config`
  construiește clienții LLM din config (fără cheie OpenRouter ⇒ Criticul rulează local). Raportul
  nocturn merge pe Telegram via `_notify`. Rămâne: **daemonul freqtrade dry-run** (Bucla 1 execuție,
  proces separat, cere `.trading-venv`) + `bias_allows` în `SampleStrategy` (fișier gitignored) —
  **= felia „T1-exec" din reordonarea 09.07.2026** (devreme: criteriul „~3 luni de paper" e timp
  calendaristic — ceasul pornește abia când daemonul rulează).
- **BUG fundație reparat (08.07.2026):** `runner.backtest_to_ledger` înregistra `amount=stake_amount`
  (noțional USDT), dar `close_paper_trade` face `pnl=(exit−entry)×amount` → pnl umflat cu ~prețul de
  intrare (kill-switch raporta −126685%). Fix: `amount` = cantitatea în bază (freqtrade `amount`, sau
  `stake/entry`); fee absolut = `stake×ratie`. Test de regresie în `test_trading.py`. **Datele vechi
  (305 paper_trades din rulările nocturnal naive) rămân pe scara greșită — decizie de curățare la Stefan.**

**Cross-cutting (Kage-global, post-proiect): audit de utilizare a modelelor.** Inventar al tuturor
punctelor unde Kage folosește un model — **local** (Qwen via LiteLLM/Ollama), **Claude prin abonament**
(tier-urile Claude + misiuni SDK), **OpenRouter prin API** (Criticul WP-T) — cu rol×volum×cost×
sensibilitate la calitate, și decizii explicite de **upgrade spre calitate** unde merită. Surse de cost:
`trading.api_costs` (OpenRouter) + contorul budget #7 (Claude) + rutarea 6-tier din `orchestrator.py`.

**Decizii (05.07.2026, Stefan):** paper-only până la criterii clare — promovarea pe bani
reali e DOAR manuală, niciodată decisă de agent. Crypto pe **freqtrade** (motorul:
backtest + hyperopt + dry-run + live prin ccxt; proiectul incipient al lui Stefan = sursă
de idei de strategii, nu de infrastructură). Prediction markets pe **Manifold** (bani virtuali „mana",
API oficial, boții permiși). Forex: faza 1 backtest local pe date istorice
(Dukascopy/HistData); faza 2 **OANDA practice** (conectarea a eșuat la prima încercare —
de depanat: token practice vs live, endpoint `api-fxpractice.oanda.com`); puntea MT5 de pe
laptopul Windows = plan C, fragilă (două mașini pornite non-stop).
**Polymarket real prin VPN = refuzat definitiv** — pe lista neagră ONJN (amenzi pentru
utilizatori până la 10.000 RON; decizia menținută de instanță 04.2026) + încălcare ToS →
fonduri înghețabile fără recurs. Se rediscută doar dacă apare cale licențiată.

**Arhitectură:** agenții = daemoni separați, 100% pe modele locale (NU taskuri Kage —
sunt procese long-running), stare + metrici în SQLite (`trading.db`: `experiments`,
`paper_trades`, `agent_status`). **Kage = supervizor:** health check, notificări Telegram
(semnal / drawdown / eroare), `!stop` îi oprește, secțiune în briefing (WP-D), tab Trading
în Mission Control (WP10) — read-only pe aceleași tabele.

**Bucla de îmbunătățire (miezul):** misiune nocturnă WP11 pe modele locale — rulează N
variante de strategie în backtest, scrie rezultatele în experiment ledger, Qwen local
analizează și propune mutațiile pentru noaptea următoare. **Reguli nenegociabile
anti-overfitting** (hyperopt „găsește" cu entuziasm strategii care mor pe date noi):
validare walk-forward + out-of-sample la orice promovare; fees + slippage modelate mereu;
o strategie intră în paper doar cu OOS pozitiv; discuția de bani reali abia după ~3 luni de
paper profitabil. Consiliere cloud: o sinteză săptămânală pe Sonnet, gated pe buget (#7).

**Filozofia de Cercetare (Causalitate vs Corelație):** Edge-ul real nu va fi găsit prin simplă 
optimizare de parametri pe indicatori tehnici (curve fitting pe o lună de date). Sistemul 
trebuie să caute **înțelegerea cauzală** ("de ce se întâmplă X și de ce acum?"). Odată 
ce agentul și Stefan observă un fenomen logic (ne-aleator), acesta trebuie transformat 
într-o ipoteză. Ipoteza este apoi supusă unor **simulări matematice riguroase (ex: Monte Carlo, distribuții statistice)** 
pentru a verifica dacă rezultatele converg statistic către așteptările noastre (demonstrând 
că nu e un simplu *random walk*). Freqtrade e doar executantul matematic, inteligența stă în 
formularea și dovedirea statistică a ipotezei.

**Harta ML vs LLM (09.07.2026)** — ce e fiecare strat, ca să nu se confunde rolurile:
LLM = DOAR Actor + Critic (by design, invariant #5). Statistică deterministă (nu ML, nu se
antrenează nimic) = `validation.py` (DSR/PBO/bootstrap/permutation), `killswitch`, `costs`,
`calibration`. Rule-based = `regime.py` (azi). **ML clasic intră în exact 3 locuri, toate
planificate:** (1) upgrade-ul HMM la regime detection (3.2 în backlog); (2) Dixon-Coles/
Poisson la T2 sports betting; (3) opțional, mai târziu: meta-labeling (López de Prado) +
purged cross-validation peste semnalele primare. Regula build-vs-buy: algoritmi standard
plug-and-play (sklearn/statsmodels), stratul de domeniu de mână; excepție deliberată HMM-ul
(de la zero, în modul de execuție ghidat din §4 — cine implementează și cum).

**Faze:** T1 crypto lab (freqtrade dry-run + ledger + buclă nocturnă) → **T2 sports
betting** (detalii mai jos; tras înaintea Manifold: testul de edge cel mai măsurabil — CLV —
și cele mai bune date istorice gratuite) → T3 agent Manifold (predicții pe mana + scor de
calibrare) → T4 forex (date istorice → OANDA practice) → T5 tab-ul din Mission Control.

**Piață candidată: Nasdaq 100 / index equities (idee 09.07.2026, Stefan).** De evaluat ca
piață mai promițătoare decât forexul pentru metoda Actor→Critic→Validare, din motive
*structurale*, nu de „ușurință de predicție":

- **Joc cu sumă pozitivă** — spre deosebire de forex (sumă zero/negativă după spread, fără
  drift), equities au equity risk premium (drift ~13–14%/an istoric pe NDX). Expectanță de
  bază pozitivă fără să bați piața; forexul nu oferă acest cadou și e cea mai eficientă piață
  (contraparte = bănci/macro cu informație de flux inaccesibilă retailului).
- **Anomalii documentate, cu contraparte economică clară** (exact „mecanismul cauzal" cerut
  Actorului): efectul overnight (aproape tot randamentul NDX vine din gap-ul close→open),
  fluxuri de rebalansare la final de lună/trimestru, gamma hedging dealer pe opțiuni 0DTE,
  bias comportamental retail concentrat pe tech.
- **Cost practic:** date daily gratuite bune; intraday de calitate costă. Paper trading gratuit
  prin **Alpaca API** (include QQQ) — înlocuiește stratul de date Binance→Alpaca, restul
  arhitecturii WP-T e agnostic la piață (Actor/Critic/validare/pre-înregistrare identice).
- **Capcană de backtest:** piața e închisă 17.5h/24 — strategiile intraday arată artificial
  bine dacă backtestul ignoră că nu poți ieși în gap-ul overnight; kill-switch-ul determinist
  trebuie regândit pentru o piață cu program (nu 24/7 ca crypto).
- **Verdict de prioritizare:** forex < crypto ≈ NDX. Crypto = cel mai bun mediu de *cercetare*
  (date gratuite perfecte, 24/7, piață încă retail-dominată — de-asta laboratorul e construit
  pe el); NDX = cea mai bună *expectanță de bază*. Nu urgent: se extinde la NDX doar dacă
  metodologia își dovedește valoarea pe crypto (ipoteze care supraviețuiesc validării), ca felie
  de lucru separată — nu rescriere.

**T2 — sports betting (design decis 05.07.2026):** legal — pariurile sportive sunt permise
în RO prin operatori licențiați ONJN; agentul DOAR analizează și ține pariuri virtuale.
**Nu plasează pariuri automat, niciodată, nici pe bani reali în viitor** (operatorii RO nu
au API public de plasare; botting-ul pe site-urile lor = încălcare ToS): agentul găsește,
Stefan decide.

- **Două fluxuri de cote, cu roluri diferite:** *referință* = Pinnacle prin The Odds API
  (linia sharp — etalonul pentru CLV; acolo NU se pariază, nici nu e licențiat RO);
  *acționabil* = casele lui Stefan: bet365/Unibet/Betfair sunt și licențiate ONJN și în
  The Odds API (verificare punctuală la început: cote API vs site-urile .ro pe 10–20
  meciuri), iar Superbet/Betano doar prin scraping pe endpoint-urile JSON interne ale
  propriilor frontend-uri (fără cont, fără login). Pariul virtual se înregistrează la cota
  acționabilă REALĂ din momentul semnalului, nu la una teoretică.
- **Provider per casă** în spatele aceleiași interfețe + circuit breaker (pattern-ul
  Ollama): un scraper căzut marchează casa indisponibilă, nu omoară pipeline-ul.
  Rate-limiting politicos (o citire la câteva minute, nu hammering). **Apify = plan B per
  provider, NU default** — local e gratis pe mașina always-on și nu e nevoie de browser
  (JSON simplu); nu există actori gata făcuți pentru casele RO (verificat 05.07.2026).
  Trigger-e pentru mutarea unei case pe Apify: IP-ul de acasă blocat → proxy rotativ; sau
  anti-bot serios (challenge Cloudflare) → browserele lor gestionate. Swap = config, nu
  rescriere.
- **Model:** probabilitățile vin din statistică clasică (Elo, Poisson/Dixon-Coles pe
  fotbal), NU din LLM; Qwen-ul local face doar extracție structurată din știri/RSS
  (accidentări, suspendări, rotații, oboseală de program) ca feature-uri. Semnal = prob.
  proprie + linia Pinnacle vs cotele caselor soft RO (clasicul value betting — casele soft
  sunt lente și des greșite); pariu virtual doar peste un prag de diferență.
- **Backtest din prima zi:** CSV-urile gratuite football-data.co.uk — cote bet365
  (deschidere + închidere) pe ani întregi de fotbal european, adică o casă la care Stefan
  chiar poate paria. **Metrica de edge: CLV susținut pe eșantion mare** (+ ROI) — nu „pe
  profit luna asta", care poate fi noroc.

**Acceptare (T1):** un ciclu nocturn complet fără intervenție (N backtests → ledger →
propuneri noi) · dry-run-ul freqtrade raportează pe Telegram · nicio cale de cod nu poate
plasa un ordin real (fără chei live în config, verificat cu test) · pytest verde.

**Acceptare (T2):** cote live din ≥3 case în `trading.db`, dintre care min. una prin
provider-scraper · un provider oprit nu blochează restul (test) · pariul virtual se
înregistrează la cota acționabilă reală · raport CLV rulat pe backtest-ul football-data ·
nicio cale de cod nu poate plasa un pariu real · pytest verde.

### G1-minim — KageBench redus: eval harness pentru misiuni · efort: o seară–un weekend · după WP-G2 · Codex proposal: G1 (acceptat, formă minimă, §4)

**Scop (14.07.2026):** propunerea Codex completă (G1 — benchmark generalizat, replay pe 3
executori, regresie automată) e un proiect de săptămâni fără cerere directă din JD. Forma
minimă acceptată e suficientă ca regression gate și ca „eval-driven development" demonstrabil
la interviu, fără costul întregului registru.

**Pași:**

1. **10–15 taskuri fixe**, fiecare cu: stare inițială (fixture/worktree curat), instrucțiune,
   tool-uri permise, criteriu de acceptare verificabil automat (nu subiectiv) — amestec de
   taskuri generale Kage (ex. „adaugă un endpoint X cu test") și taskuri din WP-T (ex.
   „rulează validarea pe fixture-ul Y, raportează p-value").
2. **Runner:** rulează fiecare task prin executorul curent (Claude, în worktree-ul din WP-SD),
   colectează: succes complet/parțial, cost EUR (din ledger-ul WP8), latență, turns, tool
   calls, aprobări cerute.
3. **Regression gate:** rulare pe cerere (nu la fiecare commit — cost) înainte de WP-uri mari;
   raport comparativ cu rularea anterioară (regresie de succes sau cost → semnal, nu blocaj
   automat).
4. **NU intră în forma minimă:** replay pe Codex/Qwen ca executori paraleli, security
   benchmark separat (G3, rămâne `proposed`), UI dedicat — un raport text/JSON e suficient
   pentru acum; consumat de WP10/G5 dacă se justifică ulterior.

**Acceptare:** 10–15 taskuri definite cu criterii automate · runner-ul produce un raport cu
metricile de mai sus · o regresie introdusă deliberat într-un task (fixture) e detectată de
raport · pytest verde.

### WP10 (#15B) — Kage Mission Control · efort: o lună+ de seri · depinde de WP1+WP8

Frontend Next.js + CopilotKit pe AG-UI: endpoint SSE `/agui` care traduce `runs`/`run_events`
în evenimente AG-UI; panouri: agent cards live, activity feed, buget/cost, cache/memorie,
inbox aprobări (`/api/pending` există), briefing-uri de la agenții programați, tab Trading
(read-only peste `trading.db` din WP-T: starea agenților, curba paper P&L, experimentele). `kage.html` se
pensionează la paritate. Referințe de design în `KAGE-EVALUARE.md` §3.12.
**Promptul de design e gata:** `design/PROMPT-DESIGN-UI.md` — Stefan îl rulează în Claude Design;
output-ul (direcție vizuală + layout-uri + componente) devine specul vizual al acestui WP.

**Amendament G5 — observabilitate pe rezultate (14.07.2026), Codex proposal: G5 (acceptat,
integrat, §4):** panoul de telemetrie nu se oprește la transcript/cost brut — arată și rata de
succes pe tip de task, costul mediu per tip, approval rate, retry rate și taxonomia eșecurilor
(din ledger-ul WP8 + raportul G1-minim, dacă există). Nu e un panou separat — sunt câmpuri
suplimentare pe panourile deja specificate mai sus (activity feed, buget/cost).

**Pensionare `kage.html` ✅ FINALIZATĂ (07.07.2026):** `kage.html` **șters**; ruta `/chat`
redirectează acum (307) la Mission Control (`:{MISSION_CONTROL_PORT}`, default 3001). Cele 3
gap-uri de paritate care blocau ștergerea au fost portate în Mission Control înainte:

1. **Sesiuni + istoric persistent** — ✅ ChatPanel cu selector de sesiuni + „conversație nouă",
   peste `/api/sessions` + `/api/history`; cheia localStorage `kage_session` e **partajată cu
   fostul kage.html** (sesiuni comune). `/api/chat` forwardează `X-Session-Id`.
2. **Task runner cu dropdown de cwd** — ✅ `TaskPanel` (⌘K): mod Claude/Sysrun (Gemini/Swarm
   retrase la WP-RMG, 13.07.2026) + dropdown cwd din `/api/config`, stream peste `/task/run`.
3. **Disponibilitate always-on** — ✅ `start_all.sh` pornește Mission Control pe `:3001`
   (`scripts/start_frontend.sh`), idempotent + non-fatal, flag `--no-ui` pentru skip.

Config nou: `mission_control_port` în `kage_config.json` (default 3001). UI-ul web al proiectului
e acum exclusiv `frontend/` (Next.js).

### Kage Terminal — aplicație desktop macOS + DMG · idee Stefan (15.07.2026) · după WP10

**Scop:** Kage se livrează și ca `Kage.app` într-un DMG, cu fereastră desktop macOS și icon
propriu. Nu este o rescriere în C++/Qt: Mission Control rămâne UI-ul existent, iar nucleul
FastAPI/self-hosted rămâne local. Terminalul este stratul de produs care pornește, supraveghează
și oprește robust serviciile existente.

**Arhitectură țintă:** shell Tauri/macOS WebView → Mission Control împachetat → sidecar FastAPI
pe loopback. App-ul alege un port local, așteaptă `/health` înainte să afișeze UI-ul, păstrează
logurile/configul în `~/Library/Application Support/Kage` și oprește procesele copil la Quit.
Secretele rămân în Keychain/config local, niciodată în bundle. UI-ul Next.js nu mai depinde de
`npm run start` în instalația utilizatorului; se livrează compilat.

**Constrângeri explicite:** Ollama și modelele locale (mai mulți GB), Claude CLI și eventualele
unelte media rămân dependențe detectate la prima pornire, cu diagnostic și setup explicit — nu
se pretinde un „single binary" fals. Nu se copiază cod din FinceptTerminal; acesta e referință
de produs/UX, nu bază tehnică sau dependență.

**Pachete:**

1. POC `Kage.app`: lifecycle local (start, readiness, logs, Quit) + Mission Control într-o
   fereastră WebView; smoke test pentru pornire repetată și port ocupat.
2. Release DMG: bundle Python/frontend, configurare first-run, health diagnostics, icon,
   versionare și update manual. Semnare/notarizare înainte de distribuție în afara Mac-ului
   personal.

**Acceptare:** DMG drag-and-drop → `Kage.app` pornește fără Terminal; Mission Control apare
doar după health check; Quit nu lasă procese Kage orfane; lipsa Ollama/model/CLI produce un
mesaj acționabil, nu un crash; `start_all.sh` rămâne cale de dezvoltare suportată.

### Restul (ordinea de aici e ÎNLOCUITĂ de „Reordonare completă 09.07.2026" din capul §5 — #7 e acum PRIMUL item; specurile rămân valabile)

- **#12 Skills**: folder `skills/` cu 3–5 SKILL.md scrise de mână; symlink în `.claude/skills`
  la cwd-ul rulărilor; `!skill list/new`; auto-distilare abia după WP8/WP9, draft + aprobare.
- **#14 Push-to-talk Mac → „Hey Jarvis"**: etapa 1 hotkey în widget (pynput + sounddevice →
  `/v1/audio/transcriptions` → TTS Piper ro_RO/`say -v Ioana`); etapa 2 `voice_daemon.py` cu
  RealtimeSTT + openWakeWord.
- **#7 Budget v2 ✅ (12.07.2026) — plafonul-gate livrat** (era PRIMUL item, reordonare
  09.07.2026; tras inițial în față pe 05.07.2026). Spec inițial: parsează `total_cost_usd`
  din evenimentul `result` → buget în bani/zi; gate pe `task_run` și pe fallback-ul
  LiteLLM→cloud. Stare 09.07.2026: parțial — colectarea `cost_usd` per run (WP9) și afișarea
  EUR existau; lipsea plafonul-gate.
  **Cum s-a livrat (12.07.2026, decizia lui Stefan: top-up ~10 € OpenRouter care trebuie să
  reziste luni — implicit NU se cheltuie nimic):** `api_budget.py` (`SpendGate`) = gardă
  GLOBALĂ pe bani reali, fail-closed: `api_budget.enabled: false` (default) → niciun apel
  plătit; modelele gratuite (prețuri 0, ex. `:free`) trec și cu switch-ul oprit; plafoane
  `monthly_cap_eur` (10) + `daily_cap_eur` (1, anti-buclă). Sursa cheltuielilor = tabela
  `api_costs` (`cache_db/trading.db`); rolurile viitoare (advisor WP13, failover) înregistrează
  tot acolo cu `role` propriu și moștenesc gate-ul. Enforcement azi în
  `trading/pipeline.from_config` (Criticul plătit → local când gate-ul refuză, cu motivul în
  raportul de dimineață) + stare în `_mc_budget()["api"]` (header MC). **Scope conștient:**
  gate-ul acoperă căile pe bani reali (OpenRouter azi, API failover mâine); `task_run`/rutarea
  Claude rămân pe abonament (cost marginal 0) sub bugetul de apeluri `max_cloud_calls_per_day`
  — gate-ul în bani pe ele ar fi teatru. Teste: 384 → 401.
  **Afișare în EUR** (decizia lui Stefan — plătește în EUR): intern totul rămâne USD (așa
  raportează API-urile), conversia doar la afișare, curs configurabil `eur_usd_rate` în
  `kage_config.json` (default static, ex. 0.92; nu chema API de curs valutar pentru asta).

  **Structura de sub-bugete + modele per rol (decizia lui Stefan, 09.07.2026** — pe prețuri
  OpenRouter verificate live 09.07.2026; consumul real estimat e de 5–10× sub plafoane,
  fiindcă la volum single-user calitatea e practic gratuită — NU optimiza prețul, alege
  modelul potrivit rolului):

  - **Total credite API: doar Trading (Critic), ~7 €/lună; plafonul global rămâne 10€/lună
    din #7 (`api_budget.py`).** Advisor și failover NU mai consumă credite API (retrase,
    13.07.2026) — top-up de 10$ o dată ajunge acum estimat 12+ luni.
  - **Trading (Critic): 7 €/lună** — plafonul `monthly_cap_eur` EXISTĂ deja (`trading/budget.py`).
    Model: `tencent/hy3:free` până pe **21.07.2026** (expiră gratuitatea), apoi
    **`deepseek/deepseek-v4-pro`** (0.435/0.87 $/M — raționament economic tăios, ieftin;
    consum real ~0.10 $/lună). Notat și în `kage_config.example.json` (`_comment_model_plan`).
  - **Advisor (WP13): 0 €/lună — pe Qwen local, RECONSIDERAT (13.07.2026, Stefan).** Nu
    Gemini prin OpenRouter API (retras din plan, nu se justifică cheltuiala acum) — model
    implicit = **Qwen local (T2)**, gratuit, suficient de diferit de Claude pentru dezacord
    genuin pe rolul de review. „LLM council" (mai multe modele pentru decizii importante) e
    doar o idee capturată, neangajată — condiționată de un WP viitor, nu de acum.
    **Decizia 13.07.2026 (Stefan):** dacă/când apare abonamentul ChatGPT Plus (20$/lună,
    pentru Codex CLI), Advisorul trece pe Codex/GPT în locul lui Qwen — cost marginal 0 pe
    un abonament deja plătit, diversitate reală (alt „creier" decât Claude). Qwen rămâne
    fallback. **Nu blochează WP13** — WP13 se construiește ACUM pe Qwen local; swap-ul e un
    WP mic separat (**WP13b**, vezi mai jos), oricând după ce abonamentul există.
    **Excepție notă la decizia WP-CX** (mai jos): apelul programatic Codex→Advisor e permis
    ÎNAINTE de WP-G2 — rol read-only de review (obiecții pe text), risc mult mai mic decât un
    executor cu tool-uri de scriere; rolul de AL DOILEA EXECUTOR (WP-CX) tot așteaptă WP-G2.
  - **Failover misiuni pe API plătit — RETRAS (13.07.2026, Stefan).** Motivul inițial
    (continuitate de comportament la resume) nu justifică riscul: bucle agentice = sute de mii
    de tokeni/WP, ar putea arde bugetul lunar într-o rulare. Cazul real ("misiunea lovește
    rate-limit-ul abonamentului Claude mid-execuție") e deja acoperit GRATUIT de WP11:
    `_mission_schedule_resume` + `parse_rate_limit_reset` (`orchestrator.py`) detectează ora
    de reset din eroare, pun misiunea în pauză, o reiau automat la ora exactă, cu notificare.
    Failover-ul plătit ar fi cumpărat doar viteză (nu aștepți reset-ul), cu risc de buget
    nejustificat — nu intră în plan.
  - **Sinteza săptămânală de trading: 0 €** — pe abonamentul Claude, nu pe API.
  - **Ce rămâne local (nu se cumpără):** Actorul (Qwen — diversitatea față de Critic e o
    virtute), embeddings, chat-ul de zi cu zi pe tier-urile existente.
- **#6 Memorie v2**: extracție de fapte pe T2 la final de conversație + job de consolidare la
  03:00 (dedup global, fuziune, bloc `user_profile` injectat mereu) — pipeline nocturn coerent
  cu vacuum 04:00 / backup 05:00.
- **#9 Tools locale pentru T2**: `local_tools.py` (vault read/write, status, schedule) prin
  function calling LiteLLM, buclă max 5 iterații, gate prin `evaluate_risk` importat.
- **RAG pe documente/cursuri** (idee 05.07.2026, acceptată ca „ulterior"): `!index <folder>`
  → colecție ChromaDB per subiect + retrieve în context. Țintă de calendar: înainte de
  sesiunea din ianuarie 2027 — nu are sens mai devreme.

---

## 6. Governance: constrângeri ca orchestratorul să nu compromită mașina

**Starea onestă (04.07.2026):** agenții rulează ca userul lui Stefan, cu Bash nelimitat și
`--permission-mode auto`; singura apărare e `risk_hook.py` — pattern matching pe comenzi.
Pattern-urile se ocolesc trivial și *neintenționat* (agentul scrie un script și îl rulează,
`python -c`, pipe-uri) — nu pentru că cineva atacă, ci pentru că un LLM găsește căi creative
de a-și termina treaba. Deny-list pe regex = barieră de viteză, nu graniță.

**Modelul de amenințare corect pentru un sistem personal** (nu enterprise, nu pentest):
1. **Ireversibilitate din prostie** — agentul șterge/suprascrie ceva (vault, repo, config).
2. **Prompt injection** — agenții au WebFetch/WebSearch (și, în viitor, browser MCP): conținut
   web nesigur poate conține instrucțiuni pe care agentul le urmează. Regula: conținutul
   extern e *date*, nu *instrucțiuni*; punctul de mitigare e exact gate-ul pe tool calls
   (de-asta WP2 e obligatoriu înainte de a da agenților browser).
3. **Runaway** — bucle care ard bani/CPU/disc (scheduler + agenți fără limite de durată).

**Cele 4 straturi, de la graniță reală la igienă:**

1. **Granițe OS (singurele cu garanții):** (a) verifică dacă Claude Code 2.1.173 expune
   sandboxing pentru Bash în rulările headless (seatbelt pe macOS; caută în settings schema —
   `--help` nu arată flag dedicat) și activează-l pentru `!run`; (b) țintă pe termen lung:
   [apple/container](https://github.com/apple/container) pentru izolarea agenților
   (deja notat ca „confinement v2" în evaluare — fără Docker); (c) alternativă ieftină: user
   macOS separat, standard, non-admin pentru procesele agentice (fără TCC grants la
   Documents/Desktop, fără keychain-ul principal).
2. **Policy as code:** azi politica e împrăștiată în 4 locuri (`HIGH_CMD_PATTERNS` în
   risk_hook, `allowed_task_roots` în kage_config, matcher în risk_settings, buget în
   ntfy_config). Un `policy.yaml` unic, per tip de run (chat / task / sysrun / scheduled):
   tools permise, rooturi, buget, durată maximă, ce cere aprobare — citit de toate punctele
   de enforcement, cu fiecare decizie logată în run ledger (WP8). Include fixul D7:
   **chat-ul implicit fără tools** — capability minimă by default, nu maximă.
3. **Blast radius (presupune că 1–2 vor fi ocolite):** kill switch `!stop` (omoară toate
   procesele agent + pauzează APScheduler, accesibil din Telegram și UI); vault-ul
   `StefanBrain` sub git (agenții scriu în el cu `!save` — orice modificare devine
   reversibilă; commit automat zilnic în pipeline-ul nocturn); procedură de **restore**
   documentată și testată pentru backup-ul existent; round limit + inactivity timeout (WP9);
   budget v2 în $ (#7).
4. **Acces & audit:** Telegram e deja restrâns la chat_id-ul lui Stefan
   (`telegram_gateway.py:140`, verificat) ✅; Tailscale e granița de rețea reală azi ✅;
   token-ul API e decorativ (`/chat` îl servește în HTML — D15) → de reparat **înainte** de
   Cloudflare; run ledger (WP8) = audit trail-ul.

### WP-G1 — governance ieftin ✅ (04.07.2026) · efort: un weekend · imediat după WP2

**Pași:** (1) `!stop` kill switch (server-side: SIGTERM pe procesele din
`_active_task_queues` + `scheduler.pause()`; expus în chat, Telegram, UI); (2) `git init` +
commit zilnic pe vault-ul StefanBrain (job APScheduler în pipeline-ul nocturn); (3)
`policy.yaml` minimal: tools per tip de run + chat fără tools (D7) — `chat_completions` nu
mai trimite `--allowedTools` decât pentru `!agent`/`!run`; (4) verifică + activează
sandbox-ul CLI dacă există în 2.1.173; (5) scoate token-ul din HTML-ul `/chat` (login simplu
sau token doar din config client-side); (6) documentează + testează restore-ul backup-ului.
**Acceptare:** `!stop` oprește un task în curs (test manual) · un `!save` greșit e
reversibil cu `git revert` în vault · mesaj de chat normal nu mai spawn-ează claude cu
Bash · pytest verde.

**Implementat (04.07.2026):**

- (1) Kill switch: registru `_running_procs` (înregistrat în `_background_task_exec` și
  `_route_claude_autonomous`), `_stop_all()` → SIGTERM + `scheduler.pause()`; comenzi
  `!stop`/`!resume` în chat (deci și pe Telegram, prin pipeline), endpoint `POST /api/stop`,
  chip în `kage.html`. Teste: `test_stop.py` + e2e.
- (2) `_vault_git_commit()` (init idempotent + commit) + job scheduler la **03:00**
  (`__vault_git_commit__`). Teste: `test_vault_git.py` (inclusiv revert round-trip).
- (3) `policy.yaml` + `_load_policy()`/`_policy_cli_flags(run_type)`; toate cele 3 spawn-site
  claude citesc politica. **Chat T3+ e read-only (fără Bash/Write/Edit)**; task/sysrun =
  capability completă. Teste: `test_policy.py`.
- (4) **Sandbox CLI: NU există flag dedicat în claude 2.1.173.** `--permission-mode` are doar
  `acceptEdits/auto/bypassPermissions/default/plan`; referințele „sandbox" din `--help` sunt
  doar text consultativ pentru `--allowedTools`/`--dangerously-skip-permissions`. Izolarea OS
  reală rămâne pe **WP-G2** (containere). Mitigarea de azi: policy read-only la chat (3) +
  blast radius (1,2,6) + hook-ul de risc.
- (5) Token scos din HTML: `/chat` îl livrează ca **cookie HttpOnly** (`kage_token`), acceptat
  și de `auth_middleware`. Nu mai apare în view-source/JS. Test: e2e `test_chat_page_hides_token`.
- (6) `_restore_cache_db()` (cu plasă de siguranță `cache_db.pre-restore-*`) + **`RESTORE.md`**.
  Teste: `test_restore.py` (round-trip + arhivă invalidă).

Notă: `pyyaml` adăugat în `requirements.txt`. Baseline teste: 46 → **69 verzi**.

### WP-G2 — izolare reală · efort: mediu-mare · după WP9

Agenții `!run` rulează în containere pornite **per-task**, nu într-un daemon rezident:

- **Opțiunea primară — Docker** (Stefan îl are instalat): `docker run --rm --memory 4g
  --cpus 4` cu workspace-ul task-ului montat rw, claude CLI + credențiale montate read-only,
  și politică de rețea explicită (`--network none` pentru taskuri pur locale). **Controlul
  RAM-ului:** limitează VM-ul din Docker Desktop → Resources (4–6GB) și activează Resource
  Saver, ca VM-ul să nu stea rezident între taskuri — pe o mașină cu 25GB ținuți de Qwen,
  un VM idle de 3–4GB permanent ar fi inacceptabil. Alternativă mai ușoară la același API:
  Colima pornit/oprit în jurul taskului (`colima start --memory 4` / `colima stop`).
- **Opțiunea mai suplă — `apple/container`**: zero footprint idle (VM ușor per container,
  fără daemon), nativ Apple Silicon; tooling mai tânăr decât Docker.
- **Fallback — user macOS separat** dacă containerele complică accesul la vault.

Ollama și orchestratorul rămân **native** indiferent de opțiune (Metal). Politica de network
egress intră în `policy.yaml` (WP-G1) și e aplicată de flag-urile de rețea ale containerului.
Precondiție practică: WP9 (executorul pe SDK) — ca să nu izolezi de două ori două
mecanisme diferite de spawn.

**Recomandarea Fable (04.07.2026):** Docker on-demand, nu apple/container (mai matur, deja
instalat, network policy testată în producție de un deceniu; apple/container de revizitat
peste ~1 an). **Trigger-ul pentru WP-G2 e de capabilitate, nu de calendar:** fă-l atunci când
agenții primesc browser MCP (chrome-devtools) sau rulări zilnice autonome nesupravegheate
(Mission Control) — până atunci, sandbox-ul CLI din WP-G1 + blast radius sunt suficiente
pentru profilul de risc real.

**Amendament GCP (13.07.2026, Stefan — decizia „pista data-stack" din §4):** WP-G2 se
livrează cu **DOUĂ ținte de execuție** pentru rulările nesupravegheate: **Docker local**
(opțiunea primară de mai sus, neschimbată) și **GCP Cloud Run jobs** (container efemer,
per-task). Motivația corectă NU e memoria — agenții consumă puțin local, Ollama e
consumatorul — ci: (a) **disponibilitate**: rulările zilnice merg cu laptopul închis/plecat;
(b) **izolare**: blast radius complet în afara mașinii personale; (c) GCP e explicit în JD.
Constrângeri obligatorii: secretele prin **Secret Manager**, NU `kage_config.json` copiat în
imagine; workspace = **git clone** + rezultatul se întoarce prin push/PR sau callback către
orchestrator (Cloudflare Tunnel, WP10); **risk_hook + plafonul #7 merg ÎN container** —
rularea cloud nu are voie să fie mai puțin governată decât cea locală. Nucleul (Ollama,
memoria, ChromaDB, chat history) NU se mută — local by design (privacy). Cost: Cloud Run
facturează per-secundă pe un profil bursty → practic zero; free tier + credit de trial.

---

## 7. Housekeeping la fiecare WP terminat

1. Actualizează `ROADMAP.md` (status) și `DESPRE_KAGE.md` dacă s-a schimbat comportament
   vizibil; corectează afirmațiile doc↔cod pe care WP-ul le-a rezolvat (§2).
2. Commit pe branch + PR spre `dev`; mesaj cu referință la WP și D-uri rezolvate.
3. Marchează în acest fișier WP-ul ca `✅ (data)` în titlul secțiunii — fișierul e checklist
   viu, nu doar plan.

---

## 8. Pista de învățare & verificarea înțelegerii (mod interviu) — v2, rescrisă 15.07.2026

**Scop (neschimbat):** proiectul e material de CV pentru un rol în direcția AI; valoarea =
capacitatea lui Stefan de a-l APĂRA la interviu. Această secțiune acoperă două goluri:
(a) subsistemele deja construite de model, pe care Stefan trebuie să le stăpânească
retroactiv; (b) subsistemele viitoare cu valoare de interviu. Rulează în PARALEL cu
ordinea din §5 — nu e un WP, e un mod de lucru.

**De ce v2:** formatul v1 (Explică/Apără/Extinde + corp de funcții scris de Stefan pe
toate părțile ML) cerea sesiuni dedicate per subsistem și s-a dovedit prea scump ca timp
(decizia din §4, 15.07.2026). Principiul v2: greutatea se mută de pe *scris cod* pe
*apărat decizii* — asta se antrenează cel mai dens pe minut investit.

**Bugetul de timp (v2):** o singură alocare fixă — **sesiunea de weekend, 2–3h**. În
timpul săptămânii nu există obligații; fișele de interviu se citesc oricând (~10 min/buc).

**Mecanica v2:**

1. **Fișă de interviu per WP** — la închiderea oricărui WP cu valoare de interviu, modelul
   generează `docs/fise-interviu/<wp>.md` (~1 pagină): decizia + DE CE, alternativele
   respinse + de ce nu, trade-off-urile acceptate, 3–5 întrebări adversariale. Răspunsurile
   la întrebări stau într-o secțiune separată la finalul fișei (self-test: Stefan răspunde
   întâi, verifică după). Generarea fișei e **criteriu de închidere al WP-ului**, obligația
   modelului, nu a lui Stefan. Pentru subsistemele deja construite, fișele se generează
   retroactiv (prima: rutare semantică + cache).
2. **Sesiunea de weekend (2–3h):**
   - **~30 min Apără**, rapid-fire, pe 1–2 subsisteme cu fișa citită în prealabil — modelul
     joacă intervievatorul (întrebări adversariale de profunzime: „de ce k-NN ponderat și
     nu 1-NN?", „ce se strică fără purged CV?"). Nivelul „Explică" din v1 dispare ca pas
     separat — e absorbit aici (cine apără, poate și explica).
   - **restul sesiunii**, una dintre: (i) lucru la **piesa-fanion** activă (vezi 3),
     (ii) un exercițiu **Extinde** — modificare mică țintită, 30–45 min, făcută SINGUR,
     modelul doar revizuiește, (iii) recuperare Apără din backlog-ul „deja construite".
3. **Piesele-fanion — singurele scrise de mână** (metoda veche: modelul scrie scheletul +
   testele, Stefan scrie corpul, review de senior după, alternativa explicată abia DUPĂ
   încercare): **HMM-ul de regim de la zero** (numpy, EM) și **Dixon-Coles** (T2 sports).
   Restul vechii liste „Stefan, ghidat" (purged CV + meta-labeling, interpretarea
   rapoartelor de validare) → defend-only + eventual Extinde.
4. **Bifele devin F/A/X** — Fișă citită / Apărat / Extins. Extinde e obligatoriu doar
   pentru top-3 ca valoare de interviu (rutare semantică+cache, executor Agent SDK,
   risk gate); la rest e opțional.

**Subsistemele DEJA construite — de recuperat prin înțelegere (ordinea = valoarea de interviu):**

| Subsistem | Concepte de interviu | Exercițiu „Extinde" propus | F/A/X |
| --- | --- | --- | --- |
| Rutare semantică + cache semantic + memorie (`decide_tier`, `_semantic_classify`, `_cache_policy`, ChromaDB) | embeddings, vector DB, k-NN ponderat, praguri de similaritate, TTL, cache invalidation | scrie un test care demonstrează capcana follow-up-ului din cache (de ce >1 tură = skip) | ☐ ☐ ☐ |
| Executorul pe Agent SDK (`agent_runner.py`: buclă tool-use, streaming, hooks, resume, inactivity timeout) | agents, tool calling, HITL gates, session state | adaugă un tip nou de eveniment normalizat + testul lui | ☐ ☐ ☐ |
| Actor→Critic→Validare (WP-T: invarianți, DSR, PBO, pre-registration) | LLM-as-judge, overfitting statistic, multiple testing | rulează manual un ciclu și explică verdictul fiecărei ipoteze din raport | ☐ ☐ ☐ |
| Risk gate + aprobări HITL (`risk_hook.evaluate_risk`, fluxul Telegram, fail-closed) | AI safety patterns, deny/allow/escalate, prompt injection | adaugă un pattern nou de risc cu test (inclusiv un false-positive evitat) | ☐ ☐ ☐ |
| Run ledger + decision trace (WP8) | observabilitate LLM, trace schema, cost tracking | scrie un query care răspunde la o întrebare de debugging reală din `runs`/`run_events` | ☐ ☐ ☐ |

**Subsistemele VIITOARE — construite de model, apărate de Stefan (fișă + Apără; scrise de
mână DOAR piesele-fanion, marcate ★):**

| Subsistem | Când | Concepte de interviu |
| --- | --- | --- |
| Logica Advisorului (WP13): promptul adversarial, context minimal, structura verdictului | la WP13 | LLM-as-judge, evaluare, debate patterns, anchoring |
| ★ HMM de regim de la zero (numpy, EM) — piesă-fanion, scrisă de mână | la upgrade 3.2 | EM, MLE, modele generative (decis în §4) |
| ★ Dixon-Coles + CLV (T2 sports) — piesă-fanion, scrisă de mână | la T2 | fitare de model, verosimilitate, calibrare probabilistică |
| Purged CV + meta-labeling | la nevoie în WP-T | leakage temporal, overfitting, evaluare |
| Memorie v2 (#6): extracție de fapte + consolidare/dedup | la #6 | RAG, memorie de agent, deduplicare semantică |
| RAG pe documente (`!index`) | înainte de ian. 2027 | chunking, retrieval, evaluare de retrieval |
| API contracts, idempotency keys, pagination, rate limiting | la R0 | design de API scalabil, contract testing, robustețe la retry |
| Schema Postgres + migrarea de pe SQLite/file-locks | la WP-PG | data modeling, tranzacții, concurență cross-proces, migrare zero-loss |
| Pipeline raw→staging→mart + backfill idempotent + point-in-time lineage (T3) | la WP-ETL | ETL, idempotență, backfill, SQL analitic, data lineage |
| DAG-uri Airflow + separarea batch vs safety-critical | la WP-AF | orchestrare batch, retries/backfill, design de scheduler |
| Execuție efemeră pe Cloud Run + Secret Manager | la WP-G2 | serverless, secrets management, izolare, cost model cloud |
| Intent router pe gateway (clasificare + dispatch) | la WP-NL | LLM-as-router, clasificare de intenție, ieșire structurată, fail-safe design |
| Bucla reflect→replan + steering | la WP-AL | planning/replanning de agent, reflection, HITL, compresie de context |
| Producer/consumer Kafka + stratul de ingest | dacă WP-KF e promovat | event streaming, partiții/offset-uri, semantici de livrare (at-least-once, idempotență) |
| Eval harness minim (taskuri fixe + criterii automate) | la G1-minim | eval-driven development, regression testing pentru agenți |

**Reguli:** plumbing-ul (endpoint-uri, scheduler, config, UI) NU intră pe pistă — zero valoare
de interviu, deci nici fișă. Nu se reconstruiește nimic deja funcțional doar de dragul
exercițiului — înțelegerea se dovedește prin „Extinde", nu prin rescriere. O sesiune de
„Apără" picată se reprogramează după re-citirea fișei, nu se treacă cu vederea. Un WP cu
valoare de interviu NU se marchează ✅ fără fișa lui în `docs/fise-interviu/`. Fișele nu
conțin date personale (repo-ul poate deveni public).
