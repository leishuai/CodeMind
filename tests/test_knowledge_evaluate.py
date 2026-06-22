from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_knowledge_evaluate_reports_missing_raw_and_strict_exit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = Path.cwd()
    summary_dir = workspace / ".automind" / "summary"
    summary_dir.mkdir(parents=True)
    (summary_dir / "index.jsonl").write_text(json.dumps({
        "id": "missing-raw-fixture",
        "title": "Missing raw fixture",
        "value": "Evaluate should report missing raw.",
        "rawPath": ".automind/summary/raw/missing.md",
        "confidence": "high",
        "phaseApplicability": ["plan"],
        "surfaces": ["build"],
        "triggers": ["missing-raw-fixture"],
    }, ensure_ascii=False) + "\n")
    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)
    env["AUTOMIND_RUNTIME_ROOT"] = str(runtime)

    result = subprocess.run(
        [sys.executable, "orchestrator/main.py", "knowledge", "evaluate", "--json"],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert any(issue["code"] == "missing_raw" for issue in report["issues"])

    strict = subprocess.run(
        [sys.executable, "orchestrator/main.py", "knowledge", "evaluate", "--strict"],
        text=True,
        capture_output=True,
        env=env,
    )
    assert strict.returncode == 1
    assert "missing_raw" in strict.stdout


def test_knowledge_evaluate_ok_for_valid_index_and_raw(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = tmp_path / "runtime"
    summary_dir = workspace / ".automind" / "summary"
    raw = summary_dir / "raw" / "ios-build" / "valid.md"
    raw.parent.mkdir(parents=True)
    (runtime / "summaries").mkdir(parents=True)
    raw.write_text("# Valid Raw\n\nThis is a useful evidence-backed raw knowledge note for future tasks.\n")
    (summary_dir / "index.jsonl").write_text(json.dumps({
        "id": "valid-fixture",
        "title": "Valid fixture",
        "value": "Evaluate should pass valid raw/index pair.",
        "rawPath": ".automind/summary/raw/ios-build/valid.md",
        "confidence": "medium",
        "phaseApplicability": ["plan", "generator"],
        "surfaces": ["ios", "build"],
        "triggers": ["xcodebuild", "valid-fixture"],
    }, ensure_ascii=False) + "\n")
    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)
    env["AUTOMIND_RUNTIME_ROOT"] = str(runtime)
    result = subprocess.run(
        [sys.executable, "orchestrator/main.py", "knowledge", "evaluate", "--json"],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["severityCounts"]["error"] == 0
