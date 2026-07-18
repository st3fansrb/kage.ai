"""Teste pentru hardening-ul gateway-ului Telegram pe fluxul video WP-V."""
import asyncio
import subprocess
import sys
from pathlib import Path

import httpx

import telegram_gateway
import video_intel as vi


def _gateway():
    return telegram_gateway.TelegramGateway(
        bot_token="t", chat_id="123", orchestrator_base_url="http://x", api_token="k"
    )


def test_canonical_video_url_removes_tracking_but_keeps_identity():
    assert vi.canonical_video_url(
        "HTTPS://www.YouTube.com/watch?utm_source=telegram&v=abc&fbclid=no#fragment"
    ) == "https://youtube.com/watch?v=abc"


async def test_gateway_video_inflight_deduplicates_equivalent_urls(monkeypatch):
    gateway = _gateway()
    sent = []
    started = asyncio.Event()
    release = asyncio.Event()
    requests = []

    async def fake_send(text, reply_markup=None):
        sent.append(text)

    class Response:
        status_code = 200

        def json(self):
            return {"ok": True, "id": "v1", "card": {"text": "card", "buttons": []}}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            requests.append((url, json))
            started.set()
            await release.wait()
            return Response()

    gateway.send = fake_send
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: Client())

    first = asyncio.create_task(
        gateway._handle_video("https://www.youtube.com/watch?v=abc&utm_source=telegram")
    )
    await started.wait()
    await gateway._handle_video("https://youtube.com/watch?v=abc")
    release.set()
    await first

    assert len(requests) == 1
    assert any("deja în curs" in message for message in sent)


async def test_gateway_video_callback_success_is_deduplicated(monkeypatch):
    gateway = _gateway()
    sent = []
    requests = []

    async def fake_send(text, reply_markup=None):
        sent.append(text)

    class Response:
        status_code = 200

        def json(self):
            return {"ok": True}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None):
            requests.append(url)
            return Response()

    gateway.send = fake_send
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: Client())

    await gateway._handle_video_callback("video:deep:v1")
    await gateway._handle_video_callback("video:deep:v1")

    assert requests == ["http://x/video/deep/v1"]
    assert any("deja în curs" in message for message in sent)


async def test_gateway_video_failure_is_retryable(monkeypatch):
    gateway = _gateway()
    sent = []
    statuses = [500, 200]

    async def fake_send(text, reply_markup=None):
        sent.append(text)

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

        def json(self):
            return {"ok": self.status_code == 200, "id": "v1", "card": {"text": "card"}}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            return Response(statuses.pop(0))

    gateway.send = fake_send
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: Client())
    gateway.send_video_card = fake_send

    await gateway._handle_video("https://youtu.be/abc")
    await gateway._handle_video("https://youtu.be/abc")

    assert statuses == []
    assert any("HTTP 500" in message for message in sent)


async def test_gateway_callback_rejects_other_chat_before_dispatch(monkeypatch):
    gateway = _gateway()
    acknowledged = []
    dispatched = []

    async def fake_tg_post(method, payload):
        acknowledged.append((method, payload))
        return {"ok": True}

    async def fake_deploy(data):
        dispatched.append(data)

    gateway._tg_post = fake_tg_post
    gateway._handle_deploy_callback = fake_deploy

    await gateway._handle_callback({
        "id": "callback-1",
        "data": "deploy:confirm:0",
        "message": {"chat": {"id": "999"}},
    })

    assert acknowledged == [("answerCallbackQuery", {"callback_query_id": "callback-1"})]
    assert dispatched == []


async def test_gateway_callback_allows_configured_chat(monkeypatch):
    gateway = _gateway()
    dispatched = []

    async def fake_tg_post(method, payload):
        return {"ok": True}

    async def fake_deploy(data):
        dispatched.append(data)

    gateway._tg_post = fake_tg_post
    gateway._handle_deploy_callback = fake_deploy

    await gateway._handle_callback({
        "id": "callback-2",
        "data": "deploy:confirm:0",
        "message": {"chat": {"id": "123"}},
    })

    assert dispatched == ["deploy:confirm:0"]


def test_video_intel_replay_pack_is_green():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/replay_video_intel.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3/3 fixture-uri verzi" in result.stdout
