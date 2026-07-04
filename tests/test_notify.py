"""Teste pentru rutarea notificărilor (WP1b).

Contractul WP1b: Telegram e canalul primar; ntfy rămâne DOAR fallback dacă
gateway-ul Telegram nu e configurat (sau dacă nu există un event loop activ).
"""
import pytest

import orchestrator


class _FakeGateway:
    """Gateway Telegram simulat — înregistrează notificările trimise."""

    def __init__(self):
        self.sent = []

    async def send_notification(self, title, body, priority="default"):
        self.sent.append((title, body, priority))


@pytest.fixture
def _capture(monkeypatch):
    """Interceptează _send_ntfy_sync și izolează _tg_gateway."""
    ntfy_calls = []
    monkeypatch.setattr(
        orchestrator, "_send_ntfy_sync",
        lambda title, body, priority="default": ntfy_calls.append((title, body, priority)),
    )
    monkeypatch.setattr(orchestrator, "_tg_gateway", None)
    return ntfy_calls


# ── Gateway configurat → Telegram primar, ntfy tăcut ─────────────────────────

async def test_notify_prefers_telegram_when_gateway_up(_capture, monkeypatch):
    gw = _FakeGateway()
    monkeypatch.setattr(orchestrator, "_tg_gateway", gw)

    orchestrator._notify("Titlu", "corp", priority="high")
    # lasă task-ul create_task să ruleze
    import asyncio
    await asyncio.sleep(0)

    assert gw.sent == [("Titlu", "corp", "high")]
    assert _capture == [], "ntfy nu trebuie apelat când gateway-ul Telegram e activ"


# ── Fără gateway → fallback pe ntfy ──────────────────────────────────────────

async def test_notify_falls_back_to_ntfy_without_gateway(_capture):
    orchestrator._notify("Titlu", "corp")
    assert _capture == [("Titlu", "corp", "default")]


# ── Gateway configurat dar fără event loop → tot cade pe ntfy ────────────────

def test_notify_falls_back_when_no_running_loop(_capture, monkeypatch):
    gw = _FakeGateway()
    monkeypatch.setattr(orchestrator, "_tg_gateway", gw)

    # apel din context pur sync (fără loop activ) — create_task ar arunca
    orchestrator._notify("Titlu", "corp")

    assert gw.sent == [], "fără loop activ nu se poate programa send_notification"
    assert _capture == [("Titlu", "corp", "default")], "trebuie să cadă pe ntfy"
