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

### [ ] CX1 — T1-exec: daemonul freqtrade dry-run (Bucla 1 execuție)

Prioritate maximă dintre toate — dependent de CALENDAR, nu de efort: criteriul de
promovare e „~3 luni de paper profitabil", deci fiecare zi fără daemon amână discuția.

```text
Lucrezi pe proiectul Kage. Pregătire: git worktree add ~/.kage-worktrees/codex-t1exec -b codex/t1exec dev
și lucrează EXCLUSIV în worktree. Citește întâi CLAUDE.md, apoi docs/KAGE-HANDOFF.md:
secțiunea „### WP-T" (subpunctul despre crypto/freqtrade și Bucla 1 de execuție) + restanța
„Daemonul freqtrade dry-run" din §5. NU atinge kage_config.json real și cache_db/.

Task: pune pe picioare daemonul freqtrade în dry-run (paper-only — promovarea pe bani reali
e DOAR manuală, decizie închisă în §4): (1) setup freqtrade în venv separat (pattern
.jobs-venv — vezi scripts/setup.sh), config dry-run cu exchange public de date; (2) strategia
SampleStrategy cu apelul bias_allows() cablat (fișierul de strategie e gitignored — creează
și un exemplu tracked *_example.py); (3) script de pornire/oprire în scripts/ (pattern
start_litellm.sh) + logare în .logs/; (4) experiment ledger: trades/echity în trading.db
(SQLite separat, NU chat_history.db); (5) un smoke test care verifică că daemonul pornește
și scrie heartbeat. Ce NU faci: bani reali, chei API private, autostart launchd (decide
Stefan la review).

Livrabil: branch codex/t1exec cu pytest verde + README scurt în docs/ despre cum se
pornește/oprește și unde se văd trade-urile.
```

### [ ] CX2 — WP-V Slice 2: pasul vizual plătit + analiza adâncă T5

Condiția (#7 plafonul EUR) e livrată din 12.07 — slice-ul e deblocat.

```text
Lucrezi pe proiectul Kage. Pregătire: git worktree add ~/.kage-worktrees/codex-wpv2 -b codex/wpv2 dev
și lucrează EXCLUSIV în worktree. Citește întâi CLAUDE.md, apoi docs/KAGE-HANDOFF.md
secțiunea „### WP-V" integral (Slice 1 e livrat; tu faci Slice 2 — pasul vizual plătit +
🔎 analiza adâncă T5, azi ambele dau mesajul „se cablează în slice 2"). NU atinge
kage_config.json real și cache_db/.

Atenție la o decizie ulterioară specului: Gemini a fost RETRAS din Kage (WP-RMG, §4,
13.07.2026 — cauză externă). Dacă specul Slice 2 pomenește Gemini pentru descrierea vizuală,
NU-l folosi: rutează pasul vizual plătit prin LiteLLM pe tier-ul cloud ieftin existent
(Haiku, T3) cu imagini base64, gated pe plafonul din api_budget.py. Dacă vezi o alternativă
mai bună, scrie opțiunile într-un comentariu de PR și lasă decizia lui Stefan — nu o lua tu.

Scope: (1) descrierea vizuală plătită pe keyframes (extracția există din Slice 1), gated pe
buget + doar la butonul 🖼 sau când transcriptul trădează conținut vizual; (2) butonul 🔎
„analiză adâncă" pe T5, gated pe buget; (3) mesajele „se cablează în slice 2" dispar;
(4) teste noi în tests/ pe frontierele subprocess/LLM injectabile (pattern-ul celor 34 din
Slice 1). Fișiere: video_intel.py, handler-ele video din telegram_gateway.py, wiring minim
în orchestrator.py DOAR în secțiunea video, config video_intel în kage_config.example.json.

Livrabil: branch codex/wpv2 cu pytest verde + criteriile de acceptare din secțiunea WP-V
raportate unul câte unul.
```

## Ferestre scurte (read-only, fără worktree)

### [ ] C1 — Review pe diff-ul WP-SD (înainte de merge)

```text
Lucrezi în ~/orchestrator-v2 (citește CLAUDE.md întâi). Task READ-ONLY: NU face
checkout/commit, doar citește și scrie fișierul-livrabil.

Review adversarial pe diff-ul: git diff dev...feat/wp-sd-self-development
Criteriile + capcanele: docs/KAGE-HANDOFF.md secțiunea „### WP-SD". Verifică punctual:
(1) fiecare criteriu de acceptare are acoperire în diff sau e marcat explicit nefăcut;
(2) capcana „două worktree-uri nu pot ține același branch" (misiune reluată cu același slug);
(3) pytest-ul misiunii nu poate atinge kage_config.json real sau cache_db/ viu; (4) curățare
worktree la succes vs păstrare la eșec; (5) recovery la restart mid-misiune.

Livrabil: docs/reviews/wpsd-codex-review.md — constatări ordonate după severitate, fiecare
cu fișier:linie și scenariu concret de eșec. Zero constatări reale → spune explicit, nu inventa.
```

### [ ] C2 — G1-minim: banca de taskuri KageBench (draft, docs-only)

```text
Lucrezi în ~/orchestrator-v2 (citește CLAUDE.md întâi). Task READ-ONLY pe cod: NU face
checkout/commit, doar scrie fișierul-livrabil.

Context: propunerea ta G1 din docs/CODEX-PROPUNERI.md, acceptată ca „G1-minim"
(docs/KAGE-HANDOFF.md §4 + §5): 10–15 taskuri fixe cu criterii automate, regression gate.

Task: proiectează banca de taskuri. Per task: id, descriere, input exact trimis lui Kage,
criteriu de trecere VERIFICABIL AUTOMAT (assert pe răspuns/DB/fișier), subsistem acoperit
(rutare, cache, risk gate, misiuni, memorie, buget). Include cazuri negative (comandă
High-risk → cere aprobare, nu execută). Ancorează criteriile în comportamentul REAL din
orchestrator.py / risk_hook.py — verifică în cod.

Livrabil: docs/kagebench-taskbank-draft.md, antet „Codex proposal: G1".
```
