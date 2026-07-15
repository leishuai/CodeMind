"""Build shared next-step instructions for skill mode and TUI resume prompts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.phase_transition import refresh_phase_transition_summary
from orchestrator.session.ask_user import normalize_pending_question
from orchestrator.session.answers import latest_pending_answer_matches_question, read_answers
from orchestrator.state import read_runtime_state
from orchestrator.state_reducer import reconcile_task_state
from orchestrator.workflow import check_workflow_consistency
from orchestrator.workflow_state import ensure_workflow_state


def _expected_phase(workflow_state: dict[str, Any]) -> str | None:
    expected = workflow_state.get("expectedNext") if isinstance(workflow_state.get("expectedNext"), list) else []
    for item in expected:
        if isinstance(item, dict) and item.get("phase"):
            return str(item.get("phase"))
    phase = workflow_state.get("currentPhase")
    return str(phase) if phase else None


def _build_effective_next(
    *,
    next_action: str,
    workflow_ok: bool,
    workflow_state: dict[str, Any],
    pending_question: dict[str, Any] | None,
) -> dict[str, Any]:
    expected_phase = _expected_phase(workflow_state)
    issue_count = int(workflow_state.get("issueCount") or 0) if isinstance(workflow_state, dict) else 0
    if pending_question:
        return {
            "action": "ask_user",
            "phase": pending_question.get("fromPhase") or expected_phase or "human_input",
            "summary": "ask_user: record the pending user answer before continuing",
        }
    if not workflow_ok:
        phase = expected_phase or "planning"
        return {
            "action": "resolve_workflow_blockers",
            "phase": phase,
            "summary": f"resolve {phase} blockers before runtime-state nextAction={next_action}",
            "blockerCount": issue_count,
        }
    phase = expected_phase or next_action
    return {
        "action": next_action,
        "phase": phase,
        "summary": f"continue workflow at {phase}",
        "blockerCount": 0,
    }


def _fresh_workflow_check_pass(task_dir: Path) -> dict[str, Any] | None:
    """Return latest workflow-check-current pass report when it is fresh enough.

    TUI rendering can race long-running Planner/Generator turns: a stale in-memory
    workflow report may say "blocked at brainstorm" while a later workflow-check
    has already passed and the runtime/workflow state has advanced. In that case
    the snapshot should not keep showing the old blocker instruction.
    """
    report_path = task_dir / "logs" / "workflow-check-current.log"
    if not report_path.exists():
        return None
    try:
        report = json.loads(report_path.read_text(errors="replace"))
    except Exception:
        return None
    if str(report.get("result") or "").lower() != "pass" or report.get("issues"):
        return None
    report_mtime = report_path.stat().st_mtime
    relevant = [
        task_dir / "Brainstorm.md",
        task_dir / "Requirements.md",
        task_dir / "TestCases.md",
        task_dir / "Plan.md",
        task_dir / "brainstorm.json",
        task_dir / "requirements.json",
        task_dir / "testcases.json",
        task_dir / "plan.json",
        task_dir / "pre-implementation-review.json",
        task_dir / "workflow.json",
    ]
    newest_artifact = max((p.stat().st_mtime for p in relevant if p.exists()), default=0.0)
    if report_mtime + 0.001 < newest_artifact:
        return None
    return report


def build_next_instruction(task_code: str, task_dir: Path) -> dict[str, Any]:
    state_authority = reconcile_task_state(task_dir, reason="continue_instruction")
    workflow_control_state = ensure_workflow_state(task_dir)
    phase_transition = refresh_phase_transition_summary(task_dir)
    state = read_runtime_state(task_dir) or {}
    workflow_ok, workflow_report = check_workflow_consistency(task_code)
    missing_task_false_negative = (
        not workflow_ok
        and task_dir.exists()
        and isinstance(workflow_report, dict)
        and any(str(issue).startswith("Task does not exist:") for issue in workflow_report.get("issues") or [])
    )
    fresh_workflow_pass = None if workflow_ok else _fresh_workflow_check_pass(task_dir)
    if fresh_workflow_pass is not None or missing_task_false_negative:
        workflow_ok = True
        workflow_report = fresh_workflow_pass or {
            "result": "pass",
            "issues": [],
            "warnings": ["workflow-check skipped stale global TASKS_DIR task lookup; explicit task_dir exists"],
            "workflowState": {"result": "pass", "issueCount": 0, "warningCount": 1, "expectedNext": [], "staleTaskLookupFallback": True},
        }
    workflow_state = workflow_report.get("workflowState", {}) if isinstance(workflow_report, dict) else {}
    pending_question = normalize_pending_question(task_dir)
    answers = read_answers(task_dir)
    next_action = state.get("nextAction") or "run_generator"
    phase_transition_stale_task_lookup = any(
        str(item).startswith("Task does not exist:") for item in (phase_transition.get("basis") or [])
    )
    if phase_transition_stale_task_lookup and task_dir.exists():
        phase_transition = {
            "currentPhase": workflow_control_state.get("currentPhase") or state.get("currentPhase"),
            "currentStatus": state.get("status"),
            "currentOwner": state.get("currentOwner"),
            # Use the immediate workflow-control route first. plannedNextPhase
            # is the phase after the current handoff (for example delivery ->
            # evaluation), and treating it as nextPhase creates false route drift
            # in phase-gate.
            "nextPhase": workflow_control_state.get("nextPhase") or state.get("nextPhase") or workflow_control_state.get("plannedNextPhase") or str(next_action),
            "nextAction": state.get("nextAction") or workflow_control_state.get("nextAction") or str(next_action),
            "nextOwner": state.get("currentOwner") or workflow_control_state.get("currentOwner"),
            "reason": "explicit task_dir exists; ignore stale cross-workspace task lookup",
            "basis": ["explicit task_dir exists", "workflow helper task lookup was stale"],
        }
    effective_next = {
        "action": phase_transition.get("nextAction") or str(next_action),
        "phase": phase_transition.get("nextPhase") or _expected_phase(workflow_state) or str(next_action),
        "summary": phase_transition.get("reason") or f"continue workflow at {phase_transition.get('nextPhase') or next_action}",
        "basis": phase_transition.get("basis") or [],
        "owner": phase_transition.get("nextOwner"),
    }
    if pending_question or not workflow_ok:
        effective_next = _build_effective_next(
            next_action=str(next_action),
            workflow_ok=workflow_ok,
            workflow_state=workflow_state,
            pending_question=pending_question,
        )

    pending_answer_for_question = latest_pending_answer_matches_question(task_dir, pending_question)

    if pending_question:
        owner = str(state.get("currentOwner") or "")
        if pending_answer_for_question:
            instruction = (
                "The pending CodeMind question already has a recorded answer awaiting delivery/application. "
                "Do not ask the user again; wait for the current agent turn to apply the answer and update artifacts."
            )
        elif owner and owner != "human":
            instruction = (
                f"A pending CodeMind question exists, but the current owner is still {owner}. "
                "Wait for the current agent turn to finish; then ask the user and record the answer with automind answer before continuing."
            )
        else:
            instruction = "Ask the user the pending CodeMind question, then record the answer with automind answer before continuing."
    elif not workflow_ok:
        phase = effective_next.get("phase") or "planning"
        blocker_count = effective_next.get("blockerCount", 0)
        instruction = (
            f"Workflow is blocked at {phase} ({blocker_count} blocker(s)); "
            f"resolve this before following runtime-state nextAction={next_action}. "
            "Refine the owning artifact, rerun workflow-check, and do not enter Generator until it passes."
        )
        state_status = str(state.get("status") or "").strip().lower()
        previous_state = state_authority.get("previousTaskState") if isinstance(state_authority, dict) else {}
        previous_status = str((previous_state or {}).get("status") or "").strip().lower()
        previous_next = str((previous_state or {}).get("nextAction") or "").strip().lower()
        terminal_drift = bool(isinstance(state_authority, dict) and state_authority.get("terminalStateObserved") and not state_authority.get("terminalAllowed"))
        if (next_action in {"done", "finish", "completed"} or state_status in {"completed", "finished"} or previous_next in {"done", "finish", "completed"} or previous_status in {"completed", "finished"} or terminal_drift):
            instruction += (
                " Do not trust runtime-state nextAction=done or completed/finished wording when workflow-check is red. "
                "This is a false-finish/reopen case. Produce a valid evaluation.json (result, nextAction, testResults), "
                "route by the effectiveNext phase, and only let completion-check decide finish."
            )
    else:
        phase = effective_next.get("phase") or next_action
        if phase == "completion":
            instruction = (
                "Continue CodeMind workflow. Effective next gate: completion-check. "
                "Continue Generator/Evaluator work as needed, and only mark finish after completion-check passes."
            )
        else:
            instruction = (
                f"Continue CodeMind workflow. Effective next phase: {phase}. "
                "Follow Plan.md/TestCases.md and finish only after completion-check passes."
            )

    latest_answer = answers[-1] if answers else None
    latest_delivery_status = str(((latest_answer or {}).get("delivery") or {}).get("status") or "") if latest_answer else ""
    if latest_answer and latest_delivery_status not in {"delivered", "applied"}:
        instruction += " Include the latest user answer in the next agent invocation prompt and update artifacts accordingly."

    return {
        "result": "ok",
        "task": task_code,
        "taskDir": str(task_dir),
        "status": state.get("status"),
        "currentOwner": state.get("currentOwner"),
        "workflowControlState": workflow_control_state,
        "phaseSummary": phase_transition,
        "stateSummary": phase_transition,  # compatibility response key; not runtime-state.json#stateSummary
        "effectiveNext": effective_next,
        "nextAction": next_action,
        "nextActionPrompt": instruction,
        "pendingQuestion": pending_question,
        "latestUserAnswer": latest_answer,
        "stateAuthority": state_authority,
        "workflowSignal": workflow_state,
        "workflowState": workflow_state,
        "terminalGuard": workflow_report.get("terminalGuard", {}) if isinstance(workflow_report, dict) else {},
        "checklist": [
            "Read automind-workflow-state.json (workflowControlState) before deciding the next macro phase.",
            "Prefer workflowControlState/phaseSummary/effectiveNext over raw runtime-state fields when they disagree; stateSummary is a compatibility response key only.",
            "Treat workflowSignal/workflowState as local resolver input/debug context, not macro phase truth.",
            "Read the current phase guideRef and required upstream artifacts.",
            "Run workflow-check after changing Phase 2 artifacts.",
            "Run completion-check before claiming finish.",
            "If ask_user is required, record the answer with automind answer and pass it to the next agent invocation.",
        ],
        "phaseChecklist": phase_transition.get("checklist") or [],
        "checkboxMarkdown": phase_transition.get("checkboxMarkdown") or [],
    }
