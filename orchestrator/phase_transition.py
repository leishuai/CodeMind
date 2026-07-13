"""Compatibility phase-transition projection for CodeAutonomy tasks.

`automind-workflow-state.json` is the live workflow control state.  This module
builds an in-memory current/next phase projection for CLI guidance.  The old
`runtime-state.json.stateSummary` mirror is obsolete and only supported as a
obsolete fallback when explicitly requested for legacy exports.

Layering (single source of truth per concern):

- ``derive_effective_state`` (orchestrator.state_reducer) is the canonical
  artifact-based router: evaluation.json, completion-report, Delivery.md,
  terminal markers. The phase summary always starts from its ``effective``
  output.
- ``_build_state_from_event`` (orchestrator.workflow_state) is the canonical
  event-applier; it never re-derives from artifacts, only normalizes the
  event payload.
- ``build_phase_transition_summary`` (this module) is the display projection.
  It only adds *gate-shaped* signals not visible to derive_effective_state
  (workflow-check hard blockers, plan-stage missing Delivery), and finally
  flattens canonical phase names through ``display_phase`` for the legacy
  CLI/skill display vocabulary.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.state import read_evaluation_json, read_runtime_state, write_runtime_state
from orchestrator.state_reducer import derive_effective_state
from orchestrator.workflow import check_workflow_consistency
from orchestrator.workflow_state import (
    ACTION_TO_PHASE,
    display_phase,
    emit_workflow_event,
    normalize_action,
    normalize_phase,
    owner_for_phase as canonical_owner_for_phase,
    read_workflow_state,
)

SCHEMA_VERSION = 1


# Owner labels exposed to the CLI/skill display layer. These mirror the macro
# display vocabulary returned by ``display_phase`` so callers can convert
# canonical owners (planner/generator/evaluator/runtime) to legacy roles
# without duplicating routing tables.
_DISPLAY_OWNER_MAP = {
    "planning": "planner",
    "delivery": "generator",
    "evaluation": "evaluator",
    "terminal": "automind",
    "human_input": "human",
    "unknown": "automind",
}


def _evaluation_complete(evaluation: dict[str, Any]) -> bool:
    return bool(evaluation.get("result")) and bool(evaluation.get("nextAction")) and isinstance(evaluation.get("testResults"), list)


def _phase_for_action(action: str, *, default: str = "unknown") -> str:
    """Resolve the macro display phase implied by an action.

    The single source of truth for action -> canonical phase routing lives in
    ``workflow_state.ACTION_TO_PHASE``. This helper just translates that to
    the legacy display vocabulary used by the phase-transition projection.
    """
    raw = str(action or "").strip()
    if not raw:
        return default
    normalized_action = normalize_action(raw) or raw
    canonical = ACTION_TO_PHASE.get(normalized_action)
    if canonical is None and raw in {"finish", "done", "completed"}:
        canonical = "completion"
    if canonical is None and raw == "ask_user":
        canonical = "pre_implementation_review"
    if canonical is None and raw in {"replan", "run_test_planner"}:
        canonical = "plan"
    if canonical is None and raw in {"retry_generator", "resume_after_recovery", "run_generator"}:
        canonical = "delivery"
    if canonical is None and raw == "run_evaluator":
        canonical = "evaluation"
    if canonical is None:
        return default
    return display_phase(canonical, fallback=default)


def _owner_for_phase(phase: str, *, fallback: str = "automind") -> str:
    """Resolve the macro display owner from either a display or canonical phase."""
    label = display_phase(phase, fallback="")
    if label and label in _DISPLAY_OWNER_MAP:
        return _DISPLAY_OWNER_MAP[label]
    canonical = normalize_phase(phase)
    return canonical_owner_for_phase(canonical, fallback=fallback)


def _workflow_signal(task_dir: Path) -> tuple[bool, dict[str, Any]]:
    try:
        ok, report = check_workflow_consistency(task_dir.name)
    except Exception as exc:
        return False, {"issues": [f"workflow-check unavailable: {type(exc).__name__}: {exc}"], "workflowState": {}}
    return bool(ok), report if isinstance(report, dict) else {}


def _file_has_content(path: Path) -> bool:
    try:
        return path.exists() and path.read_text(errors="ignore").strip() != ""
    except Exception:
        return path.exists()


def _phase_checklist(
    task_dir: Path,
    *,
    phase: str,
    workflow_ok: bool,
    workflow_report: dict[str, Any],
    evaluation: dict[str, Any],
    derived: dict[str, Any],
    delivery_exists: bool,
) -> list[dict[str, Any]]:
    """Return a machine-readable checkbox plan for skill/current-session mode."""
    def item(item_id: str, text: str, done: bool, *, command: str | None = None, required: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": item_id,
            "text": text,
            "done": bool(done),
            "required": required,
        }
        if command:
            data["command"] = command
        return data

    task_code = task_dir.name
    phase = str(phase or "unknown")
    phase_reuse_name = {"planning": "plan", "delivery": "generator", "evaluation": "evaluator", "terminal": "summary"}.get(phase, phase)
    reuse_available = _file_has_content(task_dir / "Reuse.md") or _file_has_content(task_dir / "phase-reuse" / f"{phase_reuse_name}.md")
    validation_exists = _file_has_content(task_dir / "Validation.md")
    completion_pass = bool(derived.get("terminalAllowed"))
    workflow_issues = workflow_report.get("issues") if isinstance(workflow_report.get("issues"), list) else []
    has_pending_workflow_issues = bool(workflow_issues)
    runtime_state = read_runtime_state(task_dir) or {}
    workflow_exists = _file_has_content(task_dir / "workflow.json")
    sidecar_ready = {
        "brainstorm": _file_has_content(task_dir / "brainstorm.json"),
        "requirements": _file_has_content(task_dir / "requirements.json"),
        "testcases": _file_has_content(task_dir / "testcases.json"),
        "plan": _file_has_content(task_dir / "plan.json"),
        "delivery": _file_has_content(task_dir / "delivery.json"),
    }
    logs_dir = task_dir / "logs"
    context_pack_exists = any(logs_dir.glob("iter-*/evaluator-context.*")) if logs_dir.exists() else False
    generator_context_exists = any(logs_dir.glob("iter-*/generator-context.*")) if logs_dir.exists() else False
    iteration_purpose_exists = any(logs_dir.glob("iter-*/iteration-purpose.*")) if logs_dir.exists() else False
    iter_evidence_exists = any(logs_dir.glob("iter-*/*")) if logs_dir.exists() else False
    verification_ledger_exists = _file_has_content(task_dir / "VerificationLedger.json")
    review = (runtime_state.get("planner") or {}).get("preImplementationReview")
    review_done = isinstance(review, dict) and str(review.get("decision") or review.get("status") or "").strip() not in {"", "pending", "todo"}
    evaluation_complete = _evaluation_complete(evaluation)

    common = [
        item("read_workflow_control_state", "Read automind-workflow-state.json and stages/*-stage-state.json first; runtime-state.json.stateSummary is obsolete fallback only.", True),
        item("read_reuse_context", f"Review Reuse.md and phase-reuse/{phase_reuse_name}.md when present; use them as guidance, not as requirements.", reuse_available, required=False),
    ]
    if phase == "planning":
        return common + [
            item("read_planning_inputs", "Read Brainstorm.md, Requirements.md, TestCases.md, Plan.md, runtime-state.json, and existing phase sidecars if present.", any(_file_has_content(task_dir / name) for name in ["Brainstorm.md", "Requirements.md", "TestCases.md", "Plan.md"])),
            item("brainstorm_ready", "Refine Brainstorm.md with demand digestion, assumptions, risks, and open decisions.", _file_has_content(task_dir / "Brainstorm.md")),
            item("requirements_ready", "Refine Requirements.md with R/AC coverage.", _file_has_content(task_dir / "Requirements.md")),
            item("testcases_ready", "Refine TestCases.md with required TC coverage and evidence expectations.", _file_has_content(task_dir / "TestCases.md")),
            item("plan_ready", "Refine Plan.md with implementation and verification checklist.", _file_has_content(task_dir / "Plan.md")),
            item("preimplementation_review_resolved", "Resolve pre-implementation review: auto_proceed, ask_user answered, or replan.", review_done),
            item("planning_sidecars_updated", "Update planning sidecars (brainstorm.json, requirements.json, testcases.json, plan.json) and workflow.json via workflow-check.", workflow_exists and all(sidecar_ready[name] for name in ["brainstorm", "requirements", "testcases", "plan"]), command=f"automind workflow-check {task_code}"),
            item("workflow_check_green", "Run workflow-check and resolve hard blockers before Build/Generator.", workflow_ok and not has_pending_workflow_issues, command=f"automind workflow-check {task_code}"),
            item("phase_summary_refreshed", "Rerun phase-gate build/auto so automind-workflow-state.json and checklist reflect the next handoff.", workflow_ok and not has_pending_workflow_issues, command=f"automind phase-gate {task_code} build"),
        ]
    if phase == "delivery":
        return common + [
            item("read_generator_inputs", "Read Requirements.md/json, Plan.md/json, TestCases.md/json, workflow.json, runtime-state.json, and any prior evaluation.json repair signal before Generator work.", _file_has_content(task_dir / "Requirements.md") and _file_has_content(task_dir / "Plan.md") and _file_has_content(task_dir / "TestCases.md")),
            item("read_iteration_context", "Read iteration-purpose and generator-context files when present for this Generator round.", iteration_purpose_exists or generator_context_exists, required=False),
            item("workflow_check_green", "Confirm workflow-check is green before editing product/runtime code.", workflow_ok and not has_pending_workflow_issues, command=f"automind workflow-check {task_code}"),
            item("generator_implementation_done", "Generator implements or repairs the product/runtime changes required by Plan.md and TestCases.md.", delivery_exists, required=True),
            item("plan_implementation_checklist_updated", "Update Plan.md implementation checklist/T* progress when applicable.", _file_has_content(task_dir / "Plan.md"), required=False),
            item("delivery_markdown_written", "Write/update Delivery.md with changed files, behavior, risks, and evidence pointers.", _file_has_content(task_dir / "Delivery.md")),
            item("delivery_sidecar_updated", "Write/update delivery.json when available so downstream gates can consume structured delivery state.", sidecar_ready["delivery"], required=False),
            item("generator_evidence_logged", "Record commands, self-tests, decisions, or scoped diagnostic-log markers under logs/iter-N when relevant.", iter_evidence_exists, required=False),
            item("temporary_diagnostics_accounted", "If temporary diagnostic logs/instrumentation were added, record whether they must be removed or promoted before finish.", delivery_exists, required=False),
            item("phase_summary_refreshed", "Run phase-gate verify/auto so automind-workflow-state.json and checklist hand off to Evaluator only after Delivery is ready.", delivery_exists and workflow_ok, command=f"automind phase-gate {task_code} verify"),
        ]
    if phase == "evaluation":
        return common + [
            item("read_evaluator_inputs", "Read Delivery.md/delivery.json, Requirements.md/json, workflow.json, TestCases.md/json, and current context pack when model evaluation is needed.", delivery_exists and _file_has_content(task_dir / "TestCases.md")),
            item("context_pack_ready", "Generate/read evaluator context pack for isolated model Evaluator when needed.", context_pack_exists, command=f"automind context-pack {task_code}", required=False),
            item("verification_executed", "Run required verifier(s): deterministic runner, script-command, probe-flow, tests, or isolated Evaluator.", evaluation_complete),
            item("evidence_logged", "Save verification evidence under logs/iter-N and include paths/signals in evaluation.json evidence/evidenceIndex when available.", iter_evidence_exists or bool(evaluation.get("evidenceIndex") or evaluation.get("evidence")), required=False),
            item("validation_written", "Write/update Validation.md with executed checks, evidence, failures, and nextAction rationale.", validation_exists),
            item("verification_checklist_updated", "Update Plan.md verification checklist / TC-* progress when applicable.", validation_exists, required=False),
            item("evaluation_json_complete", "Write evaluation.json with result, nextAction, testResults[], failedChecks[] when relevant, and evidenceIndex when available.", evaluation_complete),
            item("route_next_action_recorded", "Ensure evaluation.json.nextAction routes finish, retry_generator, replan, ask_user, stop, or pause_for_external explicitly.", bool(evaluation.get("nextAction"))),
            item("phase_summary_refreshed", "Rerun phase-gate auto/finish so automind-workflow-state.json reflects retry_generator, replan, ask_user, or finish.", evaluation_complete, command=f"automind phase-gate {task_code} auto"),
            item("finish_gate_checked", "If evaluation requests finish, run completion-check and require completion-report.json result=pass.", completion_pass, command=f"automind completion-check {task_code}", required=str(evaluation.get("nextAction") or "") == "finish"),
        ]
    if phase == "human_input":
        return common + [
            item("read_pending_question", "Read pending question from runtime-state/session artifacts before asking the user.", True),
            item("answer_recorded", "Ask the pending question once and record the answer with automind answer.", False, command=f"automind answer {task_code} --text '<answer>'"),
            item("phase_summary_refreshed", "Rerun phase-gate auto after the answer is applied so automind-workflow-state.json routes the next phase.", False, command=f"automind phase-gate {task_code} auto"),
        ]
    if phase == "terminal":
        return common + [
            item("read_completion_report", "Read completion-report.json, VerificationLedger.json, evaluation.json, Delivery.md/json, and workflow.json before final handoff.", _file_has_content(task_dir / "completion-report.json")),
            item("completion_check_passed", "Confirm completion-check passed authoritatively.", completion_pass, command=f"automind completion-check {task_code}"),
            item("verification_ledger_updated", "Ensure VerificationLedger.json is present/updated when completion-check produces it.", verification_ledger_exists, required=False),
            item("reuse_learning_promoted", "Promote useful phase-learning/reuse findings into summary/reuse artifacts when they have real value.", _file_has_content(task_dir / "summary.md"), command=f"automind summary {task_code}", required=False),
            item("summary_generated", "Generate summary/reuse artifacts.", _file_has_content(task_dir / "summary.md"), command=f"automind summary {task_code}"),
            item("record_check_available", "Run record-check as a final reusable-record diagnostic when useful.", False, command=f"automind record-check {task_code}", required=False),
            item("report_generated", "Generate final Report.html for human review.", _file_has_content(task_dir / "Report.html"), command=f"automind report {task_code}", required=False),
            item("phase_summary_refreshed", "Rerun phase-gate finish to keep automind-workflow-state.json terminal checklist current.", completion_pass, command=f"automind phase-gate {task_code} finish"),
        ]
    return common + [
        item("inspect_next_action", "Inspect automind-workflow-state.json, stages/*-stage-state.json, and in-memory phase summary reason/basis; runtime-state.stateSummary is obsolete fallback only.", False),
    ]


def build_phase_transition_summary(task_dir: Path) -> dict[str, Any]:
    """Resolve the concise macro phase transition summary for ``task_dir``."""
    state = read_runtime_state(task_dir) or {}
    evaluation = read_evaluation_json(task_dir) or {}
    derived = derive_effective_state(task_dir)
    effective = derived.get("effective") if isinstance(derived.get("effective"), dict) else {}
    workflow_ok, workflow_report = _workflow_signal(task_dir)
    workflow_state = workflow_report.get("workflowState") if isinstance(workflow_report.get("workflowState"), dict) else {}

    delivery_exists = (task_dir / "Delivery.md").exists() or (task_dir / "delivery.json").exists()
    current_status = str(effective.get("status") or state.get("status") or "created")
    current_owner = str(effective.get("currentOwner") or state.get("currentOwner") or "automind")
    current_phase_raw = str(effective.get("phase") or "").strip()
    if not current_phase_raw or current_phase_raw == "unknown":
        current_phase_raw = _phase_for_action(str(effective.get("nextAction") or state.get("nextAction") or ""))
    current_phase = str(current_phase_raw or "unknown")

    next_phase = current_phase
    next_action = str(effective.get("nextAction") or state.get("nextAction") or "run_generator")
    next_owner = str(effective.get("currentOwner") or state.get("currentOwner") or _owner_for_phase(_phase_for_action(next_action)))
    reason = "Use the derived runtime state as the next phase projection."
    basis: list[str] = []

    if derived.get("terminalAllowed"):
        next_phase = "terminal"
        next_action = "finish"
        next_owner = "automind"
        reason = "completion-report.json is an authoritative pass, so the task can finish."
        basis = ["completion-report.json authoritative pass"]
    elif str(effective.get("nextAction") or state.get("nextAction") or "") == "ask_user" or str(effective.get("status") or state.get("status") or "") == "human_input_pending":
        next_phase = "human_input"
        next_action = "ask_user"
        next_owner = "human"
        reason = "runtime/effective state is waiting for user input before CodeAutonomy can continue."
        basis = ["runtime-state nextAction=ask_user or status=human_input_pending"]
    elif evaluation.get("nextAction") == "ask_user":
        next_phase = "human_input"
        next_action = "ask_user"
        next_owner = "human"
        reason = "evaluation.json requests user input before CodeAutonomy can continue."
        basis = ["evaluation.json nextAction=ask_user"]
    elif evaluation.get("nextAction") == "replan":
        next_phase = "planning"
        next_action = "run_test_planner"
        next_owner = "planner"
        reason = "evaluation.json requests replanning, so the next phase is planning."
        basis = ["evaluation.json nextAction=replan"]
    elif evaluation.get("nextAction") in {"retry_generator", "resume_after_recovery"}:
        next_phase = "delivery"
        next_action = str(evaluation.get("nextAction"))
        next_owner = "generator"
        reason = "evaluation.json routes repair back to the Generator/Delivery phase."
        basis = [f"evaluation.json nextAction={evaluation.get('nextAction')}"]
    elif derived.get("terminalStateObserved") and not derived.get("terminalAllowed"):
        if delivery_exists:
            next_phase = "evaluation"
            next_action = "run_evaluator"
            next_owner = "evaluator"
            reason = "A terminal marker exists without authoritative completion pass; Delivery exists, so evaluation must reopen the task."
            basis = ["runtime state has terminal marker", "completion-report.json is not authoritative pass", "Delivery.md exists"]
        else:
            next_phase = "delivery"
            next_action = "retry_generator"
            next_owner = "generator"
            reason = "A terminal marker exists without authoritative completion pass and no Delivery artifact exists, so Generator must produce/repair delivery."
            basis = ["runtime state has terminal marker", "completion-report.json is not authoritative pass", "Delivery.md missing"]
    elif not workflow_ok:
        next_phase = "planning"
        next_action = "run_test_planner"
        next_owner = "planner"
        reason = "workflow-check has hard blockers, so planning/phase artifacts must be refined before downstream handoff."
        issues = workflow_report.get("issues") if isinstance(workflow_report.get("issues"), list) else []
        basis = ["workflow-check failed", *(str(item) for item in issues[:3])]
    elif delivery_exists and not _evaluation_complete(evaluation):
        next_phase = "evaluation"
        next_action = "run_evaluator"
        next_owner = "evaluator"
        reason = "Delivery exists and workflow-check is green, but evaluation.json is missing complete testResults, so evaluation must run next."
        basis = ["Delivery.md exists", "workflow-check passed", "evaluation.json missing complete testResults", "completion-report.json is not authoritative pass"]
    elif workflow_ok and not delivery_exists:
        next_phase = "delivery"
        next_action = "run_generator"
        next_owner = "generator"
        reason = "Planning artifacts are coherent and no Delivery artifact exists, so Generator should run next."
        basis = ["workflow-check passed", "Delivery.md missing"]
    else:
        next_phase = _phase_for_action(next_action, default=current_phase)
        next_owner = _owner_for_phase(next_phase, fallback=next_owner)
        basis = [f"derived nextAction={next_action}"]

    if not basis:
        basis = ["derived runtime state"]

    # Flatten any canonical phase that may have leaked into the projection
    # through display_phase so the CLI/skill vocabulary is the single output
    # contract. Legacy display names round-trip unchanged.
    current_phase_display = display_phase(current_phase, fallback=current_phase or "unknown")
    next_phase_display = display_phase(next_phase, fallback=next_phase or "unknown")

    checklist = _phase_checklist(
        task_dir,
        phase=next_phase_display,
        workflow_ok=workflow_ok,
        workflow_report=workflow_report,
        evaluation=evaluation,
        derived=derived,
        delivery_exists=delivery_exists,
    )
    checkbox_markdown = [f"- [{'x' if item.get('done') else ' '}] {item.get('text')}" for item in checklist]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "taskCode": task_dir.name,
        "currentPhase": current_phase_display,
        "currentStatus": current_status,
        "currentOwner": current_owner,
        "nextPhase": next_phase_display,
        "nextAction": next_action,
        "nextOwner": next_owner,
        "reason": reason,
        "basis": basis,
        "checklist": checklist,
        "checkboxMarkdown": checkbox_markdown,
    }



def refresh_phase_transition_summary(task_dir: Path, *, update_runtime_projection: bool = False) -> dict[str, Any]:
    """Recompute the in-memory phase summary and clean obsolete mirrors.

    The live workflow control state is `automind-workflow-state.json` plus
    `stages/*-stage-state.json`.  `runtime-state.json.stateSummary` is not
    written by default; pass update_runtime_projection=True only for explicit
    legacy compatibility exports.
    """
    summary = build_phase_transition_summary(task_dir)
    stale_mirror = task_dir / "phase-transition-summary.json"
    if stale_mirror.exists():
        stale_mirror.unlink()
    state = read_runtime_state(task_dir) or {}
    if "phaseTransition" in state or (not update_runtime_projection and "stateSummary" in state):
        state.pop("phaseTransition", None)
        if not update_runtime_projection:
            state.pop("stateSummary", None)
        write_runtime_state(task_dir, state)
    if update_runtime_projection:
        state["stateSummary"] = dict(summary)
        write_runtime_state(task_dir, state)
    phase = normalize_phase(str(summary.get("nextPhase") or summary.get("currentPhase") or "unknown"), action=str(summary.get("nextAction") or ""))
    action = normalize_action(str(summary.get("nextAction") or ""))
    workflow_state = read_workflow_state(task_dir)
    # Read-only/status refreshes should not spam or override explicit
    # workflow events. Seed the new workflow state only when it is missing;
    # real transitions must be emitted by the owning runtime phase/action.
    if not workflow_state:
        emit_workflow_event(task_dir, {
            "type": "state_projected",
            "phase": phase,
            "action": action,
            "nextAction": action,
            "nextPhase": phase,
            "owner": summary.get("nextOwner") or summary.get("currentOwner"),
            "iteration": (read_runtime_state(task_dir) or {}).get("iteration", 0),
            "reason": "phase_transition_projection",
        })
    return summary
