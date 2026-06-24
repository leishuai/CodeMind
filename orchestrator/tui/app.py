"""Minimal AutoMind TUI timeline renderer.

This dependency-light terminal UI renders the shared `events.jsonl` timeline,
workflow state, heartbeat, trace summary, and pending ask_user prompt.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.session.ask_user import normalize_pending_question
from orchestrator.session.events import append_event, read_events, render_timeline_events
from orchestrator.session.answers import latest_pending_answer_matches_question
from orchestrator.session.instructions import build_next_instruction
from orchestrator.session.trace import build_trace
from orchestrator.state import read_runtime_state
from orchestrator.version import automind_version_label
from orchestrator.tui.style import BLUE, CYAN, GRAY, GREEN, MAGENTA, RED, YELLOW, level_color, status_color, style
from orchestrator.tui.input import tui_input

def _main_py() -> Path:
    return Path(__file__).resolve().parents[1] / "main.py"


LOGO = r"""
    /\         _        __  __ _           _
   /  \  _   _| |_ ___ |  \/  (_)_ __   __| |
  / /\ \  | | | __/ _ \| |\/| | | '_ \ / _` |
 / ____ \ |_| | || (_) | |  | | | | | | (_| |
/_/    \_\__,_|\__\___/|_|  |_|_|_| |_|\__,_|
""".strip("\n")




def _shorten(text: object, *, max_chars: int = 300) -> str:
    value = str(text or "").replace("\t", "    ")
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"




def _agent_output_limit() -> int | None:
    raw = os.environ.get("AUTOMIND_TUI_AGENT_OUTPUT_LINES", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _compact_timeline_events(events: list[dict[str, Any]], *, max_agent_output: int | None = None) -> list[dict[str, Any]]:
    if max_agent_output is None:
        max_agent_output = _agent_output_limit()
    if max_agent_output is None:
        return events
    total_agent_output = sum(1 for event in events if event.get("type") == "agent_output")
    if total_agent_output <= max_agent_output:
        return events
    remaining_agent_output = max_agent_output
    compact_reversed: list[dict[str, Any]] = []
    for event in reversed(events):
        if event.get("type") == "agent_output":
            if remaining_agent_output <= 0:
                continue
            remaining_agent_output -= 1
        compact_reversed.append(event)
    hidden = total_agent_output - max_agent_output
    marker = {
        "ts": "",
        "type": "agent_output_compacted",
        "level": "info",
        "message": f"{hidden} earlier agent output lines hidden in TUI; full transcript remains in events.jsonl / phase logs",
        "source": "tui",
    }
    return [marker, *reversed(compact_reversed)]








def _recent_events_for_input_focus(raw_events: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    """For human-input screens, keep timeline focused on this prompt session.

    Old generator/tui events are still available in events.jsonl, but showing
    them below the active question makes the prompt feel stale. Prefer events
    since the latest TUI session start; otherwise use the runtime state's updatedAt
    as a conservative anchor.
    """
    anchor_ts = ""
    for event in raw_events:
        if event.get("type") == "tui_session_started" and event.get("ts"):
            anchor_ts = str(event.get("ts"))
    if not anchor_ts:
        anchor_ts = str(state.get("updatedAt") or "")
    if not anchor_ts:
        return raw_events
    filtered = [event for event in raw_events if str(event.get("ts") or "") >= anchor_ts]
    return filtered or raw_events[-5:]


def _now_prefix() -> str:
    return datetime.now().strftime("[%H:%M:%S]")


def _heartbeat_from_events(raw_events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(raw_events):
        if event.get("type") == "heartbeat_status" and event.get("ts"):
            return {
                "lastBeatAt": event.get("ts"),
                "owner": event.get("data", {}).get("owner") if isinstance(event.get("data"), dict) else "",
                "note": "event-fallback",
            }
    return {}


def _agent_output_kind(message: str) -> str:
    stripped = message.strip()
    lower = stripped.lower()
    if re.match(r"^\*\*.+\*\*$", stripped):
        return "progress"
    if lower.startswith(("i'm ", "i am ", "i’ll ", "i'll ", "i need ", "i’m ", "let me ", "now ")):
        return "analysis"
    if "error" in lower or "exception" in lower or "failed" in lower or "traceback" in lower:
        return "error"
    if lower.startswith(("final", "summary", "done", "completed", "result", "answer")):
        return "result"
    if re.match(r"^\d+:", stripped):
        return "tool"
    return "output"


def _agent_output_kind_label(kind: str) -> str:
    labels = {
        "progress": "progress",
        "analysis": "visible analysis",
        "error": "error",
        "result": "result",
        "tool": "tool",
        "output": "output",
    }
    return labels.get(kind, "output")


def _agent_output_kind_color(kind: str) -> str:
    if kind == "error":
        return RED
    if kind == "result":
        return GREEN
    if kind == "progress":
        return BLUE
    if kind == "analysis":
        return YELLOW
    if kind == "tool":
        return CYAN
    return GRAY


def _format_agent_message(message: str) -> str:
    match = re.match(r"^(\d+):(.*)$", message)
    if match:
        return f"{style('L' + match.group(1), GRAY)} │ {match.group(2).lstrip()}"
    return message


def _format_timeline_event(event: dict[str, Any], *, max_chars: int = 300) -> list[str]:
    ts = str(event.get("ts", ""))[-8:]
    level = str(event.get("level", "info"))
    event_type = str(event.get("type", "event"))
    phase = str(event.get("phase") or "")
    source = str(event.get("source") or "")
    message = _shorten(event.get("message", ""), max_chars=max_chars)
    head = f"{style('[' + ts + ']', GRAY)} {style(f'{level:<5}', level_color(level), bold=level in {'error','warn','warning'})}"

    if event_type == "agent_output_compacted":
        return [f"{head} {style('agent output', GRAY, bold=True)} — {style(message, GRAY)}"]

    if event_type == "heartbeat_status":
        return [f"{head} {style('♥ heartbeat', YELLOW, bold=True)} — {style(message, CYAN)}"]

    if event_type.startswith("agent_"):
        label = source or "agent"
        phase_text = f"/{phase}" if phase else ""
        if event_type == "agent_output":
            kind = _agent_output_kind(message)
            kind_label = _agent_output_kind_label(kind)
            kind_color = _agent_output_kind_color(kind)
            return [f"{head} {style('agent', CYAN, bold=True)}{style(phase_text, GRAY)} {style(label + ' ›', MAGENTA)} {style(kind_label + ':', kind_color, bold=kind in {'error','result','progress'})} {_format_agent_message(message)}"]
        if event_type == "agent_still_running":
            return [f"{head} {style('agent', YELLOW, bold=True)}{style(phase_text, GRAY)} {style('still running', YELLOW)} — {message}"]
        if event_type == "agent_started":
            return [style("─" * 56, GRAY), f"{head} {style('agent started', CYAN, bold=True)}{style(phase_text, GRAY)} — {message}"]
        if event_type in {"agent_done", "agent_failed", "agent_timeout"}:
            color = GREEN if event_type == "agent_done" else RED
            return [f"{head} {style(event_type.replace('_', ' '), color, bold=True)}{style(phase_text, GRAY)} — {message}", style("─" * 56, GRAY)]

    phase_text = f"[{phase}] " if phase else ""
    return [f"{head} {phase_text}{message}"]


def _parse_iso_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _age_text(raw_ts: object) -> str:
    ts = _parse_iso_ts(raw_ts)
    if not ts:
        return "unknown"
    age = max(0, int((datetime.now() - ts).total_seconds()))
    if age < 60:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m{age % 60:02d}s"
    return f"{age // 3600}h{(age % 3600) // 60:02d}m"


def _event_brief(event: dict[str, Any] | None) -> str:
    if not event:
        return "-"
    source = event.get("source") or "automind"
    event_type = event.get("type") or "event"
    # Keep this header compact. Full agent/Codex output is rendered in the
    # chronological Timeline below Next instruction so the visual order matches
    # the underlying log order.
    return f"{event_type} from {source} age={_age_text(event.get('ts'))}"


def _runtime_status_lines(
    *,
    state: dict[str, Any],
    instruction: dict[str, Any],
    heartbeat: dict[str, Any],
    raw_events: list[dict[str, Any]],
) -> list[str]:
    status = str(state.get("status") or "unknown")
    owner = str(state.get("currentOwner") or "unknown")
    next_action = str(state.get("nextAction") or "unknown")
    effective = instruction.get("effectiveNext") if isinstance(instruction.get("effectiveNext"), dict) else {}
    workflow_control_state = instruction.get("workflowControlState") if isinstance(instruction.get("workflowControlState"), dict) else {}
    phase_transition = instruction.get("phaseSummary") if isinstance(instruction.get("phaseSummary"), dict) else instruction.get("stateSummary") if isinstance(instruction.get("stateSummary"), dict) else state.get("stateSummary") if isinstance(state.get("stateSummary"), dict) else {}
    phase_next = workflow_control_state.get("nextPhase") or phase_transition.get("nextPhase") or effective.get("phase") or "-"
    phase_action = workflow_control_state.get("nextAction") or phase_transition.get("nextAction") or effective.get("action") or next_action
    effective_text = phase_transition.get("reason") or effective.get("summary") or effective.get("action") or "-"
    running_statuses = {"planning", "generating", "evaluating", "running", "validating", "resuming"}
    is_running = status in running_statuses or next_action.startswith("run_") or owner in {"planner", "generator", "evaluator"}
    last_event = raw_events[-1] if raw_events else None
    agent_events = [event for event in raw_events if str(event.get("type", "")).startswith("agent_")]
    last_agent = agent_events[-1] if agent_events else None
    lines: list[str] = []
    if is_running:
        lines.append(style("Input disabled: AutoMind is running.", YELLOW, bold=True))
        lines.append(f"{style('Running:', MAGENTA, bold=True)} status={status} owner={owner} runtime next={next_action}")
        lines.append(f"{style('Phase next:', MAGENTA, bold=True)} {phase_next} / {phase_action}")
        lines.append(f"{style('Effective next:', MAGENTA, bold=True)} {effective_text}")
        lines.append(
            f"{style('Last heartbeat:', MAGENTA, bold=True)} age={_age_text(heartbeat.get('lastBeatAt'))} "
            f"at={heartbeat.get('lastBeatAt', '-')} note={heartbeat.get('note', '')}".rstrip()
        )
        lines.append(f"{style('Last event:', MAGENTA, bold=True)} {_event_brief(last_event)}")
        if last_agent and last_agent is not last_event:
            lines.append(f"{style('Last agent event:', MAGENTA, bold=True)} {_event_brief(last_agent)}")
        lines.append("If this stays quiet for several minutes, use Ctrl+C to pause, then `automind status <task>` / `automind resume <task> <agent>`.")
    else:
        lines.append(style("Input disabled: no pending question. AutoMind is ready to continue/resume.", GRAY))
        lines.append(f"{style('Phase next:', MAGENTA, bold=True)} {phase_next} / {phase_action}")
        lines.append(f"{style('Effective next:', MAGENTA, bold=True)} {effective_text}")
    return lines


def render_tui_snapshot(task_code: str, task_dir: Path, *, limit: int = 80, show_logo: bool = True) -> str:
    state = read_runtime_state(task_dir) or {}
    instruction = build_next_instruction(task_code, task_dir)
    workflow_state = instruction.get("workflowState") if isinstance(instruction.get("workflowState"), dict) else {}
    workflow_control_state = instruction.get("workflowControlState") if isinstance(instruction.get("workflowControlState"), dict) else {}
    phase_transition = instruction.get("phaseSummary") if isinstance(instruction.get("phaseSummary"), dict) else instruction.get("stateSummary") if isinstance(instruction.get("stateSummary"), dict) else state.get("stateSummary") if isinstance(state.get("stateSummary"), dict) else {}
    pending = normalize_pending_question(task_dir)
    timeline_pending_answer = latest_pending_answer_matches_question(task_dir, pending) if pending else None
    awaiting_human_answer = bool(
        pending
        and not timeline_pending_answer
        and state.get("status") == "human_input_pending"
        and str(state.get("currentOwner") or "") in {"human", ""}
    )
    heartbeat = state.get("heartbeat") if isinstance(state.get("heartbeat"), dict) else {}
    raw_events = read_events(task_dir, limit=limit)
    if not heartbeat.get("lastBeatAt"):
        heartbeat = _heartbeat_from_events(raw_events)
    timeline_raw_events = _recent_events_for_input_focus(raw_events, state) if awaiting_human_answer else raw_events
    events = _compact_timeline_events(render_timeline_events(timeline_raw_events), max_agent_output=0 if awaiting_human_answer else None)
    lines: list[str] = []
    if show_logo:
        lines.append(style(LOGO, CYAN, bold=True))
        lines.append(style(automind_version_label(), BLUE, bold=True))
        lines.append(style("Workflow-driven coding harness", GRAY))
        lines.append(style("─" * 56, GRAY))
    lines.append(f"{style('Task:', MAGENTA, bold=True)} {style(task_code, CYAN, bold=True)}")
    status = state.get('status', '-')
    lines.append(f"{style('Status:', MAGENTA, bold=True)} {style(status, status_color(status), bold=True)}")
    lines.append(f"{style('Owner:', MAGENTA, bold=True)} {state.get('currentOwner', '-')}")
    lines.append(f"{style('Runtime next:', MAGENTA, bold=True)} {state.get('nextAction', '-')}")
    effective_next = instruction.get("effectiveNext") if isinstance(instruction.get("effectiveNext"), dict) else {}
    phase_next = workflow_control_state.get("nextPhase") or phase_transition.get("nextPhase") or effective_next.get("phase") or "-"
    phase_action = workflow_control_state.get("nextAction") or phase_transition.get("nextAction") or effective_next.get("action") or state.get("nextAction", "-")
    phase_reason = phase_transition.get("reason") or effective_next.get("summary") or "-"
    lines.append(f"{style('Phase next:', MAGENTA, bold=True)} {style(str(phase_next), CYAN)} / {phase_action}")
    lines.append(f"{style('Phase reason:', MAGENTA, bold=True)} {style(str(phase_reason), CYAN)}")
    effective_summary = effective_next.get("summary") or effective_next.get("action") or "-"
    lines.append(f"{style('Effective next:', MAGENTA, bold=True)} {style(str(effective_summary), CYAN)}")
    lines.append(f"{style('Iteration:', MAGENTA, bold=True)} {state.get('iteration', 0)}")
    wf_result = workflow_state.get('result', '-')
    wf_issues = int(workflow_state.get('issueCount') or 0) if isinstance(workflow_state, dict) else 0
    lines.append(f"{style('Workflow control:', MAGENTA, bold=True)} {workflow_control_state.get('currentStage', '-')}/{workflow_control_state.get('currentPhase', '-')} owner={workflow_control_state.get('currentOwner', '-')} next={workflow_control_state.get('nextPhase', '-')}/{workflow_control_state.get('nextAction', '-')} health={workflow_control_state.get('stateHealth', '-')}")
    lines.append(f"{style('Workflow contract:', MAGENTA, bold=True)} {style(wf_result, status_color(wf_result), bold=True)} / issues={style(wf_issues, RED if wf_issues else GREEN, bold=True)}")
    lines.append(f"{style('Phase state:', MAGENTA, bold=True)} current={phase_transition.get('currentPhase', '-')} owner={phase_transition.get('currentOwner', '-')} next={phase_transition.get('nextPhase', '-')}/{phase_transition.get('nextOwner', '-')} (phase summary)")
    lines.append(f"{style('Heartbeat:', MAGENTA, bold=True)} {heartbeat.get('lastBeatAt', '-')} {heartbeat.get('owner', '')} {heartbeat.get('note', '')}".rstrip())
    lines.append(f"{style('Blockers:', MAGENTA, bold=True)} {style(wf_issues, RED if wf_issues else GREEN, bold=True)}")
    trace = build_trace(task_code, task_dir)
    trace_summary = trace.get("summary", {}) if isinstance(trace.get("summary"), dict) else {}
    error_count = trace_summary.get('errorCount', 0)
    lines.append(f"{style('Trace:', MAGENTA, bold=True)} spans={trace_summary.get('spanCount', 0)} errors={style(error_count, RED if error_count else GREEN, bold=True)}")
    lines.append(style("─" * 56, GRAY))
    if pending:
        owner = str(state.get("currentOwner") or "")
        pending_answer = timeline_pending_answer
        if pending_answer:
            prefix = style(_now_prefix(), GRAY)
            lines.append(f"{prefix} {style('Answer recorded: waiting for the current agent to apply it.', YELLOW, bold=True)}")
            lines.append(f"{prefix} {style('Answered question:', YELLOW, bold=True)} {pending_answer.get('question') or pending.get('question')}")
            lines.append(f"{prefix} {style('Recorded answer:', YELLOW, bold=True)} {pending_answer.get('selectedOption') or pending_answer.get('answerText') or '-'}")
        elif state.get("status") == "human_input_pending" and owner in {"human", ""}:
            prefix = style(_now_prefix(), GRAY)
            lines.append(f"{prefix} {style('Input enabled: AutoMind needs user input.', YELLOW, bold=True)}")
            lines.append(f"{prefix} {style('Question:', YELLOW, bold=True)} {pending.get('question')}")
        else:
            prefix = style(_now_prefix(), GRAY)
            lines.append(f"{prefix} {style('Input pending: waiting for current agent to finish before accepting an answer.', YELLOW, bold=True)}")
            lines.append(f"{prefix} {style('Question:', YELLOW, bold=True)} {pending.get('question')}")
        options = pending.get("options") if isinstance(pending.get("options"), list) else []
        for idx, option in enumerate(options, start=1):
            if isinstance(option, dict):
                lines.append(f"  {idx}. {option.get('id', idx)} — {option.get('label', '')} {option.get('impact', '')}".rstrip())
            else:
                lines.append(f"  {idx}. {option}")
        if pending_answer:
            lines.append("No further user input is needed for this question unless the agent asks a new one.")
        elif state.get("status") == "human_input_pending" and str(state.get("currentOwner") or "") in {"human", ""}:
            lines.append("Answer in TUI with option number/id or free text. CLI: automind answer <task> --text '...' or --option <id|number>")
        else:
            lines.append("Answer will be enabled after the current agent turn finishes. Do not answer yet; AutoMind will return to the prompt at the next safe boundary.")
    else:
        lines.extend(_runtime_status_lines(state=state, instruction=instruction, heartbeat=heartbeat, raw_events=raw_events))
    lines.append(style("─" * 56, GRAY))
    lines.append(style("Next instruction:", BLUE, bold=True))
    lines.append(str(instruction.get("nextActionPrompt") or "-"))
    lines.append(style("─" * 56, GRAY))
    if events:
        lines.append(style("Timeline:", BLUE, bold=True))
        for event in events:
            lines.extend(_format_timeline_event(event))
    else:
        lines.append(style("Timeline:", BLUE, bold=True))
        lines.append("- no events yet")
    return "\n".join(lines)


def run_tui(task_code: str, task_dir: Path, *, watch: bool = False, interactive: bool = False, interval: float = 2.0) -> None:
    append_event(task_dir, "tui_opened", f"TUI opened for {task_code}", replace_key="tui:opened")
    if interactive:
        run_tui_interactive(task_code, task_dir)
        return
    if not watch:
        print(render_tui_snapshot(task_code, task_dir))
        return
    try:
        while True:
            print("\033[2J\033[H" + render_tui_snapshot(task_code, task_dir, show_logo=False))
            time.sleep(interval)
    except KeyboardInterrupt:
        append_event(task_dir, "tui_closed", f"TUI closed for {task_code}", replace_key="tui:opened")
        print("\nAutoMind TUI closed.")


def run_tui_interactive(task_code: str, task_dir: Path) -> None:
    print(render_tui_snapshot(task_code, task_dir))
    print("\nInteractive TUI: commands = refresh/status/trace/process-check/resume/exit; natural language is sent to this task/session.")
    while True:
        try:
            raw = tui_input(style(f"automind:{task_code}> ", BLUE, bold=True)).strip()
        except EOFError:
            print("")
            return
        except KeyboardInterrupt:
            print("\nUse `exit` to close this TUI.")
            continue
        if not raw:
            continue
        if raw in {"exit", "quit", ":q"}:
            append_event(task_dir, "tui_closed", f"TUI closed for {task_code}", replace_key="tui:opened")
            return
        if raw in {"refresh", "r"}:
            print(render_tui_snapshot(task_code, task_dir, show_logo=False))
            continue
        if raw == "clear":
            print("\033[2J\033[H" + render_tui_snapshot(task_code, task_dir, show_logo=False))
            continue
        parts = raw.split()
        command = parts[0]
        if command in {"status", "trace", "process-check", "resume", "workflow-check", "completion-check"}:
            argv = [command, task_code, *parts[1:]]
        else:
            argv = ["message", task_code, "--text", raw, "--resume", "auto"]
        proc = subprocess.run([sys.executable, str(_main_py()), *argv])
        if proc.returncode != 0:
            print(f"[AutoMind TUI] command exited with code {proc.returncode}", file=sys.stderr)
        print(render_tui_snapshot(task_code, task_dir, show_logo=False))


def prompt_for_pending_answer(task_code: str, task_dir: Path) -> dict[str, Any] | None:
    """Render a pending ask_user prompt and read one terminal answer."""
    from orchestrator.session.answers import apply_user_answer, resolve_selected_option
    from orchestrator.state import read_evaluation_json, read_runtime_state

    pending = normalize_pending_question(task_dir)
    if not pending:
        return None
    print(render_tui_snapshot(task_code, task_dir, show_logo=False))
    options = pending.get("options") if isinstance(pending.get("options"), list) else []
    # Stably re-print the question + options block right above the input line so
    # that long generator stdout cannot scroll the question off-screen. This is
    # the minimum guarantee the user reported missing in the iOS task.
    print()
    print(style("─" * 56, GRAY))
    print(style("AutoMind needs your input", YELLOW, bold=True))
    category = str(pending.get("category") or "").strip()
    if category:
        print(f"{style('Category:', YELLOW, bold=True)} {category}")
    question_text = str(pending.get("question") or "").strip() or "(no question text recorded)"
    print(f"{style('Question:', YELLOW, bold=True)} {question_text}")
    if options:
        print(style("Options:", YELLOW, bold=True))
        for idx, option in enumerate(options, start=1):
            if isinstance(option, dict):
                opt_id = option.get("id") or idx
                label = option.get("label") or ""
                impact = option.get("impact") or ""
                line = f"  {idx}. {opt_id} — {label}".rstrip()
                if impact:
                    line += f" [{impact}]"
                print(line)
            else:
                print(f"  {idx}. {option}")
        print(style("Reply with option number/id, or free text.", GRAY))
    else:
        print(style("Reply with free text.", GRAY))
    # Staleness warning: if the persisted askUserQuestion was recorded at an
    # earlier iteration than the current runtime iteration, the on-screen
    # question/options may not match what the latest agent turn just printed
    # (the user reported exactly this in the iOS XCUITest signing case).
    state_for_warn = read_runtime_state(task_dir) or {}
    evaluation_for_warn = read_evaluation_json(task_dir) or {}
    try:
        state_iter = int(state_for_warn.get("iteration") or 0)
    except (TypeError, ValueError):
        state_iter = 0
    try:
        eval_iter = int(evaluation_for_warn.get("iteration") or 0)
    except (TypeError, ValueError):
        eval_iter = 0
    if eval_iter and state_iter and eval_iter < state_iter:
        print(style(
            f"Warning: this question was recorded at iter {eval_iter} but the runtime is at iter {state_iter}. "
            "If it no longer matches what the latest agent output asked, reply with free text describing your decision.",
            YELLOW, bold=True,
        ))
    # Reuse-safe-path reminder: if the active reuseGate flagged a repeated
    # failure (signing/device/build/repeated-same-failure) and matched safe
    # reuse paths exist, surface them next to the input box so the user is not
    # forced to choose between mis-aligned generator options without seeing the
    # reuse fallback first.
    reuse_gate = state_for_warn.get("reuseGate") if isinstance(state_for_warn.get("reuseGate"), dict) else {}
    repeated_gate = None
    for phase_key in ("evaluator", "generator"):
        gate_entry = reuse_gate.get(phase_key) if isinstance(reuse_gate.get(phase_key), dict) else None
        if not gate_entry:
            continue
        repeated_meta = gate_entry.get("repeatedFailure") if isinstance(gate_entry.get("repeatedFailure"), dict) else {}
        if repeated_meta.get("detected"):
            repeated_gate = (phase_key, gate_entry, repeated_meta)
            break
    if repeated_gate:
        phase_key, gate_entry, repeated_meta = repeated_gate
        safe_paths = gate_entry.get("safePaths") or []
        avoid_paths = gate_entry.get("avoidPaths") or []
        category_label = repeated_meta.get("category") or "repeated_failure"
        print(style(
            f"Repeated-failure reuse fallback active ({phase_key}, category={category_label}).",
            YELLOW, bold=True,
        ))
        if safe_paths:
            print(style("Try these safe reuse paths first (before sensitive actions):", YELLOW, bold=True))
            for sp in safe_paths[:5]:
                print(f"  + {sp}")
        if avoid_paths:
            print(style("Avoid these paths (known-bad):", YELLOW, bold=True))
            for ap in avoid_paths[:5]:
                print(f"  - {ap}")
        print(style(
            "Tip: replying with free text like 'replan_verification: <reason> + <safe paths to try>' is preferred over picking a stale numeric option.",
            GRAY,
        ))
    print(style("─" * 56, GRAY))
    raw = tui_input("\nAutoMind answer > ").strip()
    selected = resolve_selected_option(task_dir, raw)
    answer = apply_user_answer(task_dir, answer_text=raw, selected_option=selected)
    print(f"Recorded answer: {answer.get('id')}")
    return answer
