"""Contracte HTTP pentru R0: versiuni, idempotency, paginare și 429.

WP-PG: missions/usage → PG de test (fixture `pg`); messages + idempotency_keys → SQLite.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

import orchestrator
import pg_store


@pytest.fixture
def api_db(pg, monkeypatch, tmp_path):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("""CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT DEFAULT 'default', role TEXT, content TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    monkeypatch.setattr(orchestrator, "_db_conn", conn)
    monkeypatch.setattr(orchestrator, "MISSIONS_DIR", tmp_path / "missions")
    monkeypatch.setattr(orchestrator, "_active_mission_id", None)
    monkeypatch.setattr(orchestrator, "_mission_launch", lambda _mid: None)
    monkeypatch.setattr(orchestrator, "_get_api_token", lambda: "")
    orchestrator._rate_limit_buckets.clear()
    yield conn
    conn.close()


@pytest.fixture
def client(api_db):
    return TestClient(orchestrator.app)


def _mission_file(tmp_path):
    path = tmp_path / "missions" / "demo" / "mission.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Mission: Demo API\n\n## WP1 — contract\n- lucru\n", encoding="utf-8")
    return path


def test_missions_versioned_contract_and_pagination(client, api_db, tmp_path):
    _mission_file(tmp_path)
    for suffix in ("a", "b"):
        pg_store.execute(
            "INSERT INTO missions (id, slug, title, path, cwd, status, current_idx, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, 'done', 0, %s, %s)",
            (suffix, suffix, "Title " + suffix, "/tmp/mission.md", "/tmp", suffix, suffix),
        )
        pg_store.execute("INSERT INTO mission_wps (mission_id, idx, title, status) VALUES (%s, 0, 'WP', 'done')", (suffix,))

    response = client.get("/v1/missions?limit=1&offset=1")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert set(body[0]) == {"id", "slug", "title", "status", "current_idx", "created_at", "updated_at", "wps_total", "wps_done"}


def test_mission_create_idempotency_replays_without_duplicate(client, api_db, tmp_path):
    _mission_file(tmp_path)
    headers = {"Idempotency-Key": "mobile-retry-1"}
    first = client.post("/v1/missions", json={"source": "demo", "cwd": str(tmp_path)}, headers=headers)
    second = client.post("/v1/missions", json={"source": "demo", "cwd": str(tmp_path)}, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    assert second.headers["Idempotency-Replayed"] == "true"
    assert pg_store.fetchone("SELECT COUNT(*) FROM missions")[0] == 1


def test_idempotency_key_rejects_different_payload(client, tmp_path):
    _mission_file(tmp_path)
    headers = {"Idempotency-Key": "same-key"}
    assert client.post("/v1/missions", json={"source": "demo"}, headers=headers).status_code == 201
    response = client.post("/v1/missions", json={"source": "other"}, headers=headers)
    assert response.status_code == 409


def test_usage_contract_is_paginated(client, api_db):
    for idx in range(3):
        pg_store.execute("INSERT INTO usage (ts, tier, model, cloud, agent, duration_ms, preview) VALUES (%s, 1, 'qwen', 0, NULL, 4, 'x')", ("2026-07-14T00:00:0" + str(idx),))
    response = client.get("/v1/usage?limit=2&offset=1")
    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert len(response.json()["items"]) == 2


def test_gateway_rate_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RATE_LIMIT_MAX_REQUESTS", 1)
    monkeypatch.setattr(orchestrator, "_RATE_LIMIT_WINDOW_SECONDS", 60)
    # Corp invalid intenționat: prima cerere consumă tokenul înainte de validarea chat-ului.
    client.post("/v1/chat/completions", json={})
    response = client.post("/v1/chat/completions", json={})
    assert response.status_code == 429
    assert response.headers["Retry-After"]


def test_pending_has_explicit_contract(client, monkeypatch):
    monkeypatch.setattr(orchestrator, "pending_risk_meta", {
        "risk-1": {"id": "risk-1", "tool_name": "Bash", "cmd": "pwd", "reason": "test", "time": "now"}
    })
    monkeypatch.setattr(orchestrator, "risk_decisions", {})
    response = client.get("/api/pending")
    assert response.status_code == 200
    assert response.json()[0]["id"] == "risk-1"
