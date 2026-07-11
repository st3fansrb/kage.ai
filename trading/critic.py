"""Critic — filtru de plauzibilitate (WP-T, Etapa 5.2). OpenRouter, 1 apel/noapte, aprobă ≤1.

Rol: NU validează statistic (invariant #5 — asta face `validation.py`). Doar taie absurdul logic
și întreabă „cine ar arbitra asta imediat dacă ar fi adevărat?". Aprobă CEL MULT o singură ipoteză,
ca să economisim compute-ul de backtest pe propunerea cea mai plauzibilă.

Gardă de buget (Etapa 2.3): dacă bugetul lunar OpenRouter e depășit, Criticul rulează pe modelul
LOCAL de fallback (calitate mai slabă acceptată — bucla nu moare). Costul apelului plătit se
înregistrează în `api_costs`.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional, Sequence

from trading.budget import ApiBudget, estimate_usd
from trading.llm import ChatResult

CRITIC_SYSTEM = (
    "Ești un critic sceptic de ipoteze de trading. Nu rulezi statistici. Evaluezi DOAR "
    "plauzibilitatea mecanismului cauzal: e coerent economic? cine ar fi forțat să tranzacționeze? "
    "dacă edge-ul ar fi real, cine l-ar arbitra imediat și l-ar face să dispară? Ești zgârcit: "
    "aprobi cel mult UNA, pe cea mai plauzibilă și cel mai greu de arbitrat instant."
)

_INSTRUCTION = (
    "Ți se dau {n} ipoteze (index 0..{last}). Alege CEL MULT una de investigat. Răspunde DOAR cu "
    "un obiect JSON: {{\"approved_index\": <int sau null>, \"reasoning\": \"<de ce, scurt>\"}}. "
    "Dacă niciuna nu e plauzibilă, approved_index = null."
)


class CriticError(RuntimeError):
    """Răspuns de Critic ne-parsabil."""


def build_messages(proposals: Sequence[dict]) -> list[dict]:
    listing = []
    for i, p in enumerate(proposals):
        listing.append(
            f"[{i}] mecanism: {p.get('mecanism_cauzal', '')}\n"
            f"    predicție: {json.dumps(p.get('predictie_cu_interval', {}), ensure_ascii=False)}\n"
            f"    falsificare: {p.get('criteriu_falsificare', '')}"
        )
    user = "\n".join(listing) + "\n\n" + _INSTRUCTION.format(n=len(proposals), last=len(proposals) - 1)
    return [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": user},
    ]


def parse_verdict(raw: str, n: int) -> dict:
    """Extrage {approved_index, reasoning}. Impune ≤1 și index valid (altfel None)."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i != -1 and j != -1 and j > i:
            text = text[i:j + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CriticError(f"Criticul nu a întors JSON valid: {exc}") from exc
    idx = data.get("approved_index")
    if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or idx >= n:
        idx = None
    return {"approved_index": idx, "reasoning": str(data.get("reasoning", "")).strip()}


def critique(
    proposals: Sequence[dict],
    critic_chat: Callable[[list[dict]], ChatResult],
    local_chat: Optional[Callable[[list[dict]], ChatResult]] = None,
    budget: Optional[ApiBudget] = None,
    critic_model: str = "openrouter",
    price_per_mtok_in: float = 0.0,
    price_per_mtok_out: float = 0.0,
) -> dict:
    """Aprobă ≤1 ipoteză. Buget-gated: peste plafon → `local_chat` (fallback).

    Întoarce: {approved, approved_index, reasoning, model_used, fallback_used, cost_usd}.
    """
    if not proposals:
        return {"approved": None, "approved_index": None, "reasoning": "fără propuneri",
                "model_used": None, "fallback_used": False, "fallback_reason": None, "cost_usd": 0.0}

    # Fallback pe local în două cazuri: (1) buget lunar depășit; (2) apelul OpenRouter EȘUEAZĂ
    # (rate-limit, model free dispărut, rețea) — ca noaptea să nu se piardă. Local = Qwen, cost 0.
    use_fallback = bool(budget and budget.over_budget() and local_chat is not None)
    fallback_reason = "budget" if use_fallback else None
    chat = local_chat if use_fallback else critic_chat
    if chat is None:
        raise CriticError("niciun client de Critic disponibil (nici OpenRouter, nici fallback local)")

    messages = build_messages(proposals)
    try:
        result = chat(messages)
    except Exception as exc:  # noqa: BLE001 — orice eșec al Criticului principal → fallback local
        if use_fallback or local_chat is None:
            raise CriticError(f"Critic indisponibil și fără fallback local: {exc}") from exc
        use_fallback = True
        fallback_reason = "error"
        result = local_chat(messages)
    verdict = parse_verdict(result.content, len(proposals))

    cost = 0.0
    model_used = result.model or (critic_model if not use_fallback else "local")
    if not use_fallback and budget is not None:
        # Costul real dacă providerul îl întoarce; altfel estimare din tokeni.
        cost = result.cost_usd if result.cost_usd is not None else estimate_usd(
            int(result.usage.get("prompt_tokens", 0)),
            int(result.usage.get("completion_tokens", 0)),
            price_per_mtok_in, price_per_mtok_out,
        )
        budget.record(
            model=model_used, usd=cost, role="critic",
            prompt_tokens=int(result.usage.get("prompt_tokens", 0)),
            completion_tokens=int(result.usage.get("completion_tokens", 0)),
        )

    idx = verdict["approved_index"]
    return {
        "approved": proposals[idx] if idx is not None else None,
        "approved_index": idx,
        "reasoning": verdict["reasoning"],
        "model_used": model_used,
        "fallback_used": use_fallback,
        "fallback_reason": fallback_reason,   # None | "budget" | "error"
        "cost_usd": cost,
    }
