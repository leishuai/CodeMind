from __future__ import annotations

import json
from pathlib import Path

from orchestrator.state import read_runtime_state, write_runtime_state
from orchestrator.state_reducer import derive_effective_state, reconcile_task_state


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def test_replan_routes_to_planner_until_workflow_check_passes_after_planning(tmp_path: Path) -> None:
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "evaluating",
        "currentOwner": "evaluator",
        "nextAction": "run_evaluator",
        "workflowCheck": {"ok": False, "issues": ["Plan.md missing"]},
    })
    _write_json(task_dir / "evaluation.json", {"result": "blocked", "nextAction": "replan", "testResults": []})

    derived = derive_effective_state(task_dir)

    assert derived["reason"] == "evaluation_requests_replan"
    assert derived["effective"]["status"] == "replan_pending"
    assert derived["effective"]["currentOwner"] == "planner"
    assert derived["effective"]["nextAction"] == "run_test_planner"
    assert derived["evaluation"]["consumedReplan"] is False


def test_stale_replan_is_not_guessed_satisfied_from_old_planner_and_workflow_state(tmp_path: Path) -> None:
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "replan_pending",
        "currentOwner": "planner",
        "nextAction": "run_test_planner",
        "iteration": 4,
        "planner": {"mode": "ai_test_planner", "ok": True, "exitCode": 0},
        "workflowCheck": {"ok": True, "issues": [], "warnings": ["reuse warning"]},
    })
    _write_json(task_dir / "evaluation.json", {"result": "blocked", "nextAction": "replan", "testResults": []})

    derived = derive_effective_state(task_dir)

    assert derived["reason"] == "evaluation_requests_replan"
    assert derived["effective"]["status"] == "replan_pending"
    assert derived["effective"]["currentOwner"] == "planner"
    assert derived["effective"]["nextAction"] == "run_test_planner"
    assert derived["evaluation"]["consumedReplan"] is False


def test_explicit_replan_resolution_updates_evaluation_route(tmp_path: Path) -> None:
    from orchestrator.main import clear_replan_signal_after_planner

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "replan_pending",
        "currentOwner": "planner",
        "nextAction": "run_test_planner",
        "iteration": 4,
    })
    _write_json(task_dir / "evaluation.json", {"result": "blocked", "nextAction": "replan", "iteration": 4, "testResults": []})

    clear_replan_signal_after_planner(task_dir, 4, reason="unit_workflow_pass_after_current_planner")

    evaluation = json.loads((task_dir / "evaluation.json").read_text())
    state = read_runtime_state(task_dir)
    assert evaluation["previousNextAction"] == "replan"
    assert evaluation["nextAction"] == "retry_generator"
    assert evaluation["replanResolution"]["reason"] == "unit_workflow_pass_after_current_planner"
    assert state["status"] == "retry_pending"
    assert state["currentOwner"] == "generator"
    assert state["nextAction"] == "retry_generator"


def test_consumed_replan_evaluation_routes_generator(tmp_path: Path) -> None:
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "replan_pending",
        "currentOwner": "planner",
        "nextAction": "run_test_planner",
        "iteration": 4,
    })
    _write_json(task_dir / "evaluation.json", {
        "result": "fail",
        "previousNextAction": "replan",
        "nextAction": "retry_generator",
        "replanResolution": {"reason": "workflow_pass_after_current_planner"},
        "iteration": 4,
        "testResults": [],
    })

    derived = derive_effective_state(task_dir)

    assert derived["reason"] == "evaluation_requests_generator_retry"
    assert derived["effective"]["status"] == "retry_pending"
    assert derived["evaluation"]["consumedReplan"] is True


def test_reconcile_refreshes_stale_state_authority_when_evaluation_route_changes(tmp_path: Path) -> None:
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "retry_pending",
        "currentOwner": "generator",
        "nextAction": "retry_generator",
        "iteration": 4,
        "stateAuthority": {
            "role": "derived_cache",
            "reason": "evaluation_requests_replan",
            "evaluation": {"result": "blocked", "nextAction": "replan"},
            "effective": {"status": "retry_pending", "currentOwner": "generator", "nextAction": "retry_generator"},
        },
    })
    _write_json(task_dir / "evaluation.json", {"result": "fail", "nextAction": "retry_generator", "iteration": 4, "testResults": []})

    reconciled = reconcile_task_state(task_dir, reason="unit_test_authority_refresh")
    state = read_runtime_state(task_dir)

    assert reconciled["reason"] == "evaluation_requests_generator_retry"
    assert state["status"] == "retry_pending"
    assert state["stateAuthority"]["reason"] == "evaluation_requests_generator_retry"
    assert state["stateAuthority"]["evaluation"]["nextAction"] == "retry_generator"


def _authoritative_completion_payload() -> dict:
    return {
        "result": "pass",
        "completionVerdict": {"result": "pass", "overridesRawEvaluation": False, "reason": "completion-check passed"},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish", "iteration": 3},
        "testResults": [{"testCaseId": "TC-F01", "result": "pass", "required": True}],
    }


def test_authoritative_completion_pass_closes_task_when_summary_is_last(tmp_path: Path) -> None:
    task_dir = tmp_path / "task01"
    (task_dir / "stages").mkdir(parents=True)
    write_runtime_state(task_dir, {"taskId": "task01", "status": "finished", "currentOwner": "automind", "nextAction": "finish"})
    _write_json(task_dir / "stages" / "verification-loop-stage-state.json", {
        "stage": "verification_loop",
        "status": "completed",
        "updatedAt": "2026-06-12T18:10:00",
    })
    _write_json(task_dir / "stages" / "summary-stage-state.json", {
        "stage": "summary",
        "status": "completed",
        "updatedAt": "2026-06-12T18:16:17",
        "completion": _authoritative_completion_payload(),
    })

    derived = derive_effective_state(task_dir)

    assert derived["terminalAllowed"] is True
    assert derived["terminalAuthoritative"] is True
    assert derived["reason"] == "completion_check_pass"
    assert derived["effective"]["status"] == "finished"


def test_stale_completion_pass_is_rejected_when_verification_loop_reopened(tmp_path: Path) -> None:
    task_dir = tmp_path / "task01"
    (task_dir / "stages").mkdir(parents=True)
    (task_dir / "Delivery.md").write_text("delivery\n")
    write_runtime_state(task_dir, {"taskId": "task01", "status": "finished", "currentOwner": "automind", "nextAction": "finish"})
    # Summary recorded a completion pass first ...
    _write_json(task_dir / "stages" / "summary-stage-state.json", {
        "stage": "summary",
        "status": "completed",
        "updatedAt": "2026-06-12T18:16:17",
        "completion": _authoritative_completion_payload(),
    })
    # ... but the verification loop was re-activated afterwards (false finish rollback).
    _write_json(task_dir / "stages" / "verification-loop-stage-state.json", {
        "stage": "verification_loop",
        "status": "active",
        "updatedAt": "2026-06-12T18:47:57",
    })

    derived = derive_effective_state(task_dir)

    assert derived["terminalAllowed"] is False
    assert derived["terminalAuthoritative"] is False
    assert derived["effective"]["status"] != "finished"


def test_write_evaluation_json_stamps_updated_at(tmp_path: Path) -> None:
    from orchestrator.state import read_evaluation_json, write_evaluation_json

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_evaluation_json(task_dir, {"iteration": 1, "result": "fail", "summary": "x", "failedChecks": [], "nextAction": "retry_generator"})

    evaluation = read_evaluation_json(task_dir)
    assert evaluation["updatedAt"]


def test_apply_evaluation_result_refreshes_phase_transition_summary(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.evaluation_result import apply_evaluation_result

    # Avoid workflow side effects in phase summary refresh.
    import orchestrator.phase_transition as phase_transition
    monkeypatch.setattr(phase_transition, "check_workflow_consistency", lambda _task: (True, {"result": "pass", "workflowState": {"expectedNext": [{"phase": "delivery"}]}}))

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "evaluating", "nextAction": "run_evaluator"})
    from orchestrator.state import write_evaluation_json
    evaluation = {"iteration": 1, "result": "fail", "summary": "needs repair", "failedChecks": [], "nextAction": "retry_generator", "testResults": []}
    write_evaluation_json(task_dir, evaluation)

    apply_evaluation_result(task_dir, evaluation)

    state = read_runtime_state(task_dir)
    assert state["status"] == "retry_pending"
    assert "stateSummary" not in state
    from orchestrator.phase_transition import build_phase_transition_summary
    summary = build_phase_transition_summary(task_dir)
    assert summary["currentPhase"] == "delivery"
    assert summary["nextAction"] == "retry_generator"
