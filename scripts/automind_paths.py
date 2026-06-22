"""Shared path resolution for AutoMind helper scripts.

Runtime root is the AutoMind installation/checkout containing scripts and
requirements. Workspace root is the caller's target project; task artifacts and
project-local helper virtualenvs live under it.
"""
from __future__ import annotations

import os
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[1]


def resolve_workspace_root() -> Path:
    raw = os.environ.get("AUTOMIND_WORKSPACE_ROOT") or os.environ.get("AUTOMIND_PROJECT_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd().resolve()


WORKSPACE_ROOT = resolve_workspace_root()
TASKS_DIR = WORKSPACE_ROOT / ".automind" / "tasks"
SUMMARY_DIR = WORKSPACE_ROOT / ".automind" / "summary"
CHECKPOINTS_DIR = WORKSPACE_ROOT / ".automind" / "checkpoints"
ANDROID_TOOLS_PY = WORKSPACE_ROOT / ".venv-android-tools" / "bin" / "python"
RUNTIME_ANDROID_TOOLS_PY = RUNTIME_ROOT / ".venv-android-tools" / "bin" / "python"
IOS_TOOLS_PY = WORKSPACE_ROOT / ".venv-ios-tools" / "bin" / "python"
VISUAL_TOOLS_PY = WORKSPACE_ROOT / ".venv-visual-tools" / "bin" / "python"

# Propagate the resolved roots to child processes even if they run with cwd set
# to the AutoMind runtime checkout.
os.environ.setdefault("AUTOMIND_RUNTIME_ROOT", str(RUNTIME_ROOT))
os.environ.setdefault("AUTOMIND_WORKSPACE_ROOT", str(WORKSPACE_ROOT))


def workspace_path(path: str | Path) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (WORKSPACE_ROOT / p).resolve()


def runtime_path(path: str | Path) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (RUNTIME_ROOT / p).resolve()


def rel_to_workspace(path: str | Path) -> str:
    p = Path(path).resolve()
    try:
        return str(p.relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)
