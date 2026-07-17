"""etl.py — pipeline de analytics peste telemetria Kage (WP-ETL).

Rulează clasic **raw → staging → mart**, tot în SQL peste PostgreSQL (datele-s mici,
patternul contează — NU pandas). Sursele raw sunt tabelele WP-PG (`runs`, `missions`,
`mission_wps`) plus `trading.db` (SQLite, sursă separată — demonstrează ingest
multi-sursă). Ținta sunt trei mart-uri agregate pe zi, consumate de WP10 (+ G5).

Idempotență (criteriu de acceptare): totul se procesează **per zi**, cu
delete-and-rewrite pe partiția zilei — a rula agregarea de 2× pentru aceeași zi dă
exact același rezultat. Backfill-ul = bucla peste zilele distincte din raw.

Lineage point-in-time (Codex proposal T3, integrat aici): fiecare rând din staging
poartă `event_time` (când s-a produs), `available_time` (când a devenit vizibil
pipeline-ului) și `ingested_time` (când a intrat în staging); fiecare rând de mart
poartă `dataset_snapshot_id` (rulaarea de agregare care l-a produs, în `etl_snapshots`).
Suficient să răspundă „ce știa sistemul la momentul X" fără versionare completă de date.

Data-quality pe lineage: evenimentele din viitor (`event_time > ingested_time`) sunt
respinse la staging; rândurile fără `available_time` nu intră în mart-ul zilei;
duplicatele sunt absorbite de cheile primare din staging (`ON CONFLICT`).
"""
from __future__ import annotations

import datetime
import logging
import os
import sqlite3
import uuid
from decimal import Decimal

import pg_store

logger = logging.getLogger("kage.etl")


ETL_SCHEMA = """
-- Provenance: o linie per rulare de agregare (dataset_snapshot_id).
CREATE TABLE IF NOT EXISTS etl_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    day         TEXT,
    kind        TEXT NOT NULL,          -- 'nightly' | 'backfill'
    rows_in     INTEGER NOT NULL DEFAULT 0,
    rows_out    INTEGER NOT NULL DEFAULT 0,
    rejected    INTEGER NOT NULL DEFAULT 0
);

-- STAGING (lineage T3) — materializare tipizată a raw-ului, o linie per rând raw.
CREATE TABLE IF NOT EXISTS stg_runs (
    run_id         TEXT PRIMARY KEY,     -- idempotent pe cheia din raw
    day            TEXT NOT NULL,
    event_time     TIMESTAMPTZ NOT NULL, -- created_at (când s-a produs)
    available_time TIMESTAMPTZ,          -- finished_at (când a devenit vizibil)
    ingested_time  TIMESTAMPTZ NOT NULL, -- când a intrat în staging
    source         TEXT NOT NULL DEFAULT 'runs',
    tier           INTEGER,
    model          TEXT,
    cloud          INTEGER NOT NULL DEFAULT 0,
    cache_hit      INTEGER NOT NULL DEFAULT 0,
    status         TEXT,
    cost_usd       REAL,
    duration_ms    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_stg_runs_day ON stg_runs(day);

CREATE TABLE IF NOT EXISTS stg_trades (
    trade_key      TEXT PRIMARY KEY,     -- 'paper:<id>', idempotent
    day            TEXT NOT NULL,
    event_time     TIMESTAMPTZ NOT NULL, -- exit_ts (închiderea)
    available_time TIMESTAMPTZ,
    ingested_time  TIMESTAMPTZ NOT NULL,
    source         TEXT NOT NULL,
    pnl            REAL,
    pair           TEXT
);
CREATE INDEX IF NOT EXISTS idx_stg_trades_day ON stg_trades(day);

CREATE TABLE IF NOT EXISTS stg_equity (
    snap_key      TEXT PRIMARY KEY,      -- 'eq:<id>', idempotent
    day           TEXT NOT NULL,
    event_time    TIMESTAMPTZ NOT NULL,
    ingested_time TIMESTAMPTZ NOT NULL,
    source        TEXT NOT NULL,
    equity        REAL
);
CREATE INDEX IF NOT EXISTS idx_stg_equity_day ON stg_equity(day);

-- MARTS — agregate pe zi, fiecare rând ștampilat cu dataset_snapshot_id.
CREATE TABLE IF NOT EXISTS mart_daily_usage (
    day                 TEXT NOT NULL,
    tier                INTEGER,
    model               TEXT,
    runs_count          INTEGER NOT NULL DEFAULT 0,
    cloud_runs          INTEGER NOT NULL DEFAULT 0,
    cache_hits          INTEGER NOT NULL DEFAULT 0,
    cache_hit_rate      REAL,
    error_count         INTEGER NOT NULL DEFAULT 0,
    total_cost_usd      REAL NOT NULL DEFAULT 0,
    avg_duration_ms     REAL,
    dataset_snapshot_id TEXT NOT NULL,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mart_usage_day ON mart_daily_usage(day);

CREATE TABLE IF NOT EXISTS mart_mission_stats (
    day                 TEXT NOT NULL,
    missions_created    INTEGER NOT NULL DEFAULT 0,
    missions_completed  INTEGER NOT NULL DEFAULT 0,
    missions_failed     INTEGER NOT NULL DEFAULT 0,
    wps_done            INTEGER NOT NULL DEFAULT 0,
    dataset_snapshot_id TEXT NOT NULL,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mart_mission_day ON mart_mission_stats(day);

CREATE TABLE IF NOT EXISTS mart_trading_daily (
    day                 TEXT NOT NULL,
    source              TEXT NOT NULL,
    closing_equity      REAL,
    trades_closed       INTEGER NOT NULL DEFAULT 0,
    gross_pnl           REAL NOT NULL DEFAULT 0,
    wins                INTEGER NOT NULL DEFAULT 0,
    losses              INTEGER NOT NULL DEFAULT 0,
    win_rate            REAL,
    dataset_snapshot_id TEXT NOT NULL,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mart_trading_day ON mart_trading_daily(day);
"""

# Mart-urile deținute de acest strat — folosite de fixture-ul de test pentru TRUNCATE.
TABLES = ("etl_snapshots", "stg_runs", "stg_trades", "stg_equity",
          "mart_daily_usage", "mart_mission_stats", "mart_trading_daily")


def ensure_schema() -> None:
    """Creează schema ETL (idempotent). Ridică dacă PG e indisponibil."""
    pg_store.execute(ETL_SCHEMA)


def _default_trading_db() -> str | None:
    """Calea `trading.db` (sursă SQLite separată). None dacă modulul lipsește."""
    try:
        from trading.ledger import DEFAULT_DB_PATH
        return str(DEFAULT_DB_PATH)
    except Exception:
        return None


# ── STAGING ──────────────────────────────────────────────────────────────────

def _stage_runs(day: str, ingested: str) -> tuple[int, int]:
    """Materializează `runs` din ziua `day` în `stg_runs` cu lineage. Respinge
    evenimentele din viitor (event_time > ingested_time). Întoarce (staged, rejected)."""
    rejected = pg_store.fetchone(
        "SELECT count(*) FROM runs WHERE left(created_at, 10) = %s "
        "AND created_at IS NOT NULL AND created_at::timestamptz > %s::timestamptz",
        (day, ingested))[0]
    pg_store.execute(
        """
        INSERT INTO stg_runs (run_id, day, event_time, available_time, ingested_time,
                              source, tier, model, cloud, cache_hit, status,
                              cost_usd, duration_ms)
        SELECT id, left(created_at, 10), created_at::timestamptz,
               finished_at::timestamptz, %s::timestamptz, 'runs',
               tier, model,
               CASE WHEN tier >= 3 THEN 1 ELSE 0 END,
               COALESCE(cache_hit, 0), status, cost_usd, duration_ms
        FROM runs
        WHERE left(created_at, 10) = %s
          AND created_at IS NOT NULL
          AND created_at::timestamptz <= %s::timestamptz
        ON CONFLICT (run_id) DO UPDATE SET
          available_time = EXCLUDED.available_time,
          status         = EXCLUDED.status,
          cost_usd       = EXCLUDED.cost_usd,
          duration_ms    = EXCLUDED.duration_ms,
          cache_hit      = EXCLUDED.cache_hit,
          tier           = EXCLUDED.tier,
          model          = EXCLUDED.model,
          cloud          = EXCLUDED.cloud
        """,
        (ingested, day, ingested))
    staged = pg_store.fetchone("SELECT count(*) FROM stg_runs WHERE day = %s", (day,))[0]
    return staged, rejected


def _stage_trading(day: str, ingested: str, db_path: str | None) -> tuple[int, int]:
    """Ingestă tranzacțiile paper închise + snapshot-urile de equity din `trading.db`
    (SQLite) pentru ziua `day`. Degradare grațioasă dacă DB-ul lipsește."""
    if not db_path or not os.path.exists(db_path):
        return 0, 0
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        trades = con.execute(
            "SELECT t.id AS id, t.exit_ts AS exit_ts, t.pnl AS pnl, t.pair AS pair, "
            "       COALESCE(e.strategy, 'paper') AS source "
            "FROM paper_trades t LEFT JOIN experiments e ON e.id = t.experiment_id "
            "WHERE t.status = 'closed' AND t.exit_ts IS NOT NULL "
            "AND substr(t.exit_ts, 1, 10) = ?", (day,)).fetchall()
        equities = con.execute(
            "SELECT id, source, equity, recorded_at FROM equity_snapshots "
            "WHERE substr(recorded_at, 1, 10) = ?", (day,)).fetchall()
    except sqlite3.Error as e:
        logger.warning(f"[etl] citirea trading.db a eșuat: {e}")
        return 0, 0
    finally:
        con.close()
    for t in trades:
        pg_store.execute(
            "INSERT INTO stg_trades (trade_key, day, event_time, available_time, "
            "ingested_time, source, pnl, pair) "
            "VALUES (%s, %s, %s::timestamptz, %s::timestamptz, %s::timestamptz, %s, %s, %s) "
            "ON CONFLICT (trade_key) DO UPDATE SET pnl = EXCLUDED.pnl, "
            "available_time = EXCLUDED.available_time",
            (f"paper:{t['id']}", day, t["exit_ts"], t["exit_ts"], ingested,
             t["source"], t["pnl"], t["pair"]))
    for e in equities:
        pg_store.execute(
            "INSERT INTO stg_equity (snap_key, day, event_time, ingested_time, source, equity) "
            "VALUES (%s, %s, %s::timestamptz, %s::timestamptz, %s, %s) "
            "ON CONFLICT (snap_key) DO UPDATE SET equity = EXCLUDED.equity",
            (f"eq:{e['id']}", day, e["recorded_at"], ingested, e["source"], e["equity"]))
    return len(trades), len(equities)


# ── MARTS ────────────────────────────────────────────────────────────────────

def _mart_daily_usage(day: str, snap: str) -> None:
    pg_store.execute("DELETE FROM mart_daily_usage WHERE day = %s", (day,))
    pg_store.execute(
        """
        INSERT INTO mart_daily_usage (day, tier, model, runs_count, cloud_runs,
            cache_hits, cache_hit_rate, error_count, total_cost_usd, avg_duration_ms,
            dataset_snapshot_id)
        SELECT day, tier, model,
               count(*),
               COALESCE(sum(cloud), 0),
               COALESCE(sum(cache_hit), 0),
               CASE WHEN count(*) > 0 THEN sum(cache_hit)::real / count(*) END,
               COALESCE(sum(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0),
               COALESCE(sum(cost_usd), 0),
               avg(duration_ms),
               %s
        FROM stg_runs
        WHERE day = %s AND available_time IS NOT NULL
        GROUP BY day, tier, model
        """,
        (snap, day))


def _mart_mission_stats(day: str, snap: str) -> None:
    pg_store.execute("DELETE FROM mart_mission_stats WHERE day = %s", (day,))
    pg_store.execute(
        """
        INSERT INTO mart_mission_stats (day, missions_created, missions_completed,
            missions_failed, wps_done, dataset_snapshot_id)
        SELECT %s,
          (SELECT count(*) FROM missions WHERE left(created_at, 10) = %s),
          (SELECT count(*) FROM missions WHERE left(created_at, 10) = %s AND status = 'done'),
          (SELECT count(*) FROM missions WHERE left(created_at, 10) = %s AND status = 'failed'),
          (SELECT count(*) FROM mission_wps WHERE finished_at IS NOT NULL
                 AND left(finished_at, 10) = %s AND status = 'done'),
          %s
        WHERE (SELECT count(*) FROM missions WHERE left(created_at, 10) = %s) > 0
           OR (SELECT count(*) FROM mission_wps WHERE finished_at IS NOT NULL
                 AND left(finished_at, 10) = %s AND status = 'done') > 0
        """,
        (day, day, day, day, day, snap, day, day))


def _mart_trading_daily(day: str, snap: str) -> None:
    pg_store.execute("DELETE FROM mart_trading_daily WHERE day = %s", (day,))
    pg_store.execute(
        """
        INSERT INTO mart_trading_daily (day, source, closing_equity, trades_closed,
            gross_pnl, wins, losses, win_rate, dataset_snapshot_id)
        SELECT d.day, d.source,
               eq.closing_equity,
               COALESCE(tr.trades_closed, 0),
               COALESCE(tr.gross_pnl, 0),
               COALESCE(tr.wins, 0),
               COALESCE(tr.losses, 0),
               CASE WHEN COALESCE(tr.trades_closed, 0) > 0
                    THEN tr.wins::real / tr.trades_closed END,
               %s
        FROM (
            SELECT day, source FROM stg_trades WHERE day = %s
            UNION
            SELECT day, source FROM stg_equity WHERE day = %s
        ) d
        LEFT JOIN (
            SELECT day, source, count(*) AS trades_closed, sum(pnl) AS gross_pnl,
                   sum(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                   sum(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) AS losses
            FROM stg_trades WHERE day = %s GROUP BY day, source
        ) tr ON tr.day = d.day AND tr.source = d.source
        LEFT JOIN (
            SELECT DISTINCT ON (day, source) day, source, equity AS closing_equity
            FROM stg_equity WHERE day = %s
            ORDER BY day, source, event_time DESC
        ) eq ON eq.day = d.day AND eq.source = d.source
        """,
        (snap, day, day, day, day))


# ── ORCHESTRARE ──────────────────────────────────────────────────────────────

def run_day(day: str, kind: str = "nightly", trading_db_path: str | None = None) -> dict:
    """Rulează pipeline-ul complet pentru o singură zi (idempotent). Întoarce sumarul."""
    ensure_schema()
    if trading_db_path is None:
        trading_db_path = _default_trading_db()
    snap = uuid.uuid4().hex
    ingested = datetime.datetime.now().isoformat()

    staged_runs, rejected = _stage_runs(day, ingested)
    tr_trades, tr_equity = _stage_trading(day, ingested, trading_db_path)

    _mart_daily_usage(day, snap)
    _mart_mission_stats(day, snap)
    _mart_trading_daily(day, snap)

    rows_out = sum(
        pg_store.fetchone(f"SELECT count(*) FROM {t} WHERE dataset_snapshot_id = %s", (snap,))[0]
        for t in ("mart_daily_usage", "mart_mission_stats", "mart_trading_daily"))
    rows_in = staged_runs + tr_trades + tr_equity
    pg_store.execute(
        "INSERT INTO etl_snapshots (snapshot_id, day, kind, rows_in, rows_out, rejected) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (snap, day, kind, rows_in, rows_out, rejected))
    return {"snapshot_id": snap, "day": day, "rows_in": rows_in, "rows_out": rows_out,
            "rejected": rejected, "staged_runs": staged_runs,
            "trades": tr_trades, "equity_points": tr_equity}


def distinct_days(trading_db_path: str | None = None) -> list[str]:
    """Toate zilele cu date în vreo sursă raw (pentru backfill)."""
    days: set[str] = set()
    for sql in (
        "SELECT DISTINCT left(created_at, 10) FROM runs WHERE created_at IS NOT NULL",
        "SELECT DISTINCT left(created_at, 10) FROM missions WHERE created_at IS NOT NULL",
        "SELECT DISTINCT left(finished_at, 10) FROM mission_wps WHERE finished_at IS NOT NULL",
    ):
        days.update(r[0] for r in pg_store.fetchall(sql) if r[0])

    if trading_db_path is None:
        trading_db_path = _default_trading_db()
    if trading_db_path and os.path.exists(trading_db_path):
        con = sqlite3.connect(trading_db_path)
        try:
            for sql in (
                "SELECT DISTINCT substr(exit_ts, 1, 10) FROM paper_trades WHERE exit_ts IS NOT NULL",
                "SELECT DISTINCT substr(recorded_at, 1, 10) FROM equity_snapshots",
            ):
                days.update(r[0] for r in con.execute(sql).fetchall() if r[0])
        except sqlite3.Error as e:
            logger.warning(f"[etl] distinct_days pe trading.db a eșuat: {e}")
        finally:
            con.close()
    return sorted(days)


def backfill(trading_db_path: str | None = None) -> dict:
    """Rulează pipeline-ul pe tot istoricul, zi cu zi (idempotent)."""
    ensure_schema()
    if trading_db_path is None:
        trading_db_path = _default_trading_db()
    days = distinct_days(trading_db_path)
    for d in days:
        run_day(d, kind="backfill", trading_db_path=trading_db_path)
    return {"days": len(days), "range": [days[0], days[-1]] if days else None}


# ── DATA-QUALITY & RAPORT ────────────────────────────────────────────────────

def find_gaps(days) -> list[str]:
    """Zilele calendaristice lipsă între prima și ultima zi prezentă (detecție de gap)."""
    ds = sorted(set(days))
    if len(ds) < 2:
        return []
    start = datetime.date.fromisoformat(ds[0])
    end = datetime.date.fromisoformat(ds[-1])
    present = set(ds)
    gaps, cur = [], start
    while cur <= end:
        s = cur.isoformat()
        if s not in present:
            gaps.append(s)
        cur += datetime.timedelta(days=1)
    return gaps


def quality_report(day: str) -> dict:
    """Verificări de data-quality pe staging-ul unei zile."""
    future = pg_store.fetchone(
        "SELECT count(*) FROM stg_runs WHERE day = %s AND event_time > ingested_time",
        (day,))[0]
    unavailable = pg_store.fetchone(
        "SELECT count(*) FROM stg_runs WHERE day = %s AND available_time IS NULL",
        (day,))[0]
    return {"day": day, "future_events": int(future), "unavailable_events": int(unavailable)}


def _num(x):
    if isinstance(x, Decimal):
        return float(x)
    return x


def daily_report(days: int = 30) -> list[dict]:
    """Seria zilnică rulată de mart-uri (cea mai recentă zi prima). Consumat de /analytics/daily."""
    rows = pg_store.fetchall(
        """
        WITH u AS (
          SELECT day, sum(runs_count) AS runs, sum(cloud_runs) AS cloud_runs,
                 sum(cache_hits) AS cache_hits, sum(total_cost_usd) AS cost,
                 CASE WHEN sum(runs_count) > 0
                      THEN sum(cache_hits)::real / sum(runs_count) END AS hit_rate
          FROM mart_daily_usage GROUP BY day
        ),
        t AS (
          SELECT day, sum(trades_closed) AS trades, sum(gross_pnl) AS pnl,
                 sum(closing_equity) AS equity
          FROM mart_trading_daily GROUP BY day
        )
        SELECT d.day, u.runs, u.cloud_runs, u.cache_hits, u.hit_rate, u.cost,
               m.missions_created, m.missions_completed, m.missions_failed, m.wps_done,
               t.trades, t.pnl, t.equity
        FROM (
            SELECT day FROM u UNION SELECT day FROM mart_mission_stats UNION SELECT day FROM t
        ) d
        LEFT JOIN u ON u.day = d.day
        LEFT JOIN mart_mission_stats m ON m.day = d.day
        LEFT JOIN t ON t.day = d.day
        ORDER BY d.day DESC
        LIMIT %s
        """, (max(1, int(days)),))
    keys = ["day", "runs", "cloud_runs", "cache_hits", "cache_hit_rate", "total_cost_usd",
            "missions_created", "missions_completed", "missions_failed", "wps_done",
            "trades_closed", "gross_pnl", "closing_equity"]
    return [{k: _num(v) for k, v in zip(keys, r)} for r in rows]


if __name__ == "__main__":  # CLI: backfill istoric / o zi / raport
    import json
    import sys

    pg_store.configure(os.environ.get("KAGE_PG_DSN", "dbname=kage"))
    if not pg_store.connect():
        print("Postgres indisponibil (setează KAGE_PG_DSN)", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backfill"
    if cmd == "backfill":
        print(json.dumps(backfill(), indent=2))
    elif cmd == "day" and len(sys.argv) > 2:
        print(json.dumps(run_day(sys.argv[2]), indent=2))
    elif cmd == "report":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        print(json.dumps(daily_report(n), indent=2, default=str))
    else:
        print("Utilizare: python -m etl [backfill | day <YYYY-MM-DD> | report [N]]",
              file=sys.stderr)
        sys.exit(2)
