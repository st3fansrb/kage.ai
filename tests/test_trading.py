"""Teste pentru fundația WP-T (T1): ledger `trading.db` + garda paper-only.

Criteriul de acceptare cheie — *nicio cale de cod nu poate plasa un ordin real* — e
verificat de `TestPaperOnlyGuard`: config-ul real din `kage_config.example.json` trece,
iar orice chei live / `dry_run:false` sunt refuzate.

Slice 2: teste pentru `FreqtradeRunner` (parsare rezultate, punte → ledger, comenzi,
gardă paper-only pe config freqtrade).
"""
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from trading import (
    TradingLedger, Experiment, PAPER_ONLY,
    assert_paper_only, is_paper_only, PaperOnlyViolation,
    FreqtradeRunner, FreqtradeNotInstalled,
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


# ══════════════════════════════════════════════════════════════════════════════
# Slice 2: FreqtradeRunner — parsare, punte → ledger, comenzi, safety
# ══════════════════════════════════════════════════════════════════════════════

# Fixture: JSON freqtrade backtesting output (structură reală minimală).
_SAMPLE_BT_RESULT = {
    "strategy": {
        "SampleStrategy": {
            "total_trades": 5,
            "profit_total": 0.025,
            "profit_total_abs": 25.0,
            "profit_factor": 1.5,
            "max_drawdown": 0.012,
            "max_drawdown_abs": 12.0,
            "wins": 3,
            "losses": 2,
            "draws": 0,
            "avg_profit": 0.005,
            "holding_avg": "2:30:00",
            "trade_count_long": 5,
            "trade_count_short": 0,
            "backtest_start": "2026-04-01 00:00:00",
            "backtest_end": "2026-07-01 00:00:00",
            "pair_results": [
                {"key": "BTC/USDT", "trades": 3, "profit_total_abs": 18.0},
                {"key": "ETH/USDT", "trades": 2, "profit_total_abs": 7.0},
                {"key": "TOTAL", "trades": 5, "profit_total_abs": 25.0},
            ],
            "trades": [
                {
                    "pair": "BTC/USDT", "is_short": False,
                    "open_rate": 40000.0, "close_rate": 40800.0,
                    "stake_amount": 100.0,
                    "fee_open": 0.1, "fee_close": 0.1,
                    "open_date": "2026-04-15 10:00:00",
                    "close_date": "2026-04-15 12:30:00",
                },
                {
                    "pair": "ETH/USDT", "is_short": False,
                    "open_rate": 2500.0, "close_rate": 2480.0,
                    "stake_amount": 100.0,
                    "fee_open": 0.1, "fee_close": 0.1,
                    "open_date": "2026-05-01 08:00:00",
                    "close_date": "2026-05-01 11:00:00",
                },
            ],
        }
    },
    "strategy_comparison": [],
}


@pytest.fixture
def bt_result_file(tmp_path):
    """Scrie un fișier JSON de rezultate freqtrade fixture."""
    path = tmp_path / "backtest_results" / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_SAMPLE_BT_RESULT), encoding="utf-8")
    return path


@pytest.fixture
def runner(tmp_path):
    """Runner cu venv/config/userdata în tmp_path (nu depinde de instalare reală)."""
    venv = tmp_path / ".trading-venv"
    venv.mkdir()
    ft_bin = venv / "bin"
    ft_bin.mkdir()
    # Creează un binar fals ca placeholder.
    fake_bin = ft_bin / "freqtrade"
    fake_bin.write_text("#!/bin/sh\necho fake")
    fake_bin.chmod(0o755)

    config = tmp_path / "ft_config_dry.json"
    config.write_text(json.dumps({
        "dry_run": True,
        "exchange": {"name": "binance", "key": "", "secret": ""},
    }), encoding="utf-8")

    userdata = tmp_path / "ft_userdata"
    userdata.mkdir()
    (userdata / "data").mkdir()
    (userdata / "backtest_results").mkdir()
    (userdata / "strategies").mkdir()

    return FreqtradeRunner(
        venv_dir=venv, config_path=config, userdata_dir=userdata,
    )


class TestFreqtradeConfigSafety:
    """Config-ul freqtrade template trece garda paper-only."""

    def test_ft_config_dry_is_paper_only(self):
        cfg = json.loads(
            (PROJECT_ROOT / "trading" / "ft_config_dry.json").read_text(encoding="utf-8")
        )
        assert_paper_only(cfg)

    def test_ft_config_has_dry_run_true(self):
        cfg = json.loads(
            (PROJECT_ROOT / "trading" / "ft_config_dry.json").read_text(encoding="utf-8")
        )
        assert cfg.get("dry_run") is True

    def test_ft_config_no_live_credentials(self):
        cfg = json.loads(
            (PROJECT_ROOT / "trading" / "ft_config_dry.json").read_text(encoding="utf-8")
        )
        key = cfg.get("exchange", {}).get("key", "")
        secret = cfg.get("exchange", {}).get("secret", "")
        assert key == "", "config-ul template nu trebuie să aibă chei live"
        assert secret == "", "config-ul template nu trebuie să aibă secret live"


class TestRunnerNotInstalled:
    """Degradare grațioasă dacă .trading-venv nu există."""

    def test_is_installed_false_when_missing(self, tmp_path):
        runner = FreqtradeRunner(venv_dir=tmp_path / "nonexistent")
        assert runner.is_installed is False

    def test_download_raises_not_installed(self, tmp_path):
        runner = FreqtradeRunner(venv_dir=tmp_path / "nonexistent")
        with pytest.raises(FreqtradeNotInstalled):
            runner.download_data(["BTC/USDT"])

    def test_backtest_raises_not_installed(self, tmp_path):
        runner = FreqtradeRunner(venv_dir=tmp_path / "nonexistent")
        with pytest.raises(FreqtradeNotInstalled):
            runner.run_backtest("SampleStrategy")


class TestRunnerRejectsLiveConfig:
    """Runner-ul refuză un config cu dry_run:false sau chei live."""

    def test_rejects_dry_run_false(self, tmp_path):
        venv = tmp_path / ".trading-venv"
        venv.mkdir()
        ft_bin = venv / "bin"
        ft_bin.mkdir()
        fake = ft_bin / "freqtrade"
        fake.write_text("#!/bin/sh\necho fake")
        fake.chmod(0o755)

        config = tmp_path / "bad_config.json"
        config.write_text(json.dumps({"dry_run": False}), encoding="utf-8")

        runner = FreqtradeRunner(venv_dir=venv, config_path=config)
        with pytest.raises(PaperOnlyViolation):
            runner.download_data(["BTC/USDT"])

    def test_rejects_live_keys(self, tmp_path):
        venv = tmp_path / ".trading-venv"
        venv.mkdir()
        ft_bin = venv / "bin"
        ft_bin.mkdir()
        fake = ft_bin / "freqtrade"
        fake.write_text("#!/bin/sh\necho fake")
        fake.chmod(0o755)

        config = tmp_path / "bad_config.json"
        config.write_text(json.dumps({
            "dry_run": True,
            "exchange": {"key": "REAL_KEY_123", "secret": "REAL_SECRET"},
        }), encoding="utf-8")

        runner = FreqtradeRunner(venv_dir=venv, config_path=config)
        with pytest.raises(PaperOnlyViolation):
            runner.run_backtest("SampleStrategy")


class TestParseBacktestResult:
    """Parsarea JSON-ului de rezultate freqtrade."""

    def test_parse_valid_result(self, bt_result_file):
        result = FreqtradeRunner.parse_backtest_result(bt_result_file)
        assert result is not None
        assert result["strategy"] == "SampleStrategy"
        assert result["total_trades"] == 5
        assert result["profit_total"] == 0.025
        assert result["profit_factor"] == 1.5
        assert result["wins"] == 3
        assert result["losses"] == 2
        assert len(result["pairs"]) == 2  # TOTAL e filtrat
        assert len(result["trades_detail"]) == 2

    def test_parse_missing_file(self, tmp_path):
        result = FreqtradeRunner.parse_backtest_result(tmp_path / "nonexistent.json")
        assert result is None

    def test_parse_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json{{{", encoding="utf-8")
        result = FreqtradeRunner.parse_backtest_result(bad)
        assert result is None

    def test_parse_empty_strategy(self, tmp_path):
        empty = tmp_path / "empty.json"
        empty.write_text(json.dumps({"strategy": {}}), encoding="utf-8")
        result = FreqtradeRunner.parse_backtest_result(empty)
        assert result is None

    def test_parse_zip_format(self, tmp_path):
        """Freqtrade 2026+ scrie rezultatele într-un ZIP."""
        zip_path = tmp_path / "backtest-result-2026-07-07.zip"
        json_content = json.dumps(_SAMPLE_BT_RESULT).encode()
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("backtest-result-2026-07-07.json", json_content)
        result = FreqtradeRunner.parse_backtest_result(zip_path)
        assert result is not None
        assert result["strategy"] == "SampleStrategy"
        assert result["total_trades"] == 5

    def test_parse_meta_json_skipped(self, tmp_path):
        """Fișierele .meta.json nu conțin rezultate — sunt ignorate."""
        meta = tmp_path / "backtest-result.meta.json"
        meta.write_text("{}", encoding="utf-8")
        result = FreqtradeRunner.parse_backtest_result(meta)
        assert result is None

    def test_find_latest_via_pointer(self, tmp_path):
        """_find_and_parse_latest folosește .last_result.json."""
        results_dir = tmp_path / "backtest_results"
        results_dir.mkdir()
        # Scrie ZIP-ul de rezultate.
        zip_path = results_dir / "backtest-result-2026-07-07.zip"
        json_content = json.dumps(_SAMPLE_BT_RESULT).encode()
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("backtest-result-2026-07-07.json", json_content)
        # Scrie pointer-ul.
        pointer = results_dir / ".last_result.json"
        pointer.write_text(json.dumps({"latest_backtest": "backtest-result-2026-07-07.zip"}))
        result = FreqtradeRunner._find_and_parse_latest(results_dir)
        assert result is not None
        assert result["strategy"] == "SampleStrategy"


class TestBacktestToLedger:
    """Punte rezultat parsat → TradingLedger."""

    def test_writes_experiment_and_trades(self, ledger, bt_result_file):
        result = FreqtradeRunner.parse_backtest_result(bt_result_file)
        assert result is not None
        exp_id = FreqtradeRunner.backtest_to_ledger(result, ledger, notes="test run")

        # Experiment creat.
        exps = ledger.get_experiments()
        assert len(exps) == 1
        exp = exps[0]
        assert exp["strategy"] == "SampleStrategy"
        assert exp["metrics"]["profit_factor"] == 1.5
        assert exp["metrics"]["wins"] == 3
        assert exp["notes"] == "test run"
        assert exp["status"] == "backtest"
        assert exp["backtest_start"] == "2026-04-01 00:00:00"

        # Paper trades create (2 trades din fixture).
        trades = ledger.get_paper_trades(experiment_id=exp_id)
        assert len(trades) == 2
        # Prima tranzacție: BTC long, entry 40000, exit 40800.
        btc_trade = [t for t in trades if t["pair"] == "BTC/USDT"][0]
        assert btc_trade["side"] == "long"
        assert btc_trade["status"] == "closed"
        assert btc_trade["entry_price"] == 40000.0
        assert btc_trade["exit_price"] == 40800.0

    def test_writes_with_mission_id(self, ledger, bt_result_file):
        result = FreqtradeRunner.parse_backtest_result(bt_result_file)
        exp_id = FreqtradeRunner.backtest_to_ledger(
            result, ledger, mission_id="nightly-2026-07-07"
        )
        exp = ledger.get_experiments()[0]
        assert exp["mission_id"] == "nightly-2026-07-07"


class TestRunnerCommands:
    """Verificarea comenzilor construite (fără a rula freqtrade real)."""

    def test_download_command_structure(self, runner):
        cmd = runner.build_download_command(
            pairs=["BTC/USDT", "ETH/USDT"], timeframe="1h", days=30
        )
        assert cmd[0].endswith("freqtrade")
        assert "download-data" in cmd
        assert "--pairs" in cmd
        assert "BTC/USDT" in cmd
        assert "ETH/USDT" in cmd
        assert "--timeframes" in cmd
        assert "1h" in cmd
        assert "--days" in cmd
        assert "30" in cmd

    def test_backtest_command_structure(self, runner):
        cmd = runner.build_backtest_command(
            strategy="SampleStrategy", timeframe="5m", timerange="20260101-20260401"
        )
        assert cmd[0].endswith("freqtrade")
        assert "backtesting" in cmd
        assert "--strategy" in cmd
        assert "SampleStrategy" in cmd
        assert "--timerange" in cmd
        assert "20260101-20260401" in cmd
        assert "--export" in cmd
        assert "trades" in cmd

    def test_backtest_command_no_timerange(self, runner):
        cmd = runner.build_backtest_command(strategy="X", timeframe="1h")
        assert "--timerange" not in cmd
