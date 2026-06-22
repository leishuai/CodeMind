from pathlib import Path

from orchestrator.completion import build_completion_report


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

| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / AutoMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |
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
