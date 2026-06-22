"""Build shared next-step instructions for skill mode and TUI resume prompts."""
from __future__ import annotations

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


def build_next_instruction(task_code: str, task_dir: Path) -> dict[str, Any]:
    state_authority = reconcile_task_state(task_dir, reason="continue_instruction")
    workflow_control_state = ensure_workflow_state(task_dir)
    phase_transition = refresh_phase_transition_summary(task_dir)
    state = read_runtime_state(task_dir) or {}
    workflow_ok, workflow_report = check_workflow_consistency(task_code)
    workflow_state = workflow_report.get("workflowState", {}) if isinstance(workflow_report, dict) else {}
    pending_question = normalize_pending_question(task_dir)
    answers = read_answers(task_dir)
    next_action = state.get("nextAction") or "run_generator"
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
                "The pending AutoMind question already has a recorded answer awaiting delivery/application. "
                "Do not ask the user again; wait for the current agent turn to apply the answer and update artifacts."
            )
        elif owner and owner != "human":
            instruction = (
                f"A pending AutoMind question exists, but the current owner is still {owner}. "
                "Wait for the current agent turn to finish; then ask the user and record the answer with automind answer before continuing."
            )
        else:
            instruction = "Ask the user the pending AutoMind question, then record the answer with automind answer before continuing."
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
                "Continue AutoMind workflow. Effective next gate: completion-check. "
                "Continue Generator/Evaluator work as needed, and only mark finish after completion-check passes."
            )
        else:
            instruction = (
                f"Continue AutoMind workflow. Effective next phase: {phase}. "
                "Follow Plan.md/TestCases.md and finish only after completion-check passes."
            )

    latest_answer = answers[-1] if answers else None
    if latest_answer and latest_answer.get("delivery", {}).get("status") != "delivered":
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
