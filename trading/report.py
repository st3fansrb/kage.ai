"""Raport de verdict peste ledger-ul real (WP-T, Etapa 1.2).

Rulează modulul de validare (`validation.py`) peste toate experimentele din `trading.db` și
răspunde onest: *ce a produs bucla până acum e SEMNAL sau ZGOMOT?*

Seria de „returns" a unui experiment = PnL-urile tranzacțiilor VIRTUALE închise. Contorul de
trial-uri (pentru deflatarea DSR) = Sharpe-urile tuturor experimentelor cu destule trade-uri.
(Contorul global riguros — inclusiv eșecurile — vine în Etapa 2.)

CLI: `python -m trading.report` (opțional `--min-trades N`, `--db cale`).
"""

from __future__ import annotations

import argparse
from typing import Optional, Sequence

from trading.costs import stress_pnls
from trading.ledger import TradingLedger
from trading.validation import Verdict, classify, sharpe_ratio


def _closed_pnls(ledger: TradingLedger, experiment_id: int, stressed: bool = False) -> list[float]:
    """PnL-urile tranzacțiilor închise ale unui experiment (seria de returns).

    `stressed=True` aplică costuri stresate (invariant #4: slippage dublat pe noțional).
    """
    trades = [t for t in ledger.get_paper_trades(experiment_id=experiment_id, limit=1_000_000)
              if t.get("status") == "closed" and t.get("pnl") is not None]
    if stressed:
        return stress_pnls(trades)
    return [t["pnl"] for t in trades]


def collect_trial_sharpes(
    ledger: TradingLedger, experiments: Sequence[dict], stressed: bool = False
) -> list[float]:
    """Sharpe-urile tuturor experimentelor cu serie calculabilă = populația de trial-uri."""
    sharpes: list[float] = []
    for e in experiments:
        sr = sharpe_ratio(_closed_pnls(ledger, e["id"], stressed=stressed))
        if sr is not None:
            sharpes.append(sr)
    return sharpes


def verdicts_for_ledger(
    ledger: TradingLedger, min_trades: int = 20, stressed: bool = False
) -> list[Verdict]:
    """Verdict SEMNAL/ZGOMOT/INSUFICIENT pentru fiecare experiment din ledger.

    N pentru deflatarea DSR = contorul GLOBAL de trial-uri (invariant #3) dacă e populat;
    altfel fallback pe numărul de experimente cu Sharpe calculabil. `stressed=True` rulează
    verdictul pe PnL cu costuri stresate (invariant #4).
    """
    experiments = ledger.get_experiments(limit=1000)
    trial_sharpes = collect_trial_sharpes(ledger, experiments, stressed=stressed)
    global_trials = ledger.count_trials()
    n_trials = global_trials if global_trials >= len(trial_sharpes) and global_trials > 0 else None
    out: list[Verdict] = []
    for e in experiments:
        pnls = _closed_pnls(ledger, e["id"], stressed=stressed)
        out.append(classify(
            returns=pnls, trial_sharpes=trial_sharpes, pnls=pnls,
            experiment_id=e["id"], strategy=e["strategy"], min_trades=min_trades,
            n_trials=n_trials,
        ))
    return out


def _fmt(x: Optional[float], prec: int = 3) -> str:
    return f"{x:.{prec}f}" if isinstance(x, (int, float)) else "—"


def format_report(verdicts: Sequence[Verdict], n_trials: int) -> str:
    """Tabel text lizibil + sumar. n_trials = mărimea populației de deflatare."""
    lines = [
        f"VERDICT VALIDARE — {len(verdicts)} experimente · deflatare pe {n_trials} trial-uri",
        "=" * 78,
        f"{'id':>3}  {'strategie':<22} {'trades':>6} {'sharpe':>7} {'DSR':>6} {'perm_p':>7}  verdict",
        "-" * 78,
    ]
    counts = {"SEMNAL": 0, "ZGOMOT": 0, "INSUFICIENT": 0}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
        lines.append(
            f"{(v.experiment_id or 0):>3}  {v.strategy[:22]:<22} {v.n_trades:>6} "
            f"{_fmt(v.sharpe):>7} {_fmt(v.dsr):>6} {_fmt(v.permutation_p):>7}  {v.verdict}"
        )
    lines += [
        "-" * 78,
        f"SEMNAL={counts.get('SEMNAL',0)}  ZGOMOT={counts.get('ZGOMOT',0)}  "
        f"INSUFICIENT={counts.get('INSUFICIENT',0)}",
    ]
    signals = [v for v in verdicts if v.verdict == "SEMNAL"]
    if signals:
        lines.append("Candidați de INVESTIGAT (nu promovare automată):")
        for v in signals:
            lines.append(f"  · exp {v.experiment_id} ({v.strategy}): " + "; ".join(v.reasons))
    else:
        lines.append("Niciun semnal care să supraviețuiască deflatării — consistent cu teza: "
                     "backtests naive = zgomot.")
    return "\n".join(lines)


def run(db_path: Optional[str] = None, min_trades: int = 20, stressed: bool = False) -> str:
    ledger = TradingLedger(db_path) if db_path else TradingLedger()
    try:
        experiments = ledger.get_experiments(limit=1000)
        n_trials = len(collect_trial_sharpes(ledger, experiments, stressed=stressed))
        verdicts = verdicts_for_ledger(ledger, min_trades=min_trades, stressed=stressed)
        return format_report(verdicts, n_trials)
    finally:
        ledger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Raport de verdict peste trading.db")
    parser.add_argument("--db", type=str, default=None, help="cale trading.db (default: cache_db/)")
    parser.add_argument("--min-trades", type=int, default=20, help="prag minim de trade-uri")
    parser.add_argument("--stressed", action="store_true",
                        help="rulează verdictul pe PnL cu costuri stresate (slippage dublat)")
    args = parser.parse_args()
    print(run(db_path=args.db, min_trades=args.min_trades, stressed=args.stressed))
