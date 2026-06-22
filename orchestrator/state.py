"""Runtime state and evaluation file helpers for the AutoMind orchestrator."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from orchestrator.config import AUTOMIND_ROOT, AUTOMIND_WORKSPACE_ROOT, TASKS_DIR
from orchestrator.console import warn


def ensure_dir(path: Path):
    """Ensure directory exists."""
    path.mkdir(parents=True, exist_ok=True)


def get_runtime_state_path(task_dir: Path) -> Path:
    """Return the runtime-state.json path for a task directory.

    Runtime state is the mutable resume/session/heartbeat projection. It is
    intentionally not the macro phase-transition truth.
    """
    return task_dir / "runtime-state.json"


def _read_state_path(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        warn(f"Runtime state file is invalid: {path}")
        return None
    return data if isinstance(data, dict) else None


def write_runtime_state(task_dir: Path, state: dict):
    """Write runtime-state.json."""
    task_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(state or {})
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    get_runtime_state_path(task_dir).write_text(text)


def read_runtime_state(task_dir: Path) -> Optional[dict]:
    """Read runtime-state.json."""
    return _read_state_path(get_runtime_state_path(task_dir))


def update_runtime_state(task_dir: Path, **updates):
    """Update runtime-state.json."""
    state = read_runtime_state(task_dir) or {}
    state.update(updates)
    state["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    write_runtime_state(task_dir, state)


# Decision Log (R06/AC-012/AC-013):
# Append a short self-description per iteration so subsequent prompts can
# inject recent context even after long-context compression. Each entry is
# capped at DECISION_LOG_MAX_CHARS.
DECISION_LOG_MAX_CHARS = 200
DECISION_LOG_PROMPT_LIMIT = 5


def append_decision_log(
    task_dir: Path,
    iteration: int,
    phase: str,
    text: str,
) -> dict:
    """Append a single decision-log entry to runtime-state.json.

    Truncates ``text`` to ``DECISION_LOG_MAX_CHARS`` characters.
    Returns the entry written.
    """
    state = read_runtime_state(task_dir) or {}
    log = state.get("decisionLog")
    if not isinstance(log, list):
        log = []
    truncated = (text or "").strip()[:DECISION_LOG_MAX_CHARS]
    entry = {
        "iteration": int(iteration),
        "phase": str(phase or "generic"),
        "text": truncated,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    log.append(entry)
    state["decisionLog"] = log
    state["updatedAt"] = entry["timestamp"]
    write_runtime_state(task_dir, state)
    return entry


def format_recent_decisions(state: Optional[dict], limit: int = DECISION_LOG_PROMPT_LIMIT) -> str:
    """Render the last ``limit`` decision-log entries as a prompt-ready block.

    Returns "" when no entries exist. Format::

        [iter=N phase=P] text...
    """
    if not isinstance(state, dict):
        return ""
    log = state.get("decisionLog")
    if not isinstance(log, list) or not log:
        return ""
    tail = log[-int(limit):] if limit and limit > 0 else log
    lines = []
    for entry in tail:
        if not isinstance(entry, dict):
            continue
        lines.append(
            f"[iter={entry.get('iteration')} phase={entry.get('phase')}] {entry.get('text', '')}"
        )
    return "\n".join(lines)


# Heartbeat (R08/AC-016/AC-017):
# Lightweight per-phase progress beacons written to logs/heartbeat.log.
# Each line is plain text "ISO_TIMESTAMP\tEVENT\tPHASE\t[detail]" so that
# external supervisors / cmd_doctor can detect stalled phases.
HEARTBEAT_LOG_NAME = "heartbeat.log"


def _heartbeat_log_path(task_dir: Path) -> Path:
    log_dir = task_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / HEARTBEAT_LOG_NAME


def write_heartbeat(task_dir: Path, event: str, phase: str, detail: str = "") -> None:
    """Append a single heartbeat line to logs/heartbeat.log."""
    path = _heartbeat_log_path(task_dir)
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"{ts}\t{event}\t{phase}\t{detail}".rstrip()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_heartbeat_log(task_dir: Path) -> list[str]:
    """Return heartbeat.log lines (chronological, no trailing newline)."""
    path = _heartbeat_log_path(task_dir)
    if not path.exists():
        return []
    return [ln.rstrip("\n") for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


@contextmanager
def heartbeat_tick(task_dir: Path, phase: str) -> "Generator[None, None, None]":
    """Context manager that records phase_start / phase_end (or phase_end_error).

    Usage::

        with heartbeat_tick(task_dir, "generator"):
            run_generator(...)

    On exception, writes ``phase_end_error\\t<phase>\\t<exc class>: <msg>`` and
    re-raises. Always writes a phase_start before yielding so external watchers
    see the phase even if the body crashes immediately.
    """
    write_heartbeat(task_dir, "phase_start", phase)
    try:
        yield
    except BaseException as exc:
        detail = f"{type(exc).__name__}: {exc}"
        write_heartbeat(task_dir, "phase_end_error", phase, detail)
        raise
    else:
        write_heartbeat(task_dir, "phase_end", phase)


def list_tasks() -> list:
    """List all task codes."""
    if not TASKS_DIR.exists():
        return []
    return sorted([d.name for d in TASKS_DIR.iterdir() if d.is_dir() and not d.name.startswith("__")])


def get_task_dir(task_code: str) -> Path:
    """Return a task directory path."""
    return TASKS_DIR / task_code


def task_exists(task_code: str) -> bool:
    """Return whether a task exists."""
    return get_task_dir(task_code).exists()


# Skill/Command-mode automation: current-task marker.
#
# Skill mode lacks the long-running orchestrator loop, so the host agent (or
# Hook scripts) must be able to discover the active task without re-asking the
# user. We persist the active task code at .automind/current-task so any tool
# can pick it up via three-level fallback (env -> file -> latest task dir).
def _current_task_path() -> Path:
    return AUTOMIND_WORKSPACE_ROOT / ".automind" / "current-task"


def write_current_task(task_code: str) -> Path:
    """Persist the active task code so Skill-mode tooling can discover it.

    Called by scaffold/ask on task creation. Cleared by completion-check on
    pass to avoid stale triggers.
    """
    path = _current_task_path()
    ensure_dir(path.parent)
    path.write_text(str(task_code).strip() + "\n")
    return path


def read_current_task() -> Optional[str]:
    """Return the active task code recorded in .automind/current-task, if any.

    Lookup order (matches docs/workflow.md Skill-mode protocol):
    1. ``AUTOMIND_CURRENT_TASK`` environment override.
    2. ``.automind/current-task`` marker file under the workspace root.
    """
    env = os.environ.get("AUTOMIND_CURRENT_TASK", "").strip()
    if env:
        return env
    path = _current_task_path()
    if not path.exists():
        return None
    text = path.read_text(errors="ignore").strip()
    return text or None


def clear_current_task() -> None:
    """Remove the .automind/current-task marker once the task finishes."""
    path = _current_task_path()
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            warn(f"Failed to clear current-task marker: {exc}")




def get_tui_chat_task_code() -> str:
    return os.environ.get("AUTOMIND_TUI_CHAT_TASK", "__tui_chat__").strip() or "__tui_chat__"


def get_tui_chat_task_dir() -> Path:
    return get_task_dir(get_tui_chat_task_code())


def ensure_tui_chat_task() -> Path:
    task_dir = get_tui_chat_task_dir()
    ensure_dir(task_dir)
    state = read_runtime_state(task_dir) or {}
    if not state:
        write_runtime_state(task_dir, {
            "taskId": get_tui_chat_task_code(),
            "status": "chat",
            "currentOwner": "human",
            "nextAction": "chat",
            "createdAt": datetime.now().isoformat(timespec="seconds"),
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
        })
    return task_dir


def read_tui_chat_primary_session() -> dict:
    state = read_runtime_state(get_tui_chat_task_dir()) or {}
    sessions = state.get("agentSessions") if isinstance(state.get("agentSessions"), dict) else {}
    primary = sessions.get("primary") if isinstance(sessions.get("primary"), dict) else {}
    return primary if primary.get("sessionId") else {}


def seed_task_primary_session_from_tui_chat(task_dir: Path, agent: str = "auto") -> bool:
    primary = read_tui_chat_primary_session()
    if not primary:
        return False
    primary_agent = str(primary.get("agent") or "")
    if agent not in {"", "auto", primary_agent} and primary_agent and agent != primary_agent:
        return False
    state = read_runtime_state(task_dir) or {}
    sessions = state.get("agentSessions") if isinstance(state.get("agentSessions"), dict) else {}
    existing = sessions.get("primary") if isinstance(sessions.get("primary"), dict) else {}
    if existing.get("sessionId"):
        return False
    sessions["primary"] = dict(primary)
    state["agentSessions"] = sessions
    state["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    write_runtime_state(task_dir, state)
    return True



def clear_task_primary_session(task_dir: Path, reason: str = "") -> bool:
    """Clear task-local primary agent session so next Planner/Generator starts fresh."""
    state = read_runtime_state(task_dir) or {}
    sessions = state.get("agentSessions") if isinstance(state.get("agentSessions"), dict) else {}
    primary = sessions.get("primary") if isinstance(sessions.get("primary"), dict) else {}
    if not primary.get("sessionId"):
        return False
    cleared = dict(primary)
    sessions.pop("primary", None)
    archived = sessions.get("clearedPrimarySessions") if isinstance(sessions.get("clearedPrimarySessions"), list) else []
    archived.append({
        **cleared,
        "clearedAt": datetime.now().isoformat(timespec="seconds"),
        "clearReason": reason or "manual_clear",
    })
    sessions["clearedPrimarySessions"] = archived[-20:]
    state["agentSessions"] = sessions
    state["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    write_runtime_state(task_dir, state)
    return True


def tick_iteration(task_dir: Path, *, phase: str = "generic") -> dict:
    """Increment runtime-state.json.iteration and return the updated counter view.

    Skill mode does not run the orchestrator loop, so each Generator/Evaluator
    turn must self-report iteration progress. This helper enforces the
    AUTOMIND_MAX_ITERATIONS budget client-side: when the budget is exhausted
    the returned dict carries ``budgetExhausted=True`` and the caller is
    expected to halt or escalate to ask_user.
    """
    # Local import avoids a config<->state import cycle at module load.
    from orchestrator.config import MAX_ITERATIONS

    state = read_runtime_state(task_dir) or {}
    try:
        current = int(state.get("iteration", 0) or 0)
    except (TypeError, ValueError):
        current = 0
    nxt = current + 1
    try:
        configured = int(state.get("maxIterations", 0) or 0)
    except (TypeError, ValueError):
        configured = 0
    budget = max(MAX_ITERATIONS, configured)
    exhausted = nxt > budget
    phase_name = str(phase or "generic")
    update_runtime_state(
        task_dir,
        iteration=nxt,
        lastTickPhase=phase_name,
        lastTickAt=datetime.now().isoformat(timespec="seconds"),
    )
    # Keep the new workflow control state in sync with skill/current-session
    # iteration ticks.  Workflow-state iteration means the active/next loop
    # attempt being executed.  Persist any sync failure instead of silently
    # hiding drift between runtime-state and workflow-state.
    try:
        from orchestrator.workflow_state import emit_workflow_event, normalize_phase

        normalized_phase = normalize_phase(phase_name, default="delivery" if phase_name == "generator" else "evaluation")
        next_action = "run_evaluation" if normalized_phase == "delivery" else "judge_evidence"
        emit_workflow_event(task_dir, {
            "type": "iteration_tick",
            "phase": normalized_phase,
            "action": next_action,
            "nextAction": next_action,
            "nextPhase": normalized_phase,
            "iteration": nxt,
            "maxIterations": budget,
            "status": "running" if not exhausted else "failed",
            "stateHealth": "ok" if not exhausted else "degraded",
            "reason": "iteration_budget_tick" if not exhausted else "iteration_budget_exhausted",
        })
        update_runtime_state(task_dir, workflowStateError=None)
    except Exception as exc:
        update_runtime_state(
            task_dir,
            workflowStateError={
                "phase": phase_name,
                "iteration": nxt,
                "error": f"{type(exc).__name__}: {exc}",
                "at": datetime.now().isoformat(timespec="seconds"),
            },
        )
    return {
        "iteration": nxt,
        "budget": budget,
        "budgetExhausted": exhausted,
        "phase": phase_name,
    }


def _safe_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def completion_report_is_authoritative_terminal_pass(report: dict) -> bool:
    """Return True only for the standard completion-check terminal proof.

    A bare ``result=pass`` marker is intentionally not enough: old or partial
    ledger artifacts must not close the loop.  Terminal authority requires the
    completion-check report to preserve both the raw evaluator finish claim and
    the completion verdict that accepted it.
    """
    if str(report.get("result") or "").strip().lower() != "pass":
        return False
    verdict = report.get("completionVerdict")
    if not isinstance(verdict, dict) or str(verdict.get("result") or "").strip().lower() != "pass":
        return False
    raw_claim = report.get("rawEvaluationClaim")
    if not isinstance(raw_claim, dict):
        return False
    if str(raw_claim.get("result") or "").strip().lower() != "pass":
        return False
    if str(raw_claim.get("nextAction") or "").strip().lower() != "finish":
        return False
    if not isinstance(report.get("testResults"), list):
        return False
    return True


def _stage_state_payload(task_dir: Path, stage: str, key: str) -> dict:
    """Read a payload from the new stage-state JSON without importing workflow_state.

    state.py sits below workflow_state.py in the import graph, so this helper
    reads the deterministic stage path directly.  Old sidecars remain fallback
    artifacts; stage-state payloads are the preferred control-state projection.
    """
    path = task_dir / "stages" / f"{stage}-stage-state.json"
    state = _safe_json(path)
    payload = state.get(key) if isinstance(state.get(key), dict) else {}
    return payload if isinstance(payload, dict) else {}


def _stage_state_payload_with_mtime(task_dir: Path, stage: str, key: str) -> tuple[dict, float]:
    path = task_dir / "stages" / f"{stage}-stage-state.json"
    payload = _stage_state_payload(task_dir, stage, key)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return payload, mtime


def _stage_meta(task_dir: Path, stage: str) -> tuple[str, str]:
    """Return (top-level updatedAt, stage status) for a stage-state file."""
    data = _safe_json(task_dir / "stages" / f"{stage}-stage-state.json")
    return str(data.get("updatedAt") or ""), str(data.get("status") or "").strip().lower()


def completion_pass_superseded_by_reopened_verification(task_dir: Path) -> bool:
    """True when the verification loop was re-activated after summary completion.

    A summary completion pass is only authoritative if it is the last word.  When
    the verification_loop stage is updated *after* the summary stage and is no
    longer in a terminal status (e.g. ``active``), the loop has reopened (a false
    finish was rolled back), so a stale completion pass must not close the task.
    Normal finished tasks write the summary stage last, so this never fires for
    them.
    """
    summary_ts, _ = _stage_meta(task_dir, "summary")
    verify_ts, verify_status = _stage_meta(task_dir, "verification-loop")
    if not summary_ts or not verify_ts:
        return False
    if verify_status in {"finished", "completed"}:
        return False
    return verify_ts > summary_ts


def task_has_authoritative_terminal_pass(task_dir: Path) -> bool:
    """Return True when completion-check proves a final pass.

    The new preferred source is stages/summary-stage-state.json#completion.
    completion-report.json remains a compatibility artifact for older commands
    and exported skills.  A completion pass that has been superseded by a
    re-opened verification loop is treated as stale, so every caller (resume,
    evaluation write-barrier, workflow-check terminal guard, state reducer)
    rejects it consistently.
    """
    if completion_pass_superseded_by_reopened_verification(task_dir):
        return False
    return completion_report_is_authoritative_terminal_pass(
        _stage_state_payload(task_dir, "summary", "completion") or _safe_json(task_dir / "completion-report.json")
    )


def _is_terminal_pass_evaluation(evaluation: dict) -> bool:
    return (
        str(evaluation.get("result") or "").strip().lower() == "pass"
        and str(evaluation.get("nextAction") or "").strip().lower() == "finish"
    )


def write_evaluation_json(task_dir: Path, evaluation: dict):
    """Write latest structured evaluator result.

    Guardrail: after authoritative completion pass, non-final evaluator outputs
    are advisory only. They are preserved under logs/ instead of overwriting
    `evaluation.json`, because weak partial probe-flow/current-check artifacts
    can otherwise pull a finished task back into Generator.
    """
    evaluation = dict(evaluation or {})
    evaluation["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    evaluation_path = task_dir / "evaluation.json"
    if task_has_authoritative_terminal_pass(task_dir) and not _is_terminal_pass_evaluation(evaluation):
        advisory_dir = task_dir / "logs" / "advisory-evaluations"
        advisory_dir.mkdir(parents=True, exist_ok=True)
        iteration = evaluation.get("iteration", "unknown")
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        advisory_path = advisory_dir / f"blocked-after-terminal-pass-iter-{iteration}-{ts}.json"
        advisory_path.write_text(json.dumps(evaluation, ensure_ascii=False, indent=2))
        append_progress_log(
            task_dir,
            "blocked non-terminal evaluation write after authoritative terminal pass",
            iteration=int(iteration) if isinstance(iteration, int) else None,
            owner="state",
            level="warn",
        )
        return
    evaluation_path.write_text(json.dumps(evaluation, ensure_ascii=False, indent=2))
    try:
        from orchestrator.workflow_state import emit_workflow_event

        emit_workflow_event(task_dir, {
            "type": "evaluation_written",
            "phase": "evaluation",
            "action": "judge_evidence",
            "nextAction": evaluation.get("nextAction") or "judge_evidence",
            "nextPhase": "completion" if evaluation.get("nextAction") == "finish" else "evaluation",
            "status": "completed" if evaluation.get("nextAction") == "finish" and evaluation.get("result") == "pass" else "running",
            "iteration": evaluation.get("iteration", 0),
            "result": evaluation.get("result"),
            "evaluation": evaluation,
        })
    except Exception as exc:
        append_progress_log(
            task_dir,
            f"failed to sync evaluation into workflow stage state: {type(exc).__name__}: {exc}",
            iteration=evaluation.get("iteration") if isinstance(evaluation.get("iteration"), int) else None,
            owner="state",
            level="warn",
        )


def read_evaluation_json(task_dir: Path) -> Optional[dict]:
    """Read latest structured evaluator result.

    Preferred source is stages/verification-loop-stage-state.json#evaluation.
    evaluation.json is retained as a compatibility artifact and legacy fallback.
    """
    stage_evaluation, stage_mtime = _stage_state_payload_with_mtime(task_dir, "verification-loop", "evaluation")
    evaluation_path = task_dir / "evaluation.json"
    if not evaluation_path.exists():
        return stage_evaluation or None
    try:
        file_mtime = evaluation_path.stat().st_mtime
    except OSError:
        file_mtime = 0.0
    prefer_sidecar = bool(stage_evaluation) and file_mtime > stage_mtime + 0.001
    if stage_evaluation and not prefer_sidecar:
        return stage_evaluation
    try:
        data = json.loads(evaluation_path.read_text())
    except json.JSONDecodeError as exc:
        warn(f"evaluation.json is not valid JSON: {exc}")
        return None
    if not isinstance(data, dict):
        warn("evaluation.json top-level value must be an object")
        return None
    return data


def rel_to_root(path: Path | str) -> str:
    """Return a stable path relative to workspace or runtime root when possible."""
    resolved = Path(path).resolve()
    for root in (AUTOMIND_WORKSPACE_ROOT, AUTOMIND_ROOT):
        try:
            return str(resolved.relative_to(root))
        except Exception:
            continue
    return str(path)


def read_text_if_exists(path: Path) -> str:
    """Read a UTF-8-ish text file, returning empty text when absent."""
    if not path.exists():
        return ""
    return path.read_text(errors="ignore")


# ---------------------------------------------------------------------------
# Long-running task helpers
#
# These helpers give long-running AutoMind tasks a lightweight liveness signal
# without changing the canonical Plan -> Build -> Verify -> Finish loop. They
# write strictly to `.automind/tasks/<task>/` artifacts so supervisors and
# resume flows can read them as the source of truth.
#
# TODO(long-run): expose `parallel verification` (run independent TC-* probes
# concurrently while keeping evaluation.json as the single sink), `auto resume`
# (supervisor watches heartbeat staleness and replays the last `nextAction`),
# and a dedicated `supervisor` daemon that owns these signals end-to-end. For
# now the helpers are intentionally synchronous and free of background
# threads/processes so existing CLI flows behave the same.
# ---------------------------------------------------------------------------


def update_heartbeat(task_dir: Path, *, owner: str | None = None, note: str | None = None) -> str:
    """Touch the task heartbeat with the current timestamp.

    Heartbeats are written to `runtime-state.json.heartbeat` so supervisors can
    detect stalled long-running runs without scanning logs. The function is
    intentionally cheap (single state update) and safe to call from any
    long-running phase. Returns the timestamp written.
    """
    timestamp = datetime.now().isoformat(timespec="seconds")
    state = read_runtime_state(task_dir) or {}
    heartbeat = state.get("heartbeat") if isinstance(state.get("heartbeat"), dict) else {}
    heartbeat["lastBeatAt"] = timestamp
    if owner:
        heartbeat["owner"] = owner
    if note:
        heartbeat["note"] = note
    update_runtime_state(task_dir, heartbeat=heartbeat)
    return timestamp


def append_progress_log(
    task_dir: Path,
    message: str,
    *,
    iteration: int | None = None,
    owner: str | None = None,
    level: str = "info",
) -> Path:
    """Append a progress entry to `progress.log` under the task directory.

    `progress.log` is a flat NDJSON stream so external supervisors / dashboards
    can tail it without re-reading the entire iteration log. Each entry is a
    single JSON object (one line) so partial reads remain safe. The file is
    created lazily; callers do not need to pre-create it.
    """
    ensure_dir(task_dir)
    progress_path = task_dir / "progress.log"
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "level": level,
        "message": message,
    }
    if iteration is not None:
        entry["iteration"] = iteration
    if owner:
        entry["owner"] = owner
    line = json.dumps(entry, ensure_ascii=False)
    with progress_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return progress_path


def notify_user(
    task_dir: Path,
    message: str,
    *,
    severity: str = "info",
    action: str | None = None,
    payload: dict | None = None,
) -> Path:
    """Append a user-facing notification to `notifications.jsonl`.

    Notifications are how long-running tasks surface external-blocker prompts,
    pause/resume signals, and "ready for review" hints without abusing the
    chat channel. The file is NDJSON so the CLI `automind notifications` and
    future supervisors can tail it deterministically.
    """
    ensure_dir(task_dir)
    notifications_path = task_dir / "notifications.jsonl"
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "severity": severity,
        "message": message,
    }
    if action:
        entry["action"] = action
    if payload:
        entry["payload"] = payload
    line = json.dumps(entry, ensure_ascii=False)
    with notifications_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return notifications_path


def read_notifications(task_dir: Path, *, limit: int | None = None) -> list[dict]:
    """Read structured notifications for display via `automind notifications`."""
    notifications_path = task_dir / "notifications.jsonl"
    if not notifications_path.exists():
        return []
    entries: list[dict] = []
    for raw in notifications_path.read_text(errors="ignore").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    if limit is not None and limit > 0:
        return entries[-limit:]
    return entries
