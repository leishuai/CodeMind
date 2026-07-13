"""CodeAutonomy workflow control-state model.

This module owns the agent/runtime-facing workflow state files:

- automind-workflow-state.json: the only live workflow control state.
- automind-workflow-events.jsonl: append-only state transition log.
- stages/<stage>-stage-state.json: stage-local control state.

It is intentionally separate from artifact files such as evaluation.json,
workflow.json, completion-report.json, reports, and checklist/guide markdown.
Those artifacts may influence events, but they are not live workflow state.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.phase_registry import (
    CONTROL_PHASE_REGISTRY,
    PHASE_REGISTRY,
    PHASE_TO_STAGE,
    STAGE_REGISTRY,
)
from orchestrator.state import read_runtime_state

WORKFLOW_STATE_SCHEMA = "automind.workflow_state.v1"
STAGE_STATE_SCHEMA = "automind.stage_state.v1"
WORKFLOW_EVENT_SCHEMA = "automind.workflow_event.v1"

WORKFLOW_STATE_FILE = "automind-workflow-state.json"
WORKFLOW_EVENTS_FILE = "automind-workflow-events.jsonl"

LEGACY_PHASE_MAP = {
    "planning": "plan",
    "delivery": "delivery",
    "evaluation": "evaluation",
    "terminal": "completion",
    "completion": "completion",
    "human_input": "pre_implementation_review",
    "unknown": "task_setup",
}

# Reverse mapping for the CLI / skill-mode display layer. The runtime/control
# state always operates on canonical phase names from CONTROL_PHASE_REGISTRY;
# external surfaces (phase-transition summary, current-session instructions,
# resume prompts) historically expose a smaller set of "macro" phase labels.
# All display labels MUST flow through ``display_phase`` so the mapping has a
# single source of truth.
DISPLAY_PHASE_MAP = {
    "plan": "planning",
    "brainstorm": "planning",
    "requirements": "planning",
    "testcases": "planning",
    "pre_implementation_review": "human_input",
    "delivery": "delivery",
    "evaluation": "evaluation",
    "completion": "terminal",
    "task_setup": "unknown",
    "context_load": "unknown",
    "environment_readiness": "unknown",
}


def display_phase(phase: str | None, *, fallback: str = "unknown") -> str:
    """Map canonical/legacy phase names to the macro display label.

    The display layer keeps the historical "planning/human_input/delivery/
    evaluation/terminal/unknown" vocabulary so existing CLI prompts, skill
    instructions and tests stay stable. Internally callers should hold the
    canonical phase, then call this once when crossing into the display layer.
    """
    raw = str(phase or "").strip()
    if not raw:
        return fallback
    if raw in DISPLAY_PHASE_MAP:
        return DISPLAY_PHASE_MAP[raw]
    if raw in LEGACY_PHASE_MAP:
        # Already a legacy display name; round-trip via the canonical name to
        # collapse synonyms (e.g. "completion" -> canonical -> "terminal").
        canonical = LEGACY_PHASE_MAP[raw]
        return DISPLAY_PHASE_MAP.get(canonical, raw)
    return fallback

ACTION_TO_PHASE = {
    "create_task": "task_setup",
    "load_context": "context_load",
    "check_readiness": "environment_readiness",
    "run_brainstorm": "brainstorm",
    "analyze_requirement": "requirements",
    "create_plan": "plan",
    "create_testcases": "testcases",
    "run_pre_implementation_review": "pre_implementation_review",
    "request_user_decision": "pre_implementation_review",
    "run_test_planner": "plan",
    "run_generator": "delivery",
    "retry_generator": "delivery",
    "retry_delivery": "delivery",
    "run_evaluator": "evaluation",
    "run_evaluation": "evaluation",
    "run_verification": "evaluation",
    "judge_evidence": "evaluation",
    "finish": "completion",
    "finish_task": "completion",
    "complete_task": "completion",
    "archive_task": "completion",
    "update_reuse_index": "completion",
    "wait_for_user": "pre_implementation_review",
    "wait_for_tool": "environment_readiness",
    "pause_for_external": "pre_implementation_review",
    "fail_task": "evaluation",
}

NEXT_ACTION_NORMALIZATION = {
    "finish": "finish_task",
    "run_evaluator": "run_evaluation",
    "run_test_planner": "create_plan",
    "ask_user": "wait_for_user",
    "pause_for_external": "wait_for_user",
    "stop": "fail_task",
    "stop_blocked": "fail_task",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def workflow_state_path(task_dir: Path) -> Path:
    return task_dir / WORKFLOW_STATE_FILE


def workflow_events_path(task_dir: Path) -> Path:
    return task_dir / WORKFLOW_EVENTS_FILE


def stage_state_path(task_dir: Path, stage: str) -> Path:
    meta = STAGE_REGISTRY.get(stage) or {}
    rel = str(meta.get("stateFile") or f"stages/{stage}-stage-state.json")
    return task_dir / rel


def read_workflow_state(task_dir: Path) -> dict[str, Any]:
    return _safe_json(workflow_state_path(task_dir))


def read_stage_state(task_dir: Path, stage: str) -> dict[str, Any]:
    return _safe_json(stage_state_path(task_dir, stage))


def _event_lines(task_dir: Path) -> list[dict[str, Any]]:
    path = workflow_events_path(task_dir)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events


def _next_event_id(task_dir: Path) -> str:
    max_seen = 0
    for event in _event_lines(task_dir):
        raw = str(event.get("id") or "")
        if raw.startswith("evt_"):
            try:
                max_seen = max(max_seen, int(raw.split("_", 1)[1]))
            except Exception:
                pass
    return f"evt_{max_seen + 1:06d}"


def normalize_action(action: str | None) -> str | None:
    if action is None:
        return None
    value = str(action or "").strip()
    if not value:
        return None
    return NEXT_ACTION_NORMALIZATION.get(value, value)


def normalize_phase(phase: str | None, *, action: str | None = None, default: str = "task_setup") -> str:
    raw = str(phase or "").strip()
    if raw in CONTROL_PHASE_REGISTRY:
        return raw
    if raw in LEGACY_PHASE_MAP:
        return LEGACY_PHASE_MAP[raw]
    act = normalize_action(action)
    if act and act in ACTION_TO_PHASE:
        return ACTION_TO_PHASE[act]
    return default


def stage_for_phase(phase: str) -> str:
    normalized = normalize_phase(phase)
    return str(PHASE_TO_STAGE.get(normalized) or "initialization")


def default_planned_next_phase(phase: str | None) -> str | None:
    """Return the canonical "next" phase declared in PHASE_REGISTRY.

    Used as the deterministic plan-link projection when an event does not
    carry an explicit ``plannedNextPhase``. Returns ``None`` for terminal or
    unknown phases so callers can leave the field blank instead of guessing.
    """
    if not phase:
        return None
    canonical = normalize_phase(phase)
    meta = PHASE_REGISTRY.get(canonical) or CONTROL_PHASE_REGISTRY.get(canonical) or {}
    nxts = meta.get("next") if isinstance(meta.get("next"), list) else []
    if not nxts:
        return None
    return str(nxts[0])


def owner_for_phase(phase: str, *, fallback: str = "runtime") -> str:
    meta = CONTROL_PHASE_REGISTRY.get(normalize_phase(phase)) or {}
    owners = meta.get("owners") if isinstance(meta.get("owners"), list) else []
    return str(owners[0]) if owners else fallback


def _workflow_status_for(action: str | None, phase: str, event_type: str, explicit: str | None) -> str:
    if explicit:
        return str(explicit)
    action = normalize_action(action)
    if event_type == "workflow_completed" or action == "finish_task":
        return "completed"
    if event_type == "workflow_failed" or action == "fail_task":
        return "failed"
    if action == "wait_for_user":
        return "waiting_user"
    if action == "wait_for_tool":
        return "waiting_tool"
    return "running"


def _stage_status_for(workflow_status: str, event: dict[str, Any]) -> str:
    explicit = event.get("stageStatus")
    if explicit:
        return str(explicit)
    if workflow_status == "completed":
        return "completed"
    if workflow_status == "failed":
        return "failed"
    if workflow_status == "waiting_user":
        return "waiting_user"
    if workflow_status == "waiting_tool":
        return "waiting_tool"
    if str(event.get("reason") or "") == "verification_failed_retryable" or event.get("retryable") is True:
        return "failed_retryable" if normalize_phase(event.get("phase"), action=event.get("action")) == "evaluation" else "active"
    return "active"


def append_workflow_event(task_dir: Path, event: dict[str, Any]) -> dict[str, Any]:
    """Append a workflow event and return the normalized event."""
    task_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(event or {})
    payload.setdefault("schema", WORKFLOW_EVENT_SCHEMA)
    payload.setdefault("id", _next_event_id(task_dir))
    payload.setdefault("at", _now())
    payload.setdefault("type", "phase_action_completed")
    payload["action"] = normalize_action(payload.get("action")) or payload.get("action")
    payload["nextAction"] = normalize_action(payload.get("nextAction"))
    if payload.get("phase"):
        payload["phase"] = normalize_phase(payload.get("phase"), action=payload.get("action"))
        payload.setdefault("stage", stage_for_phase(payload["phase"]))
    if payload.get("nextPhase"):
        payload["nextPhase"] = normalize_phase(payload.get("nextPhase"), action=payload.get("nextAction"))
    if payload.get("plannedNextPhase"):
        payload["plannedNextPhase"] = normalize_phase(payload.get("plannedNextPhase"))
    path = workflow_events_path(task_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return payload


def _build_state_from_event(task_dir: Path, event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    previous = read_workflow_state(task_dir)
    prev_version = int(previous.get("version") or 0) if previous else 0
    prev_iteration = int(previous.get("iteration") or 0) if str(previous.get("iteration") or "0").isdigit() else 0

    event_type = str(event.get("type") or "phase_action_completed")
    next_action = normalize_action(event.get("nextAction") or event.get("action"))
    current_action = normalize_action(event.get("currentAction") or event.get("nextAction") or event.get("action"))
    current_phase = normalize_phase(event.get("currentPhase") or event.get("nextPhase") or event.get("phase"), action=current_action)
    current_stage = stage_for_phase(current_phase)
    planned_next_phase = event.get("plannedNextPhase")
    if planned_next_phase:
        planned_next_phase = normalize_phase(planned_next_phase)
    else:
        # Fall back to the canonical phase chain (PHASE_REGISTRY.next) so the
        # plan link is always populated when the registry knows a follow-up,
        # and stays empty for terminal/unknown phases.
        planned_next_phase = default_planned_next_phase(current_phase)

    owner = str(event.get("currentOwner") or event.get("nextOwner") or owner_for_phase(current_phase, fallback=str(event.get("owner") or "runtime")))
    workflow_status = _workflow_status_for(current_action, current_phase, event_type, event.get("status"))

    iteration = event.get("iteration")
    if iteration is None:
        if current_stage == "verification_loop":
            iteration = max(prev_iteration, 1)
            if event_type == "iteration_failed_retryable" or (event.get("reason") == "verification_failed_retryable" and current_phase == "delivery"):
                iteration = prev_iteration + 1 if prev_iteration else 2
        else:
            iteration = prev_iteration
    try:
        iteration = int(iteration or 0)
    except Exception:
        iteration = 0

    workflow = {
        "schema": WORKFLOW_STATE_SCHEMA,
        "taskId": task_dir.name,
        "version": prev_version + 1,
        "status": workflow_status,
        "currentStage": current_stage,
        "currentPhase": current_phase,
        "currentAction": current_action,
        "currentOwner": owner,
        "nextAction": next_action,
        "nextPhase": normalize_phase(event.get("nextPhase") or current_phase, action=next_action),
        "plannedNextPhase": planned_next_phase,
        "iteration": iteration,
        "stateHealth": str(event.get("stateHealth") or "ok"),
        "lastEventId": event.get("id"),
        "updatedAt": _now(),
    }

    stage_prev = read_stage_state(task_dir, current_stage)
    stage_version = int(stage_prev.get("version") or 0) if stage_prev else 0
    stage_state: dict[str, Any] = {
        "schema": STAGE_STATE_SCHEMA,
        "stage": current_stage,
        "version": stage_version + 1,
        "status": _stage_status_for(workflow_status, event),
        "currentPhase": current_phase,
        "currentAction": current_action,
        "owner": owner,
        "nextAction": next_action,
        "nextPhase": workflow["nextPhase"],
        "plannedNextPhase": planned_next_phase,
        "lastEventId": event.get("id"),
        "updatedAt": workflow["updatedAt"],
    }
    if current_stage == "verification_loop":
        stage_state["iteration"] = {
            "current": iteration,
            "max": event.get("maxIterations") or previous.get("maxIterations") or (read_runtime_state(task_dir) or {}).get("maxIterations"),
            "phase": current_phase,
            "lastResult": event.get("result") or event.get("lastResult"),
            "retryable": event.get("retryable") if "retryable" in event else event.get("reason") == "verification_failed_retryable",
            "updatedAt": workflow["updatedAt"],
        }
        evaluation_payload = event.get("evaluation") if isinstance(event.get("evaluation"), dict) else None
        if evaluation_payload:
            stage_state["evaluation"] = evaluation_payload
        elif isinstance(stage_prev.get("evaluation"), dict):
            stage_state["evaluation"] = stage_prev["evaluation"]
    elif current_stage == "summary":
        completion_payload = event.get("completion") if isinstance(event.get("completion"), dict) else None
        if completion_payload:
            stage_state["completion"] = completion_payload
        elif isinstance(stage_prev.get("completion"), dict):
            stage_state["completion"] = stage_prev["completion"]
    return workflow, stage_state


def apply_workflow_event(task_dir: Path, event: dict[str, Any]) -> dict[str, Any]:
    """Apply an already appended event to stage state and workflow state."""
    workflow, stage_state = _build_state_from_event(task_dir, event)
    _atomic_write_json(stage_state_path(task_dir, stage_state["stage"]), stage_state)
    _atomic_write_json(workflow_state_path(task_dir), workflow)
    return workflow


def emit_workflow_event(task_dir: Path, event: dict[str, Any]) -> dict[str, Any]:
    """Append an event and update all workflow control-state projections."""
    appended = append_workflow_event(task_dir, event)
    return apply_workflow_event(task_dir, appended)


def finalize_workflow_state_if_terminal(task_dir: Path, *, reason: str = "summary") -> dict[str, Any] | None:
    """Emit a terminal ``workflow_completed`` event when the task is finished.

    The verification-loop routing emits an intermediate ``complete_task`` event
    (status=running) that hands control to the summary stage. Nothing afterwards
    marks the workflow control state ``completed``, so ``automind-workflow-state
    .json`` would otherwise stay stuck at status=running / currentAction=
    complete_task even though the loop is done. This closes that gap once the
    task has an authoritative terminal pass.

    Returns the new workflow state, or ``None`` when the task is not (yet) a
    terminal pass or the workflow state is already completed.
    """
    from orchestrator.state import task_has_authoritative_terminal_pass

    if not task_has_authoritative_terminal_pass(task_dir):
        return None
    current = read_workflow_state(task_dir)
    if str(current.get("status") or "").strip().lower() == "completed":
        return None
    runtime = read_runtime_state(task_dir) or {}
    return emit_workflow_event(task_dir, {
        "type": "workflow_completed",
        "phase": "completion",
        "action": "finish_task",
        "nextAction": "finish_task",
        "nextPhase": "completion",
        "plannedNextPhase": None,
        "status": "completed",
        "iteration": runtime.get("iteration", current.get("iteration", 0)),
        "reason": f"summary_finalized:{reason}",
    })


def _last_known_good_phase(task_dir: Path) -> str | None:
    """Return the most recent canonical phase observed in durable artifacts.

    Repair flows must not regress to ``task_setup`` when the task has clearly
    progressed past initialization. We look at the existing workflow state,
    runtime-state, and earlier events (newest first) for the first canonical
    phase that is not ``task_setup``.
    """
    workflow = read_workflow_state(task_dir) or {}
    candidates: list[Any] = [workflow.get("currentPhase"), workflow.get("nextPhase")]
    runtime = read_runtime_state(task_dir) or {}
    candidates.extend([runtime.get("phase"), runtime.get("lastTickPhase")])
    for ev in reversed(_event_lines(task_dir)):
        candidates.extend([ev.get("currentPhase"), ev.get("phase"), ev.get("nextPhase")])
    for raw in candidates:
        if not raw:
            continue
        canonical = normalize_phase(raw, default="task_setup")
        if canonical and canonical != "task_setup":
            return canonical
    return None


def reconcile_workflow_state(task_dir: Path, *, reason: str = "reconcile") -> dict[str, Any]:
    """Rebuild workflow state from the latest valid event, or seed from runtime-state.

    Reconciliation never asks the user and never treats state drift as a blocker.
    It marks the rebuilt state degraded/reconciling only as machine-readable
    health, then lets the caller continue the CodeAutonomy loop.

    When falling back to runtime-state, we anchor on the most recent known-good
    canonical phase (existing workflow state -> runtime-state -> earlier events)
    rather than silently retreating to ``task_setup``.
    """
    events = _event_lines(task_dir)
    if events:
        latest = dict(events[-1])
        latest["stateHealth"] = "degraded" if reason else latest.get("stateHealth", "ok")
        return apply_workflow_event(task_dir, latest)

    runtime = read_runtime_state(task_dir) or {}
    action = normalize_action(runtime.get("nextAction")) or "create_task"
    fallback_phase = _last_known_good_phase(task_dir) or "task_setup"
    phase = normalize_phase(
        runtime.get("phase") or runtime.get("lastTickPhase"),
        action=action,
        default=fallback_phase,
    )
    return emit_workflow_event(task_dir, {
        "type": "state_reconciled_from_runtime",
        "phase": phase,
        "action": action,
        "nextAction": action,
        "nextPhase": phase,
        "status": "running" if runtime.get("status") not in {"finished", "completed"} else "completed",
        "iteration": runtime.get("iteration", 0),
        "stateHealth": "reconciling",
        "reason": reason,
        "source": "runtime-state fallback; no workflow events existed",
    })


def ensure_workflow_state(task_dir: Path) -> dict[str, Any]:
    state = read_workflow_state(task_dir)
    if state:
        return state
    return reconcile_workflow_state(task_dir, reason="missing_workflow_state")


def workflow_state_consistent(task_dir: Path) -> bool:
    workflow = read_workflow_state(task_dir)
    if not workflow:
        return False
    stage = str(workflow.get("currentStage") or "")
    stage_state = read_stage_state(task_dir, stage)
    if not stage_state:
        return False
    keys = ["lastEventId", "currentPhase", "nextPhase"]
    return all(workflow.get(k) == stage_state.get(k) for k in keys) and workflow.get("currentStage") == stage_state.get("stage")
