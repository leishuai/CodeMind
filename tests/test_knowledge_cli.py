from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_knowledge_cli_search_shows_index_not_raw(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = Path.cwd()
    task_code = "knowledge_cli_fixture"
    task_dir = workspace / ".automind/tasks" / task_code
    raw_path = workspace / ".automind/summary/raw/ios-build/knowledge-cli-fixture.md"
    index_path = workspace / ".automind/summary/index.jsonl"
    task_dir.mkdir(parents=True)
    raw_path.parent.mkdir(parents=True)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    raw_marker = "RAW_SECRET_DETAIL_SHOULD_NOT_APPEAR_IN_SEARCH"
    raw_path.write_text(f"# Raw fixture\n\n{raw_marker}\n")
    record = {
        "id": "knowledge-cli-fixture",
        "title": "Knowledge CLI fixture",
        "rawPath": ".automind/summary/raw/ios-build/knowledge-cli-fixture.md",
        "description": "Fixture for deterministic knowledge search.",
        "value": "Search should show index summary and raw path only.",
        "taskTypes": ["ios"],
        "projects": [workspace.name],
        "surfaces": ["ios", "build"],
        "phaseApplicability": ["plan"],
        "triggers": ["knowledge_cli_fixture", "xcodebuild"],
        "confidence": "high",
        "successfulPaths": ["Use the fixture successful path."],
    }
    index_path.write_text(json.dumps(record, ensure_ascii=False) + "\n")

    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": task_code,
        "userInput": "knowledge_cli_fixture xcodebuild plan",
        "taskType": "ios",
    }))
    (task_dir / "Requirements.md").write_text("# Requirements\n\nNeed xcodebuild plan fixture.\n")

    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)
    env["AUTOMIND_RUNTIME_ROOT"] = str(runtime)

    search = subprocess.run(
        [sys.executable, "orchestrator/main.py", "knowledge", "search", task_code, "plan"],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert "knowledge-cli-fixture" in search.stdout
    assert "raw:" in search.stdout
    assert raw_marker not in search.stdout

    show_raw = subprocess.run(
        [sys.executable, "orchestrator/main.py", "knowledge", "show", "knowledge-cli-fixture", "--raw"],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert raw_marker in show_raw.stdout
