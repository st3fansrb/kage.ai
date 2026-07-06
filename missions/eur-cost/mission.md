# Mission: Cost în EUR (felia de afișare din #7)

Adaugă afișarea costului în **EUR** pe lângă USD în run ledger. Intern totul rămâne
USD (așa raportează API-urile); conversia e DOAR la afișare, cu un curs static din
config (`eur_usd_rate`, default `0.92`) — fără apel la vreun API de curs valutar.

Prima misiune reală de dogfood a Mission Runner-ului (WP11). Lucrează pe un branch nou;
la final Stefan revizuiește PR-ul manual (mission-ul face codarea, omul face merge-ul +
restartul). Toate criteriile se verifică cu `pytest` — nu e nevoie de restart ca să treacă.

## WP1 — Branch de lucru ✅ (2026-07-06)

- Creează și treci pe un branch nou `feat/mission-eur-cost` pornit din `dev`.
- Nu modifica nimic altceva în acest pachet.

### Acceptare
- `git rev-parse --abbrev-ref HEAD | grep -qx feat/mission-eur-cost`

## WP2 — Helper de conversie + config

- În `orchestrator.py`, lângă celelalte constante de config (ex. zona `WHISPER_*` /
  `AGENT_INACTIVITY_TIMEOUT`), adaugă constanta `EUR_USD_RATE` citită din config:
  `float(_cfg.get("eur_usd_rate", 0.92))`.
- Adaugă o funcție **pură** `_usd_to_eur(usd, rate=None)`:
  - dacă `usd` e `None` → întoarce `None`;
  - altfel întoarce `round(usd * (rate if rate is not None else EUR_USD_RATE), 4)`.
- Documentează cheia `eur_usd_rate` în `kage_config.example.json` (cu un `_comment_*` scurt).
- Scrie `tests/test_eur.py` cu cazuri: `None → None`; `_usd_to_eur(1.0, 0.9) == 0.9`;
  un caz care folosește `EUR_USD_RATE` implicit (fără `rate`).

### Acceptare
- `grep -q "def _usd_to_eur" orchestrator.py`
- `.venv/bin/pytest -q tests/test_eur.py`

## WP3 — Expune `cost_eur` în /api/runs

- În endpoint-ul `/api/runs`, adaugă în fiecare dict din răspuns cheia `cost_eur` =
  `_usd_to_eur(cost_usd)` (folosind valoarea `cost_usd` a run-ului).
- În `tests/test_eur.py`, adaugă un test care — prin `fastapi.testclient.TestClient`, cu un
  run inserat care are `cost_usd` — verifică că `/api/runs` întoarce `cost_eur` corect calculat.

### Acceptare
- `.venv/bin/pytest -q tests/test_eur.py`
- `.venv/bin/pytest -q`
