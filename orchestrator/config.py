"""Configuration constants for the AutoMind orchestrator.

This module is intentionally small and side-effect free. Keep user-facing CLI
behavior in ``main.py`` / future ``cli.py``; keep runtime state helpers in
``state.py``.
"""
from __future__ import annotations

import os
from pathlib import Path

# Runtime root is the AutoMind installation/checkout. It owns scripts,
# templates, schemas, requirements, and bundled examples.
AUTOMIND_RUNTIME_ROOT = Path(__file__).parent.parent.resolve()
# Backward-compatible alias for runtime assets.
AUTOMIND_ROOT = AUTOMIND_RUNTIME_ROOT


def _resolve_workspace_root() -> Path:
    """Return the project/workspace root for task artifacts.

    Installed AutoMind runs from the AutoMind checkout, but users invoke it from
    their own project. Task files must therefore live under the caller's project
    by default, not under the AutoMind runtime checkout.
    """
    raw = os.environ.get("AUTOMIND_WORKSPACE_ROOT") or os.environ.get("AUTOMIND_PROJECT_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd().resolve()


AUTOMIND_WORKSPACE_ROOT = _resolve_workspace_root()

# Task artifacts and local summaries belong to the caller's project/workspace.
TASKS_DIR = AUTOMIND_WORKSPACE_ROOT / ".automind" / "tasks"
SUMMARY_DIR = AUTOMIND_WORKSPACE_ROOT / ".automind" / "summary"
PROMPTS_DIR = AUTOMIND_RUNTIME_ROOT / "templates"
# Low-risk Python helper packages are installed into the user workspace, not the
# AutoMind runtime checkout. This keeps installed AutoMind immutable and lets
# each project carry its own verification helper environment.
ANDROID_TOOLS_VENV = AUTOMIND_WORKSPACE_ROOT / ".venv-android-tools"
IOS_TOOLS_VENV = AUTOMIND_WORKSPACE_ROOT / ".venv-ios-tools"
VISUAL_TOOLS_VENV = AUTOMIND_WORKSPACE_ROOT / ".venv-visual-tools"
AUTOMATION_SETUP_DIR = AUTOMIND_WORKSPACE_ROOT / ".automind" / "setup" / "automation-tools"
SUMMARY_LESSONS_PATH = SUMMARY_DIR / "lessons-learned.md"
LOCAL_REUSE_INDEX_PATH = SUMMARY_DIR / "local-reuse-index.md"

# Machine-global accumulated summaries live in the AutoMind runtime install,
# not in any single project workspace. This directory is install-exclude
# protected (see install.sh `--exclude='summaries/accumulated/'`), so lessons
# sunk here survive runtime re-installs. Two destinations only:
# - technical/        : cross-project, business-agnostic, public-safe lessons.
# - business/<slug>/  : project-bound lessons reusable across that project's tasks.
ACCUMULATED_DIR = AUTOMIND_RUNTIME_ROOT / "summaries" / "accumulated"
ACCUMULATED_TECHNICAL_DIR = ACCUMULATED_DIR / "technical"
ACCUMULATED_BUSINESS_DIR = ACCUMULATED_DIR / "business"
# Simple anti-bloat guard: cap entries per accumulated file. Once reached, new
# entries are skipped (existing knowledge is preserved). Promotion to preloaded
# is a maintainer-only, git-distributed step and is intentionally not automated.
ACCUMULATED_MAX_ENTRIES_PER_FILE = int(os.environ.get("AUTOMIND_ACCUMULATED_MAX_ENTRIES", "500"))

# Public-safe preloaded seed summaries live under `summaries/preloaded/<pack>.md`.
# Packs are discovered by directory/file prefix instead of a hard-coded file
# list, so new curated packs can be added without touching code.
#
# Naming convention:
# - common-*  : cross-stack guidance (build/test/reuse/logs when not client-only)
# - client-*  : cross-client/mobile guidance shared by iOS/Android
# - ios-*     : iOS-specific guidance
# - android-* : Android-specific guidance
#
# Keep this business-agnostic: project/domain-specific knowledge belongs in
# `summaries/accumulated/business/**`, not in preloaded packs.
PRELOADED_SUMMARY_PREFIXES = {
    "common": ["common-"],
    "mobile_common": ["client-"],
    "ios": ["ios-"],
    "android": ["android-"],
}

# Progressive loading budget: Reuse.md includes only pack overviews, not full
# pack content. Agents should read the referenced pack README only when the
# task needs that capability.
PRELOADED_OVERVIEW_MAX_CHARS_PER_PACK = int(os.environ.get("AUTOMIND_PRELOADED_OVERVIEW_MAX_CHARS", "700"))


# Agent-specific adapters stay deliberately thin: they only describe how to
# discover and invoke a concrete coding-agent runtime. AutoMind's task files,
# evaluation schema, retry policy, and evidence rules remain agent-agnostic.
TRAE_ALLOWED_TOOLS = "Read,Write,Edit,ApplyPatch,Glob,LS,Grep,Bash,BashOutput,KillShell,Task,Skill,TodoWrite"

def _read_runtime_state_for_execution_policy(task_dir) -> dict:
    try:
        from orchestrator.state import read_runtime_state

        state = read_runtime_state(Path(task_dir))
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def _task_agent_execution_policy(task_dir) -> dict:
    """Return the task-level coding-agent execution policy.

    `agentExecutionPolicy` is the agent-agnostic source of truth. The legacy
    `codexDangerousBypass` field is read only for compatibility with older
    tasks and should not be used by new code as the primary policy field.

    Missing policy falls back to bypass for Planner/Generator so non-new tasks,
    detached/resume/script paths, or any flow that cannot ask still keep the
    high-automation default. New TUI tasks record default_bypass without prompting. Evaluator bypass is handled separately
    by the agent adapter layer.
    """
    state = _read_runtime_state_for_execution_policy(task_dir)
    policy = state.get("agentExecutionPolicy")
    if isinstance(policy, dict) and isinstance(policy.get("bypassApprovals"), bool):
        return policy
    legacy = state.get("codexDangerousBypass")
    if isinstance(legacy, dict) and legacy.get("consent") in {"user_approved", "user_declined"}:
        enabled = legacy.get("consent") == "user_approved"
        return {
            "bypassApprovals": enabled,
            "consent": legacy.get("consent"),
            "scope": legacy.get("scope", "task"),
            "source": "legacy_codexDangerousBypass",
            "agent": legacy.get("agent", "codex"),
        }
    return {
        "bypassApprovals": True,
        "consent": "default_bypass",
        "scope": "task",
        "source": "missing_policy_default_bypass",
    }


def _agent_bypass_approvals_enabled(task_dir) -> bool:
    """AutoMind runs supported coding agents in bypass mode by default.

    Task-level policy is still persisted for audit/history, but execution now
    consistently prefers the high-automation bypass path for Codex/Claude/Trae.
    High-risk actions are still expected to surface through AutoMind ask_user
    / completion gates rather than the underlying agent sandbox/approval mode.
    """
    _task_agent_execution_policy(task_dir)  # keep compatibility/audit reads warm
    return True


def _codex_dangerous_bypass_enabled(task_dir) -> bool:
    """Backward-compatible wrapper for older call sites."""
    return _agent_bypass_approvals_enabled(task_dir)


# claude stream-json streaming (claude only). When enabled, claude prints
# realtime JSON-line events (system/assistant/result) instead of buffering the
# whole answer until exit, so the TUI can show incremental output like codex.
# Decoding back to clean final text happens in the agent IO bridge. Set
# AUTOMIND_CLAUDE_STREAM_JSON=0 to fall back to the plain --print text mode.
CLAUDE_STREAM_JSON_FLAGS = ["--output-format", "stream-json", "--verbose"]


def _claude_stream_json_enabled() -> bool:
    return os.environ.get("AUTOMIND_CLAUDE_STREAM_JSON", "1").strip().lower() not in {"0", "false", "no", "off"}


def _codex_command(prompt, task_dir):
    if _agent_bypass_approvals_enabled(task_dir):
        return [
            "codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "exec",
            "--skip-git-repo-check",
            "-C", str(AUTOMIND_WORKSPACE_ROOT),
            prompt,
        ]
    return [
        "codex",
        "--ask-for-approval", os.environ.get("AUTOMIND_CODEX_ASK_FOR_APPROVAL", "on-request"),
        "exec",
        "--sandbox", "workspace-write",
        "--skip-git-repo-check",
        "-C", str(AUTOMIND_WORKSPACE_ROOT),
        prompt,
    ]


def _claude_command(prompt, task_dir):
    stream_flags = CLAUDE_STREAM_JSON_FLAGS if _claude_stream_json_enabled() else []
    if _agent_bypass_approvals_enabled(task_dir):
        return ["claude", "--print", *stream_flags, "--dangerously-skip-permissions", "--permission-mode", "bypassPermissions", prompt]
    return ["claude", "--print", *stream_flags, prompt]


def _trae_command(prompt, task_dir):
    cmd = ["traecli", "-p", prompt, "--allowed-tool", TRAE_ALLOWED_TOOLS]
    if _agent_bypass_approvals_enabled(task_dir):
        cmd.append("--yolo")
    cmd.append("--json")
    return cmd


AGENT_ADAPTERS = {
    "codex": {
        "binary": "codex",
        "probe": ["codex", "--version"],
        "description": "OpenAI Codex CLI",
        "command": _codex_command,
    },
    "claude": {
        "binary": "claude",
        "probe": ["claude", "--version"],
        "description": "Claude Code CLI",
        "command": _claude_command,
    },
    "trae": {
        "binary": "traecli",
        "probe": ["traecli", "--version"],
        "description": "Trae CLI",
        "allowedTools": TRAE_ALLOWED_TOOLS,
        "command": _trae_command,
    },
    "trae-cn": {
        "binary": "traecli",
        "probe": ["traecli", "--version"],
        "description": "Trae-CN CLI alias",
        "allowedTools": TRAE_ALLOWED_TOOLS,
        "command": _trae_command,
    },
}

MAX_ITERATIONS = int(os.environ.get("AUTOMIND_MAX_ITERATIONS", "1000"))
MAX_REFLECTIONS_PER_TC = int(os.environ.get("AUTOMIND_MAX_REFLECTIONS_PER_TC", "10"))
# When the per-TC reflection budget is exhausted, AutoMind first tries this many
# autonomous replan rounds (reuse the existing autonomous `replan` path) before
# escalating to a human `ask_user`. Set to 0 to keep the legacy behavior of
# escalating to ask_user immediately on budget exhaustion.
AUTONOMOUS_REPLAN_AFTER_BUDGET = int(os.environ.get("AUTOMIND_AUTONOMOUS_REPLAN_AFTER_BUDGET", "2"))

TASK_STATUSES = {
    "created",
    "planned",
    "ready",
    "generating",
    "evaluating",
    "retry_pending",
    "replan_pending",
    "human_input_pending",
    "paused_by_user",
    "completed",
    "finished",
    "failed",
    "aborted",
}

PLANNER_PRE_IMPLEMENTATION_DECISIONS = {"auto_proceed", "ask_user", "replan"}

# Ensure direct child scripts launched by the orchestrator resolve the same
# workspace even when their cwd is the runtime checkout.
os.environ.setdefault("AUTOMIND_RUNTIME_ROOT", str(AUTOMIND_RUNTIME_ROOT))
os.environ.setdefault("AUTOMIND_WORKSPACE_ROOT", str(AUTOMIND_WORKSPACE_ROOT))

RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'
