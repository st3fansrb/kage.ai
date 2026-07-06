import orchestrator as orch
from orchestrator import _usd_to_eur, EUR_USD_RATE


def test_none_returns_none():
    assert _usd_to_eur(None) is None


def test_explicit_rate():
    assert _usd_to_eur(1.0, 0.9) == 0.9


def test_default_rate():
    assert _usd_to_eur(2.0) == round(2.0 * EUR_USD_RATE, 4)
