"""Teste pentru bugetul cloud — _usage_counts_today + _budget_check."""
import json

import orchestrator


def _set_max_cloud(monkeypatch, tmp_path, value):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"max_cloud_calls_per_day": value}), encoding="utf-8")
    monkeypatch.setattr(orchestrator, "NTFY_CONFIG_PATH", cfg)


# ── _usage_counts_today ───────────────────────────────────────────────────────

def test_usage_counts_today_from_log(monkeypatch, tmp_path):
    import datetime
    today = datetime.date.today().isoformat()
    log = tmp_path / "usage_log.jsonl"
    log.write_text(
        json.dumps({"ts": f"{today}T10:00:00", "cloud": True}) + "\n"
        + json.dumps({"ts": f"{today}T11:00:00", "cloud": False}) + "\n"
        + json.dumps({"ts": "2000-01-01T00:00:00", "cloud": True}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestrator, "USAGE_LOG", log)
    total, cloud = orchestrator._usage_counts_today()
    assert (total, cloud) == (2, 1)


def test_usage_counts_today_no_file(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "USAGE_LOG", tmp_path / "missing.jsonl")
    assert orchestrator._usage_counts_today() == (0, 0)


# ── _budget_check ─────────────────────────────────────────────────────────────

def test_budget_under_limit_passes(monkeypatch, tmp_path):
    _set_max_cloud(monkeypatch, tmp_path, 20)
    monkeypatch.setattr(orchestrator, "_usage_counts_today", lambda: (5, 5))
    monkeypatch.setattr(orchestrator, "_notify", lambda *a, **k: None)
    tier, conf, warning = orchestrator._budget_check(5, 0.85)
    assert (tier, warning) == (5, None)


def test_budget_over_limit_downgrades_to_t2(monkeypatch, tmp_path):
    _set_max_cloud(monkeypatch, tmp_path, 20)
    monkeypatch.setattr(orchestrator, "_usage_counts_today", lambda: (20, 20))
    monkeypatch.setattr(orchestrator, "_notify", lambda *a, **k: None)
    tier, conf, warning = orchestrator._budget_check(5, 0.85)
    assert tier == 2 and warning is not None


def test_budget_80pct_warning(monkeypatch, tmp_path):
    _set_max_cloud(monkeypatch, tmp_path, 20)
    monkeypatch.setattr(orchestrator, "_usage_counts_today", lambda: (16, 16))
    monkeypatch.setattr(orchestrator, "_notify", lambda *a, **k: None)
    tier, conf, warning = orchestrator._budget_check(5, 0.85)
    # tier neschimbat, dar avertisment prezent
    assert tier == 5 and warning is not None
