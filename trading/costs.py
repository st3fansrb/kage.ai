"""Costuri stresate (WP-T, invariant #4).

„Orice backtest care ajunge la validare rulează cu fees reale + slippage DUBLAT." freqtrade
modelează fee-ul, dar NU slippage-ul → îl adăugăm noi, post-hoc, ca penalizare per tranzacție.
O strategie care nu supraviețuiește costurilor stresate nu trebuie să arate cifre optimiste la
validare. Model conservator, determinist, zero LLM.

Penalizarea per tranzacție (round-trip) = 2 × (slippage_bps / 10_000) × mult, aplicată pe
noționalul poziției (`amount` = stake în quote currency din freqtrade). Se scade din PnL.
"""

from __future__ import annotations

from typing import Optional, Sequence

DEFAULT_SLIPPAGE_BPS = 5.0   # 5 bps per latură (nominal, tipic pe BTC/ETH lichid)
STRESS_MULT = 2.0            # slippage DUBLAT (invariant #4)


def roundtrip_penalty_frac(slippage_bps: float = DEFAULT_SLIPPAGE_BPS, mult: float = STRESS_MULT) -> float:
    """Fracția de noțional pierdută pe un round-trip la costuri stresate (entry + exit)."""
    return 2.0 * (float(slippage_bps) / 10_000.0) * float(mult)


def stress_pnls(
    trades: Sequence[dict],
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    mult: float = STRESS_MULT,
) -> list[float]:
    """PnL-urile tranzacțiilor închise, penalizate cu slippage stresat pe noțional.

    Așteaptă rânduri `paper_trades` (chei `pnl`, `amount`). Ignoră trade-urile fără pnl.
    """
    frac = roundtrip_penalty_frac(slippage_bps, mult)
    out: list[float] = []
    for t in trades:
        pnl = t.get("pnl")
        if pnl is None:
            continue
        notional = abs(float(t.get("amount") or 0.0))
        out.append(float(pnl) - notional * frac)
    return out


def stress_returns(
    returns: Sequence[float],
    notionals: Optional[Sequence[float]] = None,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    mult: float = STRESS_MULT,
) -> list[float]:
    """Variantă pe serii aliniate: penalizează fiecare return cu slippage stresat.

    Dacă `notionals` lipsește, presupune return-uri procentuale (penalizare = fracția direct).
    """
    frac = roundtrip_penalty_frac(slippage_bps, mult)
    if notionals is None:
        return [float(r) - frac for r in returns]
    return [float(r) - abs(float(n)) * frac for r, n in zip(returns, notionals)]
