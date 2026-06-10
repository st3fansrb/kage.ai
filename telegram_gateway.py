"""
Telegram Bot Gateway pentru Kage — înlocuitor bidirecțional pentru ntfy.sh.

Funcționalitate:
- Trimite notificări (budget, risc, task done) ca mesaje Telegram
- Trimite butoane inline Confirmă/Blochează pentru aprobare risc
- Primește comenzi (!status, !run, etc.) și le rutează la orchestrator
- Session izolată: comenzile din Telegram folosesc session_id "telegram_{chat_id}"
- Degradare gracioasă: dacă token/chat_id lipsesc, gateway-ul nu pornește
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_PREVIEW_MAX_CHARS = 800
_POLL_TIMEOUT = 30  # secunde, long-polling Telegram


class TelegramGateway:
    def __init__(
        self,
        bot_token: str,
        chat_id: str | int,
        orchestrator_base_url: str,
        api_token: str,
    ):
        self._token = bot_token
        self._chat_id = str(chat_id)
        self._orchestrator_url = orchestrator_base_url.rstrip("/")
        self._api_token = api_token
        self._api_base = f"https://api.telegram.org/bot{bot_token}"
        self._offset = 0
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=_POLL_TIMEOUT + 5)
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(), name="telegram_poll")
        logger.info("[TelegramGateway] polling started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
        logger.info("[TelegramGateway] stopped")

    # ── Public API (apelat din orchestrator.py) ────────────────────────────────

    async def send(self, text: str, reply_markup: Optional[dict] = None) -> None:
        """Trimite un mesaj simplu în chat-ul configurat."""
        payload: dict = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        await self._tg_post("sendMessage", payload)

    async def send_notification(self, title: str, body: str, priority: str = "default") -> None:
        """Înlocuitor async pentru _send_ntfy_sync — trimite alertă simplă."""
        icon = {"high": "🔴", "urgent": "🚨"}.get(priority, "🔔")
        text = f"{icon} <b>{_escape(title)}</b>\n{_escape(body)}"
        await self.send(text)

    async def send_risk_approval(
        self, request_id: str, tool_name: str, cmd: str, reason: str
    ) -> None:
        """Trimite notificare de risc cu butoane inline Confirmă/Blochează."""
        cmd_preview = cmd[:300] + ("…" if len(cmd) > 300 else "")
        text = (
            f"⚠️ <b>Aprobare risc necesară</b>\n\n"
            f"🔧 <b>Tool:</b> <code>{_escape(tool_name)}</code>\n"
            f"📋 <b>Comandă:</b>\n<pre>{_escape(cmd_preview)}</pre>\n"
            f"💡 <b>Motiv:</b> {_escape(reason)}"
        )
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Confirmă", "callback_data": f"risk:confirm:{request_id}"},
                {"text": "🚫 Blochează", "callback_data": f"risk:block:{request_id}"},
            ]]
        }
        await self.send(text, reply_markup=keyboard)

    # ── Polling loop ───────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        consecutive_errors = 0
        while self._running:
            try:
                updates = await self._get_updates()
                consecutive_errors = 0
                for update in updates:
                    self._offset = update["update_id"] + 1
                    await self._dispatch(update)
            except asyncio.CancelledError:
                break
            except Exception as e:
                consecutive_errors += 1
                wait = min(30, 2 ** consecutive_errors)
                logger.warning(f"[TelegramGateway] poll error ({consecutive_errors}): {e}. Retry in {wait}s")
                await asyncio.sleep(wait)

    async def _get_updates(self) -> list[dict]:
        if not self._client:
            return []
        resp = await self._client.post(
            f"{self._api_base}/getUpdates",
            json={"offset": self._offset, "timeout": _POLL_TIMEOUT, "allowed_updates": ["message", "callback_query"]},
            timeout=_POLL_TIMEOUT + 10,
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"getUpdates error: {data}")
        return data.get("result", [])

    async def _dispatch(self, update: dict) -> None:
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
        elif "message" in update:
            msg = update["message"]
            # Acceptă doar mesaje din chat-ul configurat (securitate)
            if str(msg.get("chat", {}).get("id", "")) != self._chat_id:
                return
            text = msg.get("text", "").strip()
            if text:
                await self._handle_message(text)

    # ── Handlers ───────────────────────────────────────────────────────────────

    async def _handle_message(self, text: str) -> None:
        """Rute mesaje text → orchestrator sau răspuns local."""
        # Comenzi Telegram native
        if text == "/help":
            await self.send(
                "🤖 <b>Kage Bot</b>\n\n"
                "Comenzi disponibile:\n"
                "• /status — starea sistemului\n"
                "• /help — acest mesaj\n\n"
                "Prefixe Kage:\n"
                "• <code>!fast</code> — răspuns rapid (local)\n"
                "• <code>!best</code> — model cel mai bun\n"
                "• <code>!run &lt;task&gt;</code> — agent background\n"
                "• <code>!status</code> — statistici live\n"
                "• <code>!swarm &lt;task&gt;</code> — agent paralel\n"
                "• <code>!schedule</code> — task programat\n\n"
                "Orice alt mesaj merge direct la Kage."
            )
            return

        if text == "/status":
            text = "!status"

        # Rutare la orchestrator
        await self._forward_to_orchestrator(text)

    async def _handle_callback(self, callback_query: dict) -> None:
        """Procesează apăsare buton inline (aprobare/blocare risc)."""
        data = callback_query.get("data", "")
        callback_id = callback_query.get("id", "")
        chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))

        # Confirmă primirea (elimină loading din Telegram)
        await self._tg_post("answerCallbackQuery", {"callback_query_id": callback_id})

        if not data.startswith("risk:"):
            return

        parts = data.split(":", 2)
        if len(parts) != 3:
            return

        _, action, request_id = parts
        tg_action = "confirm" if action == "confirm" else "block"

        # Apel intern la orchestrator
        try:
            headers = {}
            if self._api_token:
                headers["Authorization"] = f"Bearer {self._api_token}"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._orchestrator_url}/risk/respond/{request_id}",
                    json={"action": tg_action},
                    headers=headers,
                )
            if resp.status_code == 200:
                icon = "✅" if tg_action == "confirm" else "🚫"
                label = "Confirmat" if tg_action == "confirm" else "Blocat"
                await self.send(f"{icon} Task {label}.")
            else:
                await self.send(f"⚠️ Eroare la trimitere decizie: HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[TelegramGateway] risk respond failed: {e}")
            await self.send(f"⚠️ Eroare internă la procesare decizie.")

    async def _forward_to_orchestrator(self, text: str) -> None:
        """Trimite mesaj text la /v1/chat/completions și returnează răspunsul în Telegram."""
        session_id = f"telegram_{self._chat_id}"
        headers: dict[str, str] = {"x-session-id": session_id, "Content-Type": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"

        payload = {
            "model": "auto",
            "messages": [{"role": "user", "content": text}],
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self._orchestrator_url}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )

            if resp.status_code != 200:
                await self.send(f"⚠️ Orchestrator error: HTTP {resp.status_code}")
                return

            data = resp.json()
            content: str = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                or "(răspuns gol)"
            )

            if len(content) > _PREVIEW_MAX_CHARS:
                preview = content[:_PREVIEW_MAX_CHARS].rstrip() + "…"
                kage_link = f"{self._orchestrator_url}/chat"
                await self.send(
                    f"{_escape(preview)}\n\n"
                    f'<a href="{kage_link}">📖 Răspuns complet în Kage UI</a>'
                )
            else:
                await self.send(_escape(content))

        except Exception as e:
            logger.error(f"[TelegramGateway] forward failed: {e}")
            await self.send(f"⚠️ Eroare la comunicare cu orchestratorul: {e}")

    # ── HTTP helper ────────────────────────────────────────────────────────────

    async def _tg_post(self, method: str, payload: dict) -> dict:
        if not self._client:
            return {}
        try:
            resp = await self._client.post(
                f"{self._api_base}/{method}",
                json=payload,
                timeout=10,
            )
            return resp.json()
        except Exception as e:
            logger.warning(f"[TelegramGateway] {method} failed: {e}")
            return {}


# ── Factory ────────────────────────────────────────────────────────────────────

def init_gateway(cfg: dict) -> Optional[TelegramGateway]:
    """
    Creează gateway dacă config-ul e valid.
    Returnează None dacă token/chat_id lipsesc sau conțin 'CHANGEME'.
    """
    token = cfg.get("telegram_bot_token", "")
    chat_id = cfg.get("telegram_chat_id", "")

    if not token or not chat_id:
        return None
    if "CHANGEME" in str(token) or "CHANGEME" in str(chat_id):
        return None

    orchestrator_url = cfg.get(
        "orchestrator_tailscale_url",
        "http://localhost:4001",
    )
    api_token = cfg.get("api_token", "")

    logger.info(f"[TelegramGateway] inițializat pentru chat_id={chat_id}")
    return TelegramGateway(token, chat_id, orchestrator_url, api_token)


# ── Util ───────────────────────────────────────────────────────────────────────

def _escape(text: str) -> str:
    """Escaped HTML minimal pentru Telegram parse_mode=HTML."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
