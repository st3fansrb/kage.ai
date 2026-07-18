"""Contracte pentru KageBench minimal (Codex proposal: G1)."""
import json
import subprocess
from pathlib import Path

import pytest

import kagebench


def _init_clean_worktree(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def test_kagebench_defines_ten_fixed_tasks_with_fixtures():
    tasks = kagebench.load_tasks()
    assert len(tasks) == 10
    assert {task.id for task in tasks} == {"KB%02d" % n for n in range(1, 11)}
    for task in tasks:
        assert (kagebench.ROOT / "kagebench" / task.fixture).is_file()
        assert task.allowed_tools
        assert task.acceptance_command


@pytest.mark.asyncio
async def test_runner_collects_metrics_and_writes_machine_and_human_reports(tmp_path, monkeypatch):
    async def fake_executor(_prompt, **kwargs):
        assert kwargs["cwd"] == str(tmp_path.resolve())
        yield {"type": "text", "text": "checking"}
        yield {"type": "tool_use", "name": "Read"}
        yield {"type": "result", "cost_usd": 0.012}

    monkeypatch.setattr(kagebench, "_verify", lambda *_args, **_kwargs: {
        "passed": True, "exit_code": 0, "output": "ok", "duration_ms": 1,
    })
    _init_clean_worktree(tmp_path)
    report = await kagebench.run_benchmark(fake_executor, worktree=tmp_path)

    assert report["summary"]["success"] == 10
    assert report["summary"]["cost_usd"] == pytest.approx(0.12)
    assert report["tasks"][0]["turns"] == 1
    assert report["tasks"][0]["tool_calls"] == 1

    json_path, md_path = kagebench.write_report(report, tmp_path / "reports")
    assert json.loads(json_path.read_text(encoding="utf-8"))["task_count"] == 10
    assert "KageBench report" in md_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_regression_from_prior_success_is_reported(tmp_path, monkeypatch):
    async def fake_executor(_prompt, **_kwargs):
        yield {"type": "result", "cost_usd": 0.02}

    _init_clean_worktree(tmp_path)

    def verify(task, *_args, **_kwargs):
        return {"passed": task.id != "KB01", "exit_code": 1,
                "output": "deliberate fixture regression", "duration_ms": 1}

    monkeypatch.setattr(kagebench, "_verify", verify)
    previous = {"tasks": [{"id": "KB01", "status": "success", "cost_usd": 0.01}]}
    report = await kagebench.run_benchmark(fake_executor, worktree=tmp_path,
                                            previous_report=previous)

    first = report["tasks"][0]
    assert first["status"] == "partial"
    assert first["regression"] == {"success": True, "cost_usd": True}
    assert report["summary"]["regressions"] == ["KB01"]


@pytest.mark.asyncio
async def test_dirty_worktree_is_rejected_before_an_executor_runs(tmp_path):
    _init_clean_worktree(tmp_path)
    (tmp_path / "uncommitted.txt").write_text("not a reproducible baseline\n")

    async def executor(*_args, **_kwargs):
        raise AssertionError("executor must not start")
        yield  # pragma: no cover - keeps this an async generator

    with pytest.raises(ValueError, match="curat"):
        await kagebench.run_benchmark(executor, worktree=tmp_path)
