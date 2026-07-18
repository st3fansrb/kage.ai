"""KageBench minimal — regression harness pentru executorul agentic.

Codex proposal: G1 (forma minimă acceptată).  Taskurile sunt fixe și declarative,
iar runner-ul nu alege singur ce să evalueze: pentru fiecare task rulează executorul
curent într-un worktree indicat, verifică o comandă de acceptare și scrie un raport
JSON + Markdown comparabil cu rularea precedentă.

Rularea reală este intenţionat la cerere, de exemplu:

    python -m kagebench --worktree /cale/catre/worktree --output reports/kagebench

Nu este un gate de CI: apelurile executorului Claude au cost, iar regresiile sunt
semnale pentru review, nu blocaje automate.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional


ROOT = Path(__file__).resolve().parent
TASKS_PATH = ROOT / "kagebench" / "tasks.json"


@dataclass(frozen=True)
class BenchmarkTask:
    id: str
    title: str
    fixture: str
    instruction: str
    allowed_tools: list[str]
    acceptance_command: str


def load_tasks(path: Path = TASKS_PATH) -> list[BenchmarkTask]:
    """Încarcă taskurile fixe și refuză o suită accidental prea mică/mare."""
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = [BenchmarkTask(**item) for item in data["tasks"]]
    if not 10 <= len(tasks) <= 15:
        raise ValueError("KageBench minimal necesită între 10 și 15 taskuri")
    if len({task.id for task in tasks}) != len(tasks):
        raise ValueError("ID-uri KageBench duplicate")
    return tasks


def _verify(task: BenchmarkTask, worktree: Path, timeout_s: int = 120) -> dict:
    """Rulează criteriul automat într-un worktree, fără shell interpolation."""
    command = shlex.split(task.acceptance_command.format(python=shlex.quote(sys.executable)))
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command, cwd=str(worktree), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout_s,
        )
        return {
            "passed": proc.returncode == 0,
            "exit_code": proc.returncode,
            "output": proc.stdout[-4000:],
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
    except subprocess.TimeoutExpired as exc:
        return {"passed": False, "exit_code": None,
                "output": (exc.stdout or "")[-4000:] + "\n[acceptance timeout]",
                "duration_ms": round((time.monotonic() - started) * 1000)}


def _require_clean_worktree(worktree: Path) -> None:
    """Refuză un benchmark peste modificări locale: baseline-ul ar fi ambiguu."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(worktree), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("worktree trebuie să fie un Git worktree valid") from exc
    if proc.stdout.strip():
        raise ValueError("worktree-ul KageBench trebuie să fie curat înainte de rulare")


async def run_benchmark(
    executor: Callable[..., AsyncIterator[dict]],
    *,
    worktree: Path,
    tasks: Optional[list[BenchmarkTask]] = None,
    previous_report: Optional[dict] = None,
    acceptance_timeout_s: int = 120,
) -> dict:
    """Rulează taskurile printr-un executor care emite evenimente normalizate.

    Contractul executorului este acelaşi ca ``AgentRunner.run``: evenimente
    ``tool_use``, ``result`` şi ``error`` sunt suficiente pentru metricile minime.
    Callback-ul de aprobare este injectat per task şi blochează implicit, astfel
    încât benchmark-ul nu confirmă acţiuni cu risc în mod automat.
    """
    worktree = Path(worktree).resolve()
    _require_clean_worktree(worktree)
    tasks = tasks or load_tasks()
    previous = {item["id"]: item for item in (previous_report or {}).get("tasks", [])}
    results: list[dict] = []

    for task in tasks:
        fixture_path = ROOT / "kagebench" / task.fixture
        if not fixture_path.is_file():
            raise ValueError("fixture lipsă pentru %s: %s" % (task.id, fixture_path))
        approvals = 0
        tools: list[str] = []
        turns = 0
        cost_usd: Optional[float] = None
        errors: list[str] = []
        started = time.monotonic()

        async def approval_cb(_name: str, _input: dict, _level: str, _reason: str) -> str:
            nonlocal approvals
            approvals += 1
            return "block"

        prompt = (
            "KageBench task %s — %s\n\n%s\n\n"
            "Fixture obligatoriu: %s\n"
            "Criteriul final este automat; nu pretinde succes fără a-l rula."
            % (task.id, task.title, task.instruction, fixture_path)
        )
        try:
            async for event in executor(
                prompt, user_message=task.instruction, cwd=str(worktree),
                allowed_tools=task.allowed_tools, approval_cb=approval_cb,
            ):
                kind = event.get("type")
                if kind == "tool_use":
                    tools.append(str(event.get("name", "unknown")))
                elif kind == "text":
                    turns += 1
                elif kind == "result" and event.get("cost_usd") is not None:
                    cost_usd = float(event["cost_usd"])
                elif kind == "error":
                    errors.append(str(event.get("error", "executor error")))
        except Exception as exc:
            errors.append("executor exception: %s" % exc)

        verification = _verify(task, worktree, acceptance_timeout_s)
        latency_ms = round((time.monotonic() - started) * 1000)
        status = "success" if verification["passed"] and not errors else (
            "partial" if verification["passed"] or not errors else "failed")
        item = {
            "id": task.id, "title": task.title, "fixture": task.fixture,
            "status": status, "acceptance": verification,
            "cost_usd": cost_usd, "latency_ms": latency_ms, "turns": turns,
            "tool_calls": len(tools), "tools": tools, "approvals_requested": approvals,
            "errors": errors,
        }
        prior = previous.get(task.id)
        if prior:
            item["regression"] = {
                "success": prior.get("status") == "success" and status != "success",
                "cost_usd": cost_usd is not None and prior.get("cost_usd") is not None
                and cost_usd > float(prior["cost_usd"]),
            }
        results.append(item)

    regressions = [item["id"] for item in results if any(item.get("regression", {}).values())]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "worktree": str(worktree),
        "task_count": len(results),
        "summary": {
            "success": sum(item["status"] == "success" for item in results),
            "partial": sum(item["status"] == "partial" for item in results),
            "failed": sum(item["status"] == "failed" for item in results),
            "cost_usd": round(sum(item["cost_usd"] or 0 for item in results), 6),
            "regressions": regressions,
        },
        "tasks": results,
    }


def write_report(report: dict, output_dir: Path) -> tuple[Path, Path]:
    """Persistă raportul maşină-citibil şi rezumatul text cerut de forma minimă."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / ("kagebench-%s.json" % stamp)
    md_path = output_dir / ("kagebench-%s.md" % stamp)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = report["summary"]
    lines = [
        "# KageBench report", "", "Codex proposal: G1 (minimal accepted form).", "",
        "- Tasks: %s" % report["task_count"],
        "- Success / partial / failed: %(success)s / %(partial)s / %(failed)s" % summary,
        "- Cost: $%s" % summary["cost_usd"],
        "- Regressions: %s" % (", ".join(summary["regressions"]) or "none"), "",
        "| Task | Status | Cost USD | Latency ms | Turns | Tools | Approvals |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report["tasks"]:
        lines.append("| {id} | {status} | {cost_usd} | {latency_ms} | {turns} | {tool_calls} | {approvals_requested} |".format(**item))
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


async def _cli_run(args: argparse.Namespace) -> int:
    from agent_runner import AgentRunner
    from orchestrator import AGENT_INACTIVITY_TIMEOUT, AUTONOMOUS_MODE, _policy_tools

    output_dir = Path(args.output).resolve()
    previous = None
    if args.previous:
        previous = json.loads(Path(args.previous).read_text(encoding="utf-8"))
    runner = AgentRunner()
    allowed, disallowed, permission_mode = _policy_tools("task")

    async def executor(prompt: str, **kwargs) -> AsyncIterator[dict]:
        async for event in runner.run(
            prompt, model=args.model, inactivity_timeout=AGENT_INACTIVITY_TIMEOUT,
            autonomous=AUTONOMOUS_MODE, disallowed_tools=disallowed,
            permission_mode=permission_mode, **kwargs,
        ):
            yield event

    report = await run_benchmark(executor, worktree=Path(args.worktree), previous_report=previous)
    json_path, md_path = write_report(report, output_dir)
    print("KageBench: %s\n%s" % (json_path, md_path))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run KageBench minimal on demand.")
    parser.add_argument("--worktree", required=True, help="clean Git worktree for agent execution")
    parser.add_argument("--output", default="reports/kagebench", help="report directory")
    parser.add_argument("--previous", help="previous KageBench JSON report for regression comparison")
    parser.add_argument("--model", default=None, help="optional Claude model override")
    return asyncio.run(_cli_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
