from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_ios_probe_flow_dry_run_writes_action_trace_and_validation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    tasks = workspace / ".automind" / "tasks"
    task = tasks / "ios_action_trace_fixture"
    task.mkdir(parents=True)
    flow = task / "probe-flow.ios.json"
    flow.write_text(json.dumps({
        "adapter": "xcuitest",
        "app": {
            "projectPath": "/tmp/Demo.xcodeproj",
            "scheme": "Demo",
            "deviceId": "FAKE-DEVICE",
            "bundleId": "com.example.demo",
        },
        "testIntent": {
            "goal": "Verify playback button can be tapped",
            "sources": ["TC-F01"],
            "acceptanceCriteria": ["AC-001"],
            "steps": [
                {
                    "type": "tap",
                    "name": "Tap play button",
                    "selector": {"accessibilityIdentifier": "play_button"},
                    "critical": True,
                },
                {
                    "type": "assert_page",
                    "name": "Player page",
                    "pageSignature": {"name": "player", "required": [{"accessibilityIdentifier": "pause_button"}]},
                },
                {
                    "type": "assert_exists",
                    "name": "Pause button visible",
                    "selector": {"accessibilityIdentifier": "pause_button"},
                },
            ],
        },
    }))
    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)
    result = subprocess.run(
        [sys.executable, "scripts/ios_probe_flow_runner.py", "ios_action_trace_fixture", "1", "--dry-run"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["result"] == "pass"
    assert any(item.get("note") == "ui-evidence-check" for item in data["evidence"])

    iter_dir = task / "logs" / "iter-1"
    trace_path = iter_dir / "action-trace.jsonl"
    assert trace_path.exists()
    lines = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["platform"] == "ios"
    assert lines[0]["backend"] == "xcuitest"
    assert lines[0]["target"] == "accessibilityIdentifier=play_button"

    summary = json.loads((iter_dir / "ios-probe-flow-summary.json").read_text())
    assert summary["criticalActions"] == 1
    assert summary["actionTrace"] == "logs/iter-1/action-trace.jsonl"
    assert any(item["path"] == "logs/iter-1/action-trace.jsonl" for item in summary["evidenceIndex"])
    assert any(item["path"] == "logs/iter-1/action-trace.jsonl" for item in data["evidenceIndex"])

    validation = (task / "Validation.md").read_text()
    assert "## Iteration 1 - iOS Probe Flow" in validation
    assert "### Client UI action evidence" in validation
    assert "Tap play button" in validation
    assert "accessibilityIdentifier=play_button" in validation
    assert "action-trace.jsonl" in validation
    assert "## Iteration 1 - UI Evidence Check" in validation
    report = json.loads((iter_dir / "ui-evidence-check.json").read_text())
    assert report["result"] == "pass"


def test_ios_probe_flow_dry_run_blocks_runtime_proof_required(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = workspace / ".automind" / "tasks" / "ios_runtime_required_fixture"
    task.mkdir(parents=True)
    (task / "runtime-state.json").write_text(json.dumps({
        "planner": {
            "preImplementationReview": {
                "decisionBundle": {"runtimeProofRequired": "yes"}
            }
        }
    }))
    (task / "probe-flow.ios.json").write_text(json.dumps({
        "adapter": "xcuitest",
        "app": {"projectPath": "/tmp/Demo.xcodeproj", "scheme": "Demo", "deviceId": "FAKE"},
        "testIntent": {
            "goal": "Verify real runtime",
            "sources": ["TC-F01"],
            "acceptanceCriteria": ["AC-001"],
            "steps": [{"type": "tap", "name": "Tap", "selector": {"accessibilityIdentifier": "go"}, "critical": True}],
        },
    }))
    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)
    result = subprocess.run(
        [sys.executable, "scripts/ios_probe_flow_runner.py", "ios_runtime_required_fixture", "1", "--dry-run"],
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["result"] == "blocked"
    assert data["nextAction"] == "retry_generator"
    assert data["failedChecks"][0]["name"] == "ios_probe_flow_dry_run_runtime_proof"
    summary = json.loads((task / "logs" / "iter-1" / "ios-probe-flow-summary.json").read_text())
    assert summary["dryRun"] is True
    assert summary["runtimeProofRequired"] is True
