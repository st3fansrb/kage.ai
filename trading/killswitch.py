"""Kill-switch determinist (WP-T, Etapa 2 — invariant #6).

Pur Python, ZERO LLM, independent de restul sistemului. Rulat pe cron (5 min): citește
ledger-ul, calculează drawdown-ul global pe equity-ul paper și, dacă depășește pragul
(default −15%), declanșează halt-ul („flat everything") + notificare Telegram. Bucla de
execuție (freqtrade) citește `killswitch.halted` și nu mai deschide poziții.

Ridicarea halt-ului e MANUALĂ (decizie umană), niciodată automată — `clear_killswitch()`.

CLI: `python -m trading.killswitch [--threshold -0.15] [--wallet 1000] [--check-only]`.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Sequence

from trading.ledger import TradingLedger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_KAGE_CONFIG = _PROJECT_ROOT / "kage_config.json"

DEFAULT_THRESHOLD = -0.15   # −15% drawdown pe equity paper
DEFAULT_WALLET = 1000.0     # bază (dry_run_wallet)


def _trading_cfg() -> dict:
    try:
        return json.loads(_KAGE_CONFIG.read_text(encoding="utf-8")).get("trading", {})
    except Exception:
        return {}


def default_wallet() -> float:
    return float(_trading_cfg().get("dry_run_wallet", DEFAULT_WALLET))


# ── drawdown pe equity-ul paper ───────────────────────────────────────────────
def _closed_trades_sorted(ledger: TradingLedger) -> list[dict]:
    """Toate tranzacțiile închise, în ordinea închiderii (exit_ts)."""
    trades = [t for t in ledger.get_paper_trades(experiment_id=None, limit=1_000_000)
              if t.get("status") == "closed" and t.get("pnl") is not None]
    return sorted(trades, key=lambda t: (t.get("exit_ts") or t.get("created_at") or ""))


def equity_curve(ledger: TradingLedger, wallet: Optional[float] = None) -> list[float]:
    """Curba de equity = wallet + PnL cumulat pe tranzacțiile închise, în ordine cronologică."""
    base = default_wallet() if wallet is None else float(wallet)
    eq = base
    curve = [eq]
    for t in _closed_trades_sorted(ledger):
        eq += float(t["pnl"])
        curve.append(eq)
    return curve


def current_drawdown(ledger: TradingLedger, wallet: Optional[float] = None) -> float:
    """Drawdown-ul curent (minim relativ peak→now) pe equity paper. ≤ 0. 0 dacă fără date."""
    curve = equity_curve(ledger, wallet)
    peak = curve[0]
    worst = 0.0
    for eq in curve:
        peak = max(peak, eq)
        if peak > 0:
            worst = min(worst, (eq - peak) / peak)
    return float(worst)


# ── notificare (injectabilă) ──────────────────────────────────────────────────
def _default_notify(msg: str) -> None:
    """Trimite pe Telegram via API direct (stdlib urllib). Sare tăcut dacă nu e configurat."""
    cfg = {}
    try:
        cfg = json.loads(_KAGE_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return
    token, chat_id = cfg.get("telegram_bot_token"), cfg.get("telegram_chat_id")
    if not token or not chat_id or token == "CHANGEME":
        return
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10
        )
    except Exception:
        pass


# ── verificare + declanșare ───────────────────────────────────────────────────
def check(
    ledger: TradingLedger,
    threshold: float = DEFAULT_THRESHOLD,
    wallet: Optional[float] = None,
    notify: Optional[Callable[[str], None]] = None,
) -> dict:
    """Verifică drawdown-ul; declanșează halt-ul dacă ≤ prag și nu e deja declanșat.

    Idempotent: dacă e deja halted, nu re-notifică. Întoarce starea pentru logging/CLI.
    """
    notify = notify or _default_notify
    dd = current_drawdown(ledger, wallet)
    already = ledger.is_halted()
    tripped_now = dd <= threshold and not already

    if tripped_now:
        reason = f"drawdown {dd*100:.1f}% ≤ prag {threshold*100:.1f}%"
        ledger.trip_killswitch(reason=reason, drawdown=dd)
        notify(f"🛑 <b>KILL-SWITCH TRADING</b>\n{reason}\nFlat everything. Ridicare = manuală.")

    return {
        "drawdown": dd,
        "threshold": threshold,
        "tripped_now": tripped_now,
        "halted": ledger.is_halted(),
    }


# ── decay de alocare (heuristică simplă; alocarea reală vine cu bucla de execuție) ─
def decay_candidates(ledger: TradingLedger, min_trades: int = 10) -> list[str]:
    """Strategii de dezalocat: profit total paper negativ pe eșantion suficient.

    Simplificat până există bucla de execuție cu alocări reale: semnalează strategiile care
    pierd bani virtual, pentru a le duce alocarea spre zero.
    """
    per_strategy: dict[str, list[float]] = {}
    for e in ledger.get_experiments(limit=1000):
        pnls = [t["pnl"] for t in ledger.get_paper_trades(experiment_id=e["id"], limit=1_000_000)
                if t.get("status") == "closed" and t.get("pnl") is not None]
        per_strategy.setdefault(e["strategy"], []).extend(pnls)
    return [s for s, pnls in per_strategy.items() if len(pnls) >= min_trades and sum(pnls) < 0]


def run(threshold: float = DEFAULT_THRESHOLD, wallet: Optional[float] = None,
        check_only: bool = False, db_path: Optional[str] = None) -> dict:
    ledger = TradingLedger(db_path) if db_path else TradingLedger()
    try:
        if check_only:
            return {"drawdown": current_drawdown(ledger, wallet), "halted": ledger.is_halted()}
        return check(ledger, threshold=threshold, wallet=wallet)
    finally:
        ledger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kill-switch determinist trading")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--wallet", type=float, default=None)
    parser.add_argument("--check-only", action="store_true", help="doar raportează, nu declanșează")
    parser.add_argument("--db", type=str, default=None)
    args = parser.parse_args()
    print(run(threshold=args.threshold, wallet=args.wallet,
              check_only=args.check_only, db_path=args.db))
