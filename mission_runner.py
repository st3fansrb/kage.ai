"""Mission Runner — logica pură pentru modul handoff (WP11).

„Îi dau planul și lucrează singur": automatizarea buclei pe care Stefan o face azi
manual (plan cu WP-uri → sesiune per WP → verifică criterii → următorul).

Acest modul conține DOAR partea pură, fără I/O — parsarea unui `mission.md` în
pachete de lucru, extragerea criteriilor de acceptare verificabile (comenzi shell),
marcarea unui WP ca ✅ în textul markdown, și parsarea orei de reset dintr-un mesaj
de rate-limit Claude Code. Orchestrarea cu stare (DB, AgentRunner, Telegram,
scheduler, git) trăiește în `orchestrator.py`, secțiunea Mission Runner — la fel ca
`_briefing_gather` (pur) vs restul briefing-ului.

Format `missions/<slug>/mission.md` (același ca acest handoff, deja validat pe WP1–WP4):

    # Mission: <titlu>

    <intro opțional>

    ## <titlu WP>
    - pas 1
    - pas 2
    ### Acceptare
    - `pytest -q`            ← criteriu VERIFICABIL (comandă shell între backtick-uri)
    - criteriu în text liber ← informativ (agentul se auto-verifică)

    ## <titlu WP2>
    ...
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class WorkPackage:
    """Un pachet de lucru dintr-o misiune."""
    title: str
    body: str                              # pașii (text markdown, fără secțiunea Acceptare)
    criteria: List[str] = field(default_factory=list)       # toate criteriile (text)
    shell_checks: List[str] = field(default_factory=list)   # subsetul rulabil (comenzi shell)
    done: bool = False                     # deja marcat ✅ în md la parsare


@dataclass
class Mission:
    title: str
    wps: List[WorkPackage] = field(default_factory=list)


# Un criteriu e „verificabil" dacă e în întregime o comandă shell între backtick-uri:
#   - `pytest -q`
#   - `curl -s localhost:4001/health`
_SHELL_CRITERION_RE = re.compile(r"^`([^`]+)`$")
# Sub-antetele care încep secțiunea de criterii de acceptare.
_ACCEPT_HEADER_RE = re.compile(r"^#{3,}\s*(acceptare|acceptance|criterii|criteria)\b", re.IGNORECASE)
_WP_HEADER_RE = re.compile(r"^##\s+(?!#)(.*)$")     # `## titlu` (nu `### `)
_TITLE_RE = re.compile(r"^#\s+(?!#)(.*)$")          # `# titlu`
_DONE_SUFFIX_RE = re.compile(r"\s*✅\s*(\([^)]*\))?\s*$")   # „✅" sau „✅ (data)" la coadă


def _looks_like_shell(text: str) -> Optional[str]:
    """Dacă `text` (conținutul unui bullet) e o singură comandă shell între backtick-uri,
    întoarce comanda; altfel None. Ex.: '`pytest -q`' → 'pytest -q'."""
    m = _SHELL_CRITERION_RE.match(text.strip())
    if not m:
        return None
    cmd = m.group(1).strip()
    return cmd or None


def _strip_bullet(line: str) -> str:
    """Scoate marcajul de listă („- ", „* ", „1. ") de la începutul unei linii."""
    return re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", line).strip()


def parse_mission(text: str) -> Mission:
    """Parsează un `mission.md` în `Mission` (titlu + listă de WP-uri).

    - Primul `# ` = titlul misiunii (fără prefixul „Mission:").
    - Fiecare `## ` începe un WP; un `✅` în antet = WP deja terminat (la reluare).
    - Corpul WP-ului = tot până la următorul `## `, MAI PUȚIN secțiunea `### Acceptare`.
    - Sub `### Acceptare`, fiecare bullet e un criteriu; cele integral între backtick-uri
      devin `shell_checks` (rulabile), restul rămân informative.
    """
    lines = text.splitlines()
    title = ""
    wps: List[WorkPackage] = []

    cur: Optional[WorkPackage] = None
    body_lines: List[str] = []
    in_accept = False

    def _flush():
        nonlocal cur, body_lines, in_accept
        if cur is not None:
            cur.body = "\n".join(body_lines).strip()
            wps.append(cur)
        cur, body_lines, in_accept = None, [], False

    for line in lines:
        m_title = _TITLE_RE.match(line)
        if m_title and cur is None and not title:
            title = re.sub(r"^mission:\s*", "", m_title.group(1).strip(), flags=re.IGNORECASE)
            continue

        m_wp = _WP_HEADER_RE.match(line)
        if m_wp:
            _flush()
            raw = m_wp.group(1).strip()
            done = bool(_DONE_SUFFIX_RE.search(raw))
            clean = _DONE_SUFFIX_RE.sub("", raw).strip()
            cur = WorkPackage(title=clean, body="", done=done)
            continue

        if cur is None:
            continue  # preambul înainte de primul WP — ignorat

        if _ACCEPT_HEADER_RE.match(line):
            in_accept = True
            continue

        if in_accept:
            stripped = line.strip()
            if not stripped:
                continue
            crit = _strip_bullet(line)
            if not crit:
                continue
            cur.criteria.append(crit)
            sh = _looks_like_shell(crit)
            if sh:
                cur.shell_checks.append(sh)
        else:
            body_lines.append(line)

    _flush()
    return Mission(title=title or "(fără titlu)", wps=wps)


def mark_wp_done(md_text: str, wp_index: int, stamp: str = "") -> str:
    """Adaugă „✅" (opțional „✅ (stamp)") în antetul WP-ului `wp_index` (0-based) din
    textul markdown. Idempotent: dacă e deja marcat, nu dublează. Checklist viu."""
    lines = md_text.splitlines(keepends=True)
    idx = -1
    suffix = f" ✅ ({stamp})" if stamp else " ✅"
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if _WP_HEADER_RE.match(stripped):
            idx += 1
            if idx == wp_index:
                if _DONE_SUFFIX_RE.search(stripped):
                    return md_text  # deja marcat
                newline = "\n" if line.endswith("\n") else ""
                lines[i] = stripped + suffix + newline
                break
    return "".join(lines)


# ── Parsare rate-limit (auto-resume, WP11 §4) ────────────────────────────────
_CLOCK_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
_RELATIVE_RE = re.compile(r"\bin\s+(\d+)\s*(second|minute|hour)s?\b", re.IGNORECASE)


def parse_rate_limit_reset(text: str, now=None) -> Optional[int]:
    """Din mesajul de rate-limit al Claude Code, deduce câte SECUNDE până la reset.

    Recunoaște: „try again at 6pm" / „resets at 18:00" / „in 45 minutes" / „in 2 hours".
    Întoarce secunde (>0) sau None dacă nu găsește nimic → caller-ul cade pe retry la
    15 min (WP11 §4). `now` injectabil pentru teste."""
    import datetime as _dt
    if now is None:
        now = _dt.datetime.now()
    low = text.lower()

    # Relativ: „in N minutes/hours"
    mrel = _RELATIVE_RE.search(low)
    if mrel:
        n = int(mrel.group(1))
        unit = mrel.group(2)
        secs = n * {"second": 1, "minute": 60, "hour": 3600}[unit]
        return max(secs, 1)

    # Absolut: ancorat pe cuvinte de reset ca să nu prindem cifre aleatorii.
    anchor = re.search(r"(again at|resets? at|reset at|available at|try again|limit.*reset)", low)
    search_from = low[anchor.start():] if anchor else (low if ("reset" in low or "limit" in low) else "")
    if not search_from:
        return None
    mclock = _CLOCK_RE.search(search_from)
    if not mclock:
        return None
    hour = int(mclock.group(1))
    minute = int(mclock.group(2) or 0)
    ampm = (mclock.group(3) or "").lower()
    if hour > 23 or minute > 59:
        return None
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += _dt.timedelta(days=1)   # ora a trecut azi → mâine
    return int((target - now).total_seconds())
