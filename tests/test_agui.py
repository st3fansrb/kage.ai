"""Teste pentru stratul AG-UI Mission Control (WP10, slice 1).

Acoperă: mapările pure (risk label, agent status), forma stării (`_mc_state`),
traducerea runs → agents/activity, și endpoint-ul SSE `/agui` (RUN_STARTED →
STATE_SNAPSHOT). Testele nu ating servere externe.
"""
import asyncio
import json

import orchestrator


# ── mapări pure ───────────────────────────────────────────────────────────────

def test_risk_label_destructiv():
    assert orchestrator._mc_risk_label("Bash", "rm -rf ./build") == "DESTRUCTIV"


def test_risk_label_filesystem():
    assert orchestrator._mc_risk_label("Write", "") == "FILESYSTEM"


def test_risk_label_git():
    assert orchestrator._mc_risk_label("Bash", "git push --force") == "GIT"


def test_agent_status_mapping():
    assert orchestrator._mc_agent_status("running") == "running"
    assert orchestrator._mc_agent_status("paused") == "pending"
    assert orchestrator._mc_agent_status("failed") == "failed"
    assert orchestrator._mc_agent_status("done") == "done"


# ── traducerea runs → agents / activity ───────────────────────────────────────

_FAKE_RUNS = [
    {"id": "r1", "kind": "task", "channel": "agent", "tier": None, "model": "claude",
     "status": "running", "cost_usd": 0.08, "duration_ms": 724000,
     "created_at": "2026-07-06T10:00:00", "finished_at": None, "input": "!run scan jobs"},
    {"id": "r2", "kind": "chat", "channel": "web", "tier": 2, "model": "qwen35b",
     "status": "done", "cost_usd": None, "duration_ms": 1200,
     "created_at": "2026-07-06T09:59:00", "finished_at": "2026-07-06T09:59:01", "input": "salut"},
]


def test_agents_filters_agentic_kinds():
    agents = orchestrator._mc_agents(_FAKE_RUNS)
    assert [a["id"] for a in agents] == ["r1"]  # doar kind task/mission
    assert agents[0]["status"] == "running"
    assert agents[0]["costUsd"] == 0.08


def test_activity_includes_all_runs():
    act = orchestrator._mc_activity(_FAKE_RUNS)
    assert [e["id"] for e in act] == ["r1", "r2"]
    assert act[1]["tier"] == "T2"
    assert act[0]["tier"] == "—"  # tier None → em-dash


def test_state_has_expected_shape():
    st = orchestrator._mc_state()
    assert set(st.keys()) >= {"budget", "agents", "approvals", "activity", "runningCount"}
    assert set(st["budget"].keys()) >= {"cloudCalls", "maxCloud", "spentUsd", "spentEur", "maxUsd"}


# ── endpoint SSE /agui ────────────────────────────────────────────────────────

class _FakeRequest:
    """Request minimal pentru handler-ul SSE (nu se deconectează niciodată)."""
    async def is_disconnected(self):
        return False


def _sse_data(chunk: str) -> dict:
    """Extrage JSON-ul dintr-o linie SSE `data: {...}`."""
    for line in chunk.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError(f"chunk fără linie data: {chunk!r}")


def test_agui_streams_run_started_then_snapshot():
    async def run():
        resp = await orchestrator.agui_stream(_FakeRequest())
        assert resp.media_type == "text/event-stream"
        gen = resp.body_iterator
        # RUN_STARTED și STATE_SNAPSHOT sunt emise înainte de bucla de poll → imediate
        first = _sse_data(await gen.__anext__())
        second = _sse_data(await gen.__anext__())
        await gen.aclose()  # oprește generatorul curat (fără să atingem asyncio.sleep)
        return first, second

    first, second = asyncio.run(run())
    assert first["type"] == "RUN_STARTED"
    assert "threadId" in first and "runId" in first
    assert second["type"] == "STATE_SNAPSHOT"
    assert set(second["snapshot"].keys()) >= {"budget", "agents", "approvals", "activity"}
