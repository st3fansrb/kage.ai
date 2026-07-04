# Kage — Roadmap

Sursă unică de adevăr pentru tot ce e planificat, în lucru, sau decis conștient că nu se face.
`MULTI_TENANT_ARCH.md` a fost absorbit aici — poate fi șters.

> **03.07.2026:** evaluarea tehnică completă e în `KAGE-EVALUARE.md`, iar planul de execuție
> prioritizat (pachete de lucru + criterii de acceptare, pentru sesiuni AI viitoare) e în
> `KAGE-HANDOFF.md`. **Ordinea de implementare de acolo are prioritate** față de secțiunile
> „Prioritate medie/scăzută" de mai jos, care rămân ca istoric de idei.

---

## Starea curentă: Faza 19 ✅

| Feature | Status |
|---|---|
| LiteLLM proxy + 6-tier routing (Qwen 8B/35B → Haiku/Sonnet/Opus → Gemini) | ✅ |
| Semantic routing (ChromaDB + nomic-embed-text, <50ms) | ✅ |
| Semantic cache (cosine 0.92, TTL 24h, vacuum la 04:00) | ✅ |
| Risk gate 3-axis (PreToolUse hook, Never/High/Medium/Safe) | ✅ |
| Push notifications ntfy.sh + Tailscale (aprobare risc din telefon) | ✅ |
| Budget enforcement (20 cloud/zi, alerte la 80%, fallback T2) | ✅ |
| macOS menubar widget (rumps, Python 3.12) | ✅ |
| Dashboard HTML cu live polling + tab Tasks | ✅ |
| APScheduler cron tasks (`!schedule "CRON" msg`) | ✅ |
| Kage UI (`kage.html`) — SSE streaming, chips prefix, pending approvals | ✅ |
| SQLite sesiuni persistente cu `session_id` | ✅ |
| API token auth (middleware FastAPI) | ✅ |
| `!run` (background agent — Claude/Gemini) + `!swarm` (paralel) | ✅ |
| `kage_config.json` ca sursă de config unificată | ✅ |
| Separare completă de Odysseus (eliminat din toate scripturile) | ✅ |
| Telegram Bot Gateway (`@kage_hub_bot`) — canal bidirecțional, aprobare risc inline | ✅ |
| Context compaction (sliding window + summarization opțional, tiers 1-2) — Faza 18 | ✅ |
| Long-term memory vector (`long_term_memory`, dedup, injecție în prompt) — Faza 18 | ✅ |
| `kage.html` URL relativ (`window.location.host`) — prerequisit Cloudflare | ✅ |
| Workspace confinement (`allowed_task_roots` pentru `!run`/`!sysrun`/`!swarm`) — Faza 19 | ✅ |
| Backup zilnic `cache_db/` (tar.gz, rotație, `POST /admin/backup`) — Faza 19 | ✅ |
| Suită teste pytest (28 teste: routing, budget, compaction, confinement, memory, backup) — Faza 19 | ✅ |

---

## Prioritate medie

### Acces Remote via Cloudflare Tunnel
Face Kage accesibil de pe orice device fără VPN sau port forwarding manual.

**Prerequisit:** ✅ rezolvat — `kage.html` folosește deja URL relativ (`window.location.host`).

**Pași implementare:**
```bash
brew install cloudflared
cloudflared tunnel login
cloudflared tunnel create kage-orch
# mapează kage.stefan.ro → http://localhost:4001
```
Autentificare: Cloudflare Zero Trust → Self-hosted App → politică pe email-ul tău.

**Notă arhitecturală (viitor):** modelul "Hub + Agent distribuit" — Mac-ul e Hub central, alți utilizatori rulează un agent mic pe hardware-ul lor, cu propriile chei Claude/Gemini. Nu se implementează acum, dar designul Cloudflare trebuie să țină cont de această direcție.

---

### RAG pe Documente
Indexare și căutare în vault-ul Obsidian sau fișiere locale — distinctă față de memory_vector care e per-conversație.

- Indexare fișiere `.md` / `.pdf` din `StefanBrain/` în ChromaDB (colecție separată `rag_docs`)
- `!rag <query>` sau injecție automată la keyword-uri relevante din prompt
- ChromaDB e deja instalat — e o extensie naturală a caching-ului existent

---

### Workspace Confinement ✅ (Faza 19)
Implementat — `_validate_task_cwd` validează `cwd`-ul agenților `!run`/`!sysrun`/`!swarm` față de `allowed_task_roots` din `kage_config.json` (canonicalizare cu `resolve()`, anti-bypass `..`/symlink). Listă goală/absentă = dezactivat (non-breaking); `PROJECT_ROOT` mereu permis pentru `!sysrun`. Task respins → răspuns `[BLOCKED]`, niciun subprocess lansat.

---

### Round Limit + Continue
Previne buclele infinite în taskuri lungi de agent.

- Limită configurabilă de runde per `!run` task (default în `kage_config.json`)
- Buton "Continue" în Kage UI când agentul atinge limita
- Port al `feat: round-limit + Continue` din Odysseus

---

### Code-nav Tools pentru Agent
Îmbogățirea `!run` cu tools structurate, nu doar shell brut.

- `grep`, `glob`, `ls`, `read_file` cu line ranges, `edit_file` cu diff vizual
- Port al `services/agent_tools.py` din Odysseus (MIT)
- Complement natural pentru Workspace Confinement

---

## Prioritate scăzută / Explorare

### Frontend Standalone
Înlocuirea `kage.html` embedded cu un frontend propriu scalabil.

- Opțiuni evaluate: Next.js, SvelteKit, sau vanilla JS extins din `kage.html`
- Backend rămâne neschimbat (OpenAI-compatible API pe `:4001`)
- **Blocat de:** Context Compaction + Memory Vector — fără astea orice UI nou e la fel de limitat

---

### Settings UI Web
Editare `kage_config.json` și `risk_settings.json` din browser, fără SSH/editor.

---

### File Upload în Chat
Upload fișiere direct în conversație (cod, logs, documente) pentru analiză cu modele multimodale (T4 Gemini).

---

### MCP Streamable HTTP
Expunerea orchestratorului ca MCP server pentru integrare cu alte tooluri (Claude Desktop, IDE-uri).

---

### Parallel Subagents cu Budgeting Per-Subagent
Extensie a `!swarm` existent — fiecare subagent primește un buget de cloud calls izolat, nu shared din pool-ul global.

---

## Stabilitate & Ops

### Backup `cache_db/` ✅ (Faza 19)
`_backup_cache_db()` rulează zilnic la 05:00 (după vacuum-ul de la 04:00): snapshot SQLite via Online Backup API + copytree pentru ChromaDB, arhivat `cache_db-YYYYMMDD-HHMMSS.tar.gz` în `backup_dir` (default `~/Documents/StefanBrain/backups/kage`), cu rotație `backup_keep` (default 7). Trigger manual: `POST /admin/backup`.

---

### kage.html URL Relativ ✅
Rezolvat — `kage.html` derivă host-ul din `window.location.host`, fără `localhost:4001` hardcodat. Cloudflare Remote Access nu mai e blocat de asta.

---

### Teste de bază ✅ (Faza 19)
Suită pytest (`tests/`, `pytest.ini`, `requirements-dev.txt`): 28 teste pe `_heuristic_classify`, `_build_tier_models/short`, `decide_tier` (prefixe forțate), `_usage_counts_today`, `_budget_check`, `_compact_messages`, `_validate_task_cwd`, `_memory_retrieve`, `_backup_cache_db`. Import sigur al modulului (startup events nu rulează la import). Rulare: `pytest`.

---

### WP1 — Reparația fundației ✅ (04.07.2026)

Primul pachet din `KAGE-HANDOFF.md`. Rezolvă D1/D2/D13 + igienă de log:
- `import re` global (înlocuiește cele două `import re as _re` locale) — repară **500-ul de chat** (`NameError: re`) prezent din 8 iunie (D1).
- Ramură **non-stream** în `POST /v1/chat/completions`: `stream:false` întoarce un JSON OpenAI standard (`_sse_to_openai_json`) — deblochează gateway-ul Telegram care făcea `resp.json()` pe corp SSE (D2).
- Handler **server-side pentru `!run`/`!swarm`/`!sysrun`** în chat (`_prepare_and_launch_task`, refolosit și de `/task/run`): pornește task-ul și confirmă cu id (D13). Streamul complet spre Telegram vine la WP8.
- `@app.exception_handler(Exception)` → notificare + log în loc de 500 mut.
- `httpx` logger la WARNING (nu mai scrie token-ul botului Telegram în log).
- **Teste e2e pe stratul HTTP** (`tests/test_e2e.py`, `respx` + `TestClient`): SSE parsabil, `stream:false` → JSON valid, `!run` pornit, confinement blochează — golul D3.

---

### WP1b — Consolidarea canalelor: Telegram unic ✅ (04.07.2026)

Al doilea pachet din `KAGE-HANDOFF.md`. Retrage ntfy.sh — Telegram devine canalul unic:
- `_notify()` rutează întâi prin gateway-ul Telegram; ntfy rămâne **doar fallback** dacă
  gateway-ul e neconfigurat sau dacă nu există event loop activ (context sync/thread).
- Cheile de config (`max_cloud_calls_per_day`, `ntfy_topic`) sunt citite din
  `kage_config.json` (`NTFY_CONFIG_PATH` → **`KAGE_CONFIG_PATH`**); `ntfy_config.json` rămâne
  doar fallback legacy pentru instalări vechi.
- `risk_hook.py`: eliminate butoanele ntfy cu **link-uri Tailscale** din fluxul de aprobare —
  aprobarea de risc se face prin butoanele **inline Telegram** (emise de orchestrator la
  `/risk/register`); `risk_hook` doar polling-uiește `/risk/status/{id}`.
- `tests/test_notify.py` (nou): Telegram primar când gateway-ul e activ, fallback ntfy fără
  gateway și fără loop.

---

## Viziune business (toamnă 2026)

Kage ca produs pentru verticala manufacturing/automotive România:
- Self-hosted, GDPR-ready — diferențiator față de soluții SaaS
- WinMentor / Saga integration — context injectat automat din ERP-ul clientului
- Model "Hub + Agent distribuit": Mac-ul clientului e Hub, angajații rulează Agent pe laptopurile lor cu propriile conturi cloud
- **Primul client țintă:** rețeaua tatălui (toamnă 2026)

---

## Ce NU facem (decizie conștientă)

- **Multi-user auth complex** — Kage e personal acum; multi-tenant vine abia cu Cloudflare Zero Trust
- **Docker pentru deploy** — rămâne exclus (Ollama are nevoie de Metal → totul nativ); **revizuit 04.07.2026:** Docker e acceptat ca sandbox on-demand per-task pentru agenții `!run` (WP-G2 în KAGE-HANDOFF)
- **Calendar / Email / Gallery integrations** — scope creep față de core use case
- **Skill creation loop** (Hermes-style) — ~~complexitate nejustificată~~ **revizuit 03.07.2026:** skills scrise de mână în format Agent Skills intră în scope (itemul #12 din KAGE-EVALUARE); auto-distilarea rămâne exclusă până există run ledger (#5), și doar ca draft + aprobare
- **Voice transcription** — ~~nu în core loop~~ **răsturnat 03.07.2026:** voice memos pe Telegram (whisper.cpp local, #13) și push-to-talk pe Mac + wake word (#14) intră în scope
