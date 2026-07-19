"""DAG-urile batch ale Kage (WP-AF).

Externalizează din APScheduler (care murea cu procesul orchestratorului — cazul din
09.07, scanul de 19:00 pierdut tăcut) cele patru batch-uri NON-safety-critical:
agregarea nightly ETL, backupul, scanul de joburi, calibrarea săptămânală de trading.
Fiecare DAG apelează endpoint-ul existent — Airflow dă program, retries, backfill, istoric.

NU sunt migrate cron-urile safety-critical / near-realtime: killswitch-ul de trading
(`*/5`) și heartbeat-ul WP12 rămân în APScheduler, în proces — fail-closed-ul lor nu are
ce căuta într-un scheduler extern.
"""
from __future__ import annotations

import pendulum
from airflow.decorators import dag, task

from kage_common import call_kage, notify_failure

_DEFAULT_ARGS = {
    "owner": "kage",
    "retries": 2,
    "retry_delay": pendulum.duration(minutes=5),
    "on_failure_callback": notify_failure,
}
_START = pendulum.datetime(2026, 7, 1, tz="Europe/Bucharest")
_COMMON = dict(
    start_date=_START,
    catchup=False,            # nu re-rula retroactiv la prima pornire
    default_args=_DEFAULT_ARGS,
    tags=["kage", "batch"],
)


@dag(schedule="30 1 * * *", **_COMMON)
def kage_etl_daily():
    """Agregarea nightly a telemetriei (raw → staging → mart) pentru ziua închisă (`ds`).
    Idempotent: o re-rulare pe aceeași zi dă același rezultat (delete-and-rewrite)."""
    @task
    def aggregate(ds=None):
        return call_kage("/admin/etl", {"action": "day", "day": ds})
    aggregate()


@dag(schedule="0 5 * * *", **_COMMON)
def kage_backup_daily():
    """Backup off-machine al cache_db + pg_dump (WP-B)."""
    @task
    def backup():
        return call_kage("/admin/backup")
    backup()


@dag(schedule="0 7,19 * * *", **_COMMON)
def kage_job_scan():
    """Scanul de joburi (WP-J), 2×/zi. Endpoint-ul e fire-and-forget: succesul task-ului =
    scan pornit; digestul ajunge pe Telegram când e gata."""
    @task
    def scan():
        return call_kage("/jobs/scan")
    scan()


@dag(schedule="0 4 * * 1", **_COMMON)
def kage_trading_calibration():
    """Calibrarea săptămânală de trading (luni 04:00, paper-only)."""
    @task
    def calibrate():
        return call_kage("/admin/trading/calibration")
    calibrate()


kage_etl_daily()
kage_backup_daily()
kage_job_scan()
kage_trading_calibration()
