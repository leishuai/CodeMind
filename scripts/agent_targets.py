#!/usr/bin/env python3
"""Shared user-level agent integration targets for AutoMind exports."""
from __future__ import annotations

import pathlib
from typing import Literal

AgentTarget = Literal["none", "all", "auto", "claude", "codex", "trae", "trae-cn"]

AGENT_ORDER_ALL = ["claude", "codex", "trae", "trae-cn"]
AGENT_ORDER_AUTO = ["claude", "codex", "trae", "trae-cn"]


def _skill_specs(install_name: str) -> dict[str, dict[str, pathlib.Path]]:
    home = pathlib.Path.home()
    return {
        "claude": {"marker": home / ".claude", "path": home / ".claude" / "skills" / install_name},
        "codex": {"marker": home / ".codex", "path": home / ".codex" / "skills" / install_name},
        "trae": {"marker": home / ".trae", "path": home / ".trae" / "skills" / install_name},
        "trae-cn": {"marker": home / ".trae-cn", "path": home / ".trae-cn" / "skills" / install_name},
    }


def display_path(path: pathlib.Path) -> str:
    """Return a user-safe display path without leaking the absolute home prefix."""
    try:
        return "~/" + str(path.expanduser().resolve().relative_to(pathlib.Path.home().resolve()))
    except Exception:
        return str(path)


def _agent_names(agent: str) -> list[str]:
    if agent == "none":
        return []
    if agent == "all":
        return AGENT_ORDER_ALL
    if agent == "auto":
        return AGENT_ORDER_AUTO
    return [agent]


def skill_target_entries(agent: str, install_name: str = "automind") -> list[dict[str, object]]:
    """Return user-level skill target entries with detection status.

    Default install policy is conservative: never create an agent root directory.
    If the agent root/marker exists, installing may create the child `skills/`
    directory and overwrite AutoMind's skill folder there.
    """
    specs = _skill_specs(install_name)
    entries: list[dict[str, object]] = []
    for current in _agent_names(agent):
        spec = specs.get(current)
        if not spec:
            continue
        marker = spec["marker"]
        path = spec["path"]
        available = marker.exists()
        entries.append({
            "agent": current,
            "kind": f"{current}:user",
            "marker": marker,
            "path": path,
            "available": available,
            "reason": "detected" if available else f"agent root not found: {display_path(marker)}",
        })
    return entries


def skill_targets(agent: str, install_name: str = "automind") -> list[tuple[str, pathlib.Path]]:
    """Return installable user-level skill targets.

    `auto` is the main install selector: install into every detected supported
    agent. `all` is kept as a compatibility alias over the same conservative
    policy; it does not create missing agent roots. Explicit agents also skip
    when their root is not present.
    """
    targets: list[tuple[str, pathlib.Path]] = []
    for entry in skill_target_entries(agent, install_name):
        if not entry.get("available"):
            continue
        targets.append((str(entry["kind"]), entry["path"]))
    return targets


def command_targets(agent: str, command_name: str = "automind") -> list[tuple[str, pathlib.Path]]:
    """Return user-level slash-command targets for an install selector."""
    home = pathlib.Path.home()
    specs = {
        "claude": {"marker": home / ".claude", "path": home / ".claude" / "commands" / f"{command_name}.md"},
        "codex": {"marker": home / ".codex", "path": home / ".codex" / "commands" / f"{command_name}.md"},
        "trae": {"marker": home / ".trae", "path": home / ".trae" / "commands" / f"{command_name}.md"},
        "trae-cn": {"marker": home / ".trae-cn", "path": home / ".trae-cn" / "commands" / f"{command_name}.md"},
    }
    targets: list[tuple[str, pathlib.Path]] = []
    for current in _agent_names(agent):
        spec = specs.get(current)
        if not spec:
            continue
        if agent == "auto" and not spec["marker"].exists():
            continue
        targets.append((f"{current}:user", spec["path"]))
    return targets
