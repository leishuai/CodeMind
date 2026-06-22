from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _write_flow(task: Path, command: str | None = None) -> None:
    data = {
        "platform": "web",
        "name": "Web login flow",
        "uiUnblock": {
            "enabled": True,
            "policy": "safe_non_destructive_only",
            "maxAttempts": 2,
        },
        "testIntent": {
            "goal": "Verify login navigation",
            "sources": ["TC-F01"],
            "acceptanceCriteria": ["AC-001"],
            "steps": [
                {"type": "navigate", "name": "Open login", "url": "http://localhost:3000/login", "critical": True},
                {"type": "click", "name": "Submit login", "selector": {"role": "button", "name": "Login"}, "critical": True},
                {"type": "assert_page", "name": "Dashboard page", "pageSignature": {"name": "dashboard", "required": [{"text": "Dashboard"}]}},
                {"type": "assert_text", "name": "Dashboard visible", "text": "Dashboard"},
            ],
        },
    }
    if command:
        data["command"] = command
    (task / "probe-flow.web.json").write_text(json.dumps(data))


def test_web_probe_flow_dry_run_writes_client_ui_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = workspace / ".automind" / "tasks" / "web_probe_flow_fixture"
    task.mkdir(parents=True)
    _write_flow(task)
    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)

    result = subprocess.run(
        [sys.executable, "scripts/web_probe_flow_runner.py", "web_probe_flow_fixture", "1", "--dry-run"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["result"] == "pass"
    assert any(item.get("note") == "ui-evidence-check" for item in data["evidence"])

    iter_dir = task / "logs" / "iter-1"
    lines = [json.loads(line) for line in (iter_dir / "action-trace.jsonl").read_text().splitlines() if line.strip()]
    assert [line["platform"] for line in lines] == ["web", "web", "web"]
    assert lines[0]["type"] == "overlay_unblock"
    assert lines[0]["uiUnblock"]["result"] == "planned"
    assert lines[1]["target"] == "url=http://localhost:3000/login"
    assert lines[2]["target"] == "role=button"

    summary = json.loads((iter_dir / "web-probe-flow-summary.json").read_text())
    assert summary["criticalActions"] == 3
    assert summary["actionTrace"] == "logs/iter-1/action-trace.jsonl"
    assert summary["uiUnblock"]["result"] == "planned"
    assert any(item["path"] == "logs/iter-1/action-trace.jsonl" for item in summary["evidenceIndex"])
    assert any(item["path"] == "logs/iter-1/action-trace.jsonl" for item in data["evidenceIndex"])

    validation = (task / "Validation.md").read_text()
    assert "## Iteration 1 - Web Probe Flow" in validation
    assert "### Client UI action evidence" in validation
    assert "Open login" in validation
    assert "Submit login" in validation
    assert "action-trace.jsonl" in validation
    assert "## Iteration 1 - UI Evidence Check" in validation
    report = json.loads((iter_dir / "ui-evidence-check.json").read_text())
    assert report["result"] == "pass"


def test_web_probe_flow_project_command_pass_marks_actions_pass(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = workspace / ".automind" / "tasks" / "web_probe_flow_command_fixture"
    task.mkdir(parents=True)
    _write_flow(task, command="printf web-ok")
    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)

    result = subprocess.run(
        [sys.executable, "scripts/web_probe_flow_runner.py", "web_probe_flow_command_fixture", "1"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["result"] == "pass"

    iter_dir = task / "logs" / "iter-1"
    assert "web-ok" in (iter_dir / "web-probe-flow.log").read_text()
    assert any(item["path"] == "logs/iter-1/web-probe-flow.log" for item in data["evidenceIndex"])
    lines = [json.loads(line) for line in (iter_dir / "action-trace.jsonl").read_text().splitlines() if line.strip()]
    assert lines[0]["type"] == "overlay_unblock"
    assert lines[0]["ok"] is None
    assert all(line["ok"] is True for line in lines[1:])
    validation = (task / "Validation.md").read_text()
    assert "PASS" in validation
    assert (iter_dir / "ui-evidence-check.json").exists()


def test_automind_web_probe_flow_wrapper_dry_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = workspace / ".automind" / "tasks" / "web_probe_flow_cli_fixture"
    task.mkdir(parents=True)
    _write_flow(task)
    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)
    subprocess.run(
        ["./automind.sh", "web-probe-flow", "web_probe_flow_cli_fixture", "1", "--dry-run"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert (task / "logs" / "iter-1" / "web-probe-flow-summary.json").exists()


def test_web_probe_flow_retries_project_command_then_passes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = workspace / ".automind" / "tasks" / "web_probe_flow_retry_fixture"
    task.mkdir(parents=True)
    marker = workspace / "retry-marker.txt"
    command = (
        f"if [ ! -f {marker} ]; then "
        f"echo first-fail; touch {marker}; exit 7; "
        "else echo second-pass; exit 0; fi"
    )
    _write_flow(task, command=command)
    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)

    result = subprocess.run(
        [sys.executable, "scripts/web_probe_flow_runner.py", "web_probe_flow_retry_fixture", "1", "--retries", "1"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["result"] == "pass"
    assert any(item.get("note") == "web-probe-flow-attempt-1" for item in data["evidence"])
    assert any(item.get("note") == "web-probe-flow-attempt-2" for item in data["evidence"])

    iter_dir = task / "logs" / "iter-1"
    summary = json.loads((iter_dir / "web-probe-flow-summary.json").read_text())
    assert summary["retries"] == 1
    assert [item["exitCode"] for item in summary["attempts"]] == [7, 0]
    assert "first-fail" in (iter_dir / "web-probe-flow-attempt-1.log").read_text()
    assert "second-pass" in (iter_dir / "web-probe-flow-attempt-2.log").read_text()
    assert json.loads((iter_dir / "ui-evidence-check.json").read_text())["result"] == "pass"
