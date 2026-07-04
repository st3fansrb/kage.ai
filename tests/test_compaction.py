"""Teste pentru context compaction — _compact_messages (sliding window)."""
import orchestrator


def _msgs(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(n)]


async def test_compact_under_limit_unchanged():
    msgs = [{"role": "system", "content": "sys"}] + _msgs(4)
    out = await orchestrator._compact_messages(msgs, max_messages=20)
    assert out == msgs


async def test_compact_over_limit_keeps_last_n(monkeypatch):
    monkeypatch.setattr(orchestrator, "ENABLE_SUMMARIZATION", False)
    msgs = [{"role": "system", "content": "sys"}] + _msgs(30)
    out = await orchestrator._compact_messages(msgs, max_messages=10)
    conv = [m for m in out if m["role"] != "system"]
    assert len(conv) == 10
    assert conv[-1]["content"] == "m29"


async def test_compact_preserves_system_prompt(monkeypatch):
    monkeypatch.setattr(orchestrator, "ENABLE_SUMMARIZATION", False)
    msgs = [{"role": "system", "content": "SYSTEM"}] + _msgs(30)
    out = await orchestrator._compact_messages(msgs, max_messages=5)
    assert out[0] == {"role": "system", "content": "SYSTEM"}
