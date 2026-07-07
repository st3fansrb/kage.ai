"""Runner freqtrade — punte subprocess → TradingLedger (WP-T T1 slice 2).

Rulează freqtrade ca subprocess din `.trading-venv` (izolat de dependințele Kage).
Parsează output-ul JSON al backtesting-ului și scrie rezultatele în `trading.db`
prin :class:`TradingLedger`.

Garanții de siguranță:
- ``assert_paper_only(config)`` înainte de orice subprocess
- Verifică existența `.trading-venv` înainte de a rula
- Nicio cale de cod nu expune credențiale (config-ul nu le conține)

Utilizare tipică (din buclă nocturnă / misiune WP11)::

    from trading.runner import FreqtradeRunner
    from trading.ledger import TradingLedger

    runner = FreqtradeRunner()
    result = runner.run_backtest("SampleStrategy", "BTC/USDT", "5m")
    if result:
        ledger = TradingLedger()
        exp_id = runner.backtest_to_ledger(result, ledger)
"""

from __future__ import annotations

import json
import zipfile
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from trading.ledger import TradingLedger, Experiment
from trading.safety import assert_paper_only

logger = logging.getLogger("trading.runner")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_VENV = _PROJECT_ROOT / ".trading-venv"
_DEFAULT_CONFIG = _PROJECT_ROOT / "trading" / "ft_config_dry.json"
_DEFAULT_USERDATA = _PROJECT_ROOT / "trading" / "ft_userdata"


class FreqtradeNotInstalled(RuntimeError):
    """`.trading-venv` nu există sau freqtrade nu e instalat."""


class BacktestError(RuntimeError):
    """Freqtrade backtest a eșuat (exit code != 0)."""


class FreqtradeRunner:
    """Rulează freqtrade ca subprocess din `.trading-venv`, parsează output-ul.

    Nu importă freqtrade ca librărie — doar subprocess cu JSON I/O.
    """

    def __init__(
        self,
        venv_dir: str | Path | None = None,
        config_path: str | Path | None = None,
        userdata_dir: str | Path | None = None,
    ) -> None:
        self.venv_dir = Path(venv_dir) if venv_dir else _DEFAULT_VENV
        self.config_path = Path(config_path) if config_path else _DEFAULT_CONFIG
        self.userdata_dir = Path(userdata_dir) if userdata_dir else _DEFAULT_USERDATA
        self._ft_bin = self.venv_dir / "bin" / "freqtrade"

    # ── verificări ───────────────────────────────────────────────────────────

    @property
    def is_installed(self) -> bool:
        """True dacă `.trading-venv` și binarul freqtrade există."""
        return self._ft_bin.is_file()

    def _assert_installed(self) -> None:
        if not self.is_installed:
            raise FreqtradeNotInstalled(
                f"freqtrade nu e instalat la {self._ft_bin} — "
                f"rulează: bash scripts/setup_trading.sh"
            )

    def _load_and_validate_config(self) -> dict:
        """Încarcă config-ul freqtrade și verifică paper-only."""
        cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
        assert_paper_only(cfg)
        return cfg

    # ── download data ────────────────────────────────────────────────────────

    def build_download_command(
        self,
        pairs: list[str],
        timeframe: str = "5m",
        days: int = 90,
    ) -> list[str]:
        """Construiește comanda de download fără a o rula — util pentru teste."""
        cmd = [
            str(self._ft_bin),
            "download-data",
            "--config", str(self.config_path),
            "--pairs",
        ] + pairs + [
            "--timeframes", timeframe,
            "--days", str(days),
            "--datadir", str(self.userdata_dir / "data"),
            "--exchange", "binance",
        ]
        return cmd

    def download_data(
        self,
        pairs: list[str] | None = None,
        timeframe: str = "5m",
        days: int = 90,
        timeout: int = 600,
    ) -> bool:
        """Descarcă date istorice prin `freqtrade download-data`.

        Args:
            pairs: Perechile de descărcat (default: din config).
            timeframe: Timeframe-ul de descărcat.
            days: Câte zile de date.
            timeout: Timeout subprocess (secunde).

        Returns:
            True dacă download-ul a reușit.

        Raises:
            FreqtradeNotInstalled: Dacă .trading-venv lipsește.
            PaperOnlyViolation: Dacă config-ul nu e paper-only.
        """
        self._assert_installed()
        cfg = self._load_and_validate_config()

        if pairs is None:
            pairs = cfg.get("exchange", {}).get("pair_whitelist", ["BTC/USDT"])

        cmd = self.build_download_command(pairs, timeframe, days)
        logger.info("download-data: %s", " ".join(cmd))

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(_PROJECT_ROOT),
        )

        if result.returncode != 0:
            logger.error("download-data failed:\nstdout: %s\nstderr: %s",
                         result.stdout[-2000:] if result.stdout else "",
                         result.stderr[-2000:] if result.stderr else "")
            return False

        logger.info("download-data OK")
        return True

    # ── backtest ─────────────────────────────────────────────────────────────

    def build_backtest_command(
        self,
        strategy: str,
        timeframe: str = "5m",
        timerange: str | None = None,
    ) -> list[str]:
        """Construiește comanda de backtest fără a o rula — util pentru teste."""
        cmd = [
            str(self._ft_bin),
            "backtesting",
            "--config", str(self.config_path),
            "--strategy", strategy,
            "--timeframe", timeframe,
            "--datadir", str(self.userdata_dir / "data"),
            "--userdir", str(self.userdata_dir),
            "--export", "trades",
        ]
        if timerange:
            cmd.extend(["--timerange", timerange])
        return cmd

    def run_backtest(
        self,
        strategy: str = "SampleStrategy",
        timeframe: str = "5m",
        timerange: str | None = None,
        timeout: int = 1200,
    ) -> dict | None:
        """Rulează un backtest freqtrade și returnează rezultatele parsate.

        Args:
            strategy: Numele strategiei freqtrade.
            timeframe: Timeframe-ul de backtest.
            timerange: Opțional, ex. ``"20260101-20260401"``.
            timeout: Timeout subprocess (secunde).

        Returns:
            Dict cu rezultatele parsate, sau None dacă a eșuat.

        Raises:
            FreqtradeNotInstalled: Dacă .trading-venv lipsește.
            PaperOnlyViolation: Dacă config-ul nu e paper-only.
        """
        self._assert_installed()
        self._load_and_validate_config()

        cmd = self.build_backtest_command(strategy, timeframe, timerange)
        logger.info("backtest: %s", " ".join(cmd))

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(_PROJECT_ROOT),
        )

        if result.returncode != 0:
            logger.error("backtest failed (exit %d):\nstdout: %s\nstderr: %s",
                         result.returncode,
                         result.stdout[-2000:] if result.stdout else "",
                         result.stderr[-2000:] if result.stderr else "")
            return None

        # Freqtrade 2026+ scrie un pointer .last_result.json → fișierul real.
        results_dir = self.userdata_dir / "backtest_results"
        return self._find_and_parse_latest(results_dir)

    # ── parsare rezultate ────────────────────────────────────────────────────

    @staticmethod
    def _find_and_parse_latest(results_dir: Path) -> dict | None:
        """Găsește cel mai recent rezultat (via .last_result.json) și parsează-l."""
        pointer = results_dir / ".last_result.json"
        if pointer.is_file():
            try:
                meta = json.loads(pointer.read_text(encoding="utf-8"))
                latest = meta.get("latest_backtest", "")
                if latest:
                    target = results_dir / latest
                    return FreqtradeRunner.parse_backtest_result(target)
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Nu pot citi .last_result.json: %s", exc)

        # Fallback: cel mai recent .json sau .zip din director.
        candidates = sorted(results_dir.glob("backtest-result-*"), reverse=True)
        for c in candidates:
            result = FreqtradeRunner.parse_backtest_result(c)
            if result is not None:
                return result
        return None

    @staticmethod
    def parse_backtest_result(result_path: str | Path) -> dict | None:
        """Parsează un fișier de rezultate freqtrade backtesting.

        Suportă:
        - JSON direct (format legacy)
        - ZIP (freqtrade 2026+): extrage JSON-ul principal din arhivă

        Returnează un dict plat cu metricile cheie, sau None dacă fișierul
        nu există / nu poate fi parsat.
        """
        path = Path(result_path)
        if not path.is_file():
            logger.warning("Fișier de rezultate inexistent: %s", path)
            return None

        raw = None

        # ZIP (freqtrade 2026+): JSON-ul principal e primul .json din arhivă.
        if path.suffix == ".zip":
            try:
                with zipfile.ZipFile(path) as zf:
                    json_names = [n for n in zf.namelist() if n.endswith(".json")
                                  and "config" not in n.lower()]
                    if not json_names:
                        logger.warning("ZIP fără JSON de rezultate: %s", path)
                        return None
                    raw = json.loads(zf.read(json_names[0]))
            except (zipfile.BadZipFile, json.JSONDecodeError) as exc:
                logger.warning("Nu pot parsa ZIP %s: %s", path, exc)
                return None
        else:
            # JSON direct (format legacy sau .meta.json — skip meta).
            if ".meta." in path.name:
                return None
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning("Nu pot parsa %s: %s", path, exc)
                return None

        if raw is None:
            return None

        strategies = raw.get("strategy", {})
        if not strategies:
            logger.warning("JSON fără cheia 'strategy': %s", path)
            return None

        # Ia prima (și de obicei singura) strategie.
        strategy_name = next(iter(strategies))
        data = strategies[strategy_name]

        trades = data.get("trades", [])
        total_trades = data.get("total_trades", len(trades))

        # Pair results: freqtrade 2026+ folosește 'results_per_pair' (nu 'pair_results').
        pair_results = data.get("pair_results", data.get("results_per_pair", []))

        result = {
            "strategy": strategy_name,
            "total_trades": total_trades,
            "profit_total": data.get("profit_total", 0.0),
            "profit_total_abs": data.get("profit_total_abs", 0.0),
            "profit_factor": data.get("profit_factor", 0.0),
            "max_drawdown": data.get("max_drawdown", 0.0),
            "max_drawdown_abs": data.get("max_drawdown_abs", 0.0),
            "win_rate": (
                data.get("wins", 0) / total_trades if total_trades > 0 else 0.0
            ),
            "wins": data.get("wins", 0),
            "losses": data.get("losses", 0),
            "draws": data.get("draws", 0),
            "avg_profit": data.get("avg_profit", data.get("profit_mean", 0.0)),
            "holding_avg": data.get("holding_avg", data.get("holding_avg_s", "")),
            "trade_count_long": data.get("trade_count_long", 0),
            "trade_count_short": data.get("trade_count_short", 0),
            "backtest_start": data.get("backtest_start", ""),
            "backtest_end": data.get("backtest_end", ""),
            "pairs": [
                {"pair": p.get("key", ""), "trades": p.get("trades", 0),
                 "profit_total_abs": p.get("profit_total_abs", 0.0)}
                for p in pair_results
                if p.get("key") != "TOTAL"
            ],
            "trades_detail": trades,
            "source_file": str(path),
        }
        return result

    # ── punte → ledger ───────────────────────────────────────────────────────

    @staticmethod
    def backtest_to_ledger(
        result: dict,
        ledger: TradingLedger,
        mission_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> int:
        """Scrie rezultatele unui backtest în `trading.db`.

        Creează un :class:`Experiment` cu metricile agregate și, opțional,
        ``paper_trades`` individuale pentru fiecare tranzacție din backtest.

        Args:
            result: Dict returnat de :meth:`parse_backtest_result`.
            ledger: Instanță :class:`TradingLedger`.
            mission_id: ID-ul misiunii WP11 (dacă e din buclă nocturnă).
            notes: Note libere.

        Returns:
            ID-ul experimentului creat.
        """
        # Adună perechile din rezultate.
        pair_list = [p["pair"] for p in result.get("pairs", [])]
        pair_str = ",".join(pair_list) if pair_list else "unknown"

        # Determină timeframe-ul din holding_avg sau pune default.
        timeframe = "5m"  # default; freqtrade nu îl pune explicit în JSON
        for suffix in ("backtest_start", "backtest_end"):
            val = result.get(suffix, "")
            if val:
                # Salvăm start/end direct.
                pass

        exp = Experiment(
            strategy=result.get("strategy", "unknown"),
            pair=pair_str,
            timeframe=timeframe,
            exchange="binance",
            params={
                "total_trades": result.get("total_trades", 0),
                "trade_count_long": result.get("trade_count_long", 0),
                "trade_count_short": result.get("trade_count_short", 0),
            },
            metrics={
                "profit_total": result.get("profit_total", 0.0),
                "profit_total_abs": result.get("profit_total_abs", 0.0),
                "profit_factor": result.get("profit_factor", 0.0),
                "max_drawdown": result.get("max_drawdown", 0.0),
                "max_drawdown_abs": result.get("max_drawdown_abs", 0.0),
                "win_rate": result.get("win_rate", 0.0),
                "wins": result.get("wins", 0),
                "losses": result.get("losses", 0),
                "avg_profit": result.get("avg_profit", 0.0),
                "holding_avg": result.get("holding_avg", ""),
            },
            backtest_start=result.get("backtest_start"),
            backtest_end=result.get("backtest_end"),
            status="backtest",
            mission_id=mission_id,
            notes=notes,
        )
        exp_id = ledger.record_experiment(exp)
        logger.info("Experiment %d creat: %s pe %s", exp_id, exp.strategy, pair_str)

        # Scrie tranzacțiile individuale ca paper_trades (virtuale).
        trades = result.get("trades_detail", [])
        for trade in trades:
            pair = trade.get("pair", pair_str)
            side = "long" if trade.get("is_short") is not True else "short"
            entry_price = float(trade.get("open_rate", 0.0))
            exit_price = float(trade.get("close_rate", 0.0))
            amount = float(trade.get("stake_amount", 0.0))
            fee = float(trade.get("fee_open", 0.0)) + float(trade.get("fee_close", 0.0))

            if entry_price > 0 and amount > 0:
                trade_id = ledger.record_paper_trade(
                    pair=pair,
                    side=side,
                    entry_price=entry_price,
                    amount=amount,
                    experiment_id=exp_id,
                    fee=fee,
                    entry_ts=trade.get("open_date"),
                )
                if exit_price > 0:
                    ledger.close_paper_trade(
                        trade_id, exit_price=exit_price,
                        exit_ts=trade.get("close_date"),
                    )

        n_trades = len(trades)
        logger.info("  → %d paper_trades scrise pentru experiment %d", n_trades, exp_id)
        return exp_id
