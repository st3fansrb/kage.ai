"""#7 Budget v2 — plafonul-gate global pe bani reali (`api_budget.SpendGate`).

Contractul: fail-closed (implicit NU se cheltuie), modelele gratuite trec mereu,
plafoanele lunar/zilnic sunt garduri hard, iar pipeline-ul de trading ține Criticul
pe local când gate-ul refuză (bucla nu moare, doar nu cheltuie).
"""

import json
import time

import pytest

from api_budget import DEFAULT_DAILY_CAP_EUR, DEFAULT_MONTHLY_CAP_EUR, SpendGate
from trading.budget import ApiBudget
from trading.ledger import TradingLedger
from trading.pipeline import NightlyPipeline


@pytest.fixture
def ledger(tmp_path):
    return TradingLedger(tmp_path / "trading.db")


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


def test_from_config_reads_ledger_sums(ledger):
    ledger.record_api_cost(model="m", usd=3.0, role="critic")
    cfg = {"eur_usd_rate": 1.0,
           "api_budget": {"enabled": True, "monthly_cap_eur": 2.5, "daily_cap_eur": 5}}
    d = SpendGate.from_config(cfg, ledger).allows()
    assert not d.allowed and d.reason == "monthly_cap"


def test_ledger_api_cost_sum_day(ledger):
    ledger.record_api_cost(model="m", usd=0.25, role="critic")
    today = time.strftime("%Y-%m-%d", time.localtime())
    assert ledger.api_cost_sum_day(today) == pytest.approx(0.25)
    assert ledger.api_cost_sum_day("1999-01-01") == 0.0


def test_gate_status_shape(ledger):
    st = SpendGate.from_config({"api_budget": {"enabled": False}}, ledger).status()
    assert st["enabled"] is False
    assert st["blocked_reason"] == "disabled"
    assert st["spend_month_eur"] == 0.0
    assert st["monthly_cap_eur"] == DEFAULT_MONTHLY_CAP_EUR


# ── integrarea în pipeline-ul de trading ─────────────────────────────────────

_PROPOSALS = json.dumps([{
    "mecanism_cauzal": "funding negativ extrem forțează închideri de shorts",
    "predictie_cu_interval": {"kind": "point", "mean": 1.2, "interval_80": [-1, 4], "horizon": "48h"},
    "criteriu_falsificare": "randament mediu 48h post-semnal < 0 pe 30 instanțe",
    "implementare_schita": "filtrează funding < percentila 5, long pe fereastră de 48h",
}])
_APPROVE = json.dumps({"approved_index": 0, "reasoning": "plauzibil"})


def _mk_config(enabled: bool, price_in: float, price_out: float) -> dict:
    return {
        "eur_usd_rate": 1.0,
        "api_budget": {"enabled": enabled, "monthly_cap_eur": 10, "daily_cap_eur": 1},
        "providers": {"litellm_url": "http://localhost:4000", "litellm_key": "sk-x"},
        "trading": {
            "openrouter_api_key": "sk-or-test",
            "actor": {"litellm_name": "tier-2-worker", "max_proposals": 2},
            "critic": {"model": "deepseek/deepseek-chat", "monthly_cap_eur": 7,
                       "price_per_mtok_in": price_in, "price_per_mtok_out": price_out},
        },
    }


def _local_poster(url, headers, payload):
    content = _APPROVE if "approved_index" in json.dumps(payload) else _PROPOSALS
    return {"model": "qwen35b", "choices": [{"message": {"content": content}}], "usage": {}}


def _or_poster(url, headers, payload):
    raise AssertionError("apel OpenRouter deși plafonul global e închis")


def test_pipeline_gate_disabled_keeps_critic_local(ledger):
    """Cheie OpenRouter + model PLĂTIT + api_budget oprit → Critic local, zero apeluri plătite."""
    pipe = NightlyPipeline.from_config(
        _mk_config(enabled=False, price_in=0.4, price_out=0.87), ledger,
        poster_local=_local_poster, poster_openrouter=_or_poster,
    )
    assert pipe.budget is None
    assert pipe.gate_reason == "disabled"
    out = pipe.run_once(regime="bull")   # _or_poster ar exploda dacă ar fi apelat
    assert out["critic"]["cost_usd"] == 0.0
    assert "Plafon global #7" in out["report"]
    assert ledger.api_cost_sum() == 0.0


def test_pipeline_gate_disabled_allows_free_model(ledger):
    """Model gratuit (prețuri 0, ex. ':free') → OpenRouter permis chiar cu switch-ul oprit."""
    def or_ok(url, headers, payload):
        return {"model": "hy3-free", "choices": [{"message": {"content": _APPROVE}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    pipe = NightlyPipeline.from_config(
        _mk_config(enabled=False, price_in=0.0, price_out=0.0), ledger,
        poster_local=_local_poster, poster_openrouter=or_ok,
    )
    assert pipe.budget is not None and pipe.gate_reason is None
    out = pipe.run_once(regime="bull")
    assert out["critic"]["model_used"] == "hy3-free"
    assert out["critic"]["cost_usd"] == 0.0   # prețuri 0 → cost estimat 0


def test_pipeline_gate_enabled_allows_paid(ledger):
    """Switch pornit + sub plafoane → Criticul plătit funcționează ca înainte."""
    def or_ok(url, headers, payload):
        return {"model": "deepseek", "choices": [{"message": {"content": _APPROVE}}],
                "usage": {"prompt_tokens": 400, "completion_tokens": 80}}
    pipe = NightlyPipeline.from_config(
        _mk_config(enabled=True, price_in=0.4, price_out=0.87), ledger,
        poster_local=_local_poster, poster_openrouter=or_ok,
    )
    assert pipe.budget is not None and pipe.gate_reason is None
    out = pipe.run_once(regime="bull")
    assert out["critic"]["cost_usd"] > 0
    assert ledger.api_cost_sum() > 0


def test_pipeline_gate_monthly_cap_keeps_critic_local(ledger):
    """Plafonul global lunar atins → Critic local chiar cu switch-ul pornit."""
    ledger.record_api_cost(model="m", usd=11.0, role="critic")   # peste 10€ la curs 1.0
    pipe = NightlyPipeline.from_config(
        _mk_config(enabled=True, price_in=0.4, price_out=0.87), ledger,
        poster_local=_local_poster, poster_openrouter=_or_poster,
    )
    assert pipe.budget is None
    assert pipe.gate_reason == "monthly_cap"


def test_trading_subcap_still_enforced(ledger):
    """Sub-plafonul Criticului (7€) rămâne valabil SUB plafonul global (10€)."""
    ledger.record_api_cost(model="m", usd=8.0, role="critic")   # 8€ < 10€ global, > 7€ critic
    cfg = _mk_config(enabled=True, price_in=0.4, price_out=0.87)
    cfg["api_budget"]["daily_cap_eur"] = 50   # izolează testul de gardul zilnic (suma e „azi")
    gate = SpendGate.from_config(cfg, ledger)
    assert gate.allows().allowed                       # globalul mai permite
    b = ApiBudget(ledger, cap_eur=7.0, eur_usd=1.0)
    assert b.over_budget()                             # dar sub-plafonul Criticului nu
