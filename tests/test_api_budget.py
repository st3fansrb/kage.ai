"""#7 Budget v2 — plafonul-gate global pe bani reali (`api_budget.SpendGate`).

Contractul: fail-closed (implicit NU se cheltuie), modelele gratuite trec mereu,
plafoanele lunar/zilnic sunt garduri hard. Integrarea cu ledger-ul real de costuri
(Criticul nocturn) a plecat în kage-labs.
"""

import pytest

from api_budget import DEFAULT_DAILY_CAP_EUR, DEFAULT_MONTHLY_CAP_EUR, SpendGate


class _FakeLedger:
    """Orice obiect cu `api_cost_sum(month)` + `api_cost_sum_day(day)` merge ca ledger."""

    def __init__(self, month_usd: float = 0.0, day_usd: float = 0.0):
        self.month_usd = month_usd
        self.day_usd = day_usd

    def api_cost_sum(self, month=None) -> float:
        return self.month_usd

    def api_cost_sum_day(self, day) -> float:
        return self.day_usd


@pytest.fixture
def ledger():
    return _FakeLedger()


# ── semantica gate-ului ──────────────────────────────────────────────────────

def test_gate_disabled_blocks_paid():
    d = SpendGate(enabled=False).allows()
    assert not d.allowed and d.reason == "disabled"


def test_gate_disabled_allows_free():
    assert SpendGate(enabled=False).allows(free=True).allowed


def test_gate_enabled_under_caps_allows():
    g = SpendGate(enabled=True, month_usd_fn=lambda: 0.5, day_usd_fn=lambda: 0.01)
    assert g.allows().allowed


def test_gate_monthly_cap_blocks():
    g = SpendGate(enabled=True, monthly_cap_eur=10.0, eur_usd=1.0,
                  month_usd_fn=lambda: 10.0, day_usd_fn=lambda: 0.0)
    d = g.allows()
    assert not d.allowed and d.reason == "monthly_cap"


def test_gate_daily_cap_blocks():
    g = SpendGate(enabled=True, daily_cap_eur=1.0, eur_usd=1.0,
                  month_usd_fn=lambda: 2.0, day_usd_fn=lambda: 1.0)
    d = g.allows()
    assert not d.allowed and d.reason == "daily_cap"


def test_gate_estimated_cost_counts():
    """Un apel care AR depăși plafonul e refuzat preventiv (est_usd intră în sumă)."""
    g = SpendGate(enabled=True, monthly_cap_eur=10.0, eur_usd=1.0,
                  month_usd_fn=lambda: 9.5, day_usd_fn=lambda: 0.0)
    assert g.allows(est_usd=0.1).allowed
    assert g.allows(est_usd=0.6).reason == "monthly_cap"


def test_gate_spend_read_error_fails_closed():
    def boom():
        raise RuntimeError("db picat")
    g = SpendGate(enabled=True, month_usd_fn=boom, day_usd_fn=boom)
    d = g.allows()
    assert not d.allowed and d.reason == "error"


def test_gate_eur_conversion():
    """Plafoanele sunt în EUR; cheltuielile (USD) se convertesc cu eur_usd_rate."""
    g = SpendGate(enabled=True, monthly_cap_eur=9.0, eur_usd=0.9,
                  month_usd_fn=lambda: 10.0, day_usd_fn=lambda: 0.0)  # 10$ × 0.9 = 9€ = plafon
    assert g.allows().reason == "monthly_cap"


# ── from_config: fail-closed + citirea din ledger ────────────────────────────

def test_from_config_missing_section_is_disabled():
    g = SpendGate.from_config({})
    assert g.enabled is False
    assert g.monthly_cap_eur == DEFAULT_MONTHLY_CAP_EUR
    assert g.daily_cap_eur == DEFAULT_DAILY_CAP_EUR
    assert g.allows().reason == "disabled"


def test_from_config_reads_ledger_sums():
    cfg = {"eur_usd_rate": 1.0,
           "api_budget": {"enabled": True, "monthly_cap_eur": 2.5, "daily_cap_eur": 5}}
    d = SpendGate.from_config(cfg, _FakeLedger(month_usd=3.0, day_usd=3.0)).allows()
    assert not d.allowed and d.reason == "monthly_cap"


def test_from_config_without_ledger_counts_zero_spend():
    """Fără ledger (cum e acum în orchestrator), cheltuielile sunt 0 și decid doar switch-ul și plafoanele."""
    cfg = {"api_budget": {"enabled": True, "monthly_cap_eur": 10, "daily_cap_eur": 1}}
    st = SpendGate.from_config(cfg).status()
    assert st["enabled"] is True
    assert st["spend_month_eur"] == 0.0
    assert SpendGate.from_config(cfg).allows().allowed


def test_gate_status_shape(ledger):
    st = SpendGate.from_config({"api_budget": {"enabled": False}}, ledger).status()
    assert st["enabled"] is False
    assert st["blocked_reason"] == "disabled"
    assert st["spend_month_eur"] == 0.0
    assert st["monthly_cap_eur"] == DEFAULT_MONTHLY_CAP_EUR
