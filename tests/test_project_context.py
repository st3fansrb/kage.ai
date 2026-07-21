"""Teste pentru _get_project_context: grounding-ul chat-ului în docs/KAGE-HANDOFF.md.

Contract: pe mențiune de WP sau întrebare despre starea/planul proiectului, injectează
DOAR secțiunile relevante (ultima reordonare + WP-ul menționat), nu fișierul întreg,
și NU face potrivire de tip prefix (WP1 nu trebuie să potrivească WP10/WP1b).
"""
import pytest

import orchestrator as orch

_FAKE_HANDOFF = """# KAGE-HANDOFF — fixture de test

## 5. Pachetele de lucru

### Reordonare (10.07.2026) — veche
MARKER_REORDER_OLD

### Reordonare (14.07.2026) — nouă, starea curentă
MARKER_REORDER_NEW

### WP1 (#1) — Reparația fundației ✅
MARKER_WP1

### WP1b — Consolidarea canalelor
MARKER_WP1B

### WP10 (#15B) — Kage Mission Control
MARKER_WP10

### WP-G2 — izolare reală
MARKER_WPG2

### WP13 — Advisor-in-the-loop
MARKER_WP13

### WP-KF — Kafka ca transport de evenimente AMÂNAT
MARKER_WPKF

### Restul (ordinea de aici e ÎNLOCUITĂ)
MARKER_RESTUL

## 6. Governance

### WP-G3 — un alt pachet gata ✅
MARKER_WPG3

## 7. Housekeeping

### WP99 — în afara scopului de scanare status (§7, nu §5/§6)
MARKER_WP99
"""


@pytest.fixture
def _fake_handoff(tmp_path, monkeypatch):
    path = tmp_path / "KAGE-HANDOFF.md"
    path.write_text(_FAKE_HANDOFF, encoding="utf-8")
    monkeypatch.setattr(orch, "PROJECT_HANDOFF_PATH", path)
    return path


def test_no_trigger_returns_none(_fake_handoff):
    assert orch._get_project_context("salut, ce faci azi?") is None


def test_wp_mention_includes_latest_reorder_and_matched_section(_fake_handoff):
    ctx = orch._get_project_context("ce facem cu WP13 mai departe?")
    assert ctx is not None
    assert "MARKER_WP13" in ctx
    assert "MARKER_REORDER_NEW" in ctx
    assert "MARKER_REORDER_OLD" not in ctx


def test_wp1_does_not_falsely_match_wp10_or_wp1b(_fake_handoff):
    ctx = orch._get_project_context("care e stadiul lui WP1?")
    assert ctx is not None
    assert "MARKER_WP1\n" in ctx or ctx.endswith("MARKER_WP1")
    assert "MARKER_WP10" not in ctx
    assert "MARKER_WP1B" not in ctx


def test_dash_variant_matches_same_as_no_dash(_fake_handoff):
    ctx_dash = orch._get_project_context("status pe WP-G2")
    ctx_nodash = orch._get_project_context("status pe WPG2")
    assert ctx_dash is not None and "MARKER_WPG2" in ctx_dash
    assert ctx_nodash is not None and "MARKER_WPG2" in ctx_nodash


def test_trigger_phrase_without_wp_returns_status_and_latest_reorder_only(_fake_handoff):
    ctx = orch._get_project_context("unde suntem cu proiectul?")
    assert ctx is not None
    assert "Stare live" in ctx
    assert "MARKER_REORDER_NEW" in ctx
    # doar rezumatul de status + blocul de reordonare, nu conținutul secțiunilor WP
    assert "MARKER_WP13" not in ctx
    assert "MARKER_WP1\n" not in ctx


def test_missing_handoff_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "PROJECT_HANDOFF_PATH", tmp_path / "nu-exista.md")
    assert orch._get_project_context("ce urmează în roadmap?") is None


def test_result_is_capped_at_max_chars(tmp_path, monkeypatch):
    huge_section = "MARKER_WP1\n" + ("x" * (orch.PROJECT_CONTEXT_MAX_CHARS * 2))
    text = f"### Reordonare (14.07.2026)\n{huge_section}\n\n### WP1 — test\n{huge_section}\n"
    path = tmp_path / "KAGE-HANDOFF.md"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(orch, "PROJECT_HANDOFF_PATH", path)

    ctx = orch._get_project_context("ce urmează cu WP1?")
    assert ctx is not None
    assert len(ctx) <= orch.PROJECT_CONTEXT_MAX_CHARS


# ── _wp_id: potrivire exactă, nu prefix ──────────────────────────────────────────

def test_wp_id_extraction():
    assert orch._wp_id("WP13") == "13"
    assert orch._wp_id("WP-G2") == "g2"
    assert orch._wp_id("WP1b") == "1b"
    assert orch._wp_id("WP1 (#1) — Reparația fundației") == "1"
    assert orch._wp_id("nimic aici") is None


# ── _handoff_wp_status_summary: rezumat live gata/rămas/amânat ──────────────────

def _parse_status_list(line: str) -> set[str]:
    _, _, items = line.partition(":")
    return {x.strip() for x in items.split(",") if x.strip() and x.strip() != "—"}


def test_status_summary_classifies_done_pending_deferred(_fake_handoff):
    text = orch.PROJECT_HANDOFF_PATH.read_text(encoding="utf-8")
    summary = orch._handoff_wp_status_summary(text)
    assert summary is not None

    gata = _parse_status_list(next(l for l in summary.splitlines() if l.startswith("- Gata")))
    ramase = _parse_status_list(next(l for l in summary.splitlines() if l.startswith("- Rămase")))
    amanate = _parse_status_list(next(l for l in summary.splitlines() if l.startswith("- Amânate")))

    assert gata == {"WP1", "WP-G3"}
    assert ramase == {"WP1b", "WP10", "WP-G2", "WP13"}
    assert amanate == {"WP-KF"}


def test_status_summary_excludes_reordonare_and_restul_headings(_fake_handoff):
    text = orch.PROJECT_HANDOFF_PATH.read_text(encoding="utf-8")
    summary = orch._handoff_wp_status_summary(text)
    assert "Reordonare" not in summary
    assert "Restul" not in summary


def test_status_summary_scoped_to_section_5_and_6_only(_fake_handoff):
    text = orch.PROJECT_HANDOFF_PATH.read_text(encoding="utf-8")
    summary = orch._handoff_wp_status_summary(text)
    assert "WP99" not in summary  # definit sub §7, în afara scopului de scanare


def test_status_summary_returns_none_without_section_5_marker():
    assert orch._handoff_wp_status_summary("# doc fără secțiunea 5\n### WP1 ✅\nx\n") is None


def test_status_summary_included_by_default_in_project_context(_fake_handoff):
    ctx = orch._get_project_context("ce urmează în roadmap?")
    assert ctx is not None
    assert "Stare live" in ctx
    assert "WP-G2" in ctx  # din rezumatul de status, ca token scurt


def test_status_summary_also_included_when_specific_wp_mentioned(_fake_handoff):
    ctx = orch._get_project_context("ce facem cu WP13 mai departe?")
    assert "Stare live" in ctx
