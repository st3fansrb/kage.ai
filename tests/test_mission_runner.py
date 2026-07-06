"""Teste pentru logica pură a Mission Runner-ului (WP11).

Parsare mission.md → WP-uri + criterii verificabile, marcarea ✅ idempotentă,
parsarea orei de reset dintr-un mesaj de rate-limit. Fără I/O — orchestrarea cu
stare e testată separat (test_mission_orchestration).
"""
import datetime

import mission_runner as mr


_SAMPLE = """# Mission: Test end-to-end

Un intro care se ignoră.

## WP1 — Adaugă funcția foo
- scrie `foo()` în utils.py
- documenteaz-o

### Acceptare
- `pytest -q tests/test_foo.py`
- funcția are docstring

## WP2 — Curăță ✅ (06.07)
- deja gata la o rulare anterioară

### Acceptare
- `ruff check .`
"""


def test_parse_title_and_wps():
    m = mr.parse_mission(_SAMPLE)
    assert m.title == "Test end-to-end"
    assert len(m.wps) == 2
    assert m.wps[0].title == "WP1 — Adaugă funcția foo"
    assert m.wps[1].title == "WP2 — Curăță"       # ✅ scos din titlu


def test_parse_body_excludes_acceptance():
    m = mr.parse_mission(_SAMPLE)
    body = m.wps[0].body
    assert "scrie `foo()`" in body
    assert "documenteaz-o" in body
    assert "pytest" not in body                    # criteriile nu-s în corp


def test_parse_shell_checks_vs_freetext():
    m = mr.parse_mission(_SAMPLE)
    wp = m.wps[0]
    assert wp.shell_checks == ["pytest -q tests/test_foo.py"]
    assert "funcția are docstring" in wp.criteria    # text liber rămâne criteriu
    assert len(wp.criteria) == 2


def test_parse_marks_done_from_checkmark():
    m = mr.parse_mission(_SAMPLE)
    assert m.wps[0].done is False
    assert m.wps[1].done is True                    # avea ✅ în antet


def test_parse_empty_mission():
    m = mr.parse_mission("# Mission: goală\n\nfără WP-uri")
    assert m.title == "goală" and m.wps == []


# ── mark_wp_done ──────────────────────────────────────────────────────────────

def test_mark_wp_done_appends_checkmark():
    md = "# Mission: X\n\n## WP1 — a\n- pas\n\n## WP2 — b\n- pas\n"
    out = mr.mark_wp_done(md, 0, stamp="06.07")
    assert "## WP1 — a ✅ (06.07)" in out
    assert "## WP2 — b\n" in out                    # WP2 neatins


def test_mark_wp_done_idempotent():
    md = "## WP1 — a ✅ (ieri)\n- pas\n"
    assert mr.mark_wp_done(md, 0, stamp="azi") == md  # deja marcat → neschimbat


# ── parse_rate_limit_reset ────────────────────────────────────────────────────

def test_rate_limit_relative_minutes():
    assert mr.parse_rate_limit_reset("rate limit reached, try again in 45 minutes") == 45 * 60


def test_rate_limit_relative_hours():
    assert mr.parse_rate_limit_reset("usage limit; retry in 2 hours") == 2 * 3600


def test_rate_limit_absolute_pm():
    now = datetime.datetime(2026, 7, 6, 14, 0, 0)   # 14:00
    secs = mr.parse_rate_limit_reset("limit reached. try again at 6pm", now=now)
    assert secs == 4 * 3600                          # 14:00 → 18:00


def test_rate_limit_absolute_next_day():
    now = datetime.datetime(2026, 7, 6, 20, 0, 0)   # 20:00
    secs = mr.parse_rate_limit_reset("resets at 9am", now=now)
    assert secs == 13 * 3600                          # 20:00 → 09:00 mâine


def test_rate_limit_no_match_returns_none():
    assert mr.parse_rate_limit_reset("ceva eroare fără oră") is None
    # cifre aleatorii fără ancoră de reset nu declanșează
    assert mr.parse_rate_limit_reset("processed 42 items") is None
