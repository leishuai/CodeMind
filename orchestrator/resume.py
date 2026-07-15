"""Stage-level resume/recovery helpers for interrupted CodeMind tasks."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def iter_dir_for(task_dir: Path, iteration: int) -> Path:
    """Return the log directory for a concrete loop iteration."""
    return task_dir / "logs" / f"iter-{iteration}"


# Whitelist of categories CodeMind considers safely auto-resumable.
# Keep tightly scoped: product failures and authorization issues MUST NOT
# be added here.
RECOVERABLE_INTERRUPTION_CATEGORIES: frozenset[str] = frozenset({
    "agent_unavailable",
    "agent_timeout",
    "network_timeout",
    "cli_crash",
    "build_failure_transient",
    "test_failure_transient",
    "probe_flow_failure",
    "signing_renew_pending",
})


def _evaluation_failed_categories(evaluation: Optional[dict]) -> set[str]:
    """Extract failed-check categories from evaluation.json."""
    if not isinstance(evaluation, dict):
        return set()
    categories: set[str] = set()
    failed_checks = evaluation.get("failedChecks", [])
    if isinstance(failed_checks, list):
        for check in failed_checks:
            if isinstance(check, dict) and check.get("category"):
                categories.add(str(check.get("category")))
    return categories


def is_recoverable_external_interruption(evaluation: Optional[dict]) -> bool:
    """Return whether a failed task likely stopped because of a recoverable external condition.

    Recoverable categories (auto-resume whitelist):
    - agent_unavailable / agent_timeout : agent CLI binary missing or hung
    - network_timeout                   : transient network outage
    - cli_crash                         : agent process crashed mid-flight
    - build_failure_transient           : transient build/dependency hiccup
    - test_failure_transient            : flaky/timing test failure marked transient
    - probe_flow_failure                : probe_flow runner glitch
    - signing_renew_pending             : iOS signing material refresh pending

    Product failures and unsafe blockers (permission_denied, unauthorized_destructive,
    user_blocked, etc.) MUST NOT be auto-resumed.
    """
    categories = _evaluation_failed_categories(evaluation)
    return bool(categories.intersection(RECOVERABLE_INTERRUPTION_CATEGORIES))


def infer_interrupted_phase(state: dict, evaluation: Optional[dict]) -> str:
    """Infer which phase should be retried after a recoverable interruption."""
    owner = str(state.get("currentOwner") or "").lower()
    next_action = str(state.get("nextAction") or "").lower()
    if owner in {"generator", "evaluator", "planner"}:
        return owner
    if next_action in {"run_generator", "retry_generator"}:
        return "generator"
    if next_action == "run_evaluator":
        return "evaluator"
    if next_action in {"run_test_planner", "replan"}:
        return "planner"

    blob = json.dumps(evaluation or {}, ensure_ascii=False).lower()
    if "evaluator agent" in blob or "evaluator" in blob:
        return "evaluator"
    if "generator agent" in blob or "generator" in blob:
        return "generator"
    if "planner" in blob or "replan" in blob:
        return "planner"
    return "generator"


def build_resume_recovery_entry(task_dir: Path, state: dict, evaluation: Optional[dict] = None) -> dict:
    """Decide the stage-level recovery entry from persisted task artifacts.

    CodeMind cannot recover an agent's private call stack. It can recover at the
    phase boundary recorded in runtime-state/evaluation/log artifacts:

    - interrupted Generator without `generator.log`: rerun Generator for that
      iteration;
    - Generator completed (`generator.log` exists) but Evaluator did not finish:
      continue at Evaluator for that iteration;
    - interrupted Evaluator: rerun Evaluator for that iteration;
    - failed due agent/runtime unavailability: retry the interrupted phase after
      the environment is fixed.
    """
    status = str(state.get("status") or "unknown")
    next_action = str(state.get("nextAction") or "")
    state_iteration = int(state.get("iteration", 0) or 0)
    eval_iteration = 0
    if isinstance(evaluation, dict):
        try:
            eval_iteration = int(evaluation.get("iteration", 0) or 0)
        except Exception:
            eval_iteration = 0
    iteration = max(state_iteration, eval_iteration, 1)
    current_iter_dir = iter_dir_for(task_dir, iteration)

    entry = {
        "stage": "normal",
        "iteration": state_iteration,
        "reason": "normal loop entry",
        "recoverable": False,
    }

    if status == "generating":
        if (current_iter_dir / "generator.log").exists():
            return {
                "stage": "evaluator",
                "iteration": iteration,
                "reason": f"runtime-state says generating, but logs/iter-{iteration}/generator.log exists; resume at Evaluator",
                "recoverable": True,
            }
        return {
            "stage": "generator",
            "iteration": iteration,
            "reason": f"runtime-state says generating and logs/iter-{iteration}/generator.log is missing; rerun Generator for the interrupted iteration",
            "recoverable": True,
        }

    if status == "evaluating" or next_action == "run_evaluator":
        return {
            "stage": "evaluator",
            "iteration": iteration,
            "reason": f"runtime-state says {status}/{next_action}; rerun Evaluator for the interrupted iteration",
            "recoverable": True,
        }

    if status == "failed" and is_recoverable_external_interruption(evaluation):
        phase = infer_interrupted_phase(state, evaluation)
        categories = _evaluation_failed_categories(evaluation)
        reason_prefix = "previous failure was agent/runtime timeout" if "agent_timeout" in categories else "previous failure was agent/runtime unavailable"
        if phase == "evaluator":
            return {
                "stage": "evaluator",
                "iteration": iteration,
                "reason": f"{reason_prefix} during Evaluator; resume Evaluator after environment recovery",
                "recoverable": True,
            }
        if phase == "planner":
            return {
                "stage": "planner",
                "iteration": iteration,
                "reason": f"{reason_prefix} during planning; rerun Phase 2 Refiner before continuing",
                "recoverable": True,
            }
        return {
            "stage": "generator",
            "iteration": iteration,
            "reason": f"{reason_prefix} during Generator; resume Generator after environment recovery",
            "recoverable": True,
        }

    return entry
