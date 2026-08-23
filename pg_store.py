"""pg_store.py — stratul de acces PostgreSQL pentru starea partajată + telemetrie (WP-PG).

Ce trăiește aici: `usage`, `runs`/`run_events`, `missions`/`mission_wps`, `job_runs`,
`scheduled_tasks`, `status`. Chat history (`messages`) + ChromaDB RĂMÂN în `cache_db/`
(SQLite) — migrarea lor nu stinge nicio durere (spec WP-PG pasul 2).

Decizii de design:

- **psycopg sync, o conexiune per proces + RLock** — consistent cu patternul sqlite3
  `check_same_thread=False` de dinainte; pool async doar dacă apar blocaje măsurate.
- **autocommit** — fiecare operație e o tranzacție scurtă (echivalentul commit-per-op
  al vechiului cod); tranzacții explicite (`transaction()`) doar unde atomicitatea
  contează: migrarea de cutover și insertul misiune + WP-uri.
- **Timestamps ca TEXT ISO-8601** — compatibile byte-cu-byte cu datele migrate din
  SQLite; ISO-8601 sortează lexicografic == cronologic, deci toate filtrările
  `ts >= ? AND ts < ?` rămân corecte. Tipizarea strictă (TIMESTAMPTZ) se face la
  WP-ETL în stratul de staging, nu la cutover — minimizează riscul migrării.
- **Fără FOREIGN KEY run_events → runs** — telemetria e best-effort pe hot path
  (scrierile nu au voie să pice chat-ul); un FK ar transforma un rând orfan într-o
  eroare de scriere. Validarea integrității e treabă de ETL (WP-ETL), nu de ingest.
- **Reconectare leneșă** — o operație care prinde conexiunea moartă (restart Postgres)
  reconectează O dată și reîncearcă; dacă și asta pică, excepția urcă la apelant,
  care decide (căile best-effort o înghit deja, căile de stare o raportează).
"""
from __future__ import annotations

import logging
import threading
import time

import psycopg

logger = logging.getLogger("kage.pg")

_lock = threading.RLock()
_conn: psycopg.Connection | None = None
_dsn: str | None = None


class PgUnavailable(RuntimeError):
    """Postgres nu e configurat sau nu poate fi contactat."""


def configure(dsn: str | None) -> None:
    """Setează DSN-ul (ex. "dbname=kage"). None/"" dezactivează stratul PG."""
    global _dsn
    _dsn = dsn or None


def connect() -> bool:
    """(Re)deschide conexiunea. Nu ridică — întoarce False la eșec (apelantul decide
    dacă reîncearcă; startup-ul orchestratorului face retry cu deadline)."""
    global _conn
    if _dsn is None:
        return False
    with _lock:
        try:
            if _conn is not None and not _conn.closed:
                _conn.close()
        except Exception:
            pass
        try:
            _conn = psycopg.connect(_dsn, autocommit=True, connect_timeout=5)
            return True
        except Exception as e:
            _conn = None
            logger.debug(f"[pg] connect eșuat: {e}")
            return False


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


def available() -> bool:
    return _conn is not None and not _conn.closed


def configured() -> bool:
    """True dacă există un DSN — folosit de guard-urile „fără DB → default"
    (echivalentul vechiului `if _db_conn is None`). Conexiunea propriu-zisă se
    face leneș, la prima operație, deci `configured()` NU implică `available()`."""
    return _dsn is not None


def _run(sql: str, params: tuple, fetch: str | None):
    """Execută sub lock, cu o singură reconectare la conexiune moartă."""
    if _dsn is None:
        raise PgUnavailable("postgres_dsn neconfigurat")
    with _lock:
        for attempt in (1, 2):
            if _conn is None or _conn.closed:
                if not connect():
                    raise PgUnavailable("Postgres indisponibil")
            try:
                cur = _conn.execute(sql, params)
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
                return cur.rowcount
            except (psycopg.OperationalError, psycopg.InterfaceError) as e:
                # Conexiune căzută (restart PG etc.) — reconectăm o dată și reîncercăm.
                if attempt == 2:
                    raise
                logger.warning(f"[pg] conexiune pierdută ({e}) — reconectez")
                if not connect():
                    raise PgUnavailable("Postgres indisponibil după reconectare")


def execute(sql: str, params: tuple = ()) -> int:
    return _run(sql, params, fetch=None)


def fetchone(sql: str, params: tuple = ()):
    return _run(sql, params, fetch="one")


def fetchall(sql: str, params: tuple = ()) -> list:
    return _run(sql, params, fetch="all")


class transaction:
    """Tranzacție explicită sub lock: `with pg_store.transaction() as conn:`.
    Folosită unde atomicitatea contează (migrare, insert misiune + WP-uri).
    Rollback automat la excepție (semantica psycopg `Connection.transaction`)."""

    def __enter__(self) -> psycopg.Connection:
        _lock.acquire()
        try:
            if _conn is None or _conn.closed:
                if not connect():
                    raise PgUnavailable("Postgres indisponibil")
            self._tx = _conn.transaction()
            self._tx.__enter__()
            return _conn
        except Exception:
            _lock.release()
            raise

    def __exit__(self, *exc) -> bool:
        try:
            return bool(self._tx.__exit__(*exc))
        finally:
            _lock.release()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id          BIGSERIAL PRIMARY KEY,
    ts          TEXT NOT NULL,
    tier        INTEGER,
    model       TEXT,
    cloud       INTEGER NOT NULL DEFAULT 0,
    agent       TEXT,
    duration_ms INTEGER,
    preview     TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);

CREATE TABLE IF NOT EXISTS runs (
    id                 TEXT PRIMARY KEY,
    kind               TEXT NOT NULL,
    session_id         TEXT,
    channel            TEXT,
    input              TEXT,
    tier               INTEGER,
    model              TEXT,
    routing_method     TEXT,
    routing_confidence REAL,
    routing_neighbor   TEXT,
    cache_hit          INTEGER DEFAULT 0,
    budget_state       TEXT,
    status             TEXT NOT NULL,
    cost_usd           REAL,
    duration_ms        INTEGER,
    created_at         TEXT NOT NULL,
    finished_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);

CREATE TABLE IF NOT EXISTS run_events (
    id      BIGSERIAL PRIMARY KEY,
    run_id  TEXT NOT NULL,
    ts      TEXT NOT NULL,
    type    TEXT NOT NULL,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id);

CREATE TABLE IF NOT EXISTS missions (
    id             TEXT PRIMARY KEY,
    slug           TEXT,
    title          TEXT,
    path           TEXT,
    cwd            TEXT,
    status         TEXT NOT NULL,
    current_idx    INTEGER DEFAULT 0,
    sdk_session_id TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS mission_wps (
    id          BIGSERIAL PRIMARY KEY,
    mission_id  TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    title       TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    detail      TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mission_wps ON mission_wps(mission_id);

CREATE TABLE IF NOT EXISTS job_runs (
    id          BIGSERIAL PRIMARY KEY,
    job_id      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_runs_open ON job_runs(job_id, finished_at);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id            TEXT PRIMARY KEY,
    cron          TEXT NOT NULL,
    message       TEXT NOT NULL,
    tier_override INTEGER,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS status (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    active       BOOLEAN NOT NULL DEFAULT FALSE,
    tier         INTEGER,
    model        TEXT,
    task_preview TEXT,
    obsidian     BOOLEAN DEFAULT FALSE,
    started_at   TEXT,
    last_updated TEXT
);
"""

# Tabelele deținute de acest strat — folosit de migrare și de fixture-ul de test.
TABLES = ("usage", "runs", "run_events", "missions", "mission_wps",
          "job_runs", "scheduled_tasks", "status")


def ensure_schema() -> None:
    """Creează schema (idempotent). Ridică dacă PG e indisponibil."""
    with _lock:
        if _conn is None or _conn.closed:
            if not connect():
                raise PgUnavailable("Postgres indisponibil")
        _conn.execute(_SCHEMA)


def connect_with_retry(deadline_s: float, interval_s: float = 2.0) -> bool:
    """Retry blocant până la deadline (launchd nu garantează ordinea de pornire —
    orchestratorul pornit înaintea Postgres NU moare, așteaptă). Doar la startup;
    după aceea reconectarea e leneșă, per operație."""
    end = time.monotonic() + max(0.0, deadline_s)
    while True:
        if connect():
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(interval_s)
