"""Evaluation nextAction -> runtime-state transition helpers."""
from __future__ import annotations

from pathlib import Path

from orchestrator.state import task_has_authoritative_terminal_pass, update_runtime_state
from orchestrator.phase_transition import refresh_phase_transition_summary
from orchestrator.workflow_state import emit_workflow_event


def apply_evaluation_result(task_dir: Path, evaluation: dict):
    """Update runtime-state.json from evaluation.json nextAction and refresh phase summary.

    Iteration semantics (single source of truth):
    - ``evaluation.iteration`` is the attempt that *just finished/failed*.
    - ``runtime-state.iteration`` mirrors that attempt; it is not bumped here.
    - ``automind-workflow-state.iteration`` is the *active/next* attempt being
      routed. On retry the workflow event therefore advances to
      ``evaluation.iteration + 1`` so downstream consumers (Generator,
      iteration-tick) see the new attempt number. Other callers (the
      orchestrator main loop and ``tick_iteration``) never +1 the same
      boundary, so the counter is monotonic per real attempt.
    """
    next_action = evaluation.get("nextAction", "stop")
    iteration = evaluation.get("iteration", 0)
    result = evaluation.get("result", "fail")

    if task_has_authoritative_terminal_pass(task_dir) and next_action != "finish":
        update_runtime_state(
            task_dir,
            status="finished",
            iteration=iteration,
            currentOwner="automind",
            nextAction="finish",
            lastResult="pass",
            terminalWriteBarrier={
                "blockedNextAction": next_action,
                "blockedResult": result,
                "reason": "authoritative_completion_pass",
            },
        )
        refresh_phase_transition_summary(task_dir)
        return

    workflow_event = {
        "type": "phase_action_completed",
        "phase": "evaluation",
        "action": "judge_evidence",
        "owner": "evaluator",
        "iteration": iteration,
        "result": result,
        "reason": f"evaluation_{next_action}",
    }
    if next_action == "finish":
        update_runtime_state(task_dir, status="finished", iteration=iteration, currentOwner="automind", nextAction="finish", lastResult=result)
        workflow_event.update(type="phase_action_completed", nextAction="complete_task", nextPhase="completion", plannedNextPhase=None, reason="verification_passed")
    elif next_action == "retry_generator":
        update_runtime_state(task_dir, status="retry_pending", iteration=iteration, currentOwner="generator", nextAction="retry_generator", lastResult=result, askUserQuestion=None, pendingQuestion=None)
        # evaluation.json.iteration is the attempt that just failed. Workflow
        # control-state iteration is the active/next attempt being routed, so a
        # retry points at the next delivery iteration.
        next_iteration = int(iteration or 0) + 1
        workflow_event.update(type="iteration_failed_retryable", iteration=next_iteration, nextAction="retry_generator", nextPhase="delivery", plannedNextPhase="evaluation", retryable=True, reason="verification_failed_retryable")
    elif next_action == "replan":
        update_runtime_state(task_dir, status="replan_pending", iteration=iteration, currentOwner="planner", nextAction="run_test_planner", lastResult=result, askUserQuestion=None, pendingQuestion=None)
        workflow_event.update(nextAction="create_plan", nextPhase="plan", plannedNextPhase="pre_implementation_review", reason="verification_requests_replan")
    elif next_action == "ask_user":
        update_runtime_state(task_dir, status="human_input_pending", iteration=iteration, currentOwner="human", nextAction="ask_user", lastResult=result, askUserQuestion=evaluation.get("askUserQuestion"))
        workflow_event.update(nextAction="wait_for_user", nextPhase="pre_implementation_review", plannedNextPhase="delivery", reason="verification_requires_user")
    elif next_action == "pause_for_external":
        update_runtime_state(
            task_dir,
            status="human_input_pending",
            iteration=iteration,
            currentOwner="human",
            nextAction="pause_for_external",
            lastResult=result,
            pauseReason=evaluation.get("pauseReason") or evaluation.get("askUserQuestion"),
        )
        workflow_event.update(nextAction="wait_for_user", nextPhase="pre_implementation_review", plannedNextPhase="delivery", reason="verification_paused_for_external")
    else:
        normalized_stop = "stop_blocked" if next_action in {"stop", "stop_blocked"} else "stop"
        update_runtime_state(task_dir, status="failed", iteration=iteration, currentOwner="supervisor", nextAction=normalized_stop, lastResult=result)
        workflow_event.update(type="workflow_failed", nextAction="fail_task", nextPhase="evaluation", plannedNextPhase=None, reason="verification_failed_final")
    workflow_event["evaluation"] = {
        "result": result,
        "nextAction": next_action,
        "iteration": iteration,
        "testResults": evaluation.get("testResults") if isinstance(evaluation.get("testResults"), list) else [],
        "failedChecks": evaluation.get("failedChecks") if isinstance(evaluation.get("failedChecks"), list) else [],
        "askUserQuestion": evaluation.get("askUserQuestion") if isinstance(evaluation.get("askUserQuestion"), dict) else None,
        "pauseReason": evaluation.get("pauseReason"),
        "evidence": evaluation.get("evidence"),
        "evidenceIndex": evaluation.get("evidenceIndex"),
        "updatedAt": evaluation.get("updatedAt"),
    }
    emit_workflow_event(task_dir, workflow_event)
    refresh_phase_transition_summary(task_dir)
