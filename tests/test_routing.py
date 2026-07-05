"""Teste pentru clasificare/routing — funcții pure + decide_tier cu prefixe forțate."""
import pytest

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


# ── decide_tier — prefixe noi WP3 (!opus / !gemini / !retry cap 6) ────────────

async def test_decide_tier_opus_forces_tier6():
    tier, forced, _, method = await orchestrator.decide_tier("!opus rezolvă problema grea")
    assert (tier, forced, method) == (6, True, "forced")


async def test_decide_tier_gemini_forces_tier4():
    tier, forced, _, method = await orchestrator.decide_tier("!gemini rezumă documentul")
    assert (tier, forced, method) == (4, True, "forced")


async def test_decide_tier_retry_caps_at_6(monkeypatch, tmp_path):
    status = tmp_path / "status.json"
    status.write_text('{"tier": 6}')
    monkeypatch.setattr(orchestrator, "STATUS_FILE", status)
    tier, forced, _, _ = await orchestrator.decide_tier("!retry mai bine")
    assert (tier, forced) == (6, True)  # min(6+1, 6) == 6, nu 7


# ── _strip_routing_prefixes (pur) ─────────────────────────────────────────────

def test_strip_routing_prefixes_removes_commands():
    assert orchestrator._strip_routing_prefixes("!best !nocache scrie un eseu") == "scrie un eseu"
    assert orchestrator._strip_routing_prefixes("escaladează te rog X") == "te rog X"
    assert orchestrator._strip_routing_prefixes("!OPUS mesaj") == "mesaj"


# ── _semantic_classify — vot ponderat k-NN ────────────────────────────────────

class _FakeCol:
    """Colecție minimă ChromaDB pentru a controla distanțele întoarse de query."""
    def __init__(self, distances, metas):
        self._d, self._m = distances, metas

    def count(self):
        return len(self._d)

    def query(self, query_embeddings, n_results, include):
        return {"distances": [self._d[:n_results]], "metadatas": [self._m[:n_results]]}


async def _const_emb(_text):
    return [1.0, 0.0]


async def test_semantic_classify_weighted_vote_beats_closest(monkeypatch):
    # Cel mai apropiat vecin e T1 (sim .90), dar trei vecini T5 (sim .75/.72/.70)
    # cumulează 2.17 > .90 → votul ponderat alege T5, nu 1-NN.
    dist = [0.10, 0.25, 0.28, 0.30, 0.95]
    metas = [{"tier": 1}, {"tier": 5}, {"tier": 5}, {"tier": 5}, {"tier": 3}]
    monkeypatch.setattr(orchestrator, "_routing_collection", _FakeCol(dist, metas))
    monkeypatch.setattr(orchestrator, "_get_embedding", _const_emb)
    tier, conf = await orchestrator._semantic_classify("orice")
    assert tier == 5
    assert conf == 0.75  # cel mai bun vecin al tier-ului câștigător


async def test_semantic_classify_below_floor_defaults_t3(monkeypatch):
    dist = [0.5, 0.6]  # similarități .5 și .4, sub pragul 0.6
    metas = [{"tier": 1}, {"tier": 5}]
    monkeypatch.setattr(orchestrator, "_routing_collection", _FakeCol(dist, metas))
    monkeypatch.setattr(orchestrator, "_get_embedding", _const_emb)
    tier, conf = await orchestrator._semantic_classify("necunoscut")
    assert (tier, conf) == (3, 0.6)


# ── Feedback loop + vacuum (integrare cu ChromaDB ephemeral) ───────────────────

_VOCAB = ["scrie", "eseu", "arhitectura", "sistem", "complex",
          "recursivitate", "email", "cod", "imagine", "rezuma"]


async def _bow_embedding(text):
    t = text.lower()
    v = [1.0 if w in t else 0.0 for w in _VOCAB]
    return v if any(v) else [0.01] * len(_VOCAB)


@pytest.fixture
def routing_env(monkeypatch):
    import uuid as _uuid

    import chromadb
    client = chromadb.EphemeralClient()
    # Nume unic: EphemeralClient partajează instanța in-memory între teste în același proces.
    col = client.get_or_create_collection(
        f"test-routing-{_uuid.uuid4().hex}", metadata={"hnsw:space": "cosine"}
    )
    monkeypatch.setattr(orchestrator, "_routing_collection", col)
    monkeypatch.setattr(orchestrator, "_get_embedding", _bow_embedding)
    monkeypatch.setattr(orchestrator, "PERSONAL_KEYWORDS", [])
    return col


async def test_feedback_then_similar_routes_via_sem(routing_env):
    # Un override !best învață routerul; un mesaj similar (fără prefix) merge la T5 cu metoda sem.
    await orchestrator._record_routing_feedback("!best scrie un eseu despre arhitectura sistem", 5)
    tier, conf, method = await orchestrator._classify("scrie un eseu")
    assert tier == 5
    assert method == "sem"


async def test_decide_tier_learns_from_override(routing_env):
    await orchestrator._record_routing_feedback("!best scrie un eseu despre arhitectura sistem", 5)
    tier, forced, _, method = await orchestrator.decide_tier("scrie un eseu")
    assert tier == 5 and forced is False and method == "sem"


async def test_short_feedback_is_ignored(routing_env):
    await orchestrator._record_routing_feedback("!best go", 5)  # curățat → "go" (<4 chars)
    assert routing_env.count() == 0


async def test_routing_vacuum_caps_per_tier(routing_env, monkeypatch):
    monkeypatch.setattr(orchestrator, "MAX_FEEDBACK_PER_TIER", 5)
    for i in range(12):
        emb = await orchestrator._get_embedding(f"exemplu numarul {i}")
        routing_env.add(
            documents=[f"ex{i}"], embeddings=[emb],
            metadatas=[{"tier": 5, "source": "feedback", "ts": float(i)}], ids=[f"id{i}"],
        )
    # Un seed pe alt tier — nu trebuie atins de vacuum.
    seed_emb = await orchestrator._get_embedding("seed cod")
    routing_env.add(documents=["seed"], embeddings=[seed_emb],
                    metadatas=[{"tier": 3, "source": "seed"}], ids=["seed1"])
    await orchestrator._routing_vacuum()
    res = routing_env.get(include=["metadatas"])
    fb5 = [m for m in res["metadatas"] if m.get("source") == "feedback" and m.get("tier") == 5]
    seeds = [m for m in res["metadatas"] if m.get("source") == "seed"]
    assert len(fb5) == 5    # păstrează cele mai noi 5
    assert len(seeds) == 1  # seed-ul intact
