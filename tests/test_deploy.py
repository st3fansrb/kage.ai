"""Teste WP-SD — `!deploy`: pull + restart pe checkout-ul viu, DOAR pe confirmare
explicită de buton. `_handle_deploy_command`/`/deploy/confirm` cu subprocess mock-uit
(fără git/restart real); `_trigger_restart` verificat separat (Popen mock-uit — nu
pornește procese reale); gateway-ul Telegram (cardul + callback-urile).
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import orchestrator
import telegram_gateway


def _proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ── _handle_deploy_command ──────────────────────────────────────────────────────

async def test_deploy_command_reports_up_to_date(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[-1] == "fetch":
            return _proc()
        if "rev-parse" in cmd:
            return _proc(stdout="dev\n")
        if "rev-list" in cmd:
            return _proc(stdout="0\n")
        raise AssertionError(f"unexpected git call: {cmd}")

    monkeypatch.setattr(orchestrator.subprocess, "run", fake_run)
    resp = await orchestrator._handle_deploy_command()
    body = "".join([c async for c in resp.body_iterator])
    assert "deja la zi" in body
    assert "dev" in body


async def test_deploy_command_sends_card_when_behind(monkeypatch):
    def fake_run(cmd, **kw):
        if cmd[-1] == "fetch":
            return _proc()
        if "rev-parse" in cmd:
            return _proc(stdout="dev\n")
        if "rev-list" in cmd:
            return _proc(stdout="3\n")
        raise AssertionError(f"unexpected git call: {cmd}")

    monkeypatch.setattr(orchestrator.subprocess, "run", fake_run)
    sent = []

    class _FakeGw:
        async def send_deploy_card(self, branch, detail):
            sent.append((branch, detail))

    monkeypatch.setattr(orchestrator, "_tg_gateway", _FakeGw())
    resp = await orchestrator._handle_deploy_command()
    body = "".join([c async for c in resp.body_iterator])
    assert "3 commit-uri noi" in body
    import asyncio
    await asyncio.sleep(0)   # lasă task-ul create_task să ruleze
    assert sent == [("dev", "3 commit-uri noi")]


async def test_deploy_command_git_error_is_graceful(monkeypatch):
    def fake_run(cmd, **kw):
        raise RuntimeError("git indisponibil")
    monkeypatch.setattr(orchestrator.subprocess, "run", fake_run)
    resp = await orchestrator._handle_deploy_command()
    body = "".join([c async for c in resp.body_iterator])
    # SSE-ul e JSON (ensure_ascii) — diacriticele/emoji apar ca \uXXXX, nu literal.
    assert "Verificarea git" in body and "git indisponibil" in body


# ── /deploy/confirm ──────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(orchestrator, "_get_api_token", lambda: "")
    return TestClient(orchestrator.app)


def test_deploy_confirm_pulls_and_triggers_restart(client, monkeypatch):
    def fake_run(cmd, **kw):
        assert cmd[:2] == ["git", "-C"]
        assert cmd[3] == "pull"
        return _proc(stdout="Updating abc..def\n 2 files changed")
    monkeypatch.setattr(orchestrator.subprocess, "run", fake_run)
    triggered = []
    monkeypatch.setattr(orchestrator, "_trigger_restart", lambda: triggered.append(1))

    resp = client.post("/deploy/confirm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "Updating" in body["detail"]
    assert triggered == [1]


def test_deploy_confirm_ff_only_failure_does_not_restart(client, monkeypatch):
    def fake_run(cmd, **kw):
        return _proc(returncode=1, stderr="fatal: not possible to fast-forward")
    monkeypatch.setattr(orchestrator.subprocess, "run", fake_run)
    triggered = []
    monkeypatch.setattr(orchestrator, "_trigger_restart", lambda: triggered.append(1))

    resp = client.post("/deploy/confirm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "fast-forward" in body["detail"]
    assert triggered == []   # eșec de pull → NU se restart


def test_deploy_confirm_pull_exception_does_not_restart(client, monkeypatch):
    def fake_run(cmd, **kw):
        raise RuntimeError("git indisponibil")
    monkeypatch.setattr(orchestrator.subprocess, "run", fake_run)
    triggered = []
    monkeypatch.setattr(orchestrator, "_trigger_restart", lambda: triggered.append(1))

    resp = client.post("/deploy/confirm")
    assert resp.json()["ok"] is False
    assert triggered == []


# ── _trigger_restart ─────────────────────────────────────────────────────────────

def test_trigger_restart_spawns_detached_process(monkeypatch):
    calls = []

    class _FakePopen:
        def __init__(self, *a, **k):
            calls.append((a, k))

    monkeypatch.setattr(orchestrator.subprocess, "Popen", _FakePopen)
    orchestrator._trigger_restart()
    assert len(calls) == 1
    args, kwargs = calls[0]
    cmd = args[0]
    assert cmd[0] == "bash" and cmd[1] == "-c"
    assert "stop_all.sh" in cmd[2] and "start_all.sh" in cmd[2]
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True


# ── Gateway: cardul de deploy + callback-uri ─────────────────────────────────────

def _make_gateway():
    return telegram_gateway.TelegramGateway(
        bot_token="t", chat_id="123", orchestrator_base_url="http://x", api_token="k")


async def test_gateway_deploy_card_keyboard():
    gw = _make_gateway()
    sent = {}

    async def _fake_send(text, reply_markup=None):
        sent["text"] = text
        sent["kb"] = reply_markup
    gw.send = _fake_send
    await gw.send_deploy_card("dev", "3 commit-uri noi")
    assert "dev" in sent["text"] and "3 commit-uri noi" in sent["text"]
    buttons = sent["kb"]["inline_keyboard"][0]
    actions = [b["callback_data"] for b in buttons]
    assert actions == ["deploy:confirm:0", "deploy:cancel:0"]


async def test_gateway_deploy_callback_cancel_is_local_noop():
    gw = _make_gateway()
    sent = []

    async def _fake_send(text, reply_markup=None):
        sent.append(text)
    gw.send = _fake_send
    await gw._handle_deploy_callback("deploy:cancel:0")
    assert sent == ["🚫 Deploy anulat."]


async def test_gateway_deploy_callback_confirm_calls_endpoint(monkeypatch):
    gw = _make_gateway()
    sent = []

    async def _fake_send(text, reply_markup=None):
        sent.append(text)
    gw.send = _fake_send

    class _Resp:
        status_code = 200
        def json(self):
            return {"ok": True, "detail": "2 fișiere actualizate"}

    class _FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers=None):
            assert url == "http://x/deploy/confirm"
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient())
    await gw._handle_deploy_callback("deploy:confirm:0")
    assert any("2 fișiere actualizate" in s for s in sent)
    assert any("Repornesc" in s for s in sent)
