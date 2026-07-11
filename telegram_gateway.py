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
        # WP12: după ✏️ pe cardul de schiță, următorul mesaj liber = instrucțiuni de revizie.
        self._pending_revise = False

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

    async def send_mission_question(
        self, request_id: str, question: str, options: list
    ) -> None:
        """Puntea de decizii (WP11 §3): o misiune blocată pe o decizie trimite
        întrebarea + opțiunile ca butoane inline. Răspunsul se injectează înapoi în
        runner prin /mission/answer/{id}."""
        text = f"🤔 <b>Decizie misiune</b>\n\n{_escape(question)}"
        row = [{"text": _escape(str(o))[:32], "callback_data": f"mission:{o}:{request_id}"}
               for o in (options or ["ok"])[:4]]
        await self.send(text, reply_markup={"inline_keyboard": [row]})

    async def send_mission_draft(self, mission_id: str, title: str, wp_titles: list) -> None:
        """WP12: schița unei misiuni redactate de Kage (`!mission new`), cu butoane
        ✅ Pornește / ✏️ Revizuiește / 🗑 Renunță. La ✏️ următorul mesaj liber devine
        instrucțiuni de revizie."""
        lines = [f"📝 <b>Schiță de misiune</b>: {_escape(str(title))}", ""]
        for i, t in enumerate(wp_titles[:12], 1):
            lines.append(f"  {i}. {_escape(str(t))}")
        if not wp_titles:
            lines.append("  <i>(niciun pachet de lucru — revizuiește)</i>")
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Pornește", "callback_data": f"missiondraft:start:{mission_id}"},
                {"text": "✏️ Revizuiește", "callback_data": f"missiondraft:revise:{mission_id}"},
                {"text": "🗑 Renunță", "callback_data": f"missiondraft:discard:{mission_id}"},
            ]]
        }
        await self.send("\n".join(lines), reply_markup=keyboard)

    async def send_job_card(self, job: dict) -> None:
        """Trimite un card de job (WP-J) cu butoane inline 🔖/✍️/🗑.
        `job` are cheile: hash, title, company, location, url, score."""
        jhash = job.get("hash", "")
        score = job.get("score")
        score_str = f" · scor {score}/10" if score is not None else ""
        title = _escape(str(job.get("title", "(fără titlu)")))
        company = _escape(str(job.get("company", "")))
        location = _escape(str(job.get("location", "")))
        url = str(job.get("url", ""))
        header = f"💼 <b>{title}</b>{score_str}"
        lines = [header]
        if company:
            lines.append(f"🏢 {company}")
        if location:
            lines.append(f"📍 {location}")
        if url:
            lines.append(f'<a href="{_escape(url)}">🔗 vezi anunțul</a>')
        keyboard = {
            "inline_keyboard": [[
                {"text": "🔖 Salvează", "callback_data": f"job:save:{jhash}"},
                {"text": "✍️ Aplică", "callback_data": f"job:apply:{jhash}"},
                {"text": "🗑 Ignoră", "callback_data": f"job:ignore:{jhash}"},
            ]]
        }
        await self.send("\n".join(lines), reply_markup=keyboard)

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
                return
            # WP6: voice memo → transcriere locală (whisper.cpp) → pipeline normal
            voice = msg.get("voice") or msg.get("audio")
            if voice and voice.get("file_id"):
                await self._handle_voice(voice["file_id"])

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
                "• <code>!schedule</code> — task programat\n"
                "• <code>!scan</code> — caută joburi noi (WP-J)\n\n"
                "🎙 Mesaj vocal — transcris local și trimis la Kage.\n"
                "Orice alt mesaj merge direct la Kage."
            )
            return

        if text == "/status":
            text = "!status"

        # WP12: dacă tocmai s-a cerut o revizie de schiță (✏️), primul mesaj liber (nu comandă)
        # devine instrucțiunile de revizie. O comandă (`!`/`/`) anulează așteptarea.
        if self._pending_revise:
            self._pending_revise = False
            if not text.startswith(("!", "/")):
                await self._forward_to_orchestrator(f"!mission revise {text}")
                return

        # Rutare la orchestrator
        await self._forward_to_orchestrator(text)

    async def _handle_voice(self, file_id: str) -> None:
        """WP6: descarcă voice memo-ul, îl transcrie local (endpoint orchestrator)
        și trimite textul mai departe în pipeline-ul normal de chat."""
        try:
            # 1. getFile → file_path pe serverele Telegram
            info = await self._tg_post("getFile", {"file_id": file_id})
            file_path = info.get("result", {}).get("file_path", "") if info.get("ok") else ""
            if not file_path:
                await self.send("⚠️ Nu am putut prelua fișierul vocal.")
                return
            # 2. Download OGG (Opus) din API-ul de fișiere Telegram
            if not self._client:
                return
            dl = await self._client.get(
                f"https://api.telegram.org/file/bot{self._token}/{file_path}",
                timeout=30,
            )
            if dl.status_code != 200 or not dl.content:
                await self.send("⚠️ Descărcarea fișierului vocal a eșuat.")
                return
            # 3. Transcriere 100% locală prin orchestrator (/v1/audio/transcriptions)
            headers = {}
            if self._api_token:
                headers["Authorization"] = f"Bearer {self._api_token}"
            suffix = file_path.rsplit(".", 1)[-1] if "." in file_path else "ogg"
            files = {"file": (f"voice.{suffix}", dl.content, "application/ogg")}
            async with httpx.AsyncClient(timeout=330) as client:
                tr = await client.post(
                    f"{self._orchestrator_url}/v1/audio/transcriptions",
                    files=files,
                    headers=headers,
                )
            if tr.status_code == 503:
                await self.send("🎙 Transcrierea vocală nu e configurată (whisper.cpp neinstalat).")
                return
            if tr.status_code != 200:
                await self.send(f"⚠️ Transcriere eșuată: HTTP {tr.status_code}")
                return
            transcript = (tr.json().get("text") or "").strip()
            if not transcript:
                await self.send("🎙 N-am înțeles nimic din mesajul vocal.")
                return
            # 4. Confirmă ce a înțeles + trimite textul în pipeline
            await self.send(f"📝 Am înțeles: <i>{_escape(transcript)}</i>")
            await self._forward_to_orchestrator(transcript)
        except Exception as e:
            logger.error(f"[TelegramGateway] voice handling failed: {e}")
            await self.send("⚠️ Eroare internă la procesarea mesajului vocal.")

    async def _handle_callback(self, callback_query: dict) -> None:
        """Procesează apăsare buton inline (aprobare/blocare risc)."""
        data = callback_query.get("data", "")
        callback_id = callback_query.get("id", "")
        chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))

        # Confirmă primirea (elimină loading din Telegram)
        await self._tg_post("answerCallbackQuery", {"callback_query_id": callback_id})

        if data.startswith("risk:"):
            await self._handle_risk_callback(data)
        elif data.startswith("job:"):
            await self._handle_job_callback(data)
        elif data.startswith("missiondraft:"):
            await self._handle_mission_draft_callback(data)
        elif data.startswith("mission:"):
            await self._handle_mission_callback(data)

    async def _handle_risk_callback(self, data: str) -> None:
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

    async def _handle_mission_callback(self, data: str) -> None:
        """Buton de decizie misiune (WP11): mission:<answer>:<request_id> →
        /mission/answer/{id}. Deblochează runner-ul cu răspunsul ales."""
        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        _, answer, request_id = parts
        try:
            headers = {}
            if self._api_token:
                headers["Authorization"] = f"Bearer {self._api_token}"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._orchestrator_url}/mission/answer/{request_id}",
                    json={"answer": answer},
                    headers=headers,
                )
            if resp.status_code == 200:
                await self.send(f"↩️ Răspuns înregistrat: <b>{_escape(answer)}</b>")
            else:
                await self.send(f"⚠️ Eroare decizie misiune: HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[TelegramGateway] mission callback failed: {e}")
            await self.send("⚠️ Eroare internă la procesare decizie.")

    async def _handle_mission_draft_callback(self, data: str) -> None:
        """WP12: butoanele cardului de schiță — missiondraft:start|revise|discard:<id>.
        `revise` doar armează captura următorului mesaj liber; start/discard → endpoint."""
        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        _, action, mission_id = parts
        if action == "revise":
            self._pending_revise = True
            await self.send("✏️ Răspunde cu ce să modific în plan (un singur mesaj).")
            return
        try:
            headers = {}
            if self._api_token:
                headers["Authorization"] = f"Bearer {self._api_token}"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self._orchestrator_url}/mission/draft/{mission_id}/{action}",
                    headers=headers,
                )
            body = resp.json() if resp.status_code == 200 else {}
            if resp.status_code == 200 and body.get("ok"):
                if action == "start":
                    await self.send(f"🚀 Pornesc misiunea: <b>{_escape(str(body.get('detail', '')))}</b>.")
                else:
                    await self.send(f"🗑 Schiță ștearsă: {_escape(str(body.get('detail', '')))}.")
            else:
                detail = body.get("detail") if body else f"HTTP {resp.status_code}"
                await self.send(f"⚠️ Nu am putut {action}: {_escape(str(detail))}")
        except Exception as e:
            logger.error(f"[TelegramGateway] mission draft callback failed: {e}")
            await self.send("⚠️ Eroare internă la procesarea schiței.")

    async def _handle_job_callback(self, data: str) -> None:
        """Butoane job (WP-J): job:save|apply|ignore:<hash> → endpoint /jobs/*."""
        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        _, action, jhash = parts
        if action == "apply":
            path = f"/jobs/apply/{jhash}"
        elif action in ("save", "ignore"):
            path = f"/jobs/action/{action}/{jhash}"
        else:
            return
        try:
            headers = {}
            if self._api_token:
                headers["Authorization"] = f"Bearer {self._api_token}"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self._orchestrator_url}{path}", headers=headers)
            if resp.status_code == 200:
                if action == "save":
                    await self.send("🔖 Job salvat.")
                elif action == "ignore":
                    await self.send("🗑 Job ignorat (nu revine).")
                else:
                    msg = resp.json().get("message", "career-ops pornit")
                    await self.send(f"✍️ {_escape(str(msg))}")
            else:
                await self.send(f"⚠️ Eroare job ({action}): HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[TelegramGateway] job callback failed: {e}")
            await self.send("⚠️ Eroare internă la procesare job.")

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
