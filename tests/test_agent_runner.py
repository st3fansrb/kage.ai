"""Teste pentru executorul pe Claude Agent SDK (WP9 / #4).

Acoperă: maparea mesajelor SDK → evenimente normalizate (`_map_sync`), gate-ul de
risc in-proces (`_make_gate` refolosind matricea din risk_hook), bucla `run()` cu
un client SDK mock (delte reale + inactivity timeout + interrupt), plus integrarea
în orchestrator (mapare sesiuni pentru resume + `_agent_approval_cb`).

Rulează 100% pe mock-uri — niciun apel real la claude CLI/SDK. Sar peste dacă SDK-ul
nu e disponibil (rulare pe un runtime <3.10 unde importul degradează grațios).
"""
import asyncio
import sqlite3

import pytest

import agent_runner as ar

pytestmark = pytest.mark.skipif(not ar.SDK_AVAILABLE, reason="claude-agent-sdk indisponibil (necesită Python ≥3.10)")

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


def _stream_delta(text):
    return StreamEvent(uuid="u", session_id="s",
                       event={"type": "content_block_delta",
                              "delta": {"type": "text_delta", "text": text}},
                       parent_tool_use_id=None)


# ── _map_sync ─────────────────────────────────────────────────────────────────

def test_map_stream_text_delta():
    assert ar._map_sync(_stream_delta("salut")) == [{"type": "text", "text": "salut"}]


def test_map_stream_non_text_delta_ignored():
    se = StreamEvent(uuid="u", session_id="s",
                     event={"type": "content_block_delta",
                            "delta": {"type": "thinking_delta", "thinking": "hmm"}},
                     parent_tool_use_id=None)
    assert ar._map_sync(se) == []


def test_map_assistant_emits_tool_use_skips_text():
    msg = AssistantMessage(
        content=[TextBlock(text="ignoră-mă"),
                 ToolUseBlock(id="t1", name="Bash", input={"command": "ls"})],
        model="m", parent_tool_use_id=None, error=None, usage=None,
        message_id="mid", stop_reason=None, session_id="s", uuid="u")
    out = ar._map_sync(msg)
    # TextBlock e sărit (textul vine ca delte), doar tool_use rămâne
    assert out == [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}, "id": "t1"}]


def test_map_user_tool_result():
    msg = UserMessage(
        content=[ToolResultBlock(tool_use_id="t1", content="fișiere: a b c", is_error=False)],
        uuid="u", parent_tool_use_id=None, tool_use_result=None)
    out = ar._map_sync(msg)
    assert out == [{"type": "tool_result", "content": "fișiere: a b c", "is_error": False}]


def test_map_result_carries_session_cost_duration():
    msg = ResultMessage(
        subtype="success", duration_ms=1234, duration_api_ms=1000, is_error=False,
        num_turns=1, session_id="sdk-sess-abc", stop_reason=None, total_cost_usd=0.0123,
        usage=None, result="gata", structured_output=None, model_usage=None,
        permission_denials=None, deferred_tool_use=None, errors=None,
        api_error_status=None, uuid="u")
    out = ar._map_sync(msg)
    assert out == [{
        "type": "result", "session_id": "sdk-sess-abc", "cost_usd": 0.0123,
        "duration_ms": 1234, "text": "gata", "is_error": False,
    }]


# ── Gate de risc in-proces ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gate_safe_allows():
    gate = ar.AgentRunner()._make_gate("scrie un test", autonomous=False, approval_cb=None)
    res = await gate("Read", {"file_path": "README.md"}, None)
    assert isinstance(res, PermissionResultAllow)


@pytest.mark.asyncio
async def test_gate_never_denies():
    gate = ar.AgentRunner()._make_gate("orice", autonomous=False, approval_cb=None)
    res = await gate("Bash", {"command": "sudo rm -rf /"}, None)
    assert isinstance(res, PermissionResultDeny)


@pytest.mark.asyncio
async def test_gate_high_without_channel_denies():
    # git reset = High; fără approval_cb → fail-closed (deny)
    gate = ar.AgentRunner()._make_gate("fă ceva", autonomous=False, approval_cb=None)
    res = await gate("Bash", {"command": "git reset --soft HEAD~1"}, None)
    assert isinstance(res, PermissionResultDeny)


@pytest.mark.asyncio
async def test_gate_high_confirm_allows():
    async def _cb(tool, inp, level, reason):
        assert level == "High"
        return "confirm"
    gate = ar.AgentRunner()._make_gate("fă ceva", autonomous=False, approval_cb=_cb)
    res = await gate("Bash", {"command": "git reset --soft HEAD~1"}, None)
    assert isinstance(res, PermissionResultAllow)


@pytest.mark.asyncio
async def test_gate_high_block_denies():
    async def _cb(tool, inp, level, reason):
        return "block"
    gate = ar.AgentRunner()._make_gate("fă ceva", autonomous=False, approval_cb=_cb)
    res = await gate("Bash", {"command": "git reset --soft HEAD~1"}, None)
    assert isinstance(res, PermissionResultDeny)


@pytest.mark.asyncio
async def test_gate_explicit_keyword_downgrades_high_to_allow_non_autonomous():
    # "șterge" în mesaj → High se coboară la Medium în evaluate_risk; Medium
    # non-autonomous → allow direct (fără aprobare).
    gate = ar.AgentRunner()._make_gate("șterge fișierele din src", autonomous=False, approval_cb=None)
    res = await gate("Bash", {"command": "rm src/vechi.py"}, None)
    assert isinstance(res, PermissionResultAllow)


# ── run() cu client SDK mock ──────────────────────────────────────────────────

class _FakeClient:
    """Client SDK fals: `receive_response` livrează o listă predefinită de mesaje."""
    messages: list = []
    hang: bool = False

    def __init__(self, options=None):
        self.options = options
        self.interrupted = False
        self.connected = False

    async def connect(self):
        self.connected = True

    async def query(self, prompt):
        self.prompt = prompt

    async def disconnect(self):
        self.connected = False

    async def interrupt(self):
        self.interrupted = True

    async def receive_response(self):
        if self.hang:
            await asyncio.sleep(10)
            return
        for m in self.messages:
            yield m


@pytest.mark.asyncio
async def test_run_streams_deltas_and_result(monkeypatch):
    result = ResultMessage(
        subtype="success", duration_ms=10, duration_api_ms=5, is_error=False,
        num_turns=1, session_id="sdk-9", stop_reason=None, total_cost_usd=0.001,
        usage=None, result="ignorat (deja streamat)", structured_output=None,
        model_usage=None, permission_denials=None, deferred_tool_use=None,
        errors=None, api_error_status=None, uuid="u")
    _FakeClient.messages = [_stream_delta("Sal"), _stream_delta("ut"), result]
    _FakeClient.hang = False
    monkeypatch.setattr(ar, "ClaudeSDKClient", _FakeClient)

    runner = ar.AgentRunner()
    events = [ev async for ev in runner.run("salut", user_message="salut")]
    texts = [e["text"] for e in events if e["type"] == "text"]
    results = [e for e in events if e["type"] == "result"]
    assert "".join(texts) == "Salut"
    assert results and results[0]["session_id"] == "sdk-9" and results[0]["cost_usd"] == 0.001
    assert not runner.active_clients   # curățat la final


@pytest.mark.asyncio
async def test_run_result_fallback_when_no_deltas(monkeypatch):
    # Fără delte de text, dar result cu text → emis ca text (fallback).
    result = ResultMessage(
        subtype="success", duration_ms=10, duration_api_ms=5, is_error=False,
        num_turns=1, session_id="s", stop_reason=None, total_cost_usd=None,
        usage=None, result="răspuns scurt", structured_output=None, model_usage=None,
        permission_denials=None, deferred_tool_use=None, errors=None,
        api_error_status=None, uuid="u")
    _FakeClient.messages = [result]
    _FakeClient.hang = False
    monkeypatch.setattr(ar, "ClaudeSDKClient", _FakeClient)

    events = [ev async for ev in ar.AgentRunner().run("x", user_message="x")]
    texts = [e["text"] for e in events if e["type"] == "text"]
    assert "".join(texts) == "răspuns scurt"


@pytest.mark.asyncio
async def test_run_inactivity_timeout_interrupts(monkeypatch):
    _FakeClient.messages = []
    _FakeClient.hang = True
    monkeypatch.setattr(ar, "ClaudeSDKClient", _FakeClient)

    runner = ar.AgentRunner()
    events = [ev async for ev in runner.run("x", user_message="x", inactivity_timeout=0.1)]
    errs = [e for e in events if e["type"] == "error"]
    assert errs and "timeout" in errs[0]["error"].lower()


@pytest.mark.asyncio
async def test_run_sdk_unavailable_yields_error(monkeypatch):
    monkeypatch.setattr(ar, "SDK_AVAILABLE", False)
    events = [ev async for ev in ar.AgentRunner().run("x", user_message="x")]
    assert len(events) == 1 and events[0]["type"] == "error"
    assert "indisponibil" in events[0]["error"]


# ── Integrare orchestrator: mapare sesiuni + approval_cb ───────────────────────

@pytest.fixture
def sess_db(monkeypatch):
    import orchestrator
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    orchestrator._ensure_agent_sessions_table(conn)
    orchestrator._ensure_approvals_table(conn)
    conn.commit()
    monkeypatch.setattr(orchestrator, "_db_conn", conn)
    yield orchestrator
    conn.close()


def test_sdk_session_roundtrip(sess_db):
    orch = sess_db
    assert orch._get_sdk_session("sess1") is None
    orch._save_sdk_session("sess1", "sdk-aaa")
    assert orch._get_sdk_session("sess1") == "sdk-aaa"
    # upsert: a doua tură suprascrie
    orch._save_sdk_session("sess1", "sdk-bbb")
    assert orch._get_sdk_session("sess1") == "sdk-bbb"


def test_save_sdk_session_ignores_empty(sess_db):
    orch = sess_db
    orch._save_sdk_session("sess2", None)
    orch._save_sdk_session(None, "sdk-x")
    assert orch._get_sdk_session("sess2") is None


@pytest.mark.asyncio
async def test_approval_cb_confirm(sess_db, monkeypatch):
    orch = sess_db
    monkeypatch.setattr(orch, "_tg_gateway", None)
    monkeypatch.setattr(orch, "CONFIRM_TIMEOUT_SECS", 5)

    async def _resolver():
        # Simulează apăsarea butonului Telegram după un tic
        await asyncio.sleep(0.05)
        for rid, ev in list(orch.pending_risk.items()):
            orch.risk_decisions[rid] = "confirm"
            ev.set()

    asyncio.create_task(_resolver())
    decision = await orch._agent_approval_cb("Bash", {"command": "git reset"}, "High", "git reset")
    assert decision == "confirm"


@pytest.mark.asyncio
async def test_approval_cb_timeout_blocks(sess_db, monkeypatch):
    orch = sess_db
    monkeypatch.setattr(orch, "_tg_gateway", None)
    monkeypatch.setattr(orch, "CONFIRM_TIMEOUT_SECS", 0.1)
    decision = await orch._agent_approval_cb("Bash", {"command": "git reset"}, "High", "git reset")
    assert decision == "block"
