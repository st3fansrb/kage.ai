"""Teste pentru bugetul cloud — _usage_counts_today + _budget_check.

WP-PG: tabelul `usage` trăiește în Postgres — testele care-l ating cer fixture-ul `pg`.
"""
import datetime
import json

import orchestrator
import pg_store


def _set_max_cloud(monkeypatch, tmp_path, value):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"max_cloud_calls_per_day": value}), encoding="utf-8")
    monkeypatch.setattr(orchestrator, "KAGE_CONFIG_PATH", cfg)


def _seed_usage(rows):
    """Populează tabelul `usage` din DB-ul PG de test."""
    for ts, cloud in rows:
        pg_store.execute("INSERT INTO usage (ts, cloud) VALUES (%s, %s)",
                         (ts, 1 if cloud else 0))


# ── _usage_counts_today ───────────────────────────────────────────────────────

def test_usage_counts_today_from_db(pg):
    today = datetime.date.today().isoformat()
    _seed_usage([
        (f"{today}T10:00:00", True),
        (f"{today}T11:00:00", False),
        ("2000-01-01T00:00:00", True),  # altă zi — nu se numără
    ])
    total, cloud = orchestrator._usage_counts_today()
    assert (total, cloud) == (2, 1)


def test_usage_counts_today_empty(pg):
    assert orchestrator._usage_counts_today() == (0, 0)


def test_usage_counts_today_no_db():
    # reset_global_state lasă pg_store neconfigurat → degradare la (0, 0)
    assert orchestrator._usage_counts_today() == (0, 0)


def test_log_usage_inserts_and_updates_cache(pg):
    today = datetime.date.today().isoformat()
    # cache pe ziua curentă → _log_usage incrementează in-place
    orchestrator._usage_cache = {"date": today, "total": 0, "cloud": 0}
    orchestrator._log_usage(5, "sonnet", "task", 120, agent=None)   # cloud (tier>=3)
    orchestrator._log_usage(1, "qwen8b", "chat", 30, agent=None)    # local
    n = pg_store.fetchone("SELECT COUNT(*) FROM usage")[0]
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
