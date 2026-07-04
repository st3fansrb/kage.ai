"""Teste e2e pe stratul HTTP al orchestratorului (WP1 / D3).

Aceste teste lovesc `POST /v1/chat/completions` prin `TestClient`, cu LiteLLM și
Ollama simulate prin `respx`. Ele acoperă exact golul prin care a trecut D1
(chat 500 din cauza lui `re`) — nicio dependență de servicii reale.

Ollama și LiteLLM NU rulează în test: respx interceptează cererile httpx pe care le
face orchestratorul (AsyncHTTPTransport), lăsând transportul ASGI al TestClient-ului
să treacă neatins.
"""
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import orchestrator


# ── Fixturi ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(orchestrator.app)


@pytest.fixture(autouse=True)
def _silence_side_effects(monkeypatch):
    """Neutralizează efectele secundare pe disc/rețea ale unei cereri de chat
    (status.json, log în vault, notificări) ca testele să fie hermetice."""
    monkeypatch.setattr(orchestrator, "_write_status", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_write_status_idle", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_log_to_vault", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_notify", lambda *a, **k: None)
    # Dezactivează auth-ul (mașina reală poate avea un api_token în config)
    monkeypatch.setattr(orchestrator, "_get_api_token", lambda: "")


def _mock_backends(router):
    """Înregistrează pe routerul respx răspunsuri deterministe pentru LiteLLM + Ollama."""
    # Ollama: clasificare → digit "1" ; embeddings → vector scurt
    router.post(f"{orchestrator.OLLAMA_URL}/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "1"}})
    )
    router.post(f"{orchestrator.OLLAMA_URL}/api/embeddings").mock(
        return_value=httpx.Response(200, json={"embedding": [0.0, 0.1, 0.2]})
    )
    # LiteLLM: stream SSE OpenAI-style
    sse = (
        'data: {"choices":[{"delta":{"content":"Salut, "},"index":0}]}\n\n'
        'data: {"choices":[{"delta":{"content":"sunt Kage."},"index":0}]}\n\n'
        "data: [DONE]\n\n"
    )
    router.post(f"{orchestrator.LITELLM_URL}/chat/completions").mock(
        return_value=httpx.Response(
            200, content=sse.encode(), headers={"content-type": "text/event-stream"}
        )
    )


# ── Test 1: chat normal (stream implicit) → 200 + SSE parsabil ────────────────

@respx.mock
def test_chat_stream_returns_parsable_sse(client):
    _mock_backends(respx.mock)
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "salut"}]},
    )
    assert resp.status_code == 200

    deltas = []
    saw_done = False
    for line in resp.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload.strip() == "[DONE]":
            saw_done = True
            continue
        obj = json.loads(payload)  # trebuie să fie JSON valid
        content = obj["choices"][0]["delta"].get("content", "")
        if content:
            deltas.append(content)

    assert saw_done, "streamul trebuie să se termine cu [DONE]"
    joined = "".join(deltas)
    assert "sunt Kage." in joined


# ── Test 2: stream:false → un singur JSON OpenAI valid (D2) ────────────────────

@respx.mock
def test_chat_non_stream_returns_openai_json(client):
    _mock_backends(respx.mock)
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "salut"}], "stream": False},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")

    data = resp.json()  # NU trebuie să fie SSE — exact bug-ul care rupea Telegram
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert "sunt Kage." in data["choices"][0]["message"]["content"]


# ── Test 3: !run → task pornit, confirmare cu id ──────────────────────────────

@respx.mock
def test_run_command_launches_task(client, monkeypatch):
    _mock_backends(respx.mock)

    launched = {}

    async def _fake_exec(task_id, task_text, agent, cwd, is_sysrun, parent_id=None):
        launched["task_id"] = task_id
        launched["task_text"] = task_text
        launched["agent"] = agent

    monkeypatch.setattr(orchestrator, "_background_task_exec", _fake_exec)
    # confinement dezactivat pentru test (allowed_task_roots gol → orice cwd permis)
    monkeypatch.setattr(orchestrator, "ALLOWED_TASK_ROOTS", [])

    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "!run listează fișierele"}]},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "Task pornit" in body
    # id-ul din confirmare trebuie să fie cel cu care s-a spawn-at execuția
    assert launched.get("task_id") is not None
    assert launched["task_id"] in body
    assert launched["task_text"] == "listează fișierele"


# ── Test 4: !run blocat de confinement → [BLOCKED], fără spawn ─────────────────

@respx.mock
def test_run_command_blocked_by_confinement(client, monkeypatch):
    _mock_backends(respx.mock)

    called = {"spawned": False}

    async def _fake_exec(*a, **k):
        called["spawned"] = True

    monkeypatch.setattr(orchestrator, "_background_task_exec", _fake_exec)
    # confinement activ cu un root care NU include home → !run (cwd=home) e blocat
    monkeypatch.setattr(orchestrator, "ALLOWED_TASK_ROOTS", [orchestrator.PROJECT_ROOT / "cache_db"])

    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "!run rm -rf /"}]},
    )
    assert resp.status_code == 200
    assert "[BLOCKED]" in resp.text
    assert called["spawned"] is False
