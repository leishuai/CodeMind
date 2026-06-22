"""TC-R06: Decision Log 最简版。

AC-012: 写入 runtime-state.json.decisionLog 长度 ≥1 且字段齐全。
AC-013: 第二轮 prompt 注入最近 N 条摘要。
"""
from __future__ import annotations

from pathlib import Path

from orchestrator.state import (
    append_decision_log,
    format_recent_decisions,
    read_runtime_state,
    write_runtime_state,
)


def _make_task_dir(tmp_path: Path) -> Path:
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "ready"})
    return task_dir


def test_append_decision_log_writes_entry(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    append_decision_log(task_dir, iteration=1, phase="generator", text="实现 T1")
    state = read_runtime_state(task_dir)
    assert state is not None
    log = state.get("decisionLog")
    assert isinstance(log, list)
    assert len(log) == 1
    entry = log[0]
    assert entry["iteration"] == 1
    assert entry["phase"] == "generator"
    assert entry["text"] == "实现 T1"
    assert "timestamp" in entry


def test_decision_log_truncates_to_200_chars(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    long_text = "x" * 500
    append_decision_log(task_dir, iteration=1, phase="evaluator", text=long_text)
    state = read_runtime_state(task_dir)
    entry = state["decisionLog"][0]
    # AC-012 限 200 字
    assert len(entry["text"]) <= 200


def test_format_recent_decisions_returns_last_n(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    for i in range(7):
        append_decision_log(task_dir, iteration=i + 1, phase="generator", text=f"step {i}")
    state = read_runtime_state(task_dir)
    rendered = format_recent_decisions(state, limit=5)
    # AC-013: 注入最近 5 条
    assert "step 6" in rendered
    assert "step 5" in rendered
    assert "step 4" in rendered
    assert "step 3" in rendered
    assert "step 2" in rendered
    # 第 1/0 条不在
    assert "step 0" not in rendered
    assert "step 1" not in rendered


def test_format_recent_decisions_empty(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    state = read_runtime_state(task_dir)
    assert format_recent_decisions(state) == ""
