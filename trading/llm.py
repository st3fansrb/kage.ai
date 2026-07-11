"""Client LLM subțire pentru Actor (Qwen local) și Critic (OpenRouter) — WP-T, Etapa 5.

Interfață OpenAI-compatibilă (chat/completions), folosită de:
- Actor → LiteLLM local (`tier-2-worker` = Qwen 35B), zero cost.
- Critic → OpenRouter (model ieftin/capabil), buget-gated (`budget.py`).

Transportul (`poster`) e injectabil: testele pasează un fake care întoarce un răspuns
OpenAI-like fără rețea. Fără dependințe grele — doar stdlib `urllib`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

# poster(url, headers, payload) -> dict (răspuns OpenAI-compatible)
Poster = Callable[[str, dict, dict], dict]


class LLMError(RuntimeError):
    """Apel LLM eșuat (rețea, format de răspuns neașteptat)."""


@dataclass
class ChatResult:
    content: str
    usage: dict = field(default_factory=dict)   # {prompt_tokens, completion_tokens, ...}
    model: str = ""
    cost_usd: Optional[float] = None            # dacă providerul îl întoarce (OpenRouter)


def _urllib_poster(url: str, headers: dict, payload: dict, timeout: float = 120.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={**headers, "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Corpul răspunsului de eroare (LiteLLM/OpenRouter îl pun în JSON) — esențial la debugging.
        try:
            body = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            body = ""
        raise LLMError(f"HTTP {exc.code} de la {url}: {body}") from exc


class ChatClient:
    """Client chat OpenAI-compatible. `poster` injectabil pentru teste."""

    def __init__(
        self, base_url: str, model: str, api_key: str = "",
        poster: Optional[Poster] = None, timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._poster = poster or (lambda u, h, p: _urllib_poster(u, h, p, timeout))

    def chat(
        self, messages: list[dict], temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> ChatResult:
        url = f"{self.base_url}/chat/completions"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict = {
            "model": self.model, "messages": messages, "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        try:
            data = self._poster(url, headers, payload)
        except Exception as exc:  # noqa: BLE001 — orice eroare de transport devine LLMError
            raise LLMError(f"apel LLM eșuat către {url}: {exc}") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"răspuns LLM fără choices/message: {data!r}") from exc
        usage = data.get("usage") or {}
        # OpenRouter poate întoarce costul real în usage.cost (USD).
        cost = None
        if isinstance(usage, dict) and usage.get("cost") is not None:
            try:
                cost = float(usage["cost"])
            except (TypeError, ValueError):
                cost = None
        return ChatResult(content=content, usage=usage, model=data.get("model", self.model), cost_usd=cost)

    def as_callable(self, **kw) -> Callable[[list[dict]], ChatResult]:
        """Adaptor: `chat_fn(messages) -> ChatResult` cu parametri ficși (temperature etc.)."""
        return lambda messages: self.chat(messages, **kw)
