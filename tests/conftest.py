"""Fixturi comune pentru testele Kage.

Notă: `import orchestrator` e sigur — FastAPI app e creat la import, dar init-ul
ChromaDB/SQLite/Ollama/Postgres rulează doar în handlerele @app.on_event("startup"),
care NU se declanșează la import. Deci unit-testele nu pornesc serverul.

WP-PG: testele care ating tabelele migrate (usage, runs, missions, job_runs,
scheduled_tasks, status) cer fixture-ul `pg` — un DB de test REAL, temporar per
sesiune (creat/șters de `_pg_session`), curățat cu TRUNCATE per test. Specul WP-PG
cere explicit instanță de test, nu Postgres-ul „de producție" (baza `kage`).
Fără server PG local, testele care cer `pg` sar cu skip explicit.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest

# Permite `import orchestrator` rulând pytest din orice director
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import orchestrator  # noqa: E402
import pg_store  # noqa: E402
from psycopg.conninfo import conninfo_to_dict, make_conninfo  # noqa: E402

_PG_ADMIN_DSN = os.environ.get("KAGE_TEST_PG_ADMIN_DSN", "dbname=postgres")
_TEST_DB_NAME = f"kage_test_{uuid.uuid4().hex[:8]}"
# DSN-ul de test moștenește host/user/parolă/port din DSN-ul admin, schimbând DOAR baza.
# Un `dbname=...` gol se conectează pe user-ul OS local prin socket — merge local (trust),
# dar pică în CI unde Postgres cere postgres/postgres@localhost (255 erori la primul CI).
_TEST_DSN = make_conninfo(**{**conninfo_to_dict(_PG_ADMIN_DSN), "dbname": _TEST_DB_NAME})


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
    # Plasă de siguranță WP-PG: implicit stratul PG e NEconfigurat — un test nu are
    # voie să scrie accidental în baza `kage` reală. Cine vrea PG cere fixture-ul `pg`.
    pg_store.configure(None)
    pg_store.close()
    yield


@pytest.fixture(scope="session")
def _pg_session():
    """Creează un DB de test temporar o dată per sesiune; îl șterge la final."""
    import psycopg
    try:
        admin = psycopg.connect(_PG_ADMIN_DSN, autocommit=True, connect_timeout=2)
    except Exception:
        pytest.skip("PostgreSQL local indisponibil — testele PG sar")
    admin.execute(f'CREATE DATABASE "{_TEST_DB_NAME}"')
    admin.close()
    pg_store.configure(_TEST_DSN)
    assert pg_store.connect(), "conexiunea la DB-ul de test a eșuat"
    pg_store.ensure_schema()
    yield _TEST_DSN
    pg_store.close()
    admin = psycopg.connect(_PG_ADMIN_DSN, autocommit=True, connect_timeout=2)
    admin.execute(f'DROP DATABASE IF EXISTS "{_TEST_DB_NAME}" WITH (FORCE)')
    admin.close()


@pytest.fixture()
def pg(_pg_session, reset_global_state):
    """Stratul pg_store pe DB-ul de test, cu tabelele goale. Depinde explicit de
    reset_global_state ca reconfigurarea de aici să ruleze DUPĂ plasa de siguranță."""
    pg_store.configure(_pg_session)
    if not pg_store.available():
        assert pg_store.connect()
    pg_store.execute("TRUNCATE " + ", ".join(pg_store.TABLES) + " RESTART IDENTITY")
    yield pg_store
