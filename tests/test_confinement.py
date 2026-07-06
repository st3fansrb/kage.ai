"""Teste pentru workspace confinement.

Două straturi:
- orchestrator `_validate_task_cwd` — confinement pe cwd-ul de pornire (Faza 19);
- risk_hook `_path_in_allowed_roots` + `evaluate_risk` — confinement per-tool-call
  pe Write/Edit (D4 punctul 3 / #2): scriere în afara rooturilor → aprobare.
"""
from pathlib import Path

import orchestrator
import risk_hook


def test_disabled_returns_canonical(monkeypatch):
    monkeypatch.setattr(orchestrator, "ALLOWED_TASK_ROOTS", [])
    out = orchestrator._validate_task_cwd("/tmp/../tmp")
    assert out == str(Path("/tmp").resolve())


def test_path_inside_allowed_root(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "ALLOWED_TASK_ROOTS", [tmp_path.resolve()])
    sub = tmp_path / "proj" / "sub"
    sub.mkdir(parents=True)
    assert orchestrator._validate_task_cwd(str(sub)) == str(sub.resolve())


def test_path_outside_allowed_root_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "ALLOWED_TASK_ROOTS", [(tmp_path / "allowed").resolve()])
    (tmp_path / "allowed").mkdir()
    assert orchestrator._validate_task_cwd("/tmp") is None


def test_dotdot_bypass_blocked(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setattr(orchestrator, "ALLOWED_TASK_ROOTS", [allowed.resolve()])
    # încearcă să scape din allowed/ prin ..
    assert orchestrator._validate_task_cwd(str(allowed / ".." / "secret")) is None


def test_project_root_always_allowed(monkeypatch, tmp_path):
    # chiar dacă PROJECT_ROOT nu e în lista configurată, e permis implicit (pentru !sysrun)
    monkeypatch.setattr(orchestrator, "ALLOWED_TASK_ROOTS", [(tmp_path / "other").resolve()])
    assert orchestrator._validate_task_cwd(str(orchestrator.PROJECT_ROOT)) == str(
        orchestrator.PROJECT_ROOT.resolve()
    )


# ── risk_hook: helper pur _path_in_allowed_roots ──────────────────────────────

def test_hook_path_inside_root_is_allowed(tmp_path):
    target = tmp_path / "sub" / "file.txt"
    assert risk_hook._path_in_allowed_roots(str(target), [str(tmp_path)]) is True


def test_hook_path_outside_root_is_rejected(tmp_path):
    inside = tmp_path / "work"
    inside.mkdir()
    outside = tmp_path / "secret.txt"  # frate cu rootul, nu în interior
    assert risk_hook._path_in_allowed_roots(str(outside), [str(inside)]) is False


def test_hook_empty_roots_disables_confinement(tmp_path):
    assert risk_hook._path_in_allowed_roots(str(tmp_path / "orice"), []) is True


def test_hook_dotdot_bypass_is_rejected(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    escaped = root / ".." / "secret.txt"  # canonicalizat iese din root
    assert risk_hook._path_in_allowed_roots(str(escaped), [str(root)]) is False


# ── risk_hook: aplicarea confinement-ului pe Write/Edit ───────────────────────

def test_hook_write_outside_root_is_high(tmp_path, monkeypatch):
    monkeypatch.setattr(risk_hook, "ALLOWED_TASK_ROOTS", [str(tmp_path)])
    outside = "/Users/altcineva/Desktop/nota.txt"
    lvl, _ = risk_hook.evaluate_risk("Write", {"file_path": outside}, "")
    assert lvl == "High"


def test_hook_write_outside_root_downgrades_with_explicit_keyword(tmp_path, monkeypatch):
    monkeypatch.setattr(risk_hook, "ALLOWED_TASK_ROOTS", [str(tmp_path)])
    outside = "/Users/altcineva/Desktop/nota.txt"
    lvl, _ = risk_hook.evaluate_risk("Write", {"file_path": outside}, "șterge nota aia")
    assert lvl == "Medium"


def test_hook_write_inside_root_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(risk_hook, "ALLOWED_TASK_ROOTS", [str(tmp_path)])
    inside = str(tmp_path / "proiect" / "main.py")
    lvl, _ = risk_hook.evaluate_risk("Write", {"file_path": inside}, "")
    assert lvl == "Safe"


def test_hook_empty_roots_write_outside_is_safe(tmp_path, monkeypatch):
    # confinement dezactivat global (roots gol) → nu blochează scrierile din afară
    monkeypatch.setattr(risk_hook, "ALLOWED_TASK_ROOTS", [])
    lvl, _ = risk_hook.evaluate_risk("Write", {"file_path": "/tmp/oriunde.txt"}, "")
    assert lvl == "Safe"
