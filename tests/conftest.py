"""Fixturi comune pentru testele Kage.

Notă: `import orchestrator` e sigur — FastAPI app e creat la import, dar init-ul
ChromaDB/SQLite/Ollama rulează doar în handlerele @app.on_event("startup"),
care NU se declanșează la import. Deci unit-testele nu pornesc serverul.
"""
import sys
from pathlib import Path

import pytest

# Permite `import orchestrator` rulând pytest din orice director
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import orchestrator  # noqa: E402


@pytest.fixture(autouse=True)
def reset_global_state():
    """Resetează starea globală mutabilă între teste."""
    orchestrator._ollama_failures = 0
    orchestrator._ollama_dead = False
    orchestrator._budget_alert_80_sent = ""
    orchestrator._usage_cache = {"date": "", "total": 0, "cloud": 0}
    # Plasă de siguranță WP-B: niciun test de backup nu trebuie să scrie în iCloud-ul
    # REAL. Testele care verifică copia off-machine monkeypatchează la un tmp_path.
    orchestrator.ICLOUD_BACKUP_DIR = None
    yield
