"""Agent subprocess I/O bridge shared by TUI and CLI-owned sessions.

The bridge is intentionally conservative: it can stream stdout/stderr into the
shared event timeline while preserving the existing captured-output contract.
Direct stdin passthrough is exposed as a future capability but artifact-based
`automind answer` remains the durable source of truth.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from orchestrator.config import AUTOMIND_WORKSPACE_ROOT
from orchestrator.session.events import append_event
from orchestrator.state import read_evaluation_json, read_runtime_state


@dataclass(frozen=True)
class AgentIOCapabilities:
    stream_stdout: bool = True
    stream_stderr: bool = True
    accepts_stdin: bool = False
    supports_interactive_session: bool = False
    supports_resume_session: bool = False
    supports_structured_prompt_injection: bool = True


AGENT_IO_CAPABILITIES: dict[str, AgentIOCapabilities] = {
    "codex": AgentIOCapabilities(supports_resume_session=True),
    "claude": AgentIOCapabilities(supports_resume_session=True),
    "trae": AgentIOCapabilities(supports_resume_session=True),
}


def capabilities_for(agent: str) -> AgentIOCapabilities:
    return AGENT_IO_CAPABILITIES.get(agent, AgentIOCapabilities())


def task_requires_human_input(task_dir: Path) -> bool:
    """Return True when durable task artifacts request ask_user.

    Agent CLIs can write runtime-state/evaluation files before their own final
    analysis is complete. CodeAutonomy should notice that a human-input gate exists,
    but by default it waits for the current agent turn to finish so the full
    reasoning/output chain is preserved.
    """
    state = read_runtime_state(task_dir) or {}
    if state.get("status") == "human_input_pending" or state.get("nextAction") == "ask_user":
        return True
    evaluation = read_evaluation_json(task_dir)
    return isinstance(evaluation, dict) and evaluation.get("nextAction") == "ask_user"


def interrupt_agent_on_ask_user() -> bool:
    """Optional escape hatch; default is to wait for the agent to finish."""
    return os.environ.get("AUTOMIND_INTERRUPT_AGENT_ON_ASK_USER", "0").lower() in {"1", "true", "yes", "on"}


def stream_agent_command(
    cmd: list[str],
    *,
    task_dir: Path,
    agent: str,
    phase: str,
    cwd: str | None = None,
    timeout: int | None = None,
    mirror_stdout: bool = False,
    env: dict[str, str] | None = None,
    display_transform: Callable[[str], str | None] | None = None,
    output_decoder: Callable[[list[str]], str] | None = None,
) -> tuple[int, str, str]:
    """Run an agent command while streaming output into `events.jsonl`.

    Returns the same shape as `console.run_cmd`: `(returncode, stdout, stderr)`.
    stderr is merged into stdout for streaming purposes because many coding-agent
    CLIs use stderr for progress output. The durable transcript is still returned
    as stdout so existing agent-session parsing can continue to work.

    `display_transform` only changes what is published as a readable
    `agent_output` event (e.g. decoding claude stream-json lines into plain
    assistant text). Returning None/"" from the transform suppresses the event
    for that line.
    `output_decoder` rebuilds the returned stdout transcript from the raw lines
    (e.g. extracting claude stream-json's final result text) so downstream
    reply/JSON parsing keeps receiving clean text instead of raw JSON lines.
    When omitted, the raw concatenated stdout is returned unchanged, so
    non-claude agents are completely unaffected.
    """
    if timeout is None:
        raw_timeout = os.environ.get("AUTOMIND_AGENT_TIMEOUT", os.environ.get("AUTOMIND_CMD_TIMEOUT", "43200"))
        try:
            timeout = int(raw_timeout)
        except ValueError:
            timeout = 43200
    append_event(
        task_dir,
        "agent_started",
        f"{agent} started for {phase}",
        phase=phase,
        replace_key=f"agent:{phase}",
        source="agent_io",
        data={"agent": agent, "cmd": cmd[:4]},
    )
    output_lines: list[str] = []
    raw_idle_timeout = os.environ.get("AUTOMIND_AGENT_IDLE_TIMEOUT_SECONDS", "1800")
    try:
        idle_timeout = int(raw_idle_timeout)
    except ValueError:
        idle_timeout = 1800
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd or str(AUTOMIND_WORKSPACE_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
        )
    except Exception as exc:
        append_event(task_dir, "agent_failed", f"{agent} failed to start: {exc}", level="error", phase=phase, replace_key=f"agent:{phase}", source="agent_io")
        return -1, "", str(exc)

    timed_out = False
    idle_timed_out = False
    input_pending_detected = False
    interrupted_for_input = False
    timer: threading.Timer | None = None
    started_at = time.monotonic()
    last_output_at = started_at

    def publish_still_running() -> None:
        elapsed = int(time.monotonic() - started_at)
        quiet_for = int(time.monotonic() - last_output_at)
        append_event(
            task_dir,
            "agent_still_running",
            f"{agent} still running for {phase}; elapsed={elapsed}s quietFor={quiet_for}s",
            phase=phase,
            source="agent_io",
            data={"agent": agent, "elapsedSeconds": elapsed, "quietForSeconds": quiet_for},
        )

    def kill_on_timeout() -> None:
        nonlocal timed_out
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass

    def kill_on_idle_timeout(quiet_for: int) -> None:
        nonlocal idle_timed_out
        idle_timed_out = True
        append_event(
            task_dir,
            "agent_stalled_no_output",
            f"{agent} produced no output for {quiet_for}s during {phase}; killing stale process",
            level="error",
            phase=phase,
            replace_key=f"agent:{phase}",
            source="agent_io",
            data={"agent": agent, "quietForSeconds": quiet_for, "idleTimeoutSeconds": idle_timeout},
        )
        try:
            proc.kill()
        except Exception:
            pass

    def mark_human_input_pending() -> None:
        nonlocal input_pending_detected, interrupted_for_input
        if input_pending_detected:
            return
        input_pending_detected = True
        append_event(
            task_dir,
            "agent_input_pending",
            f"{agent} observed ask_user during {phase}; waiting for current agent turn to finish",
            level="warn",
            phase=phase,
            replace_key=f"agent:{phase}:ask_user",
            source="agent_io",
            data={"agent": agent, "interrupt": interrupt_agent_on_ask_user()},
        )
        if not interrupt_agent_on_ask_user():
            return
        interrupted_for_input = True
        try:
            proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    if timeout and timeout > 0:
        timer = threading.Timer(timeout, kill_on_timeout)
        timer.daemon = True
        timer.start()
    progress_stop = threading.Event()

    def progress_worker() -> None:
        last_publish_at = time.monotonic()
        while not progress_stop.wait(1):
            if task_requires_human_input(task_dir):
                mark_human_input_pending()
                if interrupt_agent_on_ask_user():
                    return
            now = time.monotonic()
            quiet_for = int(now - last_output_at)
            if idle_timeout > 0 and quiet_for >= idle_timeout:
                kill_on_idle_timeout(quiet_for)
                return
            if now - last_publish_at >= 60:
                publish_still_running()
                last_publish_at = now

    progress_thread = threading.Thread(target=progress_worker, name=f"automind-agent-progress-{agent}-{phase}", daemon=True)
    progress_thread.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            last_output_at = time.monotonic()
            output_lines.append(line)
            raw_text = line.rstrip("\n")
            if display_transform is not None:
                try:
                    text = display_transform(raw_text)
                except Exception:
                    text = raw_text
            else:
                text = raw_text
            if text:
                append_event(task_dir, "agent_output", text, phase=phase, source=agent, data={"agent": agent})
                if mirror_stdout:
                    print(text, flush=True)
            if task_requires_human_input(task_dir):
                mark_human_input_pending()
        code = proc.wait()
    finally:
        progress_stop.set()
        progress_thread.join(timeout=1)
        if timer:
            timer.cancel()
    if output_decoder is not None:
        try:
            output = output_decoder(output_lines)
        except Exception:
            output = "".join(output_lines)
    else:
        output = "".join(output_lines)
    if interrupted_for_input:
        append_event(task_dir, "agent_stopped_for_input", f"{agent} stopped for {phase}; waiting for user answer", phase=phase, replace_key=f"agent:{phase}", source="agent_io", data={"agent": agent})
        return -3, output, "Agent stopped because task requires human input"
    if idle_timed_out:
        return -4, output, f"Agent stalled with no output for {idle_timeout}s (stalled_no_output)"
    if timed_out:
        append_event(task_dir, "agent_timeout", f"{agent} timed out after {timeout}s", level="error", phase=phase, replace_key=f"agent:{phase}", source="agent_io")
        return -1, output, f"Command timeout after {timeout}s"
    if code == 0:
        append_event(task_dir, "agent_done", f"{agent} completed for {phase}", phase=phase, replace_key=f"agent:{phase}", source="agent_io", data={"agent": agent, "exitCode": code})
    else:
        append_event(task_dir, "agent_failed", f"{agent} exited with code {code}", level="error", phase=phase, replace_key=f"agent:{phase}", source="agent_io", data={"agent": agent, "exitCode": code})
    return code, output, ""
