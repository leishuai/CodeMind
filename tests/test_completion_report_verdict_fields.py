from pathlib import Path

from orchestrator.completion import (
    build_completion_report,
    validate_verification_unblock_changes,
)


def test_completion_report_separates_raw_evaluation_claim_from_final_verdict(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "Requirements.md").write_text("""
# Requirements

## R01 Runtime proof
- AC-001: Stop reporting includes stop_reason.
""")
    (task_dir / "TestCases.md").write_text("""
# Test Cases

| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |
|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|
| TC-F04 | R01 / AC-001 | Functional | runtime | Android device | android-probe-flow | Execute music stop proof flow. | music_audio_finish with stop_reason | - | yes |
""")
    evaluation = {
        "iteration": 5,
        "result": "pass",
        "nextAction": "finish",
        "testResults": [],
    }

    report, _ = build_completion_report(task_dir, evaluation)

    assert report["result"] == "fail"
    assert "evaluation" not in report
    assert report["rawEvaluationClaim"] == {"result": "pass", "nextAction": "finish", "iteration": 5}
    assert report["completionVerdict"]["result"] == "fail"
    assert report["completionVerdict"]["overridesRawEvaluation"] is True
    assert "required testcase not_run" in report["completionVerdict"]["reason"]


def _instrumentation_change(status: str, **overrides) -> dict:
    item = {
        "id": "VUC-009",
        "status": status,
        "category": "test_instrumentation",
        "files": ["Sources/MyView.swift"],
        "reason": "added accessibilityIdentifier so XCUITest can locate the cover",
        "checkpoint": "logs/iter-1/vuc-009.diff",
        "verificationEvidence": "logs/iter-1/proof.log",
        "restoreEvidence": "logs/iter-1/restore.log",
    }
    item.update(overrides)
    return item


def test_promoted_test_instrumentation_is_blocked(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    evaluation = {"verificationUnblockChanges": [_instrumentation_change("promoted")]}

    issues, _ = validate_verification_unblock_changes(task_dir, evaluation)

    assert any("test instrumentation" in issue for issue in issues)


def test_restored_test_instrumentation_is_allowed(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    # restore/verification evidence paths must exist for a clean pass
    (task_dir / "logs").mkdir()
    (task_dir / "logs" / "iter-1").mkdir()
    for name in ("vuc-009.diff", "proof.log", "restore.log"):
        (task_dir / "logs" / "iter-1" / name).write_text("ok")
    evaluation = {"verificationUnblockChanges": [_instrumentation_change("restored")]}

    issues, _ = validate_verification_unblock_changes(task_dir, evaluation)

    assert not any("test instrumentation" in issue for issue in issues)


def test_build_unblock_can_be_promoted(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "logs").mkdir()
    (task_dir / "logs" / "iter-1").mkdir()
    for name in ("vuc-009.diff", "proof.log"):
        (task_dir / "logs" / "iter-1" / name).write_text("ok")
    change = _instrumentation_change(
        "promoted",
        category="build_unblock",
        files=["Module.podspec"],
        reason="add BDText dependency so the module compiles",
    )
    change.pop("restoreEvidence", None)
    evaluation = {"verificationUnblockChanges": [change]}

    issues, _ = validate_verification_unblock_changes(task_dir, evaluation)

    assert not any("test instrumentation" in issue for issue in issues)


def test_instrumentation_detected_by_keyword_without_category(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    change = _instrumentation_change("promoted")
    change.pop("category")  # rely on keyword fallback (accessibilityIdentifier in reason)
    evaluation = {"verificationUnblockChanges": [change]}

    issues, _ = validate_verification_unblock_changes(task_dir, evaluation)

    assert any("test instrumentation" in issue for issue in issues)
