"""Regression tests for the real-device-first / runtime-proof protocol gates.

Covers:
- workflow.py runtime-proof gate (decisionBundle.runtimeProofRequired=yes
  forces a runtime-capable verificationTarget or a signed
  runtimeDowngradeApproval; otherwise an issue, not a warning, is emitted).
- completion.py runtime-proof gate (a finish requires at least one
  runtime/device-level required TC pass, unless a signed
  runtimeDowngradeApproval is present).
- main.py hard-stop phrasing detection (`真机不可用`, `no real device`, etc.
  must trigger the pre-implementation review gate even when
  verificationTarget is provided).
"""
from __future__ import annotations

import json
from pathlib import Path

from orchestrator.completion import build_completion_report
from orchestrator.main import (
    is_client_development_or_verification_task,
    mentions_hardstop_device_unavailable_phrasing,
    mobile_task_needs_verification_target_review,
)
from orchestrator.workflow import check_workflow_consistency


def _write_runtime_task(
    task_dir: Path,
    *,
    runtime_proof_required: str = "yes",
    verification_target: str = "real_device",
    downgrade: dict | None = None,
    tc_required: bool = True,
    tc_runtime_level: str = "runtime",
    tc_result: str = "pass",
    with_screenshot: bool = True,
) -> None:
    """Write a minimal but valid client/app task fixture for completion tests."""
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "Brainstorm.md").write_text("# Brainstorm\n\nUser Decision Bundle\n")
    (task_dir / "Requirements.md").write_text(
        "# Requirements\n\n"
        "## Requirements with inline Acceptance Criteria\n\n"
        "### R01 — runtime\n"
        "- **AC-001**: runtime evidence required\n"
        "  - Verification method: TC-F01\n"
    )
    required_label = "yes" if tc_required else "no"
    (task_dir / "TestCases.md").write_text(
        "# TestCases\n\n"
        "| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeAutonomy command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n"
        "|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n"
        f"| TC-F01 | R01 / AC-001 | Functional | {tc_runtime_level} | preflight ready | command: pytest | run command | logs/iter-1/evidence.txt exists and pass | none | {required_label} |\n"
    )
    (task_dir / "Plan.md").write_text(
        "# Plan\n\n## Implementation Checklist\n\n| ID | Status |\n|----|--------|\n| T01 | done |\n\n"
        "## Verification Checklist\n\n| ID | Status | Evidence |\n|----|--------|----------|\n"
        "| TC-F01 | pass | logs/iter-1/evidence.txt |\n"
    )
    (task_dir / "Delivery.md").write_text("# Delivery\n\nImplemented R01.\n")
    (task_dir / "Validation.md").write_text(
        "# Validation\n\n## Status\n<!-- Finished -->\n\n## Environment\nok\n\n"
        "## Commands\npytest\n\n## Evidence\nlogs/iter-1/evidence.txt\n\n"
        "## Reusable findings\n- runtime ok\n\n## Avoid repeating\n- none\n"
    )
    decision_bundle: dict = {
        "verificationTarget": verification_target,
        "runtimeProofRequired": runtime_proof_required,
        "runtimeDowngradeApproval": downgrade,
        "taskType": "ios",
        "confirmedAt": "2026-06-01T00:00:00Z",
        "confirmedBy": "user",
    }
    state = {
        "taskId": task_dir.name,
        "status": "finished",
        "iteration": 1,
        "planner": {
            "preImplementationReview": {
                "decision": "auto_proceed",
                "decisionBundle": decision_bundle,
            }
        },
    }
    (task_dir / "runtime-state.json").write_text(json.dumps(state))
    iter_dir = task_dir / "logs" / "iter-1"
    iter_dir.mkdir(parents=True, exist_ok=True)
    (iter_dir / "evidence.txt").write_text("passed")
    (iter_dir / "env.json").write_text("{}")
    (iter_dir / "commands.md").write_text("```bash\npytest\n```\n")
    (iter_dir / "evaluator.log").write_text("pass")
    evidence_refs = ["logs/iter-1/evidence.txt"]
    if with_screenshot:
        (iter_dir / "tc-f01-after.png").write_bytes(b"fake-png")
        evidence_refs.append("logs/iter-1/tc-f01-after.png")
    (task_dir / "evaluation.json").write_text(json.dumps({
        "iteration": 1,
        "result": "pass",
        "summary": "ok",
        "failedChecks": [],
        "nextAction": "finish",
        "testResults": [{
            "testCaseId": "TC-F01",
            "result": tc_result,
            "acceptanceCriteria": ["AC-001"],
            "evidence": list(evidence_refs),
            "evidenceAssessment": {
                "verdict": "proved",
                "machineAnchor": "logs/iter-1/evidence.txt",
                "hardMetrics": [{"name": "exit_code", "passed": True}],
            },
        }],
        "evidence": list(evidence_refs),
    }))


# ---------------------------------------------------------------------------
# completion.py gate
# ---------------------------------------------------------------------------


def test_completion_passes_when_runtime_tc_passes(tmp_path: Path) -> None:
    """Baseline: runtimeProofRequired=yes + a runtime TC pass = report pass."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="runtime", tc_result="pass")
    report, _ = build_completion_report(task_dir)
    assert report["result"] == "pass", report
    coverage = report["coverage"]
    assert coverage["runtimeProofRequired"] == "yes"
    assert coverage["runtimeProofPassed"] == ["TC-F01"]
    assert report["completion"]["runtimeProofSatisfied"] is True


def test_completion_passes_with_compound_device_runtime_level(tmp_path: Path) -> None:
    """Regression for app_0614231227_354e: a compound runtimeLevel cell such as
    "device/runtime" must be normalized to a runtime/device level so the
    completion gate does not falsely report "no required TC with runtimeLevel
    in {runtime, device}"."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="device/runtime", tc_result="pass")
    report, _ = build_completion_report(task_dir)
    assert report["result"] == "pass", report
    assert report["coverage"]["runtimeProofPassed"] == ["TC-F01"]
    assert report["completion"]["runtimeProofSatisfied"] is True
    assert not any(
        "declares no required TC with runtimeLevel" in issue for issue in report["issues"]
    ), report["issues"]


def test_completion_passes_with_compound_static_runtime_level(tmp_path: Path) -> None:
    """A "static/runtime" compound cell still counts as a runtime-capable TC."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="static/runtime", tc_result="pass")
    report, _ = build_completion_report(task_dir)
    assert report["result"] == "pass", report
    assert report["coverage"]["runtimeProofPassed"] == ["TC-F01"]
    assert report["completion"]["runtimeProofSatisfied"] is True


def test_completion_fails_when_proved_metric_has_no_evidence_anchor(tmp_path: Path) -> None:
    """A screenshot/log is precise proof only when the proved metric points at an artifact."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="runtime", tc_result="pass")
    evaluation_path = task_dir / "evaluation.json"
    data = json.loads(evaluation_path.read_text())
    assessment = data["testResults"][0]["evidenceAssessment"]
    assessment.pop("machineAnchor", None)
    assessment["hardMetrics"] = [{"name": "ui_state_visible", "passed": True}]
    evaluation_path.write_text(json.dumps(data))

    report, _ = build_completion_report(task_dir)

    assert report["result"] == "fail", report
    assert any("proved metric lacks existing evidence artifact" in issue for issue in report["issues"])


def test_completion_fails_without_runtime_tc_pass_or_downgrade(tmp_path: Path) -> None:
    """When TC is static-level and there is no approved downgrade, completion must fail."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="static", tc_result="pass")
    report, _ = build_completion_report(task_dir)
    assert report["result"] == "fail", report
    runtime_issues = [issue for issue in report["issues"] if "runtimeProofRequired=yes" in issue]
    assert runtime_issues, report["issues"]
    assert report["completion"]["runtimeProofSatisfied"] is False


def test_completion_passes_with_approved_downgrade_warning(tmp_path: Path) -> None:
    """An approved runtimeDowngradeApproval lets a non-runtime task finish but warns."""
    task_dir = tmp_path / "task"
    _write_runtime_task(
        task_dir,
        tc_runtime_level="static",
        tc_result="pass",
        downgrade={
            "approvedBy": "user",
            "approvedAt": "2026-06-01T00:00:00Z",
            "reason": "no real device available, static evidence accepted",
        },
    )
    report, _ = build_completion_report(task_dir)
    assert report["result"] == "pass", report
    assert report["completion"]["runtimeProofSatisfied"] is True
    assert any("runtimeDowngradeApproval is approved" in w for w in report["warnings"]), report["warnings"]


def test_completion_skips_gate_when_runtime_proof_not_required(tmp_path: Path) -> None:
    """runtimeProofRequired != 'yes' (e.g. backend/library) does not trigger gate."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, runtime_proof_required="auto", tc_runtime_level="static", tc_result="pass")
    report, _ = build_completion_report(task_dir)
    assert report["result"] == "pass", report
    assert report["completion"]["runtimeProofSatisfied"] is True


def test_completion_fails_when_runtime_tc_demoted_to_optional_without_downgrade(tmp_path: Path) -> None:
    """Regression for app_0601003729: optional runtime TC must not bypass runtimeProofRequired."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_required=False, tc_runtime_level="device", tc_result="pass")
    report, _ = build_completion_report(task_dir)
    assert report["result"] == "fail", report
    assert any("demoted to optional" in issue for issue in report["issues"]), report["issues"]
    assert report["coverage"]["runtimeProofOptionalCandidates"] == ["TC-F01"]


def _make_runtime_tc_dry_run_only(task_dir: Path) -> None:
    """Rewrite the TC evidence to look like a probe-flow dry-run (intent only).

    Adds a dryRun=true probe-flow summary artifact, points the machine anchor at
    it, and replaces the passed hard metric with an `*_dry_run` metric so the
    only proof is action-intent validation, not a real device/runtime run.
    """
    iter_dir = task_dir / "logs" / "iter-1"
    iter_dir.mkdir(parents=True, exist_ok=True)
    (iter_dir / "ios-probe-flow-summary.json").write_text(json.dumps({
        "result": "pass",
        "adapter": "xcuitest",
        "dryRun": True,
    }))
    evaluation_path = task_dir / "evaluation.json"
    data = json.loads(evaluation_path.read_text())
    row = data["testResults"][0]
    row["evidence"] = ["logs/iter-1/ios-probe-flow-summary.json"]
    row["evidenceAssessment"] = {
        "verdict": "proved",
        "assessor": "ios-probe-flow",
        "machineAnchor": "logs/iter-1/ios-probe-flow-summary.json",
        "hardMetrics": [{
            "name": "ios_probe_flow_dry_run",
            "value": "pass",
            "expected": "pass",
            "passed": True,
            "evidence": "logs/iter-1/ios-probe-flow-summary.json",
        }],
    }
    data["evidence"] = ["logs/iter-1/ios-probe-flow-summary.json"]
    evaluation_path.write_text(json.dumps(data))


def test_completion_fails_when_device_tc_passes_on_dry_run_only(tmp_path: Path) -> None:
    """Regression for music_audio_stop_finish_v3: a dry-run probe-flow (action intent
    only) must not satisfy a device-level runtime-proof requirement."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="device", tc_result="pass")
    _make_runtime_tc_dry_run_only(task_dir)

    report, _ = build_completion_report(task_dir)

    assert report["result"] == "fail", report
    assert any("dry-run evidence" in issue for issue in report["issues"]), report["issues"]
    assert report["coverage"]["runtimeProofDryRunOnly"] == ["TC-F01"]
    assert report["coverage"]["runtimeProofPassed"] == []
    assert report["completion"]["runtimeProofSatisfied"] is False


def test_completion_fails_dry_run_only_even_when_runtime_proof_auto(tmp_path: Path) -> None:
    """A runtime/device-level required TC needs real proof even when
    runtimeProofRequired is 'auto'/unset; dry-run must not satisfy it."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, runtime_proof_required="auto", tc_runtime_level="runtime", tc_result="pass")
    _make_runtime_tc_dry_run_only(task_dir)

    report, _ = build_completion_report(task_dir)

    assert report["result"] == "fail", report
    assert any("dry-run evidence" in issue for issue in report["issues"]), report["issues"]
    assert report["coverage"]["runtimeProofDryRunOnly"] == ["TC-F01"]
    assert report["completion"]["runtimeProofSatisfied"] is False


def test_completion_fails_android_dry_run_even_with_generic_pass_metric(tmp_path: Path) -> None:
    """Android probe-flow dry-run summary must win over a generic passed metric name."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="device", tc_result="pass")
    _make_runtime_tc_dry_run_only(task_dir)
    evaluation_path = task_dir / "evaluation.json"
    data = json.loads(evaluation_path.read_text())
    data["testResults"][0]["evidence"] = ["logs/iter-1/probe-flow-summary.json"]
    data["testResults"][0]["evidenceAssessment"]["machineAnchor"] = "logs/iter-1/probe-flow-summary.json"
    data["testResults"][0]["evidenceAssessment"]["hardMetrics"][0]["name"] = "android_probe_flow_result"
    data["testResults"][0]["evidenceAssessment"]["hardMetrics"][0]["evidence"] = "logs/iter-1/probe-flow-summary.json"
    iter_dir = task_dir / "logs" / "iter-1"
    (iter_dir / "probe-flow-summary.json").write_text(json.dumps({
        "result": "pass",
        "platform": "android",
        "dryRun": True,
    }))
    evaluation_path.write_text(json.dumps(data))

    report, _ = build_completion_report(task_dir)

    assert report["result"] == "fail", report
    assert report["coverage"]["runtimeProofDryRunOnly"] == ["TC-F01"]
    assert report["coverage"]["runtimeProofPassed"] == []
    assert report["completion"]["runtimeProofSatisfied"] is False


def test_completion_passes_dry_run_with_approved_downgrade(tmp_path: Path) -> None:
    """An approved runtimeDowngradeApproval still lets a dry-run-only run finish (with warn)."""
    task_dir = tmp_path / "task"
    _write_runtime_task(
        task_dir,
        tc_runtime_level="device",
        tc_result="pass",
        downgrade={
            "approvedBy": "user",
            "approvedAt": "2026-06-01T00:00:00Z",
            "reason": "real device runner blocked; dry-run intent accepted",
        },
    )
    _make_runtime_tc_dry_run_only(task_dir)

    report, _ = build_completion_report(task_dir)

    assert report["result"] == "pass", report
    assert report["completion"]["runtimeProofSatisfied"] is True


def test_completion_passes_dry_run_rescued_by_independent_secondary(tmp_path: Path) -> None:
    """A non-dry-run independent secondaryAssessment proves real runtime evidence exists."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="device", tc_result="pass")
    _make_runtime_tc_dry_run_only(task_dir)
    evaluation_path = task_dir / "evaluation.json"
    data = json.loads(evaluation_path.read_text())
    (task_dir / "logs" / "iter-1" / "device-syslog.log").write_text("music_audio_stop fired")
    # Keep a screenshot so this test isolates the secondary-assessment rescue
    # (the screenshot hard gate is covered by its own test).
    (task_dir / "logs" / "iter-1" / "tc-f01-after.png").write_bytes(b"fake-png")
    data["testResults"][0]["evidence"].append("logs/iter-1/tc-f01-after.png")
    data["testResults"][0]["evidenceAssessment"]["secondaryAssessment"] = {
        "verdict": "proved",
        "assessor": "device-syslog",
        "independent": True,
        "evidence": "logs/iter-1/device-syslog.log",
    }
    evaluation_path.write_text(json.dumps(data))

    report, _ = build_completion_report(task_dir)

    assert report["result"] == "pass", report
    assert report["coverage"]["runtimeProofPassed"] == ["TC-F01"]
    assert report["coverage"]["runtimeProofDryRunOnly"] == []



def test_workflow_check_materializes_workflow_json(monkeypatch, tmp_path: Path) -> None:
    """workflow-check should create the machine-readable workflow.json contract."""
    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    import orchestrator.state as state_mod
    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")
    ok, report = check_workflow_consistency("task01")
    assert (task_dir / "workflow.json").exists()
    data = json.loads((task_dir / "workflow.json").read_text())
    assert data["version"] == 2
    assert data["runtimeProofRequired"] is True
    assert data["testcases"][0]["id"] == "TC-F01"
    assert data["testcases"][0]["intent"]["actions"]
    assert ok is True, report


# ---------------------------------------------------------------------------
# workflow.py gate
# ---------------------------------------------------------------------------


def _write_workflow_task(
    tmp_path: Path,
    *,
    runtime_proof_required: str = "yes",
    verification_target: str = "real_device",
    downgrade: dict | None = None,
) -> Path:
    """Write a minimal task layout under tmp/.automind/tasks/<task> and return the dir."""
    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "Brainstorm.md").write_text(
        "# Brainstorm\n\n"
        "## Clarification questions / decisions\n\n- No blocking questions.\n\n"
        "## Pre-implementation user review\n\n"
        "- Decision: `auto_proceed`\n"
        "- Needs user input before code changes: `false`\n\n"
        "## Assumptions\n\n- Synthetic workflow gate fixture.\n\n"
        "## User Decision Bundle (one-shot confirmation)\n\nconfirmed.\n"
    )
    (task_dir / "Requirements.md").write_text(
        "# Requirements\n\n"
        "## Requirements with inline Acceptance Criteria\n\n"
        "### R01 — runtime\n"
        "- **AC-001**: runtime ok\n"
        "  - Verification method: runtime command evidence\n"
    )
    (task_dir / "TestCases.md").write_text(
        "# TestCases\n\n"
        "Quality coverage: not applicable for this runtime gate fixture.\n\n"
        "| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeAutonomy command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n"
        "|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n"
        "| TC-F01 | R01 / AC-001 | Functional | runtime | preflight | command: pytest | run command -> assert pass | logs/iter-1/evidence.txt | none | yes |\n"
    )
    (task_dir / "Plan.md").write_text(
        "# Plan\n\n"
        "## First functional batch\n- TC-F01\n\n"
        "## Verification command\n- `automind workflow-contract task01`\n- `pytest`\n\n"
        "## Implementation Checklist\n\n| ID | Source | Status | Owner | Evidence | Notes |\n|----|--------|--------|-------|----------|-------|\n| T01 | R01 / AC-001 / TC-F01 | pending | generator | - | fixture |\n\n"
        "## Verification Checklist\n\n| ID | Required | Status | Owner | Evidence | Notes |\n|----|----------|--------|-------|----------|-------|\n| TC-F01 | yes | pending | evaluator | logs/iter-1/evidence.txt | fixture |\n"
    )
    decision_bundle = {
        "verificationTarget": verification_target,
        "runtimeProofRequired": runtime_proof_required,
        "runtimeDowngradeApproval": downgrade,
        "taskType": "ios",
        "confirmedAt": "2026-06-01T00:00:00Z",
        "confirmedBy": "user",
    }
    state = {
        "taskId": "task01",
        "status": "planned",
        "iteration": 0,
        "planner": {
            "preImplementationReview": {
                "decision": "auto_proceed",
                "decisionBundle": decision_bundle,
            }
        },
    }
    (task_dir / "runtime-state.json").write_text(json.dumps(state))
    return task_dir


def test_workflow_gate_blocks_unsigned_static_target(monkeypatch, tmp_path: Path) -> None:
    """runtimeProofRequired=yes + verificationTarget=not_applicable + no downgrade = issue."""
    _write_workflow_task(
        tmp_path,
        verification_target="not_applicable",
        downgrade=None,
    )
    import orchestrator.state as state_mod
    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")
    ok, report = check_workflow_consistency("task01")
    runtime_issues = [
        issue for issue in report["issues"] if "runtimeProofRequired=yes" in issue
    ]
    assert runtime_issues, report["issues"]
    assert ok is False


def test_workflow_gate_accepts_real_device_target(monkeypatch, tmp_path: Path) -> None:
    """runtimeProofRequired=yes + verificationTarget=real_device = no runtime-proof issue."""
    _write_workflow_task(tmp_path, verification_target="real_device")
    import orchestrator.state as state_mod
    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")
    _, report = check_workflow_consistency("task01")
    assert not any("runtimeProofRequired=yes" in issue for issue in report["issues"]), report


def test_workflow_gate_blocks_unrefined_scaffold_for_nontrivial_task(monkeypatch, tmp_path: Path) -> None:
    """A non-trivial implementation task stuck on the deterministic scaffold must be blocked.

    Regression: an Android task whose Requirements.md was only the deterministic
    scaffold (planner.mode=deterministic_scaffold, artifactsRefined!=true) produced
    coarse Rxx and too few TestCases. workflow-check must force the AI Phase 2
    Refiner before Build.
    """
    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state["userInput"] = "为 Android MediaPlay新增 music_audio_stop 埋点"
    state["planner"]["mode"] = "deterministic_scaffold"
    (task_dir / "runtime-state.json").write_text(json.dumps(state))
    import orchestrator.state as state_mod
    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")

    ok, report = check_workflow_consistency("task01")

    assert ok is False, report
    assert any("still the deterministic scaffold" in issue for issue in report["issues"]), report


def test_workflow_gate_allows_refined_planner_for_nontrivial_task(monkeypatch, tmp_path: Path) -> None:
    """After the AI Phase 2 Refiner runs (mode advances, artifactsRefined=true), the gate clears."""
    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state["userInput"] = "为 Android MediaPlay新增 music_audio_stop 埋点"
    state["planner"]["mode"] = "ai_test_planner"
    state["planner"]["artifactsRefined"] = True
    (task_dir / "runtime-state.json").write_text(json.dumps(state))
    import orchestrator.state as state_mod
    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")

    _, report = check_workflow_consistency("task01")

    assert not any("still the deterministic scaffold" in issue for issue in report["issues"]), report


def test_workflow_gate_does_not_reopen_confirmed_auto_proceed_review(monkeypatch, tmp_path: Path) -> None:
    """A consumed confirm_recommended_direction answer must not become a stale ask_user blocker."""
    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    (task_dir / "TestCases.md").write_text(
        (task_dir / "TestCases.md").read_text()
        + "\n\nUI/user action sequence: build debug APK, install/start/launch app, perform safe playback UI action, collect logcat/reporting sink.\n"
    )
    (task_dir / "Plan.md").write_text(
        (task_dir / "Plan.md").read_text()
        + "\n\nBuild/install/start/launch/UI-flow verification decision: required before finish.\n"
    )
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state["userInput"] = "为 Android MediaPlay新增 music_audio_stop 埋点"
    state["planner"]["mode"] = "ai_test_planner"
    review = state["planner"]["preImplementationReview"]
    review.update({
        "decision": "auto_proceed",
        "needsUserInput": False,
        "selectedOption": "confirm_recommended_direction",
        "confirmedAt": "2026-06-09T14:06:48",
        "confirmedBy": "cli_user",
    })
    review["decisionBundle"]["confirmedAt"] = "2026-06-09T14:06:48"
    review["decisionBundle"]["confirmedBy"] = "cli_user"
    (task_dir / "runtime-state.json").write_text(json.dumps(state))
    (task_dir / "user-answers.json").write_text(json.dumps([{
        "selectedOption": "confirm_recommended_direction",
        "answeredAt": "2026-06-09T14:06:48",
    }]))
    (task_dir / "pre-implementation-review.json").write_text(json.dumps({
        "decision": "auto_proceed",
        "needsUserInput": False,
        "approval": {"confirmedAt": "2026-06-09T14:06:48", "confirmedBy": "cli_user"},
        "decisionBundle": review["decisionBundle"],
        "nextAction": "delivery",
    }))
    import orchestrator.state as state_mod
    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")

    ok, report = check_workflow_consistency("task01")

    assert ok is True, report
    assert not any("pre-implementation review must ask_user once" in issue for issue in report["issues"]), report


def test_workflow_gate_accepts_approved_downgrade_with_warning(
    monkeypatch, tmp_path: Path
) -> None:
    """Signed downgrade should pass the gate (issue cleared) and emit a warning."""
    _write_workflow_task(
        tmp_path,
        verification_target="not_applicable",
        downgrade={
            "approvedBy": "user",
            "approvedAt": "2026-06-01T00:00:00Z",
            "reason": "no real device",
        },
    )
    import orchestrator.state as state_mod
    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")
    _, report = check_workflow_consistency("task01")
    assert not any("runtimeProofRequired=yes" in issue for issue in report["issues"]), report
    assert any(
        "runtimeDowngradeApproval is approved" in w for w in report["warnings"]
    ), report


# ---------------------------------------------------------------------------
# main.py hard-stop phrasing
# ---------------------------------------------------------------------------


def test_hardstop_phrasing_detects_chinese_unavailable() -> None:
    assert mentions_hardstop_device_unavailable_phrasing("真机不可用，请走 simulator") is True
    assert mentions_hardstop_device_unavailable_phrasing("没真机") is True


def test_hardstop_phrasing_detects_english_unavailable() -> None:
    assert mentions_hardstop_device_unavailable_phrasing("no real device available") is True
    assert mentions_hardstop_device_unavailable_phrasing("real device unavailable") is True


def test_hardstop_phrasing_ignores_neutral_mentions() -> None:
    assert mentions_hardstop_device_unavailable_phrasing("please verify on real device") is False
    assert mentions_hardstop_device_unavailable_phrasing("用真机来跑回归") is False


def test_mobile_review_still_required_under_hardstop_phrasing() -> None:
    """Hardstop phrasing must keep the review gate ON even if a target is named."""
    user_input = "iOS app: bundle id com.foo.bar 真机不可用，请走 simulator"
    assert is_client_development_or_verification_task(user_input, "ios") is True
    needs_review = mobile_task_needs_verification_target_review(user_input, "ios")
    assert needs_review is True


def test_mobile_review_skipped_when_user_explicitly_picks_real_device() -> None:
    """Explicit positive 'use real device' (no hardstop) lets the gate stay off."""
    user_input = "iOS app: bundle id com.foo.bar; please verify on real device"
    assert mentions_hardstop_device_unavailable_phrasing(user_input) is False
    # Note: the review gate logic also reads task_type; explicit real_device
    # phrasing without hardstop should NOT force a review prompt.
    needs_review = mobile_task_needs_verification_target_review(user_input, "ios")
    assert needs_review is False

def test_completion_fails_when_runtime_tc_pass_lacks_screenshot(tmp_path: Path) -> None:
    """Hard gate: a runtime/device required pass must carry a screenshot by
    default. Missing both a screenshot and a noScreenshotReason now blocks
    finish (an issue, not just a warning)."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="runtime", tc_result="pass", with_screenshot=False)

    report, _ = build_completion_report(task_dir)

    assert report["result"] == "fail", report
    assert report["coverage"]["runtimeUiPassedWithoutScreenshot"] == ["TC-F01"]
    assert report["completion"]["runtimeUiScreenshotPresent"] is False
    assert any("without screenshot evidence" in issue for issue in report["issues"])


def test_completion_accepts_runtime_tc_screenshot_or_no_screenshot_reason(tmp_path: Path) -> None:
    # A real screenshot satisfies the gate.
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="runtime", tc_result="pass", with_screenshot=True)
    report, _ = build_completion_report(task_dir)
    assert report["result"] == "pass", report
    assert report["coverage"]["runtimeUiPassedWithoutScreenshot"] == []

    # Alternative: explicit no-screenshot reason exempts pure-backend / no-capture surfaces.
    task_dir2 = tmp_path / "task2"
    _write_runtime_task(task_dir2, tc_runtime_level="runtime", tc_result="pass", with_screenshot=False)
    data = json.loads((task_dir2 / "evaluation.json").read_text())
    data["testResults"][0]["noScreenshotReason"] = "No screenshot available; xcresult attachment is cited instead."
    (task_dir2 / "evaluation.json").write_text(json.dumps(data))
    report2, _ = build_completion_report(task_dir2)
    assert report2["result"] == "pass", report2
    assert report2["coverage"]["runtimeUiPassedWithoutScreenshot"] == []


def test_completion_fails_when_proved_value_rests_on_truncated_log_line(tmp_path: Path) -> None:
    """Inline-truncation hard gate: a log/keyword hardMetric whose keyword only
    appears inside a truncated evidence line cannot stay verdict=proved. This
    mirrors the playback_analytics_stop case where stop_type was cut off and the
    proof reduced to source-code inference."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="runtime", tc_result="pass", with_screenshot=True)
    iter_dir = task_dir / "logs" / "iter-1"
    (iter_dir / "syslog.log").write_text(
        'EventV3 v3_audio_over params={"a":1,"stop_type ...[truncated 4096 chars]\n'
    )
    data = json.loads((task_dir / "evaluation.json").read_text())
    data["testResults"][0]["evidenceAssessment"]["hardMetrics"] = [{
        "name": "log_keyword_matched",
        "value": "stop_type",
        "expected": "stop_type",
        "passed": True,
        "evidence": "logs/iter-1/syslog.log",
    }]
    (task_dir / "evaluation.json").write_text(json.dumps(data))

    report, _ = build_completion_report(task_dir)
    assert report["result"] == "fail", report
    assert report["completion"]["runtimeAssertionProofUntruncated"] is False
    assert any("truncated evidence line" in issue for issue in report["issues"])


def test_completion_fails_when_truncation_explicitly_declared(tmp_path: Path) -> None:
    """The Evaluator can self-declare assertionEvidenceTruncated; the gate then
    refuses proved unless an independent backing exists."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="runtime", tc_result="pass", with_screenshot=True)
    data = json.loads((task_dir / "evaluation.json").read_text())
    data["testResults"][0]["evidenceAssessment"]["assertionEvidenceTruncated"] = True
    (task_dir / "evaluation.json").write_text(json.dumps(data))

    report, _ = build_completion_report(task_dir)
    assert report["result"] == "fail", report
    assert any("truncated evidence line" in issue for issue in report["issues"])


def test_truncated_proof_rescued_by_independent_secondary(tmp_path: Path) -> None:
    """A truncated primary line is acceptable when an independent secondary
    assessor (e.g. packet capture) proves the same value."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="runtime", tc_result="pass", with_screenshot=True)
    iter_dir = task_dir / "logs" / "iter-1"
    (iter_dir / "capture.json").write_text('{"stop_type": 3}')
    data = json.loads((task_dir / "evaluation.json").read_text())
    assessment = data["testResults"][0]["evidenceAssessment"]
    assessment["assertionEvidenceTruncated"] = True
    assessment["secondaryAssessment"] = {
        "verdict": "proved",
        "assessor": "packet-capture",
        "independent": True,
        "evidence": "logs/iter-1/capture.json",
    }
    (task_dir / "evaluation.json").write_text(json.dumps(data))

    report, _ = build_completion_report(task_dir)
    assert report["result"] == "pass", report
    assert report["completion"]["runtimeAssertionProofUntruncated"] is True


def test_untruncated_keyword_line_passes_truncation_gate(tmp_path: Path) -> None:
    """A clean (untruncated) keyword line must not trip the truncation gate."""
    task_dir = tmp_path / "task"
    _write_runtime_task(task_dir, tc_runtime_level="runtime", tc_result="pass", with_screenshot=True)
    iter_dir = task_dir / "logs" / "iter-1"
    (iter_dir / "syslog.log").write_text('EventV3 v3_audio_over params stop_type=3 is_end=1\n')
    data = json.loads((task_dir / "evaluation.json").read_text())
    data["testResults"][0]["evidenceAssessment"]["hardMetrics"] = [{
        "name": "log_keyword_matched",
        "value": "stop_type",
        "expected": "stop_type",
        "passed": True,
        "evidence": "logs/iter-1/syslog.log",
    }]
    (task_dir / "evaluation.json").write_text(json.dumps(data))

    report, _ = build_completion_report(task_dir)
    assert report["result"] == "pass", report
    assert report["completion"]["runtimeAssertionProofUntruncated"] is True
