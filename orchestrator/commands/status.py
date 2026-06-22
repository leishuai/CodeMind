"""Status and log CLI command handlers."""
from __future__ import annotations

from typing import Optional, Any
import json
import re
from pathlib import Path

from orchestrator.config import MAX_ITERATIONS
from orchestrator.console import error, run_cmd
from orchestrator.reports import build_status_guidance, print_report_manifest
from orchestrator.session.instructions import build_next_instruction
from orchestrator.state import get_task_dir, list_tasks, read_evaluation_json, read_runtime_state
from orchestrator.state_reducer import reconcile_task_state
from orchestrator.state_summary_check import check_state_summary
from orchestrator.workflow_state import ensure_workflow_state, read_stage_state


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _latest_evidence_iteration(task_dir: Path) -> int:
    """Return the latest iteration directory that contains real evidence files.

    Older status logic only counted `*summary*.json` / `*result*.json`, which
    missed evaluator-only iterations and made status disagree with Report.html.
    Treat any non-empty `logs/iter-N` directory as evidence-bearing, while
    ignoring hidden placeholders.
    """
    logs = task_dir / "logs"
    if not logs.exists():
        return 0
    latest = 0
    for path in logs.glob("iter-*"):
        if not path.is_dir():
            continue
        match = re.fullmatch(r"iter-(\d+)", path.name)
        if not match:
            continue
        has_files = any(child.is_file() and not child.name.startswith(".") for child in path.rglob("*"))
        if has_files:
            latest = max(latest, int(match.group(1)))
    return latest


def _freshness_summary(task_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    evaluation = read_evaluation_json(task_dir) or _safe_json(task_dir / "evaluation.json")
    summary_stage = read_stage_state(task_dir, "summary")
    completion = (summary_stage.get("completion") if isinstance(summary_stage.get("completion"), dict) else {}) or _safe_json(task_dir / "completion-report.json")
    ledger = _safe_json(task_dir / "VerificationLedger.json")
    latest_evidence = _latest_evidence_iteration(task_dir)
    eval_iter = int(evaluation.get("iteration") or 0) if evaluation else 0
    state_iter = int(state.get("iteration") or 0) if state else 0
    issues: list[str] = []
    completion_pass = str((completion or ledger or {}).get("result") or "").lower() == "pass"
    lag_prefix = "non-blocking metadata lag" if completion_pass else "stale"
    if latest_evidence and eval_iter < latest_evidence:
        issues.append(f"evaluation.json {lag_prefix}: iteration {eval_iter} < latest evidence iter-{latest_evidence}")
    if latest_evidence and state_iter < latest_evidence:
        issues.append(f"runtime-state {lag_prefix}: iteration {state_iter} < iter-{latest_evidence}")
    if evaluation and not isinstance(evaluation.get("testResults"), list):
        issues.append("evaluation.json missing testResults[]")
    elif evaluation and isinstance(evaluation.get("testResults"), list) and not evaluation.get("testResults"):
        issues.append("evaluation.json testResults[] is empty")
    return {
        "latestEvidenceIteration": latest_evidence or None,
        "runtimeStateIteration": state_iter or None,
        "evaluationIteration": eval_iter or None,
        "evaluationTestResults": len(evaluation.get("testResults") or []) if isinstance(evaluation.get("testResults"), list) else None,
        "completionGeneratedAt": completion.get("generatedAt"),
        "ledgerGeneratedAt": ledger.get("generatedAt"),
        "issues": issues,
    }


def cmd_status(task_code: str) -> None:
    """Show task status and gate guidance."""
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        return

    reconcile_task_state(task_dir, reason="status")
    state_check = check_state_summary(task_dir, repair=True, reason="status")
    workflow_control_state = ensure_workflow_state(task_dir)
    state = read_runtime_state(task_dir)
    if state:
        print(f"Task: {state.get('taskId', task_code)}")
        print(f"Status: {state.get('status', 'unknown')}")
        print(f"Task type: {state.get('taskType', '-')}")
        try:
            display_max_iterations = max(MAX_ITERATIONS, int(state.get('maxIterations', 0) or 0))
        except (TypeError, ValueError):
            display_max_iterations = MAX_ITERATIONS
        print(f"Iteration: {state.get('iteration', 0)} / {display_max_iterations}")
        print(f"Current owner: {state.get('currentOwner', '-')}")
        print(f"Runtime next: {state.get('nextAction', '-')}")
        workflow_state_error = state.get('workflowStateError') if isinstance(state.get('workflowStateError'), dict) else {}
        if workflow_state_error:
            print(f"Workflow state sync error: {workflow_state_error.get('error', '-')} at {workflow_state_error.get('at', '-')}; iteration={workflow_state_error.get('iteration', '-')}; phase={workflow_state_error.get('phase', '-')}")
        authority = state.get('stateAuthority') if isinstance(state.get('stateAuthority'), dict) else {}
        if authority:
            print(f"State authority: {authority.get('role', '-')} / reason={authority.get('reason', '-')}")
            effective_state = authority.get('effective') if isinstance(authority.get('effective'), dict) else {}
            if effective_state:
                print(f"Derived state: {effective_state.get('status', '-')}/{effective_state.get('currentOwner', '-')}/{effective_state.get('nextAction', '-')}")
        if state_check.get("repairs"):
            print("State repair: " + "; ".join(state_check.get("repairs", [])[:3]))
        instruction = build_next_instruction(task_code, task_dir)
        effective = instruction.get("effectiveNext") if isinstance(instruction.get("effectiveNext"), dict) else {}
        instruction_phase_transition = instruction.get("phaseSummary") if isinstance(instruction.get("phaseSummary"), dict) else instruction.get("stateSummary") if isinstance(instruction.get("stateSummary"), dict) else {}
        phase_transition = instruction_phase_transition or (state.get("stateSummary") if isinstance(state.get("stateSummary"), dict) else {})
        if workflow_control_state:
            print("Workflow control state:")
            print(f"- current: {workflow_control_state.get('currentStage', '-')} / {workflow_control_state.get('currentPhase', '-')} / {workflow_control_state.get('currentAction', '-')}")
            print(f"- owner: {workflow_control_state.get('currentOwner', '-')}")
            print(f"- next: {workflow_control_state.get('nextPhase', '-')} / {workflow_control_state.get('nextAction', '-')}")
            print(f"- plannedNextPhase: {workflow_control_state.get('plannedNextPhase', '-')}")
            print(f"- iteration: {workflow_control_state.get('iteration', 0)}; stateHealth={workflow_control_state.get('stateHealth', '-')}; lastEventId={workflow_control_state.get('lastEventId', '-')}")
            stage_state = read_stage_state(task_dir, str(workflow_control_state.get('currentStage') or ''))
            stage_iteration = stage_state.get('iteration') if isinstance(stage_state.get('iteration'), dict) else {}
            if stage_iteration:
                print(f"- stage iteration: current={stage_iteration.get('current', '-')} phase={stage_iteration.get('phase', '-')} lastResult={stage_iteration.get('lastResult', '-')} retryable={stage_iteration.get('retryable', '-')}")
        if phase_transition:
            print(f"Phase next: {phase_transition.get('nextPhase', '-')} / {phase_transition.get('nextAction', '-')} / {phase_transition.get('nextOwner', '-')}")
            print(f"Phase reason: {phase_transition.get('reason', '-')}")
        print(f"Effective next: {effective.get('summary') or effective.get('action') or '-'}")
        if state.get('lastResult'):
            print(f"Last result: {state.get('lastResult')}")
        print(f"Updated at: {state.get('updatedAt', '-')}")
        freshness = _freshness_summary(task_dir, state)
        print("")
        print("Freshness / evidence sync:")
        print(f"- latest evidence iteration: {freshness.get('latestEvidenceIteration') or '-'}")
        print(f"- runtime-state iteration: {freshness.get('runtimeStateIteration') or '-'}")
        print(f"- evaluation iteration: {freshness.get('evaluationIteration') or '-'}; testResults={freshness.get('evaluationTestResults') if freshness.get('evaluationTestResults') is not None else '-'}")
        if freshness.get('completionGeneratedAt') or freshness.get('ledgerGeneratedAt'):
            print(f"- completion generatedAt: {freshness.get('completionGeneratedAt') or '-'}; ledger generatedAt: {freshness.get('ledgerGeneratedAt') or '-'}")
        for issue in freshness.get('issues') or []:
            print(f"  ! {issue}")
        purpose = state.get("latestIterationPurpose") if isinstance(state.get("latestIterationPurpose"), dict) else {}
        exploration = state.get("explorationContext") if isinstance(state.get("explorationContext"), dict) else {}
        if purpose or exploration:
            print("")
            print("Iteration purpose / exploration convergence:")
            if purpose:
                print(f"- latest purpose: iter-{purpose.get('iteration', '-')} {purpose.get('phase', '-')} mode={purpose.get('mode', '-')}")
                print(f"- target TCs: {', '.join(purpose.get('targetTestCases') or []) or '-'}")
                print(f"- expected signal: {purpose.get('expectedSignal') or '-'}")
                if purpose.get('path'):
                    print(f"- purpose file: {purpose.get('path')}")
            items = exploration.get("items") if isinstance(exploration.get("items"), list) else []
            for item in items[:5]:
                if not isinstance(item, dict):
                    continue
                ruled = item.get("ruledOut") if isinstance(item.get("ruledOut"), list) else []
                remaining = item.get("remainingHypotheses") if isinstance(item.get("remainingHypotheses"), list) else []
                print(f"  - {item.get('testCaseId')}: attempts={item.get('attemptCount', 0)} ruledOut={len(ruled)} remaining={len(remaining)}")
                if remaining:
                    print(f"    remaining: {', '.join(str(x) for x in remaining[:3])}")
        guidance = build_status_guidance(task_code)
        phase_transition = guidance.get("phaseSummary") if isinstance(guidance.get("phaseSummary"), dict) else guidance.get("stateSummary") if isinstance(guidance.get("stateSummary"), dict) else phase_transition
        if phase_transition:
            print("")
            print("Phase transition detail:")
            print(f"- current: {phase_transition.get('currentPhase', '-')} / {phase_transition.get('currentStatus', '-')} / {phase_transition.get('currentOwner', '-')}")
            print(f"- next: {phase_transition.get('nextPhase', '-')} / {phase_transition.get('nextAction', '-')} / {phase_transition.get('nextOwner', '-')}")
            print(f"- reason: {phase_transition.get('reason', '-')}")
            basis = phase_transition.get("basis") if isinstance(phase_transition.get("basis"), list) else []
            for item in basis[:5]:
                print(f"  - {item}")
        print("")
        print("Next recommended action:")
        print(f"- Reason: {guidance.get('reason', '-')}")
        for item in guidance.get("recommended", []):
            print(f"- {item}")
        commands = guidance.get("commands", [])
        if commands:
            print("")
            print("Suggested commands:")
            for command in commands:
                print(f"- {command}")
        read_files = guidance.get("readFiles", [])
        if read_files:
            print("")
            print("Read/inspect:")
            for path in read_files:
                print(f"- {path}")
        workflow = guidance.get("workflowCheck", {})
        completion = guidance.get("completionCheck", {})
        summary_reuse = guidance.get("summaryReuse", {})
        checklist = guidance.get("planChecklist", {})
        print("")
        print("Gate summary:")
        print(f"- workflow-check: {workflow.get('result')} ({workflow.get('issueCount', 0)} issues, {workflow.get('warningCount', 0)} warnings)")
        print(f"- completion-check: {completion.get('result')} ({completion.get('issueCount', 0)} issues)")
        print(f"- summary/reuse: {summary_reuse.get('result', 'not_run')} ({summary_reuse.get('reason', '-')})")
        workflow_state = guidance.get("workflowState") if isinstance(guidance.get("workflowState"), dict) else {}
        if workflow_state:
            expected_next = workflow_state.get("expectedNext") if isinstance(workflow_state.get("expectedNext"), list) else []
            expected_text = ", ".join(str(item.get("phase")) for item in expected_next if isinstance(item, dict) and item.get("phase")) or "-"
            print("")
            print("Workflow contract signal (local resolver input):")
            print(f"- result: {workflow_state.get('result', '-')}")
            print(f"- issues/warnings: {workflow_state.get('issueCount', 0)} / {workflow_state.get('warningCount', 0)}")
            print(f"- expectedNext: {expected_text}")
            target = workflow_state.get("target") if isinstance(workflow_state.get("target"), dict) else {}
            print(f"- target: {target.get('finalPhase', '-')} / {target.get('successCondition', '-')}")
        if checklist:
            impl = checklist.get("implementation", {})
            ver = checklist.get("verification", {})
            if impl or ver:
                fmt = lambda data: ", ".join(f"{key}={value}" for key, value in sorted(data.items())) or "-"
                print("")
                print("Plan checklist:")
                print(f"- implementation: {fmt(impl)}")
                print(f"- verification: {fmt(ver)}")
        print_report_manifest(task_code, heading="Reports to inspect / share")
        return

    val_path = task_dir / "Validation.md"
    if val_path.exists():
        content = val_path.read_text()
        for line in content.split('\n'):
            if '## Status' in line:
                print(f"\n{line}")
            elif '<!--' in line and ('In Progress' in line or 'Fail' in line or 'Finished' in line):
                status = line.split('<!--')[1].split('-->')[0].strip()
                print(f"  Status: {status}")
    else:
        print("  Status: initializing...")
        print("")
        print("Next recommended action:")
        print(f"- Run ./automind.sh scaffold \"<request>\" or inspect {task_dir}/ for missing runtime-state.json.")


def cmd_logs(task_code: Optional[str] = None) -> None:
    """Show generator logs for one task, or compact status for all tasks."""
    if task_code:
        task_dir = get_task_dir(task_code)
        if not task_dir.exists():
            error(f"Task does not exist: {task_code}")
            return
        log_cmd = ["tail", "-f", "-n", "50"]
        logs_dir = task_dir / "logs"
        if logs_dir.exists():
            iters = sorted(logs_dir.glob("iter-*"), key=lambda p: p.name)
            if iters:
                latest_iter = iters[-1]
                log_file = latest_iter / "generator.log"
                if log_file.exists():
                    log_cmd.append(str(log_file))
                    run_cmd(log_cmd)
                    return
        error("No logs yet")
    else:
        for task in list_tasks():
            print(f"\n=== {task} ===")
            cmd_status(task)
