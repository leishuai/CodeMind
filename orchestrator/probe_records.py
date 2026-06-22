"""Small probe artifact helpers shared by CLI wrappers."""
from __future__ import annotations

import json
from pathlib import Path

from orchestrator.state import ensure_dir


def write_json_file(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")


def init_task_artifacts(task_dir: Path, title: str, require_body: str, plan_body: str):
    ensure_dir(task_dir / "logs" / "iter-1")
    (task_dir / "Requirements.md").write_text(f"# Requirements\n\n{require_body}\n")
    (task_dir / "Plan.md").write_text(f"# Plan\n\n{plan_body}\n")
    (task_dir / "Validation.md").write_text("# Validation\n")


def write_probe_record(task_dir: Path, iteration: int, title: str, environment: str, commands: list[str], evidence_path: str, log_path: str, result: str, summary: str, category: str, reusable: str, avoid: str):
    iter_dir = task_dir / "logs" / f"iter-{iteration}"
    ensure_dir(iter_dir)
    (iter_dir / "env.json").write_text(json.dumps({"environment": environment}, ensure_ascii=False, indent=2) + "\n")
    (iter_dir / "commands.md").write_text("# Commands\n\n" + "\n".join(f"- `{cmd}`" for cmd in commands) + "\n")
    validation = task_dir / "Validation.md"
    current = validation.read_text() if validation.exists() else "# Validation\n"
    validation.write_text(
        current.rstrip()
        + f"\n\n## Iteration {iteration} - {title}\n"
        + f"- Environment: {environment}\n"
        + f"- Commands: {' '.join(commands)}\n"
        + f"- Result: {result}\n"
        + f"- Failure category: {category}\n"
        + f"- Evidence: {evidence_path}; {log_path}\n"
        + f"- Summary: {summary}\n"
        + f"- Reusable findings: {reusable}\n"
        + f"- Avoid repeating: {avoid}\n"
        + "\n"
    )
