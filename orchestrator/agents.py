"""Agent CLI adapter dispatch for AutoMind."""
from __future__ import annotations

import json
import os
import re
import subprocess
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from orchestrator.config import AGENT_ADAPTERS, AUTOMIND_WORKSPACE_ROOT, CLAUDE_STREAM_JSON_FLAGS, _agent_bypass_approvals_enabled, _claude_stream_json_enabled, _task_agent_execution_policy
from orchestrator.console import error, log, run_cmd, success
from orchestrator.session.agent_io import stream_agent_command
from orchestrator.session.events import append_event
from orchestrator.state import clear_task_primary_session, read_runtime_state, update_runtime_state

PRIMARY_SESSION_PHASES = {"planner", "generator"}
FRESH_SESSION_PHASES = {"evaluator"}

# Agent CLI retry policy (R01).
# Defaults to three retries with exponential backoff 1s/3s/9s.
AGENT_RETRY_MAX_ATTEMPTS = int(os.environ.get("AUTOMIND_AGENT_RETRY_MAX", "3"))
AGENT_RETRY_BACKOFF: tuple[float, ...] = (1.0, 3.0, 9.0)


def _is_retryable_agent_failure(code: int, stderr: str) -> bool:
    """Classify an agent CLI invocation as a transient failure worth retrying.

    Retryable: timeout, agent process crashed without producing artifacts,
    network glitch, transient SIGPIPE/SIGTERM. Non-retryable: clean exit codes
    that indicate the agent ran but produced bad output (those are product
    failures handled by the loop, not by retry-with-sleep).
    """
    if code == 0:
        return False
    if code == -1:
        return True  # run_cmd's signal for TimeoutExpired / Exception
    text = (stderr or "").lower()
    transient_markers = (
        "timeout",
        "timed out",
        "connection reset",
        "connection refused",
        "broken pipe",
        "temporary failure",
        "network is unreachable",
    )
    return any(marker in text for marker in transient_markers)


def workspace_cwd() -> str:
    """cwd for user/project agent commands."""
    return str(AUTOMIND_WORKSPACE_ROOT)


def preflight_agent(agent: str) -> tuple[bool, dict]:
    """
    Run a lightweight availability check before invoking an Agent.
    This does not run a real coding task; it only checks binary/basic CLI availability.
    """
    if agent not in AGENT_ADAPTERS:
        return False, {
            "category": "unsupported_agent",
            "agent": agent,
            "reason": f"Unsupported agent: {agent}",
            "supportedAgents": sorted(AGENT_ADAPTERS.keys()),
        }

    spec = AGENT_ADAPTERS[agent]
    binary = spec["binary"]
    binary_path = shutil.which(binary)
    if not binary_path:
        return False, {
            "category": "missing_binary",
            "agent": agent,
            "binary": binary,
            "reason": f"Required CLI binary not found: {binary}",
        }

    code, stdout, stderr = run_cmd(spec["probe"], cwd=workspace_cwd())
    output = (stdout + stderr).strip()
    if code != 0:
        return False, {
            "category": "agent_probe_failed",
            "agent": agent,
            "binary": binary,
            "binaryPath": binary_path,
            "probe": spec["probe"],
            "exitCode": code,
            "reason": output[:500] or "Agent probe failed",
        }

    return True, {
        "category": "ok",
        "agent": agent,
        "binary": binary,
        "binaryPath": binary_path,
        "probeOutput": output[:300],
    }


def format_preflight_failure(info: dict) -> str:
    """Format a preflight failure for logs."""
    return json.dumps(info, ensure_ascii=False, indent=2)


def discover_available_agents(preferred: list[str] | None = None) -> dict:
    """Return preflight results for supported coding-agent CLIs.

    The result is diagnostic-only: no coding task is launched.
    """
    order = preferred or ["codex", "claude", "trae"]
    checked = []
    selected = None
    for name in order:
        ok, info = preflight_agent(name)
        checked.append(info)
        if ok and selected is None:
            selected = name
    return {
        "selected": selected,
        "checked": checked,
        "supportedAgents": sorted(AGENT_ADAPTERS.keys()),
    }


def resolve_agent(agent: str | None) -> tuple[str | None, dict]:
    """Resolve `auto` to an installed/probeable coding agent.

    Explicit agent names are returned unchanged after a normal preflight check.
    `auto` tries codex -> claude -> trae/trae-cn and returns the first passing
    agent. If none pass, the diagnostic includes every checked adapter plus a
    current-session fallback hint.
    """
    requested = (agent or "auto").strip().lower()
    if requested in {"", "auto"}:
        report = discover_available_agents()
        if report.get("selected"):
            return str(report["selected"]), {
                "category": "ok",
                "requested": "auto",
                "selected": report["selected"],
                "checked": report["checked"],
            }
        return None, {
            "category": "no_available_agent",
            "requested": "auto",
            "reason": "No supported coding-agent CLI passed preflight",
            "checked": report["checked"],
            "options": [
                "Install/configure one supported CLI: codex, claude, or traecli (Trae/Trae-CN).",
                "Use current-session mode instead: automind scaffold <task>, or /automind <task> inside an installed coding agent.",
                "Specify an installed agent explicitly: automind ask <task> claude|codex|trae|trae-cn.",
            ],
        }

    ok, info = preflight_agent(requested)
    if ok:
        return requested, info
    return None, info


def _agent_session_policy() -> str:
    """Return primary-session policy for detached CLI Planner/Generator work."""
    raw = os.environ.get("AUTOMIND_AGENT_SESSION_POLICY", "primary-persistent")
    value = raw.strip().lower()
    if value in {"fresh", "none", "disabled"}:
        return "fresh"
    return "primary-persistent"


def _session_role_for_phase(phase: str) -> str:
    phase = (phase or "generic").strip().lower()
    if phase in PRIMARY_SESSION_PHASES:
        return "primary"
    if phase in FRESH_SESSION_PHASES:
        return "evaluator"
    return "fresh"


def _read_agent_sessions(task_dir: Path) -> dict:
    state = read_runtime_state(task_dir) or {}
    sessions = state.get("agentSessions")
    return sessions if isinstance(sessions, dict) else {}


def _write_agent_sessions(task_dir: Path, sessions: dict) -> None:
    update_runtime_state(task_dir, agentSessions=sessions)


def _primary_session_for(task_dir: Path, agent: str) -> dict:
    sessions = _read_agent_sessions(task_dir)
    primary = sessions.get("primary") if isinstance(sessions.get("primary"), dict) else {}
    if primary.get("agent") != agent:
        return {}
    return primary


def _latest_generator_log_contains(task_dir: Path, needle: str) -> bool:
    """Return whether the latest generator.log contains a small marker."""
    if not needle:
        return False
    logs_dir = task_dir / "logs"
    try:
        candidates = sorted(
            logs_dir.glob("iter-*/generator.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        candidates = []
    for path in candidates[:3]:
        try:
            if needle in path.read_text(errors="ignore")[:65536]:
                return True
        except Exception:
            continue
    return False


def _task_requests_fresh_approval_session(task_dir: Path) -> bool:
    """Detect user/evaluator handoffs asking not to resume approval=never Codex."""
    state = read_runtime_state(task_dir) or {}
    fragments: list[str] = []
    for key in ("latestUserAnswer", "askUserQuestion", "resumeRecovery"):
        value = state.get(key)
        if isinstance(value, dict):
            fragments.append(json.dumps(value, ensure_ascii=False).lower())
    text = "\n".join(fragments)
    return (
        "approval_capable_or_host_runner" in text
        or "fresh approval" in text
        or "fresh approval-capable" in text
        or ("do not resume" in text and "approval" in text)
    )


def _maybe_clear_stale_codex_primary_session(task_dir: Path, primary: dict) -> bool:
    """Clear old approval=never Codex sessions when current policy can request approval.

    Codex stores the approval policy in the persistent session. If an older
    primary session was created with approval=never, `codex exec resume <id>`
    keeps that policy even after AutoMind's adapter default changes to
    --ask-for-approval on-request. In that case, resuming is actively harmful:
    the session can neither request host approvals nor prove host-only Android
    commands. Clear it so the next primary command starts fresh.
    """
    if primary.get("agent") != "codex" or not primary.get("sessionId"):
        return False
    desired = os.environ.get("AUTOMIND_CODEX_ASK_FOR_APPROVAL", "on-request").strip().lower()
    if desired in {"", "never"}:
        return False
    if not _latest_generator_log_contains(task_dir, "approval: never"):
        return False
    if not _task_requests_fresh_approval_session(task_dir):
        return False
    return clear_task_primary_session(
        task_dir,
        reason=f"stale_codex_approval_never_policy_desired_{desired}",
    )


def _record_primary_session(task_dir: Path, agent: str, session_id: str, phase: str, action: str, meta: dict | None = None) -> None:
    if not session_id:
        return
    sessions = _read_agent_sessions(task_dir)
    existing = sessions.get("primary") if isinstance(sessions.get("primary"), dict) else {}
    created_at = existing.get("createdAt") if existing.get("agent") == agent else None
    try:
        current_iteration = int((read_runtime_state(task_dir) or {}).get("iteration", 0) or 0)
    except (TypeError, ValueError):
        current_iteration = 0
    sessions["primary"] = {
        "agent": agent,
        "sessionId": session_id,
        "policy": "primary-persistent",
        "role": "planner_generator_repair",
        "lastPhase": phase,
        "lastAction": action,
        "createdAt": created_at or datetime.now().isoformat(timespec="seconds"),
        "createdIteration": existing.get("createdIteration", current_iteration) if existing.get("agent") == agent else current_iteration,
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    if isinstance(meta, dict):
        bypass = bool(meta.get("agentExecutionBypass"))
        sessions["primary"]["agentExecutionBypass"] = bypass
        sessions["primary"]["executionMode"] = "dangerous_bypass" if bypass else "normal"
        if agent == "codex" and bypass:
            sessions["primary"]["codexDangerousBypass"] = True  # legacy audit field
    _write_agent_sessions(task_dir, sessions)


def _record_fresh_session(task_dir: Path, agent: str, phase: str, session_id: str | None, action: str) -> None:
    sessions = _read_agent_sessions(task_dir)
    runs = sessions.get("freshRuns") if isinstance(sessions.get("freshRuns"), list) else []
    runs.append({
        "agent": agent,
        "phase": phase,
        "sessionId": session_id or "",
        "policy": "fresh-isolated" if phase == "evaluator" else "fresh",
        "action": action,
        "createdAt": datetime.now().isoformat(timespec="seconds"),
    })
    sessions["freshRuns"] = runs[-20:]
    _write_agent_sessions(task_dir, sessions)




def _primary_session_iteration_age(primary: dict, current_iteration: int) -> int:
    try:
        created_iter = int(primary.get("createdIteration", current_iteration) or current_iteration)
    except (TypeError, ValueError):
        created_iter = current_iteration
    return max(0, int(current_iteration or 0) - created_iter)


def _maybe_clear_rotation_primary_session(task_dir: Path, primary: dict, phase: str) -> bool:
    """Rotate long-lived primary sessions before context becomes the state store."""
    if not primary.get("sessionId"):
        return False
    state = read_runtime_state(task_dir) or {}
    recovery = state.get("resumeRecovery") if isinstance(state.get("resumeRecovery"), dict) else {}
    reason = str(recovery.get("reason") or state.get("lastResult") or "").lower()
    if any(marker in reason for marker in ["context", "window", "overflow", "run out"]):
        return clear_task_primary_session(task_dir, reason="context_exhausted_start_fresh")
    try:
        iteration = int(state.get("iteration", 0) or 0)
    except (TypeError, ValueError):
        iteration = 0
    threshold_raw = os.environ.get("AUTOMIND_AGENT_SESSION_ITERATION_THRESHOLD", "12")
    try:
        threshold = int(threshold_raw)
    except (TypeError, ValueError):
        threshold = 12
    if threshold > 0 and _primary_session_iteration_age(primary, iteration) >= threshold:
        return clear_task_primary_session(task_dir, reason=f"iteration_threshold_rotation_{threshold}")
    return False

def parse_agent_session_id(agent: str, output: str) -> str | None:
    """Extract a CLI session id from agent output when the CLI exposes one."""
    if not output:
        return None
    if agent == "codex":
        match = re.search(r"session id:\s*([0-9a-fA-F-]{20,})", output, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    if agent in {"claude", "trae", "trae-cn"}:
        for pattern in [
            r"session(?:\s+id)?[:=]\s*([0-9a-fA-F-]{32,36})",
            r"conversation(?:\s+id)?[:=]\s*([0-9a-fA-F-]{32,36})",
        ]:
            match = re.search(pattern, output, flags=re.IGNORECASE)
            if match:
                return match.group(1)
    return None


def build_agent_cli_command(agent: str, prompt: str, task_dir: Path, phase: str = "generic") -> tuple[list[str], dict]:
    """Build an agent command plus session metadata without running it."""
    adapter = AGENT_ADAPTERS.get(agent)
    if not adapter:
        raise ValueError(f"Unknown agent: {agent}")

    normalized_phase = (phase or "generic").strip().lower()
    role = _session_role_for_phase(normalized_phase)
    primary_enabled = _agent_session_policy() == "primary-persistent"
    meta = {
        "agent": agent,
        "phase": normalized_phase,
        "sessionRole": role,
        "sessionPolicy": "fresh-isolated" if role == "evaluator" else ("primary-persistent" if role == "primary" and primary_enabled else "fresh"),
        "sessionAction": "fresh",
        "sessionId": "",
        "supportsPersistentSession": agent in {"codex", "claude", "trae", "trae-cn"},
    }

    if role == "evaluator" and agent == "codex":
        # Evaluator must be fresh-isolated for context purity and always bypassed
        # for execution power. Task-level Planner/Generator bypass policy does
        # not constrain Evaluator evidence collection.
        meta.update({
            "sessionPolicy": "fresh-isolated",
            "sessionAction": "fresh",
            "agentExecutionBypass": True,
            "executionMode": "dangerous_bypass",
            "executionModeReason": "evaluator_always_bypassed",
        })
        return [
            "codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "exec",
            "--skip-git-repo-check",
            "-C", str(AUTOMIND_WORKSPACE_ROOT),
            prompt,
        ], meta

    if role == "primary" and primary_enabled and agent == "codex":
        bypass_enabled = _agent_bypass_approvals_enabled(task_dir)
        primary = _primary_session_for(task_dir, agent)
        if bypass_enabled:
            # Detached/non-TTY Codex defaults to no-sandbox/no-approval for high
            # automation. Do not silently resume an older sandbox/approval-mode
            # primary session; start fresh once, then reuse the bypass session
            # for Planner/Generator context continuity.
            if primary and primary.get("agentExecutionBypass") is not True:
                clear_task_primary_session(task_dir, reason="agent_execution_mode_changed_to_bypass")
                primary = {}
            session_id = str(primary.get("sessionId") or "")
            if primary and _maybe_clear_rotation_primary_session(task_dir, primary, normalized_phase):
                primary = {}
                session_id = ""
            if session_id:
                meta.update({"sessionAction": "resume", "sessionId": session_id, "agentExecutionBypass": True, "executionMode": "dangerous_bypass"})
                return ["codex", "--dangerously-bypass-approvals-and-sandbox", "exec", "resume", "--skip-git-repo-check", "--", session_id, prompt], meta
            meta.update({"sessionAction": "new", "agentExecutionBypass": True, "executionMode": "dangerous_bypass"})
            return adapter["command"](prompt, task_dir), meta
        if primary and primary.get("agentExecutionBypass") is True:
            clear_task_primary_session(task_dir, reason="agent_execution_mode_changed_to_normal")
            primary = {}
        if primary and _maybe_clear_stale_codex_primary_session(task_dir, primary):
            primary = {}
        session_id = str(primary.get("sessionId") or "")
        if session_id:
            meta.update({"sessionAction": "resume", "sessionId": session_id})
            return ["codex", "exec", "resume", "--skip-git-repo-check", "--", session_id, prompt], meta
        meta.update({"sessionAction": "new", "agentExecutionBypass": False, "executionMode": "normal"})
        return adapter["command"](prompt, task_dir), meta

    if role == "primary" and primary_enabled and agent == "claude":
        bypass_enabled = _agent_bypass_approvals_enabled(task_dir)
        primary = _primary_session_for(task_dir, agent)
        if primary and primary.get("agentExecutionBypass") is not bypass_enabled:
            clear_task_primary_session(task_dir, reason="agent_execution_mode_changed")
            primary = {}
        session_id = str(primary.get("sessionId") or "") or str(uuid.uuid4())
        meta.update({
            "sessionAction": "resume" if primary else "new",
            "sessionId": session_id,
            "agentExecutionBypass": bypass_enabled,
            "executionMode": "dangerous_bypass" if bypass_enabled else "normal",
        })
        # claude --session-id <id> *creates* a session and fails with
        # "Session ID ... is already in use" if the id already exists. To reuse a
        # persisted primary session we must pass --resume <id> instead; only a
        # brand-new session uses --session-id.
        session_args = ["--resume", session_id] if primary else ["--session-id", session_id]
        stream_flags = CLAUDE_STREAM_JSON_FLAGS if _claude_stream_json_enabled() else []
        if bypass_enabled:
            return [
                "claude",
                "--print",
                *stream_flags,
                "--dangerously-skip-permissions",
                "--permission-mode",
                "bypassPermissions",
                *session_args,
                prompt,
            ], meta
        return ["claude", "--print", *stream_flags, *session_args, prompt], meta

    if role == "primary" and primary_enabled and agent in {"trae", "trae-cn"}:
        bypass_enabled = _agent_bypass_approvals_enabled(task_dir)
        primary = _primary_session_for(task_dir, agent)
        if primary and primary.get("agentExecutionBypass") is not bypass_enabled:
            clear_task_primary_session(task_dir, reason="agent_execution_mode_changed")
            primary = {}
        session_id = str(primary.get("sessionId") or "") or str(uuid.uuid4())
        session_action = "resume" if primary else "new"
        meta.update({
            "sessionAction": session_action,
            "sessionId": session_id,
            "agentExecutionBypass": bypass_enabled,
            "executionMode": "dangerous_bypass" if bypass_enabled else "normal",
        })
        session_args = ["--resume", session_id] if primary else ["--session-id", session_id]
        cmd = [
            "traecli",
            "-p",
            prompt,
            *session_args,
            "--allowed-tool",
            adapter["allowedTools"],
        ]
        if bypass_enabled:
            cmd.append("--yolo")
        cmd.append("--json")
        return cmd, meta

    # Evaluator and unsupported persistent-session agents intentionally get a
    # fresh process/session. This preserves context isolation. Coding-agent
    # Evaluators always bypass regardless of task Planner/Generator policy.
    if role == "evaluator":
        meta.update({
            "sessionPolicy": "fresh-isolated",
            "sessionAction": "fresh",
            "agentExecutionBypass": True,
            "executionMode": "dangerous_bypass",
            "executionModeReason": "evaluator_always_bypassed",
        })
        if agent == "claude":
            stream_flags = CLAUDE_STREAM_JSON_FLAGS if _claude_stream_json_enabled() else []
            return ["claude", "--print", *stream_flags, "--dangerously-skip-permissions", "--permission-mode", "bypassPermissions", prompt], meta
        if agent in {"trae", "trae-cn"}:
            return ["traecli", "-p", prompt, "--allowed-tool", adapter["allowedTools"], "--yolo", "--json"], meta
    return adapter["command"](prompt, task_dir), meta


def record_agent_session_after_run(task_dir: Path, meta: dict, output: str, code: int) -> None:
    """Persist reusable primary sessions; audit fresh isolated sessions."""
    if not isinstance(meta, dict):
        return
    agent = str(meta.get("agent") or "")
    phase = str(meta.get("phase") or "generic")
    role = str(meta.get("sessionRole") or "fresh")
    action = str(meta.get("sessionAction") or "fresh")
    session_id = str(meta.get("sessionId") or "") or (parse_agent_session_id(agent, output) or "")

    if role == "primary" and meta.get("sessionPolicy") == "primary-persistent" and session_id:
        _record_primary_session(task_dir, agent, session_id, phase, action, meta)
        return

    if role == "evaluator":
        _record_fresh_session(task_dir, agent, phase, session_id, action)


def run_agent(
    mode: Literal["cli", "llm"],
    agent: str,
    prompt: str,
    task_dir: Path,
    phase: str = "generic",
    quiet: bool = False,
) -> tuple[int, str]:
    """
    执行 Agent
    mode: cli - 命令行模式，llm - LLM API 模式
    agent: codex / claude / trae / trae-cn
    """

    if mode == "cli":
        resolved_agent, info = resolve_agent(agent)
        if not resolved_agent:
            return -1, "[AutoMind] Agent preflight failed:\n" + format_preflight_failure(info)
        if info.get("requested") == "auto" and not quiet:
            log(f"Auto-selected coding agent: {resolved_agent}")
        return run_agent_cli(resolved_agent, prompt, task_dir, phase=phase, quiet=quiet)
    else:
        return run_agent_llm(agent, prompt, task_dir)




def _read_small_text(path: Path, max_bytes: int = 65536) -> str:
    try:
        data = path.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def task_needs_android_environment(task_dir: Path) -> bool:
    """Heuristic: enable Android env only for Android-relevant tasks/projects.

    Task type is the strongest signal. Do not enable adb just because a generic
    mobile/iOS artifact mentions real devices (for example Chinese `真机`).
    """
    state = read_runtime_state(task_dir) or {}
    task_type = str(state.get("taskType") or "").strip().lower()
    if task_type in {"android", "dual"}:
        return True
    if task_type in {"ios", "script", "web"}:
        return False

    root = Path(workspace_cwd())
    project_markers = [
        root / "settings.gradle",
        root / "settings.gradle.kts",
        root / "build.gradle",
        root / "build.gradle.kts",
        root / "gradlew",
        root / "app" / "src" / "main" / "AndroidManifest.xml",
    ]
    if any(path.exists() for path in project_markers):
        # A Gradle project alone is not always Android, so require Android-ish
        # files or task text when possible. AndroidManifest is a strong signal.
        if (root / "app" / "src" / "main" / "AndroidManifest.xml").exists():
            return True
    task_text = "\n".join(_read_small_text(task_dir / name) for name in [
        "Requirements.md",
        "TestCases.md",
        "Plan.md",
        "Brainstorm.md",
        "evaluation.json",
        "runtime-state.json",
    ])
    lowered = task_text.lower()
    android_markers = [
        "android",
        "adb",
        "apk",
        "gradlew",
        "android-preflight",
        "android-probe-flow",
        "uiautomator",
        "安卓",
    ]
    return any(marker in lowered for marker in android_markers)


def _android_sdk_candidates(env: dict[str, str]) -> list[Path]:
    candidates: list[Path] = []
    for key in ["ANDROID_HOME", "ANDROID_SDK_ROOT"]:
        raw = env.get(key)
        if raw:
            candidates.append(Path(raw))

    root = Path(workspace_cwd())
    for base in [root, *root.parents]:
        local = base / "local.properties"
        if local.exists():
            for line in _read_small_text(local).splitlines():
                if line.strip().startswith("sdk.dir="):
                    raw = line.split("=", 1)[1].strip().replace("\\:", ":")
                    candidates.append(Path(raw).expanduser())
                    break

    which_adb = shutil.which("adb", path=env.get("PATH"))
    if which_adb:
        adb_path = Path(which_adb).resolve()
        if adb_path.parent.name == "platform-tools":
            candidates.append(adb_path.parent.parent)

    # Common defaults, not user-specific constants. They are only candidates and
    # are used after env/local.properties/PATH discovery.
    candidates.extend([
        Path.home() / "Library" / "Android" / "sdk",
        Path.home() / "Android" / "Sdk",
    ])

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            resolved = path.expanduser().resolve()
        except Exception:
            resolved = path.expanduser()
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            deduped.append(resolved)
    return deduped


def discover_android_sdk(env: dict[str, str]) -> tuple[Path | None, dict]:
    checked: list[str] = []
    for sdk in _android_sdk_candidates(env):
        checked.append(str(sdk))
        adb = sdk / "platform-tools" / "adb"
        if adb.exists() and adb.is_file():
            return sdk, {"strategy": "discovered", "sdk": str(sdk), "adb": str(adb), "checked": checked}
    return None, {"strategy": "not_found", "checked": checked}


def build_agent_subprocess_env(task_dir: Path) -> tuple[dict[str, str], dict]:
    """Return subprocess env plus Android discovery diagnostics for coding agents."""
    env = os.environ.copy()
    diagnostics: dict = {"androidEnvEnabled": False, "reason": "not_android_task"}
    if not task_needs_android_environment(task_dir):
        return env, diagnostics

    sdk, discovery = discover_android_sdk(env)
    diagnostics = {"androidEnvEnabled": bool(sdk), "reason": "android_task_detected", "discovery": discovery}
    if not sdk:
        return env, diagnostics

    sdk_str = str(sdk)
    env.setdefault("ANDROID_HOME", sdk_str)
    env.setdefault("ANDROID_SDK_ROOT", sdk_str)
    platform_tools = str(sdk / "platform-tools")
    current_path = env.get("PATH", "")
    parts = current_path.split(os.pathsep) if current_path else []
    if platform_tools not in parts:
        env["PATH"] = platform_tools + (os.pathsep + current_path if current_path else "")
    adb = sdk / "platform-tools" / "adb"
    if adb.exists():
        env.setdefault("AUTOMIND_ADB", str(adb))
    diagnostics["env"] = {
        "ANDROID_HOME": env.get("ANDROID_HOME", ""),
        "ANDROID_SDK_ROOT": env.get("ANDROID_SDK_ROOT", ""),
        "AUTOMIND_ADB": env.get("AUTOMIND_ADB", ""),
        "platformToolsPrepended": platform_tools,
    }
    return env, diagnostics




def adb_devices_output_has_ready_device(output: str) -> bool:
    """Return True when `adb devices -l` output contains state=device."""
    for line in (output or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and not parts[0].lower().startswith("list") and parts[1] == "device":
            return True
    return False


def ensure_android_adb_server_ready(task_dir: Path, env: dict[str, str], diagnostics: dict | None = None) -> dict:
    """Best-effort host-side adb server prestart before sandboxed agents run."""
    diagnostics = diagnostics or {}
    if not diagnostics.get("androidEnvEnabled"):
        return {"attempted": False, "reason": diagnostics.get("reason", "android_env_disabled")}
    adb = env.get("AUTOMIND_ADB") or shutil.which("adb", path=env.get("PATH"))
    report: dict = {"attempted": False, "adb": adb or "", "startExit": None, "devicesExit": None, "discovery": diagnostics.get("discovery")}
    if not adb:
        append_event(task_dir, "android_adb_server_prestart", "No adb found for host-side prestart", level="warn", source="automind", data=report)
        return report
    report["attempted"] = True
    try:
        start = subprocess.run([adb, "start-server"], cwd=workspace_cwd(), env=env, text=True, capture_output=True, timeout=12)
        report.update({"startExit": start.returncode, "startOutputTail": ((start.stdout or "") + (start.stderr or ""))[-2000:]})
    except Exception as exc:
        report.update({"startExit": 124, "startOutputTail": str(exc)})
    try:
        devices = subprocess.run([adb, "devices", "-l"], cwd=workspace_cwd(), env=env, text=True, capture_output=True, timeout=8)
        report.update({"devicesExit": devices.returncode, "devicesOutputTail": ((devices.stdout or "") + (devices.stderr or ""))[-2000:]})
    except Exception as exc:
        report.update({"devicesExit": 124, "devicesOutputTail": str(exc)})
    level = "info" if report.get("devicesExit") == 0 and adb_devices_output_has_ready_device(str(report.get("devicesOutputTail") or "")) else "warn"
    append_event(
        task_dir,
        "android_adb_server_prestart",
        f"Host-side adb prestart {('ready' if level == 'info' else 'not ready')}: {adb}",
        level=level,
        source="automind",
        data=report,
    )
    return report


def _claude_stream_event_to_display(raw_line: str) -> str | None:
    """Turn one claude stream-json line into readable incremental TUI text.

    Returns None to suppress noisy/structural events. Non-JSON lines are passed
    through unchanged so nothing is silently lost.
    """
    line = raw_line.strip()
    if not line or not line.startswith("{"):
        return raw_line or None
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return raw_line or None
    if not isinstance(event, dict):
        return raw_line or None
    etype = event.get("type")
    if etype == "assistant":
        message = event.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        parts: list[str] = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text" and block.get("text"):
                    parts.append(str(block["text"]))
                elif btype == "tool_use" and block.get("name"):
                    parts.append(f"[tool: {block['name']}]")
        text = "\n".join(p for p in parts if p).strip()
        return text or None
    if etype == "result":
        # The final result text is re-published via the decoded transcript; avoid
        # duplicating the whole answer here. Surface only an error subtype note.
        if event.get("is_error") or str(event.get("subtype") or "") not in {"", "success"}:
            return f"[claude result: {event.get('subtype') or 'error'}]"
        return None
    # system/init/usage/etc. structural events are not useful as TUI lines.
    return None


def _claude_stream_decode_output(lines: list[str]) -> str:
    """Rebuild the final assistant transcript from claude stream-json lines.

    Prefers the authoritative `result` event text (same content as `--print`
    text mode). Falls back to concatenated assistant text blocks, then to the
    raw output, so callers always get usable text even if the format shifts.
    """
    result_text: str | None = None
    assistant_chunks: list[str] = []
    saw_json = False
    for raw in lines:
        stripped = raw.strip()
        if not stripped or not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        saw_json = True
        etype = event.get("type")
        if etype == "result" and isinstance(event.get("result"), str):
            result_text = event["result"]
        elif etype == "assistant":
            message = event.get("message") or {}
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                        assistant_chunks.append(str(block["text"]))
    if result_text is not None:
        return result_text
    if assistant_chunks:
        return "\n".join(assistant_chunks).strip()
    if saw_json:
        # JSON events seen but no extractable text; return raw so nothing is lost.
        return "".join(lines)
    return "".join(lines)


def run_agent_cli(
    agent: str,
    prompt: str,
    task_dir: Path,
    phase: str = "generic",
    quiet: bool = False,
) -> tuple[int, str]:
    """通过 CLI 执行 Agent，对短时性故障自动重试。

    Retry policy (R01/AC-001/AC-002):
    - 最多 ``AGENT_RETRY_MAX_ATTEMPTS`` 次（默认 3）retry
    - 退避序列 ``AGENT_RETRY_BACKOFF`` = (1s, 3s, 9s)
    - 仅 timeout / network glitch / process crash 走 retry，code==0 直接返回
    - 每次 retry 前打印 ``[heartbeat] retry=N/M backoff=Ks reason=...`` 行
    """

    try:
        cmd, meta = build_agent_cli_command(agent, prompt, task_dir, phase=phase)
    except ValueError as exc:
        return -1, str(exc)

    action = meta.get("sessionAction")
    policy = meta.get("sessionPolicy")
    session_suffix = f" session={action}/{policy}"
    if meta.get("sessionId"):
        session_suffix += f" id={str(meta.get('sessionId'))[:8]}..."
    if not quiet:
        log(f"Running command: {' '.join(cmd[:3])}... phase={phase}{session_suffix}")
    raw_timeout = os.environ.get("AUTOMIND_AGENT_TIMEOUT", os.environ.get("AUTOMIND_CMD_TIMEOUT", "43200"))
    try:
        timeout = int(raw_timeout)
    except ValueError:
        timeout = 43200

    max_attempts = max(1, AGENT_RETRY_MAX_ATTEMPTS)
    backoff = AGENT_RETRY_BACKOFF
    last_code, last_output = -1, ""
    agent_env, agent_env_diagnostics = build_agent_subprocess_env(task_dir)
    if agent_env_diagnostics.get("androidEnvEnabled"):
        append_event(task_dir, "agent_android_env", "Android environment prepared for coding agent", source="automind", data=agent_env_diagnostics)
    ensure_android_adb_server_ready(task_dir, agent_env, agent_env_diagnostics)

    # claude stream-json: decode realtime JSON-line events into readable TUI
    # output and rebuild the clean final transcript for downstream parsing.
    stream_kwargs: dict = {}
    if agent == "claude" and "stream-json" in cmd:
        stream_kwargs = {
            "display_transform": _claude_stream_event_to_display,
            "output_decoder": _claude_stream_decode_output,
        }

    for attempt in range(1, max_attempts + 2):  # initial + up to max_attempts retries
        code, stdout, stderr = stream_agent_command(
            cmd,
            task_dir=task_dir,
            agent=agent,
            phase=phase,
            cwd=workspace_cwd(),
            timeout=timeout,
            mirror_stdout=(not quiet and os.environ.get("AUTOMIND_AGENT_MIRROR_STDOUT", "0") == "1"),
            env=agent_env,
            **stream_kwargs,
        )
        output = stdout + stderr
        last_code, last_output = code, output

        if code == -3:
            # Agent I/O bridge stopped the subprocess because task artifacts
            # reached a durable ask_user gate. This is a controlled pause, not
            # an execution failure and must not be retried.
            break
        if code == 0 or not _is_retryable_agent_failure(code, stderr):
            break
        if attempt > max_attempts:
            break

        # Exponential backoff: index attempt-1 maps to (1s, 3s, 9s)
        delay = backoff[min(attempt - 1, len(backoff) - 1)]
        reason = "timeout" if code == -1 else "transient"
        if not quiet:
            log(
                f"[heartbeat] retry={attempt}/{max_attempts} backoff={delay:.1f}s "
                f"reason={reason} agent={agent} phase={phase}"
            )
        time.sleep(delay)

    record_agent_session_after_run(task_dir, meta, last_output, last_code)

    if last_code == -3:
        if not quiet:
            log("Agent paused because task requires human input")
    elif last_code != 0:
        error(f"Agent execution failed: {last_output[-200:]}")
    elif not quiet:
        success("Agent execution completed")

    return last_code, last_output



def extract_agent_reply(agent: str, output: str) -> str:
    """Return the human-facing assistant reply from noisy CLI output."""
    text = output or ""
    if agent == "codex":
        matches = list(re.finditer(r"(?:^|\n)codex\n", text))
        if matches:
            start = matches[-1].end()
            tail = text[start:]
            stop_candidates = []
            for marker in ["\nhook: Stop", "\ntokens used", "\n--------\n"]:
                idx = tail.find(marker)
                if idx >= 0:
                    stop_candidates.append(idx)
            end = min(stop_candidates) if stop_candidates else len(tail)
            reply = tail[:end].strip()
            if reply:
                return reply
        # Fallback: strip obvious runtime banners/noise and keep the tail.
        clean_lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                clean_lines.append(line)
                continue
            lower = stripped.lower()
            if lower.startswith(("warning:", "openai codex", "workdir:", "model:", "provider:", "approval:", "sandbox:", "reasoning ", "session id:", "hook:", "tokens used")):
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}t.*\berror\b", stripped, flags=re.IGNORECASE):
                continue
            clean_lines.append(line)
        fallback = "\n".join(clean_lines).strip()
        return fallback or text.strip()
    return text.strip()


def run_agent_llm(
    agent: str,
    prompt: str,
    task_dir: Path
) -> tuple[int, str]:
    """Run through LLM API (reserved interface)."""
    # TODO: 实现 LLM API 模式
    return -1, "LLM mode is not implemented yet"
