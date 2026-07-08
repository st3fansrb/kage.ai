"""Bucla 2 — context zilnic (WP-T, Etapa 3.3).

Rulează 1×/zi: trage date de piață (via `MarketDataProvider`), clasifică regimul NON-LLM
(`regime.detect_regime`), scrie `daily_context.json` + un rând în `daily_context` (ledger).
Bucla 1 (execuție) citește `bias` prin `read_bias()` ca LIMITATOR de direcție.

LLM opțional (nu aici): ar putea rescrie `reasoning` într-un JSON mai natural, dar clasificarea
rămâne strict non-LLM (invariant). Fără date de preț (provider jos) NU suprascrie contextul
vechi — degradare grațioasă.

CLI: `python -m trading.daily_context [--pair BTC/USDT]`.
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
from typing import Optional

from trading.ledger import TradingLedger
from trading.market_data import MarketDataProvider
from trading.regime import detect_regime, RegimeResult, NEUTRAL

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DAILY_CONTEXT_PATH = _PROJECT_ROOT / "cache_db" / "daily_context.json"


def _today() -> str:
    return datetime.date.today().isoformat()


def build(
    provider: MarketDataProvider,
    pair: str = "BTC/USDT",
    ledger: Optional[TradingLedger] = None,
    context_path: Path = DAILY_CONTEXT_PATH,
    date: Optional[str] = None,
) -> Optional[RegimeResult]:
    """Construiește contextul zilei. Întoarce None dacă providerul e jos (fără preț)."""
    closes = provider.daily_closes(pair)
    if not closes:
        return None  # sursă indisponibilă — păstrăm contextul vechi, nu scriem zgomot

    funding = provider.funding_rate(pair)
    oi = provider.open_interest(pair)
    result = detect_regime(closes, funding=funding, open_interest=oi)

    day = date or _today()
    payload = {
        "date": day,
        "pair": pair,
        "regime": result.regime,
        "bias": result.bias,
        "confidence": result.confidence,
        "reasoning": result.reasoning,
        "features": result.features,
    }
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if ledger is not None:
        ledger.upsert_daily_context(
            date=day, regime=result.regime, bias=result.bias,
            confidence=result.confidence, reasoning=result.reasoning, features=result.features,
        )
    return result


def read_bias(context_path: Path = DAILY_CONTEXT_PATH) -> str:
    """Bias-ul curent pentru bucla de execuție (LIMITATOR). NEUTRAL dacă nu există context."""
    try:
        data = json.loads(context_path.read_text(encoding="utf-8"))
        return str(data.get("bias", NEUTRAL))
    except Exception:
        return NEUTRAL


def bias_allows(side: str, context_path: Path = DAILY_CONTEXT_PATH) -> bool:
    """True dacă bias-ul zilei permite deschiderea unei poziții pe direcția `side` (long/short).

    Contract pentru strategiile freqtrade: apelează în `populate_entry_trend` înainte de a marca
    o intrare. flat ⇒ nimic; long_only ⇒ doar long; short_only ⇒ doar short; neutral ⇒ ambele.
    """
    bias = read_bias(context_path)
    if bias == "flat":
        return False
    if bias == "long_only":
        return side == "long"
    if bias == "short_only":
        return side == "short"
    return True  # neutral


def run(pair: str = "BTC/USDT", provider: Optional[MarketDataProvider] = None,
        db_path: Optional[str] = None) -> Optional[dict]:
    provider = provider or MarketDataProvider(min_interval_s=0.3)
    ledger = TradingLedger(db_path) if db_path else TradingLedger()
    try:
        result = build(provider, pair=pair, ledger=ledger)
        if result is None:
            return None
        return {"regime": result.regime, "bias": result.bias,
                "confidence": result.confidence, "reasoning": result.reasoning}
    finally:
        ledger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bucla de context zilnic (regim + bias)")
    parser.add_argument("--pair", type=str, default="BTC/USDT")
    parser.add_argument("--db", type=str, default=None)
    args = parser.parse_args()
    out = run(pair=args.pair, db_path=args.db)
    print(json.dumps(out, ensure_ascii=False, indent=2) if out else "provider indisponibil (fără date)")
