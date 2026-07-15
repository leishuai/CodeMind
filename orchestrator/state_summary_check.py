"""Consistency check and repair for workflow control-state compatibility projections."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.phase_transition import build_phase_transition_summary, refresh_phase_transition_summary
from orchestrator.state import read_runtime_state
from orchestrator.state_reducer import derive_effective_state, reconcile_task_state
from orchestrator.workflow_state import ensure_workflow_state, reconcile_workflow_state, workflow_state_consistent

SUMMARY_COMPARE_KEYS = (
    "schemaVersion",
    "taskCode",
    "currentPhase",
    "currentStatus",
    "currentOwner",
    "nextPhase",
    "nextAction",
    "nextOwner",
    "reason",
)


def _subset(data: dict[str, Any], keys: tuple[str, ...] = SUMMARY_COMPARE_KEYS) -> dict[str, Any]:
    return {key: data.get(key) for key in keys if key in data}


def check_state_summary(task_dir: Path, *, repair: bool = False, reason: str = "state_summary_check") -> dict[str, Any]:
    """Validate workflow control-state and remove obsolete stateSummary mirrors.

    This checker is self-healing when ``repair=True``: it reconciles runtime
    route fields from durable source signals, verifies/repairs
    ``automind-workflow-state.json`` against the active stage state, then
    removes obsolete migration mirrors such as ``runtime-state.json.stateSummary``.
    It should not route to ask_user or stop by itself; any remaining issue is a
    bug report / warning for the caller, not a user-facing decision request.

    Repair contract (single ordered self-heal pass; CodeMind never ``ask_user``s
    from this checker):

    1. ``derive_effective_state`` is the first/source-of-truth pass.
    2. ``reconcile_task_state`` rewrites runtime-state route fields if drifted.
    3. ``reconcile_workflow_state`` rebuilds the control-state file from the
       latest event or anchors on the last-known-good canonical phase.
    4. ``refresh_phase_transition_summary`` clears obsolete mirrors and seeds
       a projection event only when one is missing.

    Each step reads the previous step's output, so callers should not chain
    additional repair primitives on top of this entry point.
    """
    issues: list[str] = []
    warnings: list[str] = []
    repairs: list[str] = []

    task_dir = Path(task_dir)
    state = read_runtime_state(task_dir) or {}
    if not state:
        return {
            "result": "fail",
            "issues": ["runtime-state.json missing or empty"],
            "warnings": [],
            "repairs": [],
            "effective": {},
            "expectedSummary": {},
            "stateSummary": {},
            "workflowControlState": {},
        }

    derived = derive_effective_state(task_dir)
    effective = derived.get("effective") if isinstance(derived.get("effective"), dict) else {}
    if any(state.get(k) != effective.get(k) for k in ("status", "currentOwner", "nextAction")):
        issues.append("runtime-state route differs from derived effective state")
        if repair:
            reconcile_task_state(task_dir, reason=reason)
            repairs.append("reconciled runtime-state route from derived effective state")
            state = read_runtime_state(task_dir) or {}
            derived = derive_effective_state(task_dir)
            effective = derived.get("effective") if isinstance(derived.get("effective"), dict) else {}

    workflow_control_state = ensure_workflow_state(task_dir)
    workflow_needs_repair = False
    if not workflow_control_state:
        issues.append("automind-workflow-state.json missing or empty")
        workflow_needs_repair = True
    elif not workflow_state_consistent(task_dir):
        issues.append("automind workflow control state differs from active stage state")
        workflow_needs_repair = True
    elif workflow_control_state.get("stateHealth") in {"reconciling", "degraded"}:
        warnings.append(f"workflow control state health is {workflow_control_state.get('stateHealth')}")

    expected = build_phase_transition_summary(task_dir)
    actual = state.get("stateSummary") if isinstance(state.get("stateSummary"), dict) else {}
    if actual:
        issues.append("obsolete runtime-state.json stateSummary exists")
        if _subset(actual) != _subset(expected):
            warnings.append("obsolete runtime-state.json stateSummary differs from deterministic resolver")

    if (task_dir / "phase-transition-summary.json").exists():
        issues.append("obsolete phase-transition-summary.json exists")
    if "phaseTransition" in state:
        issues.append("obsolete runtime-state.phaseTransition exists")

    if repair and issues:
        if workflow_needs_repair:
            reconcile_workflow_state(task_dir, reason=reason)
            repairs.append("reconciled automind workflow control state from latest event")
        refresh_phase_transition_summary(task_dir, update_runtime_projection=False)
        repairs.append("removed obsolete runtime-state/stateSummary and phase summary mirror artifacts")
        state = read_runtime_state(task_dir) or {}
        derived = derive_effective_state(task_dir)
        effective = derived.get("effective") if isinstance(derived.get("effective"), dict) else {}
        expected = build_phase_transition_summary(task_dir)
        actual = state.get("stateSummary") if isinstance(state.get("stateSummary"), dict) else {}
        issues = []
        workflow_control_state = ensure_workflow_state(task_dir)
        if workflow_control_state.get("stateHealth") in {"reconciling", "degraded"}:
            warnings.append(f"workflow control state health is {workflow_control_state.get('stateHealth')}")
        if any(state.get(k) != effective.get(k) for k in ("status", "currentOwner", "nextAction")):
            issues.append("runtime-state route still differs from derived effective state after repair")
        if not workflow_control_state:
            issues.append("automind-workflow-state.json still missing after repair")
        elif not workflow_state_consistent(task_dir):
            issues.append("automind workflow control state still differs from active stage state after repair")
        if actual:
            issues.append("obsolete runtime-state.json stateSummary still exists after repair")
        if (task_dir / "phase-transition-summary.json").exists():
            issues.append("obsolete phase-transition-summary.json still exists after repair")
        if "phaseTransition" in state:
            issues.append("obsolete runtime-state.phaseTransition still exists after repair")

    return {
        "result": "fail" if issues else "pass",
        "issues": issues,
        "warnings": warnings,
        "repairs": repairs,
        "effective": effective,
        "derivedReason": derived.get("reason"),
        "expectedSummary": _subset(expected),
        "stateSummary": _subset(actual),
        "workflowControlState": workflow_control_state,
    }
