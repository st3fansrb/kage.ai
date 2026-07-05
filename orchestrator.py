"""
AI Orchestration System v2 — Layer 3: Orchestrator
Exposes OpenAI-compatible API on port 4001.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import tarfile
import tempfile
import asyncio
import datetime
import logging
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Dict

import uuid
import time as _time
import subprocess
import httpx
import yaml
import chromadb
import sqlite3
from filelock import FileLock
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
import telegram_gateway as _tg_module

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
# httpx la INFO scrie URL-ul complet al fiecărui getUpdates → token-ul botului Telegram
# ajunge în log. Ridicăm pragul la WARNING. (WP1 / D-token)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent

def _load_kage_config() -> dict:
    for name in ("kage_config.json", "ntfy_config.json"):
        p = PROJECT_ROOT / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}

_cfg = _load_kage_config()

def _find_cli(name: str) -> str:
    configured = _cfg.get(f"{name}_cli", "")
    if configured:
        resolved = Path(configured).expanduser()
        if resolved.exists():
            return str(resolved)
    found = shutil.which(name)
    return found or name

LITELLM_URL  = _cfg.get("litellm_url",  "http://localhost:4000/v1")
LITELLM_KEY  = _cfg.get("litellm_key",  "sk-orchestrator-local")
OLLAMA_URL   = _cfg.get("ollama_url",   "http://localhost:11434")
VAULT        = Path(_cfg.get("vault_path", str(Path.home() / "Documents" / "KageVault"))).expanduser()
CLAUDE_CLI   = _find_cli("claude")
GEMINI_CLI   = _find_cli("gemini")

RISK_SETTINGS        = PROJECT_ROOT / "risk_settings.json"
STATUS_FILE          = PROJECT_ROOT / "status.json"
USAGE_LOG            = PROJECT_ROOT / "usage_log.jsonl"
# Config unificat (WP1b): kage_config.json e sursa; ntfy_config.json rămâne doar
# fallback legacy pentru instalări vechi.
KAGE_CONFIG_PATH     = next(
    (PROJECT_ROOT / n for n in ("kage_config.json", "ntfy_config.json") if (PROJECT_ROOT / n).exists()),
    PROJECT_ROOT / "kage_config.json",
)
SCHEDULED_TASKS_FILE = PROJECT_ROOT / "scheduled_tasks.json"
CACHE_DB_PATH        = PROJECT_ROOT / "cache_db"
EMBED_MODEL          = _cfg.get("embed_model",      "nomic-embed-text")
CACHE_SIMILARITY_THRESHOLD = _cfg.get("cache_threshold", 0.92)
CACHE_TTL_SECONDS    = _cfg.get("cache_ttl",        86400)
MAX_CONTEXT_MESSAGES = _cfg.get("max_context_messages", 20)
ENABLE_SUMMARIZATION = _cfg.get("enable_summarization", False)
MEMORY_TOP_K               = _cfg.get("memory_top_k", 5)
MEMORY_DEDUP_THRESHOLD     = _cfg.get("memory_dedup_threshold", 0.95)
MEMORY_RELEVANCE_THRESHOLD = _cfg.get("memory_relevance_threshold", 0.70)

# ── Workspace confinement (opțional, Faza 19) ─────────────────────────────────
ALLOWED_TASK_ROOTS = [
    Path(p).expanduser().resolve()
    for p in _cfg.get("allowed_task_roots", [])
    if isinstance(p, str) and p.strip()
]

def _validate_task_cwd(cwd: str) -> Optional[str]:
    """Validează cwd-ul unui task de agent (!run/!sysrun/!swarm) față de allowed_task_roots.

    Returnează calea canonică (str) dacă e permisă, altfel None.
    Dacă allowed_task_roots e gol → confinement dezactivat (returnează cwd canonic).
    PROJECT_ROOT e mereu permis implicit (necesar pentru !sysrun).
    Canonicalizarea cu resolve() previne bypass prin `..` sau symlink.
    """
    try:
        resolved = Path(cwd).expanduser().resolve()
    except Exception:
        return None
    if not ALLOWED_TASK_ROOTS:
        return str(resolved)
    for root in ALLOWED_TASK_ROOTS + [PROJECT_ROOT.resolve()]:
        if resolved == root or root in resolved.parents:
            return str(resolved)
    return None


def _default_task_cwd() -> str:
    """cwd implicit pentru !run fără cwd explicit (WP2): primul allowed_task_root,
    ca !run din chat/Telegram/UI să pornească fără [BLOCKED]. Dacă confinement-ul
    e dezactivat (listă goală), cade pe home."""
    if ALLOWED_TASK_ROOTS:
        return str(ALLOWED_TASK_ROOTS[0])
    return str(Path.home())

# ── Policy as code (WP-G1, §6.2) ──────────────────────────────────────────────
POLICY_FILE = PROJECT_ROOT / "policy.yaml"

# Fallback dacă policy.yaml lipsește sau e corupt — capability minimă la chat,
# completă la task/sysrun. Ține-le în sincron cu policy.yaml.
_POLICY_FALLBACK = {
    "run_types": {
        "chat":      {"tools": ["Read", "Glob", "Grep", "WebFetch", "WebSearch"],
                      "disallowed": ["Bash", "Write", "Edit"], "permission_mode": "auto"},
        "task":      {"tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch"],
                      "permission_mode": "auto"},
        "sysrun":    {"tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch"],
                      "permission_mode": "auto"},
        "scheduled": {"tools": ["Read", "Glob", "Grep", "WebFetch", "WebSearch"],
                      "disallowed": ["Bash", "Write", "Edit"], "permission_mode": "auto"},
    }
}

def _load_policy() -> dict:
    """Încarcă policy.yaml (necache-uit: fișier mic, permite editare la cald)."""
    try:
        if POLICY_FILE.exists():
            data = yaml.safe_load(POLICY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("run_types"), dict):
                return data
    except Exception as e:
        logger.warning(f"[Policy] policy.yaml invalid, folosesc fallback: {e}")
    return _POLICY_FALLBACK


def _policy_cli_flags(run_type: str) -> list[str]:
    """Traduce politica pentru `run_type` în flag-uri pentru claude CLI.

    Întoarce lista de argumente (--allowedTools / --disallowedTools / --permission-mode).
    Pentru `chat`/`scheduled` NU include Bash/Write/Edit → D7: un mesaj de chat normal
    nu poate spawna claude cu Bash. `tools: []` → niciun --allowedTools (zero unelte).
    """
    spec = _load_policy().get("run_types", {}).get(run_type) \
        or _POLICY_FALLBACK["run_types"].get(run_type, {})
    flags: list[str] = []
    tools = spec.get("tools") or []
    if tools:
        flags += ["--allowedTools", ",".join(tools)]
    disallowed = spec.get("disallowed") or []
    if disallowed:
        flags += ["--disallowedTools", ",".join(disallowed)]
    flags += ["--permission-mode", spec.get("permission_mode", "auto")]
    return flags


# ── Blast radius: vault sub git (WP-G1, §6.3) ─────────────────────────────────
def _vault_git_commit(vault_path: Optional[Path] = None) -> str:
    """`git init` (dacă lipsește) + commit al tuturor schimbărilor din vault.

    Face fiecare `!save` al unui agent reversibil cu `git revert`. Rulează zilnic
    (pipeline nocturn, 03:00) și e idempotent: dacă nu s-a schimbat nimic, no-op.
    Returnează un mesaj de stare. Sincron (subprocess.run) — ușor de testat.
    """
    vault = Path(vault_path) if vault_path is not None else VAULT
    if not vault.exists() or not vault.is_dir():
        return f"[vault-git] skip: {vault} nu există"

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(vault),
            capture_output=True, text=True, timeout=60,
        )

    try:
        if not (vault / ".git").exists():
            _git("init")
            # Identitate locală, ca commit-ul să nu eșueze pe o mașină fără git config global.
            _git("config", "user.name", "Kage")
            _git("config", "user.email", "kage@localhost")
            logger.info(f"[vault-git] init pe {vault}")

        _git("add", "-A")
        status = _git("status", "--porcelain")
        if not status.stdout.strip():
            return "[vault-git] nimic de comis"

        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        res = _git("commit", "-m", f"kage auto-commit {ts}")
        if res.returncode != 0:
            logger.warning(f"[vault-git] commit eșuat: {res.stderr.strip()[:200]}")
            return f"[vault-git] commit eșuat: {res.stderr.strip()[:120]}"
        logger.info(f"[vault-git] commit ok pe {vault}")
        return f"[vault-git] commit ok ({ts})"
    except Exception as e:
        logger.error(f"[vault-git] eroare: {e}")
        return f"[vault-git] eroare: {e}"


async def _vault_git_commit_job() -> None:
    """Wrapper async pentru scheduler — rulează commit-ul fără a bloca event loop-ul."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _vault_git_commit)


# ── Backup cache_db (Faza 19) ─────────────────────────────────────────────────
BACKUP_DIR  = Path(_cfg.get("backup_dir", str(VAULT / "backups" / "kage"))).expanduser()
BACKUP_KEEP = int(_cfg.get("backup_keep", 7))

def _build_tier_models(cfg: dict) -> dict:
    m = cfg.get("models", {})
    def _t(key: str, prov_def: str, model_def: str):
        t = m.get(key, {})
        return (t.get("provider", prov_def), t.get("model", model_def) or model_def)
    return {
        1: m.get("tier1", {}).get("litellm_name", "tier-1-orchestrator"),
        2: m.get("tier2", {}).get("litellm_name", "tier-2-worker"),
        3: _t("tier3", "claude",  "claude-haiku-4-5"),
        4: _t("tier4", "gemini",  ""),
        5: _t("tier5", "claude",  "claude-sonnet-4-6"),
        6: _t("tier6", "claude",  "claude-opus-4-8"),
    }

def _build_tier_short(cfg: dict) -> dict:
    m = cfg.get("models", {})
    _defaults = {1: "qwen8b", 2: "qwen35b", 3: "haiku", 4: "gemini", 5: "sonnet", 6: "opus"}
    return {i: m.get(f"tier{i}", {}).get("short", _defaults[i]) for i in range(1, 7)}

TIER_MODELS = _build_tier_models(_cfg)
TIER_SHORT  = _build_tier_short(_cfg)

_TIER_CONFIDENCE = {1: 0.9, 2: 0.75, 3: 0.8, 4: 0.8, 5: 0.85, 6: 0.9}

# Router feedback loop: cap learned examples per tier so tier_routing can't grow unbounded.
MAX_FEEDBACK_PER_TIER = int(_cfg.get("max_routing_feedback_per_tier", 50))

PERSONAL_KEYWORDS = _cfg.get("personal_keywords", [])

UNCERTAINTY_PHRASES = [
    "nu știu", "nu sunt sigur", "nu am informații", "nu pot",
    "i don't know", "i'm not sure", "i cannot", "uncertain",
    "nu am acces", "limita mea", "depășește",
]

# ── Circuit breaker state ─────────────────────────────────────────────────────
_ollama_failures: int = 0
_ollama_dead: bool = False
_budget_alert_80_sent: str = ""  # ISO date string — resets automatically at day change
_usage_cache: dict = {"date": "", "total": 0, "cloud": 0}  # in-memory daily counter

# ── Risk in-memory state (Faza 7) ─────────────────────────────────────────────
pending_risk: Dict[str, asyncio.Event] = {}
risk_decisions: Dict[str, str] = {}

# ── Scheduler state (Faza 10) ─────────────────────────────────────────────────
_scheduler: Optional[AsyncIOScheduler] = None

# ── Cache state (Faza 11) ─────────────────────────────────────────────────────
_chroma_client: Optional[chromadb.PersistentClient] = None
_cache_collection = None
_routing_collection = None
_memory_collection = None
_cache_hits: int = 0
_cache_misses: int = 0
_db_conn: Optional[sqlite3.Connection] = None

# ── Pending risk approvals (kage UI) ─────────────────────────────────────────
pending_risk_meta: dict[str, dict] = {}
_active_task_queues: dict[str, asyncio.Queue] = {}

# ── Kill switch (WP-G1, §6.3) ─────────────────────────────────────────────────
# Registru al proceselor-agent vii (claude/gemini spawn-ate). !stop le omoară pe
# toate + pune scheduler-ul pe pauză. Procesele se auto-dezînregistrează la final.
_running_procs: set = set()

def _register_proc(proc) -> None:
    _running_procs.add(proc)

def _unregister_proc(proc) -> None:
    _running_procs.discard(proc)

def _stop_all() -> dict:
    """Kill switch: SIGTERM pe toate procesele-agent vii + scheduler.pause().
    Returnează un rezumat {procs_killed, scheduler_paused}."""
    killed = 0
    for proc in list(_running_procs):
        try:
            proc.terminate()
            killed += 1
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.warning(f"[!stop] nu am putut opri procesul: {e}")
        finally:
            _running_procs.discard(proc)
    scheduler_paused = False
    if _scheduler is not None:
        try:
            _scheduler.pause()
            scheduler_paused = True
        except Exception as e:
            logger.warning(f"[!stop] scheduler.pause() eșuat: {e}")
    logger.info(f"[!stop] {killed} procese oprite, scheduler_paused={scheduler_paused}")
    return {"procs_killed": killed, "scheduler_paused": scheduler_paused}

# ── Telegram gateway (Faza 17) ────────────────────────────────────────────────
_tg_gateway: Optional[_tg_module.TelegramGateway] = None


async def _background_task_exec(task_id: str, task_text: str, agent: str, cwd: str, is_sysrun: bool, parent_id: Optional[str] = None):
    """Executes agent in background, puts chunks in queue, saves to DB at end."""
    badge = f"**[{'SYS·' if is_sysrun else ''}TASK·{agent}]** "
    full_output = [badge]
    start_ts = datetime.datetime.now()

    target_id = parent_id or task_id
    queue = _active_task_queues.get(target_id)
    if queue:
        await queue.put(badge)

    proc = None
    try:
        env = {**os.environ, "ORCHESTRATOR_USER_MSG": task_text}
        if agent == "gemini":
            cmd = [GEMINI_CLI, "-p", task_text]
        else:
            # Policy as code (WP-G1): !sysrun = auto-modificare, !run = task pe workspace.
            cmd = [
                CLAUDE_CLI, "-p", task_text,
                "--output-format", "text",
                *_policy_cli_flags("sysrun" if is_sysrun else "task"),
                "--settings", str(RISK_SETTINGS),
            ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env=env,
        )
        assert proc.stdout is not None
        _register_proc(proc)

        while True:
            chunk = await proc.stdout.read(512)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            full_output.append(text)
            q = _active_task_queues.get(target_id)
            if q:
                await q.put(text)

        await proc.wait()
        duration_ms = int((datetime.datetime.now() - start_ts).total_seconds() * 1000)
        _log_usage(5, agent, task_text, duration_ms, agent=agent)

        # Persistence: save to DB so it survives page reloads
        if _db_conn:
            final_text = "".join(full_output)
            try:
                # We save once for the whole task
                _db_conn.execute("INSERT INTO messages (role, content) VALUES (?, ?)",
                                 ("user", f"!run {agent} {task_text[:120]}"))
                _db_conn.execute("INSERT INTO messages (role, content) VALUES (?, ?)",
                                 ("assistant", final_text))
                _db_conn.commit()
            except Exception as db_err:
                logger.error(f"Failed to save background task to DB: {db_err}")

    except Exception as e:
        err = f"\n\n*[task error: {e}]*"
        q = _active_task_queues.get(target_id)
        if q:
            await q.put(err)
    finally:
        _unregister_proc(proc)
        q = _active_task_queues.get(target_id)
        if q:
            # For swarm, we don't want to send [DONE] prematurely
            if not parent_id:
                await q.put("[DONE]")


async def _swarm_task_exec(task_id: str, task_text: str, cwd: str, is_sysrun: bool):
    """Executes Claude and Gemini in parallel."""
    badge = f"**[{'SYS·' if is_sysrun else ''}SWARM]** Activare agenți paraleli (Claude + Gemini)...\n"
    queue = _active_task_queues.get(task_id)
    if queue:
        await queue.put(badge)

    # Launch both agents concurrently, piping to the same main queue
    await asyncio.gather(
        _background_task_exec(uuid.uuid4().hex[:8], task_text, "claude", cwd, is_sysrun, parent_id=task_id),
        _background_task_exec(uuid.uuid4().hex[:8], task_text, "gemini", cwd, is_sysrun, parent_id=task_id),
    )

    if queue:
        await queue.put("\n\n**[SWARM·COMPLET]** Ambii agenți au terminat.")
        await queue.put("[DONE]")


# ── Auth helpers (Faza 16) ────────────────────────────────────────────────────
_AUTH_EXEMPT = {"/health", "/chat", "/dashboard", "/v1/models", "/manifest.json"}

def _get_api_token() -> str:
    try:
        return json.loads(KAGE_CONFIG_PATH.read_text(encoding="utf-8")).get("api_token", "")
    except Exception:
        return ""

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI()


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path in _AUTH_EXEMPT:
        return await call_next(request)
    token = _get_api_token()
    if not token:
        return await call_next(request)
    auth_header = request.headers.get("Authorization", "")
    query_token = request.query_params.get("token", "")
    cookie_token = request.cookies.get("kage_token", "")
    if auth_header == f"Bearer {token}" or query_token == token or cookie_token == token:
        return await call_next(request)
    return JSONResponse({"error": "Unauthorized"}, status_code=401)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Prinde orice excepție nehandled: loghează + notifică pe canalul de alerte,
    în loc să lase clientul cu un 500 mut (exact golul prin care a trecut D1)."""
    logger.exception(f"Eroare internă la {request.method} {request.url.path}: {exc}")
    try:
        _notify("💥 Eroare internă", f"{request.url.path}: {exc}", priority="high")
    except Exception:
        pass
    return JSONResponse({"error": "internal server error", "detail": str(exc)}, status_code=500)


# ── Scheduler helpers (Faza 10) ───────────────────────────────────────────────

def _parse_cron(cron_str: str) -> dict:
    """Parse '0 8 * * *' → APScheduler cron kwargs."""
    parts = cron_str.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expresie cron invalidă: {cron_str!r} (așteptat 5 câmpuri)")
    return dict(zip(["minute", "hour", "day", "month", "day_of_week"], parts))


async def _run_scheduled_task(task: dict) -> None:
    msg = task.get("message", "")
    tier_override = task.get("tier_override")

    if tier_override:
        tier = int(tier_override)
        confidence = 1.0
    else:
        tier, _, confidence, _ = await decide_tier(msg)

    obs_context = _get_obsidian_context(msg)
    system_prompt = _build_system_prompt(tier, obs_context)
    result_text = ""
    model_name = ""
    start_ts = datetime.datetime.now()

    try:
        if tier <= 2:
            model = TIER_MODELS[tier]
            model_name = str(model)
            msgs = _inject_system_prompt([{"role": "user", "content": msg}], system_prompt)
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{LITELLM_URL}/chat/completions",
                    json={"model": model, "messages": msgs, "stream": False},
                    headers={"Authorization": f"Bearer {LITELLM_KEY}"},
                    timeout=120,
                )
            result_text = r.json()["choices"][0]["message"]["content"]
        else:
            provider, model = TIER_MODELS[tier]
            model_name = model or "gemini-pro"
            full_prompt = f"{system_prompt}\n\nTask: {msg}"
            if provider == "gemini":
                cmd = [GEMINI_CLI, "-p", full_prompt, "--output-format", "json"]
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
                raw = stdout.decode("utf-8", errors="replace").strip()
                try:
                    raw = json.loads(raw).get("response", raw)
                except Exception:
                    pass
                result_text = raw or "[Gemini: răspuns gol]"
            else:
                env = {**os.environ, "ORCHESTRATOR_USER_MSG": msg}
                cmd = [CLAUDE_CLI, "-p", full_prompt, "--model", model, "--output-format", "text"]
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
                result_text = stdout.decode("utf-8", errors="replace").strip()
    except Exception as e:
        result_text = f"[Scheduled task error: {e}]"
        logger.error(f"Scheduled task '{msg[:40]}' failed: {e}")

    duration_ms = int((datetime.datetime.now() - start_ts).total_seconds() * 1000)
    _log_usage(tier, model_name, msg[:40], duration_ms)
    _log_to_vault(tier, f"[SCHEDULED] {msg}", obs_context is not None, confidence, False)

    preview = result_text[:200] if result_text else "(niciun răspuns)"
    _notify(f"⏰ T{tier}: {msg[:40]}", preview, priority="default")
    logger.info(f"Scheduled task done: tier={tier} msg={msg[:40]!r}")


async def _handle_schedule_command(message: str) -> StreamingResponse:
    """Handle !schedule \"CRON\" mesaj from chat."""
    m = re.match(r'!schedule\s+"([^"]+)"\s+(.+)', message, re.IGNORECASE | re.DOTALL)

    async def respond(text: str):
        yield f'data: {json.dumps({"choices": [{"delta": {"content": text}, "index": 0}]})}\n\n'
        yield "data: [DONE]\n\n"

    if not m:
        hint = (
            "Format: `!schedule \"CRON\" mesajul taskului`\n"
            "Exemplu: `!schedule \"0 8 * * *\" Bună ziua, ce am pe agenda azi?`"
        )
        return StreamingResponse(respond(hint), media_type="text/event-stream")

    cron_str = m.group(1)
    task_message = m.group(2).strip()

    try:
        _parse_cron(cron_str)
    except ValueError as e:
        return StreamingResponse(respond(f"Cron invalid: {e}"), media_type="text/event-stream")

    new_task = {
        "id": uuid.uuid4().hex[:12],
        "cron": cron_str,
        "message": task_message,
        "tier_override": None,
        "enabled": True,
    }

    tasks: list = []
    lock = FileLock(str(SCHEDULED_TASKS_FILE) + ".lock")
    with lock.acquire(timeout=2):
        if SCHEDULED_TASKS_FILE.exists():
            try:
                tasks = json.loads(SCHEDULED_TASKS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        tasks.append(new_task)
        SCHEDULED_TASKS_FILE.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")

    if _scheduler:
        cron_kwargs = _parse_cron(cron_str)
        _scheduler.add_job(
            _run_scheduled_task, "cron",
            id=new_task["id"], kwargs={"task": new_task}, **cron_kwargs
        )

    confirm = f"✅ Task programat (ID: {new_task['id']})\nCron: `{cron_str}`\nMesaj: {task_message[:80]}"
    return StreamingResponse(respond(confirm), media_type="text/event-stream")


@app.on_event("startup")
async def startup_scheduler():
    global _scheduler
    try:
        _scheduler = AsyncIOScheduler()
        loaded = 0
        lock = FileLock(str(SCHEDULED_TASKS_FILE) + ".lock")
        with lock.acquire(timeout=2):
            if SCHEDULED_TASKS_FILE.exists():
                tasks = json.loads(SCHEDULED_TASKS_FILE.read_text(encoding="utf-8"))
                for task in tasks:
                    if not task.get("enabled", True):
                        continue
                    try:
                        cron_kwargs = _parse_cron(task.get("cron", ""))
                        _scheduler.add_job(
                            _run_scheduled_task, "cron",
                            id=task.get("id", uuid.uuid4().hex[:12]),
                            kwargs={"task": task},
                            **cron_kwargs,
                        )
                        loaded += 1
                    except Exception as e:
                        logger.warning(f"Task {task.get('id')} skip: {e}")
        _scheduler.add_job(_vault_git_commit_job, "cron", hour=3, minute=0, id="__vault_git_commit__")
        _scheduler.add_job(_cache_vacuum, "cron", hour=4, minute=0, id="__cache_vacuum__")
        _scheduler.add_job(_backup_cache_db, "cron", hour=5, minute=0, id="__backup_cache_db__")
        _scheduler.start()
        logger.info(f"APScheduler started — {loaded} tasks loaded")
    except Exception as e:
        logger.error(f"Scheduler startup failed: {e}")


@app.on_event("startup")
async def startup_telegram():
    global _tg_gateway
    try:
        cfg = _load_kage_config()
        gw = _tg_module.init_gateway(cfg)
        if gw:
            _tg_gateway = gw
            await _tg_gateway.start()
        else:
            logger.info("[TelegramGateway] dezactivat (telegram_bot_token/chat_id neconfigurate)")
    except Exception as e:
        logger.error(f"Telegram gateway startup failed: {e}")


@app.on_event("shutdown")
async def shutdown_telegram():
    if _tg_gateway:
        await _tg_gateway.stop()


TIER_EXAMPLES: dict[int, list[str]] = {
    1: [
        "cât face 2+2", "ce înseamnă recursivitatea", "traduce hello în română",
        "ce zi e azi", "definește polinomul", "ce este o variabilă",
        "cât fac 10*5", "care e capitala Franței",
    ],
    2: [
        "explică-mi proiectul orchestrator", "ce am lucrat la aumovio",
        "cum merg proiectele mele", "rezumă activitatea din ultimele zile",
        "status internship", "ce face litellm", "ajutor cu flutter",
        "ce am de făcut la facultate",
    ],
    3: [
        "scrie un email formal", "analizează acest cod Python",
        "corectează textul următor", "explică-mi async/await",
        "ajutor cu debugging", "scrie o funcție care",
        "cum funcționează REST API", "optimizează codul acesta",
    ],
    4: [
        "rezumă acest document lung", "analizează această imagine",
        "extrage informațiile din PDF-ul atașat", "tradu și rezumă articolul acesta",
        "compară aceste două texte lungi", "descrie ce se vede în poză",
        "rezumă conținutul acestui fișier mare",
    ],
    5: [
        "construiește o arhitectură pentru", "planifică implementarea",
        "implementează feature-ul complet", "scrie un sistem complex",
        "analizează în profunzime", "creează un plan detaliat",
        "proiectează baza de date", "refactorizează întregul modul",
    ],
    6: [
        "demonstrează teorema", "rezolvă această problemă grea de algoritmică",
        "proiectează un sistem distribuit complex de la zero",
        "raționament matematic avansat pas cu pas",
        "analiză juridică aprofundată a contractului",
        "optimizează algoritmul la complexitatea minimă posibilă",
    ],
}


@app.on_event("startup")
async def startup_cache():
    global _chroma_client, _cache_collection, _routing_collection, _memory_collection, _db_conn
    try:
        CACHE_DB_PATH.mkdir(parents=True, exist_ok=True)
        # SQLite History
        db_path = CACHE_DB_PATH / "chat_history.db"
        _db_conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _db_conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT 'default',
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Non-destructive migration: add session_id to existing tables
        existing_cols = [r[1] for r in _db_conn.execute("PRAGMA table_info(messages)").fetchall()]
        if "session_id" not in existing_cols:
            _db_conn.execute("ALTER TABLE messages ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'")
        _db_conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON messages(session_id)")
        _db_conn.commit()

        _chroma_client = chromadb.PersistentClient(path=str(CACHE_DB_PATH))
        _cache_collection = _chroma_client.get_or_create_collection(
            "semantic_cache", metadata={"hnsw:space": "cosine"}
        )
        _routing_collection = _chroma_client.get_or_create_collection(
            "tier_routing", metadata={"hnsw:space": "cosine"}
        )
        _memory_collection = _chroma_client.get_or_create_collection(
            "long_term_memory", metadata={"hnsw:space": "cosine"}
        )
        count = _cache_collection.count()
        logger.info(f"ChromaDB ready — cache={count} routing={_routing_collection.count()} memory={_memory_collection.count()}")
        if count > 0:
            asyncio.create_task(_cache_vacuum())

        # Seed TIER_EXAMPLES for any tier missing seed entries (idempotent — picks up
        # newly added tiers like T4/T6 on an already-populated collection).
        await _seed_routing_examples()
    except Exception as e:
        logger.error(f"ChromaDB startup failed: {e}")


async def _seed_routing_examples() -> None:
    """Seed TIER_EXAMPLES for tiers that have no seed entries yet. Idempotent."""
    if _routing_collection is None:
        return
    try:
        existing = _routing_collection.get(include=["metadatas"])
        present = {
            int(meta.get("tier", -1))
            for meta in existing["metadatas"]
            if meta.get("source") != "feedback"
        }
    except Exception:
        present = set()
    seeded = 0
    for tier_num, examples in TIER_EXAMPLES.items():
        if tier_num in present:
            continue
        for ex in examples:
            emb = await _get_embedding(ex)
            if emb is None:
                continue
            try:
                _routing_collection.add(
                    documents=[ex],
                    embeddings=[emb],
                    metadatas=[{"tier": tier_num, "source": "seed"}],
                    ids=[uuid.uuid4().hex],
                )
                seeded += 1
            except Exception as e:
                logger.debug(f"Seed failed for '{ex}': {e}")
    if seeded:
        logger.info(f"Routing collection seeded {seeded} new examples")
    else:
        logger.info(f"Routing collection ready ({_routing_collection.count()} examples)")


# ── Risk endpoints ────────────────────────────────────────────────────────────

@app.post("/risk/register/{request_id}")
async def risk_register(request_id: str, request: Request):
    """Called by risk_hook.py to surface pending approvals in kage UI."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    tool_name = body.get("tool_name", "")
    cmd = body.get("cmd", "")
    reason = body.get("reason", "")
    pending_risk_meta[request_id] = {
        "id": request_id,
        "tool_name": tool_name,
        "cmd": cmd,
        "reason": reason,
        "time": body.get("time", "now"),
    }
    if _tg_gateway:
        asyncio.create_task(
            _tg_gateway.send_risk_approval(request_id, tool_name, cmd, reason)
        )
    return {"ok": True}


@app.post("/risk/respond/{request_id}")
async def risk_respond(request_id: str, request: Request):
    """Rezolvă o aprobare de risc — calea primară (WP1b) sunt butoanele inline
    Telegram (Confirmă/Blochează); `risk_hook.py` polling-uiește
    `/risk/status/{id}` până se setează decizia aici."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = body.get("action", "block")
    risk_decisions[request_id] = action
    pending_risk_meta.pop(request_id, None)
    if request_id in pending_risk:
        pending_risk[request_id].set()
    return {"ok": True, "action": action}


@app.get("/risk/status/{request_id}")
async def risk_status(request_id: str):
    """Polled by risk_hook.py instead of reading /tmp files."""
    if request_id in risk_decisions:
        return {"resolved": True, "action": risk_decisions[request_id]}
    return {"resolved": False, "action": None}


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [{"id": "auto", "object": "model", "owned_by": "orchestrator"}],
    }


@app.get("/health")
async def health():
    today = datetime.date.today().isoformat()
    requests_today = cloud_today = 0
    last_request = None
    try:
        lock = FileLock(str(USAGE_LOG) + ".lock")
        with lock.acquire(timeout=2):
            if USAGE_LOG.exists():
                for line in USAGE_LOG.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("ts", "").startswith(today):
                            requests_today += 1
                            if entry.get("cloud"):
                                cloud_today += 1
                            last_request = entry.get("ts")
                    except Exception:
                        pass
    except Exception:
        pass
    return {
        "ollama": "up" if _port_up(11434) else "down",
        "litellm": "up" if _port_up(4000) else "down",
        "ollama_failures": _ollama_failures,
        "requests_today": requests_today,
        "cloud_today": cloud_today,
        "last_request": last_request,
        "cache_hits": _cache_hits,
        "cache_misses": _cache_misses,
    }


@app.get("/api/stats")
async def api_stats():
    """JSON stats for live dashboard polling."""
    return _aggregate_usage()


@app.get("/api/config")
async def api_config():
    """Config non-sensibil pentru UI (WP2): rooturile permise pentru task-uri +
    cwd-ul implicit, ca task runner-ul din kage.html să ofere un dropdown de cwd."""
    return {
        "allowed_task_roots": [str(r) for r in ALLOWED_TASK_ROOTS],
        "default_task_cwd": _default_task_cwd(),
        "confinement_enabled": bool(ALLOWED_TASK_ROOTS),
    }


@app.get("/api/pending")
async def api_pending():
    """Return pending risk-approval items for the kage UI."""
    resolved = set(risk_decisions.keys())
    return [v for k, v in pending_risk_meta.items() if k not in resolved]


@app.get("/api/history")
async def api_history(session_id: str = "default"):
    """Return last 50 messages for a session from SQLite history."""
    if _db_conn is None:
        return []
    try:
        cursor = _db_conn.execute(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 50",
            (session_id,)
        )
        rows = cursor.fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    except Exception as e:
        logger.error(f"History fetch failed: {e}")
        return []


@app.get("/api/sessions")
async def api_sessions():
    """Return list of recent chat sessions."""
    if _db_conn is None:
        return []
    try:
        cursor = _db_conn.execute("""
            SELECT session_id, MIN(timestamp) as started, COUNT(*) as msg_count
            FROM messages GROUP BY session_id ORDER BY started DESC LIMIT 20
        """)
        return [{"id": r[0], "started": r[1], "messages": r[2]} for r in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Sessions fetch failed: {e}")
        return []


@app.post("/schedule")
async def schedule_endpoint(request: Request):
    body = await request.json()
    action = body.get("action", "list")

    lock = FileLock(str(SCHEDULED_TASKS_FILE) + ".lock")
    with lock.acquire(timeout=2):
        tasks: list = []
        if SCHEDULED_TASKS_FILE.exists():
            try:
                tasks = json.loads(SCHEDULED_TASKS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

        if action == "list":
            return {"tasks": tasks}

        if action == "add":
            new_task = {
                "id": uuid.uuid4().hex[:12],
                "cron": body.get("cron", "0 8 * * *"),
                "message": body.get("message", ""),
                "tier_override": body.get("tier_override"),
                "enabled": True,
            }
            try:
                cron_kwargs = _parse_cron(new_task["cron"])
            except ValueError as e:
                return {"error": str(e)}
            tasks.append(new_task)
            SCHEDULED_TASKS_FILE.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")
            if _scheduler:
                _scheduler.add_job(_run_scheduled_task, "cron", id=new_task["id"], kwargs={"task": new_task}, **cron_kwargs)
            return {"ok": True, "task": new_task}

        if action == "remove":
            task_id = body.get("id")
            tasks = [t for t in tasks if t.get("id") != task_id]
            SCHEDULED_TASKS_FILE.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")
            if _scheduler:
                try:
                    _scheduler.remove_job(task_id)
                except Exception:
                    pass
            return {"ok": True}

    return {"error": "Unknown action. Use: list | add | remove"}


@app.get("/dashboard")
async def dashboard():
    data = _aggregate_usage()
    return HTMLResponse(_build_dashboard_html(data))


@app.get("/chat")
async def chat_ui():
    # WP-G1 (D15): token-ul NU se mai injectează în HTML/JS (era vizibil în sursa
    # paginii). Îl livrăm ca cookie HttpOnly — invizibil pentru JS și pentru
    # view-source; fetch-urile same-origin din pagină îl trimit automat.
    kage_path = Path(__file__).parent / "kage.html"
    html = kage_path.read_text(encoding="utf-8")
    resp = HTMLResponse(html)
    token = _get_api_token()
    if token:
        resp.set_cookie(
            "kage_token", token,
            httponly=True, samesite="strict", path="/", max_age=60 * 60 * 24 * 30,
        )
    return resp


@app.get("/manifest.json")
async def pwa_manifest():
    return JSONResponse({
        "name": "kage — AI command center",
        "short_name": "kage",
        "start_url": "/chat",
        "display": "standalone",
        "background_color": "#080a0e",
        "theme_color": "#080a0e",
        "icons": [
            {"src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='20' fill='%23E8470A'/%3E%3Crect x='30' y='30' width='40' height='40' rx='6' fill='%23080a0e' opacity='.55'/%3E%3C/svg%3E",
             "sizes": "any", "type": "image/svg+xml"}
        ],
    })


def _prepare_and_launch_task(task_text: str, cwd: str, register_queue: bool = True) -> tuple[Optional[str], Optional[str]]:
    """Pregătește și pornește în fundal un task de agent (!run/!swarm/!sysrun).

    Aplică transformarea !sysrun, verifică confinement-ul (allowed_task_roots),
    alege swarm vs single agent (+ backend gemini/claude) și spawn-ează execuția.
    Returnează (task_id, None) la succes sau (None, mesaj_eroare) dacă task-ul e gol
    sau cwd-ul e blocat. Cu register_queue=True înregistrează o coadă în
    _active_task_queues pentru streaming (folosit de /task/run); cu False task-ul
    rulează fără consumator de stream — output-ul e salvat în DB oricum.
    """
    task_text = task_text.strip()
    if not task_text:
        return None, "task is required"

    # 1. System context routing (!sysrun)
    is_sysrun = task_text.startswith("!sysrun")
    if is_sysrun:
        task_text = task_text[len("!sysrun"):].strip()
        cwd = str(Path(__file__).parent)
        task_text = (
            "Ești în directorul sursă al Orchestratorului. "
            "Scopul tău este să analizezi și să modifici codul pentru a adăuga funcționalități sau a repara bug-uri.\n\n"
            f"Task: {task_text}"
        )

    # 1b. Workspace confinement — validează cwd față de allowed_task_roots
    validated_cwd = _validate_task_cwd(cwd)
    if validated_cwd is None:
        logger.warning(f"[Confinement] task respins — cwd '{cwd}' în afara allowed_task_roots")
        _notify("🚫 Task blocat (confinement)", f"cwd {cwd} în afara workspace-ului permis", priority="default")
        return None, f"cwd `{cwd}` e în afara workspace-ului permis (`allowed_task_roots`)."
    cwd = validated_cwd

    # 2. Swarm vs Single Agent
    is_swarm = task_text.startswith("!swarm")
    if is_swarm:
        task_text = task_text[len("!swarm"):].strip()
        task_id = uuid.uuid4().hex[:8]
        if register_queue:
            _active_task_queues[task_id] = asyncio.Queue()
        asyncio.create_task(_swarm_task_exec(task_id, task_text, cwd, is_sysrun))
    else:
        # Single agent backend selection
        agent = "claude"
        if task_text.lower().startswith("gemini "):
            agent = "gemini"
            task_text = task_text[7:].strip()
        elif task_text.lower().startswith("claude "):
            agent = "claude"
            task_text = task_text[7:].strip()

        task_id = uuid.uuid4().hex[:8]
        if register_queue:
            _active_task_queues[task_id] = asyncio.Queue()
        asyncio.create_task(_background_task_exec(task_id, task_text, agent, cwd, is_sysrun))

    return task_id, None


@app.post("/task/run")
async def task_run(request: Request):
    """Spawn autonomous agent as background task and stream output back.
    Saves to history even if stream disconnects.
    """
    body = await request.json()
    task_text = body.get("task", "").strip()
    cwd = body.get("cwd") or _default_task_cwd()

    if not task_text:
        return JSONResponse({"error": "task is required"}, status_code=400)

    task_id, error = _prepare_and_launch_task(task_text, cwd, register_queue=True)
    if error is not None:
        async def _blocked():
            yield f'data: {json.dumps({"choices": [{"delta": {"content": f"**[BLOCKED]** {error}"}}]})}\n\n'
            yield "data: [DONE]\n\n"

        return StreamingResponse(_blocked(), media_type="text/event-stream")

    queue = _active_task_queues[task_id]

    async def _stream():
        try:
            while True:
                chunk = await queue.get()
                if chunk == "[DONE]":
                    break
                yield f'data: {json.dumps({"choices": [{"delta": {"content": chunk}}]})}\n\n'
        except asyncio.CancelledError:
            # Client disconnected, but background task continues independently
            pass
        finally:
            _active_task_queues.pop(task_id, None)
            yield "data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.post("/api/stop")
async def api_stop():
    """Kill switch (WP-G1): oprește toate procesele-agent + pauzează scheduler-ul.
    Expus pentru butonul din UI; din chat/Telegram se apelează prin comanda `!stop`."""
    return JSONResponse(_stop_all())


@app.post("/admin/backup")
async def admin_backup():
    """Trigger manual al backup-ului cache_db. Protejat de auth_middleware."""
    try:
        path = await _backup_cache_db()
        return JSONResponse({"status": "ok", "archive": path})
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


async def _sse_to_openai_json(resp: StreamingResponse, model: str = "kage") -> JSONResponse:
    """Consumă un StreamingResponse SSE și îl transformă într-un răspuns JSON
    OpenAI-compatible (non-stream). Golirea generatorului declanșează și efectele
    lui secundare (salvare istoric/cache/memorie), la fel ca în modul stream."""
    collected: list[str] = []
    async for chunk in resp.body_iterator:
        chunk_str = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        for line in chunk_str.splitlines():
            if line.startswith("data: ") and "[DONE]" not in line:
                try:
                    delta = json.loads(line[6:]).get("choices", [{}])[0].get("delta", {})
                    piece = delta.get("content", "")
                    if piece:
                        collected.append(piece)
                except Exception:
                    pass
    content = "".join(collected)
    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(_time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
    })


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    resp = await _chat_dispatch(request, body)
    # Ramură non-stream: dacă clientul cere stream:false (ex. gateway-ul Telegram),
    # colapsează SSE-ul într-un JSON OpenAI standard. (WP1 / D2)
    if body.get("stream") is False and isinstance(resp, StreamingResponse):
        return await _sse_to_openai_json(resp)
    return resp


async def _chat_dispatch(request: Request, body: dict):
    messages: list = body.get("messages", [])
    session_id: str = request.headers.get("x-session-id", "default")

    last_user = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )

    # Handler server-side pentru task-urile de agent (!run/!swarm/!sysrun), ÎNAINTE
    # de cache. Pornește task-ul în fundal și răspunde cu o confirmare + task id.
    # Streamul complet al task-ului (spre Telegram/UI) vine la WP8. (WP1 / D13)
    _lu_stripped = last_user.strip()
    if re.match(r"^!(run|swarm|sysrun)\b", _lu_stripped, re.IGNORECASE):
        if _lu_stripped.lower().startswith("!run"):
            _task_text = _lu_stripped[len("!run"):].strip()
        else:
            _task_text = _lu_stripped  # !swarm / !sysrun sunt interpretate în helper
        task_id, error = _prepare_and_launch_task(_task_text, _default_task_cwd(), register_queue=False)
        if error is not None:
            confirm = f"**[BLOCKED]** {error}"
        else:
            confirm = f"🚀 Task pornit — id `{task_id}`. Rulează în fundal; rezultatul va veni când e gata."

        async def _agent_confirm():
            yield f'data: {json.dumps({"choices": [{"delta": {"content": confirm}, "index": 0}]})}\n\n'
            yield "data: [DONE]\n\n"

        return StreamingResponse(_agent_confirm(), media_type="text/event-stream")

    if last_user.strip() == "!status":
        return _status_snapshot()

    if last_user.strip().lower() == "!stop":
        return _stop_snapshot()

    if last_user.strip().lower() == "!resume":
        resumed = False
        if _scheduler is not None:
            try:
                _scheduler.resume()
                resumed = True
            except Exception:
                pass
        msg = "▶️ Scheduler reluat." if resumed else "▶️ Scheduler indisponibil."
        return _instant_sse(msg)

    if last_user.strip().lower() == "!sleep":
        return await _sleep_response()

    if last_user.strip().lower() == "!help":
        return _help_response()

    if last_user.strip().lower().startswith("!schedule "):
        return await _handle_schedule_command(last_user.strip())

    # Semantic cache lookup (skip for !nocache and !retry)
    use_cache = "!nocache" not in last_user.lower() and "!retry" not in last_user.lower()
    cache_query = last_user.lower().replace("!nocache", "").strip()
    cache_embedding: Optional[list] = None

    if use_cache:
        cached_response, cached_tier = await _cache_lookup(cache_query)
        if cached_response is not None:
            global _cache_hits
            _cache_hits += 1
            logger.info(f"Cache HIT tier={cached_tier} query={cache_query[:50]!r}")
            return _make_cache_hit_response(cached_response, cached_tier)
        else:
            global _cache_misses
            _cache_misses += 1
            cache_embedding = await _get_embedding(cache_query)

    tier, forced, confidence, routing_method = await decide_tier(last_user)

    # Feedback loop: an explicit tier override teaches the router (WP3). method=="forced"
    # excludes !plan (keeps classifier tier) and un-prefixed classifications.
    if forced and routing_method == "forced":
        asyncio.create_task(_record_routing_feedback(last_user, tier))

    original_tier = tier
    budget_warning: Optional[str] = None
    if tier >= 3 and not forced:
        tier, confidence, budget_warning = _budget_check(tier, confidence)
    budget_downgraded = (tier != original_tier)

    badge = _tier_badge_ext(tier, routing_method, confidence, budget_downgraded)

    memory_ctx = await _memory_retrieve(session_id, last_user, precomputed_emb=cache_embedding)
    obs_context = _get_obsidian_context(last_user)
    system_prompt = _build_system_prompt(tier, obs_context, memory_ctx)
    messages_out = _inject_system_prompt(messages, system_prompt)
    _save_match = re.search(r"!save\s+(\S+)", last_user, re.IGNORECASE)
    save_path: Optional[str] = _save_match.group(1) if _save_match else (
        None if "!save" not in last_user.lower() else ""
    )
    # save_path = None → no save; "" → default AI_Outputs; "some/path.md" → custom

    logger.info(
        f"Tier {tier} | forced={forced} | conf={confidence:.2f} | method={routing_method} | "
        f"obsidian={'yes' if obs_context else 'no'} | save={save_path!r} | "
        f"preview={last_user[:60]!r}"
    )

    _model_label = str(TIER_MODELS[tier]) if tier <= 2 else (TIER_MODELS[tier][1] or "gemini-pro")
    _write_status(True, tier, _model_label, last_user[:60], obs_context is not None)
    _log_to_vault(tier, last_user, obs_context is not None, confidence, forced)

    # Save User message to SQLite
    if _db_conn:
        try:
            _db_conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, "user", last_user))
            _db_conn.commit()
        except Exception as e:
            logger.error(f"Failed to save user message: {e}")

    if tier <= 2:
        messages_out = await _compact_messages(messages_out, MAX_CONTEXT_MESSAGES)
        response = await _route_litellm(tier, messages_out, system_prompt, last_user, messages, save_path=save_path, badge=badge)
    else:
        response = await _route_cli(tier, system_prompt, last_user, messages, save_path=save_path, badge=badge)

    # Wrap generator to store response in cache and history after streaming completes
    original_gen = response.body_iterator

    async def history_caching_gen():
        collected: list[str] = []
        async for chunk in original_gen:
            yield chunk
            chunk_str = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            if chunk_str.startswith("data: ") and "[DONE]" not in chunk_str:
                try:
                    text = json.loads(chunk_str[6:]).get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if text:
                        collected.append(text)
                except Exception:
                    pass
        if collected:
            full = "".join(collected)
            # Strip leading badge (e.g. **[T3·haiku·sem:0.97]** )
            clean_text = re.sub(r'^\*\*\[.*?\]\*\*\s*', '', full)
            if clean_text:
                # Cache store
                if use_cache and cache_embedding is not None:
                    asyncio.create_task(_cache_store_async(cache_query, clean_text, tier, cache_embedding))
                # Memory store — fire-and-forget
                asyncio.create_task(_memory_store(session_id, last_user, clean_text))
                # History store
                if _db_conn:
                    try:
                        _db_conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, "assistant", clean_text))
                        _db_conn.commit()
                    except Exception as e:
                        logger.error(f"Failed to save assistant message: {e}")

    response = StreamingResponse(history_caching_gen(), media_type="text/event-stream")

    # Prepend budget warning AFTER history/caching wrapper so it is not stored in history/cache
    if budget_warning:
        _warning_text = budget_warning
        original_gen = response.body_iterator

        async def warning_gen():
            yield f'data: {json.dumps({"choices": [{"delta": {"content": _warning_text}, "index": 0}]})}\n\n'
            async for chunk in original_gen:
                yield chunk

        response = StreamingResponse(warning_gen(), media_type="text/event-stream")

    return response


# ── Tier decision ─────────────────────────────────────────────────────────────

async def decide_tier(message: str) -> tuple[int, bool, float, str]:
    """Return (tier, forced, confidence, method). Forced=True when prefix overrides classifier."""
    msg = message.strip()
    msg_lower = msg.lower()

    if msg_lower.startswith("escaladează"):
        return 5, True, 1.0, "forced"

    if "!fast" in msg_lower:
        return 1, True, 1.0, "forced"
    if "!best" in msg_lower:
        return 5, True, 1.0, "forced"
    if "!opus" in msg_lower:
        return 6, True, 1.0, "forced"
    if "!gemini" in msg_lower:
        return 4, True, 1.0, "forced"
    if "!retry" in msg_lower:
        last_tier = 1
        try:
            status = json.loads(STATUS_FILE.read_text())
            last_tier = int(status.get("tier", 1))
        except Exception:
            pass
        return min(last_tier + 1, 6), True, 1.0, "forced"
    if "!plan" in msg_lower:
        clean = msg_lower.replace("!plan", "").strip()
        tier, confidence, method = await _classify(clean or msg)
        return max(tier, 2), True, confidence, method

    tier, confidence, method = await _classify(msg)

    # Personal context → minimum tier 2
    if any(kw in msg_lower for kw in PERSONAL_KEYWORDS) and tier < 2:
        tier = 2

    return tier, False, confidence, method


async def _classify(message: str) -> tuple[int, float, str]:
    """Return (tier, confidence, method). Semantic routing primary, Qwen fallback."""
    try:
        tier, conf = await _semantic_classify(message)
        return tier, conf, "sem"
    except Exception as e:
        logger.debug(f"Semantic classify failed ({e}), falling back to Qwen")
        tier, conf, method = await _qwen_classify(message)
        return tier, conf, method


async def _semantic_classify(message: str) -> tuple[int, float]:
    """Classify via k-NN (k=5) over TIER_EXAMPLES + learned feedback, weighted by similarity.

    Tier = argmax of per-tier summed similarity among neighbors above the 0.6 floor.
    Confidence = strength of the best matching neighbor of the winning tier.
    """
    if _routing_collection is None or _routing_collection.count() == 0:
        raise RuntimeError("routing_collection not ready")
    embedding = await _get_embedding(message[:400])
    if embedding is None:
        raise RuntimeError("embedding unavailable")
    n = min(5, _routing_collection.count())
    results = _routing_collection.query(
        query_embeddings=[embedding], n_results=n,
        include=["metadatas", "distances"],
    )
    if not results["distances"] or not results["distances"][0]:
        return 3, 0.6
    votes: dict[int, float] = {}       # tier -> summed similarity weight
    best_sim: dict[int, float] = {}    # tier -> strongest neighbor similarity
    for distance, meta in zip(results["distances"][0], results["metadatas"][0]):
        similarity = 1.0 - distance
        if similarity < 0.6:
            continue
        t = int(meta.get("tier", 3))
        votes[t] = votes.get(t, 0.0) + similarity
        best_sim[t] = max(best_sim.get(t, 0.0), similarity)
    if not votes:
        return 3, 0.6
    tier = max(votes, key=votes.get)
    return tier, round(best_sim[tier], 3)


async def _qwen_classify(message: str) -> tuple[int, float, str]:
    """Return (tier, confidence, method). Digit-only response from Qwen 8B, circuit breaker on failures."""
    global _ollama_failures, _ollama_dead

    if _ollama_dead:
        t = _heuristic_classify(message)
        return t, _TIER_CONFIDENCE.get(t, 0.75), "heur"

    prompt = (
        "Classify this task. Reply with ONLY a single digit (1-6):\n\n"
        "1 = trivial: math, definitions, one-liner facts\n"
        "2 = medium or personal context: projects, decisions, depth needed\n"
        "3 = medium cloud quality (Haiku-level)\n"
        "4 = medium cloud alternative (Gemini)\n"
        "5 = complex: serious analysis, long writing (Sonnet-level)\n"
        "6 = maximum difficulty (Opus-level)\n\n"
        f"Task: {message[:400]}\n\n"
        "Digit:"
    )
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": "qwen3:8b",
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"think": False, "num_predict": 15},
                },
                timeout=15,
            )
        content = r.json().get("message", {}).get("content", "").strip()
        digit = next((c for c in content if c.isdigit() and 1 <= int(c) <= 6), None)
        _ollama_failures = 0
        _ollama_dead = False
        tier = int(digit) if digit else 1
        return tier, _TIER_CONFIDENCE.get(tier, 0.75), "cls"

    except Exception as e:
        _ollama_failures += 1
        logger.warning(f"Classification failed ({e}), failures={_ollama_failures}")
        if _ollama_failures >= 3:
            _ollama_dead = True
            _notify("⚠️ Ollama offline", "Clasificarea a eșuat de 3 ori. Fallback heuristic activ.", priority="high")
            logger.error("Ollama circuit breaker tripped — heuristic fallback active")
        t = _heuristic_classify(message)
        return t, _TIER_CONFIDENCE.get(t, 0.75), "heur"


def _heuristic_classify(message: str) -> int:
    msg_lower = message.lower()
    if any(kw in msg_lower for kw in PERSONAL_KEYWORDS):
        return 2
    if len(message) > 200:
        return 2
    return 1


# ── Router feedback loop (WP3) ────────────────────────────────────────────────

# Prefixes stripped before storing a message as a routing example.
_ROUTING_PREFIXES = (
    "!fast", "!best", "!opus", "!gemini", "!plan", "!retry",
    "!nocache", "!save", "!status", "!help",
)


def _strip_routing_prefixes(message: str) -> str:
    """Remove command prefixes so the learned example is just the natural-language task."""
    clean = message
    for p in _ROUTING_PREFIXES:
        clean = re.sub(re.escape(p), "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^\s*escaladează\s*", "", clean, flags=re.IGNORECASE)
    return clean.strip()


async def _record_routing_feedback(message: str, tier: int) -> None:
    """Learn from an explicit tier override: store the cleaned message under the chosen tier.

    Fired (non-blocking) whenever a forced prefix (!fast/!best/!opus/!gemini/!retry/escaladează)
    picks a tier — a later semantically similar message then routes there via _semantic_classify.
    """
    if _routing_collection is None:
        return
    clean = _strip_routing_prefixes(message)
    if len(clean) < 4:
        return
    try:
        emb = await _get_embedding(clean[:400])
        if emb is None:
            return
        _routing_collection.add(
            documents=[clean[:400]],
            embeddings=[emb],
            metadatas=[{"tier": int(tier), "source": "feedback", "ts": _time.time()}],
            ids=[uuid.uuid4().hex],
        )
        logger.info(f"Routing feedback: tier={tier} query={clean[:40]!r}")
        await _routing_vacuum()
    except Exception as e:
        logger.warning(f"Routing feedback failed: {e}")


async def _routing_vacuum() -> None:
    """Cap feedback entries per tier — keep newest MAX_FEEDBACK_PER_TIER, drop oldest. Seeds kept."""
    if _routing_collection is None:
        return
    try:
        res = _routing_collection.get(include=["metadatas"])
        by_tier: dict[int, list] = {}   # tier -> [(ts, id), ...]
        for id_, meta in zip(res["ids"], res["metadatas"]):
            if meta.get("source") != "feedback":
                continue
            by_tier.setdefault(int(meta.get("tier", -1)), []).append((float(meta.get("ts", 0)), id_))
        to_delete: list[str] = []
        for entries in by_tier.values():
            excess = len(entries) - MAX_FEEDBACK_PER_TIER
            if excess > 0:
                entries.sort()  # oldest ts first
                to_delete.extend(id_ for _, id_ in entries[:excess])
        if to_delete:
            _routing_collection.delete(ids=to_delete)
            logger.info(f"Routing vacuum: removed {len(to_delete)} old feedback entries")
    except Exception as e:
        logger.warning(f"Routing vacuum failed: {e}")


def _usage_counts_today() -> tuple[int, int]:
    """Return (requests_today, cloud_today). Uses in-memory cache, rebuilt once per day."""
    today = datetime.date.today().isoformat()
    if _usage_cache["date"] == today:
        return _usage_cache["total"], _usage_cache["cloud"]
    # Day changed — rebuild from file
    total = cloud = 0
    try:
        lock = FileLock(str(USAGE_LOG) + ".lock")
        with lock.acquire(timeout=2):
            if USAGE_LOG.exists():
                for line in USAGE_LOG.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("ts", "").startswith(today):
                            total += 1
                            if entry.get("cloud"):
                                cloud += 1
                    except Exception:
                        pass
    except Exception:
        pass
    _usage_cache.update({"date": today, "total": total, "cloud": cloud})
    return total, cloud


def _budget_check(tier: int, confidence: float) -> tuple[int, float, Optional[str]]:
    """Enforce daily cloud budget. Returns (tier, confidence, warning_msg_or_none)."""
    global _budget_alert_80_sent
    try:
        cfg = json.loads(KAGE_CONFIG_PATH.read_text(encoding="utf-8"))
        max_cloud = int(cfg.get("max_cloud_calls_per_day", 20))
    except Exception:
        max_cloud = 20

    _, cloud_today = _usage_counts_today()
    today = datetime.date.today().isoformat()

    if cloud_today >= max_cloud:
        _notify(
            "🚫 Budget cloud depășit",
            f"Limita de {max_cloud} apeluri cloud/zi atinsă. Request redirecționat la T2.",
            priority="high",
        )
        logger.warning(f"Budget exceeded ({cloud_today}/{max_cloud}) — downgrading to T2")
        warning = f"🚫 Budget epuizat ({cloud_today}/{max_cloud} cloud) — redirecționat automat la **T2·qwen35b**.\n\n"
        return 2, _TIER_CONFIDENCE[2], warning

    if cloud_today >= int(max_cloud * 0.8) and _budget_alert_80_sent != today:
        _budget_alert_80_sent = today
        _notify(
            "⚠️ Budget cloud 80%",
            f"{cloud_today}/{max_cloud} apeluri cloud folosite azi.",
            priority="default",
        )
        remaining = max_cloud - cloud_today
        warning = f"⚠️ Budget: {cloud_today}/{max_cloud} cloud — mai ai {remaining} apeluri azi. Folosește `!fast` pentru local.\n\n"
        return tier, confidence, warning

    return tier, confidence, None


# ── Semantic cache (Faza 11) ──────────────────────────────────────────────────

async def _get_embedding(text: str) -> Optional[list]:
    """Get embedding from Ollama nomic-embed-text. Returns None on failure."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text[:500]},
                timeout=10,
            )
        return r.json().get("embedding")
    except Exception as e:
        logger.debug(f"Embedding failed: {e}")
        return None


async def _cache_lookup(query: str) -> tuple[Optional[str], Optional[int]]:
    """Return (cached_response, tier) if semantic cache hit, else (None, None)."""
    if _cache_collection is None:
        return None, None
    embedding = await _get_embedding(query)
    if embedding is None:
        return None, None
    try:
        results = _cache_collection.query(
            query_embeddings=[embedding], n_results=1,
            include=["documents", "metadatas", "distances"],
        )
        if not results["distances"] or not results["distances"][0]:
            return None, None
        distance = results["distances"][0][0]
        similarity = 1.0 - distance
        if similarity < CACHE_SIMILARITY_THRESHOLD:
            return None, None
        metadata = results["metadatas"][0][0]
        if _time.time() - float(metadata.get("ts", 0)) > CACHE_TTL_SECONDS:
            return None, None
        return results["documents"][0][0], int(metadata.get("tier", 1))
    except Exception as e:
        logger.debug(f"Cache lookup failed: {e}")
        return None, None


async def _cache_store_async(query: str, response_text: str, tier: int, embedding: list) -> None:
    if _cache_collection is None or not embedding:
        return
    try:
        _cache_collection.add(
            documents=[response_text[:6000]],
            embeddings=[embedding],
            metadatas=[{"tier": tier, "ts": str(_time.time())}],
            ids=[uuid.uuid4().hex],
        )
        logger.debug(f"Cache stored tier={tier} len={len(response_text)}")
    except Exception as e:
        logger.debug(f"Cache store failed: {e}")


async def _cache_vacuum() -> None:
    """Delete expired cache entries (older than CACHE_TTL_SECONDS)."""
    if _cache_collection is None:
        return
    try:
        cutoff = _time.time() - CACHE_TTL_SECONDS
        results = _cache_collection.get(include=["metadatas"])
        to_delete = [
            id_ for id_, meta in zip(results["ids"], results["metadatas"])
            if float(meta.get("ts", 0)) < cutoff
        ]
        if to_delete:
            _cache_collection.delete(ids=to_delete)
            logger.info(f"Cache vacuum: removed {len(to_delete)} expired entries")
        else:
            logger.debug("Cache vacuum: nothing to remove")
    except Exception as e:
        logger.warning(f"Cache vacuum failed: {e}")


async def _backup_cache_db() -> str:
    """Backup cache_db/ (SQLite + ChromaDB) într-un tar.gz cu rotație. Returnează calea arhivei.

    SQLite (chat_history.db) e copiat consistent via Online Backup API; restul cache_db/
    (ChromaDB) prin copytree. Rulează zilnic la 05:00 sau on-demand via POST /admin/backup.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = BACKUP_DIR / f"cache_db-{ts}.tar.gz"
    try:
        with tempfile.TemporaryDirectory() as staging:
            staging_path = Path(staging) / "cache_db"
            if CACHE_DB_PATH.exists():
                shutil.copytree(CACHE_DB_PATH, staging_path)
            else:
                staging_path.mkdir(parents=True)
            # Snapshot SQLite consistent (suprascrie copia brută din copytree)
            if _db_conn is not None:
                try:
                    dest = sqlite3.connect(str(staging_path / "chat_history.db"))
                    with dest:
                        _db_conn.backup(dest)
                    dest.close()
                except Exception as e:
                    logger.warning(f"[Backup] SQLite online backup eșuat, folosesc copia brută: {e}")
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(staging_path, arcname="cache_db")
        # Rotație: păstrează ultimele BACKUP_KEEP arhive
        if BACKUP_KEEP > 0:
            backups = sorted(BACKUP_DIR.glob("cache_db-*.tar.gz"))
            for old in backups[:-BACKUP_KEEP]:
                old.unlink(missing_ok=True)
        logger.info(f"[Backup] cache_db → {archive_path} ({archive_path.stat().st_size} bytes)")
        return str(archive_path)
    except Exception as e:
        logger.error(f"[Backup] eșuat: {e}")
        _notify("⚠️ Backup eșuat", str(e), priority="high")
        raise


def _restore_cache_db(archive_path, dest=None) -> str:
    """Restaurează cache_db/ dintr-o arhivă produsă de `_backup_cache_db`.

    Extrage `cache_db-*.tar.gz` și înlocuiește directorul `dest` (implicit
    CACHE_DB_PATH). Directorul curent e mutat în `<dest>.pre-restore-<ts>` ca plasă
    de siguranță. A se rula cu ORCHESTRATORUL OPRIT (SQLite/ChromaDB țin fișiere
    deschise). Vezi RESTORE.md. Returnează un mesaj de stare.
    """
    archive = Path(archive_path)
    target = Path(dest) if dest is not None else CACHE_DB_PATH
    if not archive.exists():
        raise FileNotFoundError(f"arhiva nu există: {archive}")

    with tempfile.TemporaryDirectory() as staging:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(staging)
        extracted = Path(staging) / "cache_db"
        if not extracted.exists():
            raise ValueError(f"arhivă invalidă: lipsește cache_db/ în {archive.name}")
        if target.exists():
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            safety = target.with_name(f"{target.name}.pre-restore-{ts}")
            shutil.move(str(target), str(safety))
            logger.info(f"[Restore] cache_db curent salvat în {safety}")
        shutil.copytree(extracted, target)
    logger.info(f"[Restore] cache_db restaurat din {archive.name}")
    return f"cache_db restaurat din {archive.name}"


async def _memory_store(session_id: str, user_msg: str, assistant_msg: str) -> None:
    """Stochează perechea (user, assistant) în long_term_memory cu dedup per-sesiune."""
    if _memory_collection is None:
        return
    try:
        document = f"{user_msg[:300]}\n{assistant_msg[:300]}"
        emb = await _get_embedding(document)
        if emb is None:
            return
        # Dedup: skip dacă există deja un fapt similar în această sesiune
        if _memory_collection.count() > 0:
            results = _memory_collection.query(
                query_embeddings=[emb], n_results=1,
                where={"session_id": session_id},
                include=["distances"],
            )
            if results["distances"] and results["distances"][0]:
                if 1.0 - results["distances"][0][0] >= MEMORY_DEDUP_THRESHOLD:
                    return
        _memory_collection.add(
            documents=[document],
            embeddings=[emb],
            metadatas=[{"session_id": session_id, "timestamp": str(_time.time()), "type": "context"}],
            ids=[uuid.uuid4().hex],
        )
    except Exception as e:
        logger.debug(f"[Memory] store failed: {e}")


async def _memory_retrieve(
    session_id: str, query: str, precomputed_emb: Optional[list] = None
) -> str:
    """Returnează faptele relevante din long_term_memory pentru injecție în system prompt."""
    if _memory_collection is None or _memory_collection.count() == 0:
        return ""
    try:
        emb = precomputed_emb or await _get_embedding(query[:500])
        if emb is None:
            return ""
        n = min(MEMORY_TOP_K, _memory_collection.count())
        results = _memory_collection.query(
            query_embeddings=[emb], n_results=n,
            include=["documents", "distances"],
        )
        relevant = [
            doc for doc, dist in zip(results["documents"][0], results["distances"][0])
            if 1.0 - dist >= MEMORY_RELEVANCE_THRESHOLD
        ]
        return "\n".join(relevant) if relevant else ""
    except Exception as e:
        logger.debug(f"[Memory] retrieve failed: {e}")
        return ""


def _make_cache_hit_response(response_text: str, tier: int) -> StreamingResponse:
    badge = f"**[CACHE·T{tier}·{TIER_SHORT.get(tier, '?')}]** "
    full = badge + response_text
    chunk_size = 20

    async def generate():
        for i in range(0, len(full), chunk_size):
            yield f'data: {json.dumps({"choices": [{"delta": {"content": full[i:i+chunk_size]}, "index": 0}]})}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def _aggregate_usage() -> dict:
    """Aggregate usage_log.jsonl into dashboard data."""
    today = datetime.date.today().isoformat()
    all_entries: list[dict] = []
    try:
        lock = FileLock(str(USAGE_LOG) + ".lock")
        with lock.acquire(timeout=2):
            if USAGE_LOG.exists():
                for line in USAGE_LOG.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        all_entries.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass

    today_entries = [e for e in all_entries if e.get("ts", "").startswith(today)]
    total = len(today_entries)
    cloud = sum(1 for e in today_entries if e.get("cloud"))
    claude_count = sum(1 for e in today_entries if e.get("agent") == "claude")
    gemini_count = sum(1 for e in today_entries if e.get("agent") == "gemini")

    by_tier: dict[int, int] = {}
    latency_by_tier: dict[int, list] = {}
    for e in today_entries:
        t = int(e.get("tier", 0))
        by_tier[t] = by_tier.get(t, 0) + 1
        if e.get("duration_ms") is not None:
            latency_by_tier.setdefault(t, []).append(e["duration_ms"])

    avg_latency = {t: int(sum(v) / len(v)) for t, v in latency_by_tier.items() if v}

    try:
        cfg = json.loads(KAGE_CONFIG_PATH.read_text(encoding="utf-8"))
        max_cloud = int(cfg.get("max_cloud_calls_per_day", 20))
    except Exception:
        max_cloud = 20

    task_count = 0
    try:
        if SCHEDULED_TASKS_FILE.exists():
            tasks = json.loads(SCHEDULED_TASKS_FILE.read_text(encoding="utf-8"))
            task_count = sum(1 for t in tasks if t.get("enabled", True))
    except Exception:
        pass

    return {
        "total": total,
        "cloud": cloud,
        "claude_count": claude_count,
        "gemini_count": gemini_count,
        "local": total - cloud,
        "by_tier": by_tier,
        "avg_latency": avg_latency,
        "last_10": list(reversed(today_entries))[:10],
        "date": today,
        "max_cloud": max_cloud,
        "cache_hits": _cache_hits,
        "cache_misses": _cache_misses,
        "task_count": task_count,
        "memory_count": _memory_collection.count() if _memory_collection else 0,
    }


def _build_chat_html() -> str:
    return """<!DOCTYPE html>
<html lang="ro"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Orchestrator — Chat</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;color:#333;height:100vh;display:flex;flex-direction:column}
.topbar{background:#fff;border-bottom:1px solid #e8e8e8;padding:12px 20px;display:flex;align-items:center;gap:12px;flex-shrink:0}
.topbar h1{font-size:16px;font-weight:600;color:#222}
.topbar a{font-size:13px;color:#2196F3;text-decoration:none;padding:5px 12px;border:1px solid #2196F3;border-radius:5px}
.topbar a:hover{background:#2196F3;color:#fff}
.topbar .status{font-size:12px;color:#999;margin-left:auto}
#messages{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px}
.msg{max-width:80%;padding:10px 14px;border-radius:12px;font-size:14px;line-height:1.5;word-wrap:break-word}
.msg.user{align-self:flex-end;background:#2196F3;color:#fff;border-bottom-right-radius:4px}
.msg.assistant{align-self:flex-start;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.1);border-bottom-left-radius:4px}
.msg.assistant code{background:#f0f0f0;padding:1px 4px;border-radius:3px;font-size:13px;font-family:monospace}
.msg.assistant pre{background:#1e1e1e;color:#d4d4d4;padding:10px 12px;border-radius:6px;overflow-x:auto;font-size:12px;margin:6px 0}
.msg.assistant pre code{background:transparent;padding:0;color:inherit}
.msg.assistant strong{color:#111}
.msg.assistant .badge{display:inline-block;font-size:11px;font-family:monospace;background:#e8f4fd;color:#1565C0;padding:2px 7px;border-radius:10px;margin-bottom:5px;font-weight:600}
.msg.typing{color:#999;font-style:italic}
.msg.error{background:#fff3f3;border:1px solid #ffcdd2;color:#c62828}
.bottom{background:#fff;border-top:1px solid #e8e8e8;padding:12px 20px;flex-shrink:0}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.chip{font-size:12px;padding:3px 10px;border-radius:12px;border:1px solid #ddd;background:#fafafa;cursor:pointer;color:#555;transition:all .15s}
.chip:hover{background:#e3f2fd;border-color:#2196F3;color:#1565C0}
.input-row{display:flex;gap:8px;align-items:flex-end}
#input{flex:1;border:1px solid #ddd;border-radius:8px;padding:10px 14px;font-size:14px;font-family:inherit;resize:none;outline:none;max-height:120px;min-height:44px}
#input:focus{border-color:#2196F3}
#send-btn{background:#2196F3;color:#fff;border:none;border-radius:8px;padding:10px 20px;font-size:14px;cursor:pointer;white-space:nowrap;height:44px}
#send-btn:hover{background:#1976D2}
#send-btn:disabled{background:#b0bec5;cursor:default}
.empty-state{text-align:center;color:#bbb;margin:auto;padding:40px 20px}
.empty-state h2{font-size:20px;margin-bottom:8px;color:#ccc}
.empty-state p{font-size:13px;line-height:1.6}
</style></head>
<body>
<div class="topbar">
  <h1>AI Orchestrator</h1>
  <a href="/dashboard">📊 Dashboard</a>
  <span class="status" id="status-label">gata</span>
</div>
<div id="messages">
  <div class="empty-state">
    <h2>Ce ai de gând azi?</h2>
    <p>Scrie un mesaj sau alege un prefix din bara de jos.<br>
    <code>!help</code> pentru lista completă de comenzi.</p>
  </div>
</div>
<div class="bottom">
  <div class="chips">
    <button class="chip" data-prefix="!fast">⚡ fast</button>
    <button class="chip" data-prefix="!best">🌟 best</button>
    <button class="chip" data-prefix="!plan">🗺 plan</button>
    <button class="chip" data-prefix="!nocache">🔄 nocache</button>
    <button class="chip" data-prefix="!save">💾 save</button>
    <button class="chip" data-prefix="!retry">🔁 retry</button>
    <button class="chip" data-prefix="!status">📊 status</button>
    <button class="chip" data-prefix="!help">❓ help</button>
  </div>
  <div class="input-row">
    <textarea id="input" placeholder="Scrie un mesaj... (Ctrl+Enter pentru trimite)" rows="1"></textarea>
    <button id="send-btn">Trimite</button>
  </div>
</div>
<script>
var history = [];
var maxHistory = 20;

// ── Chips ─────────────────────────────────────────────────────────────────────
document.querySelectorAll('.chip').forEach(function(chip) {
  chip.addEventListener('click', function() {
    var prefix = chip.dataset.prefix + ' ';
    var inp = document.getElementById('input');
    if (!inp.value.includes(chip.dataset.prefix)) {
      inp.value = prefix + inp.value;
    }
    inp.focus();
  });
});

// ── Auto-resize textarea ──────────────────────────────────────────────────────
var inp = document.getElementById('input');
inp.addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 120) + 'px';
});

// ── Submit ────────────────────────────────────────────────────────────────────
inp.addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); doSend(); }
});
document.getElementById('send-btn').addEventListener('click', doSend);

function doSend() {
  var text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  inp.style.height = 'auto';
  sendMessage(text);
}

// ── Markdown render ───────────────────────────────────────────────────────────
function renderMarkdown(raw) {
  // Escape HTML
  var s = raw.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  // Code blocks
  s = s.replace(/```[\\w]*\\n?([\\s\\S]*?)```/g, '<pre><code>$1</code></pre>');
  // Inline code
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Bold
  s = s.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
  // Italic
  s = s.replace(/\\*([^*]+)\\*/g, '<em>$1</em>');
  // Newlines (outside pre)
  s = s.replace(/\\n/g, '<br>');
  // Extract badge from start and style it
  s = s.replace(/^&lt;br&gt;/, '');
  // Replace **[badge]** style with styled span
  s = s.replace(/&lt;strong&gt;\\[([^\\]]+)\\]&lt;\\/strong&gt;/g, '<span class="badge">[$1]</span>');
  // Also handle already-escaped badge: <strong>[...]</strong>
  s = s.replace(/<strong>\\[([^\\]]+)\\]<\\/strong>/g, '<span class="badge">[$1]</span>');
  return s;
}

// ── DOM helpers ───────────────────────────────────────────────────────────────
function clearEmpty() {
  var e = document.querySelector('.empty-state');
  if (e) e.remove();
}

function appendMessage(role, html) {
  var div = document.createElement('div');
  div.className = 'msg ' + role;
  div.innerHTML = html;
  document.getElementById('messages').appendChild(div);
  div.scrollIntoView({behavior:'smooth', block:'end'});
  return div;
}

// ── Send & stream ─────────────────────────────────────────────────────────────
var isSending = false;

async function sendMessage(text) {
  if (isSending) return;
  isSending = true;
  clearEmpty();

  document.getElementById('send-btn').disabled = true;
  document.getElementById('status-label').textContent = 'procesează...';

  appendMessage('user', escapeHtml(text));
  history.push({role:'user', content:text});

  var assistantEl = appendMessage('assistant', '<span class="typing">...</span>');
  var content = '';

  try {
    var body = JSON.stringify({messages: history.slice(-maxHistory)});
    var resp = await fetch('/v1/chat/completions', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: body
    });

    if (!resp.ok) throw new Error('HTTP ' + resp.status);

    var reader = resp.body.getReader();
    var decoder = new TextDecoder();
    var buf = '';

    while (true) {
      var result = await reader.read();
      if (result.done) break;
      buf += decoder.decode(result.value, {stream:true});
      var lines = buf.split('\\n');
      buf = lines.pop() || '';
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        if (line.startsWith('data: ') && line.indexOf('[DONE]') === -1) {
          try {
            var data = JSON.parse(line.slice(6));
            var chunk = (data.choices && data.choices[0] && data.choices[0].delta && data.choices[0].delta.content) || '';
            content += chunk;
            assistantEl.innerHTML = renderMarkdown(content);
            assistantEl.scrollIntoView({behavior:'smooth', block:'end'});
          } catch(ex) {}
        }
      }
    }

    history.push({role:'assistant', content:content});
    document.getElementById('status-label').textContent = 'gata';

  } catch(err) {
    assistantEl.className = 'msg error';
    assistantEl.textContent = 'Eroare: ' + err.message;
    document.getElementById('status-label').textContent = 'eroare';
  }

  document.getElementById('send-btn').disabled = false;
  isSending = false;
  document.getElementById('input').focus();
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

document.getElementById('input').focus();
</script>
</body></html>"""


def _build_dashboard_html(data: dict) -> str:
    by_tier = data["by_tier"]
    avg_latency = data["avg_latency"]
    date = data["date"]
    max_cloud = data.get("max_cloud", 20)
    task_count = data.get("task_count", 0)

    tier_names = {1: "qwen8b", 2: "qwen35b", 3: "haiku", 4: "gemini", 5: "sonnet", 6: "opus"}
    max_count = max(by_tier.values(), default=1)

    tier_rows = ""
    for t in range(1, 7):
        count = by_tier.get(t, 0)
        pct = int(count / max_count * 100) if max_count > 0 and count > 0 else 0
        color = "#4CAF50" if t <= 2 else "#2196F3"
        avg_ms = avg_latency.get(t)
        avg_str = f"{avg_ms}ms" if avg_ms is not None else "—"
        min_w = "4px" if count > 0 else "0"
        tier_rows += (
            f"<tr><td style='padding:4px 8px;white-space:nowrap'>T{t} · {tier_names.get(t,'?')}</td>"
            f"<td style='padding:4px 8px;width:60%'>"
            f"<div style='background:#eee;border-radius:3px;height:18px'>"
            f"<div style='background:{color};width:{pct}%;height:18px;border-radius:3px;min-width:{min_w}'></div>"
            f"</div></td>"
            f"<td style='padding:4px 8px;text-align:right'>{count}</td>"
            f"<td style='padding:4px 8px;text-align:right;color:#888'>{avg_str}</td></tr>"
        )

    now_str = datetime.datetime.now().strftime("%H:%M:%S")

    # Initial values for server-rendered cards (JS will update live)
    total = data["total"]
    cloud = data["cloud"]
    local = data["local"]
    cache_hits = data.get("cache_hits", 0)
    cache_misses = data.get("cache_misses", 0)
    budget_pct = int(cloud / max_cloud * 100) if max_cloud > 0 else 0
    budget_color = "#4CAF50" if budget_pct < 80 else ("#FF9800" if budget_pct < 100 else "#f44336")
    cache_total = cache_hits + cache_misses
    hit_pct = int(cache_hits / cache_total * 100) if cache_total > 0 else 0

    req_rows_init = ""
    for e in data["last_10"]:
        ts = e.get("ts", "")[:19].replace("T", " ")
        t = e.get("tier", "?")
        model = e.get("model", "—")
        agent = e.get("agent")
        cloud_mark = f"☁ ({agent})" if agent else ("☁" if e.get("cloud") else "⚙")
        dur = f"{e['duration_ms']}ms" if e.get("duration_ms") is not None else "—"
        preview = (e.get("preview") or "")[:45]
        req_rows_init += (
            f"<tr><td style='padding:3px 8px;color:#888;font-size:12px'>{ts}</td>"
            f"<td style='padding:3px 8px;text-align:center'>T{t}</td>"
            f"<td style='padding:3px 8px;font-size:12px'>{model}</td>"
            f"<td style='padding:3px 8px;text-align:center'>{cloud_mark}</td>"
            f"<td style='padding:3px 8px;text-align:right;color:#888'>{dur}</td>"
            f"<td style='padding:3px 8px;font-size:12px'>{preview}</td></tr>"
        )
    if not req_rows_init:
        req_rows_init = "<tr><td colspan='6' style='padding:12px;text-align:center;color:#aaa'>Niciun request azi</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="ro"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Orchestrator — Dashboard</title>
<style>
*{{box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#f5f5f5;color:#333}}
.topbar{{background:#fff;border-bottom:1px solid #e8e8e8;padding:12px 24px;display:flex;align-items:center;gap:16px}}
.topbar h1{{font-size:16px;margin:0;color:#222;font-weight:600}}
.topbar a{{font-size:13px;color:#2196F3;text-decoration:none;padding:5px 12px;border:1px solid #2196F3;border-radius:5px}}
.topbar a:hover{{background:#2196F3;color:#fff}}
.topbar .date{{font-size:12px;color:#999;margin-left:auto}}
.main{{padding:20px 24px}}
.tabs{{display:flex;gap:0;margin-bottom:20px;border-bottom:2px solid #e8e8e8}}
.tab{{padding:8px 20px;font-size:13px;font-weight:500;color:#888;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px}}
.tab.active{{color:#2196F3;border-bottom-color:#2196F3}}
.tab-content{{display:none}}.tab-content.active{{display:block}}
.cards{{display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap}}
.card{{background:#fff;border-radius:8px;padding:16px 20px;flex:1;min-width:100px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.card-val{{font-size:30px;font-weight:700;line-height:1}}
.card-lbl{{font-size:11px;color:#888;margin-top:4px;text-transform:uppercase;letter-spacing:.5px}}
.card-sub{{font-size:11px;color:#aaa;margin-top:6px}}
.cloud .card-val{{color:#2196F3}}.local .card-val{{color:#4CAF50}}
.budget-bar{{background:#eee;border-radius:3px;height:6px;margin-top:8px}}
.budget-fill{{height:6px;border-radius:3px;transition:width .3s,background .3s}}
h2{{font-size:13px;font-weight:600;margin:0 0 10px;color:#555;text-transform:uppercase;letter-spacing:.5px}}
table{{border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:24px}}
th{{background:#f8f8f8;font-size:11px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.5px;padding:8px;text-align:left;border-bottom:1px solid #eee}}
tr:hover td{{background:#fafafa}}
.footer{{font-size:11px;color:#bbb;text-align:right}}
.btn{{background:#2196F3;color:#fff;border:none;padding:5px 12px;border-radius:5px;cursor:pointer;font-size:12px}}
.btn:hover{{background:#1976D2}}
.btn-danger{{background:#f44336}}.btn-danger:hover{{background:#d32f2f}}
.live-dot{{display:inline-block;width:7px;height:7px;border-radius:50%;background:#4CAF50;margin-right:5px;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
.task-form{{display:flex;gap:8px;margin-top:12px;align-items:center;flex-wrap:wrap}}
.task-form input{{border:1px solid #ddd;border-radius:5px;padding:6px 10px;font-size:13px}}
#new-cron{{width:130px}}#new-msg{{flex:1;min-width:180px}}
</style></head><body>
<div class="topbar">
  <h1>AI Orchestrator</h1>
  <a href="/chat">💬 Chat</a>
  <span class="date"><span class="live-dot"></span>live &nbsp;·&nbsp; {date} &nbsp;·&nbsp; actualizat <span id="last-updated">{now_str}</span></span>
</div>
<div class="main">
<div class="tabs">
  <div class="tab active" onclick="switchTab('stats',this)">📊 Stats</div>
  <div class="tab" onclick="switchTab('tasks',this)">⏰ Tasks ({task_count})</div>
</div>

<div id="tab-stats" class="tab-content active">
<div class="cards">
  <div class="card"><div class="card-val" id="stat-total">{total}</div><div class="card-lbl">Total azi</div></div>
  <div class="card cloud">
    <div class="card-val" id="stat-cloud">{cloud}</div>
    <div class="card-lbl">☁ Cloud Total</div>
    <div class="card-sub" id="stat-agent-counts">{data.get("claude_count", 0)} Claude · {data.get("gemini_count", 0)} Gemini</div>
  </div>
  <div class="card local"><div class="card-val" id="stat-local">{local}</div><div class="card-lbl">⚙ Local</div></div>
  <div class="card">
    <div class="card-val" id="stat-budget-val" style="font-size:22px;color:{budget_color}">{cloud}/{max_cloud}</div>
    <div class="card-lbl">Budget cloud</div>
    <div class="budget-bar"><div class="budget-fill" id="budget-fill" style="background:{budget_color};width:{budget_pct}%"></div></div>
    <div class="card-sub" id="stat-budget-pct">{budget_pct}% folosit</div>
  </div>
  <div class="card">
    <div class="card-val" id="stat-cache-pct" style="font-size:22px;color:#9C27B0">{hit_pct}%</div>
    <div class="card-lbl">Cache hit rate</div>
    <div class="card-sub" id="stat-cache-detail">{cache_hits} hits · {cache_misses} misses</div>
  </div>
</div>
<h2>Distribuție tier</h2>
<table><thead><tr><th>Tier</th><th>Utilizare</th><th style="text-align:right">Req</th><th style="text-align:right">Latență medie</th></tr></thead>
<tbody>{tier_rows}</tbody></table>
<h2>Ultimele 10 request-uri</h2>
<table><thead><tr><th>Ora</th><th>Tier</th><th>Model</th><th>Tip</th><th style="text-align:right">Durată</th><th>Preview</th></tr></thead>
<tbody id="req-tbody">{req_rows_init}</tbody></table>
<div class="footer">Date din usage_log.jsonl · actualizat <span id="footer-updated">{now_str}</span></div>
</div>

<div id="tab-tasks" class="tab-content">
<h2>Tasks programate</h2>
<table><thead><tr><th>ID</th><th>Cron</th><th>Mesaj</th><th>Activ</th><th></th></tr></thead>
<tbody id="tasks-tbody"><tr><td colspan="5" style="padding:12px;text-align:center;color:#aaa">Se încarcă...</td></tr></tbody></table>
<div class="task-form">
  <input id="new-cron" placeholder='Cron (e.g. 0 8 * * *)' title="Format: min oră zi lună zi-săpt">
  <input id="new-msg" placeholder="Mesaj task...">
  <button class="btn" onclick="addTask()">+ Adaugă task</button>
</div>
</div>
</div>

<script>
function switchTab(name, el) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'tasks') loadTasks();
}}

// ── Live stats polling ────────────────────────────────────────────────────────
async function updateStats() {{
  try {{
    const r = await fetch('/api/stats');
    const d = await r.json();
    document.getElementById('stat-total').textContent = d.total;
    document.getElementById('stat-cloud').textContent = d.cloud;
    document.getElementById('stat-agent-counts').textContent = (d.claude_count||0) + ' Claude · ' + (d.gemini_count||0) + ' Gemini';
    document.getElementById('stat-local').textContent = d.local;
    const maxCloud = d.max_cloud || 20;
    const pct = Math.round(d.cloud / maxCloud * 100);
    const color = pct < 80 ? '#4CAF50' : (pct < 100 ? '#FF9800' : '#f44336');
    document.getElementById('stat-budget-val').textContent = d.cloud + '/' + maxCloud;
    document.getElementById('stat-budget-val').style.color = color;
    document.getElementById('stat-budget-pct').textContent = pct + '% folosit';
    const fill = document.getElementById('budget-fill');
    fill.style.width = pct + '%'; fill.style.background = color;
    const ch = d.cache_hits || 0, cm = d.cache_misses || 0;
    const hitPct = (ch + cm > 0) ? Math.round(ch / (ch + cm) * 100) : 0;
    document.getElementById('stat-cache-pct').textContent = hitPct + '%';
    document.getElementById('stat-cache-detail').textContent = ch + ' hits · ' + cm + ' misses';
    updateReqTable(d.last_10 || []);
    const now = new Date().toLocaleTimeString('ro-RO');
    document.getElementById('last-updated').textContent = now;
    document.getElementById('footer-updated').textContent = now;
  }} catch(e) {{ console.warn('Stats poll failed:', e); }}
}}

function updateReqTable(entries) {{
  const tbody = document.getElementById('req-tbody');
  if (!entries.length) {{
    tbody.innerHTML = "<tr><td colspan='6' style='padding:12px;text-align:center;color:#aaa'>Niciun request azi</td></tr>";
    return;
  }}
  tbody.innerHTML = entries.map(e => {{
    const ts = (e.ts||'').slice(0,19).replace('T',' ');
    const dur = e.duration_ms != null ? e.duration_ms + 'ms' : '—';
    const agent = e.agent;
    const cloud = agent ? `☁ (${agent})` : (e.cloud ? '☁' : '⚙');
    const preview = (e.preview||'').slice(0,45);
    return "<tr>" +
      "<td style='padding:3px 8px;color:#888;font-size:12px'>" + ts + "</td>" +
      "<td style='padding:3px 8px;text-align:center'>T" + (e.tier||'?') + "</td>" +
      "<td style='padding:3px 8px;font-size:12px'>" + (e.model||'—') + "</td>" +
      "<td style='padding:3px 8px;text-align:center'>" + cloud + "</td>" +
      "<td style='padding:3px 8px;text-align:right;color:#888'>" + dur + "</td>" +
      "<td style='padding:3px 8px;font-size:12px'>" + preview + "</td></tr>";
  }}).join('');
}}

// ── Tasks management ──────────────────────────────────────────────────────────
async function loadTasks() {{
  try {{
    const r = await fetch('/schedule', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:'{{"action":"list"}}'}});
    const d = await r.json();
    renderTasks(d.tasks || []);
  }} catch(e) {{ console.warn('Tasks load failed:', e); }}
}}

function renderTasks(tasks) {{
  const tbody = document.getElementById('tasks-tbody');
  if (!tasks.length) {{
    tbody.innerHTML = "<tr><td colspan='5' style='padding:12px;text-align:center;color:#aaa'>Niciun task programat</td></tr>";
    return;
  }}
  tbody.innerHTML = tasks.map(t => {{
    const enabled = t.enabled !== false ? '✓' : '✗';
    const msg = (t.message||'').slice(0,60);
    return "<tr>" +
      "<td style='padding:4px 8px;font-family:monospace;font-size:12px;color:#888'>" + (t.id||'') + "</td>" +
      "<td style='padding:4px 8px'><code style='background:#f0f0f0;padding:2px 5px;border-radius:3px;font-size:12px'>" + (t.cron||'') + "</code></td>" +
      "<td style='padding:4px 8px;font-size:13px'>" + msg + "</td>" +
      "<td style='padding:4px 8px;text-align:center'>" + enabled + "</td>" +
      "<td style='padding:4px 8px'><button class='btn btn-danger' onclick='deleteTask(\"" + t.id + "\")'>Șterge</button></td>" +
      "</tr>";
  }}).join('');
}}

async function deleteTask(id) {{
  if (!confirm('Ștergi task-ul ' + id + '?')) return;
  await fetch('/schedule', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{action:'remove', id}})}});
  loadTasks();
  updateStats();
}}

async function addTask() {{
  const cron = document.getElementById('new-cron').value.trim();
  const message = document.getElementById('new-msg').value.trim();
  if (!cron || !message) {{ alert('Completează câmpurile cron și mesaj'); return; }}
  const r = await fetch('/schedule', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{action:'add', cron, message}})}});
  const d = await r.json();
  if (d.error) {{ alert('Eroare: ' + d.error); return; }}
  document.getElementById('new-cron').value = '';
  document.getElementById('new-msg').value = '';
  loadTasks();
  updateStats();
}}

// Start live polling
updateStats();
setInterval(updateStats, 10000);
</script>
</body></html>"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tier_badge(tier: int) -> str:
    short = TIER_SHORT.get(tier, f"t{tier}")
    return f"**[T{tier}·{short}]** "


def _tier_badge_ext(tier: int, method: str, confidence: float, budget_downgraded: bool = False) -> str:
    """Extended tier badge with routing method and confidence."""
    short = TIER_SHORT.get(tier, f"t{tier}")
    if budget_downgraded:
        return f"**[T{tier}·{short}·budget⚠️]** "
    if method == "forced":
        return f"**[T{tier}·{short}·forțat]** "
    if method in ("sem", "cls"):
        return f"**[T{tier}·{short}·{method}:{confidence:.2f}]** "
    return f"**[T{tier}·{short}·heur]** "


async def _sleep_response() -> StreamingResponse:
    """Kill caffeinate and force sleep."""
    text = "💤 Se activează modul sleep pe Mac...\n(Anti-sleep dezactivat, pmset sleepnow executat)"
    try:
        # Kill all caffeinate processes
        os.system("pkill caffeinate")
        # Force sleep
        os.system("pmset sleepnow")
    except Exception as e:
        text = f"❌ Eroare la activarea sleep: {e}"

    async def generate():
        yield f'data: {json.dumps({"choices": [{"delta": {"content": text}}]})}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def _help_response() -> StreamingResponse:
    """Instant !help response — no LLM call."""
    text = "\n".join([
        "📖 **Prefixe disponibile:**",
        "",
        "  `!fast`     → Tier 1 (Qwen 8B local) — răspuns rapid",
        "  `!best`     → Tier 5 (Claude Sonnet) — calitate maximă",
        "  `!opus`     → Tier 6 (Claude Opus) — dificultate maximă",
        "  `!gemini`   → Tier 4 (Gemini) — alternativă cloud",
        "  `!plan`     → min Tier 2 — raționament + context personal",
        "  `!retry`    → Tier + 1 față de ultimul răspuns (max T6)",
        "  `!nocache`  → Sare peste cache semantic",
        "  `!save`              → Salvează răspunsul în Obsidian AI_Outputs/{azi}.md",
        "  `!save plans/x.md`  → Salvează în Obsidian la path custom (ex: plans/features.md)",
        '  `!schedule "CRON" msg` → Adaugă task programat',
        "  `!run`      → Task autonom (Claude/Gemini)",
        "  `!sysrun`   → Task autonom cu context orchestrator",
        "  `!swarm`    → Task autonom PARALEL (Claude + Gemini)",
        "  `!sleep`    → Pune Mac-ul în sleep (dezactivează anti-sleep)",
        "  `!status`   → Snapshot instant (budget, cache, servicii)",
        "  `!stop`     → Kill switch: oprește toți agenții + pauzează scheduler-ul",
        "  `!resume`   → Reia scheduler-ul după !stop",
        "  `!help`     → Această listă",
        "  `escaladează` → echivalent cu !best (în română)",
        "",
        "**Tips:** Prefixele se pot combina: `!nocache !best explică-mi X`",
        "  Dashboard: http://localhost:4001/dashboard",
        "  Chat UI:   http://localhost:4001/chat",
    ])

    async def generate():
        chunk_size = 40
        for i in range(0, len(text), chunk_size):
            yield f'data: {json.dumps({"choices": [{"delta": {"content": text[i:i+chunk_size]}, "index": 0}]})}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def _port_up(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _send_ntfy_sync(title: str, body: str, priority: str = "default") -> None:
    try:
        cfg = json.loads(KAGE_CONFIG_PATH.read_text(encoding="utf-8"))
        ntfy_url = cfg.get("ntfy_url", "").rstrip("/")
        topic = cfg.get("ntfy_topic", "")
        if not ntfy_url or not topic or "CHANGEME" in topic:
            return
        req = urllib.request.Request(
            f"{ntfy_url}/{topic}",
            data=body.encode("utf-8"),
            headers={
                "Title": urllib.parse.quote(title),
                "Priority": priority,
                "Content-Type": "text/plain; charset=utf-8",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logger.debug(f"ntfy send failed: {e}")


def _notify(title: str, body: str, priority: str = "default") -> None:
    """Notifică pe Telegram (canal primar, WP1b). ntfy rămâne doar fallback dacă
    gateway-ul Telegram nu e configurat — sau dacă nu există un event loop activ
    (context sync/thread în care `asyncio.create_task` nu poate rula)."""
    if _tg_gateway:
        try:
            asyncio.get_running_loop()
            asyncio.create_task(_tg_gateway.send_notification(title, body, priority))
            return
        except RuntimeError:
            pass  # fără loop activ → cade pe ntfy (dacă e configurat)
    _send_ntfy_sync(title, body, priority)


def _status_snapshot() -> StreamingResponse:
    """Instant !status response — no LLM call."""
    try:
        cfg = json.loads(KAGE_CONFIG_PATH.read_text(encoding="utf-8"))
        max_cloud = int(cfg.get("max_cloud_calls_per_day", 20))
    except Exception:
        max_cloud = 20

    total, cloud = _usage_counts_today()
    budget_pct = int(cloud / max_cloud * 100) if max_cloud > 0 else 0
    budget_icon = "🟢" if budget_pct < 80 else ("🟠" if budget_pct < 100 else "🔴")

    hits = _cache_hits
    misses = _cache_misses
    hit_pct = int(hits / (hits + misses) * 100) if (hits + misses) > 0 else 0

    task_count = 0
    try:
        if SCHEDULED_TASKS_FILE.exists():
            tasks = json.loads(SCHEDULED_TASKS_FILE.read_text(encoding="utf-8"))
            task_count = sum(1 for t in tasks if t.get("enabled", True))
    except Exception:
        pass

    svc_checks = [("Ollama", 11434), ("LiteLLM", 4000), ("Orchestrator", 4001)]
    svc_str = "  ".join(f"{n} {'✓' if _port_up(p) else '✗'}" for n, p in svc_checks)

    text = "\n".join([
        "📊 **Orchestrator status**",
        f"  {budget_icon} Budget: {cloud}/{max_cloud} cloud azi ({budget_pct}%)",
        f"  💾 Cache: {hits} hits / {misses} misses ({hit_pct}% hit rate)",
        f"  ⏰ Tasks programate: {task_count}",
        f"  Servicii: {svc_str}",
    ])

    async def generate():
        chunk_size = 40
        for i in range(0, len(text), chunk_size):
            yield f'data: {json.dumps({"choices": [{"delta": {"content": text[i:i+chunk_size]}, "index": 0}]})}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def _instant_sse(text: str) -> StreamingResponse:
    """Răspuns SSE instant cu un text fix (fără LLM). Folosit de comenzile de control."""
    async def generate():
        yield f'data: {json.dumps({"choices": [{"delta": {"content": text}, "index": 0}]})}\n\n'
        yield "data: [DONE]\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")


def _stop_snapshot() -> StreamingResponse:
    """Instant !stop — kill switch: omoară procesele-agent + pauzează scheduler-ul."""
    result = _stop_all()
    text = (
        "🛑 **Kill switch activat**\n"
        f"  Procese-agent oprite: {result['procs_killed']}\n"
        f"  Scheduler: {'pe pauză' if result['scheduler_paused'] else 'indisponibil'}\n"
        "  Reia joburile programate cu `!resume`."
    )
    return _instant_sse(text)


def _log_usage(tier: int, model: str, task_preview: str, duration_ms: Optional[int], agent: Optional[str] = None) -> None:
    try:
        entry = {
            "ts": datetime.datetime.now().isoformat(),
            "tier": tier,
            "model": model,
            "cloud": tier >= 3 or agent is not None,
            "agent": agent,
            "duration_ms": duration_ms,
            "preview": task_preview[:40],
        }
        lock = FileLock(str(USAGE_LOG) + ".lock")
        with lock.acquire(timeout=5):
            with open(USAGE_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        today = datetime.date.today().isoformat()
        if _usage_cache["date"] == today:
            _usage_cache["total"] += 1
            if tier >= 3 or agent is not None:
                _usage_cache["cloud"] += 1
    except Exception as e:
        logger.debug(f"Usage log failed: {e}")


def _write_obsidian_output(task: str, response_text: str, save_path: Optional[str] = None) -> Path:
    """Write response to Obsidian vault. Returns the path written to."""
    try:
        today = datetime.date.today().isoformat()
        now = datetime.datetime.now().strftime("%H:%M")
        if save_path:
            # Custom path: relative to VAULT, create parent dirs
            output_path = VAULT / save_path
            if not output_path.suffix:
                output_path = output_path.with_suffix(".md")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Overwrite or create with frontmatter
            header = f"---\ncreated: {today} {now}\ntask: {task[:80]}\n---\n\n"
            body = response_text if output_path.exists() else header + response_text
            if output_path.exists():
                body = output_path.read_text(encoding="utf-8") + f"\n\n---\n\n## Update {now}\n{response_text}"
            output_path.write_text(body, encoding="utf-8")
        else:
            output_path = VAULT / "AI_Outputs" / f"{today}.md"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            entry = f"\n## {now} — {task[:80]}\n{response_text}\n"
            with open(output_path, "a", encoding="utf-8") as f:
                f.write(entry)
        return output_path
    except Exception as e:
        logger.warning(f"Obsidian write-back failed: {e}")
        return VAULT / "AI_Outputs" / f"{datetime.date.today().isoformat()}.md"


# ── Obsidian context ──────────────────────────────────────────────────────────

def _get_obsidian_context(message: str) -> Optional[str]:
    msg_lower = message.lower()
    if not any(kw in msg_lower for kw in PERSONAL_KEYWORDS):
        return None

    parts: list[str] = []
    for path in [
        VAULT / "profile" / "about-me.md",
        VAULT / "profile" / "stack.md",
        VAULT / "profile" / "goals.md",
    ]:
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))

    project_map = {
        "frigo": VAULT / "projects" / "frigo.md",
        "aumovio": VAULT / "projects" / "aumovio.md",
        "internship": VAULT / "projects" / "aumovio.md",
        "orchestrator": VAULT / "projects" / "ai-orchestrator.md",
        "litellm": VAULT / "projects" / "ai-orchestrator.md",
        "odysseus": VAULT / "projects" / "ai-orchestrator.md",
    }
    seen: set[Path] = set()
    for kw, path in project_map.items():
        if kw in msg_lower and path not in seen and path.exists():
            parts.append(path.read_text(encoding="utf-8"))
            seen.add(path)

    return "\n\n---\n\n".join(parts) if parts else None


# ── System prompt ─────────────────────────────────────────────────────────────

_STEFAN_BASE = (
    "Ești un asistent AI pentru Stefan Sârbu — student CS an 2 la UPT Timișoara, "
    "internship QA Automation la Aumovio (4h/zi). Proiecte: Frigo (Flutter+Firebase), "
    "Aumovio (NFC/automotive testing), iTECify (IDE colaborativ).\n"
    "Răspunde direct, fără ocolișuri. Fără tabele inutile. "
    "Fără întrebări de clarificare când contextul e suficient. "
    "Răspunde în română dacă întrebarea e în română.\n\n"
    "## Capabilități orchestrator\n"
    "Orchestratorul poate salva răspunsuri direct în Obsidian (vault: StefanBrain).\n"
    "Când Stefan cere să salvezi un plan, o analiză sau orice conținut în Obsidian, "
    "informează-l că poate face asta adăugând `!save` sau `!save plans/nume-fisier.md` "
    "la începutul mesajului. Exemplu: `!save plans/orchestrator-features.md fă un plan...`\n"
    "NU spune că nu poți scrie în Obsidian — orchestratorul face asta automat cu !save."
)


async def _compact_messages(messages_out: list, max_messages: int) -> list:
    """Sliding window compaction pentru LiteLLM (tiers 1-2). Protejează system prompt."""
    system_msgs = [m for m in messages_out if m.get("role") == "system"]
    conv_msgs   = [m for m in messages_out if m.get("role") != "system"]

    if len(conv_msgs) <= max_messages:
        return messages_out

    window   = conv_msgs[-max_messages:]
    overflow = conv_msgs[:-max_messages]

    summary_msg: Optional[dict] = None

    if ENABLE_SUMMARIZATION:
        overflow_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {str(m.get('content',''))[:400]}"
            for m in overflow
        )
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{LITELLM_URL}/chat/completions",
                    json={
                        "model": TIER_MODELS[2],
                        "messages": [
                            {"role": "user", "content": (
                                "Rezumă concis în 3-5 puncte esențiale, păstrând fapte și context tehnic:\n"
                                f"<conversatie>{overflow_text}</conversatie>"
                            )}
                        ],
                        "stream": False,
                    },
                    timeout=30,
                )
            summary_text = (
                resp.json().get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            if summary_text:
                summary_msg = {"role": "assistant", "content": f"[Rezumat conversație anterioară]\n{summary_text}"}
        except Exception as e:
            logger.warning(f"[Compaction] summarization failed, using sliding window: {e}")

    result = system_msgs + ([summary_msg] if summary_msg else []) + window
    logger.debug(f"[Compaction] {len(conv_msgs)} → {len(result) - len(system_msgs)} conv msgs (max={max_messages})")
    return result


def _build_system_prompt(tier: int, obs_context: Optional[str], memory_ctx: Optional[str] = None) -> str:
    if tier == 1:
        base = _STEFAN_BASE + "\nFii concis — acesta e un task simplu."
    elif tier == 2:
        base = _STEFAN_BASE + "\nAnaliza profund — acesta e un task complex sau cu context personal."
    else:
        base = (
            _STEFAN_BASE + "\n\n"
            "Hardware: MacBook Pro M5 Pro 48GB. Abonamente: Claude Pro, Gemini Pro (facultate).\n"
            "Stil: delegare execuție > scriere manuală. Corecteaz-l dacă greșește.\n\n"
            "Mod: Autonom — poți executa comenzi Bash, citi și modifica fișiere în proiectele active.\n"
            "Gândește înainte de a executa. Acțiunile cu risc ridicat sunt blocate automat de orchestrator.\n"
            "Dacă o acțiune e blocată, explică ce ai încercat și cere autorizare explicită."
        )

    if obs_context:
        base += f"\n\n## Context personal (Obsidian StefanBrain)\n{obs_context}"
    if memory_ctx:
        base += f"\n\n## Memorie relevantă\n{memory_ctx}"

    return base


def _inject_system_prompt(messages: list, system_prompt: str) -> list:
    out = [m for m in messages if m.get("role") != "system"]
    return [{"role": "system", "content": system_prompt}] + out


def _build_conversation_context(messages: list) -> str:
    turns = [m for m in messages if m.get("role") in ("user", "assistant")]
    prior = turns[:-1] if turns else []
    if not prior:
        return ""
    prior = prior[-6:]  # max 3 pairs
    lines = []
    for m in prior:
        role = "User" if m["role"] == "user" else "Assistant"
        content = str(m.get("content", ""))[:600]
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


# ── Routing: LiteLLM (tiers 1-2) ─────────────────────────────────────────────

async def _route_litellm(
    tier: int,
    messages: list,
    system_prompt: str = "",
    user_message: str = "",
    original_messages: Optional[list] = None,
    save_path: Optional[str] = None,
    badge: Optional[str] = None,
) -> StreamingResponse:
    model = TIER_MODELS[tier]
    _badge = badge or _tier_badge(tier)
    badge_chunk = f"data: {json.dumps({'choices': [{'delta': {'content': _badge}, 'index': 0}]})}\n\n"
    payload = {"model": model, "messages": messages, "stream": True}
    headers = {
        "Authorization": f"Bearer {LITELLM_KEY}",
        "Content-Type": "application/json",
    }

    async def generate():
        done_sent = False
        accumulated: list[str] = []
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{LITELLM_URL}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=httpx.Timeout(connect=5, read=120, write=10, pool=5),
                ) as r:
                    if r.status_code >= 500:
                        raise httpx.HTTPStatusError(
                            f"HTTP {r.status_code}", request=r.request, response=r
                        )
                    yield badge_chunk
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        yield line + "\n\n"
                        if save_path is not None and line.startswith("data:") and line != "data: [DONE]":
                            try:
                                chunk_data = json.loads(line[5:].strip())
                                delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                                if text := delta.get("content"):
                                    accumulated.append(text)
                            except Exception:
                                pass
            if save_path is not None and accumulated and user_message:
                written = _write_obsidian_output(user_message, "".join(accumulated), save_path or None)
                rel = str(written).replace(str(VAULT) + "/", "")
                confirm = f"\n\n*Salvat în Obsidian → {rel}*"
                yield f'data: {json.dumps({"choices": [{"delta": {"content": confirm}, "index": 0}]})}\n\n'
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
            fallback_tier = 3 if tier <= 2 else min(tier + 1, 6)
            logger.warning(f"Tier {tier} → Tier {fallback_tier} ({type(e).__name__})")
            info = f"[Tier {tier} unavailable → escalated to Tier {fallback_tier}]\n\n"
            yield f"data: {json.dumps({'choices': [{'delta': {'content': info}, 'index': 0}]})}\n\n"
            _notify(
                f"⬆️ Escalare tier {tier}→{fallback_tier}",
                f"LiteLLM indisponibil, escalat automat la tier {fallback_tier}.",
            )
            fallback_msgs = original_messages or messages
            async for chunk in _generate_cli_chunks(fallback_tier, system_prompt, user_message, fallback_msgs):
                yield chunk
            done_sent = True
        finally:
            if not done_sent:
                _write_status_idle(tier, str(model))
                yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Routing: CLI (tiers 3-6) ──────────────────────────────────────────────────

async def _route_cli(
    tier: int,
    system_prompt: str,
    user_message: str,
    messages: list,
    save_path: Optional[str] = None,
    badge: Optional[str] = None,
) -> StreamingResponse:
    provider, model = TIER_MODELS[tier]

    conv_ctx = _build_conversation_context(messages)
    if conv_ctx:
        full_prompt = (
            f"{system_prompt}\n\n"
            f"## Conversație anterioară\n{conv_ctx}\n\n"
            f"## Task curent\n{user_message}"
        )
    else:
        full_prompt = f"{system_prompt}\n\nTask: {user_message}"

    if provider == "gemini":
        return await _route_gemini(full_prompt, tier, user_message, save_path=save_path, badge=badge)
    else:
        return await _route_claude_autonomous(tier, model, full_prompt, user_message, save_path=save_path, badge=badge)


async def _route_gemini(
    full_prompt: str,
    tier: int = 4,
    user_message: str = "",
    save_path: Optional[str] = None,
    badge: Optional[str] = None,
) -> StreamingResponse:
    cmd = [GEMINI_CLI, "-p", full_prompt, "--output-format", "json"]
    _badge = badge or _tier_badge(tier)

    async def generate():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            raw = stdout.decode("utf-8", errors="replace").strip()
            try:
                raw = json.loads(raw).get("response", raw)
            except Exception:
                pass
            if not raw:
                raw = f"[Gemini CLI: răspuns gol. stderr: {stderr.decode()[:200]}]"
            full_response = raw
            raw = _badge + raw
            chunk_size = 20
            for i in range(0, len(raw), chunk_size):
                yield f'data: {json.dumps({"choices": [{"delta": {"content": raw[i:i+chunk_size]}, "index": 0}]})}\n\n'
            if save_path is not None and user_message:
                written = _write_obsidian_output(user_message, full_response, save_path or None)
                rel = str(written).replace(str(VAULT) + "/", "")
                confirm = f"\n\n*Salvat în Obsidian → {rel}*"
                yield f'data: {json.dumps({"choices": [{"delta": {"content": confirm}, "index": 0}]})}\n\n'
        except asyncio.TimeoutError:
            yield f'data: {json.dumps({"choices": [{"delta": {"content": "[Gemini timeout 120s]"}, "index": 0}]})}\n\n'
        except Exception as e:
            yield f'data: {json.dumps({"choices": [{"delta": {"content": f"[Gemini error: {e}]"}, "index": 0}]})}\n\n'
        finally:
            _write_status_idle(4, "gemini-pro")
            yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _generate_cli_chunks(
    tier: int,
    system_prompt: str,
    user_message: str,
    messages: list,
):
    """Async generator for CLI fallback (used by LiteLLM fallback chain)."""
    provider, model = TIER_MODELS[tier]
    conv_ctx = _build_conversation_context(messages)
    if conv_ctx:
        full_prompt = (
            f"{system_prompt}\n\n"
            f"## Conversație anterioară\n{conv_ctx}\n\n"
            f"## Task curent\n{user_message}"
        )
    else:
        full_prompt = f"{system_prompt}\n\nTask: {user_message}"

    if provider == "gemini":
        cmd = [GEMINI_CLI, "-p", full_prompt, "--output-format", "json"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            raw = stdout.decode("utf-8", errors="replace").strip()
            try:
                raw = json.loads(raw).get("response", raw)
            except Exception:
                pass
            raw = raw or "[Gemini: răspuns gol]"
            chunk_size = 20
            for i in range(0, len(raw), chunk_size):
                yield f'data: {json.dumps({"choices": [{"delta": {"content": raw[i:i+chunk_size]}, "index": 0}]})}\n\n'
        except Exception as e:
            yield f'data: {json.dumps({"choices": [{"delta": {"content": f"[Fallback gemini error: {e}]"}, "index": 0}]})}\n\n'
        finally:
            _write_status_idle(tier, "gemini-pro")
            yield "data: [DONE]\n\n"
        return

    # Chat (fallback CLI): capability minimă — read-only, fără Bash (WP-G1 / D7).
    cmd = [
        CLAUDE_CLI, "-p", full_prompt,
        "--model", model,
        "--output-format", "stream-json",
        "--verbose",
        *_policy_cli_flags("chat"),
        "--settings", str(RISK_SETTINGS),
    ]
    env = {**os.environ, "ORCHESTRATOR_USER_MSG": user_message}
    queue: asyncio.Queue = asyncio.Queue()

    async def feed():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            while True:
                raw_line = await proc.stdout.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            await queue.put(text)
        except Exception as e:
            await queue.put(f"[Fallback CLI error: {e}]")
        finally:
            await queue.put(None)

    try:
        asyncio.create_task(feed())
        deadline = asyncio.get_event_loop().time() + 120
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            text = await asyncio.wait_for(queue.get(), timeout=remaining)
            if text is None:
                break
            yield f'data: {json.dumps({"choices": [{"delta": {"content": text}, "index": 0}]})}\n\n'
    except asyncio.TimeoutError:
        yield f'data: {json.dumps({"choices": [{"delta": {"content": "[Fallback timeout]"}, "index": 0}]})}\n\n'
    finally:
        _write_status_idle(tier, model or "unknown")
        yield "data: [DONE]\n\n"


async def _route_claude_autonomous(
    tier: int,
    model: str,
    full_prompt: str,
    user_message: str,
    save_path: Optional[str] = None,
    badge: Optional[str] = None,
) -> StreamingResponse:
    """Run Claude CLI in autonomous mode with PreToolUse risk gating."""
    # Chat T3+ = răspuns conversațional, nu agent. Capability minimă — read-only,
    # fără Bash/Write/Edit (WP-G1 / D7). Pentru execuție reală: !run/!sysrun.
    cmd = [
        CLAUDE_CLI, "-p", full_prompt,
        "--model", model,
        "--output-format", "stream-json",
        "--verbose",
        *_policy_cli_flags("chat"),
        "--settings", str(RISK_SETTINGS),
    ]
    env = {**os.environ, "ORCHESTRATOR_USER_MSG": user_message}
    _badge = badge or _tier_badge(tier)

    async def generate():
        final_text = ""
        has_streamed = False
        badge_sent = False
        proc = None
        accumulated: list[str] = []
        queue: asyncio.Queue = asyncio.Queue()

        async def feed_queue() -> None:
            nonlocal final_text
            try:
                while True:
                    raw_line = await proc.stdout.readline()
                    if not raw_line:
                        break
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                await queue.put(text)
                    elif event.get("type") == "result":
                        final_text = event.get("result", "")
            finally:
                await queue.put(None)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            _register_proc(proc)

            reader = asyncio.create_task(feed_queue())
            deadline = asyncio.get_event_loop().time() + 120

            try:
                while True:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    text = await asyncio.wait_for(queue.get(), timeout=remaining)
                    if text is None:
                        break
                    has_streamed = True
                    if not badge_sent:
                        badge_sent = True
                        yield f'data: {json.dumps({"choices": [{"delta": {"content": _badge}, "index": 0}]})}\n\n'
                    accumulated.append(text)
                    yield f'data: {json.dumps({"choices": [{"delta": {"content": text}, "index": 0}]})}\n\n'
            except asyncio.TimeoutError:
                reader.cancel()
                if proc:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        proc.kill()
                yield f'data: {json.dumps({"choices": [{"delta": {"content": "[Timeout 120s — task oprit]"}, "index": 0}]})}\n\n'
                return

            if not has_streamed:
                if final_text:
                    accumulated.append(final_text)
                    yield f'data: {json.dumps({"choices": [{"delta": {"content": _badge}, "index": 0}]})}\n\n'
                    chunk_size = 20
                    for i in range(0, len(final_text), chunk_size):
                        yield f'data: {json.dumps({"choices": [{"delta": {"content": final_text[i:i+chunk_size]}, "index": 0}]})}\n\n'
                else:
                    stderr_data = b""
                    if proc:
                        try:
                            stderr_data = await asyncio.wait_for(proc.stderr.read(), timeout=5)
                        except asyncio.TimeoutError:
                            pass
                    error_msg = f"[Tier {tier}: răspuns gol. {stderr_data.decode('utf-8', errors='replace')[:150]}]"
                    yield f'data: {json.dumps({"choices": [{"delta": {"content": error_msg}, "index": 0}]})}\n\n'

            if save_path is not None and accumulated:
                written = _write_obsidian_output(user_message, "".join(accumulated), save_path or None)
                rel = str(written).replace(str(VAULT) + "/", "")
                confirm = f"\n\n*Salvat în Obsidian → {rel}*"
                yield f'data: {json.dumps({"choices": [{"delta": {"content": confirm}, "index": 0}]})}\n\n'

        except Exception as e:
            logger.error(f"Claude autonomous error (tier {tier}): {e}")
            yield f'data: {json.dumps({"choices": [{"delta": {"content": f"[Eroare CLI tier {tier}: {e}]"}, "index": 0}]})}\n\n'
        finally:
            _unregister_proc(proc)
            _write_status_idle(tier, model or "gemini-pro")
            yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Status widget state ───────────────────────────────────────────────────────

def _write_status(active: bool, tier: int, model: str, task: Optional[str], obsidian: bool) -> None:
    try:
        payload = {
            "active": active,
            "tier": tier,
            "model": model,
            "task_preview": task,
            "obsidian": obsidian,
            "started_at": datetime.datetime.now().isoformat() if active else None,
            "last_updated": datetime.datetime.now().isoformat(),
        }
        lock = FileLock(str(STATUS_FILE) + ".lock")
        with lock.acquire(timeout=2):
            STATUS_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Status write failed: {e}")


def _write_status_idle(tier: int, model: str) -> None:
    try:
        lock = FileLock(str(STATUS_FILE) + ".lock")
        with lock.acquire(timeout=2):
            existing: dict = {}
            if STATUS_FILE.exists():
                try:
                    existing = json.loads(STATUS_FILE.read_text())
                except Exception:
                    pass

            # Compute duration for usage log
            duration_ms: Optional[int] = None
            started_at_str = existing.get("started_at")
            if started_at_str:
                try:
                    started = datetime.datetime.fromisoformat(started_at_str)
                    duration_ms = int((datetime.datetime.now() - started).total_seconds() * 1000)
                except Exception:
                    pass

            _log_usage(tier, model, existing.get("task_preview") or "", duration_ms)

            existing.update({
                "active": False,
                "task_preview": None,
                "started_at": None,
                "last_updated": datetime.datetime.now().isoformat(),
                "tier": tier,
                "model": model,
            })
            STATUS_FILE.write_text(json.dumps(existing), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Status idle write failed: {e}")


# ── Vault logging ─────────────────────────────────────────────────────────────

def _log_to_vault(
    tier: int,
    message: str,
    used_obsidian: bool,
    confidence: float = 0.5,
    forced: bool = False,
) -> None:
    try:
        today = datetime.date.today().isoformat()
        model_name = TIER_MODELS[tier] if tier <= 2 else TIER_MODELS[tier][1] or "gemini-pro"
        log_path = VAULT / "logs" / f"{today}.md"

        entry = (
            f"\n## {datetime.datetime.now().strftime('%H:%M')}\n"
            f"Task: {message[:80]}\n"
            f"Level: {tier} | Model: {model_name} | Obsidian: {'da' if used_obsidian else 'nu'} | "
            f"Confidence: {confidence:.2f} | Forced: {'da' if forced else 'nu'}\n"
        )
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)

        patterns_path = VAULT / "routing" / "patterns.md"
        if patterns_path.exists():
            row = (
                f"| {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} "
                f"| {tier} | {model_name} "
                f"| {'da' if used_obsidian else 'nu'} "
                f"| {confidence:.2f} "
                f"| {message[:60]} |\n"
            )
            with open(patterns_path, "a", encoding="utf-8") as f:
                f.write(row)
    except Exception as e:
        logger.warning(f"Vault logging failed: {e}")
