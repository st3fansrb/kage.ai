"""Helper comun pentru DAG-urile Kage (WP-AF).

DAG-urile NU reimplementează logica batch — apelează endpoint-urile existente ale
orchestratorului (POST /admin/etl, /admin/backup, /jobs/scan, /admin/trading/…).
Airflow orchestrează: program, retries, backfill, istoric. Logica rămâne în orchestrator.

Fără dependențe în plus (doar stdlib) — token-ul de API și cel de Telegram se citesc din
`kage_config.json` la runtime, deci NU ajung niciodată în metadata-baza Airflow.
"""
from __future__ import annotations

import json
import os
import urllib.request

_DEFAULT_CONFIG = "/Users/sirbustefanandrei/orchestrator-v2/kage_config.json"
KAGE_CONFIG_PATH = os.environ.get("KAGE_CONFIG_PATH", _DEFAULT_CONFIG)
KAGE_BASE_URL = os.environ.get("KAGE_BASE_URL", "http://127.0.0.1:4001")


def _config() -> dict:
    try:
        with open(KAGE_CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def call_kage(path: str, payload: dict | None = None, timeout: int = 1800) -> str:
    """POST autentificat la orchestrator. Ridică la status != 2xx (Airflow marchează
    task-ul failed → retry + alertă). `timeout` mare: unele batch-uri rulează sincron."""
    cfg = _config()
    token = cfg.get("api_token", "")
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        KAGE_BASE_URL + path, data=data, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def notify_failure(context: dict) -> None:
    """on_failure_callback: anunță pe Telegram că un DAG a picat. Postează DIRECT la
    Telegram (nu prin orchestrator) ca alerta să ajungă chiar dacă orchestratorul e căzut."""
    cfg = _config()
    token = cfg.get("telegram_bot_token", "")
    chat = cfg.get("telegram_chat_id", "")
    if not token or not chat or token == "CHANGEME":
        return
    dag = getattr(context.get("dag"), "dag_id", "?")
    ti = context.get("task_instance")
    task = getattr(ti, "task_id", "?")
    reason = str(context.get("exception") or "eșec task")[:300]
    text = f"⚠️ Airflow: DAG <b>{dag}</b> / task <code>{task}</code> a eșuat\n{reason}"
    try:
        body = json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode("utf-8")
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=body,
            headers={"Content-Type": "application/json"}), timeout=10)
    except Exception:
        pass  # best-effort: o alertă pierdută nu trebuie să arunce din callback
