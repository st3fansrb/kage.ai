"""Registrul de ipoteze cu PRE-REGISTRATION (WP-T, Etapa 4.1 — invariant #2).

O ipoteză există doar dacă a fost scrisă în DB *înainte* de a putea fi verificată. O predicție
se poate emite DOAR sub o ipoteză deja pre-înregistrată, iar momentul semnalului trebuie să fie
ULTERIOR pre-înregistrării (gardă temporală contra storytelling-ului post-hoc).

Structura obligatorie a unei predicții (falsificabilă, cuantificată):
- punct: `{"kind": "point", "mean": float, "interval_80": [lo, hi], "horizon": "48h"}`
- probabilistic: `{"kind": "prob", "prob": 0..1, "horizon": "7d"}`
"""

from __future__ import annotations

import time
from typing import Optional

from trading.ledger import TradingLedger


class PreRegistrationError(RuntimeError):
    """Încălcare a invariantului #2 (predicție fără ipoteză pre-înregistrată valid temporal)."""


class HypothesisFormatError(ValueError):
    """Ipoteză/predicție fără structura falsificabilă cerută."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _validate_prediction(pred: dict) -> None:
    if not isinstance(pred, dict) or "horizon" not in pred:
        raise HypothesisFormatError("predicția cere cel puțin 'horizon'")
    kind = pred.get("kind")
    if kind == "point":
        iv = pred.get("interval_80")
        if "mean" not in pred or not (isinstance(iv, (list, tuple)) and len(iv) == 2):
            raise HypothesisFormatError("predicție 'point' cere 'mean' + 'interval_80'=[lo,hi]")
    elif kind == "prob":
        p = pred.get("prob")
        if not isinstance(p, (int, float)) or not (0.0 <= p <= 1.0):
            raise HypothesisFormatError("predicție 'prob' cere 'prob' ∈ [0,1]")
    else:
        raise HypothesisFormatError("predicția cere 'kind' ∈ {'point','prob'}")


def register_hypothesis(
    ledger: TradingLedger,
    mechanism: str,
    prediction: dict,
    falsification: str,
    regime_at_creation: Optional[str] = None,
    source_model: Optional[str] = None,
) -> int:
    """Pre-înregistrează o ipoteză. `pre_registered_at` = acum (gardă temporală)."""
    if not mechanism.strip():
        raise HypothesisFormatError("ipoteza cere un mecanism cauzal ne-vid")
    if not falsification.strip():
        raise HypothesisFormatError("ipoteza cere un criteriu de falsificare")
    _validate_prediction(prediction)
    return ledger.insert_hypothesis(
        mechanism=mechanism, prediction=prediction, falsification=falsification,
        pre_registered_at=_now(), regime_at_creation=regime_at_creation, source_model=source_model,
    )


def record_prediction(
    ledger: TradingLedger,
    hypothesis_id: int,
    signal_ts: str,
    predicted: dict,
    horizon: Optional[str] = None,
) -> int:
    """Emite o predicție sub o ipoteză pre-înregistrată. Refuză dacă:
    - ipoteza nu există (invariant #2), sau
    - semnalul e ANTERIOR pre-înregistrării (nu poți „prezice" retroactiv).
    """
    hyp = ledger.get_hypothesis(hypothesis_id)
    if hyp is None:
        raise PreRegistrationError(
            f"ipoteza {hypothesis_id} nu e pre-înregistrată — predicție refuzată (invariant #2)")
    if signal_ts < hyp["pre_registered_at"]:
        raise PreRegistrationError(
            f"semnal {signal_ts} anterior pre-înregistrării {hyp['pre_registered_at']} — "
            "storytelling post-hoc refuzat")
    return ledger.insert_prediction(hypothesis_id, signal_ts, predicted, horizon=horizon)


def resolve(ledger: TradingLedger, prediction_id: int, realized: dict) -> None:
    """Completează rezultatul realizat al unei predicții (pentru calibrare)."""
    ledger.resolve_prediction(prediction_id, realized)


def mark_hypothesis(ledger: TradingLedger, hypothesis_id: int, status: str) -> None:
    """confirmed | falsified | expired — decizie pe baza calibrării, nu a LLM-ului."""
    if status not in ("open", "confirmed", "falsified", "expired"):
        raise ValueError(f"status invalid: {status}")
    ledger.update_hypothesis_status(hypothesis_id, status)
