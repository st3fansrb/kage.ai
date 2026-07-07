"""WP-T — laboratorul de trading agents (crypto/prediction/forex).

Paper-only prin design: promovarea pe bani reali e DOAR manuală, niciodată decisă
de agent. Vezi `docs/KAGE-HANDOFF.md` §WP-T pentru arhitectură și criterii de acceptare.

Modulele de aici sunt self-contained (fără import din `orchestrator.py`) fiindcă
agenții de trading rulează ca daemoni separați, long-running, pe modele locale.
"""

from .ledger import TradingLedger, Experiment, PAPER_ONLY
from .safety import assert_paper_only, is_paper_only, PaperOnlyViolation

__all__ = [
    "TradingLedger", "Experiment", "PAPER_ONLY",
    "assert_paper_only", "is_paper_only", "PaperOnlyViolation",
]
