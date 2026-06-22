from __future__ import annotations

import json
from pathlib import Path


def test_run_harness_loop_does_not_reopen_stable_finished_task(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "Requirements.md").write_text("# Requirements\n")
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "finished",
        "iteration": 7,
        "currentOwner": "supervisor",
        "nextAction": "finish",
        "lastResult": "pass",
        "completionCheck": "pass",
    }))
    (task_dir / "evaluation.json").write_text(json.dumps({
        "iteration": 7,
        "result": "pass",
        "nextAction": "finish",
        "failedChecks": [],
        "testResults": [],
    }))
    (task_dir / "completion-report.json").write_text(json.dumps({
        "result": "pass",
        "completionVerdict": {"result": "pass", "reason": "completion-check passed"},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish", "iteration": 7},
        "testResults": [],
    }))

    calls = {"planner": 0, "agent": 0}
    monkeypatch.setattr(main, "TASKS_DIR", tmp_path / ".automind" / "tasks")
    monkeypatch.setattr(main, "get_task_dir", lambda task_code: tmp_path / ".automind" / "tasks" / task_code)
    monkeypatch.setattr(main, "primary_requirements_path", lambda td: td / "Requirements.md")
    monkeypatch.setattr(main, "has_unanswered_pending_question", lambda td: False)
    monkeypatch.setattr(main, "run_ai_test_planner", lambda *a, **k: calls.__setitem__("planner", calls["planner"] + 1) or True)
    monkeypatch.setattr(main, "run_agent", lambda *a, **k: calls.__setitem__("agent", calls["agent"] + 1) or (0, ""))
    monkeypatch.setattr(main, "ensure_summary_generated", lambda *a, **k: None)

    assert main.run_harness_loop("task01", agent="codex") is True
    assert calls == {"planner": 0, "agent": 0}
    state = json.loads((task_dir / "runtime-state.json").read_text())
    assert state["status"] == "finished"
    assert state["nextAction"] == "finish"


def test_terminal_pass_blocks_later_partial_regression(tmp_path: Path) -> None:
    from orchestrator.evaluation_result import apply_evaluation_result
    from orchestrator.state import read_evaluation_json, read_runtime_state, write_evaluation_json

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    terminal_eval = {"iteration": 20, "result": "pass", "nextAction": "finish", "failedChecks": [], "testResults": []}
    (task_dir / "evaluation.json").write_text(json.dumps(terminal_eval))
    report = {
        "result": "pass",
        "completionVerdict": {"result": "pass", "reason": "all required TC pass"},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish", "iteration": 20},
        "testResults": [],
    }
    (task_dir / "completion-report.json").write_text(json.dumps(report))
    (task_dir / "VerificationLedger.json").write_text(json.dumps(report))
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "finished",
        "iteration": 20,
        "currentOwner": "automind",
        "nextAction": "finish",
        "completionCheck": "pass",
    }))

    weak_partial = {"iteration": 21, "result": "partial", "nextAction": "retry_generator", "summary": "weak partial"}
    write_evaluation_json(task_dir, weak_partial)
    apply_evaluation_result(task_dir, weak_partial)

    assert read_evaluation_json(task_dir) == terminal_eval
    state = read_runtime_state(task_dir) or {}
    assert state["status"] == "finished"
    assert state["currentOwner"] == "automind"
    assert state["nextAction"] == "finish"
    assert state["lastResult"] == "pass"
    assert (task_dir / "logs" / "advisory-evaluations").exists()


def test_replan_routes_to_planner_not_generator(tmp_path: Path) -> None:
    from orchestrator.evaluation_result import apply_evaluation_result
    from orchestrator.state import read_runtime_state

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    (task_dir / "runtime-state.json").write_text(json.dumps({"taskId": "task01", "status": "evaluating"}))

    apply_evaluation_result(task_dir, {"iteration": 3, "result": "blocked", "nextAction": "replan"})

    state = read_runtime_state(task_dir) or {}
    assert state["status"] == "replan_pending"
    assert state["currentOwner"] == "planner"
    assert state["nextAction"] == "run_test_planner"
