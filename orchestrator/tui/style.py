"""ANSI style helpers for the dependency-light CodeAutonomy TUI."""
from __future__ import annotations

import os

NO_COLOR = bool(os.environ.get("NO_COLOR"))

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
BLUE = "\033[34m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
GRAY = "\033[90m"


def style(text: object, color: str = "", *, bold: bool = False, dim: bool = False) -> str:
    raw = str(text)
    if NO_COLOR:
        return raw
    prefix = ""
    if bold:
        prefix += BOLD
    if dim:
        prefix += DIM
    prefix += color
    return f"{prefix}{raw}{RESET}" if prefix else raw


def status_color(status: object) -> str:
    value = str(status or "").lower()
    if value in {"pass", "passed", "finished", "done", "ok", "ready", "planned"}:
        return GREEN
    if value in {"warn", "warning", "blocked", "human_input_pending", "ask_user", "replan", "paused_by_user"}:
        return YELLOW
    if value in {"fail", "failed", "error", "timeout", "aborted"}:
        return RED
    return CYAN


def level_color(level: object) -> str:
    value = str(level or "info").lower()
    if value in {"error", "failed", "fail"}:
        return RED
    if value in {"warn", "warning"}:
        return YELLOW
    if value in {"success", "pass"}:
        return GREEN
    return GRAY
