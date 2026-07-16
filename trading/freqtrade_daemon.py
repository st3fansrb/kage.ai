"""Supervisorul local pentru Freqtrade in dry-run.

Freqtrade ramane procesul care simuleaza ordinele; acest modul ii aplica garda
paper-only, ii scrie heartbeat-ul in `trading.db` si oglindeste capitalul virtual
pentru observabilitate. Nu citeste si nu accepta chei de exchange.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from trading.ledger import TradingLedger
from trading.safety import assert_paper_only

logger = logging.getLogger("trading.freqtrade_daemon")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "trading" / "ft_config_dry.json"
DEFAULT_USERDATA = PROJECT_ROOT / "trading" / "ft_userdata"
AGENT_NAME = "crypto-freqtrade"


class FreqtradeDryRunDaemon:
    """Porneste si supravegheaza exact un proces `freqtrade trade --dry-run`."""

    def __init__(
        self,
        config_path: str | Path = DEFAULT_CONFIG,
        userdata_dir: str | Path = DEFAULT_USERDATA,
        freqtrade_bin: str | Path | None = None,
        ledger_path: str | Path | None = None,
        heartbeat_seconds: float = 30.0,
    ) -> None:
        self.config_path = Path(config_path)
        self.userdata_dir = Path(userdata_dir)
        self.freqtrade_bin = Path(freqtrade_bin) if freqtrade_bin else PROJECT_ROOT / ".trading-venv/bin/freqtrade"
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self.heartbeat_seconds = max(float(heartbeat_seconds), 0.01)
        self._stop_requested = False

    def load_config(self) -> dict:
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        assert_paper_only(config)
        if config.get("dry_run") is not True:
            raise RuntimeError("refuz: daemonul cere explicit dry_run=true")
        return config

    def build_command(self) -> list[str]:
        self.load_config()
        return [
            str(self.freqtrade_bin), "trade", "--config", str(self.config_path),
            "--strategy", "SampleStrategy", "--userdir", str(self.userdata_dir),
        ]

    def _request_stop(self, _signum: int, _frame: object) -> None:
        self._stop_requested = True

    @staticmethod
    def _equity(ledger: TradingLedger, initial_wallet: float) -> tuple[float, int]:
        rows = ledger.conn.execute(
            "SELECT COALESCE(SUM(pnl), 0.0), SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) FROM paper_trades"
        ).fetchone()
        return initial_wallet + float(rows[0] or 0.0), int(rows[1] or 0)

    @staticmethod
    def sync_trade_rows(ledger: TradingLedger, rows: list[dict]) -> int:
        """Oglindește idempotent rândurile Freqtrade în `paper_trades`.

        Freqtrade își păstrează propriul SQLite pentru lifecycle; acest adaptor copiază
        strict datele dry-run în ledger-ul observabil Kage, fără a trimite ordine.
        """
        synced = 0
        for row in rows:
            source_id = row.get("id")
            entry = float(row.get("open_rate") or 0.0)
            amount = float(row.get("amount") or 0.0)
            if source_id is None or entry <= 0 or amount <= 0:
                continue
            local_id = ledger.freqtrade_trade_id(str(source_id))
            if local_id is None:
                fee = float(row.get("fee_open_cost") or 0.0) + float(row.get("fee_close_cost") or 0.0)
                local_id = ledger.record_paper_trade(
                    pair=str(row.get("pair") or "unknown"),
                    side="short" if row.get("is_short") else "long",
                    entry_price=entry, amount=amount, fee=fee,
                    entry_ts=str(row.get("open_date") or "") or None,
                )
                ledger.map_freqtrade_trade(str(source_id), local_id)
                synced += 1
            is_open = bool(row.get("is_open", True))
            exit_rate = float(row.get("close_rate") or 0.0)
            local = ledger.conn.execute("SELECT status FROM paper_trades WHERE id=?", (local_id,)).fetchone()
            if not is_open and exit_rate > 0 and local and local["status"] == "open":
                ledger.close_paper_trade(local_id, exit_rate, str(row.get("close_date") or "") or None)
        return synced

    @staticmethod
    def _read_freqtrade_rows(config: dict) -> list[dict]:
        db_url = str(config.get("db_url", ""))
        if not db_url.startswith("sqlite:///"):
            return []
        db_path = Path(db_url.removeprefix("sqlite:///"))
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
        if not db_path.is_file():
            return []
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                return [dict(row) for row in conn.execute("SELECT * FROM trades").fetchall()]
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.debug("nu pot citi Freqtrade SQLite încă: %s", exc)
            return []

    def run(
        self,
        log_path: str | Path,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> int:
        """Ruleaza pana la semnal/iesirea Freqtrade; returneaza exit code-ul copilului."""
        config = self.load_config()  # fail closed inainte de Popen
        if not self.freqtrade_bin.is_file():
            raise RuntimeError(f"freqtrade nu este instalat: {self.freqtrade_bin}; ruleaza scripts/setup_trading.sh")

        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        ledger = TradingLedger(self.ledger_path) if self.ledger_path else TradingLedger()
        cmd = self.build_command()
        log_file = open(log_path, "a", encoding="utf-8")
        proc: Optional[subprocess.Popen] = None
        previous_handlers = {}
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[sig] = signal.signal(sig, self._request_stop)
            # Strategia (SampleStrategy) importă `trading.daily_context.bias_allows`;
            # subprocess-ul `freqtrade` NU are cwd-ul pe sys.path (sys.path[0] = dir-ul
            # binarului), deci fără PYTHONPATH importul pică cu „Impossible to load
            # Strategy" — prins la prima pornire reală T1-exec (16.07.2026).
            env = dict(os.environ)
            env["PYTHONPATH"] = str(PROJECT_ROOT) + (
                os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            proc = popen(cmd, cwd=str(PROJECT_ROOT), stdout=log_file,
                         stderr=subprocess.STDOUT, env=env)
            ledger.update_agent_status(AGENT_NAME, "running", pid=proc.pid, message="freqtrade dry-run")
            while proc.poll() is None and not self._stop_requested:
                self.sync_trade_rows(ledger, self._read_freqtrade_rows(config))
                equity, open_count = self._equity(ledger, float(config.get("dry_run_wallet", 0)))
                ledger.record_equity_snapshot(equity, available_capital=equity, open_trade_count=open_count)
                ledger.update_agent_status(AGENT_NAME, "running", pid=proc.pid, message="heartbeat dry-run")
                sleep(self.heartbeat_seconds)
            if self._stop_requested and proc.poll() is None:
                proc.terminate()
            exit_code = proc.wait(timeout=20)
            ledger.update_agent_status(AGENT_NAME, "stopped" if exit_code == 0 else "error", pid=proc.pid,
                                       message=f"freqtrade exited ({exit_code})")
            return int(exit_code)
        finally:
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
            log_file.close()
            ledger.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Kage Freqtrade dry-run daemon (paper-only)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--userdata", default=str(DEFAULT_USERDATA))
    parser.add_argument("--freqtrade-bin", default=None)
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--log", required=True)
    parser.add_argument("--heartbeat-seconds", type=float, default=30.0)
    args = parser.parse_args()
    daemon = FreqtradeDryRunDaemon(args.config, args.userdata, args.freqtrade_bin, args.ledger,
                                   args.heartbeat_seconds)
    return daemon.run(args.log)


if __name__ == "__main__":
    raise SystemExit(main())
