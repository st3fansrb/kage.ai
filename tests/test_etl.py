"""Teste pentru pipeline-ul de analytics WP-ETL (raw → staging → mart).

Acoperă criteriile de acceptare: backfill complet pe istoric · nightly idempotent
(2× aceeași zi = același rezultat) · fiecare rând de mart are dataset_snapshot_id ·
data-quality (future timestamp respins, duplicate absorbit, gap detectat, rând fără
available_time exclus din mart) · endpoint-ul întoarce seria zilnică.

Cer o instanță PostgreSQL de test (fixture-ul `pg` din conftest) — fără ea sar cu skip.
"""
import sqlite3

import pytest

import etl
import orchestrator
import pg_store


@pytest.fixture()
def etl_pg(pg):
    """pg_store pe DB-ul de test, cu schema ETL creată și mart-urile/staging golite."""
    etl.ensure_schema()
    pg_store.execute("TRUNCATE " + ", ".join(etl.TABLES) + " RESTART IDENTITY")
    return pg_store


def _add_run(rid, created_at, *, finished_at=None, tier=1, model="qwen3:8b",
             status="done", cost_usd=0.0, cache_hit=0, duration_ms=100):
    pg_store.execute(
        "INSERT INTO runs (id, kind, status, tier, model, cache_hit, cost_usd, "
        "duration_ms, created_at, finished_at) "
        "VALUES (%s, 'chat', %s, %s, %s, %s, %s, %s, %s, %s)",
        (rid, status, tier, model, cache_hit, cost_usd, duration_ms, created_at, finished_at))


def _usage_row(day, tier):
    return pg_store.fetchone(
        "SELECT runs_count, cloud_runs, cache_hits, cache_hit_rate, error_count, "
        "total_cost_usd, dataset_snapshot_id FROM mart_daily_usage "
        "WHERE day = %s AND tier = %s", (day, tier))


# ── Agregare de bază + dataset_snapshot_id ────────────────────────────────────

def test_run_day_builds_usage_mart(etl_pg):
    _add_run("r1", "2026-07-10T09:00:00", finished_at="2026-07-10T09:01:00",
             tier=1, cost_usd=0.0, cache_hit=0)
    _add_run("r2", "2026-07-10T10:00:00", finished_at="2026-07-10T10:02:00",
             tier=5, model="sonnet", cost_usd=0.03, cache_hit=1)
    _add_run("r3", "2026-07-10T11:00:00", finished_at="2026-07-10T11:00:30",
             tier=5, model="sonnet", cost_usd=0.02, status="failed")

    summary = etl.run_day("2026-07-10")
    assert summary["staged_runs"] == 3

    t5 = _usage_row("2026-07-10", 5)
    runs_count, cloud_runs, cache_hits, hit_rate, error_count, cost, snap = t5
    assert runs_count == 2
    assert cloud_runs == 2            # tier >= 3 = cloud
    assert cache_hits == 1
    assert hit_rate == pytest.approx(0.5)
    assert error_count == 1           # r3 = failed
    assert cost == pytest.approx(0.05)
    assert snap                       # dataset_snapshot_id ne-nul

    # Fiecare rând de mart poartă dataset_snapshot_id (criteriu de acceptare).
    nulls = pg_store.fetchone(
        "SELECT count(*) FROM mart_daily_usage WHERE dataset_snapshot_id IS NULL")[0]
    assert nulls == 0


def test_idempotent_rerun_same_day(etl_pg):
    _add_run("r1", "2026-07-10T09:00:00", finished_at="2026-07-10T09:01:00", tier=2)
    _add_run("r2", "2026-07-10T10:00:00", finished_at="2026-07-10T10:01:00",
             tier=5, model="sonnet", cost_usd=0.03)

    etl.run_day("2026-07-10")
    first = pg_store.fetchall(
        "SELECT day, tier, model, runs_count, cloud_runs, cache_hits, error_count, "
        "total_cost_usd FROM mart_daily_usage ORDER BY tier")

    etl.run_day("2026-07-10")          # a doua rulare pe aceeași zi
    second = pg_store.fetchall(
        "SELECT day, tier, model, runs_count, cloud_runs, cache_hits, error_count, "
        "total_cost_usd FROM mart_daily_usage ORDER BY tier")

    assert first == second             # măsurile identice
    # delete-and-rewrite: nu se acumulează rânduri duplicate
    assert pg_store.fetchone("SELECT count(*) FROM mart_daily_usage")[0] == 2


# ── Data-quality pe lineage ───────────────────────────────────────────────────

def test_future_timestamp_rejected(etl_pg):
    _add_run("ok", "2026-07-10T09:00:00", finished_at="2026-07-10T09:01:00", tier=1)
    _add_run("future", "2099-01-01T00:00:00", finished_at="2099-01-01T00:01:00", tier=1)

    # Ambele „create" în ziua lor; procesăm ziua din viitor → respinsă.
    summary = etl.run_day("2099-01-01")
    assert summary["rejected"] == 1
    assert summary["staged_runs"] == 0
    assert pg_store.fetchone(
        "SELECT count(*) FROM stg_runs WHERE run_id = 'future'")[0] == 0
    q = etl.quality_report("2099-01-01")
    assert q["future_events"] == 0     # nimic din viitor n-a ajuns în staging


def test_duplicate_absorbed_by_staging(etl_pg):
    _add_run("dup", "2026-07-10T09:00:00", finished_at="2026-07-10T09:01:00", tier=1)
    etl.run_day("2026-07-10")
    etl.run_day("2026-07-10")          # re-staging același rând raw
    assert pg_store.fetchone(
        "SELECT count(*) FROM stg_runs WHERE run_id = 'dup'")[0] == 1


def test_unavailable_run_excluded_from_mart(etl_pg):
    # Run neterminat: available_time (finished_at) lipsă → staged, dar nu în mart.
    _add_run("open", "2026-07-10T09:00:00", finished_at=None, tier=1, status="running")
    etl.run_day("2026-07-10")
    assert pg_store.fetchone("SELECT count(*) FROM stg_runs WHERE run_id = 'open'")[0] == 1
    assert pg_store.fetchone("SELECT count(*) FROM mart_daily_usage")[0] == 0
    assert etl.quality_report("2026-07-10")["unavailable_events"] == 1


def test_find_gaps_detects_missing_days():
    assert etl.find_gaps(["2026-07-10", "2026-07-13"]) == ["2026-07-11", "2026-07-12"]
    assert etl.find_gaps(["2026-07-10", "2026-07-11"]) == []
    assert etl.find_gaps(["2026-07-10"]) == []


# ── Misiuni ───────────────────────────────────────────────────────────────────

def test_mission_stats_mart(etl_pg):
    pg_store.execute(
        "INSERT INTO missions (id, status, created_at) VALUES "
        "('m1', 'done', '2026-07-10T08:00:00'), "
        "('m2', 'failed', '2026-07-10T09:00:00'), "
        "('m3', 'running', '2026-07-10T10:00:00')")
    pg_store.execute(
        "INSERT INTO mission_wps (mission_id, idx, status, finished_at) VALUES "
        "('m1', 0, 'done', '2026-07-10T08:30:00'), "
        "('m1', 1, 'done', '2026-07-10T08:45:00'), "
        "('m2', 0, 'pending', NULL)")
    etl.run_day("2026-07-10")
    row = pg_store.fetchone(
        "SELECT missions_created, missions_completed, missions_failed, wps_done, "
        "dataset_snapshot_id FROM mart_mission_stats WHERE day = '2026-07-10'")
    assert row[:4] == (3, 1, 1, 2)
    assert row[4]                      # dataset_snapshot_id


# ── Trading (sursă SQLite separată) ───────────────────────────────────────────

def _make_trading_db(path):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE experiments (id INTEGER PRIMARY KEY, strategy TEXT);
        CREATE TABLE paper_trades (
            id INTEGER PRIMARY KEY, experiment_id INTEGER, pair TEXT, exit_ts TEXT,
            pnl REAL, status TEXT);
        CREATE TABLE equity_snapshots (
            id INTEGER PRIMARY KEY, source TEXT, equity REAL, recorded_at TEXT);
        """)
    con.execute("INSERT INTO experiments (id, strategy) VALUES (1, 'sample')")
    con.executemany(
        "INSERT INTO paper_trades (id, experiment_id, pair, exit_ts, pnl, status) VALUES (?,?,?,?,?,?)",
        [(1, 1, "BTC/USDT", "2026-07-10T12:00:00", 5.0, "closed"),
         (2, 1, "ETH/USDT", "2026-07-10T13:00:00", -2.0, "closed"),
         (3, 1, "SOL/USDT", None, None, "open")])          # deschisă → ignorată
    con.executemany(
        "INSERT INTO equity_snapshots (id, source, equity, recorded_at) VALUES (?,?,?,?)",
        [(1, "sample", 1000.0, "2026-07-10T09:00:00"),
         (2, "sample", 1003.0, "2026-07-10T18:00:00")])    # ultima = closing_equity
    con.commit()
    con.close()


def test_trading_daily_mart(etl_pg, tmp_path):
    db = tmp_path / "trading.db"
    _make_trading_db(str(db))
    etl.run_day("2026-07-10", trading_db_path=str(db))
    row = pg_store.fetchone(
        "SELECT source, closing_equity, trades_closed, gross_pnl, wins, losses, win_rate, "
        "dataset_snapshot_id FROM mart_trading_daily WHERE day = '2026-07-10'")
    assert row[0] == "sample"
    assert row[1] == pytest.approx(1003.0)   # ultimul snapshot
    assert row[2] == 2                        # doar cele închise
    assert row[3] == pytest.approx(3.0)       # 5 - 2
    assert row[4] == 1 and row[5] == 1
    assert row[6] == pytest.approx(0.5)
    assert row[7]


# ── Backfill pe tot istoricul ─────────────────────────────────────────────────

def test_backfill_covers_all_days(etl_pg):
    _add_run("a", "2026-07-08T09:00:00", finished_at="2026-07-08T09:01:00", tier=1)
    _add_run("b", "2026-07-10T09:00:00", finished_at="2026-07-10T09:01:00", tier=2)
    _add_run("c", "2026-07-10T10:00:00", finished_at="2026-07-10T10:01:00", tier=5,
             model="sonnet", cost_usd=0.04)

    days = etl.distinct_days(trading_db_path=None)
    assert days == ["2026-07-08", "2026-07-10"]

    result = etl.backfill(trading_db_path=None)
    assert result["days"] == 2
    assert result["range"] == ["2026-07-08", "2026-07-10"]
    assert pg_store.fetchone(
        "SELECT count(DISTINCT day) FROM mart_daily_usage")[0] == 2


# ── Raport zilnic + endpoint ──────────────────────────────────────────────────

def test_daily_report_shape(etl_pg):
    _add_run("r1", "2026-07-10T09:00:00", finished_at="2026-07-10T09:01:00", tier=5,
             model="sonnet", cost_usd=0.03, cache_hit=1)
    etl.run_day("2026-07-10")
    series = etl.daily_report(30)
    assert len(series) == 1
    row = series[0]
    assert row["day"] == "2026-07-10"
    assert row["runs"] == 1
    assert row["cloud_runs"] == 1
    assert row["total_cost_usd"] == pytest.approx(0.03)
    assert isinstance(row["total_cost_usd"], float)   # fără Decimal ne-serializabil


async def test_analytics_daily_endpoint(etl_pg):
    _add_run("r1", "2026-07-10T09:00:00", finished_at="2026-07-10T09:01:00", tier=2)
    etl.run_day("2026-07-10")
    resp = await orchestrator.analytics_daily(days=30)
    assert resp["count"] == 1
    assert resp["days"][0]["day"] == "2026-07-10"


async def test_analytics_daily_endpoint_no_pg(monkeypatch):
    """Fără PG configurat, endpoint-ul degradează grațios (nu ridică)."""
    monkeypatch.setattr(pg_store, "_dsn", None)
    resp = await orchestrator.analytics_daily(days=30)
    assert resp == {"days": [], "count": 0}
