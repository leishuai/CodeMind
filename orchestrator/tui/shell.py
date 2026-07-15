"""Interactive CodeMind command shell.

Bare `automind` in an interactive terminal opens this shell. Users can type the
same subcommands without repeating the `automind` prefix, e.g.:

    ask 添加一个埋点
    resume task_123 codex
    status task_123
"""
from __future__ import annotations

import os
import signal
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

from orchestrator.tui.app import LOGO
from orchestrator.version import automind_version_label
from orchestrator.tui.style import BLUE, CYAN, GRAY, style
from orchestrator.tui.input import tui_input
from orchestrator.state import ensure_tui_chat_task, get_tui_chat_task_code, read_current_task

AGENT_NAMES = {"auto", "codex", "claude", "trae"}
TUI_FLAGS = {"--tui", "--detached", "--no-tui"}
COMMANDS = {
    "ask", "resume", "status", "trace", "process-check", "workflow-check", "version", "update",
    "workflow-contract", "completion-check", "record-check", "summary",
    "summary-refine", "improve-suggestions", "tui", "answer", "message",
    "event", "observe", "continue", "logs", "list", "reuse", "preloaded-check", "help",
    "shell", "plan", "scaffold", "context-pack", "smoke",
}
CURRENT_TASK_COMMANDS = {
    "resume", "status", "trace", "process-check", "workflow-check",
    "workflow-contract", "completion-check", "record-check", "summary", "tui",
    "logs", "answer", "message",
}


def _main_py() -> Path:
    return Path(__file__).resolve().parents[1] / "main.py"


def _normalize_shell_line(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped:
        return []
    if stripped.startswith("automind "):
        stripped = stripped[len("automind "):].strip()
    if stripped == "automind":
        stripped = "help"
    first, _, rest = stripped.partition(" ")
    if first == "ask":
        return _normalize_ask(rest)
    return _apply_current_task_default(shlex.split(stripped))


def _apply_current_task_default(argv: list[str]) -> list[str]:
    if not argv or argv[0] not in CURRENT_TASK_COMMANDS:
        return argv
    if len(argv) > 1 and not str(argv[1]).startswith("--"):
        return argv
    task_code = read_current_task()
    if not task_code:
        return argv
    return [argv[0], task_code, *argv[1:]]



def _natural_language_argv(text: str) -> list[str]:
    """Route bare natural language to current task session or the shared TUI chat session."""
    task_code = read_current_task()
    if task_code:
        return ["message", task_code, "--text", text, "--resume", "auto"]
    ensure_tui_chat_task()
    return ["message", get_tui_chat_task_code(), "--text", text, "--resume", "auto"]


def _normalize_ask(rest: str) -> list[str]:
    """Allow natural shell input: `ask add tracking` without quotes."""
    tokens = shlex.split(rest) if rest.strip() else []
    flags = [tok for tok in tokens if tok in TUI_FLAGS]
    non_flags = [tok for tok in tokens if tok not in TUI_FLAGS]
    agent = "auto"
    if "--agent" in non_flags:
        idx = non_flags.index("--agent")
        if idx + 1 < len(non_flags):
            agent = non_flags[idx + 1]
            del non_flags[idx:idx + 2]
    elif non_flags and non_flags[-1] in AGENT_NAMES:
        agent = non_flags[-1]
        non_flags = non_flags[:-1]
    requirement = " ".join(non_flags).strip()
    if not requirement:
        return ["ask"]
    return ["ask", requirement, agent, *flags]


def _new_process_tui_chat_code() -> str:
    return f"__tui_chat_{os.getpid()}_{uuid.uuid4().hex[:8]}"


def run_command_shell() -> int:
    tui_chat_code = os.environ.get("AUTOMIND_TUI_CHAT_TASK", "").strip() or _new_process_tui_chat_code()
    child_env = {**os.environ, "AUTOMIND_TUI_CHAT_TASK": tui_chat_code}
    print(style(LOGO, CYAN, bold=True))
    print(style(automind_version_label(), BLUE, bold=True))
    print("Type commands without the `codemind` prefix. Examples:")
    print("  ask 添加一个埋点")
    print("  resume <task-code> codex")
    print("  status [task-code]   # current task is used when omitted")
    print("  update               # update CodeMind runtime + skill/command integrations")
    print("  也可以直接输入自然语言；有 current task 时会转给该 task/session 的 coding agent，没有 current task 时会转给当前 TUI 进程自己的默认 coding-agent session")
    print("  exit")
    print(style("Tip: say \"全自动\" or \"full auto\" to skip all ask_user gates.", GRAY))
    print(style("  提示：输入「全自动」或「full auto」，后续各步骤不再 ask_user，直接跑完全流程。", GRAY))
    print(style("─" * 56, GRAY))
    while True:
        try:
            line = tui_input(style("codemind> ", BLUE, bold=True))
        except EOFError:
            print("")
            return 0
        except KeyboardInterrupt:
            print("\nUse `exit` to leave CodeMind shell.")
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in {"exit", "quit", ":q"}:
            return 0
        if stripped == "clear":
            print("\033[2J\033[H", end="")
            continue
        first_word = stripped.split(None, 1)[0]
        natural_language = (
            first_word not in COMMANDS
            and not stripped.startswith("codemind ")
            and not stripped.startswith("automind ")
        )
        if natural_language:
            argv = _natural_language_argv(stripped)
        else:
            try:
                argv = _normalize_shell_line(stripped)
            except ValueError as exc:
                print(f"parse error: {exc}", file=sys.stderr)
                continue
        if not argv:
            continue
        if argv[0] in {"exit", "quit", ":q"}:
            return 0
        proc = subprocess.Popen([sys.executable, str(_main_py()), *argv], env=child_env)
        try:
            return_code = proc.wait()
        except KeyboardInterrupt:
            print("\n[CodeMind shell] Ctrl+C received; interrupting current command and returning to shell.", file=sys.stderr)
            try:
                proc.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                return_code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("[CodeMind shell] command did not stop after SIGINT; terminating it.", file=sys.stderr)
                proc.terminate()
                try:
                    return_code = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    return_code = proc.wait()
            print("[CodeMind shell] If a CodeMind task was paused, resume it with `codemind resume <task-code> <agent>`.", file=sys.stderr)
        if return_code != 0:
            print(f"[CodeMind shell] command exited with code {return_code}", file=sys.stderr)
    return 0
