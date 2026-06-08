"""
AI Orchestration System v2 — Faza 4: Status Widget
Menubar app (rumps) care afișează starea orchestratorului în timp real.

Menubar:  "○ AI" (idle)  |  "● T3·haiku" (activ)
Click:    Status compact + preview task + status servicii
Detalii:  Submeniu cu tier/model/obsidian/timp + ultimele 3 loguri
"""
from __future__ import annotations

import datetime
import json
import re
import socket
import subprocess
import time
from pathlib import Path

import rumps

DIR         = Path(__file__).parent
STATUS_FILE = DIR / "status.json"
USAGE_LOG   = DIR / "usage_log.jsonl"
def _load_vault_path() -> Path:
    for name in ("kage_config.json", "ntfy_config.json"):
        p = DIR / name
        if p.exists():
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
                return Path(cfg.get("vault_path", str(Path.home() / "Documents" / "KageVault"))).expanduser()
            except Exception:
                pass
    return Path.home() / "Documents" / "KageVault"

VAULT       = _load_vault_path()
START_ALL   = DIR / "start_all.sh"
STOP_ALL    = DIR / "stop_all.sh"

SERVICES = [
    ("Ollama",       11434),
    ("LiteLLM",       4000),
    ("Orchestrator",  4001),
]

TIER_SHORT = {1: "qwen8b", 2: "qwen35b", 3: "haiku", 4: "gemini", 5: "sonnet", 6: "opus"}


def _port_up(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


ACTIVE_HOLD_SECS = 6  # keep showing active for at least this long after last active signal


class OrchestratorWidget(rumps.App):
    def __init__(self):
        super().__init__("🟡 Kage", quit_button=None)
        self._last_active_at: float = 0.0
        self._pending_count: int = 0

        # ── Overview ──────────────────────────────────────────────────────────
        self._status_item = rumps.MenuItem("Idle")
        self._task_item   = rumps.MenuItem("")
        self._pending_item = rumps.MenuItem("⚠️ Aprobări necesare", callback=self._open_chat)
        self._pending_item.visible = False

        # ── Servicii submeniu ─────────────────────────────────────────────────
        self._svc_items = {name: rumps.MenuItem(f"  {name}") for name, _ in SERVICES}
        svc_menu = rumps.MenuItem("Servicii ▶")
        for item in self._svc_items.values():
            svc_menu.add(item)

        # ── Detalii submeniu ──────────────────────────────────────────────────
        self._tier_item  = rumps.MenuItem("  Tier: —")
        self._model_item = rumps.MenuItem("  Model: —")
        self._obs_item   = rumps.MenuItem("  Obsidian: —")
        self._time_item  = rumps.MenuItem("  Pornit: —")
        self._usage_item = rumps.MenuItem("  Azi: — req")
        self._log_items  = [rumps.MenuItem("  —") for _ in range(3)]

        details = rumps.MenuItem("Detalii ▶")
        for item in [self._tier_item, self._model_item, self._obs_item, self._time_item, self._usage_item, None] + self._log_items:
            details.add(item)

        # ── Control ───────────────────────────────────────────────────────────
        self._chat_btn      = rumps.MenuItem("💬  Deschide Kage (Chat)", callback=self._open_chat)
        self._dashboard_btn = rumps.MenuItem("📊  Kage Dashboard",        callback=self._open_dashboard)
        self._start_btn     = rumps.MenuItem("▶  Pornește sistemul",      callback=self._start_system)
        self._stop_btn      = rumps.MenuItem("■  Oprește sistemul",       callback=self._stop_system)

        self.menu = [
            self._status_item,
            self._task_item,
            self._pending_item,
            None,
            svc_menu,
            None,
            details,
            None,
            self._chat_btn,
            self._dashboard_btn,
            None,
            self._start_btn,
            self._stop_btn,
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]

    # ── Timer ─────────────────────────────────────────────────────────────────

    @rumps.timer(1)
    def refresh(self, _):
        self._update_status()
        self._update_pending()
        self._update_services()
        self._update_logs()
        self._update_usage()

    def _update_pending(self):
        """Poll /api/pending to see if Kage is waiting for approvals."""
        try:
            # We use urllib to avoid adding extra dependencies to the widget env
            import urllib.request
            token_path = DIR / "ntfy_config.json"
            token = ""
            if token_path.exists():
                token = json.loads(token_path.read_text()).get("api_token", "")
            
            req = urllib.request.Request("http://127.0.0.1:4001/api/pending")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            
            with urllib.request.urlopen(req, timeout=1) as resp:
                items = json.loads(resp.read().decode())
                self._pending_count = len(items)
            
            if self._pending_count > 0:
                self._pending_item.title = f"⚠️ {self._pending_count} acțiuni necesită aprobare"
                self._pending_item.visible = True
            else:
                self._pending_item.visible = False
        except Exception:
            self._pending_count = 0
            self._pending_item.visible = False

    # ── Status orchestrator ───────────────────────────────────────────────────

    def _update_status(self):
        try:
            data: dict = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

        tier  = data.get("tier")
        model = data.get("model", "—")

        orchestrator_up = _port_up(4001)

        if not orchestrator_up:
            self.title = "🔴 Kage"
            self._status_item.title = "Offline — sistem oprit"
            self._task_item.title = ""
            updated = data.get("last_updated") or ""
            self._time_item.title = f"  Ultima activitate: {updated[11:19] or '—'}"
            return

        # Debounce: keep showing active for ACTIVE_HOLD_SECS after last active signal
        if data.get("active"):
            self._last_active_at = time.time()

        is_active = data.get("active") or (time.time() - self._last_active_at < ACTIVE_HOLD_SECS)

        if is_active:
            short = TIER_SHORT.get(tier, f"t{tier}") if tier else "?"
            self.title = f"🟢 T{tier}·{short}"
            self._status_item.title = f"Activ — Tier {tier}"
            task = data.get("task_preview") or ""
            self._task_item.title = f"  \"{task[:55]}\"" if task else ""
            self._tier_item.title  = f"  Tier: {tier}"
            self._model_item.title = f"  Model: {model}"
            self._obs_item.title   = f"  Obsidian: {'da' if data.get('obsidian') else 'nu'}"
            started = data.get("started_at") or ""
            self._time_item.title  = f"  Pornit: {started[11:19] or '—'}"
        else:
            if self._pending_count > 0:
                self.title = f"🔶 Kage [{self._pending_count}]"
            else:
                self.title = "🟡 Kage"
            self._status_item.title = "Idle"
            self._task_item.title = f"  Ultimul: T{tier} · {model}" if (model and model != "—") else ""
            updated = data.get("last_updated") or ""
            self._time_item.title = f"  Ultima activitate: {updated[11:19] or '—'}"

    # ── Servicii ──────────────────────────────────────────────────────────────

    def _update_services(self):
        for name, port in SERVICES:
            up = _port_up(port)
            self._svc_items[name].title = f"  {'✓' if up else '✗'}  {name:<13} :{port}"

    # ── Loguri ────────────────────────────────────────────────────────────────

    def _update_logs(self):
        try:
            today = datetime.date.today().isoformat()
            log_path = VAULT / "logs" / f"{today}.md"
            if not log_path.exists():
                for item in self._log_items:
                    item.title = "  (niciun log astăzi)"
                return

            entries: list[str] = []
            current_time = ""
            for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
                m_hdr = re.match(r"## (\d{2}:\d{2})", line)
                if m_hdr:
                    current_time = m_hdr.group(1)
                    continue
                m_lvl = re.match(
                    r"Level:\s*(\d+)\s*\|\s*Model:\s*(\S+)\s*\|\s*Obsidian:\s*(\S+)", line
                )
                if m_lvl:
                    t, mdl, obs = m_lvl.group(1), m_lvl.group(2), m_lvl.group(3)
                    entries.append(f"  {current_time}  T{t} · {mdl}  obs:{obs}")
                    if len(entries) == 3:
                        break

            for i, item in enumerate(self._log_items):
                item.title = entries[i] if i < len(entries) else "  —"

        except Exception:
            pass

    # ── Usage ─────────────────────────────────────────────────────────────────

    def _update_usage(self):
        try:
            today = datetime.date.today().isoformat()
            total = cloud = 0
            max_cloud = 20
            try:
                ntfy_path = DIR / "ntfy_config.json"
                if ntfy_path.exists():
                    cfg = json.loads(ntfy_path.read_text(encoding="utf-8"))
                    max_cloud = int(cfg.get("max_cloud_calls_per_day", 20))
            except Exception:
                pass
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
            local = total - cloud
            budget_pct = int(cloud / max_cloud * 100) if max_cloud > 0 else 0
            warn = "⚠️ " if budget_pct >= 80 else ""
            self._usage_item.title = f"  {warn}☁ {cloud}/{max_cloud} cloud · {local} local"
        except Exception:
            pass

    # ── Control ───────────────────────────────────────────────────────────────

    def _start_system(self, _):
        subprocess.Popen(["bash", str(START_ALL)], close_fds=True)

    def _stop_system(self, _):
        subprocess.Popen(["bash", str(STOP_ALL)], close_fds=True)

    def _open_chat(self, _):
        subprocess.Popen(["open", "http://localhost:4001/chat"])

    def _open_dashboard(self, _):
        subprocess.Popen(["open", "http://localhost:4001/dashboard"])


if __name__ == "__main__":
    OrchestratorWidget().run()
