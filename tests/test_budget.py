"""Teste pentru bugetul cloud — _usage_counts_today + _budget_check."""
import datetime
import json
import sqlite3

import orchestrator


def _set_max_cloud(monkeypatch, tmp_path, value):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"max_cloud_calls_per_day": value}), encoding="utf-8")
    monkeypatch.setattr(orchestrator, "KAGE_CONFIG_PATH", cfg)


def _usage_db(monkeypatch, rows):
    """In-memory SQLite cu tabelul `usage` populat; monkeypatch pe _db_conn."""
    conn = sqlite3.connect(":memory:")
    orchestrator._ensure_usage_table(conn)
    for ts, cloud in rows:
        conn.execute("INSERT INTO usage (ts, cloud) VALUES (?, ?)", (ts, 1 if cloud else 0))
    conn.commit()
    monkeypatch.setattr(orchestrator, "_db_conn", conn)
    return conn


# ── _usage_counts_today ───────────────────────────────────────────────────────

def test_usage_counts_today_from_db(monkeypatch):
    today = datetime.date.today().isoformat()
    _usage_db(monkeypatch, [
        (f"{today}T10:00:00", True),
        (f"{today}T11:00:00", False),
        ("2000-01-01T00:00:00", True),  # altă zi — nu se numără
    ])
    total, cloud = orchestrator._usage_counts_today()
    assert (total, cloud) == (2, 1)


def test_usage_counts_today_empty(monkeypatch):
    _usage_db(monkeypatch, [])
    assert orchestrator._usage_counts_today() == (0, 0)


def test_usage_counts_today_no_db(monkeypatch):
    monkeypatch.setattr(orchestrator, "_db_conn", None)
    assert orchestrator._usage_counts_today() == (0, 0)


def test_log_usage_inserts_and_updates_cache(monkeypatch):
    today = datetime.date.today().isoformat()
    conn = _usage_db(monkeypatch, [])
    # cache pe ziua curentă → _log_usage incrementează in-place
    orchestrator._usage_cache = {"date": today, "total": 0, "cloud": 0}
    orchestrator._log_usage(5, "sonnet", "task", 120, agent=None)   # cloud (tier>=3)
    orchestrator._log_usage(1, "qwen8b", "chat", 30, agent=None)    # local
    n = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
    assert n == 2
    assert orchestrator._usage_cache["total"] == 2
    assert orchestrator._usage_cache["cloud"] == 1


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
