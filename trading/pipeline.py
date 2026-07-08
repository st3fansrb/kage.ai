"""Pipeline nocturn Actor→Critic→Validare (WP-T, Etapa 5.3). Înlocuiește `nocturnal.py` naiv.

Un ciclu complet, fără intervenție umană:

  1. **Actor** (Qwen 35B local) propune ≤3 ipoteze în format impus — NU cod de execuție.
  2. Toate propunerile se **pre-înregistrează** în ledger (invariant #2).
  3. **Critic** (OpenRouter, buget-gated; fallback local) aprobă ≤1.
  4. **Validare** statistică peste ledger la **costuri stresate** (invariant #4) → verdict onest.
  5. **Raport de dimineață**. PROMOVAREA la paper e MANUALĂ (nimic nu se promovează automat).

Garanții: nicio cale de cod nu plasează ordine reale (garda paper-only moștenită); LLM-ul nu
declară nimic „valid" (invariant #5 — validarea e matematică); nimic nu se șterge din ledger.
"""

from __future__ import annotations

import json
from typing import Callable, Optional, Sequence

from trading import actor as actor_mod
from trading import critic as critic_mod
from trading import report as report_mod
from trading.budget import ApiBudget
from trading.calibration import calibration_report
from trading.ledger import PAPER_ONLY, TradingLedger
from trading.llm import ChatResult


class NightlyPipeline:
    """Orchestrează un ciclu nocturn de cercetare. Fără efecte în afara ledger-ului."""

    def __init__(
        self,
        ledger: TradingLedger,
        actor_chat: Callable[[list[dict]], ChatResult],
        critic_chat: Callable[[list[dict]], ChatResult],
        local_critic_chat: Optional[Callable[[list[dict]], ChatResult]] = None,
        budget: Optional[ApiBudget] = None,
        actor_model: str = "qwen35b-local",
        critic_model: str = "openrouter",
        critic_price_in: float = 0.0,
        critic_price_out: float = 0.0,
    ):
        # Garda paper-only: pipeline-ul nu are voie să existe într-un build cu PAPER_ONLY compromis.
        assert PAPER_ONLY is True, "PAPER_ONLY compromis — pipeline-ul de research refuză să ruleze"
        self.ledger = ledger
        self.actor_chat = actor_chat
        self.critic_chat = critic_chat
        self.local_critic_chat = local_critic_chat
        self.budget = budget
        self.actor_model = actor_model
        self.critic_model = critic_model
        self.critic_price_in = critic_price_in
        self.critic_price_out = critic_price_out

    def run_once(
        self,
        regime: Optional[str] = None,
        recent_failures: Optional[Sequence[str]] = None,
        n_max: int = 3,
        min_trades: int = 20,
    ) -> dict:
        """Rulează un ciclu complet. Întoarce un dict cu rezultatele + raportul text."""
        # 1. Actor → propuneri conforme (strict=False: sare peste cele malformate, nu crapă bucla).
        proposals = actor_mod.propose(
            self.actor_chat, regime=regime, recent_failures=recent_failures,
            n_max=n_max, strict=False,
        )
        # 2. Pre-înregistrare (invariant #2).
        hyp_ids = actor_mod.register(
            self.ledger, proposals, regime=regime, source_model=self.actor_model
        )
        # 3. Critic → aprobă ≤1 (buget-gated).
        verdict = critic_mod.critique(
            proposals, self.critic_chat, local_chat=self.local_critic_chat,
            budget=self.budget, critic_model=self.critic_model,
            price_per_mtok_in=self.critic_price_in, price_per_mtok_out=self.critic_price_out,
        )
        approved_hyp_id = (
            hyp_ids[verdict["approved_index"]] if verdict["approved_index"] is not None else None
        )
        # 4. Validare statistică la costuri stresate (invariant #4).
        stressed_verdicts = report_mod.verdicts_for_ledger(
            self.ledger, min_trades=min_trades, stressed=True
        )
        # 5. Raport de dimineață.
        report_text = self.build_report(
            proposals, hyp_ids, verdict, approved_hyp_id, stressed_verdicts
        )
        return {
            "proposals": proposals,
            "hypothesis_ids": hyp_ids,
            "approved_index": verdict["approved_index"],
            "approved_hypothesis_id": approved_hyp_id,
            "critic": verdict,
            "stressed_verdicts": stressed_verdicts,
            "report": report_text,
            "promoted": False,   # NICIODATĂ automat — promovarea la paper e manuală (Stefan)
        }

    def build_report(
        self,
        proposals: Sequence[dict],
        hyp_ids: Sequence[int],
        verdict: dict,
        approved_hyp_id: Optional[int],
        stressed_verdicts: Sequence,
    ) -> str:
        lines = [
            "🌙 RAPORT NOCTURN — Actor → Critic → Validare",
            "=" * 60,
            f"Actor: {len(proposals)} ipoteze pre-înregistrate (id-uri: {list(hyp_ids)})",
        ]
        for i, (p, hid) in enumerate(zip(proposals, hyp_ids)):
            mark = "✅ APROBATĂ" if i == verdict["approved_index"] else "·"
            lines.append(
                f"  {mark} [h{hid}] {p.get('mecanism_cauzal', '')[:80]}\n"
                f"       predicție: {json.dumps(p.get('predictie_cu_interval', {}), ensure_ascii=False)}"
            )
        crit_src = "LOCAL (buget depășit)" if verdict["fallback_used"] else verdict["model_used"]
        lines.append(f"Critic ({crit_src}): {verdict['reasoning'] or '—'}")
        if approved_hyp_id is not None:
            lines.append(
                f"→ Ipoteza h{approved_hyp_id} așteaptă IMPLEMENTARE MANUALĂ în cod determinist "
                "(Actorul nu scrie cod). Backtest la costuri stresate DUPĂ implementare."
            )
        else:
            lines.append("→ Nicio ipoteză aprobată în această noapte.")
        if self.budget is not None:
            st = self.budget.status()
            lines.append(
                f"Buget Critic: {st['spend_eur']:.4f}€ / {st['cap_eur']:.2f}€ "
                f"(rămas {st['remaining_eur']:.4f}€)"
            )
        lines.append("-" * 60)
        lines.append("VALIDARE la costuri stresate (slippage dublat):")
        lines.append(report_mod.format_report(list(stressed_verdicts), len(stressed_verdicts)))
        try:
            lines.append("-" * 60)
            lines.append(calibration_report(self.ledger))
        except Exception:  # noqa: BLE001 — calibrarea nu trebuie să doboare raportul
            pass
        lines.append("=" * 60)
        lines.append("PROMOVARE LA PAPER = MANUALĂ (doar Stefan). Nimic nu s-a promovat automat.")
        return "\n".join(lines)
