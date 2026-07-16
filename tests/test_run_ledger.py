"""Teste pentru run ledger + decision trace + aprobări persistente (WP8 / #5).

Acoperă: schema + helperii _run_start/_run_event/_run_update/_run_end, deducerea canalului,
persistența aprobărilor de risc (supraviețuire restart), endpoint-ul /api/runs, fix-ul D9
(cache hit salvat în SQLite + stream disconnect-safe), și un run creat per chat real.

WP-PG: runs/run_events trăiesc în Postgres (fixture `pg`, DB de test); messages +
pending_approvals rămân pe SQLite in-memory. Niciun apel real de rețea.
"""
import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import orchestrator
import pg_store


@pytest.fixture
def ledger_db(pg, monkeypatch):
    """runs/run_events → PG de test (via `pg`); messages + pending_approvals → SQLite."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("""CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT DEFAULT 'default',
        role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    orchestrator._ensure_approvals_table(conn)
    conn.commit()
    monkeypatch.setattr(orchestrator, "_db_conn", conn)
    yield conn
    conn.close()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(orchestrator, "_get_api_token", lambda: "")
    return TestClient(orchestrator.app)


# ── Helpers de bază ───────────────────────────────────────────────────────────

def test_run_start_creates_row(ledger_db):
    rid = orchestrator._run_start("chat", session_id="s1", channel="ui", input_text="salut")
    assert rid
    row = pg_store.fetchone("SELECT kind, session_id, channel, input, status FROM runs WHERE id=%s", (rid,))
    assert row == ("chat", "s1", "ui", "salut", "running")


def test_run_start_none_without_db():
    # reset_global_state lasă pg_store neconfigurat → ledgerul degradează la no-op
    assert orchestrator._run_start("chat") is None


def test_run_event_and_update_and_end(ledger_db):
    rid = orchestrator._run_start("chat", input_text="x")
    orchestrator._run_event(rid, "routing", {"tier": 3, "method": "sem"})
    orchestrator._run_update(rid, tier=3, model="haiku", cache_hit=1)
    orchestrator._run_end(rid, "done", duration_ms=42)

    ev = pg_store.fetchone("SELECT type, payload FROM run_events WHERE run_id=%s", (rid,))
    assert ev[0] == "routing"
    assert json.loads(ev[1])["tier"] == 3
    row = pg_store.fetchone("SELECT tier, model, cache_hit, status, duration_ms, finished_at FROM runs WHERE id=%s", (rid,))
    assert row[0] == 3 and row[1] == "haiku" and row[2] == 1
    assert row[3] == "done" and row[4] == 42 and row[5] is not None


def test_run_update_ignores_unknown_fields(ledger_db):
    rid = orchestrator._run_start("chat")
    orchestrator._run_update(rid, tier=2, bogus="DROP")  # bogus ignorat, nu crapă
    assert pg_store.fetchone("SELECT tier FROM runs WHERE id=%s", (rid,))[0] == 2


def test_run_event_payload_truncated(ledger_db):
    rid = orchestrator._run_start("chat")
    orchestrator._run_event(rid, "big", {"blob": "x" * 10000})
    payload = pg_store.fetchone("SELECT payload FROM run_events WHERE run_id=%s", (rid,))[0]
    assert len(payload) <= 4096


def test_channel_for():
    assert orchestrator._channel_for("telegram_123") == "telegram"
    assert orchestrator._channel_for("cron_job") == "cron"
    assert orchestrator._channel_for("default") == "ui"
    assert orchestrator._channel_for("") == "ui"


# ── Aprobări persistente (WP8 §3) ─────────────────────────────────────────────

def test_approval_persist_resolve_load(ledger_db, monkeypatch):
    meta = {"tool_name": "Bash", "cmd": "rm -rf /", "reason": "periculos", "time": "now"}
    orchestrator._persist_approval("req1", meta)
    orchestrator._persist_approval("req2", {"tool_name": "Bash", "cmd": "ls"})
    orchestrator._resolve_approval("req2", "confirm")

    # simulează restart: golește cache-urile in-memory, reîncarcă din DB
    monkeypatch.setattr(orchestrator, "pending_risk_meta", {})
    monkeypatch.setattr(orchestrator, "risk_decisions", {})
    orchestrator._load_pending_approvals()

    assert "req1" in orchestrator.pending_risk_meta          # pending → revine în UI
    assert orchestrator.pending_risk_meta["req1"]["cmd"] == "rm -rf /"
    assert "req2" not in orchestrator.pending_risk_meta       # rezolvat → nu mai e pending
    assert orchestrator.risk_decisions.get("req2") == "confirm"  # decizia supraviețuiește


def test_risk_register_persists_to_db(ledger_db, client, monkeypatch):
    monkeypatch.setattr(orchestrator, "_tg_gateway", None)
    r = client.post("/risk/register/abc", json={"tool_name": "Bash", "cmd": "whoami", "reason": "test"})
    assert r.status_code == 200
    row = ledger_db.execute("SELECT status, cmd FROM pending_approvals WHERE id='abc'").fetchone()
    assert row == ("pending", "whoami")


def test_risk_respond_resolves_in_db(ledger_db, client, monkeypatch):
    monkeypatch.setattr(orchestrator, "_tg_gateway", None)
    client.post("/risk/register/xy", json={"tool_name": "Bash", "cmd": "id"})
    r = client.post("/risk/respond/xy", json={"action": "block"})
    assert r.status_code == 200
    status = ledger_db.execute("SELECT status FROM pending_approvals WHERE id='xy'").fetchone()[0]
    assert status == "block"


# ── /api/runs ─────────────────────────────────────────────────────────────────

def test_api_runs_returns_json(ledger_db, client):
    rid = orchestrator._run_start("chat", channel="ui", input_text="hello")
    orchestrator._run_update(rid, tier=3, model="haiku")
    orchestrator._run_end(rid, "done", duration_ms=100)
    r = client.get("/api/runs")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["id"] == rid and data[0]["status"] == "done" and data[0]["tier"] == 3


def test_api_run_detail_with_events(ledger_db, client):
    rid = orchestrator._run_start("chat", input_text="q")
    orchestrator._run_event(rid, "routing", {"tier": 5})
    orchestrator._run_event(rid, "result", {"chars": 10})
    orchestrator._run_end(rid, "done")
    r = client.get(f"/api/runs/{rid}")
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == rid
    assert [e["type"] for e in d["events"]] == ["routing", "result"]
    assert d["events"][0]["payload"]["tier"] == 5


def test_api_run_detail_404(ledger_db, client):
    assert client.get("/api/runs/nonexistent").status_code == 404


# ── D9: cache hit salvat + disconnect-safe ────────────────────────────────────

def test_cache_hit_saves_history_and_run(ledger_db, client, monkeypatch):
    async def _fake_lookup(q):
        return ("Răspunsul din cache.", 3)
    monkeypatch.setattr(orchestrator, "_cache_policy", lambda m, lu: (True, True, "cheie"))
    monkeypatch.setattr(orchestrator, "_cache_lookup", _fake_lookup)

    r = client.post("/v1/chat/completions",
                    json={"model": "auto", "stream": False,
                          "messages": [{"role": "user", "content": "întrebare"}]},
                    headers={"x-session-id": "sess1"})
    assert r.status_code == 200
    assert "Răspunsul din cache." in r.json()["choices"][0]["message"]["content"]

    # D9: perechea apare în istoricul SQLite
    msgs = ledger_db.execute("SELECT role, content FROM messages WHERE session_id='sess1' ORDER BY id").fetchall()
    assert msgs == [("user", "întrebare"), ("assistant", "Răspunsul din cache.")]
    # Run complet cu cache_hit (în Postgres, WP-PG)
    run = pg_store.fetchone("SELECT kind, cache_hit, status FROM runs ORDER BY created_at DESC LIMIT 1")
    assert run == ("chat", 1, "done")


@pytest.mark.asyncio
async def test_persisting_stream_survives_disconnect(ledger_db):
    done = asyncio.Event()
    captured = {}

    async def _src():
        for t in ["Sal", "ut ", "lume"]:
            yield f'data: {json.dumps({"choices": [{"delta": {"content": t}}]})}\n\n'
        yield "data: [DONE]\n\n"

    async def _on_complete(full):
        captured["full"] = full
        done.set()

    client_gen = orchestrator._persisting_stream(_src(), on_complete=_on_complete)
    # Consumă DOAR primul chunk, apoi abandonează (client deconectat)
    first = await client_gen.__anext__()
    await client_gen.aclose()

    # Drenajul de fundal trebuie să termine și să salveze tot, în ciuda deconectării
    await asyncio.wait_for(done.wait(), timeout=3)
    assert captured["full"] == "Salut lume"
    assert "Sal" in first
