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

-- Contorul GLOBAL de trial-uri (invariant #3): fiecare backtest rulat vreodată, INCLUSIV
-- eșecurile, inserează un rând. Nimic nu se șterge. Fără el, Deflated Sharpe/PBO sunt invalide.
CREATE TABLE IF NOT EXISTS trials (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL DEFAULT 'backtest', -- backtest | hyperopt
    strategy      TEXT,
    params_hash   TEXT,
    cost_profile  TEXT NOT NULL DEFAULT 'stressed', -- stressed | nominal
    outcome       TEXT NOT NULL DEFAULT 'ok',        -- ok | failed
    experiment_id INTEGER,                           -- dacă a produs un experiment
    created_at    TEXT NOT NULL
);

-- Kill-switch determinist (invariant #6): stare singleton (id=1). Bucla de execuție o citește
-- ca „flat everything". Pur descriptiv aici — logica e în killswitch.py, zero LLM.
CREATE TABLE IF NOT EXISTS killswitch (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    halted     INTEGER NOT NULL DEFAULT 0,
    reason     TEXT,
    drawdown   REAL,
    tripped_at TEXT,
    cleared_at TEXT
);

-- Context zilnic (bucla 2): regim + bias direcțional (clasificare NON-LLM). Bucla de execuție
-- citește `bias` ca LIMITATOR. Un rând pe zi (upsert pe date).
CREATE TABLE IF NOT EXISTS daily_context (
    date       TEXT PRIMARY KEY,               -- YYYY-MM-DD
    regime     TEXT NOT NULL,
    bias       TEXT NOT NULL,                  -- long_only | short_only | neutral | flat
    confidence REAL NOT NULL DEFAULT 0.0,
    reasoning  TEXT,
    features   TEXT NOT NULL DEFAULT '{}',     -- JSON: vol, trend, funding, OI…
    created_at TEXT NOT NULL
);

-- Registrul de IPOTEZE (invariant #2: pre-registration). O ipoteză există doar dacă a fost
-- scrisă AICI înainte de a putea fi verificată. Explicații post-hoc = storytelling, nu intră.
CREATE TABLE IF NOT EXISTS hypotheses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    mechanism           TEXT NOT NULL,          -- cine e forțat să facă ce și de ce atunci
    prediction          TEXT NOT NULL,          -- JSON: {mean, interval_80:[lo,hi], horizon, kind}
    falsification       TEXT NOT NULL,          -- criteriul care o infirmă
    status              TEXT NOT NULL DEFAULT 'open',  -- open | confirmed | falsified | expired
    regime_at_creation  TEXT,
    source_model        TEXT,
    pre_registered_at   TEXT NOT NULL,          -- momentul pre-înregistrării (gardă temporală)
    created_at          TEXT NOT NULL
);

-- Instanțele de predicție emise sub o ipoteză + rezultatul realizat (pentru calibrare).
CREATE TABLE IF NOT EXISTS predictions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id INTEGER NOT NULL,
    signal_ts     TEXT NOT NULL,                -- momentul semnalului (≥ pre_registered_at)
    predicted     TEXT NOT NULL,                -- JSON: {value | prob, interval_80:[lo,hi]}
    realized      TEXT,                         -- JSON: {value} — completat la rezolvare
    horizon       TEXT,
    resolved_at   TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id)
);
CREATE INDEX IF NOT EXISTS idx_pred_hyp ON predictions(hypothesis_id);
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

    # ── trials (contorul global — invariant #3) ──────────────────────────────
    def record_trial(
        self, kind: str = "backtest", strategy: Optional[str] = None,
        params_hash: Optional[str] = None, cost_profile: str = "stressed",
        outcome: str = "ok", experiment_id: Optional[int] = None,
    ) -> int:
        """Înregistrează un trial (backtest rulat). SE APELEAZĂ ȘI PE EȘEC (outcome='failed')."""
        cur = self.conn.execute(
            "INSERT INTO trials (kind, strategy, params_hash, cost_profile, outcome, "
            "experiment_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (kind, strategy, params_hash, cost_profile, outcome, experiment_id, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def count_trials(self) -> int:
        """Numărul total de trial-uri rulate vreodată (denominatorul pentru DSR/PBO)."""
        return int(self.conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0])

    def get_trials(self, limit: int = 200) -> list[dict]:
        limit = max(1, min(int(limit), 5000))
        cur = self.conn.execute("SELECT * FROM trials ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]

    # ── kill-switch (invariant #6) ───────────────────────────────────────────
    def trip_killswitch(self, reason: str, drawdown: Optional[float] = None) -> None:
        """Declanșează halt-ul (flat everything). Idempotent (upsert singleton id=1)."""
        self.conn.execute(
            "INSERT INTO killswitch (id, halted, reason, drawdown, tripped_at, cleared_at) "
            "VALUES (1, 1, ?, ?, ?, NULL) "
            "ON CONFLICT(id) DO UPDATE SET halted=1, reason=excluded.reason, "
            "drawdown=excluded.drawdown, tripped_at=excluded.tripped_at, cleared_at=NULL",
            (reason, drawdown, _now()),
        )
        self.conn.commit()

    def clear_killswitch(self) -> None:
        """Ridică halt-ul (decizie manuală). Marchează cleared_at."""
        self.conn.execute(
            "INSERT INTO killswitch (id, halted, cleared_at) VALUES (1, 0, ?) "
            "ON CONFLICT(id) DO UPDATE SET halted=0, cleared_at=excluded.cleared_at",
            (_now(),),
        )
        self.conn.commit()

    def is_halted(self) -> bool:
        row = self.conn.execute("SELECT halted FROM killswitch WHERE id=1").fetchone()
        return bool(row and row["halted"])

    def killswitch_state(self) -> dict:
        row = self.conn.execute("SELECT * FROM killswitch WHERE id=1").fetchone()
        return dict(row) if row else {"halted": 0}

    # ── daily_context (bucla 2 — regim + bias) ───────────────────────────────
    def upsert_daily_context(
        self, date: str, regime: str, bias: str, confidence: float = 0.0,
        reasoning: Optional[str] = None, features: Optional[dict] = None,
    ) -> None:
        """Scrie/actualizează contextul zilei (un rând per dată)."""
        self.conn.execute(
            "INSERT INTO daily_context (date, regime, bias, confidence, reasoning, features, created_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET regime=excluded.regime, bias=excluded.bias, "
            "confidence=excluded.confidence, reasoning=excluded.reasoning, features=excluded.features",
            (date, regime, bias, float(confidence), reasoning,
             json.dumps(features or {}, ensure_ascii=False), _now()),
        )
        self.conn.commit()

    def get_daily_context(self, date: Optional[str] = None) -> Optional[dict]:
        """Contextul unei zile (default: cel mai recent). None dacă nu există."""
        if date:
            row = self.conn.execute("SELECT * FROM daily_context WHERE date=?", (date,)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM daily_context ORDER BY date DESC LIMIT 1").fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["features"] = json.loads(d["features"]) if d.get("features") else {}
        except (json.JSONDecodeError, TypeError):
            d["features"] = {}
        return d

    # ── hypotheses / predictions (registru — invariant #2) ───────────────────
    def insert_hypothesis(
        self, mechanism: str, prediction: dict, falsification: str,
        pre_registered_at: str, regime_at_creation: Optional[str] = None,
        source_model: Optional[str] = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO hypotheses (mechanism, prediction, falsification, status, "
            "regime_at_creation, source_model, pre_registered_at, created_at) "
            "VALUES (?,?,?, 'open', ?,?,?,?)",
            (mechanism, json.dumps(prediction, ensure_ascii=False), falsification,
             regime_at_creation, source_model, pre_registered_at, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def _hyp_row(r: sqlite3.Row) -> dict:
        d = dict(r)
        try:
            d["prediction"] = json.loads(d["prediction"]) if d.get("prediction") else {}
        except (json.JSONDecodeError, TypeError):
            d["prediction"] = {}
        return d

    def get_hypothesis(self, hypothesis_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM hypotheses WHERE id=?", (hypothesis_id,)).fetchone()
        return self._hyp_row(row) if row else None

    def get_hypotheses(self, status: Optional[str] = None, limit: int = 200) -> list[dict]:
        limit = max(1, min(int(limit), 2000))
        if status:
            cur = self.conn.execute(
                "SELECT * FROM hypotheses WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        else:
            cur = self.conn.execute(
                "SELECT * FROM hypotheses ORDER BY id DESC LIMIT ?", (limit,))
        return [self._hyp_row(r) for r in cur.fetchall()]

    def update_hypothesis_status(self, hypothesis_id: int, status: str) -> None:
        self.conn.execute("UPDATE hypotheses SET status=? WHERE id=?", (status, hypothesis_id))
        self.conn.commit()

    def insert_prediction(
        self, hypothesis_id: int, signal_ts: str, predicted: dict, horizon: Optional[str] = None
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO predictions (hypothesis_id, signal_ts, predicted, horizon, created_at) "
            "VALUES (?,?,?,?,?)",
            (hypothesis_id, signal_ts, json.dumps(predicted, ensure_ascii=False), horizon, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def resolve_prediction(self, prediction_id: int, realized: dict) -> None:
        self.conn.execute(
            "UPDATE predictions SET realized=?, resolved_at=? WHERE id=?",
            (json.dumps(realized, ensure_ascii=False), _now(), prediction_id),
        )
        self.conn.commit()

    @staticmethod
    def _pred_row(r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in ("predicted", "realized"):
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except (json.JSONDecodeError, TypeError):
                    d[k] = None
        return d

    def get_predictions(
        self, hypothesis_id: Optional[int] = None, resolved: Optional[bool] = None, limit: int = 1000
    ) -> list[dict]:
        limit = max(1, min(int(limit), 100000))
        clauses, params = [], []
        if hypothesis_id is not None:
            clauses.append("hypothesis_id=?"); params.append(hypothesis_id)
        if resolved is True:
            clauses.append("realized IS NOT NULL")
        elif resolved is False:
            clauses.append("realized IS NULL")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        cur = self.conn.execute(
            f"SELECT * FROM predictions{where} ORDER BY id DESC LIMIT ?", tuple(params))
        return [self._pred_row(r) for r in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()
