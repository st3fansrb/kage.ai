"""Buget API pentru Critic (WP-T, Etapa 2.3 + 5).

Criticul e singura componentă care arde bani reali (OpenRouter). Gardă hard: plafon lunar în
euro, contor de cost în SQLite (`api_costs`), iar peste plafon jobul NU cheamă API-ul → fallback
pe Qwen local (calitate mai slabă acceptată, bucla nu moare). Pură contabilitate — zero LLM.

Costul se poate afla din răspunsul API (unele întorc `usage.cost` în USD) sau se estimează din
tokeni × preț/milion (config `price_per_mtok_in/out`).
"""

from __future__ import annotations

import time
from typing import Optional

from trading.ledger import TradingLedger

DEFAULT_CAP_EUR = 7.0        # mijlocul intervalului aprobat 5–10€/lună
DEFAULT_EUR_USD = 0.92       # pentru afișare EUR (același curs ca #7 Budget)


def _month() -> str:
    return time.strftime("%Y-%m", time.localtime())


def estimate_usd(
    prompt_tokens: int, completion_tokens: int,
    price_per_mtok_in: float, price_per_mtok_out: float,
) -> float:
    """Cost estimat (USD) din tokeni × preț/milion (input/output separat)."""
    return (prompt_tokens / 1_000_000.0) * float(price_per_mtok_in) + \
           (completion_tokens / 1_000_000.0) * float(price_per_mtok_out)


class ApiBudget:
    """Gardă de buget lunar peste `api_costs` din ledger. Instanțiază per rulare."""

    def __init__(
        self, ledger: TradingLedger, cap_eur: float = DEFAULT_CAP_EUR,
        eur_usd: float = DEFAULT_EUR_USD,
    ):
        self.ledger = ledger
        self.cap_eur = float(cap_eur)
        self.eur_usd = float(eur_usd)

    def spend_usd(self, month: Optional[str] = None) -> float:
        return self.ledger.api_cost_sum(month or _month())

    def spend_eur(self, month: Optional[str] = None) -> float:
        return self.spend_usd(month) * self.eur_usd

    def remaining_eur(self) -> float:
        return self.cap_eur - self.spend_eur()

    def over_budget(self) -> bool:
        """True dacă cheltuiala lunii curente a atins/depășit plafonul → fallback local."""
        return self.spend_eur() >= self.cap_eur

    def record(
        self, model: str, usd: float, provider: str = "openrouter",
        role: str = "critic", prompt_tokens: int = 0, completion_tokens: int = 0,
    ) -> int:
        return self.ledger.record_api_cost(
            model=model, usd=usd, provider=provider, role=role,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        )

    def status(self) -> dict:
        spend = self.spend_eur()
        return {
            "month": _month(),
            "spend_eur": round(spend, 4),
            "cap_eur": self.cap_eur,
            "remaining_eur": round(self.cap_eur - spend, 4),
            "over_budget": spend >= self.cap_eur,
        }
