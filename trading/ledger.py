"""Ledger SQLite pentru laboratorul de trading (WP-T, faza T1).

`trading.db` ține starea și metricile experimentelor: `experiments` (o rulare de
backtest/strategie cu params + metrici), `paper_trades` (tranzacții VIRTUALE — niciodată
reale) și `agent_status` (heartbeat-ul daemonilor, pentru supervizarea Kage).

Self-contained: fără dependințe de `orchestrator.py`, ca daemonii separați să-l poată
folosi direct. Conexiune per-instanță, `check_same_thread=False` pentru procese cu
threaduri (freqtrade). WAL pentru citiri concurente din Mission Control (read-only).

Garanție de siguranță (T1): acest modul NU are nicio cale de a plasa ordine reale.
`PAPER_ONLY` e True și imuabil; `record_paper_trade` scrie doar rânduri virtuale.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Rădăcina proiectului = părintele lui `trading/`. `trading.db` stă în `cache_db/`
# (backup-uit zilnic cu restul datelor live — vezi _backup_cache_db).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = _PROJECT_ROOT / "cache_db" / "trading.db"

# Invariant de design: tot laboratorul e paper-only. Nu există flag de dezactivare
# în cod — promovarea pe bani reali e o decizie manuală, în afara acestui sistem.
PAPER_ONLY: bool = True

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy     TEXT NOT NULL,
    exchange     TEXT NOT NULL DEFAULT 'binance',
    pair         TEXT NOT NULL,
    timeframe    TEXT NOT NULL,
    params       TEXT NOT NULL DEFAULT '{}',      -- JSON: hiperparametri
    metrics      TEXT NOT NULL DEFAULT '{}',      -- JSON: profit, sharpe, max_drawdown, trades…
    backtest_start TEXT,
    backtest_end   TEXT,
    oos_passed   INTEGER NOT NULL DEFAULT 0,       -- 1 doar după validare out-of-sample
    status       TEXT NOT NULL DEFAULT 'backtest', -- backtest | paper | archived | failed
    mission_id   TEXT,                             -- misiunea WP11 care a produs-o (bucla nocturnă)
    notes        TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exp_strategy ON experiments(strategy);
CREATE INDEX IF NOT EXISTS idx_exp_created  ON experiments(created_at);

CREATE TABLE IF NOT EXISTS paper_trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER,
    pair        TEXT NOT NULL,
    side        TEXT NOT NULL,                     -- long | short
    entry_ts    TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_ts     TEXT,
    exit_price  REAL,
    amount      REAL NOT NULL,
    fee         REAL NOT NULL DEFAULT 0.0,         -- modelat mereu (anti-overfitting)
    pnl         REAL,                              -- profit/loss VIRTUAL
    status      TEXT NOT NULL DEFAULT 'open',      -- open | closed
    created_at  TEXT NOT NULL,
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);
CREATE INDEX IF NOT EXISTS idx_trade_exp ON paper_trades(experiment_id);

CREATE TABLE IF NOT EXISTS agent_status (
    agent          TEXT PRIMARY KEY,               -- ex. 'crypto-freqtrade'
    status         TEXT NOT NULL DEFAULT 'stopped', -- running | stopped | error
    pid            INTEGER,
    last_heartbeat TEXT,
    message        TEXT,
    updated_at     TEXT NOT NULL
);
"""


def _now() -> str:
    """Timestamp ISO-8601 (UTC-naiv local, ca restul ledger-elor Kage)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


@dataclass
class Experiment:
    strategy: str
    pair: str
    timeframe: str
    exchange: str = "binance"
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    backtest_start: Optional[str] = None
    backtest_end: Optional[str] = None
    oos_passed: bool = False
    status: str = "backtest"
    mission_id: Optional[str] = None
    notes: Optional[str] = None


class TradingLedger:
    """Acces la `trading.db`. Instanțiază-l per proces; e ieftin de deschis."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ── experiments ──────────────────────────────────────────────────────────
    def record_experiment(self, exp: Experiment) -> int:
        cur = self.conn.execute(
            "INSERT INTO experiments (strategy, exchange, pair, timeframe, params, metrics, "
            "backtest_start, backtest_end, oos_passed, status, mission_id, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                exp.strategy, exp.exchange, exp.pair, exp.timeframe,
                json.dumps(exp.params, ensure_ascii=False),
                json.dumps(exp.metrics, ensure_ascii=False),
                exp.backtest_start, exp.backtest_end,
                1 if exp.oos_passed else 0, exp.status, exp.mission_id, exp.notes, _now(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def mark_oos_passed(self, experiment_id: int, passed: bool = True) -> None:
        self.conn.execute(
            "UPDATE experiments SET oos_passed=? WHERE id=?",
            (1 if passed else 0, experiment_id),
        )
        self.conn.commit()

    def get_experiments(self, limit: int = 50, strategy: Optional[str] = None) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        if strategy:
            cur = self.conn.execute(
                "SELECT * FROM experiments WHERE strategy=? ORDER BY created_at DESC LIMIT ?",
                (strategy, limit),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM experiments ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [self._exp_row(r) for r in cur.fetchall()]

    @staticmethod
    def _exp_row(r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in ("params", "metrics"):
            try:
                d[k] = json.loads(d[k]) if d.get(k) else {}
            except (json.JSONDecodeError, TypeError):
                d[k] = {}
        d["oos_passed"] = bool(d.get("oos_passed"))
        return d

    # ── paper_trades (VIRTUALE) ──────────────────────────────────────────────
    def record_paper_trade(
        self, pair: str, side: str, entry_price: float, amount: float,
        experiment_id: Optional[int] = None, fee: float = 0.0,
        entry_ts: Optional[str] = None,
    ) -> int:
        """Înregistrează o tranzacție VIRTUALĂ (deschisă). Niciodată reală."""
        cur = self.conn.execute(
            "INSERT INTO paper_trades (experiment_id, pair, side, entry_ts, entry_price, "
            "amount, fee, status, created_at) VALUES (?,?,?,?,?,?,?, 'open', ?)",
            (experiment_id, pair, side, entry_ts or _now(), entry_price, amount, fee, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def close_paper_trade(self, trade_id: int, exit_price: float, exit_ts: Optional[str] = None) -> None:
        row = self.conn.execute(
            "SELECT side, entry_price, amount, fee FROM paper_trades WHERE id=?", (trade_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"paper_trade {trade_id} inexistent")
        side, entry_price, amount, fee = row["side"], row["entry_price"], row["amount"], row["fee"]
        direction = 1.0 if side == "long" else -1.0
        pnl = direction * (exit_price - entry_price) * amount - fee
        self.conn.execute(
            "UPDATE paper_trades SET exit_ts=?, exit_price=?, pnl=?, status='closed' WHERE id=?",
            (exit_ts or _now(), exit_price, pnl, trade_id),
        )
        self.conn.commit()

    def get_paper_trades(self, experiment_id: Optional[int] = None, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        if experiment_id is not None:
            cur = self.conn.execute(
                "SELECT * FROM paper_trades WHERE experiment_id=? ORDER BY id DESC LIMIT ?",
                (experiment_id, limit),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in cur.fetchall()]

    # ── agent_status (heartbeat pentru supervizarea Kage) ────────────────────
    def update_agent_status(
        self, agent: str, status: str, pid: Optional[int] = None, message: Optional[str] = None
    ) -> None:
        self.conn.execute(
            "INSERT INTO agent_status (agent, status, pid, last_heartbeat, message, updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(agent) DO UPDATE SET status=excluded.status, pid=excluded.pid, "
            "last_heartbeat=excluded.last_heartbeat, message=excluded.message, "
            "updated_at=excluded.updated_at",
            (agent, status, pid, _now(), message, _now()),
        )
        self.conn.commit()

    def get_agent_status(self, agent: Optional[str] = None) -> list[dict]:
        if agent:
            cur = self.conn.execute("SELECT * FROM agent_status WHERE agent=?", (agent,))
        else:
            cur = self.conn.execute("SELECT * FROM agent_status ORDER BY agent")
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()
