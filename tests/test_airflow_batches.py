"""Teste WP-AF — delegarea batch-urilor către Airflow.

Criteriul de acceptare cheie: cele patru batch-uri migrate (agregare ETL, backup, scan
joburi, calibrare trading) NU se mai înregistrează în APScheduler când `airflow_batches`
e activ (altfel rulează de două ori); killswitch-ul + cron-urile ne-migrate rămân.
Airflow în sine rulează în `.airflow-venv` (nu în venv-ul de test), deci `kage_batch`
(care importă `airflow`) NU e importabil aici — testăm gating-ul din orchestrator +
helper-ul pur `kage_common`.
"""
import importlib.util
import pathlib

import pytest

import orchestrator

_MIGRATED = {"__etl_nightly__", "__backup_cache_db__", "__job_scan__", "__trading_calibration__"}


class _FakeScheduler:
    def __init__(self, *a, **k):
        self.jobs = []

    def add_job(self, *a, **k):
        self.jobs.append(k.get("id"))

    def start(self):
        pass


@pytest.fixture
def sched_env(monkeypatch):
    """Scheduler fals care înregistrează id-urile + globale setate ca toate ramurile de
    job să fie candidate (job scan, trading). PG „configurat" fără conexiune reală:
    _scheduled_tasks_all e stubbed la [] ca să nu atingă DB-ul."""
    monkeypatch.setattr(orchestrator, "AsyncIOScheduler", _FakeScheduler)
    monkeypatch.setattr(orchestrator, "_scheduled_tasks_all", lambda: [])
    monkeypatch.setattr(orchestrator.pg_store, "configured", lambda: True)
    monkeypatch.setattr(orchestrator, "JOBS_ENABLED", True)
    monkeypatch.setattr(orchestrator, "JOBS_PROFILES", {"stefan": {}})
    monkeypatch.setattr(orchestrator, "TRADING_ENABLED", True)
    monkeypatch.setattr(orchestrator, "BRIEFING_ENABLED", False)
    monkeypatch.setattr(orchestrator, "HEARTBEAT_URL", "")
    yield


async def test_batches_in_apscheduler_when_airflow_off(sched_env, monkeypatch):
    monkeypatch.setattr(orchestrator, "AIRFLOW_BATCHES", False)
    await orchestrator.startup_scheduler()
    ids = set(orchestrator._scheduler.jobs)
    assert _MIGRATED <= ids                       # toate 4 rulează în proces
    assert "__trading_killswitch__" in ids


async def test_batches_delegated_when_airflow_on(sched_env, monkeypatch):
    monkeypatch.setattr(orchestrator, "AIRFLOW_BATCHES", True)
    await orchestrator.startup_scheduler()
    ids = set(orchestrator._scheduler.jobs)
    assert not (_MIGRATED & ids)                  # niciunul din cele 4 migrate
    # Safety-critical + ne-migrate rămân în APScheduler:
    assert "__trading_killswitch__" in ids
    assert "__trading_context__" in ids
    assert "__cache_vacuum__" in ids
    assert "__vault_git_commit__" in ids


def test_recoverable_jobs_gated_by_flag(monkeypatch):
    monkeypatch.setattr(orchestrator, "AIRFLOW_BATCHES", True)
    assert orchestrator._recoverable_jobs() == {}   # Airflow deține recuperarea scanului
    monkeypatch.setattr(orchestrator, "AIRFLOW_BATCHES", False)
    assert "__job_scan__" in orchestrator._recoverable_jobs()


# ── helper-ul DAG (pur stdlib, importabil fără Airflow) ───────────────────────

def _load_kage_common():
    p = pathlib.Path(__file__).resolve().parent.parent / "airflow" / "dags" / "kage_common.py"
    spec = importlib.util.spec_from_file_location("kage_common", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_kage_common_reads_token(monkeypatch, tmp_path):
    cfg = tmp_path / "kage_config.json"
    cfg.write_text('{"api_token": "secret-xyz", "telegram_bot_token": "", "telegram_chat_id": ""}',
                   encoding="utf-8")
    m = _load_kage_common()
    monkeypatch.setattr(m, "KAGE_CONFIG_PATH", str(cfg))
    assert m._config()["api_token"] == "secret-xyz"


def test_notify_failure_safe_without_telegram(monkeypatch, tmp_path):
    """on_failure_callback nu are voie să arunce, chiar fără config Telegram."""
    m = _load_kage_common()
    monkeypatch.setattr(m, "KAGE_CONFIG_PATH", str(tmp_path / "missing.json"))
    m.notify_failure({"dag": None, "task_instance": None, "exception": "boom"})  # nu ridică
