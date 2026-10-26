"""Un singur programator: batch-urile de platformă (agregare ETL, backup, vacuum, commit
vault) se înregistrează mereu în APScheduler — nu mai există un scheduler extern căruia
să le delege."""
import pytest

import orchestrator


class _FakeScheduler:
    def __init__(self, *a, **k):
        self.jobs = []

    def add_job(self, *a, **k):
        self.jobs.append(k.get("id"))

    def start(self):
        pass


@pytest.fixture
def sched_env(monkeypatch):
    """Scheduler fals care înregistrează id-urile. PG „configurat" fără conexiune reală:
    _scheduled_tasks_all e stubbed la [] ca să nu atingă DB-ul."""
    monkeypatch.setattr(orchestrator, "AsyncIOScheduler", _FakeScheduler)
    monkeypatch.setattr(orchestrator, "_scheduled_tasks_all", lambda: [])
    monkeypatch.setattr(orchestrator.pg_store, "configured", lambda: True)
    monkeypatch.setattr(orchestrator, "BRIEFING_ENABLED", False)
    monkeypatch.setattr(orchestrator, "HEARTBEAT_URL", "")
    yield


async def test_platform_batches_always_in_apscheduler(sched_env):
    await orchestrator.startup_scheduler()
    ids = set(orchestrator._scheduler.jobs)
    assert {"__etl_nightly__", "__backup_cache_db__",
            "__cache_vacuum__", "__vault_git_commit__"} <= ids


async def test_etl_needs_postgres(sched_env, monkeypatch):
    monkeypatch.setattr(orchestrator.pg_store, "configured", lambda: False)
    await orchestrator.startup_scheduler()
    ids = set(orchestrator._scheduler.jobs)
    assert "__etl_nightly__" not in ids
    assert "__backup_cache_db__" in ids


def test_airflow_flag_gone():
    assert not hasattr(orchestrator, "AIRFLOW_BATCHES")
