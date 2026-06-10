# Kage — Roadmap

Sursă unică de adevăr pentru tot ce e planificat, în lucru, sau decis conștient că nu se face.
`MULTI_TENANT_ARCH.md` a fost absorbit aici — poate fi șters.

---

## Starea curentă: Faza 17 ✅

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

---

## Prioritate înaltă

### Context Compaction
Prevenirea erorilor de overflow la conversații lungi — prerequisit pentru orice UI nou.

- Trunchierea inteligentă a contextului când depășește limita modelului
- Rezumare automată a turelor vechi înainte de trimitere
- Port al `services/context_compactor.py` din [Odysseus](https://github.com/pewdiepie-archdaemon/odysseus) (MIT)

---

### Long-term Memory Vector
Memorie reală per conversație — nu cache pe răspunsuri, ci fapte și preferințe persistente.

- ChromaDB separat de semantic cache (colecție `long_term_memory`)
- Injectat automat în prompt la fiecare turn relevant
- Port al `services/memory_vector.py` din Odysseus (MIT)
- Include fix auto-dedup (prezent în Odysseus, relevant pentru ChromaDB-ul local)

---

## Prioritate medie

### Acces Remote via Cloudflare Tunnel
Face Kage accesibil de pe orice device fără VPN sau port forwarding manual.

**Prerequisit:** `kage.html` trebuie să folosească URL relativ în loc de `localhost:4001` hardcodat (schimbare ~10 linii).

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

### Workspace Confinement
Complement al risk gate-ului — limitează `!run`/`!sysrun` la un folder definit.

- `allowed_path` ca parametru opțional: orice tool call în afara lui e blocat automat
- Port al `feat: workspace confinement` din Odysseus (MIT)

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

### Backup `cache_db/`
`chat_history.db` (SQLite) și ChromaDB nu au strategie de backup. La corupere pierzi tot istoricul.

- Script cron zilnic care copiază `cache_db/` în `~/Documents/StefanBrain/backups/kage/`
- Sau rsync pe un volum extern

---

### kage.html URL Relativ
Hardcodat azi: `http://localhost:4001`. Blochează Cloudflare Remote Access.

- Înlocuiește toate referințele fixe cu URL relativ (`/api/...`) sau derivat din `window.location.origin`
- Task mic (~10 linii), dar prerequisit obligatoriu înainte de Cloudflare

---

### Teste de bază
Zero acoperire azi la 2500+ linii. Risc real la refactoring.

- Unit teste pentru `decide_tier`, `_budget_check`, `_semantic_classify`
- Integration test pentru flow complet (mock LiteLLM + ChromaDB)
- Nu e blocker pentru nicio altă fază, dar reduce riscul la modificări

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
- **Docker setup** — overhead nejustificat pentru deploy local
- **Calendar / Email / Gallery integrations** — scope creep față de core use case
- **Skill creation loop** (Hermes-style) — arhitectură separată, complexitate nejustificată momentan
- **Voice transcription** — interesant dar nu în core loop
