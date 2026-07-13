"""Process evals for CodeAutonomy runs.

Process evals check whether the harness behaved correctly, not whether the final
product output is correct.  They complement workflow-check/completion-check and
reuse the same task-local artifacts.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.phase_transition import build_phase_transition_summary
from orchestrator.session.answers import read_answers
from orchestrator.session.trace import build_trace, write_trace
from orchestrator.state import read_runtime_state, rel_to_root
from orchestrator.workflow_contract import ensure_workflow_contract, validate_workflow_contract
from orchestrator.workflow_state import read_stage_state, read_workflow_state

PROCESS_EVAL_SCHEMA_VERSION = 1


def _safe_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(errors="ignore"))
    except json.JSONDecodeError:
        return None


def _add(items: list[dict[str, Any]], check_id: str, severity: str, message: str, *, evidence: list[str] | None = None) -> None:
    items.append({"id": check_id, "severity": severity, "message": message, "evidence": evidence or []})


def run_process_eval(task_code: str, task_dir: Path, *, write: bool = True) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    passes: list[dict[str, Any]] = []

    state = read_runtime_state(task_dir) or {}
    workflow = _safe_json(task_dir / "workflow.json") or {}
    workflow_control_state = read_workflow_state(task_dir)
    phase_summary = build_phase_transition_summary(task_dir)
    pre_review = _safe_json(task_dir / "pre-implementation-review.json")
    brainstorm = _safe_json(task_dir / "brainstorm.json") or {}
    summary_stage = read_stage_state(task_dir, "summary")
    completion = (summary_stage.get("completion") if isinstance(summary_stage.get("completion"), dict) else {}) or _safe_json(task_dir / "completion-report.json") or _safe_json(task_dir / "VerificationLedger.json") or {}
    trace = write_trace(task_code, task_dir) if write else build_trace(task_code, task_dir)

    workflow_contract = ensure_workflow_contract(task_dir)
    workflow_issues, workflow_warnings = validate_workflow_contract(task_dir, workflow_contract)
    if not workflow_issues:
        _add(passes, "workflow-contract-pass", "pass", "workflow contract validation has no blocking issues", evidence=["workflow.json"])
    else:
        _add(issues, "workflow-contract-fail", "issue", "workflow contract validation has open issues", evidence=workflow_issues[:10])
    for warning in workflow_warnings[:5]:
        _add(warnings, "workflow-contract-warning", "warning", warning, evidence=["workflow.json"])

    phase_graph = workflow_contract.get("phaseGraph") if isinstance(workflow_contract.get("phaseGraph"), dict) else {}
    phase_ids = [str(p) for p in phase_graph.get("nodes", [])] if isinstance(phase_graph.get("nodes"), list) else []
    if "pre_implementation_review" in phase_ids:
        _add(passes, "pre-implementation-phase-present", "pass", "pre_implementation_review phase is present in workflow graph", evidence=["workflow.json"])
    else:
        _add(issues, "pre-implementation-phase-missing", "issue", "workflow graph is missing pre_implementation_review phase", evidence=["workflow.json"])

    decision = None
    if isinstance(pre_review, dict):
        decision = pre_review.get("decision") or pre_review.get("result")
    if decision in {"auto_proceed", "ask_user", "replan"}:
        _add(passes, "pre-implementation-decision-recorded", "pass", f"pre-implementation decision recorded: {decision}", evidence=["pre-implementation-review.json"])
    else:
        _add(issues, "pre-implementation-decision-missing", "issue", "pre-implementation review decision is missing or invalid", evidence=["pre-implementation-review.json"])

    current_phase = workflow_control_state.get("currentPhase") or workflow_control_state.get("nextPhase") or phase_summary.get("currentPhase") or phase_summary.get("nextPhase")
    expected = workflow_contract.get("expectedNext") if isinstance(workflow_contract.get("expectedNext"), list) else []
    if state.get("workflowStateError"):
        _add(warnings, "workflow-state-sync-error", "warning", "runtime-state records a workflow-state sync error", evidence=["runtime-state.json#workflowStateError"])
    state_health = workflow_control_state.get("stateHealth")
    if state_health in {"reconciling", "degraded"}:
        _add(warnings, "workflow-control-state-health", "warning", f"workflow control state health is {state_health}", evidence=["automind-workflow-state.json"])
    if workflow_control_state.get("currentPhase") or workflow_control_state.get("nextPhase"):
        _add(passes, "workflow-control-state-present", "pass", "automind workflow control state current/next state is present", evidence=["automind-workflow-state.json"])
    elif current_phase:
        _add(warnings, "workflow-control-state-missing-using-phase-summary", "warning", "automind workflow control state is missing; using deterministic phase summary projection", evidence=["automind-workflow-state.json", "stages/*-stage-state.json"])
    else:
        _add(warnings, "workflow-control-state-weak", "warning", "workflow control state is missing/weak and deterministic phase summary is also missing or weak", evidence=["automind-workflow-state.json", "stages/*-stage-state.json"])
    if expected:
        _add(passes, "workflow-expected-next-present", "pass", "workflow contract expectedNext is present", evidence=["workflow.json"])
    else:
        _add(warnings, "workflow-expected-next-weak", "warning", "workflow contract expectedNext is missing or weak", evidence=["workflow.json"])

    answers = read_answers(task_dir)
    if answers:
        latest = answers[-1]
        delivery = latest.get("delivery") if isinstance(latest.get("delivery"), dict) else {}
        if delivery.get("status") == "pending" and state.get("status") not in {"human_input_pending", "planned", "replan_pending"}:
            _add(warnings, "latest-answer-not-delivered", "warning", "latest user answer is still pending delivery to the next agent prompt", evidence=["user-answers.json"])
        else:
            _add(passes, "latest-answer-delivery-state-ok", "pass", "latest user answer delivery state is acceptable", evidence=["user-answers.json"])

    trace_summary = trace.get("summary", {}) if isinstance(trace.get("summary"), dict) else {}
    if trace_summary.get("spanCount", 0) >= 1:
        _add(passes, "trace-present", "pass", "formal trace contains spans", evidence=["trace.json"])
    else:
        _add(issues, "trace-empty", "issue", "formal trace has no spans", evidence=["trace.json"])
    if trace_summary.get("errorCount", 0):
        _add(warnings, "trace-errors-present", "warning", "formal trace contains error spans", evidence=["trace.json"])

    repo_ctx = brainstorm.get("repositoryContext") if isinstance(brainstorm, dict) else None
    if isinstance(repo_ctx, dict) and (repo_ctx.get("scripts") or repo_ctx.get("docs") or repo_ctx.get("agentInstructions")):
        _add(passes, "repository-context-discovered", "pass", "Brainstorm repositoryContext records docs/scripts/agent instruction discovery", evidence=["brainstorm.json"])
    else:
        _add(warnings, "repository-context-weak", "warning", "Brainstorm repositoryContext lacks docs/scripts/agent instruction discovery", evidence=["brainstorm.json", "Brainstorm.md"])

    if state.get("status") == "finished":
        result = completion.get("result") or completion.get("overallResult")
        if result == "pass":
            _add(passes, "completion-pass-before-finish", "pass", "finished task has passing completion report", evidence=["stages/summary-stage-state.json#completion", "completion-report.json", "VerificationLedger.json"])
        else:
            _add(issues, "finished-without-completion-pass", "issue", "task is finished but no passing completion report was found", evidence=["stages/summary-stage-state.json#completion", "completion-report.json", "VerificationLedger.json"])
        if (task_dir / "Summary.md").exists() or (task_dir / "summary.md").exists():
            _add(passes, "summary-present", "pass", "finished task has Summary artifact", evidence=["Summary.md", "summary.md"])
        else:
            _add(warnings, "summary-missing", "warning", "finished task has no Summary artifact yet", evidence=["Summary.md"])

    result = "fail" if issues else ("warn" if warnings else "pass")
    report = {
        "schemaVersion": PROCESS_EVAL_SCHEMA_VERSION,
        "taskCode": task_code,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "result": result,
        "issueCount": len(issues),
        "warningCount": len(warnings),
        "passCount": len(passes),
        "issues": issues,
        "warnings": warnings,
        "passes": passes,
        "tracePath": rel_to_root(task_dir / "trace.json") if (task_dir / "trace.json").exists() else None,
        "inputs": {
            "workflowControlState": "automind-workflow-state.json",
            "stageState": "stages/*-stage-state.json",
            "phaseSummary": "in-memory CLI guidance projection",
            "stateSummary": "runtime-state.json#stateSummary (obsolete fallback)",
            "runtimeState": "runtime-state.json",
            "workflow": "workflow.json",
            "trace": "trace.json",
            "preImplementationReview": "pre-implementation-review.json",
            "brainstorm": "brainstorm.json",
        },
    }
    if write:
        (task_dir / "process-eval.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def render_process_eval(report: dict[str, Any]) -> str:
    lines = [
        f"Process Eval: {report.get('taskCode')}",
        f"Result: {report.get('result')}  issues={report.get('issueCount')} warnings={report.get('warningCount')} passes={report.get('passCount')}",
        "",
    ]
    for title, key in (("Issues", "issues"), ("Warnings", "warnings"), ("Passes", "passes")):
        items = report.get(key) if isinstance(report.get(key), list) else []
        lines.append(f"{title}:")
        if not items:
            lines.append("- none")
        else:
            for item in items[:20]:
                lines.append(f"- {item.get('id')}: {item.get('message')}")
        lines.append("")
    return "\n".join(lines).rstrip()
