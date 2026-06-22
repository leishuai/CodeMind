from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run(task_code: str, iteration: int, workspace: Path) -> dict:
    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)
    result = subprocess.run(
        [sys.executable, "scripts/ui_evidence_check.py", task_code, str(iteration), "--json"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    return json.loads(result.stdout)


def test_ui_evidence_check_android_passes_with_screenshot_and_hierarchy(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = workspace / ".automind" / "tasks" / "android_ui_evidence"
    probe = task / "logs" / "iter-1" / "probe-flow"
    probe.mkdir(parents=True)
    (task / "Validation.md").write_text("# Validation\n")
    (task / "evaluation.json").write_text(json.dumps({
        "iteration": 1,
        "result": "pass",
        "evidence": [{"type": "other", "note": "ui-action-trace", "path": "logs/iter-1/probe-flow/action-trace.jsonl"}],
    }))
    (probe / "action-01-after.png").write_bytes(b"png")
    (probe / "action-01-after-hierarchy.xml").write_text("<hierarchy />")
    (probe / "probe-flow-summary.json").write_text("{}")
    (probe / "action-trace.jsonl").write_text(json.dumps({
        "stepIndex": 1,
        "type": "tap",
        "critical": True,
        "evidenceAfter": {
            "screenshot": "logs/iter-1/probe-flow/action-01-after.png",
            "hierarchy": "logs/iter-1/probe-flow/action-01-after-hierarchy.xml",
        },
    }) + "\n")

    data = _run("android_ui_evidence", 1, workspace)
    assert data["result"] == "pass"
    assert data["platform"] == "android"
    assert (task / "logs" / "iter-1" / "ui-evidence-check.json").exists()
    assert "UI Evidence Check" in (task / "Validation.md").read_text()


def test_ui_evidence_check_ios_requires_xcresult_and_log(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = workspace / ".automind" / "tasks" / "ios_ui_evidence"
    log_dir = task / "logs" / "iter-2"
    (log_dir / "TestResults.xcresult").mkdir(parents=True)
    (log_dir / "xcodebuild-ui-test.log").write_text("Running tests...\nTest Suite 'All tests' passed")
    (log_dir / "test-summary.txt").write_text("passed")
    (task / "Validation.md").write_text("# Validation\n")
    (task / "evaluation.json").write_text(json.dumps({
        "iteration": 2,
        "result": "pass",
        "evidence": [{"type": "other", "note": "xcresult", "path": "logs/iter-2/TestResults.xcresult"}],
    }))

    data = _run("ios_ui_evidence", 2, workspace)
    assert data["result"] == "pass"
    assert data["platform"] == "ios"
    names = {c["name"] for c in data["checks"] if c["ok"]}
    assert "ios_xcresult_present" in names
    assert "ios_xctest_ran" in names


def test_ui_evidence_check_fails_when_evaluation_missing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = workspace / ".automind" / "tasks" / "missing_evidence"
    (task / "logs" / "iter-1").mkdir(parents=True)
    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)
    result = subprocess.run(
        [sys.executable, "scripts/ui_evidence_check.py", "missing_evidence", "1", "--json"],
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["result"] == "fail"
    assert any(c["name"] == "evaluation_json_present" and not c["ok"] for c in data["checks"])
