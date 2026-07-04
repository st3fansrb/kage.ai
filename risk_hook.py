#!/usr/bin/env python3
"""
PreToolUse hook for the AI Orchestration System v2.
Receives tool call details via stdin, evaluates 3-axis risk matrix,
returns allow/deny decision to Claude Code.

Axes:
  1. Reversibilitate — se poate anula?
  2. Instrucție explicită — userul a cerut asta explicit?
  3. Conținut — ce fișiere/comenzi sunt implicate?
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
import datetime
import urllib.parse
import urllib.request
from pathlib import Path

def _load_vault() -> Path:
    root = Path(__file__).parent
    for name in ("kage_config.json", "ntfy_config.json"):
        p = root / name
        if p.exists():
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
                return Path(cfg.get("vault_path", str(Path.home() / "Documents" / "KageVault"))).expanduser()
            except Exception:
                pass
    return Path.home() / "Documents" / "KageVault"

VAULT           = _load_vault()
NTFY_CONFIG     = next(
    (Path(__file__).parent / n for n in ("kage_config.json", "ntfy_config.json") if (Path(__file__).parent / n).exists()),
    Path(__file__).parent / "kage_config.json",
)
ORCHESTRATOR_URL = "http://localhost:4001"

# ── Never list patterns (Bash commands) ───────────────────────────────────────
NEVER_CMD_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+(-[rRfF]{1,3}\s+)?~/",        "rm în home directory"),
    (r"rm\s+(-[rRfF]{1,3}\s+)?/(?!tmp/)",  "rm în root (non-/tmp)"),
    (r"rm\s+(-[rRfF]{1,3}\s+)?\.\./",      "rm în parent directory"),
    (r"\bsudo\b",                            "comandă sudo"),
    (r"brew\s+install\b",                    "instalare globală Homebrew"),
    (r"curl[^|#\n]*\|\s*(bash|sh)\b",        "curl piped to shell"),
    (r"wget[^|#\n]*\|\s*(bash|sh)\b",        "wget piped to shell"),
    (r"git\s+push\s+(--force|-f)\b",         "git force push"),
    (r"git\s+reset\s+--hard\s+.*\b(main|master)\b", "git reset --hard pe main"),
    # find -exec with destructive commands
    (r"\bfind\b.*\b-exec\s+(rm|unlink|shred)\b",      "find -exec cu ștergere"),
    (r"\bfind\b.*-delete\b",                            "find -delete"),
    # eval with remote content
    (r"\beval\b.*\$\(.*\b(curl|wget)\b",               "eval cu curl/wget"),
    (r"\beval\b\s+['\"].*\b(curl|wget)\b",             "eval cu curl/wget (quoted)"),
    # download-and-execute patterns
    (r"(curl|wget)[^;&#\n]*>\s*/\S+\.(sh|py|bash|zsh)\s*[;&\n].*\b(bash|sh|python3?|zsh)\b",
                                                         "download și execute script"),
    # Python inline destructive ops
    (r"python3?\s+-c\b.*\b(os\.remove|os\.rmdir|os\.unlink|shutil\.rmtree|shutil\.move)\s*\(",
                                                         "Python destructiv inline"),
]

NEVER_PATH_PREFIXES: list[str] = [
    "/Library/", "/System/", "/usr/", "/etc/",
    "/sbin/", "/private/etc/",
    "/.ssh/", "/.gnupg/",
]

NEVER_FILENAMES: list[str] = [
    ".env", ".env.local", ".env.production",
    "id_rsa", "id_ed25519", "id_ecdsa",
    "credentials.json", "keystore.jks",
    "google-services.json",
]

# ── High risk patterns ─────────────────────────────────────────────────────────
HIGH_CMD_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+.*\bsrc/",           "ștergere fișiere sursă src/"),
    (r"rm\s+.*\blib/",           "ștergere lib/"),
    (r"git\s+reset\b",           "git reset"),
    (r"git\s+clean\b",           "git clean"),
    (r"git\s+rebase\b",          "git rebase"),
    (r"git\s+(commit\s+)?--amend\b", "git commit --amend"),
    (r"\btruncate\b",            "truncate fișier"),
    # WP2: `>\s*/dev/null` scos — clasifica greșit `2>/dev/null` (redirect benign)
    # ca High (D6).
]

HIGH_PATH_PARTS: list[str] = ["/src/", "/lib/", "/core/", "/auth/"]

# ── Medium risk patterns ───────────────────────────────────────────────────────
MEDIUM_CMD_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+.*\b(dist|build|\.cache|\.next|node_modules)/", "ștergere foldere generate"),
    (r"flutter\s+clean\b",  "flutter clean"),
    (r"npm\s+(run\s+clean|clean)\b", "npm clean"),
]

EXPLICIT_KEYWORDS: list[str] = [
    "șterge", "delete", "remove", "clean", "rebuild", "recreate",
    "sterge", "elimina", "elimină", "curăță", "curata",
]


def _has_explicit_keyword(user_message: str) -> bool:
    """Axa 2 (instrucție explicită): userul a cerut explicit o operație distructivă?
    Dacă da, un risc High se coboară la Medium (aprobare) în loc de deny direct."""
    msg = (user_message or "").lower()
    return any(kw in msg for kw in EXPLICIT_KEYWORDS)


def _load_ntfy_config() -> dict:
    try:
        return json.loads(NTFY_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _get_api_token() -> str:
    return _load_ntfy_config().get("api_token", "")


def _send_ntfy(
    title: str,
    body: str,
    cfg: dict,
    priority: str = "default",
    actions: list[str] | None = None,
) -> None:
    ntfy_url = cfg.get("ntfy_url", "").rstrip("/")
    topic    = cfg.get("ntfy_topic", "")
    if not ntfy_url or not topic or "CHANGEME" in topic:
        return

    url = f"{ntfy_url}/{topic}"
    headers: dict[str, str] = {
        "Title":    urllib.parse.quote(title),
        "Priority": priority,
        "Content-Type": "text/plain; charset=utf-8",
    }
    if actions:
        headers["Actions"] = "; ".join(actions)

    try:
        req = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _wait_for_confirm(request_id: str, timeout_secs: int) -> str:
    """Poll orchestrator /risk/status/{id} via HTTP. Returns 'confirm', 'block', or 'timeout'."""
    token = _get_api_token()
    status_url = f"{ORCHESTRATOR_URL}/risk/status/{request_id}"
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        try:
            req = urllib.request.Request(status_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("resolved"):
                    return data.get("action", "block")
        except Exception:
            pass
        time.sleep(2)
    return "timeout"


def evaluate_risk(
    tool_name: str,
    tool_input: dict,
    user_message: str,
) -> tuple[str, str]:
    """Return (risk_level, reason). risk_level in Never/High/Medium/Low/Safe."""
    cmd: str = tool_input.get("command", "")
    file_path: str = (
        tool_input.get("file_path")
        or tool_input.get("path")
        or ""
    )

    # ── Bash command evaluation ───────────────────────────────────────────────
    if tool_name == "Bash" and cmd:
        cmd_expanded = cmd.replace("~/", str(Path.home()) + "/")

        for pattern, reason in NEVER_CMD_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return "Never", f"Comandă interzisă: {reason}"

        home = str(Path.home())
        for prefix in NEVER_PATH_PREFIXES:
            expanded_prefix = prefix.replace("~/", home + "/")
            if prefix in cmd or prefix in cmd_expanded or expanded_prefix in cmd:
                return "Never", f"Cale interzisă în comandă: {prefix}"

        cmd_lower = cmd.lower()
        for fname in NEVER_FILENAMES:
            if fname in cmd_lower:
                return "Never", f"Fișier sensibil în comandă: {fname}"

        for pattern, reason in HIGH_CMD_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                if _has_explicit_keyword(user_message):
                    return "Medium", f"{reason} (downgrade High→Medium: instrucție explicită)"
                return "High", f"Risc ridicat: {reason}"

        for pattern, reason in MEDIUM_CMD_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return "Medium", reason

    # ── Write / Edit path evaluation ─────────────────────────────────────────
    if tool_name in ("Write", "Edit") and file_path:
        home = str(Path.home())
        fp_expanded = file_path.replace("~/", home + "/")

        for prefix in NEVER_PATH_PREFIXES:
            expanded_prefix = prefix.replace("~/", home + "/")
            if fp_expanded.startswith(expanded_prefix) or prefix in file_path:
                return "Never", f"Scriere în cale interzisă: {prefix}"

        basename = os.path.basename(file_path)
        for fname in NEVER_FILENAMES:
            if basename == fname or basename.endswith(fname):
                return "Never", f"Scriere în fișier sensibil: {fname}"

        for part in HIGH_PATH_PARTS:
            if part in file_path:
                if _has_explicit_keyword(user_message):
                    return "Medium", f"Modificare fișiere active: {part} (downgrade: instrucție explicită)"
                return "High", f"Modificare fișiere active: {part}"

    return "Safe", "Operație sigură"


def log_decision(
    tool_name: str,
    tool_input: dict,
    risk_level: str,
    reason: str,
    decision: str,
) -> None:
    try:
        today = datetime.date.today().isoformat()
        log_path = VAULT / "logs" / f"{today}.md"
        entry = (
            f"\n### Risk [{datetime.datetime.now().strftime('%H:%M:%S')}]\n"
            f"Tool: `{tool_name}` | Risk: **{risk_level}** | Decision: {decision}\n"
            f"Input: `{json.dumps(tool_input)[:120]}`\n"
            f"Reason: {reason}\n"
        )
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass


def _register_with_orchestrator(
    req_id: str, tool_name: str, tool_preview: str, reason: str
) -> None:
    """Înregistrează cererea de aprobare la orchestrator (`/risk/register/{id}`),
    care emite butoanele inline Telegram și o expune în kage.html. Best-effort:
    orice eroare de rețea e ignorată (Telegram poate fi jos)."""
    api_token = _get_api_token()
    try:
        reg_data = json.dumps({
            "tool_name": tool_name,
            "cmd": tool_preview,
            "reason": reason,
            "time": datetime.datetime.now().strftime("%H:%M"),
        }).encode("utf-8")
        reg_headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_token:
            reg_headers["Authorization"] = f"Bearer {api_token}"
        reg_req = urllib.request.Request(
            f"{ORCHESTRATOR_URL}/risk/register/{req_id}",
            data=reg_data,
            headers=reg_headers,
            method="POST",
        )
        urllib.request.urlopen(reg_req, timeout=3)
    except Exception:
        pass


def main() -> None:
    raw = sys.stdin.read()
    try:
        hook_input = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }))
        return

    tool_name: str  = hook_input.get("tool_name", "")
    tool_input: dict = hook_input.get("tool_input", {})
    user_message: str = os.environ.get("ORCHESTRATOR_USER_MSG", "")

    risk_level, reason = evaluate_risk(tool_name, tool_input, user_message)

    cfg              = _load_ntfy_config()
    autonomous_mode  = cfg.get("autonomous_mode", False)
    confirm_timeout  = cfg.get("confirm_timeout_secs", 300)

    tool_preview = json.dumps(tool_input)[:100]

    if risk_level == "Never":
        decision = "deny"
        _send_ntfy(
            title="🚫 BLOCAT [Never] — orchestrator",
            body=f"Tool: {tool_name}\nMotiv: {reason}\nInput: {tool_preview}",
            cfg=cfg,
            priority="high",
        )

    elif risk_level == "High" or (risk_level == "Medium" and autonomous_mode):
        # WP2: High intră în fluxul de aprobare (nu mai e deny direct); Medium doar
        # în autonomous_mode. Calea primară de aprobare = butoanele inline Telegram
        # (emise de orchestrator la /risk/register). Timeout → deny (fail-closed).
        req_id = uuid.uuid4().hex[:12]
        _register_with_orchestrator(req_id, tool_name, tool_preview, reason)

        # Fallback ntfy (fără butoane) — no-op dacă ntfy nu mai e configurat.
        _send_ntfy(
            title=f"🔶 Confirmare necesară [{risk_level}] — orchestrator",
            body=(
                f"Tool: {tool_name}\n"
                f"Motiv: {reason}\n"
                f"Input: {tool_preview}\n"
                f"Timeout: {confirm_timeout}s"
            ),
            cfg=cfg,
            priority="high",
        )

        response = _wait_for_confirm(req_id, confirm_timeout)
        decision = "allow" if response == "confirm" else "deny"

    else:
        decision = "allow"
        if risk_level == "Medium":
            _send_ntfy(
                title="🟡 Acțiune Medium — permisă",
                body=f"Tool: {tool_name}\nMotiv: {reason}\nInput: {tool_preview}",
                cfg=cfg,
                priority="default",
            )

    log_decision(tool_name, tool_input, risk_level, reason, decision)

    output: dict = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": f"[{risk_level}] {reason}",
        }
    }

    if decision == "deny":
        preview = json.dumps(tool_input)[:80]
        output["systemMessage"] = (
            f"[BLOCAT] `{tool_name}({preview})` oprită (risc: {risk_level}): {reason}. "
            f"Spune-mi explicit în chat dacă vrei să autorizezi această acțiune."
        )

    print(json.dumps(output))


if __name__ == "__main__":
    main()
