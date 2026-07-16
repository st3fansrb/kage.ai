# Taskuri pentru Codex (sesiuni manuale, driver: Stefan)

Backlog pentru sesiunile în care Stefan rulează Codex manual. Codex SCRIE COD aici —
guvernanța e Stefan însuși (revizuiește și comite el), deci regula „Codex executor doar
post-G2" din KAGE-HANDOFF §4 NU se aplică (aia e despre integrarea programatică, WP-CX).

**Reguli pentru orice task (incluse în prompturi):**

- Serviciile vii rulează DIN checkout-ul `~/orchestrator-v2` — Codex NU lucrează acolo.
  Fiecare task își face worktree: `git worktree add ~/.kage-worktrees/codex-<slug> -b codex/<slug> dev`
  și lucrează EXCLUSIV în el. Config real (`kage_config.json`) și `cache_db/` nu se ating.
- Teritorii (până în weekendul 18–19.07, cât Claude lucrează lanțul principal):
  Claude = `docs/`, `orchestrator.py` core, `tests/` core, WP-PG. Codex = `video_intel.py`,
  handler-ele video din `telegram_gateway.py`, fișiere NOI (daemon trading, scripts).
  Wiring minim în `orchestrator.py` e permis doar în secțiunea video, izolat.
- `pytest` înainte și după. Commit-urile le face Stefan după review, în worktree, pe
  branch-ul `codex/<slug>`; PR spre `dev`.
- Bifează `[x]` + data la livrare.

---

## Cod

### [x] CX1 — T1-exec: daemonul freqtrade dry-run (Bucla 1 execuție) — 15.07.2026 (merged, PR #36)

Livrat în worktree-ul `codex/t1exec`: daemon paper-only, heartbeat/equity ledger, strategie
exemplu cu `bias_allows`, scripturi start/stop, documentație și smoke test.

### [x] CX2 — WP-V Slice 2: pasul vizual plătit + analiza adâncă T5 — 15.07.2026 (merged 16.07, PR #37)

Livrat în worktree-ul `codex/wpv2`: keyframes PNG base64 prin **OpenRouter** (Qwen3-VL;
NU LiteLLM/Haiku — alternativa aleasă de Codex, acceptată la review) + deep analysis pe
Sonnet tot prin OpenRouter, ambele fail-closed pe plafonul EUR (#7) cu costul estimat
înregistrat în `trading.db`, plus auto-trigger vizual pe semnale din transcript și teste
injectabile.

## Ferestre scurte (read-only, fără worktree)

### [x] C1 — Review pe diff-ul WP-SD (înainte de merge) — 15.07.2026

Livrabil: `docs/reviews/wpsd-codex-review.md`.

### [x] C2 — G1-minim: banca de taskuri KageBench (draft, docs-only) — 15.07.2026

Livrabil: `docs/kagebench-taskbank-draft.md` (antet `Codex proposal: G1`).
