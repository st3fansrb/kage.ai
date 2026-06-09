# Kage — Roadmap

Starea curentă: **Faza 16** — sesiuni persistente SQLite, API auth, !swarm, !run claude/gemini, background task persistence.

---

## Prioritate înaltă

### Context Compaction
Prevenirea erorilor de overflow la conversații lungi. Port al `services/context_compactor.py` din [Odysseus](https://github.com/pewdiepie-archdaemon/odysseus) (MIT).
- Trunchierea inteligentă a contextului când depășește limita modelului
- Rezumare automată a turelor vechi înainte de trimitere

### Long-term Memory Vector
Memorie reală per conversație, nu doar cache pe răspunsuri. Port al `services/memory_vector.py` din Odysseus.
- ChromaDB separat de semantic cache (fapte/preferințe per sesiune)
- Injectat automat în prompt la fiecare turn relevant

---

## Prioritate medie

### Telegram Bot Gateway
Înlocuitor mai bogat pentru ntfy.sh — posibilitate de răspuns direct la notificări din Telegram, nu doar one-way push.
- Aprobare risc direct din Telegram (fără să deschizi Kage UI)
- Notificări cu buton inline Approve/Deny
- Inspirat din arhitectura `telegram_gateway.py` din [Hermes Agent](https://github.com/nousresearch/hermes-agent) (MIT)

### Workspace Confinement
Limitarea agent tools la un folder definit, complement al risk gate-ului existent.
- `!run` și `!sysrun` primesc un `allowed_path` — orice operație în afara lui e blocată automat
- Port al `feat: workspace confinement` din Odysseus

### Round Limit + Continue
Prevenirea buclelor infinite în taskuri lungi de agent.
- Limită configurabilă de runde per `!run` task
- Buton "Continue" în Kage UI când agentul atinge limita

---

## Prioritate scăzută / Explorare

### Frontend Standalone
Înlocuirea `kage.html` embedded cu un frontend propriu scalabil.
- Opțiuni evaluate: Next.js, SvelteKit, sau vanilla JS extins
- Backend rămâne neschimbat (OpenAI-compatible API pe :4001)
- Blocat de: context compaction + memory — fără astea orice UI nou e la fel de limitat

### Settings UI Web
Editare `kage_config.json` și `risk_settings.json` din browser, fără SSH/editor.

### File Upload în Chat
Upload fișiere direct în conversație (cod, logs, documente) pentru analiză cu modele multimodale (T4 Gemini).

### MCP Streamable HTTP
Expunerea orchestratorului ca MCP server pentru integrare cu alte tooluri (Claude Desktop, IDE-uri).

---

## Ce NU facem (decizie conștientă)

- **Multi-user auth** — Kage e personal, nu hosted service
- **Docker setup** — overhead nejustificat pentru deploy local
- **Calendar / Email / Gallery integrations** — scope creep față de core use case
- **Skill creation loop** (Hermes-style) — arhitectură separată, complexitate nejustificată momentan
