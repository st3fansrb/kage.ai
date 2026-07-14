"""Plafonul-gate GLOBAL pe bani reali (#7 Budget v2 — felia de enforcement, 12.07.2026).

Decizia lui Stefan (12.07.2026): top-up-ul OpenRouter (~10 €) trebuie să reziste luni de zile —
implicit NU se cheltuie NIMIC (`api_budget.enabled: false`), iar când se activează, plafonul
lunar + cel zilnic (anti-buclă) sunt garduri hard. Modelele gratuite (prețuri 0 în config,
ex. sufix `:free` pe OpenRouter) trec și cu switch-ul oprit — nu ard credite.

Semantică fail-closed: secțiune de config lipsă → dezactivat; eroare la citirea cheltuielilor →
refuz. Gate-ul e pură contabilitate (zero LLM) și NU atinge căile pe abonament (Claude) sau
free-tier (Gemini) — acelea rămân pe bugetul de apeluri/zi existent (`max_cloud_calls_per_day`).

Sursa de adevăr pentru cheltuieli: tabela `api_costs` (azi în `cache_db/trading.db`, scrisă de
Critic prin `trading/budget.py`; rolurile viitoare — advisor WP13, failover — înregistrează tot
acolo, cu `role` propriu). Sumele se injectează ca funcții → modulul rămâne pur și testabil.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

DEFAULT_ENABLED = False          # kill-switch: implicit NU se cheltuie bani reali
DEFAULT_MONTHLY_CAP_EUR = 10.0   # plafonul aprobat în KAGE-HANDOFF §Restul #7 (10–15 €/lună)
DEFAULT_DAILY_CAP_EUR = 1.0      # anti-buclă: o zi nu are voie să ardă mai mult de atât
DEFAULT_EUR_USD = 0.92           # același curs static ca restul #7 (doar afișare/conversie)


def _month() -> str:
    return time.strftime("%Y-%m", time.localtime())


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


@dataclass
class GateDecision:
    allowed: bool
    reason: str = ""   # "" | "disabled" | "monthly_cap" | "daily_cap" | "error"

    def __bool__(self) -> bool:
        return self.allowed


class SpendGate:
    """Gardă globală peste TOATE rolurile plătite. Instanțiază per verificare — e ieftin."""

    def __init__(
        self, *,
        enabled: bool = DEFAULT_ENABLED,
        monthly_cap_eur: float = DEFAULT_MONTHLY_CAP_EUR,
        daily_cap_eur: float = DEFAULT_DAILY_CAP_EUR,
        eur_usd: float = DEFAULT_EUR_USD,
        month_usd_fn: Optional[Callable[[], float]] = None,
        day_usd_fn: Optional[Callable[[], float]] = None,
    ):
        self.enabled = bool(enabled)
        self.monthly_cap_eur = float(monthly_cap_eur)
        self.daily_cap_eur = float(daily_cap_eur)
        self.eur_usd = float(eur_usd)
        self._month_usd_fn = month_usd_fn or (lambda: 0.0)
        self._day_usd_fn = day_usd_fn or (lambda: 0.0)

    @classmethod
    def from_config(cls, config: dict, ledger=None) -> "SpendGate":
        """Construiește gate-ul din secțiunea `api_budget` a config-ului Kage.

        `ledger` = orice obiect cu `api_cost_sum(month)` + `api_cost_sum_day(day)`
        (azi: `trading.ledger.TradingLedger`); None → cheltuieli 0 (doar switch + plafoane).
        Secțiune lipsă/coruptă → toate default-urile, deci DEZACTIVAT (fail-closed).
        """
        section = config.get("api_budget")
        if not isinstance(section, dict):
            section = {}
        month_fn = (lambda: float(ledger.api_cost_sum(_month()))) if ledger is not None else None
        day_fn = (lambda: float(ledger.api_cost_sum_day(_today()))) if ledger is not None else None
        return cls(
            enabled=bool(section.get("enabled", DEFAULT_ENABLED)),
            monthly_cap_eur=float(section.get("monthly_cap_eur", DEFAULT_MONTHLY_CAP_EUR)),
            daily_cap_eur=float(section.get("daily_cap_eur", DEFAULT_DAILY_CAP_EUR)),
            eur_usd=float(config.get("eur_usd_rate", DEFAULT_EUR_USD)),
            month_usd_fn=month_fn,
            day_usd_fn=day_fn,
        )

    # ── interogări (None la eroare de citire — consumatorii decid) ──────────
    def spend_month_eur(self) -> Optional[float]:
        try:
            return self._month_usd_fn() * self.eur_usd
        except Exception:
            return None

    def spend_today_eur(self) -> Optional[float]:
        try:
            return self._day_usd_fn() * self.eur_usd
        except Exception:
            return None

    # ── decizia ──────────────────────────────────────────────────────────────
    def allows(self, est_usd: float = 0.0, free: bool = False) -> GateDecision:
        """Poate rula un apel PLĂTIT? `free=True` (ambele prețuri 0 în config) → trece mereu.

        Fail-closed: switch oprit → refuz; cheltuieli necitibile → refuz.
        """
        if free:
            return GateDecision(True)
        if not self.enabled:
            return GateDecision(False, "disabled")
        est_eur = float(est_usd) * self.eur_usd
        month = self.spend_month_eur()
        today = self.spend_today_eur()
        if month is None or today is None:
            return GateDecision(False, "error")
        if month + est_eur >= self.monthly_cap_eur:
            return GateDecision(False, "monthly_cap")
        if today + est_eur >= self.daily_cap_eur:
            return GateDecision(False, "daily_cap")
        return GateDecision(True)

    def status(self) -> dict:
        """Stare pentru afișare (Mission Control / rapoarte). Valorile în EUR."""
        month = self.spend_month_eur()
        today = self.spend_today_eur()
        return {
            "enabled": self.enabled,
            "month": _month(),
            "spend_month_eur": (round(month, 4) if month is not None else None),
            "monthly_cap_eur": self.monthly_cap_eur,
            "spend_today_eur": (round(today, 4) if today is not None else None),
            "daily_cap_eur": self.daily_cap_eur,
            "blocked_reason": (None if (d := self.allows()).allowed else d.reason),
        }
