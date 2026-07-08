"""Ingestie date de piață (WP-T, Etapa 3.1).

Funding rate + open interest + klines zilnice de la **Binance public** (gratis, fără chei).
Provider cu **circuit breaker** (pattern-ul Ollama din Kage): un endpoint căzut marchează
sursa indisponibilă (întoarce None), NU omoară pipeline-ul. Rate-limiting politicos.

Fetcher-ul HTTP e injectabil → testabil fără rețea. Simboluri Binance: `BTC/USDT` → `BTCUSDT`.
Alternativă viitoare (Coinalyze) în spatele aceleiași interfețe.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Callable, Optional

_BINANCE_SPOT = "https://api.binance.com"
_BINANCE_FAPI = "https://fapi.binance.com"

Fetcher = Callable[[str], Any]


def _http_get_json(url: str, timeout: int = 10) -> Any:
    """Fetcher implicit (stdlib urllib). Ridică la eroare de rețea/parse."""
    req = urllib.request.Request(url, headers={"User-Agent": "kage-trading/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class CircuitBreaker:
    """Deschide circuitul după `threshold` eșecuri consecutive; se închide la primul succes."""

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self.failures = 0

    @property
    def is_open(self) -> bool:
        return self.failures >= self.threshold

    def record_success(self) -> None:
        self.failures = 0

    def record_failure(self) -> None:
        self.failures += 1


def _binance_symbol(pair: str) -> str:
    return pair.replace("/", "").upper()


class MarketDataProvider:
    """Sursă de date derivate cu circuit breaker. Toate metodele întorc None dacă sursa e jos."""

    def __init__(self, fetcher: Fetcher = _http_get_json, breaker_threshold: int = 3,
                 min_interval_s: float = 0.0):
        self._fetch = fetcher
        self.breaker = CircuitBreaker(breaker_threshold)
        self.min_interval_s = min_interval_s
        self._last_call = 0.0

    @property
    def available(self) -> bool:
        return not self.breaker.is_open

    def _guarded(self, url: str) -> Optional[Any]:
        if self.breaker.is_open:
            return None
        if self.min_interval_s > 0:
            dt = time.monotonic() - self._last_call
            if dt < self.min_interval_s:
                time.sleep(self.min_interval_s - dt)
        try:
            data = self._fetch(url)
            self.breaker.record_success()
            self._last_call = time.monotonic()
            return data
        except Exception:
            self.breaker.record_failure()
            return None

    def daily_closes(self, pair: str, limit: int = 250) -> Optional[list[float]]:
        """Prețuri de închidere zilnice (spot klines 1d). None dacă sursa e jos."""
        sym = _binance_symbol(pair)
        url = f"{_BINANCE_SPOT}/api/v3/klines?symbol={sym}&interval=1d&limit={int(limit)}"
        data = self._guarded(url)
        if not isinstance(data, list) or not data:
            return None
        try:
            return [float(row[4]) for row in data]     # index 4 = close
        except (IndexError, ValueError, TypeError):
            return None

    def funding_rate(self, pair: str) -> Optional[float]:
        """Ultimul funding rate (futures). None dacă sursa e jos."""
        sym = _binance_symbol(pair)
        url = f"{_BINANCE_FAPI}/fapi/v1/fundingRate?symbol={sym}&limit=1"
        data = self._guarded(url)
        if not isinstance(data, list) or not data:
            return None
        try:
            return float(data[-1]["fundingRate"])
        except (KeyError, ValueError, TypeError):
            return None

    def open_interest(self, pair: str) -> Optional[float]:
        """Open interest curent (futures). None dacă sursa e jos."""
        sym = _binance_symbol(pair)
        url = f"{_BINANCE_FAPI}/fapi/v1/openInterest?symbol={sym}"
        data = self._guarded(url)
        if not isinstance(data, dict):
            return None
        try:
            return float(data["openInterest"])
        except (KeyError, ValueError, TypeError):
            return None
