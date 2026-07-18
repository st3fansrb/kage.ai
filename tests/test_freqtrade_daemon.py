import json
import sys
from pathlib import Path

from trading.freqtrade_daemon import AGENT_NAME, FreqtradeDryRunDaemon
from trading.ledger import TradingLedger


def _config(path: Path, *, dry_run=True):
    path.write_text(json.dumps({"dry_run": dry_run, "dry_run_wallet": 1000, "exchange": {"key": "", "secret": ""}}))


def test_daemon_rejects_non_dry_run_before_spawn(tmp_path):
    cfg = tmp_path / "config.json"
    _config(cfg, dry_run=False)
    daemon = FreqtradeDryRunDaemon(cfg, tmp_path, tmp_path / "missing")
    try:
        daemon.build_command()
    except RuntimeError as exc:
        assert "paper" in str(exc) or "dry_run" in str(exc)
    else:
        raise AssertionError("non-dry-run trebuie refuzat")


def test_daemon_smoke_starts_and_writes_heartbeat(tmp_path):
    cfg = tmp_path / "config.json"
    _config(cfg)
    # Pentru smoke nu cere freqtrade real: binarul fake este chiar Python, cu un copil scurt.
    fake_bin = tmp_path / "freqtrade"
    fake_bin.symlink_to(Path(sys.executable))
    ledger_path = tmp_path / "trading.db"
    daemon = FreqtradeDryRunDaemon(cfg, tmp_path, fake_bin, ledger_path, heartbeat_seconds=0.01)

    # `python trade ...` ar esua; injectam un Popen controlat, pastrand verificarea de lifecycle.
    import subprocess
    seen_env = {}
    def popen(_cmd, **kwargs):
        seen_env.update(kwargs.get("env") or {})
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.03)"], **kwargs)

    assert daemon.run(tmp_path / "daemon.log", popen=popen) == 0
    # Strategia importă `trading.*` în subprocess-ul freqtrade → PYTHONPATH obligatoriu
    # (fără el: „Impossible to load Strategy", prins la prima pornire reală).
    from trading.freqtrade_daemon import PROJECT_ROOT
    assert str(PROJECT_ROOT) in seen_env.get("PYTHONPATH", "")
    ledger = TradingLedger(ledger_path)
    status = ledger.get_agent_status(AGENT_NAME)[0]
    assert status["last_heartbeat"] and status["status"] == "stopped"
    assert ledger.get_equity_snapshots()  # capitalul virtual a fost înregistrat
    ledger.close()


def test_daemon_mirrors_freqtrade_dry_run_trade_idempotently(tmp_path):
    ledger = TradingLedger(tmp_path / "trading.db")
    row = {
        "id": 42, "pair": "BTC/USDT", "open_rate": 100.0, "close_rate": 110.0,
        "amount": 2.0, "is_short": False, "is_open": False,
        "fee_open_cost": 0.1, "fee_close_cost": 0.1,
        "open_date": "2026-07-15T10:00:00", "close_date": "2026-07-15T11:00:00",
    }
    assert FreqtradeDryRunDaemon.sync_trade_rows(ledger, [row]) == 1
    assert FreqtradeDryRunDaemon.sync_trade_rows(ledger, [row]) == 0
    trades = ledger.get_paper_trades()
    assert len(trades) == 1 and trades[0]["status"] == "closed"
    assert trades[0]["pnl"] == 19.8
    ledger.close()
