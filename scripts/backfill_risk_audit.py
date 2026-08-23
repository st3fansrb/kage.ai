#!/usr/bin/env python3
"""Convertește jurnalul Markdown de decizii de risc în JSONL, pentru ingestie în SIEM.

`risk_hook.log_decision()` scrie de la început blocuri Markdown în vault (pentru citit
de om). Sink-ul structurat (`_audit_event`) a apărut mai târziu, deci prinde doar
evenimentele noi. Scriptul ăsta recuperează istoricul, ca regulile de detecție să aibă
pe ce rula din prima zi, nu doar pe traficul de după.

Format sursă (un bloc per decizie, în VAULT/logs/YYYY-MM-DD.md):

    ### Risk [14:32:07]
    Tool: `Bash` | Risk: **Safe** | Decision: allow
    Input: `{"command": "..."}`
    Reason: Operație sigură

Orele din Markdown sunt locale; ies în UTC, ca sink-ul live.

Utilizare:
    python scripts/backfill_risk_audit.py --dry-run
    python scripts/backfill_risk_audit.py -o .logs/risk_audit.jsonl
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from risk_hook import VAULT, AUDIT_JSONL  # noqa: E402

BLOCK_RE = re.compile(
    r"^### Risk \[(?P<time>\d{2}:\d{2}:\d{2})\]\n"
    r"Tool: `(?P<tool>[^`]*)` \| Risk: \*\*(?P<risk>[^*]*)\*\* \| Decision: (?P<decision>\S+)\n"
    r"Input: `(?P<input>.*?)`\n"
    r"Reason: (?P<reason>.*)$",
    re.MULTILINE,
)


def parse_day(path: Path) -> list[dict]:
    """Extrage evenimentele dintr-un fișier de jurnal. Numele fișierului dă data."""
    try:
        day = datetime.date.fromisoformat(path.stem)
    except ValueError:
        return []  # fișier de jurnal care nu e datat — nu e al nostru

    events = []
    for m in BLOCK_RE.finditer(path.read_text(encoding="utf-8")):
        h, mi, sec = (int(x) for x in m.group("time").split(":"))
        # naive -> local (astimezone() aplică regulile de DST valabile la acea dată) -> UTC
        local = datetime.datetime(day.year, day.month, day.day, h, mi, sec).astimezone()
        events.append({
            "ts": local.astimezone(datetime.timezone.utc)
                  .isoformat(timespec="seconds").replace("+00:00", "Z"),
            "tool": m.group("tool"),
            "risk_level": m.group("risk"),
            "decision": m.group("decision"),
            "reason": m.group("reason").strip(),
            "input_preview": m.group("input")[:200],
            "source": "backfill",
        })
    return events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--output", type=Path, default=AUDIT_JSONL)
    ap.add_argument("--logs-dir", type=Path, default=VAULT / "logs")
    ap.add_argument("--dry-run", action="store_true", help="doar raportează, nu scrie")
    args = ap.parse_args()

    if not args.logs_dir.is_dir():
        print(f"[eroare] {args.logs_dir} nu există", file=sys.stderr)
        return 1

    events: list[dict] = []
    for f in sorted(args.logs_dir.glob("*.md")):
        events.extend(parse_day(f))
    events.sort(key=lambda e: e["ts"])

    by_level: dict[str, int] = {}
    for e in events:
        by_level[e["risk_level"]] = by_level.get(e["risk_level"], 0) + 1
    print(f"{len(events)} evenimente din {len(list(args.logs_dir.glob('*.md')))} fișiere")
    for lvl, n in sorted(by_level.items(), key=lambda kv: -kv[1]):
        print(f"  {lvl:<8} {n}")

    if args.dry_run:
        print("[dry-run] nu s-a scris nimic")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "a", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"[ok] scris în {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
