from __future__ import annotations

import json
from pathlib import Path


def test_append_validation_history_includes_lightweight_evidence_index(tmp_path: Path) -> None:
    from orchestrator.validation_history import append_validation_history

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "Validation.md").write_text("# Validation\n")
    (task_dir / "evaluation.json").write_text(json.dumps({
        "evidenceIndex": [
            {"path": "logs/iter-1/action-trace.jsonl", "type": "trace", "signal": "ui_action_trace_recorded", "tc": "TC-F01"},
        ],
    }))

    append_validation_history(task_dir, 1, "pass", "UI trace proved", "finish")

    text = (task_dir / "Validation.md").read_text()
    assert "Lightweight evidence index" in text
    assert "logs/iter-1/action-trace.jsonl" in text
    assert "ui_action_trace_recorded" in text
    assert "TC-F01" in text


def test_generate_summary_includes_lightweight_evidence_index(monkeypatch, tmp_path: Path) -> None:
    import orchestrator.summary as summary

    workspace = tmp_path / "workspace"
    task_dir = workspace / ".automind" / "tasks" / "task01"
    summary_dir = workspace / ".automind" / "summary"
    task_dir.mkdir(parents=True)
    summary_dir.mkdir(parents=True)
    (task_dir / "Requirements.md").write_text("# Requirements\n")
    (task_dir / "Plan.md").write_text("# Plan\n")
    (task_dir / "Delivery.md").write_text("# Delivery\n")
    (task_dir / "Validation.md").write_text("# Validation\n\n## Next action\nfinish\n")
    (task_dir / "runtime-state.json").write_text(json.dumps({"status": "finished", "lastResult": "pass"}))
    (task_dir / "evaluation.json").write_text(json.dumps({
        "result": "pass",
        "summary": "Done",
        "nextAction": "finish",
        "evidence": [{"type": "log", "path": "logs/iter-1/evaluator.log"}],
        "evidenceIndex": [
            {"path": "logs/iter-1/action-trace.jsonl", "type": "trace", "signal": "ui_action_trace_recorded", "tc": ["TC-F01", "TC-F02"]},
        ],
    }))

    monkeypatch.setattr(summary, "AUTOMIND_WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(summary, "SUMMARY_DIR", summary_dir)
    monkeypatch.setattr(summary, "SUMMARY_LESSONS_PATH", summary_dir / "lessons-learned.md")
    monkeypatch.setattr(summary, "LOCAL_REUSE_INDEX_PATH", summary_dir / "local-reuse-index.md")
    monkeypatch.setattr(summary, "KNOWLEDGE_RAW_DIR", summary_dir / "raw")
    monkeypatch.setattr(summary, "get_task_dir", lambda _task_code: task_dir)
    monkeypatch.setattr(summary, "check_task_records", lambda _task_code: (True, []))
    monkeypatch.setattr(summary, "check_workflow_consistency", lambda _task_code: (True, {"issues": [], "warnings": [], "ids": {}}))
    monkeypatch.setattr(summary, "run_before_phase_hooks", lambda *args, **kwargs: {})
    monkeypatch.setattr(summary, "run_after_phase_hooks", lambda *args, **kwargs: {"phaseLearningPath": "-"})
    monkeypatch.setattr(summary, "print_report_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(summary, "write_trace", lambda *args, **kwargs: {"summary": {}})

    path = summary.generate_summary("task01", reason="unit_test")

    text = Path(path).read_text()
    assert "## 3.1 Lightweight Evidence Index" in text
    assert "logs/iter-1/action-trace.jsonl" in text
    assert "ui_action_trace_recorded" in text
    assert "TC-F01, TC-F02" in text
