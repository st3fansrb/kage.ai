"""Teste WP-T Etapa 5: buget API, Actor, Critic, costuri stresate, pipeline nocturn.

LLM-ul e injectat prin fake-uri (fără rețea). Verifică: formatul impus al Actorului, gardarea
pe buget a Criticului (fallback local), aprobarea ≤1, costurile stresate, un ciclu complet fără
promovare automată.
"""
import json

import pytest

from trading.ledger import TradingLedger
from trading.llm import ChatClient, ChatResult, LLMError
from trading.budget import ApiBudget, estimate_usd
from trading import actor as A
from trading import critic as C
from trading import costs
from trading.pipeline import NightlyPipeline


@pytest.fixture
def ledger(tmp_path):
    lg = TradingLedger(db_path=tmp_path / "trading.db")
    yield lg
    lg.close()


def _chat_returning(content: str, usage=None, model="fake", cost=None):
    """Fabrică un chat-callable care întoarce mereu `content`."""
    def _chat(messages):
        return ChatResult(content=content, usage=usage or {}, model=model, cost_usd=cost)
    return _chat


# ── client LLM (transport injectat) ───────────────────────────────────────────
def test_chatclient_parses_content_and_cost():
    captured = {}

    def poster(url, headers, payload):
        captured["url"], captured["auth"] = url, headers.get("Authorization")
        return {"model": "srv-model", "choices": [{"message": {"content": "salut"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.001}}

    client = ChatClient("https://openrouter.ai/api/v1", "deepseek/deepseek-chat",
                        api_key="sk-x", poster=poster)
    res = client.chat([{"role": "user", "content": "hi"}])
    assert res.content == "salut"
    assert res.cost_usd == pytest.approx(0.001)
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer sk-x"


def test_chatclient_raises_on_bad_response():
    client = ChatClient("http://x", "m", poster=lambda u, h, p: {"nope": True})
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}])


# ── format Actor ─────────────────────────────────────────────────────────────
_GOOD_PROPOSALS = json.dumps([
    {
        "mecanism_cauzal": "funding extrem forțează long-ii supra-levered la lichidare în 48h",
        "predictie_cu_interval": {"kind": "point", "mean": -1.5, "interval_80": [-6, 2], "horizon": "48h"},
        "criteriu_falsificare": "randament mediu 48h post-semnal > 0 pe 30 instanțe",
        "implementare_schita": "filtrează funding > percentila 95, short pe fereastră de 48h",
    },
    {
        "mecanism_cauzal": "unlock mare de token crește presiunea de vânzare 7 zile",
        "predictie_cu_interval": {"kind": "prob", "prob": 0.62, "horizon": "7d"},
        "criteriu_falsificare": "prob realizată < 0.5 pe 20 unlock-uri",
        "implementare_schita": "cataloghează unlock-uri, măsoară drift 7d",
    },
])


def test_actor_parses_and_registers(ledger):
    proposals = A.propose(_chat_returning(_GOOD_PROPOSALS), regime="bear", n_max=3)
    assert len(proposals) == 2
    ids = A.register(ledger, proposals, regime="bear", source_model="qwen35b")
    assert len(ids) == 2
    hyp = ledger.get_hypothesis(ids[0])
    assert hyp["pre_registered_at"] and hyp["status"] == "open"
    assert hyp["regime_at_creation"] == "bear"


def test_actor_rejects_malformed_strict():
    bad = json.dumps([{"mecanism_cauzal": "x"}])  # lipsesc chei
    with pytest.raises(A.ActorFormatError):
        A.propose(_chat_returning(bad), n_max=3, strict=True)


def test_actor_rejects_non_json():
    with pytest.raises(A.ActorFormatError):
        A.propose(_chat_returning("nu e json deloc"), n_max=3)


def test_actor_tolerates_markdown_fence():
    fenced = "Iată ipotezele:\n```json\n" + _GOOD_PROPOSALS + "\n```\n"
    proposals = A.propose(_chat_returning(fenced), n_max=3)
    assert len(proposals) == 2


def test_actor_strict_false_skips_bad_keeps_good():
    mixed = json.dumps([
        {"mecanism_cauzal": "incomplet"},  # invalid
        json.loads(_GOOD_PROPOSALS)[0],     # valid
    ])
    proposals = A.propose(_chat_returning(mixed), n_max=3, strict=False)
    assert len(proposals) == 1


def test_actor_truncates_to_n_max():
    proposals = A.propose(_chat_returning(_GOOD_PROPOSALS), n_max=1)
    assert len(proposals) == 1


# ── buget ─────────────────────────────────────────────────────────────────────
def test_budget_estimate_and_record(ledger):
    b = ApiBudget(ledger, cap_eur=7.0, eur_usd=1.0)
    assert b.over_budget() is False
    usd = estimate_usd(1_000_000, 500_000, price_per_mtok_in=0.14, price_per_mtok_out=0.28)
    assert usd == pytest.approx(0.14 + 0.14)
    b.record(model="deepseek/deepseek-chat", usd=usd, prompt_tokens=1_000_000, completion_tokens=500_000)
    assert b.spend_usd() == pytest.approx(0.28)


def test_budget_over_cap(ledger):
    b = ApiBudget(ledger, cap_eur=1.0, eur_usd=1.0)
    b.record(model="m", usd=1.5)
    assert b.over_budget() is True
    assert b.remaining_eur() < 0


# ── Critic ────────────────────────────────────────────────────────────────────
_APPROVE_0 = json.dumps({"approved_index": 0, "reasoning": "mecanism greu de arbitrat instant"})
_APPROVE_NONE = json.dumps({"approved_index": None, "reasoning": "toate ușor de arbitrat"})


def test_critic_approves_one_and_records_cost(ledger):
    proposals = json.loads(_GOOD_PROPOSALS)
    b = ApiBudget(ledger, cap_eur=7.0, eur_usd=1.0)
    chat = _chat_returning(_APPROVE_0, usage={"prompt_tokens": 1000, "completion_tokens": 200})
    out = C.critique(proposals, chat, budget=b, critic_model="deepseek/deepseek-chat",
                     price_per_mtok_in=0.14, price_per_mtok_out=0.28)
    assert out["approved_index"] == 0
    assert out["approved"] is proposals[0]
    assert out["fallback_used"] is False
    assert out["cost_usd"] > 0
    assert b.spend_usd() == pytest.approx(out["cost_usd"])


def test_critic_uses_real_cost_when_provider_returns_it(ledger):
    proposals = json.loads(_GOOD_PROPOSALS)
    b = ApiBudget(ledger, cap_eur=7.0, eur_usd=1.0)
    chat = _chat_returning(_APPROVE_0, usage={"prompt_tokens": 1000}, cost=0.009)
    out = C.critique(proposals, chat, budget=b)
    assert out["cost_usd"] == pytest.approx(0.009)


def test_critic_falls_back_local_when_over_budget(ledger):
    proposals = json.loads(_GOOD_PROPOSALS)
    b = ApiBudget(ledger, cap_eur=1.0, eur_usd=1.0)
    b.record(model="m", usd=2.0)  # peste plafon
    or_chat = _chat_returning(_APPROVE_0, model="openrouter")
    local_chat = _chat_returning(_APPROVE_NONE, model="qwen35b")
    out = C.critique(proposals, or_chat, local_chat=local_chat, budget=b)
    assert out["fallback_used"] is True
    assert out["model_used"] == "qwen35b"
    assert out["cost_usd"] == 0.0            # fallback local nu costă
    assert b.spend_usd() == pytest.approx(2.0)  # neschimbat


def test_critic_error_falls_back_local(ledger):
    """Dacă apelul OpenRouter aruncă (rate-limit, model free dispărut), cade pe local."""
    proposals = json.loads(_GOOD_PROPOSALS)
    b = ApiBudget(ledger, cap_eur=7.0, eur_usd=1.0)  # buget OK → încearcă OpenRouter întâi

    def failing_or(messages):
        raise LLMError("HTTP 429 rate limit")

    local_chat = _chat_returning(_APPROVE_0, model="qwen35b")
    out = C.critique(proposals, failing_or, local_chat=local_chat, budget=b)
    assert out["fallback_used"] is True
    assert out["fallback_reason"] == "error"
    assert out["approved_index"] == 0
    assert out["cost_usd"] == 0.0
    assert b.spend_usd() == 0.0            # eroarea nu a costat nimic


def test_critic_error_without_fallback_raises(ledger):
    proposals = json.loads(_GOOD_PROPOSALS)

    def failing_or(messages):
        raise LLMError("boom")

    with pytest.raises(C.CriticError):
        C.critique(proposals, failing_or, local_chat=None)


def test_critic_budget_fallback_reason(ledger):
    proposals = json.loads(_GOOD_PROPOSALS)
    b = ApiBudget(ledger, cap_eur=1.0, eur_usd=1.0)
    b.record(model="m", usd=2.0)
    out = C.critique(proposals, _chat_returning(_APPROVE_0),
                     local_chat=_chat_returning(_APPROVE_NONE), budget=b)
    assert out["fallback_used"] is True and out["fallback_reason"] == "budget"


def test_critic_none_when_no_proposals():
    out = C.critique([], _chat_returning(_APPROVE_0))
    assert out["approved"] is None


def test_critic_clamps_bad_index():
    proposals = json.loads(_GOOD_PROPOSALS)
    out = C.critique(proposals, _chat_returning(json.dumps({"approved_index": 9, "reasoning": "x"})))
    assert out["approved_index"] is None


# ── costuri stresate ──────────────────────────────────────────────────────────
def test_stress_pnls_penalizes():
    trades = [{"pnl": 10.0, "amount": 1000.0}, {"pnl": -5.0, "amount": 1000.0}]
    stressed = costs.stress_pnls(trades, slippage_bps=5.0, mult=2.0)
    frac = costs.roundtrip_penalty_frac(5.0, 2.0)  # 2*5/1e4*2 = 0.002
    assert frac == pytest.approx(0.002)
    assert stressed[0] == pytest.approx(10.0 - 1000.0 * 0.002)  # 8.0
    assert stressed[1] == pytest.approx(-5.0 - 1000.0 * 0.002)  # -7.0


def test_stress_ignores_open_trades():
    trades = [{"pnl": None, "amount": 1000.0}, {"pnl": 3.0, "amount": 100.0}]
    assert len(costs.stress_pnls(trades)) == 1


# ── pipeline complet ──────────────────────────────────────────────────────────
def test_pipeline_full_cycle_no_autopromote(ledger):
    b = ApiBudget(ledger, cap_eur=7.0, eur_usd=1.0)
    pipe = NightlyPipeline(
        ledger,
        actor_chat=_chat_returning(_GOOD_PROPOSALS),
        critic_chat=_chat_returning(_APPROVE_0, usage={"prompt_tokens": 500, "completion_tokens": 100}),
        budget=b,
        critic_price_in=0.14, critic_price_out=0.28,
    )
    out = pipe.run_once(regime="bear", n_max=3)
    # Actor a pre-înregistrat ambele ipoteze
    assert len(out["hypothesis_ids"]) == 2
    assert len(ledger.get_hypotheses()) == 2
    # Critic a aprobat exact una
    assert out["approved_index"] == 0
    assert out["approved_hypothesis_id"] == out["hypothesis_ids"][0]
    # NIMIC promovat automat + niciun experiment marcat 'paper'
    assert out["promoted"] is False
    assert all(e["status"] != "paper" for e in ledger.get_experiments(limit=1000))
    # Raportul menționează promovarea manuală + costul înregistrat
    assert "MANUALĂ" in out["report"]
    assert b.spend_usd() > 0


def test_pipeline_from_config_openrouter(ledger):
    """from_config: cu cheie OpenRouter → Critic pe OpenRouter + buget; posteri injectați."""
    def local_poster(url, headers, payload):
        return {"model": "qwen35b", "choices": [{"message": {"content": _GOOD_PROPOSALS}}], "usage": {}}

    def or_poster(url, headers, payload):
        return {"model": "deepseek", "choices": [{"message": {"content": _APPROVE_0}}],
                "usage": {"prompt_tokens": 400, "completion_tokens": 80}}

    cfg = {
        "eur_usd_rate": 0.9,
        "providers": {"litellm_url": "http://localhost:4000", "litellm_key": "sk-x"},
        "trading": {
            "openrouter_api_key": "sk-or-test",
            "actor": {"litellm_name": "tier-2-worker", "max_proposals": 2},
            "critic": {"base_url": "https://openrouter.ai/api/v1", "model": "deepseek/deepseek-chat",
                       "monthly_cap_eur": 7, "price_per_mtok_in": 0.14, "price_per_mtok_out": 0.28},
        },
    }
    pipe = NightlyPipeline.from_config(cfg, ledger, poster_local=local_poster, poster_openrouter=or_poster)
    assert pipe.budget is not None and pipe.default_n_max == 2
    out = pipe.run_once(regime="bear")
    assert out["approved_index"] == 0
    assert pipe.budget.spend_usd() > 0   # OpenRouter → cost înregistrat


def test_pipeline_from_config_no_key_runs_local(ledger):
    """from_config fără cheie OpenRouter → Criticul rulează local, fără buget/cost."""
    def local_poster(url, headers, payload):
        # Actor primește propuneri; Criticul (tot local) primește o aprobare.
        # Criticul cere „approved_index" (ascii) în instrucțiune → marker de rutare.
        content = _APPROVE_0 if "approved_index" in json.dumps(payload) else _GOOD_PROPOSALS
        return {"model": "qwen35b", "choices": [{"message": {"content": content}}], "usage": {}}

    cfg = {"providers": {"litellm_url": "http://localhost:4000"},
           "trading": {"openrouter_api_key": "", "actor": {"litellm_name": "tier-2-worker"}}}
    pipe = NightlyPipeline.from_config(cfg, ledger, poster_local=local_poster)
    assert pipe.budget is None
    out = pipe.run_once(regime="bull")
    assert out["critic"]["cost_usd"] == 0.0
    assert len(ledger.get_hypotheses()) == 2


def test_pipeline_fallback_when_over_budget(ledger):
    b = ApiBudget(ledger, cap_eur=1.0, eur_usd=1.0)
    b.record(model="m", usd=5.0)  # depășit
    pipe = NightlyPipeline(
        ledger,
        actor_chat=_chat_returning(_GOOD_PROPOSALS),
        critic_chat=_chat_returning(_APPROVE_0),
        local_critic_chat=_chat_returning(_APPROVE_NONE),
        budget=b,
    )
    out = pipe.run_once(regime="bull")
    assert out["critic"]["fallback_used"] is True
    assert b.spend_usd() == pytest.approx(5.0)  # fallback nu a mai cheltuit
