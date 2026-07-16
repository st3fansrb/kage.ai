"""Teste pentru stratul WP-PG: pg_store, migrarea cutover și status-ul dual-write.

Acoperă criteriile de acceptare WP-PG: scrierile de stare merg în Postgres ·
migrarea e idempotentă și verifică numărul de rânduri · orchestratorul pornit
înaintea Postgres nu moare (retry cu deadline, degradare grațioasă) ·
status.json rămâne view derivat pentru widget.
"""
import datetime
import json
import sqlite3

import pytest

import orchestrator
import pg_store


# ── Degradare grațioasă fără PG ───────────────────────────────────────────────

def test_unconfigured_layer_raises_pg_unavailable():
    with pytest.raises(pg_store.PgUnavailable):
        pg_store.execute("SELECT 1")


def test_connect_with_retry_dead_server_returns_false():
    """Criteriul WP-PG: pornit înaintea Postgres → nu moare, doar raportează."""
    pg_store.configure("host=127.0.0.1 port=59999 dbname=nope connect_timeout=1")
    assert pg_store.connect_with_retry(deadline_s=0.5, interval_s=0.2) is False


async def test_startup_postgres_survives_dead_server(monkeypatch):
    """Handlerul de startup nu aruncă niciodată din cauza unui PG absent."""
    monkeypatch.setattr(orchestrator, "PG_DSN",
                        "host=127.0.0.1 port=59999 dbname=nope connect_timeout=1")
    monkeypatch.setattr(orchestrator, "PG_STARTUP_RETRY_S", 0.5)
    notified = []
    monkeypatch.setattr(orchestrator, "_notify", lambda *a, **k: notified.append(a))
    await orchestrator.startup_postgres()   # nu ridică
    assert notified                          # dar anunță degradarea


# ── Schema + operații de bază ─────────────────────────────────────────────────

def test_ensure_schema_idempotent(pg):
    pg_store.ensure_schema()
    pg_store.ensure_schema()   # a doua oară nu crapă (IF NOT EXISTS)
    assert pg_store.fetchone("SELECT COUNT(*) FROM usage")[0] == 0


def test_transaction_rolls_back_on_error(pg):
    with pytest.raises(RuntimeError):
        with pg_store.transaction() as conn:
            conn.execute("INSERT INTO missions (id, status, created_at) VALUES ('m1', 'draft', 'x')")
            raise RuntimeError("boom")
    assert pg_store.fetchone("SELECT COUNT(*) FROM missions")[0] == 0


# ── Migrarea cutover ──────────────────────────────────────────────────────────

def _legacy_sqlite(tmp_path):
    """chat_history.db minimal cu date legacy în tabelele migrate."""
    db_dir = tmp_path / "cache_db"
    db_dir.mkdir()
    conn = sqlite3.connect(str(db_dir / "chat_history.db"))
    conn.execute("CREATE TABLE usage (id INTEGER PRIMARY KEY, ts TEXT, tier INTEGER, "
                 "model TEXT, cloud INTEGER, agent TEXT, duration_ms INTEGER, preview TEXT)")
    conn.execute("INSERT INTO usage (ts, tier, model, cloud, agent, duration_ms, preview) "
                 "VALUES ('2026-07-01T10:00:00', 3, 'haiku', 1, NULL, 100, 'x')")
    conn.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, kind TEXT, session_id TEXT, "
                 "channel TEXT, input TEXT, tier INTEGER, model TEXT, routing_method TEXT, "
                 "routing_confidence REAL, routing_neighbor TEXT, cache_hit INTEGER, "
                 "budget_state TEXT, status TEXT, cost_usd REAL, duration_ms INTEGER, "
                 "created_at TEXT, finished_at TEXT)")
    conn.execute("INSERT INTO runs (id, kind, status, created_at) "
                 "VALUES ('r1', 'chat', 'done', '2026-07-01T10:00:00')")
    conn.execute("INSERT INTO runs (id, kind, status, created_at) "
                 "VALUES ('r2', 'task', 'failed', '2026-07-02T10:00:00')")
    conn.commit()
    conn.close()
    return db_dir


def test_migrate_copies_and_is_idempotent(pg, monkeypatch, tmp_path):
    db_dir = _legacy_sqlite(tmp_path)
    monkeypatch.setattr(orchestrator, "CACHE_DB_PATH", db_dir)
    # scheduled_tasks.json + status.json legacy
    sched = tmp_path / "scheduled_tasks.json"
    sched.write_text(json.dumps([
        {"id": "t1", "cron": "0 8 * * *", "message": "briefing", "enabled": True},
    ]), encoding="utf-8")
    monkeypatch.setattr(orchestrator, "SCHEDULED_TASKS_FILE", sched)
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"active": False, "tier": 3, "model": "haiku"}),
                      encoding="utf-8")
    monkeypatch.setattr(orchestrator, "STATUS_FILE", status)

    orchestrator._migrate_state_to_pg()

    assert pg_store.fetchone("SELECT COUNT(*) FROM usage")[0] == 1
    assert pg_store.fetchone("SELECT COUNT(*) FROM runs")[0] == 2
    assert pg_store.fetchone("SELECT cron FROM scheduled_tasks WHERE id='t1'")[0] == "0 8 * * *"
    assert pg_store.fetchone("SELECT tier FROM status WHERE id=1")[0] == 3

    # Idempotență: a doua rulare NU dublează (tabelele PG au deja rânduri → skip)
    orchestrator._migrate_state_to_pg()
    assert pg_store.fetchone("SELECT COUNT(*) FROM usage")[0] == 1
    assert pg_store.fetchone("SELECT COUNT(*) FROM runs")[0] == 2
    assert pg_store.fetchone("SELECT COUNT(*) FROM scheduled_tasks")[0] == 1


def test_migrate_fresh_install_no_sources(pg, monkeypatch, tmp_path):
    """Instalare nouă: fără chat_history.db, fără JSON-uri → migrarea e no-op curat."""
    monkeypatch.setattr(orchestrator, "CACHE_DB_PATH", tmp_path / "cache_db")
    monkeypatch.setattr(orchestrator, "SCHEDULED_TASKS_FILE", tmp_path / "nope.json")
    monkeypatch.setattr(orchestrator, "STATUS_FILE", tmp_path / "nope2.json")
    orchestrator._migrate_state_to_pg()
    assert pg_store.fetchone("SELECT COUNT(*) FROM usage")[0] == 0


# ── Scheduled tasks pe PG ─────────────────────────────────────────────────────

def test_persist_new_task_roundtrip(pg, monkeypatch):
    monkeypatch.setattr(orchestrator, "_scheduler", None)
    task = orchestrator._persist_new_task("0 9 * * *", "salut", tier_override=None)
    tasks = orchestrator._scheduled_tasks_all()
    assert [t["id"] for t in tasks] == [task["id"]]
    assert tasks[0]["cron"] == "0 9 * * *" and tasks[0]["enabled"] is True


def test_persist_new_task_invalid_cron_raises(pg, monkeypatch):
    monkeypatch.setattr(orchestrator, "_scheduler", None)
    with pytest.raises(ValueError):
        orchestrator._persist_new_task("nu e cron", "x")
    assert orchestrator._scheduled_tasks_all() == []   # nimic persistat


# ── Status: sursa de adevăr PG + view derivat pe disc ─────────────────────────

def test_write_status_dual_writes(pg, monkeypatch, tmp_path):
    status_file = tmp_path / "status.json"
    monkeypatch.setattr(orchestrator, "STATUS_FILE", status_file)

    orchestrator._write_status(True, 5, "sonnet", "task de test", False)

    row = orchestrator._status_row()
    assert row["active"] is True and row["tier"] == 5 and row["model"] == "sonnet"
    # view-ul derivat pentru widget există și e JSON valid, fără fișier .tmp rămas
    view = json.loads(status_file.read_text(encoding="utf-8"))
    assert view["tier"] == 5 and view["active"] is True
    assert not (tmp_path / "status.json.tmp").exists()


def test_write_status_idle_logs_duration(pg, monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "STATUS_FILE", tmp_path / "status.json")
    started = (datetime.datetime.now() - datetime.timedelta(seconds=2)).isoformat()
    orchestrator._status_upsert({"active": True, "tier": 3, "model": "haiku",
                                 "task_preview": "lucru", "obsidian": False,
                                 "started_at": started, "last_updated": started})
    orchestrator._write_status_idle(3, "haiku")

    row = orchestrator._status_row()
    assert row["active"] is False and row["started_at"] is None
    # _log_usage a înregistrat rularea cu durata calculată din started_at
    usage = pg_store.fetchone("SELECT duration_ms, preview FROM usage ORDER BY id DESC LIMIT 1")
    assert usage is not None and usage[0] >= 2000 and usage[1] == "lucru"


async def test_retry_reads_tier_from_pg(pg, monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "STATUS_FILE", tmp_path / "status.json")
    orchestrator._write_status(False, 3, "haiku", None, False)
    tier, forced, conf, method = await orchestrator.decide_tier("!retry")
    assert (tier, forced) == (5, True)   # 3+1=4 → clamp la 5 (Gemini retras)
