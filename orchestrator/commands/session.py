"""Low-risk session/report command handlers for the AutoMind CLI."""
from __future__ import annotations

import json
import sys
from typing import Any

from orchestrator.console import error, success, warn
from orchestrator.hooks import run_before_phase_hooks
from orchestrator.reports import build_critical_artifacts, generate_html_report
from orchestrator.agents import extract_agent_reply, run_agent
from orchestrator.session.answers import apply_user_answer, resolve_selected_option
from orchestrator.session.events import append_event
from orchestrator.session.instructions import build_next_instruction
from orchestrator.workflow_state import ensure_workflow_state, read_stage_state
from orchestrator.session.messages import append_user_message
from orchestrator.session.trace import build_trace, render_trace_text, write_trace
from orchestrator.state import clear_current_task, get_task_dir, get_tui_chat_task_code, read_current_task, read_runtime_state, rel_to_root
from orchestrator.tui.app import run_tui


def print_answer_usage() -> None:
    """Print answer command help without recording a user answer."""
    print("Usage: answer <task-code> --text TEXT | --option ID | --json JSON | TEXT")
    print("Examples:")
    print("  automind answer task01 --option 1")
    print("  automind answer task01 --text '同意，继续实现并用真机验证'")


def cmd_answer(task_code: str, args: list[str]) -> None:
    """Record a user answer for a pending ask_user question."""
    if task_code in {"-h", "--help"} or any(arg in {"-h", "--help"} for arg in args):
        print_answer_usage()
        return
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)
    answer_text = ""
    selected_option = None
    idx = 0
    while idx < len(args):
        item = args[idx]
        if item == "--text" and idx + 1 < len(args):
            answer_text = args[idx + 1]
            idx += 2
        elif item == "--option" and idx + 1 < len(args):
            selected_option = args[idx + 1]
            idx += 2
        elif item == "--json" and idx + 1 < len(args):
            try:
                data = json.loads(args[idx + 1])
            except json.JSONDecodeError as exc:
                error(f"Invalid --json answer payload: {exc}")
                sys.exit(1)
            answer_text = str(data.get("answerText") or data.get("text") or answer_text)
            selected_option = data.get("selectedOption") or data.get("option") or selected_option
            idx += 2
        else:
            # Treat remaining positional tokens as answer text for simple CLI/skill use.
            answer_text = " ".join(args[idx:]).strip()
            break
    if not answer_text and not selected_option:
        error("answer requires --text, --option, --json, or positional answer text")
        sys.exit(1)

    selected_option = resolve_selected_option(task_dir, selected_option) or selected_option
    answer = apply_user_answer(task_dir, answer_text=answer_text, selected_option=selected_option)
    print(json.dumps({"result": "ok", "task": task_code, "answer": answer}, ensure_ascii=False, indent=2))


def cmd_event(task_code: str, args: list[str]) -> None:
    """Append a shared AutoMind event for skill/TUI timelines."""
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)
    event_type = "note"
    message = ""
    phase = None
    replace_key = None
    level = "info"
    idx = 0
    while idx < len(args):
        item = args[idx]
        if item == "--type" and idx + 1 < len(args):
            event_type = args[idx + 1]
            idx += 2
        elif item == "--message" and idx + 1 < len(args):
            message = args[idx + 1]
            idx += 2
        elif item == "--phase" and idx + 1 < len(args):
            phase = args[idx + 1]
            idx += 2
        elif item == "--replace-key" and idx + 1 < len(args):
            replace_key = args[idx + 1]
            idx += 2
        elif item == "--level" and idx + 1 < len(args):
            level = args[idx + 1]
            idx += 2
        else:
            message = " ".join(args[idx:]).strip()
            break
    if not message:
        message = event_type
    event = append_event(task_dir, event_type, message, level=level, phase=phase, replace_key=replace_key, source="cli")
    print(json.dumps({"result": "ok", "task": task_code, "event": event}, ensure_ascii=False, indent=2))


def cmd_trace(task_code: str, args: list[str]) -> None:
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)
    as_json = "--json" in args
    write_file = "--write" in args or "--save" in args
    trace = write_trace(task_code, task_dir) if write_file else build_trace(task_code, task_dir)
    if as_json:
        print(json.dumps(trace, ensure_ascii=False, indent=2))
    else:
        print(render_trace_text(trace))
        if write_file:
            success(f"Trace written: {task_dir / 'trace.json'}")


def cmd_tui(task_code: str, args: list[str]) -> None:
    """Open the shared AutoMind TUI snapshot/watch/interactive view."""
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)
    watch = "--watch" in args
    interactive = "--interactive" in args or "-i" in args
    run_tui(task_code, task_dir, watch=watch, interactive=interactive)


def cmd_report(task_code: str) -> None:
    """Generate the human-readable HTML report for a task."""
    path = generate_html_report(task_code)
    task_dir = get_task_dir(task_code)
    critical_artifacts = [
        {
            "title": item.get("title"),
            "path": rel_to_root(task_dir / item.get("path")),
            "tcIds": item.get("tcIds") or [],
            "anchors": item.get("anchors") or [],
            "exists": bool(item.get("exists")),
        }
        for item in build_critical_artifacts(task_dir, max_items=6)
    ]
    key_paths = ", ".join(item["path"] for item in critical_artifacts[:3]) or "Test Results"
    handoff_summary = (
        f"任务 {task_code} 的人类可读报告已生成：{rel_to_root(path)}。"
        "请优先打开 Report.html 查看 Test Results；每个 TC 行会直接展示对应截图、关键 evidence 和日志。"
        f"建议重点核对：{key_paths}。"
    )
    print(json.dumps({
        "result": "pass",
        "task": task_code,
        "report": rel_to_root(path),
        "handoffSummary": handoff_summary,
        "criticalArtifacts": critical_artifacts,
    }, ensure_ascii=False, indent=2))


def cmd_continue(task_code: str | None = None) -> None:
    """Skill-mode automation: print shared next-step context for a task."""
    if not task_code:
        task_code = read_current_task()
    if not task_code:
        warn("No active task marker found; nothing to continue.")
        print(json.dumps({"result": "no_active_task"}, ensure_ascii=False, indent=2))
        sys.exit(1)
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist or current-task points to missing task dir: {task_code}")
        if read_current_task() == task_code:
            clear_current_task()
        print(json.dumps({"result": "stale_marker", "task": task_code}, ensure_ascii=False, indent=2))
        sys.exit(1)
    payload = build_next_instruction(task_code, task_dir)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _phase_gate_stage_to_phase(stage: str) -> str | None:
    return {
        "plan": "planning",
        "planning": "planning",
        "build": "delivery",
        "generator": "delivery",
        "delivery": "delivery",
        "verify": "evaluation",
        "evaluator": "evaluation",
        "evaluation": "evaluation",
        "finish": "terminal",
        "completion": "terminal",
        "auto": None,
    }.get(stage)


def _safe_json_file(path):
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}




def _phase_gate_route_source(
    workflow_control_state: dict[str, Any],
    phase_transition: dict[str, Any],
    effective: dict[str, Any],
    instruction: dict[str, Any],
) -> tuple[str, str, str, list[str]]:
    """Return next phase/action from one source and report cross-source drift."""
    candidates: list[tuple[str, str, str]] = []
    if workflow_control_state.get("nextPhase") or workflow_control_state.get("nextAction"):
        candidates.append(("workflowControlState", str(workflow_control_state.get("nextPhase") or ""), str(workflow_control_state.get("nextAction") or "")))
    if phase_transition.get("nextPhase") or phase_transition.get("nextAction"):
        candidates.append(("phaseSummary", str(phase_transition.get("nextPhase") or ""), str(phase_transition.get("nextAction") or "")))
    # effectiveNext may intentionally override the route to resolve workflow
    # blockers (for example a freshly refreshed reuse gate that now requires
    # acknowledgement). That is not a route-source drift; cmd_phase_gate has a
    # dedicated branch that fails with the workflow-check command.
    if (effective.get("phase") or effective.get("action")) and effective.get("action") != "resolve_workflow_blockers":
        candidates.append(("effectiveNext", str(effective.get("phase") or ""), str(effective.get("action") or "")))
    if instruction.get("nextAction"):
        candidates.append(("instruction", "", str(instruction.get("nextAction") or "")))
    if not candidates:
        return "", "", "none", []
    source, phase, action = candidates[0]
    drift: list[str] = []
    for other_source, other_phase, other_action in candidates[1:]:
        if other_phase and phase and other_phase != phase:
            drift.append(f"{other_source}.nextPhase={other_phase} differs from {source}.nextPhase={phase}")
        if other_action and action and other_action != action:
            drift.append(f"{other_source}.nextAction={other_action} differs from {source}.nextAction={action}")
    return phase, action, source, drift


_PHASE_GATE_REUSE_TARGETS = {
    "delivery": "generator",
    "evaluation": "evaluator",
}

_PHASE_REUSE_INPUTS = {
    # Use human-authored/source artifacts for freshness. workflow-check may
    # regenerate workflow.json and JSON sidecars after reuse-ack; treating those
    # derived files as reuse inputs resets the acknowledgement on every
    # phase-gate call. Markdown/source artifacts still capture semantic changes
    # that should refresh phase reuse.
    "generator": [
        "Reuse.md",
        "Brainstorm.md",
        "Requirements.md",
        "TestCases.md",
        "Plan.md",
        "Validation.md",
    ],
    "evaluator": [
        "Reuse.md",
        "Requirements.md",
        "TestCases.md",
        "Plan.md",
        "Delivery.md",
    ],
}


def _phase_reuse_refresh_reason(task_dir, reuse_phase: str) -> str | None:
    """Return why phase-gate should refresh phase reuse, or None when fresh.

    Runtime state is deliberately excluded from freshness checks because the
    before-phase hook writes runtime-state; including it would reset reuse ack on
    every phase-gate call.
    """
    phase_reuse_path = task_dir / "phase-reuse" / f"{reuse_phase}.md"
    if not phase_reuse_path.exists() or not phase_reuse_path.read_text(errors="ignore").strip():
        return "missing_phase_reuse"

    state = read_runtime_state(task_dir) or {}
    reuse_gate = state.get("reuseGate") if isinstance(state.get("reuseGate"), dict) else {}
    if not isinstance(reuse_gate.get(reuse_phase), dict):
        return "missing_reuse_gate"

    try:
        phase_reuse_mtime = phase_reuse_path.stat().st_mtime
    except OSError:
        return "missing_phase_reuse"

    for name in _PHASE_REUSE_INPUTS.get(reuse_phase, []):
        path = task_dir / name
        if not path.exists():
            continue
        try:
            if path.stat().st_mtime > phase_reuse_mtime + 0.001:
                return f"stale_after:{name}"
        except OSError:
            continue
    return None


def _maybe_refresh_phase_reuse_for_phase_gate(task_dir, next_phase: str | None) -> dict[str, Any]:
    reuse_phase = _PHASE_GATE_REUSE_TARGETS.get(str(next_phase or ""))
    if not reuse_phase:
        return {"refreshed": False, "reason": "not_delivery_or_evaluation"}

    phase_reuse_path = task_dir / "phase-reuse" / f"{reuse_phase}.md"
    refresh_reason = _phase_reuse_refresh_reason(task_dir, reuse_phase)
    if not refresh_reason:
        return {
            "refreshed": False,
            "phase": reuse_phase,
            "phaseReusePath": rel_to_root(phase_reuse_path),
            "reason": "fresh",
        }

    hook = run_before_phase_hooks(task_dir, reuse_phase, reason=f"phase_gate:{next_phase}:{refresh_reason}")
    return {
        "refreshed": True,
        "phase": reuse_phase,
        "phaseReusePath": hook.get("phaseReusePath") or rel_to_root(phase_reuse_path),
        "reason": refresh_reason,
        "matchCount": hook.get("matchCount"),
        "reuseGateRequired": hook.get("reuseGateRequired"),
        "reuseGateAcknowledged": hook.get("reuseGateAcknowledged"),
    }


def cmd_phase_gate(task_code: str, args: list[str]) -> None:
    """Skill/command-mode phase handoff gate driven by workflow control state."""
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)
    stage = "auto"
    soft = "--soft" in args
    for item in args:
        if item in {"--soft", "--json"}:
            continue
        if item.startswith("--stage="):
            stage = item.split("=", 1)[1].strip().lower() or "auto"
        elif item == "--text":
            continue
        elif not item.startswith("--"):
            stage = item.strip().lower() or "auto"
            break
    expected_phase = _phase_gate_stage_to_phase(stage)
    if stage not in {"auto", "plan", "planning", "build", "generator", "delivery", "verify", "evaluator", "evaluation", "finish", "completion"}:
        error(f"Unknown phase-gate stage: {stage}")
        sys.exit(1)

    workflow_control_state = ensure_workflow_state(task_dir)
    instruction = build_next_instruction(task_code, task_dir)
    instruction_workflow_state = instruction.get("workflowControlState") if isinstance(instruction.get("workflowControlState"), dict) else {}
    if instruction_workflow_state:
        workflow_control_state = instruction_workflow_state
    stage_state = read_stage_state(task_dir, str(workflow_control_state.get("currentStage") or "")) if workflow_control_state else {}
    phase_transition = instruction.get("phaseSummary") if isinstance(instruction.get("phaseSummary"), dict) else instruction.get("stateSummary") if isinstance(instruction.get("stateSummary"), dict) else {}
    effective = instruction.get("effectiveNext") if isinstance(instruction.get("effectiveNext"), dict) else {}
    pending = instruction.get("pendingQuestion") if isinstance(instruction.get("pendingQuestion"), dict) else None
    next_phase, next_action, route_source, route_drift = _phase_gate_route_source(
        workflow_control_state,
        phase_transition,
        effective,
        instruction,
    )
    phase_reuse_refresh = {"refreshed": False, "reason": "not_checked"}
    if (
        not pending
        and not route_drift
        and next_phase in _PHASE_GATE_REUSE_TARGETS
        and (expected_phase is None or expected_phase == next_phase)
        and effective.get("action") != "resolve_workflow_blockers"
    ):
        phase_reuse_refresh = _maybe_refresh_phase_reuse_for_phase_gate(task_dir, next_phase)
        if phase_reuse_refresh.get("refreshed"):
            workflow_control_state = ensure_workflow_state(task_dir)
            instruction = build_next_instruction(task_code, task_dir)
            instruction_workflow_state = instruction.get("workflowControlState") if isinstance(instruction.get("workflowControlState"), dict) else {}
            if instruction_workflow_state:
                workflow_control_state = instruction_workflow_state
            stage_state = read_stage_state(task_dir, str(workflow_control_state.get("currentStage") or "")) if workflow_control_state else {}
            phase_transition = instruction.get("phaseSummary") if isinstance(instruction.get("phaseSummary"), dict) else instruction.get("stateSummary") if isinstance(instruction.get("stateSummary"), dict) else {}
            effective = instruction.get("effectiveNext") if isinstance(instruction.get("effectiveNext"), dict) else {}
            pending = instruction.get("pendingQuestion") if isinstance(instruction.get("pendingQuestion"), dict) else None
            next_phase, next_action, route_source, route_drift = _phase_gate_route_source(
                workflow_control_state,
                phase_transition,
                effective,
                instruction,
            )
    result = "pass"
    can_proceed = True
    reason = phase_transition.get("reason") or effective.get("summary") or instruction.get("nextActionPrompt") or "phase handoff gate passed"
    required_command = None

    if pending:
        result = "ask_user"
        can_proceed = False
        reason = "pending ask_user question must be answered before phase handoff"
        required_command = f"automind answer {task_code} --text '<answer>'"
    elif effective.get("action") == "resolve_workflow_blockers":
        result = "fail"
        can_proceed = False
        reason = effective.get("summary") or "workflow-check blockers must be resolved before handoff"
        required_command = f"automind workflow-check {task_code}"
    elif route_drift:
        result = "fail"
        can_proceed = False
        reason = "phase-gate route sources disagree: " + "; ".join(route_drift[:3])
        required_command = f"automind status {task_code}"
    elif expected_phase and expected_phase != next_phase:
        # Finish is intentionally stricter: terminal only after completion-check.
        result = "fail"
        can_proceed = False
        reason = f"requested stage {stage} expects nextPhase={expected_phase}, but workflow control route says nextPhase={next_phase or '-'}"
        required_command = f"automind phase-gate {task_code} auto"
    elif expected_phase == "delivery":
        workflow = instruction.get("workflowSignal") if isinstance(instruction.get("workflowSignal"), dict) else {}
        issue_count = int(workflow.get("issueCount") or 0)
        if issue_count:
            result = "fail"
            can_proceed = False
            reason = "Build handoff blocked: workflow-check still has issues"
            required_command = f"automind workflow-check {task_code}"
    elif expected_phase == "evaluation" and not (task_dir / "Delivery.md").exists() and not (task_dir / "delivery.json").exists():
        result = "fail"
        can_proceed = False
        reason = "Verify handoff blocked: Delivery.md or delivery.json is required before Evaluator"
        required_command = f"automind workflow-check {task_code}"
    elif expected_phase == "terminal":
        summary_stage = read_stage_state(task_dir, "summary")
        completion = (summary_stage.get("completion") if isinstance(summary_stage.get("completion"), dict) else {}) or _safe_json_file(task_dir / "completion-report.json")
        if completion.get("result") != "pass":
            result = "fail"
            can_proceed = False
            reason = "Finish handoff blocked: completion-check has not passed"
            required_command = f"automind completion-check {task_code}"

    gate = {
        "result": result,
        "canProceed": can_proceed,
        "task": task_code,
        "requestedStage": stage,
        "expectedPhase": expected_phase,
        "centralJson": "automind-workflow-state.json",
        "workflowControlState": workflow_control_state,
        "stageState": stage_state,
        "phaseSummary": phase_transition,
        "stateSummary": phase_transition,
        "effectiveNext": effective,
        "nextAction": next_action,
        "routeSource": route_source,
        "routeDrift": route_drift,
        "phaseReuseRefresh": phase_reuse_refresh,
        "reason": reason,
        "requiredCommand": required_command,
        "checklist": phase_transition.get("checklist") or [],
        "checkboxMarkdown": phase_transition.get("checkboxMarkdown") or [],
        "nextActionPrompt": instruction.get("nextActionPrompt"),
        "readFiles": [
            "automind-workflow-state.json",
            "automind-workflow-events.jsonl",
            "stages/*-stage-state.json",
            "runtime-state.json",
            "workflow.json",
            "evaluation.json (compatibility artifact)",
            "completion-report.json (compatibility artifact)",
        ],
        "fallbackWhenCliUnavailable": [
            "Read automind-workflow-state.json as the central workflow control state.",
            "Validate automind-workflow-state.json against schemas/automind-workflow-state.schema.json when available.",
            "Use runtime-state.json.stateSummary only as obsolete fallback/diagnostic.",
            "Validate the current phase sidecar and workflow.json before handoff.",
            "Before Finish, require stages/summary-stage-state.json completion.result=pass; completion-report.json is fallback.",
        ],
    }
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    if not soft and result not in {"pass"}:
        sys.exit(2)


def cmd_message(task_code: str, args: list[str], *, resume_callback=None) -> None:
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)
    text = ""
    resume_agent = None
    if "--text" in args:
        idx = args.index("--text")
        if idx + 1 < len(args):
            text = args[idx + 1]
    elif args:
        # Allows: automind message <task> natural language text...
        chunks = []
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                continue
            if arg == "--resume":
                skip_next = True
                continue
            chunks.append(arg)
        text = " ".join(chunks).strip()
    if "--resume" in args:
        idx = args.index("--resume")
        resume_agent = args[idx + 1] if len(args) > idx + 1 and not args[idx + 1].startswith("--") else "auto"
    if not text:
        error("Usage: message <task-code> --text TEXT [--resume [agent]]")
        sys.exit(1)
    message = append_user_message(task_dir, text, source="tui_shell")
    success(f"User message recorded: {message.get('id')}")
    if resume_agent:
        tui_chat_code = get_tui_chat_task_code()
        state = read_runtime_state(task_dir) or {}
        if task_code == tui_chat_code or str(state.get("status") or "") == "chat":
            primary = state.get("agentSessions", {}).get("primary", {}) if isinstance(state.get("agentSessions"), dict) else {}
            chat_agent = primary.get("agent") if resume_agent == "auto" and primary.get("agent") else resume_agent
            print(f"\033[2m[AutoMind] coding-agent chat ({chat_agent})...\033[0m")
            code, output = run_agent("cli", chat_agent, text, task_dir, phase="generator", quiet=True)
            reply = extract_agent_reply(chat_agent, output)
            if code == 0:
                print(f"\n\033[1;36m{chat_agent}>\033[0m {reply}\n")
            else:
                print(reply or output)
            return
        if resume_callback is None:
            from orchestrator.main import cmd_resume as resume_callback  # lazy fallback
        resume_callback(task_code, resume_agent, tui=True)
