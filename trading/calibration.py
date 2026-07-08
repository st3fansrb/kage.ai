"""Calibrare (WP-T, Etapa 4.2). Job periodic peste `predictions` rezolvate.

Onestitatea unei predicții nu e „a nimerit?", ci **cât de bine calibrate sunt probabilitățile
și intervalele**: din toate dățile când ai zis „80%", s-a întâmplat ~80%? Intervalele de 80%
acoperă ~80% din realizări?

- **Brier score** pentru predicții probabilistice: media (prob − rezultat)². Mai mic = mai bine.
- **Coverage** pentru intervale: fracția de realizări în intervalul 80% prezis (ideal ~0.80).

Realized: `{"occurred": 0|1}` pentru 'prob'; `{"value": float}` pentru 'point'.
Pur — fără LLM, fără ledger în funcțiile de bază (testabile izolat).
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from trading.ledger import TradingLedger


def brier_score(prob_outcome_pairs: Sequence[tuple[float, int]]) -> Optional[float]:
    """Media (prob − rezultat)² pe predicții binare. None dacă nu sunt date."""
    if not prob_outcome_pairs:
        return None
    arr = np.asarray(prob_outcome_pairs, dtype=float)
    return float(np.mean((arr[:, 0] - arr[:, 1]) ** 2))


def interval_coverage(value_interval_pairs: Sequence[tuple[float, Sequence[float]]]) -> Optional[float]:
    """Fracția de valori realizate în intervalul [lo, hi] prezis. Ideal ~0.80 pentru intervale 80%."""
    if not value_interval_pairs:
        return None
    hits = 0
    for value, (lo, hi) in value_interval_pairs:
        if lo <= value <= hi:
            hits += 1
    return float(hits / len(value_interval_pairs))


def _gather(ledger: TradingLedger) -> tuple[list, list]:
    """Din predicțiile rezolvate: perechi (prob, occurred) și (value, interval)."""
    probs: list[tuple[float, int]] = []
    intervals: list[tuple[float, list]] = []
    for p in ledger.get_predictions(resolved=True, limit=100000):
        pred, real = p.get("predicted") or {}, p.get("realized") or {}
        if pred.get("kind") == "prob" and "occurred" in real and "prob" in pred:
            probs.append((float(pred["prob"]), int(real["occurred"])))
        elif pred.get("kind") == "point" and "value" in real and "interval_80" in pred:
            iv = pred["interval_80"]
            if isinstance(iv, (list, tuple)) and len(iv) == 2:
                intervals.append((float(real["value"]), [float(iv[0]), float(iv[1])]))
    return probs, intervals


def calibration_report(ledger: TradingLedger) -> str:
    """Raport text de calibrare peste `predictions` rezolvate."""
    probs, intervals = _gather(ledger)
    bs = brier_score(probs)
    cov = interval_coverage(intervals)
    lines = [
        "CALIBRARE — predicții rezolvate",
        "=" * 50,
        f"probabilistice: {len(probs)} · Brier = {bs:.4f}" if bs is not None
        else "probabilistice: 0 (fără date)",
        f"intervale 80%: {len(intervals)} · coverage = {cov:.3f} "
        f"(ideal ~0.80)" if cov is not None else "intervale 80%: 0 (fără date)",
    ]
    if cov is not None:
        if cov < 0.65:
            lines.append("→ intervale prea ÎNGUSTE (supraîncredere): realizările ies des din interval")
        elif cov > 0.92:
            lines.append("→ intervale prea LARGI (subîncredere): aproape orice încape")
        else:
            lines.append("→ intervale rezonabil calibrate")
    return "\n".join(lines)


def run(db_path: Optional[str] = None) -> str:
    ledger = TradingLedger(db_path) if db_path else TradingLedger()
    try:
        return calibration_report(ledger)
    finally:
        ledger.close()


if __name__ == "__main__":
    print(run())
