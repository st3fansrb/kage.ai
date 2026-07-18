#!/usr/bin/env python3
"""Rulează fixture-uri offline ca regression pack pentru Video Intel.

Nu descarcă media și nu cheamă niciun model. Reproduce doar frontiera stabilă
ExtractResult → clasificare/analiză → verdict, cu răspunsuri de model înregistrate.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import video_intel as vi  # noqa: E402

DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "video_intel_replays.json"


async def _replay(case: dict) -> vi.Analysis:
    async def fake_chat(messages: list[dict]) -> str:
        if "Clasifică" in messages[-1]["content"]:
            return json.dumps({"categorie": case["category"]})
        response = case["analysis_response"]
        return response if isinstance(response, str) else json.dumps(response)

    result = vi.ExtractResult(**case["input"])
    return await vi.VideoIntel(fake_chat).analyze(result)


def run_fixture(path: Path) -> tuple[int, list[str]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    failures: list[str] = []
    for case in cases:
        analysis = asyncio.run(_replay(case))
        expected = case["expected"]
        if analysis.category != expected["category"]:
            failures.append(f"{case['name']}: category {analysis.category!r}")
        if analysis.verdict != expected["verdict"]:
            failures.append(f"{case['name']}: verdict {analysis.verdict!r}")
        needle = expected.get("summary_contains", "").lower()
        if needle and needle not in analysis.summary.lower():
            failures.append(f"{case['name']}: summary nu conține {needle!r}")
    return len(cases), failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline regression replay pentru Video Intel")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args(argv)

    total, failures = run_fixture(args.fixture)
    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        print(f"Video Intel replay: {total - len(failures)}/{total} fixture-uri verzi")
        return 1
    print(f"Video Intel replay: {total}/{total} fixture-uri verzi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
