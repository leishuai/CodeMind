from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_ios_action_plan_materializes_screenshot_attachments(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = workspace / ".automind" / "tasks" / "ios_action_plan_attachment_fixture"
    task.mkdir(parents=True)
    plan = task / "action-plan.ios.json"
    plan.write_text(json.dumps({
        "name": "Attachment smoke",
        "steps": [
            {"type": "tap", "name": "Tap primary", "selector": {"accessibilityId": "primary"}},
            {"type": "assert_exists", "name": "Result visible", "selector": {"text": "Done"}, "critical": True},
        ],
    }))
    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)
    subprocess.run(
        [sys.executable, "scripts/ios_action_plan.py", "ios_action_plan_attachment_fixture", "action-plan.ios.json", "--iteration", "1"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    swift = (task / "logs" / "iter-1" / "GeneratedActionPlanTests.swift").read_text()
    assert "func attachScreenshot(_ name: String)" in swift
    assert "XCTAttachment(screenshot: XCUIScreen.main.screenshot())" in swift
    assert "attachment.lifetime = .keepAlways" in swift
    assert 'attachScreenshot("after-01-tap-Tap-primary")' in swift
    assert 'attachScreenshot("after-02-assert_exists-Result-visible")' in swift
    summary = json.loads((task / "logs" / "iter-1" / "ios-action-plan-summary.json").read_text())
    assert summary["screenshotAttachments"] is True
    validation = (task / "Validation.md").read_text()
    assert "Screenshot attachments" in validation


def test_ios_probe_flow_materialize_screenshot_attachments(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    task = workspace / ".automind" / "tasks" / "ios_probe_flow_attachment_fixture"
    task.mkdir(parents=True)
    flow = task / "probe-flow.ios.json"
    flow.write_text(json.dumps({
        "name": "Probe attachment smoke",
        "testIntent": {
            "goal": "Verify primary flow",
            "sources": ["TC-F01"],
            "steps": [
                {"type": "input", "name": "Enter query", "selector": {"accessibilityId": "query"}, "text": "hello"},
                {"type": "scroll", "name": "Scroll results", "direction": "up", "count": 1},
            ],
        },
    }))
    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)
    subprocess.run(
        [sys.executable, "scripts/ios_probe_flow_materialize.py", "ios_probe_flow_attachment_fixture", "1"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    swift = (task / "logs" / "iter-1" / "GeneratedProbeFlowIntentTests.swift").read_text()
    assert "func attachScreenshot(_ name: String)" in swift
    assert 'attachScreenshot("after-01-input-Enter-query")' in swift
    assert 'attachScreenshot("after-02-scroll-Scroll-results")' in swift
    summary = json.loads((task / "logs" / "iter-1" / "ios-probe-flow-materialize-summary.json").read_text())
    assert summary["screenshotAttachments"] is True


def test_ios_materialize_adds_per_tc_default_screenshot_for_assert_only_tc(tmp_path: Path) -> None:
    # A TC whose only step is an assertion would not auto-screenshot in
    # materialize (assert_* is not an interaction type). The per-TC default
    # should still mark it so Report.html can show a per-TC screenshot.
    workspace = tmp_path / "workspace"
    task = workspace / ".automind" / "tasks" / "ios_per_tc_default_fixture"
    task.mkdir(parents=True)
    flow = task / "probe-flow.ios.json"
    flow.write_text(json.dumps({
        "name": "Per-TC default smoke",
        "testIntent": {
            "goal": "Verify assertion-only TC still screenshots",
            "sources": ["TC-A01"],
            "steps": [
                {"type": "assert_exists", "name": "Banner shown", "selector": {"text": "Welcome"}, "tc": "TC-A01"},
            ],
        },
    }))
    env = os.environ.copy()
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace)
    subprocess.run(
        [sys.executable, "scripts/ios_probe_flow_materialize.py", "ios_per_tc_default_fixture", "1"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    swift = (task / "logs" / "iter-1" / "GeneratedProbeFlowIntentTests.swift").read_text()
    assert 'attachScreenshot("after-01-assert_exists-Banner-shown")' in swift
