"""Teste WP-T Etapa 3: regime detection + market data (circuit breaker) + daily context.

Fără rețea: fetcher-ul HTTP / providerul sunt injectate.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from trading.ledger import TradingLedger
from trading.market_data import MarketDataProvider, CircuitBreaker
from trading.regime import detect_regime, ema, realized_volatility, LONG_ONLY, SHORT_ONLY, NEUTRAL, FLAT
from trading import daily_context as dc


# ── regime ────────────────────────────────────────────────────────────────────
def _series(drift, vol, n=250, seed=0, start=100.0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, size=n)
    return list(start * np.exp(np.cumsum(rets)))


def test_ema_and_vol_basics():
    assert ema([1, 2, 3], 5) is None                 # prea puține puncte
    assert ema([1, 1, 1, 1, 1], 3) == pytest.approx(1.0)
    assert realized_volatility([100] * 5, 20) is None


def test_regime_bull_is_long_only():
    r = detect_regime(_series(0.004, 0.01, seed=1))   # drift pozitiv clar
    assert r.bias == LONG_ONLY and r.regime.startswith("bull")
    assert 0.0 <= r.confidence <= 1.0


def test_regime_bear_is_short_only():
    r = detect_regime(_series(-0.004, 0.01, seed=2))
    assert r.bias == SHORT_ONLY and r.regime.startswith("bear")


def test_regime_insufficient_data():
    r = detect_regime([100, 101, 102])
    assert r.regime == "unknown" and r.bias == NEUTRAL and r.confidence == 0.0


def test_regime_extreme_vol_is_flat():
    # preț plat lung, apoi un șoc de volatilitate uriaș la final → percentila vol ~1 → flat
    closes = [100.0] * 60
    rng = np.random.default_rng(3)
    closes += list(100 * np.exp(np.cumsum(rng.normal(0, 0.15, size=40))))
    r = detect_regime(closes, vol_window=20)
    assert r.bias == FLAT


# ── market data + circuit breaker ─────────────────────────────────────────────
def _fake_klines(closes):
    # rând freqtrade/binance: [open_time, o, h, l, close, ...]
    return [[0, "0", "0", "0", str(c), "0"] for c in closes]


def test_daily_closes_parsing():
    prov = MarketDataProvider(fetcher=lambda url: _fake_klines([1.0, 2.0, 3.0]))
    assert prov.daily_closes("BTC/USDT") == [1.0, 2.0, 3.0]


def test_circuit_breaker_opens_and_returns_none():
    def boom(url):
        raise ConnectionError("down")
    prov = MarketDataProvider(fetcher=boom, breaker_threshold=3)
    for _ in range(3):
        assert prov.daily_closes("BTC/USDT") is None
    assert prov.available is False                    # circuit deschis după 3 eșecuri
    assert prov.funding_rate("BTC/USDT") is None       # nu mai încearcă / nu crapă


def test_circuit_breaker_resets_on_success():
    cb = CircuitBreaker(threshold=2)
    cb.record_failure(); cb.record_failure()
    assert cb.is_open
    cb.record_success()
    assert not cb.is_open


class _FakeProvider:
    def __init__(self, closes, funding=0.0001, oi=1234.0):
        self._closes, self._f, self._oi = closes, funding, oi
    def daily_closes(self, pair, limit=250): return self._closes
    def funding_rate(self, pair): return self._f
    def open_interest(self, pair): return self._oi


# ── daily context ─────────────────────────────────────────────────────────────
def test_build_writes_json_and_ledger(tmp_path):
    lg = TradingLedger(db_path=tmp_path / "trading.db")
    ctx_path = tmp_path / "daily_context.json"
    prov = _FakeProvider(_series(0.004, 0.01, seed=1))
    res = dc.build(prov, pair="BTC/USDT", ledger=lg, context_path=ctx_path, date="2026-07-08")
    assert res is not None and res.bias == LONG_ONLY
    saved = json.loads(ctx_path.read_text(encoding="utf-8"))
    assert saved["bias"] == LONG_ONLY and saved["date"] == "2026-07-08"
    row = lg.get_daily_context("2026-07-08")
    assert row["bias"] == LONG_ONLY and "realized_vol" in row["features"]
    lg.close()


def test_build_provider_down_no_write(tmp_path):
    ctx_path = tmp_path / "daily_context.json"
    prov = _FakeProvider(None)                         # sursă jos
    assert dc.build(prov, context_path=ctx_path) is None
    assert not ctx_path.exists()                        # nu suprascrie cu zgomot


def test_bias_allows(tmp_path):
    ctx_path = tmp_path / "daily_context.json"
    ctx_path.write_text(json.dumps({"bias": "long_only"}), encoding="utf-8")
    assert dc.read_bias(ctx_path) == "long_only"
    assert dc.bias_allows("long", ctx_path) is True
    assert dc.bias_allows("short", ctx_path) is False
    # fără fișier → neutral → ambele permise
    assert dc.bias_allows("short", tmp_path / "nope.json") is True
