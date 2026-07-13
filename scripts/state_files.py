"""Runtime-state helpers for standalone CodeAutonomy scripts.

Standalone scripts run with only ``scripts/`` on sys.path, so they cannot always
import ``orchestrator.state`` safely. Keep this tiny helper in the script layer;
``runtime-state.json`` is the single runtime/resume projection.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


RUNTIME_STATE_NAME = "runtime-state.json"


def runtime_state_path(task_dir: Path) -> Path:
    return task_dir / RUNTIME_STATE_NAME


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def read_runtime_state(task_dir: Path) -> dict[str, Any]:
    return _read_json(runtime_state_path(task_dir)) or {}


def write_runtime_state(task_dir: Path, state: dict[str, Any]) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    runtime_state_path(task_dir).write_text(rendered)
