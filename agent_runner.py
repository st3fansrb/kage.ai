"""AgentRunner — executor de agenți peste claude-agent-sdk (WP9 / #4).

Înlocuiește spawn-ul subprocess `claude -p` cu SDK-ul oficial:

- **deltas reale** (`StreamEvent`/`content_block_delta`) în loc de chunking-ul
  finalului — repară D5 (streaming simulat);
- **resume** de sesiuni (`session_id` ↔ `sdk_session_id`, mapate în SQLite de
  orchestrator) — un follow-up continuă efectiv conversația;
- **hook PreToolUse in-proces** care refolosește matricea din
  `risk_hook.evaluate_risk` (o singură sursă de adevăr) prin callback-ul
  `can_use_tool` al SDK-ului — fără roundtrip HTTP la `/risk/register`;
- **inactivity timeout** (reset la fiecare eveniment) în loc de deadline fix 120s.

Runtime: **Python 3.12** (SDK cere ≥3.10). `risk_hook.py` rămâne intact ca hook
CLI pentru căile ne-SDK (fallback-ul Claude CLI).

Decuplat de `orchestrator.py`: primește gate-ul de aprobare ca `approval_cb`
injectat, ca să nu creeze un import cycle și să rămână testabil izolat.

Fluxul de evenimente normalizate emise de `run()` (dict-uri simple, ușor de mapat
în SSE + `run_events`):

    {"type": "text",        "text": str}                      # delta de răspuns
    {"type": "tool_use",    "name": str, "input": dict, "id": str}
    {"type": "tool_result", "content": str, "is_error": bool}
    {"type": "result",      "session_id": str|None, "cost_usd": float|None,
                            "duration_ms": int|None, "text": str, "is_error": bool}
    {"type": "error",       "error": str}
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from risk_hook import evaluate_risk

logger = logging.getLogger("kage.agent_runner")

# Import lazy/tolerant: dacă SDK-ul lipsește (ex. rulăm încă pe 3.9 undeva),
# orchestratorul tot importă — degradează la subprocess-ul legacy.
try:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        PermissionResultAllow,
        PermissionResultDeny,
        ResultMessage,
        StreamEvent,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )
    SDK_AVAILABLE = True
    SDK_IMPORT_ERROR: Optional[str] = None
except Exception as _e:  # pragma: no cover - depinde de runtime
    SDK_AVAILABLE = False
    SDK_IMPORT_ERROR = str(_e)


# Callback de aprobare injectat de orchestrator: (tool_name, tool_input, level,
# reason) -> "confirm" | "block". Emite butoanele Telegram + așteaptă decizia.
ApprovalCallback = Callable[[str, dict, str, str], Awaitable[str]]


def _map_sync(msg: Any) -> list[dict]:
    """Traduce un mesaj SDK în 0..N evenimente normalizate.

    Pur (fără I/O) → testabil fără CLI. Blocurile `TextBlock` din `AssistantMessage`
    sunt SĂRITE intenționat: textul vine deja ca delte prin `StreamEvent`, altfel
    l-am dubla. Din `AssistantMessage` scoatem doar `tool_use`.
    """
    out: list[dict] = []
    if isinstance(msg, StreamEvent):
        ev = msg.event or {}
        if ev.get("type") == "content_block_delta":
            delta = ev.get("delta") or {}
            if delta.get("type") == "text_delta":
                text = delta.get("text") or ""
                if text:
                    out.append({"type": "text", "text": text})
        return out

    if isinstance(msg, AssistantMessage):
        for block in (msg.content or []):
            if isinstance(block, ToolUseBlock):
                out.append({
                    "type": "tool_use",
                    "name": block.name,
                    "input": block.input,
                    "id": block.id,
                })
        return out

    if isinstance(msg, UserMessage):
        content = msg.content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, ToolResultBlock):
                    c = block.content
                    if isinstance(c, list):
                        c = " ".join(
                            str(p.get("text", p)) if isinstance(p, dict) else str(p)
                            for p in c
                        )
                    out.append({
                        "type": "tool_result",
                        "content": str(c)[:2000] if c is not None else "",
                        "is_error": bool(getattr(block, "is_error", False)),
                    })
        return out

    if isinstance(msg, ResultMessage):
        out.append({
            "type": "result",
            "session_id": msg.session_id,
            "cost_usd": msg.total_cost_usd,
            "duration_ms": msg.duration_ms,
            "text": msg.result or "",
            "is_error": bool(msg.is_error),
        })
        return out

    return out


class AgentRunner:
    """Rulează un agent Claude prin SDK și emite evenimente normalizate.

    Nu ține stare per-rulare — o instanță e reutilizabilă. Kill switch-ul
    (WP-G1) se face prin `active_clients`: `stop_all()` cheamă `interrupt()` pe
    fiecare client viu (echivalentul SIGTERM de la subprocess-uri).
    """

    def __init__(self) -> None:
        self.active_clients: set = set()

    async def stop_all(self) -> int:
        """Întrerupe toate rulările SDK vii (folosit de !stop). Întoarce câte."""
        n = 0
        for client in list(self.active_clients):
            try:
                await client.interrupt()
                n += 1
            except Exception as e:  # pragma: no cover
                logger.warning(f"[AgentRunner] interrupt eșuat: {e}")
        return n

    def _make_gate(
        self,
        user_message: str,
        autonomous: bool,
        approval_cb: Optional[ApprovalCallback],
    ):
        """Construiește callback-ul `can_use_tool` din matricea de risc.

        Never → deny direct. High (sau Medium în autonomous_mode) → cere aprobare
        prin `approval_cb`; fără canal de aprobare = deny (fail-closed). Restul →
        allow. Downgrade-ul High→Medium pe „instrucție explicită" e deja în
        `evaluate_risk` (folosește `user_message`).
        """
        async def _gate(tool_name: str, tool_input: dict, ctx: Any):
            try:
                level, reason = evaluate_risk(tool_name, tool_input, user_message)
            except Exception as e:  # pragma: no cover
                logger.warning(f"[AgentRunner] evaluate_risk a eșuat, permit: {e}")
                return PermissionResultAllow()

            if level == "Never":
                return PermissionResultDeny(message=f"[Never] {reason}")

            if level == "High" or (level == "Medium" and autonomous):
                if approval_cb is None:
                    return PermissionResultDeny(
                        message=f"[{level}] {reason} (fără canal de aprobare)")
                try:
                    decision = await approval_cb(tool_name, tool_input, level, reason)
                except Exception as e:  # pragma: no cover
                    logger.warning(f"[AgentRunner] approval_cb a eșuat: {e}")
                    decision = "block"
                if decision == "confirm":
                    return PermissionResultAllow()
                return PermissionResultDeny(message=f"[{level}] {reason}")

            return PermissionResultAllow()

        return _gate

    async def run(
        self,
        prompt: str,
        *,
        user_message: str,
        model: Optional[str] = None,
        cwd: Optional[str] = None,
        resume: Optional[str] = None,
        allowed_tools: Optional[list] = None,
        disallowed_tools: Optional[list] = None,
        permission_mode: str = "auto",
        inactivity_timeout: float = 180.0,
        autonomous: bool = False,
        approval_cb: Optional[ApprovalCallback] = None,
        env: Optional[dict] = None,
    ) -> AsyncIterator[dict]:
        """Rulează agentul și emite evenimente normalizate (vezi antetul modulului).

        `inactivity_timeout` se resetează la FIECARE mesaj primit — un task lung dar
        activ nu e ucis (repară deadline-ul fix de 120s / D5). Tăcere > timeout →
        `interrupt()` + eveniment `error` + stop.
        """
        if not SDK_AVAILABLE:
            yield {"type": "error", "error": f"claude-agent-sdk indisponibil: {SDK_IMPORT_ERROR}"}
            return

        options = ClaudeAgentOptions(
            model=model,
            cwd=cwd,
            resume=resume,
            include_partial_messages=True,
            allowed_tools=allowed_tools or [],
            disallowed_tools=disallowed_tools or [],
            permission_mode=permission_mode,
            can_use_tool=self._make_gate(user_message, autonomous, approval_cb),
            env=env or {},
        )

        streamed_text = False
        client = None
        try:
            client = ClaudeSDKClient(options=options)
            await client.connect()
            self.active_clients.add(client)
            await client.query(prompt)

            aiter = client.receive_response()
            while True:
                try:
                    msg = await asyncio.wait_for(
                        aiter.__anext__(), timeout=inactivity_timeout)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    try:
                        await client.interrupt()
                    except Exception:
                        pass
                    yield {"type": "error",
                           "error": f"inactivity timeout {inactivity_timeout:.0f}s — task oprit"}
                    return

                for ev in _map_sync(msg):
                    if ev["type"] == "text":
                        streamed_text = True
                        yield ev
                    elif ev["type"] == "result":
                        # Fallback: dacă nu s-a streamat text dar avem rezultat final
                        # (ex. răspuns foarte scurt fără delte), emite-l ca text.
                        if not streamed_text and ev["text"]:
                            yield {"type": "text", "text": ev["text"]}
                        yield ev
                    else:
                        yield ev
        except Exception as e:
            logger.error(f"[AgentRunner] rulare eșuată: {e}")
            yield {"type": "error", "error": str(e)}
        finally:
            if client is not None:
                self.active_clients.discard(client)
                try:
                    await client.disconnect()
                except Exception:
                    pass
