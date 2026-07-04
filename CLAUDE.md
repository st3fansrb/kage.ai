# CLAUDE.md — Kage (orchestrator-v2)

Kage = orchestrator personal de AI, single-user, self-hosted pe macOS (MacBook Pro M5 Pro, 48GB).
FastAPI `:4001` (`orchestrator.py`, ~2750 linii) · LiteLLM `:4000` · Ollama `:11434`.
Ce este și ce face: `DESPRE_KAGE.md`.

## Documente de citit ÎNAINTE de a implementa ceva

1. **`KAGE-HANDOFF.md`** — planul de execuție curent: pachete de lucru ordonate, criterii de
   acceptare, capcane cunoscute. **Obligatoriu** pentru orice item din lista de îmbunătățiri.
2. `KAGE-EVALUARE.md` — evaluarea tehnică completă (diagnostic D1–D15, gap agentic, cercetare,
   priorități #1–#15). Handoff-ul o referă; nu re-deriva evaluarea.
3. `ROADMAP.md` — istoricul fazelor 1–19 + decizii de scope.

## Reguli

- **Codul e sursa de adevăr, nu documentația.** README/DESPRE_KAGE descriu intenția; mai multe
  afirmații sunt contrazise de cod (listate în KAGE-HANDOFF §2–3). Verifică în cod înainte să
  presupui că un feature funcționează.
- **Python runtime = 3.9.6** (`.venv`) — fără sintaxă 3.10+ (`match`, `X | Y` în adnotări
  evaluate la runtime, `tomllib` etc.). Excepție: `status_widget.py` rulează pe 3.12
  (`.widget-venv`).
- Un pachet de lucru per branch; branch din `dev` (branch-ul de PR-uri). `pytest` înainte și
  după orice modificare (baseline 03.07.2026: 28 de teste, toate verzi).
- Serverul poate rula în producție pe mașina asta (`start_all.sh`) — nu reporni servicii fără
  să anunți userul.
- `kage_config.json` e config-ul real (template: `kage_config.example.json`) — conține
  token-uri; nu-l comite și nu-l afișa integral.
- Răspunde userului în română.

## Comenzi

- Teste: `source .venv/bin/activate && pytest`
- Pornire servicii: `./start_all.sh` · Log: `.logs/orchestrator.log`
- Health: `curl localhost:4001/health` · UI: `localhost:4001/chat` · Dashboard: `/dashboard`

## Fișiere cheie

- `orchestrator.py` — tot nucleul: rutare 6-tier, cache semantic, memorie, budget, agenți
  (`!run`/`!swarm`/`!sysrun`), scheduler, backup, toate endpoint-urile
- `risk_hook.py` — PreToolUse hook (matricea de risc) · `risk_settings.json` — matcher hooks
- `telegram_gateway.py` — gateway Telegram (polling) · `kage.html` — UI web servit la `/chat`
- `status_widget.py` — widget menubar (Python 3.12!) · `tests/` — suită pytest
- `cache_db/` — ChromaDB + `chat_history.db` (SQLite) — date live, nu le șterge
