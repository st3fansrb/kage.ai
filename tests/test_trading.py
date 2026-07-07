"""Teste pentru fundația WP-T (T1): ledger `trading.db` + garda paper-only.

Criteriul de acceptare cheie — *nicio cale de cod nu poate plasa un ordin real* — e
verificat de `TestPaperOnlyGuard`: config-ul real din `kage_config.example.json` trece,
iar orice chei live / `dry_run:false` sunt refuzate.
"""
import json
from pathlib import Path

import pytest

from trading import (
    TradingLedger, Experiment, PAPER_ONLY,
    assert_paper_only, is_paper_only, PaperOnlyViolation,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ledger(tmp_path):
    lg = TradingLedger(db_path=tmp_path / "trading.db")
    yield lg
    lg.close()


# ── Ledger: experiments ──────────────────────────────────────────────────────
def test_schema_created(ledger):
    tables = {r["name"] for r in ledger.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"experiments", "paper_trades", "agent_status"} <= tables


def test_record_and_get_experiment(ledger):
    eid = ledger.record_experiment(Experiment(
        strategy="ema_cross", pair="BTC/USDT", timeframe="5m",
        params={"ema_fast": 12, "ema_slow": 26},
        metrics={"profit_pct": 3.4, "max_drawdown": 1.2, "trades": 41},
    ))
    assert eid > 0
    rows = ledger.get_experiments()
    assert len(rows) == 1
    exp = rows[0]
    assert exp["strategy"] == "ema_cross"
    assert exp["params"]["ema_fast"] == 12          # JSON dezserializat
    assert exp["metrics"]["trades"] == 41
    assert exp["oos_passed"] is False


def test_mark_oos_passed(ledger):
    eid = ledger.record_experiment(Experiment(strategy="s", pair="ETH/USDT", timeframe="1h"))
    ledger.mark_oos_passed(eid, True)
    assert ledger.get_experiments()[0]["oos_passed"] is True


def test_get_experiments_filter_by_strategy(ledger):
    ledger.record_experiment(Experiment(strategy="a", pair="BTC/USDT", timeframe="5m"))
    ledger.record_experiment(Experiment(strategy="b", pair="BTC/USDT", timeframe="5m"))
    assert len(ledger.get_experiments(strategy="a")) == 1


# ── Ledger: paper_trades (VIRTUALE) ──────────────────────────────────────────
def test_paper_trade_long_pnl(ledger):
    tid = ledger.record_paper_trade(pair="BTC/USDT", side="long", entry_price=100.0, amount=2.0, fee=0.5)
    ledger.close_paper_trade(tid, exit_price=110.0)
    tr = ledger.get_paper_trades()[0]
    assert tr["status"] == "closed"
    # long: (110-100)*2 - 0.5 = 19.5
    assert tr["pnl"] == pytest.approx(19.5)


def test_paper_trade_short_pnl(ledger):
    tid = ledger.record_paper_trade(pair="ETH/USDT", side="short", entry_price=100.0, amount=1.0, fee=0.0)
    ledger.close_paper_trade(tid, exit_price=90.0)
    # short: -1*(90-100)*1 - 0 = 10
    assert ledger.get_paper_trades()[0]["pnl"] == pytest.approx(10.0)


def test_close_missing_trade_raises(ledger):
    with pytest.raises(ValueError):
        ledger.close_paper_trade(999, exit_price=1.0)


# ── Ledger: agent_status (upsert/heartbeat) ──────────────────────────────────
def test_agent_status_upsert(ledger):
    ledger.update_agent_status("crypto-freqtrade", "running", pid=123, message="dry-run")
    ledger.update_agent_status("crypto-freqtrade", "stopped", message="oprit de user")
    rows = ledger.get_agent_status("crypto-freqtrade")
    assert len(rows) == 1                    # upsert, nu insert dublu
    assert rows[0]["status"] == "stopped"
    assert rows[0]["last_heartbeat"]


# ── Garda paper-only (criteriul de acceptare T1) ─────────────────────────────
class TestPaperOnlyGuard:
    def test_paper_only_flag_immutable_true(self):
        assert PAPER_ONLY is True

    def test_clean_config_passes(self):
        cfg = {"exchange": "binance", "pairs": ["BTC/USDT"], "dry_run": True, "dry_run_wallet": 1000}
        assert_paper_only(cfg)              # nu ridică
        assert is_paper_only(cfg) is True

    def test_live_api_key_rejected(self):
        with pytest.raises(PaperOnlyViolation):
            assert_paper_only({"exchange": {"name": "binance", "key": "AKIA...", "secret": "xxx"}})

    def test_empty_credential_placeholder_allowed(self):
        # chei goale (placeholdere) sunt ok — doar valorile reale sunt interzise
        assert_paper_only({"exchange": {"key": "", "secret": ""}})

    def test_dry_run_false_rejected(self):
        with pytest.raises(PaperOnlyViolation):
            assert_paper_only({"dry_run": False})

    def test_live_trading_mode_rejected(self):
        with pytest.raises(PaperOnlyViolation):
            assert_paper_only({"trading_mode": "live"})

    def test_nested_and_list_scanned(self):
        with pytest.raises(PaperOnlyViolation):
            assert_paper_only({"exchanges": [{"api_secret": "realsecret"}]})

    def test_example_config_trading_block_is_paper_only(self):
        cfg = json.loads((PROJECT_ROOT / "kage_config.example.json").read_text(encoding="utf-8"))
        trading = cfg.get("trading", {})
        assert trading, "blocul 'trading' lipsește din kage_config.example.json"
        assert_paper_only(trading)          # config-ul livrat NU are chei live
