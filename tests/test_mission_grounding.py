"""Teste pentru grounding-ul draft-ului de misiune în docs/KAGE-HANDOFF.md.

Contract: _mission_draft_text rulează _agent_complete FĂRĂ tools (allowed_tools=[]) —
modelul de draft nu poate citi fișiere. Dacă direcția/revizia menționează un WP din
roadmap, promptul trebuie să conțină secțiunea relevantă extrasă de _get_project_context,
altfel modelul ar inventa scopul misiunii din nimic.
"""
import pytest

import orchestrator as orch


_FAKE_HANDOFF = """# KAGE-HANDOFF — fixture de test

### Reordonare (14.07.2026) — starea curentă
MARKER_REORDER

### WP13 — Advisor-in-the-loop
MARKER_WP13_SCOPE: advisor + HITL v2, pași concreți din roadmap
"""

_DRAFT_MD = """# Mission: test

## WP1 — pas
- a
### Acceptare
- `true`
"""


@pytest.fixture
def _fake_handoff(tmp_path, monkeypatch):
    path = tmp_path / "KAGE-HANDOFF.md"
    path.write_text(_FAKE_HANDOFF, encoding="utf-8")
    monkeypatch.setattr(orch, "PROJECT_HANDOFF_PATH", path)


@pytest.fixture
def _capture_prompt(monkeypatch):
    captured: list[str] = []

    async def _fake_complete(prompt, *, model, system=None):
        captured.append(prompt)
        return _DRAFT_MD

    monkeypatch.setattr(orch, "_agent_complete", _fake_complete)
    return captured


async def test_wp_mention_in_direction_grounds_the_prompt(_fake_handoff, _capture_prompt):
    await orch._mission_draft_text("execută WP13 din plan")
    assert len(_capture_prompt) == 1
    assert "MARKER_WP13_SCOPE" in _capture_prompt[0]
    assert "MARKER_REORDER" in _capture_prompt[0]


async def test_no_wp_mention_leaves_prompt_ungrounded(_fake_handoff, _capture_prompt):
    await orch._mission_draft_text("construiește un endpoint nou de health check")
    assert len(_capture_prompt) == 1
    assert "MARKER_WP13_SCOPE" not in _capture_prompt[0]
    assert "MARKER_REORDER" not in _capture_prompt[0]


async def test_wp_mention_in_revise_instructions_also_grounds_prompt(_fake_handoff, _capture_prompt):
    await orch._mission_draft_text(
        "planul vechi", prior_md="# Mission: veche\n\n## WP1\n- a\n### Acceptare\n- `true`\n",
        revise="aliniază la WP13 din roadmap",
    )
    assert len(_capture_prompt) == 1
    assert "MARKER_WP13_SCOPE" in _capture_prompt[0]


async def test_missing_handoff_file_does_not_break_drafting(tmp_path, monkeypatch, _capture_prompt):
    monkeypatch.setattr(orch, "PROJECT_HANDOFF_PATH", tmp_path / "nu-exista.md")
    md = await orch._mission_draft_text("execută WP13 din plan")
    assert md.startswith("# Mission:")
    assert len(_capture_prompt) == 1
