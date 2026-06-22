from __future__ import annotations

import json
from pathlib import Path

from orchestrator.state import read_runtime_state, write_runtime_state, write_evaluation_json
from orchestrator.state_summary_check import check_state_summary


def test_state_summary_check_repairs_stale_summary_and_obsolete_fields(tmp_path: Path) -> None:
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "retry_pending",
        "currentOwner": "generator",
        "nextAction": "retry_generator",
        "iteration": 2,
        "stateSummary": {"currentPhase": "planning", "currentStatus": "replan_pending", "currentOwner": "planner", "nextAction": "run_test_planner"},
        "phaseTransition": {"nextAction": "run_test_planner"},
    })
    write_evaluation_json(task_dir, {"iteration": 2, "result": "fail", "summary": "retry", "failedChecks": [], "nextAction": "retry_generator", "testResults": []})
    (task_dir / "phase-transition-summary.json").write_text("{}\n")

    before = check_state_summary(task_dir, repair=False)
    assert before["result"] == "fail"
    assert "obsolete runtime-state.json stateSummary exists" in before["issues"]
    assert any("stateSummary differs" in item for item in before["warnings"])
    assert "obsolete runtime-state.phaseTransition exists" in before["issues"]

    after = check_state_summary(task_dir, repair=True, reason="unit_test")
    state = read_runtime_state(task_dir)
    assert after["result"] == "pass"
    assert after["repairs"]
    assert "stateSummary" not in state
    assert "phaseTransition" not in state
    assert not (task_dir / "phase-transition-summary.json").exists()


def test_state_summary_check_repairs_runtime_route_before_summary(tmp_path: Path) -> None:
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "replan_pending",
        "currentOwner": "planner",
        "nextAction": "run_test_planner",
        "iteration": 3,
    })
    write_evaluation_json(task_dir, {"iteration": 3, "result": "fail", "summary": "retry", "failedChecks": [], "nextAction": "retry_generator", "testResults": []})

    result = check_state_summary(task_dir, repair=True, reason="unit_reconcile")
    state = read_runtime_state(task_dir)

    assert result["result"] == "pass"
    assert state["status"] == "retry_pending"
    assert state["currentOwner"] == "generator"
    assert state["nextAction"] == "retry_generator"
    assert "stateSummary" not in state


def test_state_summary_check_repairs_workflow_stage_drift(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.phase_transition as phase_transition

    monkeypatch.setattr(phase_transition, "check_workflow_consistency", lambda _task: (True, {"result": "pass", "workflowState": {}}))
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "retry_pending",
        "currentOwner": "generator",
        "nextAction": "retry_generator",
        "iteration": 2,
    })

    from orchestrator.workflow_state import emit_workflow_event, read_workflow_state, workflow_state_consistent

    emit_workflow_event(task_dir, {
        "type": "iteration_failed_retryable",
        "phase": "evaluation",
        "action": "judge_evidence",
        "nextAction": "retry_generator",
        "nextPhase": "delivery",
        "plannedNextPhase": "evaluation",
        "iteration": 3,
        "retryable": True,
    })
    workflow_path = task_dir / "automind-workflow-state.json"
    workflow = read_workflow_state(task_dir)
    workflow["lastEventId"] = "stale-event"
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n")
    assert workflow_state_consistent(task_dir) is False

    result = check_state_summary(task_dir, repair=True, reason="test")

    assert result["result"] == "pass"
    assert workflow_state_consistent(task_dir) is True
    assert any("workflow control state" in item for item in result["repairs"])


def test_state_summary_check_warns_when_workflow_state_seeded_from_runtime(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.phase_transition as phase_transition

    monkeypatch.setattr(phase_transition, "check_workflow_consistency", lambda _task: (True, {"result": "pass", "workflowState": {}}))
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "planning", "currentOwner": "planner", "nextAction": "run_test_planner", "iteration": 1})

    result = check_state_summary(task_dir, repair=True, reason="test")

    assert result["result"] == "pass"
    assert any("workflow control state health is reconciling" in item for item in result["warnings"])
