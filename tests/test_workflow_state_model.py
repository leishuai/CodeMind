from __future__ import annotations

import json
from pathlib import Path

from orchestrator.evaluation_result import apply_evaluation_result
from orchestrator.phase_transition import refresh_phase_transition_summary
from orchestrator.state import read_runtime_state, tick_iteration, write_runtime_state
from orchestrator.workflow_state import (
    emit_workflow_event,
    read_stage_state,
    read_workflow_state,
    workflow_events_path,
    workflow_state_consistent,
)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _workflow_ok(monkeypatch) -> None:
    import orchestrator.phase_transition as phase_transition

    monkeypatch.setattr(
        phase_transition,
        "check_workflow_consistency",
        lambda _task: (True, {"result": "pass", "workflowState": {"currentPhase": "delivery", "expectedNext": [{"phase": "evaluation"}]}}),
    )


def test_emit_workflow_event_writes_workflow_and_stage_state(tmp_path: Path) -> None:
    task_dir = tmp_path / "task01"
    workflow = emit_workflow_event(task_dir, {
        "type": "phase_action_completed",
        "phase": "delivery",
        "action": "run_generator",
        "nextAction": "run_evaluation",
        "nextPhase": "evaluation",
        "plannedNextPhase": "completion",
        "iteration": 1,
        "reason": "delivery_completed",
    })

    stage = read_stage_state(task_dir, "verification_loop")
    assert workflow["currentStage"] == "verification_loop"
    assert workflow["currentPhase"] == "evaluation"
    assert workflow["nextPhase"] == "evaluation"
    assert workflow["plannedNextPhase"] == "completion"
    assert workflow["currentOwner"] == "evaluator"
    assert workflow["lastEventId"] == stage["lastEventId"]
    assert workflow_state_consistent(task_dir) is True
    assert workflow_events_path(task_dir).read_text().count("evt_") == 1


def test_apply_evaluation_retry_advances_verification_loop_iteration(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "evaluating", "currentOwner": "evaluator", "nextAction": "run_evaluator", "iteration": 3})

    apply_evaluation_result(task_dir, {"iteration": 3, "result": "fail", "nextAction": "retry_generator", "testResults": []})

    workflow = read_workflow_state(task_dir)
    stage = read_stage_state(task_dir, "verification_loop")
    runtime = read_runtime_state(task_dir) or {}
    assert runtime["nextAction"] == "retry_generator"
    assert workflow["currentStage"] == "verification_loop"
    assert workflow["currentPhase"] == "delivery"
    assert workflow["nextAction"] == "retry_generator"
    assert workflow["nextPhase"] == "delivery"
    assert workflow["plannedNextPhase"] == "evaluation"
    assert workflow["iteration"] == 4
    assert stage["iteration"]["current"] == 4
    assert stage["iteration"]["retryable"] is True
    assert workflow_state_consistent(task_dir) is True


def test_apply_evaluation_pass_routes_to_summary_completion(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "evaluating", "currentOwner": "evaluator", "nextAction": "run_evaluator", "iteration": 2})

    apply_evaluation_result(task_dir, {"iteration": 2, "result": "pass", "nextAction": "finish", "testResults": [{"id": "TC-1", "result": "pass"}]})

    workflow = read_workflow_state(task_dir)
    stage = read_stage_state(task_dir, "summary")
    assert workflow["status"] == "running"
    assert workflow["currentStage"] == "summary"
    assert workflow["currentPhase"] == "completion"
    assert workflow["nextAction"] == "complete_task"
    assert workflow["plannedNextPhase"] is None
    assert stage["stage"] == "summary"
    assert workflow_state_consistent(task_dir) is True


def test_refresh_phase_transition_summary_does_not_spam_events_when_route_unchanged(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "planned", "nextAction": "run_generator", "iteration": 0})

    refresh_phase_transition_summary(task_dir)
    first_events = workflow_events_path(task_dir).read_text().splitlines()
    refresh_phase_transition_summary(task_dir)
    second_events = workflow_events_path(task_dir).read_text().splitlines()

    assert len(first_events) == 1
    assert len(second_events) == 1


def test_tick_iteration_updates_verification_loop_stage_state(tmp_path: Path) -> None:
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "running", "iteration": 1, "maxIterations": 5})

    info = tick_iteration(task_dir, phase="delivery")

    workflow = read_workflow_state(task_dir)
    stage = read_stage_state(task_dir, "verification_loop")
    assert info["iteration"] == 2
    assert workflow["currentStage"] == "verification_loop"
    assert workflow["currentPhase"] == "delivery"
    assert workflow["iteration"] == 2
    assert workflow["lastEventId"] == stage["lastEventId"]
    assert stage["iteration"]["current"] == 2
    assert stage["iteration"]["max"] == info["budget"]
    assert stage["iteration"]["phase"] == "delivery"
    assert workflow_state_consistent(task_dir) is True


def test_build_next_instruction_exposes_workflow_control_state(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.session.instructions as instructions

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "planned", "currentOwner": "planner", "nextAction": "run_generator", "iteration": 0})
    monkeypatch.setattr(instructions, "check_workflow_consistency", lambda _task: (True, {"workflowState": {"currentPhase": "delivery", "expectedNext": [{"phase": "delivery"}]}}))

    payload = instructions.build_next_instruction("task01", task_dir)

    assert payload["workflowControlState"]["currentPhase"] in {"delivery", "plan", "task_setup"}
    assert any("workflowControlState" in item for item in payload["checklist"])


def test_phase_gate_prefers_workflow_control_state_and_exposes_stage_state(tmp_path: Path, monkeypatch, capsys) -> None:
    import json as _json
    import orchestrator.commands.session as session_cmd

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"taskId": "task01", "status": "retry_pending", "currentOwner": "generator", "nextAction": "retry_generator", "iteration": 2})
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
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _task: task_dir)

    session_cmd.cmd_phase_gate("task01", ["build", "--soft"])
    out = capsys.readouterr().out
    payload = _json.loads(out)

    assert payload["result"] == "fail"
    assert payload["centralJson"] == "automind-workflow-state.json"
    assert payload["workflowControlState"]["currentStage"] == "verification_loop"
    assert payload["workflowControlState"]["currentPhase"] == "delivery"
    assert payload["workflowControlState"]["nextPhase"] == "delivery"
    assert payload["requiredCommand"] == "automind workflow-check task01"
    assert payload["stageState"]["stage"] == "verification_loop"
    assert "automind-workflow-state.json" in payload["readFiles"]


def test_phase_gate_refreshes_generator_reuse_when_missing(tmp_path: Path, monkeypatch, capsys) -> None:
    import json as _json
    import orchestrator.commands.session as session_cmd

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"taskId": "task01", "status": "retry_pending", "currentOwner": "generator", "nextAction": "retry_generator", "iteration": 2})
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
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _task: task_dir)
    monkeypatch.setattr(session_cmd, "build_next_instruction", lambda _task, _dir: {
        "workflowControlState": {
            "currentStage": "verification_loop",
            "currentPhase": "delivery",
            "nextPhase": "delivery",
            "nextAction": "run_generator",
        },
        "phaseSummary": {"nextPhase": "delivery", "nextAction": "run_generator", "reason": "ready for generator", "checklist": []},
        "effectiveNext": {},
        "workflowSignal": {"issueCount": 0},
        "nextActionPrompt": "ready for generator",
    })

    session_cmd.cmd_phase_gate("task01", ["build", "--soft"])
    payload = _json.loads(capsys.readouterr().out)

    assert payload["phaseReuseRefresh"]["refreshed"] is True
    assert payload["phaseReuseRefresh"]["phase"] == "generator"
    assert payload["phaseReuseRefresh"]["reason"] == "missing_phase_reuse"
    assert (task_dir / "phase-reuse" / "generator.md").exists()
    state = read_runtime_state(task_dir)
    assert state["reuseGate"]["generator"]["required"] is True
    assert state["reuseGate"]["generator"]["acknowledged"] is False


def test_phase_gate_keeps_fresh_reuse_ack(tmp_path: Path, monkeypatch, capsys) -> None:
    import json as _json
    import orchestrator.commands.session as session_cmd

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "Requirements.md").write_text("# Requirements\n")
    (task_dir / "TestCases.md").write_text("# TestCases\n")
    (task_dir / "Plan.md").write_text("# Plan\n")
    phase_reuse_dir = task_dir / "phase-reuse"
    phase_reuse_dir.mkdir()
    (phase_reuse_dir / "generator.md").write_text("# Phase Reuse: generator\n")
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "retry_pending",
        "currentOwner": "generator",
        "nextAction": "retry_generator",
        "iteration": 2,
        "reuseGate": {
            "generator": {
                "phase": "generator",
                "required": True,
                "acknowledged": True,
                "acknowledgement": {
                    "phaseReuseRead": True,
                    "reuseApplied": ["existing path"],
                    "reuseIgnored": [],
                },
            }
        },
    })
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
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _task: task_dir)
    monkeypatch.setattr(session_cmd, "build_next_instruction", lambda _task, _dir: {
        "workflowControlState": {
            "currentStage": "verification_loop",
            "currentPhase": "delivery",
            "nextPhase": "delivery",
            "nextAction": "run_generator",
        },
        "phaseSummary": {"nextPhase": "delivery", "nextAction": "run_generator", "reason": "ready for generator", "checklist": []},
        "effectiveNext": {},
        "workflowSignal": {"issueCount": 0},
        "nextActionPrompt": "ready for generator",
    })

    session_cmd.cmd_phase_gate("task01", ["build", "--soft"])
    payload = _json.loads(capsys.readouterr().out)

    assert payload["phaseReuseRefresh"]["refreshed"] is False
    assert payload["phaseReuseRefresh"]["reason"] == "fresh"
    state = read_runtime_state(task_dir)
    assert state["reuseGate"]["generator"]["acknowledged"] is True
    assert state["reuseGate"]["generator"]["acknowledgement"]["reuseApplied"] == ["existing path"]


def test_planning_legacy_phase_normalizes_to_plan() -> None:
    from orchestrator.workflow_state import normalize_phase, stage_for_phase

    assert normalize_phase("planning") == "plan"
    assert stage_for_phase("planning") == "requirement"


def test_reconcile_without_events_is_marked_as_runtime_fallback(tmp_path: Path) -> None:
    from orchestrator.workflow_state import ensure_workflow_state

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "planning", "nextAction": "run_test_planner", "iteration": 7})

    workflow = ensure_workflow_state(task_dir)
    events = workflow_events_path(task_dir).read_text()

    assert workflow["stateHealth"] == "reconciling"
    assert workflow["currentPhase"] == "plan"
    assert "state_reconciled_from_runtime" in events
    assert "runtime-state fallback" in events


def test_phase_gate_fails_when_route_sources_disagree(tmp_path: Path, monkeypatch, capsys) -> None:
    import json as _json
    import orchestrator.commands.session as session_cmd

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"taskId": "task01", "status": "running", "currentOwner": "generator", "nextAction": "run_generator", "iteration": 1})
    emit_workflow_event(task_dir, {
        "type": "phase_action_completed",
        "phase": "delivery",
        "action": "run_generator",
        "nextAction": "run_evaluation",
        "nextPhase": "evaluation",
        "iteration": 1,
    })
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _task: task_dir)
    monkeypatch.setattr(session_cmd, "build_next_instruction", lambda _task, _dir: {
        "workflowControlState": read_workflow_state(task_dir),
        "stateSummary": {"nextPhase": "delivery", "nextAction": "retry_generator", "reason": "stale"},
        "effectiveNext": {},
        "nextActionPrompt": "stale",
    })

    session_cmd.cmd_phase_gate("task01", ["verify", "--soft"])
    payload = _json.loads(capsys.readouterr().out)

    assert payload["result"] == "fail"
    assert payload["routeSource"] == "workflowControlState"
    assert payload["routeDrift"]
    assert "route sources disagree" in payload["reason"]



def test_phase_gate_does_not_report_route_drift_for_workflow_blocker_override(tmp_path: Path, monkeypatch, capsys) -> None:
    import json as _json
    import orchestrator.commands.session as session_cmd

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"taskId": "task01", "status": "retry_pending", "currentOwner": "generator", "nextAction": "retry_generator", "iteration": 2})
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
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _task: task_dir)
    monkeypatch.setattr(session_cmd, "build_next_instruction", lambda _task, _dir: {
        "workflowControlState": read_workflow_state(task_dir),
        "phaseSummary": {"nextPhase": "delivery", "nextAction": "retry_generator", "reason": "retry generator"},
        "effectiveNext": {"phase": "planning", "action": "resolve_workflow_blockers", "summary": "ack reuse first"},
        "nextActionPrompt": "ack reuse first",
    })

    session_cmd.cmd_phase_gate("task01", ["auto", "--soft"])
    payload = _json.loads(capsys.readouterr().out)

    assert payload["routeDrift"] == []
    assert payload["result"] == "fail"
    assert payload["requiredCommand"] == "automind workflow-check task01"
    assert payload["reason"] == "ack reuse first"

def test_process_eval_warns_on_workflow_state_sync_error_and_reconciling_health(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.process_eval import run_process_eval
    import orchestrator.process_eval as process_eval

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "planning",
        "currentOwner": "planner",
        "nextAction": "run_test_planner",
        "iteration": 1,
        "workflowStateError": {"error": "boom", "iteration": 1, "phase": "delivery", "at": "now"},
    })
    emit_workflow_event(task_dir, {
        "type": "state_reconciled_from_runtime",
        "phase": "plan",
        "action": "create_plan",
        "nextAction": "create_plan",
        "nextPhase": "plan",
        "iteration": 1,
        "stateHealth": "reconciling",
    })
    monkeypatch.setattr(process_eval, "ensure_workflow_contract", lambda _task_dir: {"phaseGraph": {"nodes": ["pre_implementation_review"]}, "expectedNext": [{"phase": "plan"}]})
    monkeypatch.setattr(process_eval, "validate_workflow_contract", lambda _task_dir, _contract: ([], []))
    (task_dir / "pre-implementation-review.json").write_text(json.dumps({"decision": "auto_proceed"}) + "\n")

    report = run_process_eval("task01", task_dir, write=False)
    warning_ids = {item["id"] for item in report["warnings"]}

    assert "workflow-state-sync-error" in warning_ids
    assert "workflow-control-state-health" in warning_ids


def test_status_prints_workflow_state_sync_error(tmp_path: Path, monkeypatch, capsys) -> None:
    import orchestrator.commands.status as status_cmd

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "running",
        "currentOwner": "generator",
        "nextAction": "run_generator",
        "iteration": 1,
        "workflowStateError": {"error": "ValueError: boom", "iteration": 1, "phase": "delivery", "at": "2026-06-11T13:33:00"},
    })
    emit_workflow_event(task_dir, {"phase": "delivery", "action": "run_generator", "nextAction": "run_evaluation", "nextPhase": "evaluation", "iteration": 1})
    monkeypatch.setattr(status_cmd, "get_task_dir", lambda _task: task_dir)
    monkeypatch.setattr(status_cmd, "reconcile_task_state", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(status_cmd, "check_state_summary", lambda *_args, **_kwargs: {"result": "pass", "repairs": []})
    monkeypatch.setattr(status_cmd, "build_next_instruction", lambda *_args, **_kwargs: {"effectiveNext": {}, "stateSummary": {}})
    monkeypatch.setattr(status_cmd, "build_status_guidance", lambda *_args, **_kwargs: {"stateSummary": {}, "recommendedNext": [], "readFiles": []})
    monkeypatch.setattr(status_cmd, "print_report_manifest", lambda *_args, **_kwargs: None)

    status_cmd.cmd_status("task01")
    out = capsys.readouterr().out

    assert "Workflow state sync error: ValueError: boom" in out


def test_write_evaluation_json_syncs_and_reads_from_stage_state(tmp_path: Path) -> None:
    from orchestrator.state import read_evaluation_json, write_evaluation_json

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "evaluating", "iteration": 2})
    evaluation = {
        "iteration": 2,
        "result": "fail",
        "nextAction": "retry_generator",
        "testResults": [{"id": "TC-001", "status": "failed"}],
        "failedChecks": [{"name": "TC-001", "reason": "missing proof"}],
    }

    write_evaluation_json(task_dir, evaluation)
    stage = read_stage_state(task_dir, "verification_loop")

    assert stage["evaluation"]["nextAction"] == "retry_generator"
    assert read_evaluation_json(task_dir)["nextAction"] == "retry_generator"
    assert read_evaluation_json(task_dir)["testResults"][0]["id"] == "TC-001"

    # Migration compatibility: if an old/direct writer updates evaluation.json
    # after the stage-state projection, do not let stale stage state mask it.
    (task_dir / "evaluation.json").write_text(json.dumps({"result": "fail", "nextAction": "stop", "testResults": []}) + "\n")
    assert read_evaluation_json(task_dir)["nextAction"] == "stop"


def test_completion_stage_state_is_authoritative_terminal_source(tmp_path: Path) -> None:
    from orchestrator.completion import write_completion_ledger
    from orchestrator.state import task_has_authoritative_terminal_pass
    from orchestrator.state_reducer import derive_effective_state

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "evaluating", "iteration": 2})
    report = {
        "result": "pass",
        "completionVerdict": {"result": "pass"},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish"},
        "testResults": [{"id": "TC-001", "status": "pass"}],
    }

    write_completion_ledger(task_dir, report)
    (task_dir / "completion-report.json").write_text(json.dumps({"result": "fail"}) + "\n")
    stage = read_stage_state(task_dir, "summary")
    derived = derive_effective_state(task_dir)

    assert stage["completion"]["result"] == "pass"
    assert task_has_authoritative_terminal_pass(task_dir) is True
    assert derived["terminalAllowed"] is True
    assert derived["completionSource"] == "stages/summary-stage-state.json#completion"


def test_stale_completion_pass_rejected_when_verification_reopened(tmp_path: Path) -> None:
    """A completion pass superseded by a re-activated verification loop is stale.

    Every authoritative-terminal-pass consumer (resume guard, evaluation
    write-barrier, workflow-check terminal guard, state reducer) must reject it
    so a false finish cannot weld the task shut.
    """
    from orchestrator.completion import write_completion_ledger
    from orchestrator.state import (
        completion_pass_superseded_by_reopened_verification,
        task_has_authoritative_terminal_pass,
        write_evaluation_json,
    )
    from orchestrator.workflow_state import stage_state_path

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "finished", "nextAction": "finish", "iteration": 3})
    write_completion_ledger(task_dir, {
        "result": "pass",
        "completionVerdict": {"result": "pass"},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish"},
        "testResults": [{"id": "TC-001", "status": "pass"}],
    })
    # Force the summary stamp earlier than the reopened verification loop.
    summary_stage = read_stage_state(task_dir, "summary")
    summary_stage["updatedAt"] = "2026-06-12T18:16:17"
    stage_state_path(task_dir, "summary").write_text(json.dumps(summary_stage) + "\n")
    stage_state_path(task_dir, "verification_loop").write_text(json.dumps({
        "stage": "verification_loop",
        "status": "active",
        "updatedAt": "2026-06-12T18:47:57",
    }) + "\n")

    assert completion_pass_superseded_by_reopened_verification(task_dir) is True
    assert task_has_authoritative_terminal_pass(task_dir) is False

    # Evaluation write-barrier must NOT divert a fresh fail into advisory limbo.
    write_evaluation_json(task_dir, {
        "iteration": 4,
        "result": "fail",
        "summary": "reopened verification fail",
        "failedChecks": [],
        "nextAction": "retry_generator",
        "testResults": [],
    })
    evaluation = json.loads((task_dir / "evaluation.json").read_text())
    assert evaluation["result"] == "fail"
    assert evaluation["nextAction"] == "retry_generator"
    assert not (task_dir / "logs" / "advisory-evaluations").exists()

    # apply_evaluation_result must route to retry, not re-weld to finished.
    apply_evaluation_result(task_dir, evaluation)
    state = read_runtime_state(task_dir)
    assert state["status"] == "retry_pending"
    assert state["nextAction"] == "retry_generator"


def test_finalize_workflow_state_marks_terminal_pass_completed(tmp_path: Path) -> None:
    """After summary, an intermediate complete_task event must be finalized.

    The verification-loop pass routes through a ``complete_task`` event whose
    status is still ``running``; nothing afterwards marks the workflow control
    state ``completed``. ``finalize_workflow_state_if_terminal`` closes that gap
    once an authoritative terminal pass exists.
    """
    from orchestrator.completion import write_completion_ledger
    from orchestrator.workflow_state import finalize_workflow_state_if_terminal

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "evaluating", "iteration": 2})

    write_completion_ledger(task_dir, {
        "result": "pass",
        "completionVerdict": {"result": "pass"},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish"},
        "testResults": [{"id": "TC-001", "status": "pass"}],
    })

    # A stale routing event lands AFTER the completion ledger, dragging the
    # control state back to running/complete_task (the observed real-task bug).
    emit_workflow_event(task_dir, {
        "type": "phase_action_completed",
        "phase": "completion",
        "action": "complete_task",
        "nextAction": "complete_task",
        "iteration": 2,
        "reason": "phase_action_completed",
    })
    pre = read_workflow_state(task_dir)
    assert pre["status"] == "running"
    assert pre["currentAction"] == "complete_task"

    finalized = finalize_workflow_state_if_terminal(task_dir, reason="summary")
    assert finalized is not None
    assert finalized["status"] == "completed"
    assert finalized["currentAction"] == "finish_task"

    current = read_workflow_state(task_dir)
    assert current["status"] == "completed"
    assert current["currentAction"] == "finish_task"

    # Idempotent: a second call must not emit another event.
    events_before = workflow_events_path(task_dir).read_text().count("evt_")
    assert finalize_workflow_state_if_terminal(task_dir, reason="summary") is None
    assert workflow_events_path(task_dir).read_text().count("evt_") == events_before


def test_finalize_workflow_state_noop_without_terminal_pass(tmp_path: Path) -> None:
    """No terminal pass -> no terminal event, control state stays running."""
    from orchestrator.workflow_state import finalize_workflow_state_if_terminal

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "evaluating", "iteration": 2})
    emit_workflow_event(task_dir, {
        "type": "phase_action_completed",
        "phase": "completion",
        "action": "complete_task",
        "nextAction": "complete_task",
        "iteration": 2,
    })

    assert finalize_workflow_state_if_terminal(task_dir) is None
    assert read_workflow_state(task_dir)["status"] == "running"
