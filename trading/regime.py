"""Regime detection NON-LLM (WP-T, Etapa 3.2 — invariant: fără LLM în clasificare).

Clasifică regimul pieței din prețuri zilnice: **trend** (preț vs EMA200) + **volatilitate
realizată** (vs distribuția ei istorică) → `{regime, bias, confidence}`. Bias-ul e un
LIMITATOR pentru bucla de execuție (ex. `short_only` ⇒ strategiile long nu deschid poziții).

Implementare rule-based, self-contained (numpy), deterministă și testabilă — fără dependințe
fragile. Upgrade viitor documentat: HMM (hmmlearn/statsmodels) pe volatilitate realizată, în
spatele ACELEIAȘI interfețe `detect_regime` (nu se schimbă consumatorii). Deocamdată regula
robustă e suficientă pentru limitatorul de direcție.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

# Bias-uri (contract cu bucla de execuție).
LONG_ONLY, SHORT_ONLY, NEUTRAL, FLAT = "long_only", "short_only", "neutral", "flat"


@dataclass
class RegimeResult:
    regime: str
    bias: str
    confidence: float
    reasoning: str
    features: dict = field(default_factory=dict)


def ema(values: Sequence[float], period: int) -> Optional[float]:
    """Ultima valoare EMA. None dacă sunt mai puține puncte decât `period`."""
    v = np.asarray(values, dtype=float)
    if v.size < period or period < 1:
        return None
    k = 2.0 / (period + 1.0)
    e = float(v[0])
    for x in v[1:]:
        e = x * k + e * (1.0 - k)
    return e


def log_returns(closes: Sequence[float]) -> np.ndarray:
    c = np.asarray(closes, dtype=float)
    c = c[c > 0]
    if c.size < 2:
        return np.array([])
    return np.diff(np.log(c))


def realized_volatility(closes: Sequence[float], window: int = 20) -> Optional[float]:
    """Volatilitate realizată = deviația standard a log-randamentelor pe ultima fereastră."""
    r = log_returns(closes)
    if r.size < window:
        return None
    return float(r[-window:].std(ddof=1))


def _rolling_vol_series(closes: Sequence[float], window: int) -> np.ndarray:
    r = log_returns(closes)
    if r.size < window:
        return np.array([])
    return np.array([r[i - window:i].std(ddof=1) for i in range(window, r.size + 1)])


def detect_regime(
    daily_closes: Sequence[float],
    vol_window: int = 20,
    trend_period: int = 200,
    funding: Optional[float] = None,
    open_interest: Optional[float] = None,
) -> RegimeResult:
    """Regim + bias din prețuri zilnice. Degradează la NEUTRAL/confidence mică dacă lipsesc date.

    - trend: preț curent vs EMA(trend_period) (sau EMA cea mai lungă disponibilă).
    - vol: volatilitatea realizată curentă vs percentila ei istorică (high >70, low <30).
    - `funding`/`open_interest` intră doar ca features/context (nu decid singure regimul aici).
    """
    c = np.asarray(daily_closes, dtype=float)
    c = c[np.isfinite(c) & (c > 0)]
    if c.size < vol_window + 2:
        return RegimeResult("unknown", NEUTRAL, 0.0, "date insuficiente",
                            {"n": int(c.size)})

    # Trend: folosește EMA200 dacă avem, altfel cea mai lungă EMA posibilă.
    period = trend_period if c.size >= trend_period else max(10, c.size // 2)
    ema_val = ema(c, period)
    price = float(c[-1])
    trend_gap = (price - ema_val) / ema_val if ema_val else 0.0   # >0 bull, <0 bear

    # Volatilitate: curentă vs distribuția istorică.
    cur_vol = realized_volatility(c, vol_window)
    vol_hist = _rolling_vol_series(c, vol_window)
    vol_pct = float((vol_hist < cur_vol).mean()) if vol_hist.size and cur_vol is not None else 0.5
    median_vol = float(np.median(vol_hist)) if vol_hist.size else 0.0
    # Spike real = vol curentă mult peste tipic (nu doar „percentila 90", care e 1-în-10 din zgomot).
    vol_ratio = (cur_vol / median_vol) if (median_vol > 0 and cur_vol is not None) else \
                (float("inf") if cur_vol and cur_vol > 0 else 1.0)

    trend_label = "bull" if trend_gap > 0.01 else "bear" if trend_gap < -0.01 else "side"
    vol_label = "high_vol" if vol_pct > 0.70 else "low_vol" if vol_pct < 0.30 else "mid_vol"
    regime = f"{trend_label}_{vol_label}"

    # Bias (limitatorul): spike de volatilitate ⇒ stai deoparte; altfel urmează trendul.
    if vol_ratio > 2.0:
        bias, why = FLAT, f"spike de volatilitate (×{vol_ratio:.1f} peste tipic) → flat"
    elif trend_label == "bull":
        bias, why = LONG_ONLY, "preț peste EMA — trend up → long_only"
    elif trend_label == "bear":
        bias, why = SHORT_ONLY, "preț sub EMA — trend down → short_only"
    else:
        bias, why = NEUTRAL, "fără trend clar → neutral"

    # Confidence: tăria trendului (distanța de EMA) temperată de incertitudinea de volatilitate.
    conf = min(1.0, abs(trend_gap) / 0.05) * (0.5 + 0.5 * abs(vol_pct - 0.5) * 2)
    conf = float(round(max(0.0, min(1.0, conf)), 3))

    features = {
        "price": round(price, 4),
        "ema_period": period,
        "trend_gap": round(trend_gap, 4),
        "realized_vol": round(cur_vol, 6) if cur_vol is not None else None,
        "vol_percentile": round(vol_pct, 3),
        "vol_ratio": round(vol_ratio, 3) if vol_ratio != float("inf") else None,
        "funding": funding,
        "open_interest": open_interest,
    }
    return RegimeResult(regime, bias, conf, why, features)
