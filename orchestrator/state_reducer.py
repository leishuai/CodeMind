"""Deterministic effective-state reducer for AutoMind tasks.

`runtime-state.json` is a resume cache/runtime projection, not the macro phase
truth.  This module re-derives the effective loop state from phase artifacts and
gate outputs, then optionally reconciles drifted runtime-state fields before
CLI/skill-mode resume.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.state import (
    completion_pass_superseded_by_reopened_verification,
    completion_report_is_authoritative_terminal_pass,
    read_evaluation_json,
    read_runtime_state,
    update_runtime_state,
)

TERMINAL_STATUSES = {"finished", "completed"}
TERMINAL_NEXT_ACTIONS = {"finish", "done", "completed"}


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _completion_artifact(task_dir: Path) -> tuple[dict[str, Any], str]:
    stage = _safe_json(task_dir / "stages" / "summary-stage-state.json")
    completion = stage.get("completion") if isinstance(stage.get("completion"), dict) else {}
    if completion:
        return completion, "stages/summary-stage-state.json#completion"
    report = _safe_json(task_dir / "completion-report.json")
    if report:
        return report, "completion-report.json"
    ledger = _safe_json(task_dir / "VerificationLedger.json")
    if ledger:
        return ledger, "VerificationLedger.json"
    return {}, "none"


def _completion_result(task_dir: Path, state: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None, str, bool]:
    completion, source = _completion_artifact(task_dir)
    if completion:
        verdict = completion.get("completionVerdict") if isinstance(completion.get("completionVerdict"), dict) else None
        authoritative = (
            source in {"stages/summary-stage-state.json#completion", "completion-report.json"}
            and completion_report_is_authoritative_terminal_pass(completion)
            and not completion_pass_superseded_by_reopened_verification(task_dir)
        )
        return completion.get("result"), verdict, source, authoritative
    verdict = state.get("completionVerdict") if isinstance(state.get("completionVerdict"), dict) else None
    return state.get("completionCheck"), verdict, "runtime-state.json", False


def _evaluation_complete(evaluation: dict[str, Any]) -> bool:
    return bool(evaluation.get("result")) and bool(evaluation.get("nextAction")) and isinstance(evaluation.get("testResults"), list)



def derive_effective_state(task_dir: Path) -> dict[str, Any]:
    """Return the deterministic effective state for a task directory.

    The reducer is intentionally small: it only encodes invariants that decide
    resume routing and terminal permission.  Phase contracts still live in
    workflow-check/completion-check.
    """
    state = read_runtime_state(task_dir) or {}
    evaluation = read_evaluation_json(task_dir) or {}
    completion_result, completion_verdict, completion_source, terminal_allowed = _completion_result(task_dir, state)
    state_status = str(state.get("status") or "").strip().lower()
    state_next = str(state.get("nextAction") or "").strip().lower()
    terminal_observed = state_status in TERMINAL_STATUSES or state_next in TERMINAL_NEXT_ACTIONS
    eval_next = str(evaluation.get("nextAction") or "").strip()

    reason = "state_consistent"
    effective = {
        "status": state.get("status") or "created",
        "currentOwner": state.get("currentOwner") or "automind",
        "nextAction": state.get("nextAction") or "run_generator",
        "phase": "unknown",
    }
    terminal_authoritative = False

    if terminal_allowed:
        reason = "completion_check_pass"
        terminal_authoritative = True
        effective.update(status="finished", currentOwner="automind", nextAction="finish", phase="terminal")
    elif eval_next == "ask_user":
        reason = "evaluation_requires_user"
        effective.update(status="human_input_pending", currentOwner="human", nextAction="ask_user", phase="human_input")
    elif eval_next == "replan":
        reason = "evaluation_requests_replan"
        effective.update(status="replan_pending", currentOwner="planner", nextAction="run_test_planner", phase="planning")
    elif eval_next in {"retry_generator", "resume_after_recovery"}:
        reason = "evaluation_requests_generator_retry"
        effective.update(status="retry_pending", currentOwner="generator", nextAction="retry_generator", phase="delivery")
    elif terminal_observed and not terminal_allowed:
        # False finish / legacy completed-done drift.  Route to the first phase
        # that can repair the missing proof.  If Delivery exists, Evaluator owns
        # the missing/invalid evaluation; otherwise Generator must produce it.
        if (task_dir / "Delivery.md").exists() or (task_dir / "delivery.json").exists():
            reason = "terminal_marker_without_completion_pass_evaluation_required"
            effective.update(status="evaluating", currentOwner="evaluator", nextAction="run_evaluator", phase="evaluation")
        else:
            reason = "terminal_marker_without_completion_pass_generator_required"
            effective.update(status="retry_pending", currentOwner="generator", nextAction="retry_generator", phase="delivery")
    elif (task_dir / "Delivery.md").exists() and not _evaluation_complete(evaluation):
        reason = "delivery_done_evaluation_incomplete"
        effective.update(status="evaluating", currentOwner="evaluator", nextAction="run_evaluator", phase="evaluation")

    drift = {
        "status": state.get("status"),
        "currentOwner": state.get("currentOwner"),
        "nextAction": state.get("nextAction"),
    }
    existing_authority = state.get("stateAuthority") if isinstance(state.get("stateAuthority"), dict) else {}
    authority_stale = bool(existing_authority) and (
        existing_authority.get("reason") != reason
        or ((existing_authority.get("evaluation") if isinstance(existing_authority.get("evaluation"), dict) else {}).get("nextAction") != evaluation.get("nextAction"))
        or ((existing_authority.get("evaluation") if isinstance(existing_authority.get("evaluation"), dict) else {}).get("result") != evaluation.get("result"))
    )
    should_reconcile = (
        any(drift.get(k) != effective.get(k) for k in ("status", "currentOwner", "nextAction"))
        or authority_stale
    ) and reason != "state_consistent"
    return {
        "role": "derived_cache",
        "effective": effective,
        "observed": drift,
        "terminalAllowed": terminal_allowed,
        "terminalAuthoritative": terminal_authoritative,
        "terminalStateObserved": terminal_observed,
        "completionCheck": completion_result,
        "completionVerdict": completion_verdict,
        "completionSource": completion_source,
        "evaluation": {
            "result": evaluation.get("result"),
            "nextAction": evaluation.get("nextAction"),
            "testResultsPresent": isinstance(evaluation.get("testResults"), list),
            "consumedReplan": evaluation.get("previousNextAction") == "replan" and evaluation.get("nextAction") != "replan",
        },
        "reason": reason,
        "shouldReconcile": should_reconcile,
    }


def reconcile_task_state(task_dir: Path, *, reason: str = "derived_state_reconcile") -> dict[str, Any]:
    """Recompute effective state and rewrite runtime-state cache when it drifted."""
    derived = derive_effective_state(task_dir)
    if not derived.get("shouldReconcile"):
        return derived
    previous = derived.get("observed") or {}
    effective = derived.get("effective") or {}
    authority = {
        **derived,
        "reconciledAt": datetime.now().isoformat(timespec="seconds"),
        "reconcileReason": reason,
        "previousTaskState": previous,
    }
    extra: dict[str, Any] = {}
    state = read_runtime_state(task_dir) or {}
    evaluation = read_evaluation_json(task_dir) or {}
    if effective.get("nextAction") == "ask_user":
        extra["askUserQuestion"] = evaluation.get("askUserQuestion") or state.get("askUserQuestion")
    if derived.get("terminalStateObserved") and not derived.get("terminalAllowed"):
        extra["falseFinishRecovery"] = {
            "reason": "finished_without_completion_pass",
            "previousStatus": previous.get("status"),
            "previousNextAction": previous.get("nextAction"),
            "completionCheck": derived.get("completionCheck"),
            "completionVerdict": derived.get("completionVerdict"),
            "completionSource": derived.get("completionSource"),
            "authoritativeCompletionPass": bool(derived.get("terminalAllowed")),
            "recoveredBy": "state_reducer",
        }
    update_runtime_state(
        task_dir,
        status=effective.get("status"),
        currentOwner=effective.get("currentOwner"),
        nextAction=effective.get("nextAction"),
        stateAuthority=authority,
        **extra,
    )
    try:
        from orchestrator.phase_transition import refresh_phase_transition_summary

        refresh_phase_transition_summary(task_dir, update_runtime_projection=False)
    except Exception:
        # State reconciliation must never be blocked by the human-facing macro
        # summary. Status/resume entrances also perform read-through refresh.
        pass
    return authority
