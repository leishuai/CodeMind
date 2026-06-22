"""TC-R08: heartbeat 全覆盖。

AC-016: 跑一段含多 phase 的 sandbox loop → heartbeat.log 行数 ≥8。
AC-017: with heartbeat_tick(state, "refiner") 块抛 RuntimeError → heartbeat.log 含 phase_end_error。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.state import heartbeat_tick, read_heartbeat_log, write_runtime_state


def _make_task_dir(tmp_path: Path) -> Path:
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01"})
    return task_dir


def test_heartbeat_count_threshold(tmp_path):
    """AC-016: 多 phase 进出 → heartbeat.log 行数 ≥8。"""
    task_dir = _make_task_dir(tmp_path)
    phases = [
        "preflight", "planner", "refiner", "workflow_check",
        "generator", "evaluator", "completion_check", "summary",
    ]
    for ph in phases:
        with heartbeat_tick(task_dir, ph):
            pass
    log = read_heartbeat_log(task_dir)
    # 每个 phase 至少 phase_start + phase_end，共 8*2=16 行
    assert len(log) >= 8, f"expected ≥8 heartbeat lines, got {len(log)}"


def test_heartbeat_records_error_on_exception(tmp_path):
    """AC-017: with heartbeat_tick 块抛 RuntimeError → heartbeat.log 含 phase_end_error。"""
    task_dir = _make_task_dir(tmp_path)
    with pytest.raises(RuntimeError):
        with heartbeat_tick(task_dir, "refiner"):
            raise RuntimeError("boom")
    log = read_heartbeat_log(task_dir)
    text = "\n".join(log)
    assert "phase_end_error" in text, f"expected phase_end_error marker in heartbeat log, got:\n{text}"
    assert "refiner" in text


def test_heartbeat_records_phase_start_and_end(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    with heartbeat_tick(task_dir, "generator"):
        pass
    log = read_heartbeat_log(task_dir)
    text = "\n".join(log)
    assert "phase_start" in text
    assert "phase_end" in text
    assert "generator" in text
