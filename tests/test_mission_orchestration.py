"""Teste pentru orchestrarea Mission Runner-ului (WP11) — bucla cu stare.

Bucla `_mission_run` peste un AgentRunner mock: parcurge WP-urile, verifică criteriile
(comenzi shell reale `true`/`false`), marchează ✅ + commit, avansează; rate-limit →
pauză + resume programat; verificare picată → puntea de decizii (retry/skip/abort/timeout);
restart → reia misiunile `running`. Plus comenzile `!mission` și endpoint-ul de răspuns.

AgentRunner, git-commit, caffeinate și Telegram sunt mock-uite — niciun apel real.
"""
import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import orchestrator


class _FakeRunner:
    """AgentRunner fals: fiecare apel `run` livrează evenimentele următoare din listă."""
    def __init__(self, events_per_call):
        self.active_clients = set()
        self.calls = []
        self._events = events_per_call

    async def run(self, prompt, **kw):
        i = len(self.calls)
        self.calls.append((prompt, kw))
        evs = self._events[min(i, len(self._events) - 1)]
        for e in evs:
            yield e

    async def stop_all(self):
        return 0


def _result_ev(sid="sdk-s", text="gata"):
    return {"type": "result", "session_id": sid, "cost_usd": 0.01,
            "duration_ms": 5, "text": text, "is_error": False}


@pytest.fixture
def mdb(monkeypatch, tmp_path):
    """DB in-memory cu toate tabelele WP8/WP9/WP11 + no-op-uri pe caffeinate/commit."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("""CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT DEFAULT 'default', role TEXT, content TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    orchestrator._ensure_runs_table(conn)
    orchestrator._ensure_agent_sessions_table(conn)
    orchestrator._ensure_missions_table(conn)
    conn.commit()
    monkeypatch.setattr(orchestrator, "_db_conn", conn)
    monkeypatch.setattr(orchestrator, "_active_mission_id", None)
    monkeypatch.setattr(orchestrator, "_mission_stop", {})
    monkeypatch.setattr(orchestrator, "pending_mission_q", {})
    monkeypatch.setattr(orchestrator, "mission_answers", {})
    monkeypatch.setattr(orchestrator, "_tg_gateway", None)
    monkeypatch.setattr(orchestrator, "_mission_caffeinate_start", lambda: None)
    monkeypatch.setattr(orchestrator, "_mission_caffeinate_stop", lambda: None)
    commits = []
    monkeypatch.setattr(orchestrator, "_mission_mark_and_commit",
                        lambda path, idx, title, git_cwd=None: commits.append((idx, title)))
    # WP12: nu atinge git-ul real în teste (branch-ul misiunii).
    monkeypatch.setattr(orchestrator, "_mission_git_ensure_branch", lambda slug: None)
    monkeypatch.setattr(orchestrator, "MISSIONS_DIR", tmp_path / "missions")
    orchestrator._commits = commits  # expus pentru assert

    # decide_tier determinist (T5 → Sonnet) ca bucla să nu atingă ChromaDB reală.
    async def _fake_decide(msg):
        return (5, False, 1.0, "test")
    monkeypatch.setattr(orchestrator, "decide_tier", _fake_decide)
    yield orchestrator
    conn.close()


def _write_mission(orch, tmp_path, body):
    d = tmp_path / "missions" / "test"
    d.mkdir(parents=True, exist_ok=True)
    (d / "mission.md").write_text(body, encoding="utf-8")
    return "test"


_TWO_WP = """# Mission: Demo două pachete

## WP1 — primul
- fă ceva
### Acceptare
- `true`

## WP2 — al doilea
- fă altceva
### Acceptare
- `true`
"""


# ── _mission_create ───────────────────────────────────────────────────────────

def test_mission_create_marks_done_and_current_idx(mdb, tmp_path):
    orch = mdb
    body = "# Mission: X\n\n## WP1 — gata ✅\n- x\n\n## WP2 — de făcut\n- y\n"
    _write_mission(orch, tmp_path, body)
    mid, err = orch._mission_create("test", cwd=str(tmp_path))
    assert err is None and mid
    wps = orch._mission_wps(mid)
    assert [w["status"] for w in wps] == ["done", "pending"]
    assert orch._mission_row(mid)["current_idx"] == 1     # primul pending


def test_mission_create_missing_returns_error(mdb, tmp_path):
    mid, err = mdb._mission_create("nu-exista", cwd=str(tmp_path))
    assert mid is None and "negăsită" in err


# ── Bucla completă ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_loop_completes_two_wps(mdb, tmp_path, monkeypatch):
    orch = mdb
    _write_mission(orch, tmp_path, _TWO_WP)
    monkeypatch.setattr(orch, "_agent_runner", _FakeRunner([[_result_ev()], [_result_ev()]]))
    mid, _ = orch._mission_create("test", cwd=str(tmp_path))

    await orch._mission_run(mid)

    assert orch._mission_row(mid)["status"] == "done"
    assert [w["status"] for w in orch._mission_wps(mid)] == ["done", "done"]
    assert len(orch._agent_runner.calls) == 2          # o sesiune per WP
    assert orch._commits == [(0, "WP1 — primul"), (1, "WP2 — al doilea")]
    assert orch._active_mission_id is None             # curățat la final


@pytest.mark.asyncio
async def test_loop_resumes_session_across_wps(mdb, tmp_path, monkeypatch):
    orch = mdb
    _write_mission(orch, tmp_path, _TWO_WP)
    monkeypatch.setattr(orch, "_agent_runner",
                        _FakeRunner([[_result_ev(sid="sess-A")], [_result_ev(sid="sess-A")]]))
    mid, _ = orch._mission_create("test", cwd=str(tmp_path))
    await orch._mission_run(mid)
    # al doilea WP trebuie să reia sesiunea SDK salvată de primul
    assert orch._agent_runner.calls[1][1]["resume"] == "sess-A"


# ── Verificare picată → puntea de decizii ─────────────────────────────────────

_FAIL_WP = """# Mission: Cu verificare care pică

## WP1 — pică
- x
### Acceptare
- `false`
"""


@pytest.mark.asyncio
async def test_verify_fail_skip_advances(mdb, tmp_path, monkeypatch):
    orch = mdb
    _write_mission(orch, tmp_path, _FAIL_WP)
    monkeypatch.setattr(orch, "_agent_runner", _FakeRunner([[_result_ev()]]))
    async def _ask(q, opts, timeout=None):
        return "skip"
    monkeypatch.setattr(orch, "_mission_ask", _ask)
    mid, _ = orch._mission_create("test", cwd=str(tmp_path))
    await orch._mission_run(mid)
    assert orch._mission_row(mid)["status"] == "done"
    assert orch._mission_wps(mid)[0]["status"] == "done"


@pytest.mark.asyncio
async def test_verify_fail_abort_fails_mission(mdb, tmp_path, monkeypatch):
    orch = mdb
    _write_mission(orch, tmp_path, _FAIL_WP)
    monkeypatch.setattr(orch, "_agent_runner", _FakeRunner([[_result_ev()]]))
    async def _ask(q, opts, timeout=None):
        return "abort"
    monkeypatch.setattr(orch, "_mission_ask", _ask)
    mid, _ = orch._mission_create("test", cwd=str(tmp_path))
    await orch._mission_run(mid)
    assert orch._mission_row(mid)["status"] == "failed"


@pytest.mark.asyncio
async def test_verify_fail_timeout_pauses(mdb, tmp_path, monkeypatch):
    orch = mdb
    _write_mission(orch, tmp_path, _FAIL_WP)
    monkeypatch.setattr(orch, "_agent_runner", _FakeRunner([[_result_ev()]]))
    async def _ask(q, opts, timeout=None):
        return None                          # fără răspuns
    monkeypatch.setattr(orch, "_mission_ask", _ask)
    mid, _ = orch._mission_create("test", cwd=str(tmp_path))
    await orch._mission_run(mid)
    assert orch._mission_row(mid)["status"] == "paused"    # NU failed — decizie ≠ risc


# ── Rate-limit → pauză + resume programat ─────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_pauses_and_schedules(mdb, tmp_path, monkeypatch):
    orch = mdb
    _write_mission(orch, tmp_path, _TWO_WP)
    err_ev = {"type": "error", "error": "usage limit reached, try again in 30 minutes"}
    monkeypatch.setattr(orch, "_agent_runner", _FakeRunner([[err_ev]]))
    scheduled = []
    monkeypatch.setattr(orch, "_mission_schedule_resume",
                        lambda mid, secs: scheduled.append(secs))
    mid, _ = orch._mission_create("test", cwd=str(tmp_path))
    await orch._mission_run(mid)
    assert orch._mission_row(mid)["status"] == "paused"
    assert orch._mission_wps(mid)[0]["status"] == "pending"   # WP se reia
    assert scheduled == [30 * 60]


# ── Restart → reia misiunile running ──────────────────────────────────────────

def test_resume_on_startup_relaunches_running(mdb, tmp_path, monkeypatch):
    orch = mdb
    _write_mission(orch, tmp_path, _TWO_WP)
    mid, _ = orch._mission_create("test", cwd=str(tmp_path))  # status 'running'
    launched = []
    monkeypatch.setattr(orch, "_mission_launch", lambda m: launched.append(m))
    orch._mission_resume_on_startup()
    assert launched == [mid]


# ── Rutare model prin router-ul Kage + logare cost ────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("tier,expected", [
    (3, "claude-haiku-4-5"),
    (5, "claude-sonnet-4-6"),
    (6, "claude-opus-4-8"),
])
async def test_pick_model_claude_tiers(mdb, monkeypatch, tier, expected):
    async def _dec(msg):
        return (tier, False, 1.0, "t")
    monkeypatch.setattr(mdb, "decide_tier", _dec)
    t, model = await mdb._mission_pick_model("orice")
    assert t == tier and model == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", [1, 2, 4])
async def test_pick_model_non_claude_clamps_to_default(mdb, monkeypatch, tier):
    async def _dec(msg):
        return (tier, False, 1.0, "t")
    monkeypatch.setattr(mdb, "decide_tier", _dec)
    _t, model = await mdb._mission_pick_model("orice")
    assert model == mdb.MISSION_DEFAULT_MODEL


@pytest.mark.asyncio
async def test_pick_model_decide_error_falls_back(mdb, monkeypatch):
    async def _boom(msg):
        raise RuntimeError("x")
    monkeypatch.setattr(mdb, "decide_tier", _boom)
    _t, model = await mdb._mission_pick_model("orice")
    assert model == mdb.MISSION_DEFAULT_MODEL


@pytest.mark.asyncio
async def test_loop_passes_router_model_and_logs_cost(mdb, tmp_path, monkeypatch):
    orch = mdb
    _write_mission(orch, tmp_path, _TWO_WP)
    async def _dec(msg):
        return (3, False, 1.0, "t")            # T3 → Haiku
    monkeypatch.setattr(orch, "decide_tier", _dec)
    monkeypatch.setattr(orch, "_agent_runner", _FakeRunner([[_result_ev()], [_result_ev()]]))
    mid, _ = orch._mission_create("test", cwd=str(tmp_path))
    await orch._mission_run(mid)
    # agentul primește modelul ales de router
    assert orch._agent_runner.calls[0][1]["model"] == "claude-haiku-4-5"
    # model + cost real logate pe run-ul de misiune (înainte erau None)
    row = orch._db_conn.execute(
        "SELECT model, cost_usd FROM runs WHERE kind='mission' ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row[0] == "claude-haiku-4-5"
    assert abs(row[1] - 0.02) < 1e-9           # 2 WP × 0.01 (cost din _result_ev)


# ── _mission_ask (unit) ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ask_returns_answer(mdb, monkeypatch):
    orch = mdb

    class _GW:
        async def send_mission_question(self, req_id, q, opts):
            orch.mission_answers[req_id] = "retry"
            orch.pending_mission_q[req_id].set()
    monkeypatch.setattr(orch, "_tg_gateway", _GW())
    ans = await orch._mission_ask("întrebare?", ["retry", "abort"], timeout=2)
    assert ans == "retry"


@pytest.mark.asyncio
async def test_ask_timeout_returns_none(mdb, monkeypatch):
    orch = mdb

    class _GW:
        async def send_mission_question(self, req_id, q, opts):
            pass                              # nu răspunde
    monkeypatch.setattr(orch, "_tg_gateway", _GW())
    ans = await orch._mission_ask("întrebare?", ["a", "b"], timeout=0.1)
    assert ans is None


# ── Endpoint + comenzi ────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(orchestrator, "_get_api_token", lambda: "")
    return TestClient(orchestrator.app)


def test_mission_answer_endpoint(mdb, client):
    orch = mdb
    e = asyncio.Event()
    orch.pending_mission_q["req1"] = e
    r = client.post("/mission/answer/req1", json={"answer": "skip"})
    assert r.status_code == 200 and r.json()["answer"] == "skip"
    assert orch.mission_answers["req1"] == "skip" and e.is_set()


async def _read_sse(resp):
    raw = ""
    async for chunk in resp.body_iterator:
        raw += chunk if isinstance(chunk, str) else chunk.decode()
    text = ""
    for line in raw.splitlines():
        if line.startswith("data: ") and "[DONE]" not in line:
            try:
                text += json.loads(line[6:])["choices"][0]["delta"].get("content", "")
            except Exception:
                pass
    return text


@pytest.mark.asyncio
async def test_command_start_launches(mdb, tmp_path, monkeypatch):
    orch = mdb
    _write_mission(orch, tmp_path, _TWO_WP)
    launched = []
    monkeypatch.setattr(orch, "_mission_launch", lambda m: launched.append(m))
    resp = await orch._handle_mission_command("!mission start test")
    body = await _read_sse(resp)
    assert "Misiune pornită" in body and len(launched) == 1


@pytest.mark.asyncio
async def test_command_status_no_mission(mdb):
    resp = await mdb._handle_mission_command("!mission status")
    body = await _read_sse(resp)
    assert "Nicio misiune" in body


@pytest.mark.asyncio
async def test_command_start_busy_when_active(mdb, tmp_path, monkeypatch):
    orch = mdb
    _write_mission(orch, tmp_path, _TWO_WP)
    monkeypatch.setattr(orch, "_active_mission_id", "ceva")
    monkeypatch.setattr(orch, "_mission_row", lambda m: {"title": "Alta"})
    resp = await orch._handle_mission_command("!mission start test")
    body = await _read_sse(resp)
    assert "rulează deja" in body
