"""Teste pentru clasificare/routing — funcții pure + decide_tier cu prefixe forțate."""
import orchestrator


# ── _heuristic_classify (pur) ─────────────────────────────────────────────────

def test_heuristic_personal_keyword(monkeypatch):
    monkeypatch.setattr(orchestrator, "PERSONAL_KEYWORDS", ["aumovio", "flutter"])
    assert orchestrator._heuristic_classify("ce am lucrat la aumovio azi") == 2


def test_heuristic_long_message_is_tier2(monkeypatch):
    monkeypatch.setattr(orchestrator, "PERSONAL_KEYWORDS", [])
    assert orchestrator._heuristic_classify("x" * 201) == 2


def test_heuristic_short_generic_is_tier1(monkeypatch):
    monkeypatch.setattr(orchestrator, "PERSONAL_KEYWORDS", [])
    assert orchestrator._heuristic_classify("cât face 2+2") == 1


# ── _build_tier_models / _build_tier_short (pur) ──────────────────────────────

def test_build_tier_models_defaults():
    m = orchestrator._build_tier_models({})
    assert m[1] == "tier-1-orchestrator"
    assert m[2] == "tier-2-worker"
    assert m[3] == ("claude", "claude-haiku-4-5")
    assert m[6] == ("claude", "claude-opus-4-8")


def test_build_tier_models_override():
    cfg = {"models": {"tier5": {"provider": "claude", "model": "claude-opus-4-8"}}}
    m = orchestrator._build_tier_models(cfg)
    assert m[5] == ("claude", "claude-opus-4-8")


def test_build_tier_short_defaults():
    s = orchestrator._build_tier_short({})
    assert s == {1: "qwen8b", 2: "qwen35b", 3: "haiku", 4: "gemini", 5: "sonnet", 6: "opus"}


# ── decide_tier — prefixe forțate ─────────────────────────────────────────────

async def test_decide_tier_fast_forces_tier1():
    tier, forced, conf, method = await orchestrator.decide_tier("!fast ce e recursivitatea")
    assert (tier, forced) == (1, True)


async def test_decide_tier_best_forces_tier5():
    tier, forced, _, _ = await orchestrator.decide_tier("!best scrie un eseu")
    assert (tier, forced) == (5, True)


async def test_decide_tier_escaladeaza_forces_tier5():
    tier, forced, _, _ = await orchestrator.decide_tier("escaladează te rog")
    assert (tier, forced) == (5, True)


async def test_decide_tier_plan_minimum_tier2(monkeypatch):
    async def fake_classify(msg):
        return 1, 0.9, "sem"
    monkeypatch.setattr(orchestrator, "_classify", fake_classify)
    tier, forced, _, _ = await orchestrator.decide_tier("!plan ceva simplu")
    assert tier >= 2 and forced is True
