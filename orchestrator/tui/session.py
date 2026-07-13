"""TUI-owned session runner for CodeAutonomy ask/resume.

This module deliberately reuses the existing harness loop. It adds a small
interactive supervisor around it: show TUI snapshots, let users answer pending
ask_user prompts, and resume until the task reaches a terminal state or a real
blocker remains.
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from orchestrator.session.answers import latest_pending_answer_matches_question
from orchestrator.session.ask_user import normalize_pending_question
from orchestrator.session.events import append_event
from orchestrator.state import clear_task_primary_session, read_runtime_state, update_runtime_state, update_heartbeat
from orchestrator.state_summary_check import check_state_summary
from orchestrator.tui.app import LOGO, prompt_for_pending_answer, render_tui_snapshot
from orchestrator.tui.input import enable_line_editing, tui_input
from orchestrator.tui.style import BLUE, CYAN, GRAY, GREEN, RED, YELLOW, style
from orchestrator.version import automind_version_label


HEARTBEAT_STATUS_INTERVAL_SECONDS = 60


def render_tui_welcome() -> str:
    tip = 'say "全自动" or "full auto" to skip all ask_user gates.'
    return (
        f"{style(LOGO, BLUE, bold=True)}\n"
        f"{style('CodeAutonomy TUI', CYAN, bold=True)} {automind_version_label()}\n"
        f"{style('TUI-owned CodeAutonomy session', GRAY)}\n"
        f"{style('Tip: ' + tip, GRAY)}"
    )


def _agent_execution_policy_state(task_dir: Path) -> dict:
    state = read_runtime_state(task_dir) or {}
    policy = state.get("agentExecutionPolicy") if isinstance(state.get("agentExecutionPolicy"), dict) else {}
    if policy.get("consent") in {"user_approved", "user_declined"}:
        return policy
    legacy = state.get("codexDangerousBypass") if isinstance(state.get("codexDangerousBypass"), dict) else {}
    if legacy.get("consent") in {"user_approved", "user_declined"}:
        return legacy
    return {}


def _has_agent_execution_policy_decision(task_dir: Path) -> bool:
    policy = _agent_execution_policy_state(task_dir)
    return policy.get("consent") in {"user_approved", "user_declined", "default_bypass"}


def _tui_agent_can_use_bypass_policy(agent: str) -> bool:
    """Return whether this new TUI task uses a supported coding-agent CLI."""
    normalized = (agent or "auto").strip().lower()
    if normalized in {"codex", "claude", "trae", "trae-cn"}:
        return True
    if normalized != "auto":
        return False
    try:
        from orchestrator.agents import resolve_agent

        resolved, _info = resolve_agent("auto")
    except Exception:
        return False
    return resolved in {"codex", "claude", "trae", "trae-cn"}


def _prompt_for_agent_execution_policy(task_code: str, task_dir: Path, agent: str) -> None:
    """Record default Planner/Generator bypass policy for new TUI tasks.

    CodeAutonomy now defaults supported coding-agent Planner/Generator runs to the
    high-automation bypass path. Keep an auditable runtime-state policy record, but
    do not interrupt the user with an approval/sandbox prompt for this default.
    High-risk actions must still route through CodeAutonomy ask_user gates.
    """
    if not _tui_agent_can_use_bypass_policy(agent) or _has_agent_execution_policy_decision(task_dir):
        return
    answered_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "bypassApprovals": True,
        "consent": "default_bypass",
        "scope": "task",
        "agent": (agent or "auto"),
        "appliesTo": ["planner", "generator"],
        "evaluatorAlwaysBypass": True,
        "answeredAt": answered_at,
        "question": "Default coding-agent approval/sandbox bypass for Planner/Generator on this CodeAutonomy task.",
        "source": "tui_default_bypass_no_prompt",
    }
    legacy = {
        "enabled": True,
        "consent": payload["consent"],
        "scope": "task",
        "agent": "codex",
        "answeredAt": answered_at,
        "question": "Runtime policy compatibility field",
    }
    update_runtime_state(task_dir, agentExecutionPolicy=payload, codexDangerousBypass=legacy)
    append_event(
        task_dir,
        "agent_execution_policy_default",
        "Planner/Generator bypass enabled by default",
        level="info",
        replace_key="agent:execution_policy:default",
        data=payload,
    )

def _status(task_dir: Path) -> str:
    state = read_runtime_state(task_dir) or {}
    return str(state.get("status") or "unknown")


def _heartbeat_age_text(last_beat: object) -> str:
    if not isinstance(last_beat, str) or not last_beat:
        return "unknown"
    try:
        age = max(0.0, (datetime.now() - datetime.fromisoformat(last_beat)).total_seconds())
    except ValueError:
        return "unknown"
    if age < 60:
        return f"{int(age)}s"
    minutes = int(age // 60)
    seconds = int(age % 60)
    return f"{minutes}m{seconds:02d}s"


def _heartbeat_status_message(task_code: str, task_dir: Path) -> str:
    state = read_runtime_state(task_dir) or {}
    heartbeat = state.get("heartbeat") if isinstance(state.get("heartbeat"), dict) else {}
    last_beat = heartbeat.get("lastBeatAt") or "-"
    owner = heartbeat.get("owner") or state.get("currentOwner") or "unknown"
    note = heartbeat.get("note") or ""
    status = state.get("status") or "unknown"
    next_action = state.get("nextAction") or "unknown"
    iteration = state.get("iteration") or 0
    suffix = f" note={note}" if note else ""
    age = _heartbeat_age_text(last_beat)
    return f"Heartbeat: {task_code} status={status} owner={owner} iter={iteration} next={next_action} lastBeatAge={age} lastBeat={last_beat}{suffix}"


def _start_heartbeat_status_thread(task_code: str, task_dir: Path, stop_event: threading.Event, *, interval: int = HEARTBEAT_STATUS_INTERVAL_SECONDS) -> threading.Thread:
    """Publish replaceable TUI heartbeat status while the blocking loop runs.

    The event uses a stable ``replaceKey`` so renderers update one visible row
    instead of appending a new timeline line on every check.
    """

    def worker() -> None:
        while not stop_event.wait(interval):
            state = read_runtime_state(task_dir) or {}
            owner = str(state.get("currentOwner") or "") or None
            note = str(state.get("nextAction") or "") or None
            update_heartbeat(task_dir, owner=owner, note=note)
            append_event(
                task_dir,
                "heartbeat_status",
                _heartbeat_status_message(task_code, task_dir),
                replace_key="heartbeat:status",
                source="tui-heartbeat",
            )
            try:
                print("\033[2J\033[H" + render_tui_snapshot(task_code, task_dir, show_logo=False), flush=True)
            except Exception:
                # Heartbeat rendering must never interrupt the owned loop.
                pass

    thread = threading.Thread(target=worker, name=f"automind-heartbeat-{task_code}", daemon=True)
    thread.start()
    return thread


def _is_loop_continuation_state(state: dict) -> bool:
    """Return whether TUI should invoke run_loop again instead of exiting.

    TUI-owned mode is meant to keep the harness alive across recoverable
    workflow states. `replan_pending/run_test_planner` is not terminal: the
    next loop must run Planner, then continue to Generator/Evaluator once the
    workflow gate passes.
    """
    status = str(state.get("status") or "")
    next_action = str(state.get("nextAction") or "")
    if status == "human_input_pending" or next_action == "ask_user":
        return True
    if status in {"replan_pending", "planned", "ready", "retry_pending"}:
        return next_action in {"run_test_planner", "replan", "run_generator", "retry_generator", "run_evaluator"}
    return False


def run_tui_owned_loop(
    task_code: str,
    task_dir: Path,
    *,
    agent: str,
    run_loop: Callable[[], bool],
    max_answer_cycles: int = 20,
    ask_execution_policy: bool = False,
) -> bool:
    """Run a CodeAutonomy loop with TUI ask_user handling.

    `run_loop` is usually a closure around `run_harness_loop(...)`. If the loop
    parks in `human_input_pending`, this supervisor prompts the user in the TUI,
    records the answer through the shared answer protocol, and invokes the loop
    again so the agent receives the answer through the next prompt/resume.
    """
    line_editing_enabled = enable_line_editing()
    print(render_tui_welcome())
    append_event(task_dir, "tui_session_started", f"TUI session started agent={agent} lineEditing={line_editing_enabled}", replace_key="tui:session", data={"lineEditing": line_editing_enabled, "askExecutionPolicy": ask_execution_policy})
    if ask_execution_policy:
        _prompt_for_agent_execution_policy(task_code, task_dir, agent)
    cycles = 0
    continuation_cycles = 0
    last_continuation_key = None
    while True:
        state_at_cycle_start = read_runtime_state(task_dir) or {}
        if state_at_cycle_start.get("status") == "human_input_pending" and str(state_at_cycle_start.get("currentOwner") or "") in {"human", ""}:
            cycles += 1
            if cycles > max_answer_cycles:
                append_event(task_dir, "tui_session_blocked", "Too many ask_user cycles", level="error", replace_key="tui:session")
                return False
            # P0-1d: an external `automind answer` (or a previous cycle) may have
            # already recorded an answer for the current pending question. In that
            # case, consume it automatically instead of re-prompting the user for
            # the same question. apply_user_answer already advanced the runtime
            # status off human_input_pending, so this branch mostly guards the
            # race where the recorded answer has not yet flowed into runtime state.
            pending_question = normalize_pending_question(task_dir)
            external_answer = latest_pending_answer_matches_question(task_dir, pending_question)
            if external_answer:
                append_event(
                    task_dir,
                    "tui_external_answer_consumed",
                    f"Consumed externally recorded answer {external_answer.get('id')} for {pending_question.get('id') if pending_question else 'pending question'}; skipping re-prompt",
                    replace_key="tui:session",
                    data={"answerId": external_answer.get("id"), "selectedOption": external_answer.get("selectedOption")},
                )
                print(render_tui_snapshot(task_code, task_dir, show_logo=False))
            else:
                answer = prompt_for_pending_answer(task_code, task_dir)
                if not answer:
                    append_event(task_dir, "tui_session_blocked", "No pending question found to answer", level="error", replace_key="tui:session")
                    return False
        print(render_tui_snapshot(task_code, task_dir, show_logo=False))
        heartbeat_stop = threading.Event()
        heartbeat_thread = _start_heartbeat_status_thread(task_code, task_dir, heartbeat_stop)
        try:
            ok = run_loop()
        except KeyboardInterrupt:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)
            cleared_primary = clear_task_primary_session(task_dir, reason="keyboard_interrupt")
            update_runtime_state(
                task_dir,
                status="paused_by_user",
                nextAction="resume_after_user_interrupt",
                currentOwner="human",
                interruptedByUser=True,
                resumeRecovery={
                    "reason": "keyboard_interrupt",
                    "resumeCommand": f"automind resume {task_code} {agent}",
                    "clearedPrimaryAgentSession": cleared_primary,
                },
            )
            append_event(task_dir, "tui_user_interrupt", "User interrupted CodeAutonomy with Ctrl+C; task paused and can be resumed with a fresh primary agent session", level="warn", replace_key="tui:session", data={"clearedPrimaryAgentSession": cleared_primary})
            print("\nCodeAutonomy paused by user. Resume with:")
            print(f"  automind resume {task_code} {agent}")
            return False
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)
        # 先读取一次 run_loop 写入的真实终止状态。如果它已经把任务标成 finished，
        # 就直接返回，避免后续的 self-heal（check_state_summary repair=True）依据
        # 历史事件重新派生出 retry_pending/retry_generator，从而把已经完成的任务
        # 拽回循环——这是 TUI owned loop 之前出现的死循环根因。
        post_loop_state = read_runtime_state(task_dir) or {}
        if str(post_loop_state.get("status") or "") == "finished":
            append_event(task_dir, "tui_loop_result", f"Loop returned ok={ok} status=finished", replace_key="tui:loop")
            print(render_tui_snapshot(task_code, task_dir, show_logo=False))
            append_event(task_dir, "tui_session_done", f"TUI session finished {task_code}", replace_key="tui:session")
            return True
        state_summary_check = check_state_summary(task_dir, repair=True, reason="tui_loop_after_run_loop")
        if state_summary_check.get("repairs"):
            append_event(task_dir, "state_summary_repaired", "Repaired workflow/stage state after run_loop", replace_key="state:summary", data=state_summary_check)
        state = read_runtime_state(task_dir) or {}
        status = str(state.get("status") or "unknown")
        append_event(task_dir, "tui_loop_result", f"Loop returned ok={ok} status={status}", replace_key="tui:loop")
        print(render_tui_snapshot(task_code, task_dir, show_logo=False))
        if status == "finished":
            append_event(task_dir, "tui_session_done", f"TUI session finished {task_code}", replace_key="tui:session")
            return True
        if _is_loop_continuation_state(state):
            # Stay inside the TUI container. For ask_user, the next cycle will
            # collect a human answer. For replan/retry/ready states, the next
            # cycle invokes run_loop() again so CodeAutonomy can run Planner, then
            # proceed to Generator/Evaluator without the detached shell exiting.
            continuation_key = (status, str(state.get("nextAction") or ""), str(state.get("currentOwner") or ""), int(state.get("iteration", 0) or 0))
            if continuation_key == last_continuation_key and not ok:
                continuation_cycles += 1
            else:
                continuation_cycles = 0
                last_continuation_key = continuation_key
            if continuation_cycles >= 3:
                append_event(task_dir, "tui_session_blocked", f"Repeated unchanged continuation state {continuation_key}; stopping to avoid a tight loop", level="error", replace_key="tui:session")
                return False
            append_event(task_dir, "tui_session_continue", f"Continuing TUI-owned loop status={status} nextAction={state.get('nextAction')}", replace_key="tui:continue")
            continue
        append_event(task_dir, "tui_session_stopped", f"TUI session stopped status={status}", replace_key="tui:session")
        return bool(ok)
