import sqlite3

from fastapi.testclient import TestClient

import orchestrator as orch
from orchestrator import _usd_to_eur, EUR_USD_RATE


def test_none_returns_none():
    assert _usd_to_eur(None) is None


def test_explicit_rate():
    assert _usd_to_eur(1.0, 0.9) == 0.9


def test_default_rate():
    assert _usd_to_eur(2.0) == round(2.0 * EUR_USD_RATE, 4)


def test_api_runs_exposes_cost_eur(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    orch._ensure_runs_table(conn)
    conn.execute(
        "INSERT INTO runs (id, kind, status, cost_usd, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("run-1", "chat", "done", 1.0, "2026-07-06T10:00:00"),
    )
    conn.commit()
    monkeypatch.setattr(orch, "_db_conn", conn)
    monkeypatch.setattr(orch, "_get_api_token", lambda: "")

    client = TestClient(orch.app)
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["cost_usd"] == 1.0
    assert data[0]["cost_eur"] == _usd_to_eur(1.0)
