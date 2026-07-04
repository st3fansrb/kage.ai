# KAGE-HANDOFF — plan de execuție pentru sesiunile următoare

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
- **Cheia de cache = doar ultimul mesaj user**, fără context de conversație și cu prefixele
  incluse — follow-up-uri („continuă") pot primi răspunsul altei conversații.
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

---

## 5. Pachetele de lucru, în ordinea execuției

Ordinea (din `KAGE-EVALUARE.md` §4, raționamentul acolo):
**WP1(#1) → WP1b(canale) → WP2(#2) → WP-G1(§6) → WP3(#3) → WP4(#8) → WP5(#11) → WP6(#13) →
WP7(#15A) → WP8(#5) → WP9(#4) → WP-G2(§6) → WP10(#15B)** → apoi #12, #14, #7, #6, #9.

Prompt de pornire recomandat (copy-paste, înlocuiește N):
> Citește CLAUDE.md și KAGE-HANDOFF.md (§0–§4 integral + secțiunea pachetului: §5 pentru
> WP1–WP10, §6 pentru WP-G1/WP-G2), apoi implementează pachetul WP*N* exact cum e
> specificat, pe un branch nou din dev. Rulează pytest înainte și după. Nu atinge alte
> fișiere decât cele listate. La final raportează criteriile de acceptare unul câte unul și
> aplică pașii de housekeeping din §7.

### WP1 (#1) — Reparația fundației · efort: o seară–un weekend

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

### WP1b — Consolidarea canalelor: Telegram unic, retragerea ntfy · efort: o seară · după verificarea WP1

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

### WP2 (#2) — Risk gate v2 + confinement funcțional · efort: mic

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

### WP3 (#3) — Router cu feedback loop · efort: o seară–un weekend

**Fișiere:** `orchestrator.py` (`decide_tier`, `_semantic_classify`), `tests/test_routing.py`.
**Pași:** (1) la `!retry`/prefix forțat, adaugă mesajul cu tier-ul corectat în colecția
`tier_routing` cu `metadata={"source":"feedback"}`; (2) `_semantic_classify`: `n_results=5`,
vot ponderat cu similaritatea (azi 1-NN); (3) plafonează colecția (vacuum la N exemple/tier);
(4) prefixe noi `!opus` (T6) / `!gemini` (T4) + `!retry` plafonat la 6, nu 5; (5) exemple seed
pentru T4/T6 în `TIER_EXAMPLES`.
**Acceptare:** teste: după un override `!best` pe un mesaj, un mesaj similar se rutează T5 cu
metoda `sem` · `!opus`/`!gemini` forțează corect · colecția nu crește nelimitat · pytest verde.

### WP4 (#8) — Cache v2 context-aware · efort: ~2 ore

**Fișiere:** `orchestrator.py` (`chat_completions`), teste noi.
**Pași:** (1) sari peste cache (lookup ȘI store) dacă conversația are >1 tură de user;
(2) nu stoca răspunsuri la mesaje cu referenți temporali (regex `azi|acum|mâine|ieri|astăzi`);
(3) curăță prefixele (`!best` etc.) din `cache_query` înainte de embedding.
**Acceptare:** teste pentru fiecare din cele 3 comportamente; pytest verde.

### WP5 (#11) — Igienă de repo · efort: o seară

**Pași:** persona (`_STEFAN_BASE`, `project_map` din `_get_obsidian_context`,
`TIER_EXAMPLES` personale) → `kage_config.json`/vault cu default generic; șterge
`_build_chat_html`, `UNCERTAINTY_PHRASES`, `MULTI_TENANT_ARCH.md`; unifică cele două căi de
„add scheduled task"; `usage_log` → SQLite sau rotație lunară (repară și cititul integral la
fiecare poll de 10s).
**Acceptare:** grep fără date personale hardcodate în `.py` · pytest verde · dashboard
funcțional după mutarea usage.

### WP6 (#13) — Voice memos pe Telegram · efort: o seară–un weekend · depinde de WP1

**Fișiere:** `orchestrator.py`, `telegram_gateway.py`, `kage_config.json[.example]`.
**Pași:** (1) endpoint `POST /v1/audio/transcriptions` (OpenAI-compatible) — subprocess
whisper.cpp cu `large-v3-turbo` (instalare: `brew install whisper-cpp` sau build; modelul
~1,6GB, descărcat separat); config `whisper_bin`, `whisper_model`; (2) în gateway: ramură
pentru `message.voice` → `getFile` → download OGG → transcrie → intră în pipeline-ul normal,
cu reply „📝 Am înțeles: …"; (3) test cu fixture audio scurt.
**Acceptare:** voice memo în română pe Telegram → răspuns text corect (test manual) · endpoint
testat cu fixture · transcrierea rulează 100% local.

### WP7 (#15A) — Phoenix peste LiteLLM · efort: câteva seri

**Pași:** `pip install arize-phoenix` (venv separat dacă 3.9 face probleme — Phoenix poate
cere 3.10+; atunci rulează-l standalone: `phoenix serve`); callback Phoenix în
`litellm_config.yaml` ([integrare nativă](https://docs.litellm.ai/docs/observability/phoenix_integration));
adaugă în `start_all.sh`.
**Acceptare:** un request T1/T2 apare ca trace în UI-ul Phoenix (`localhost:6006`).

### WP8 (#5) — Run ledger + decision trace · efort: 1–2 săptămâni de seri

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

### WP9 (#4) — Executor pe Claude Agent SDK · efort: 2–4 săptămâni de seri

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

### WP10 (#15B) — Kage Mission Control · efort: o lună+ de seri · depinde de WP1+WP8

Frontend Next.js + CopilotKit pe AG-UI: endpoint SSE `/agui` care traduce `runs`/`run_events`
în evenimente AG-UI; panouri: agent cards live, activity feed, buget/cost, cache/memorie,
inbox aprobări (`/api/pending` există), briefing-uri de la agenții programați. `kage.html` se
pensionează la paritate. Referințe de design în `KAGE-EVALUARE.md` §3.12.
**Promptul de design e gata:** `PROMPT-DESIGN-UI.md` — Stefan îl rulează în Claude Design;
output-ul (direcție vizuală + layout-uri + componente) devine specul vizual al acestui WP.

### Restul (după WP10, ordine: #12 → #14 → #7 → #6 → #9)

- **#12 Skills**: folder `skills/` cu 3–5 SKILL.md scrise de mână; symlink în `.claude/skills`
  la cwd-ul rulărilor; `!skill list/new`; auto-distilare abia după WP8/WP9, draft + aprobare.
- **#14 Push-to-talk Mac → „Hey Jarvis"**: etapa 1 hotkey în widget (pynput + sounddevice →
  `/v1/audio/transcriptions` → TTS Piper ro_RO/`say -v Ioana`); etapa 2 `voice_daemon.py` cu
  RealtimeSTT + openWakeWord.
- **#7 Budget v2**: parsează `total_cost_usd` din evenimentul `result` → buget în $/zi;
  gate pe `task_run` și pe fallback-ul LiteLLM→cloud. (Trage-l mai devreme dacă agenții încep
  să fie folosiți intens — vezi capcana bugetului din §3.)
- **#6 Memorie v2**: extracție de fapte pe T2 la final de conversație + job de consolidare la
  03:00 (dedup global, fuziune, bloc `user_profile` injectat mereu) — pipeline nocturn coerent
  cu vacuum 04:00 / backup 05:00.
- **#9 Tools locale pentru T2**: `local_tools.py` (vault read/write, status, schedule) prin
  function calling LiteLLM, buclă max 5 iterații, gate prin `evaluate_risk` importat.

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

### WP-G1 — governance ieftin · efort: un weekend · imediat după WP2

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

---

## 7. Housekeeping la fiecare WP terminat

1. Actualizează `ROADMAP.md` (status) și `DESPRE_KAGE.md` dacă s-a schimbat comportament
   vizibil; corectează afirmațiile doc↔cod pe care WP-ul le-a rezolvat (§2).
2. Commit pe branch + PR spre `dev`; mesaj cu referință la WP și D-uri rezolvate.
3. Marchează în acest fișier WP-ul ca `✅ (data)` în titlul secțiunii — fișierul e checklist
   viu, nu doar plan.
