"""Teste pentru workspace confinement — _validate_task_cwd."""
from pathlib import Path

import orchestrator


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
