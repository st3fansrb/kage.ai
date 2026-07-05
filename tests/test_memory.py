"""Teste pentru long-term memory — _memory_retrieve (filtrare pe relevanță)."""
import orchestrator


class FakeCollection:
    def __init__(self, docs, distances):
        self._docs = docs
        self._distances = distances

    def count(self):
        return len(self._docs)

    def query(self, query_embeddings, n_results, include):
        return {"documents": [self._docs], "distances": [self._distances]}


async def test_memory_retrieve_filters_by_relevance(monkeypatch):
    # threshold 0.70 → relevanță = 1-dist; păstrează dist<=0.30
    monkeypatch.setattr(orchestrator, "MEMORY_RELEVANCE_THRESHOLD", 0.70)
    fake = FakeCollection(docs=["relevant", "irrelevant"], distances=[0.2, 0.6])
    monkeypatch.setattr(orchestrator, "_memory_collection", fake)
    out = await orchestrator._memory_retrieve("sess", "query", precomputed_emb=[0.1])
    assert out == "relevant"


async def test_memory_retrieve_empty_collection(monkeypatch):
    monkeypatch.setattr(orchestrator, "_memory_collection", FakeCollection([], []))
    out = await orchestrator._memory_retrieve("sess", "query", precomputed_emb=[0.1])
    assert out == ""


async def test_memory_retrieve_no_collection(monkeypatch):
    monkeypatch.setattr(orchestrator, "_memory_collection", None)
    out = await orchestrator._memory_retrieve("sess", "query", precomputed_emb=[0.1])
    assert out == ""
