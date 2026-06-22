import json
from pathlib import Path

from orchestrator.completion import build_completion_report
from orchestrator.tc_attempts import record_tc_attempts, read_tc_attempts


def write_testcases(task_dir: Path) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "Requirements.md").write_text("# Requirements\n\n## R01\n- AC-001: runtime proof.\n")
    (task_dir / "TestCases.md").write_text("""
# TestCases

| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / AutoMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |
|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|
| TC-F04 | R01 / AC-001 | Functional | device | adb | android-probe-flow | Play then pause. | music_audio_finish + stop_reason | - | yes |
""")


def test_record_tc_attempts_and_completion_distinguishes_attempted_from_not_run(tmp_path: Path) -> None:
    task_dir = tmp_path / "task01"
    write_testcases(task_dir)
    evidence = task_dir / "logs" / "iter-7" / "runtime-result.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("{}")
    evaluation = {
        "iteration": 7,
        "result": "partial",
        "nextAction": "retry_generator",
        "testResults": [{
            "testCaseId": "TC-F04",
            "result": "partial",
            "attemptIteration": 7,
            "summary": "Runtime proof executed but required report signals were missing.",
            "progressKind": "control_discovery",
            "hypothesis": "Playback control may be inside the book detail page.",
            "actionTried": "Tapped the first cover card from home.",
            "expectedSignal": "Book detail page exposes a play/listen button.",
            "outcome": "Entered a new detail-like page but stop reporting signals were still absent.",
            "ruledOut": ["home screen direct playback button"],
            "remainingHypotheses": ["detail page play button", "bottom mini-player"],
            "evidence": [str(evidence)],
            "missingSignals": ["music_audio_finish", "stop_reason"],
        }],
    }

    ledger = record_tc_attempts(task_dir, evaluation, source="android-probe-flow")

    assert ledger["latest"]["TC-F04"]["iteration"] == 7
    saved = read_tc_attempts(task_dir)
    assert saved["latest"]["TC-F04"]["result"] == "partial"
    assert saved["latest"]["TC-F04"]["progressKind"] == "control_discovery"
    assert saved["latest"]["TC-F04"]["hypothesis"] == "Playback control may be inside the book detail page."
    assert saved["progressByTc"]["TC-F04"]["ruledOut"] == ["home screen direct playback button"]
    assert saved["progressByTc"]["TC-F04"]["remainingHypotheses"] == ["detail page play button", "bottom mini-player"]

    report, _ = build_completion_report(task_dir, {"iteration": 8, "result": "partial", "nextAction": "retry_generator"})

    tc = next(item for item in report["testResults"] if item["testCaseId"] == "TC-F04")
    assert tc["result"] == "partial"
    assert tc["sources"] == ["tc-attempts"]
    assert "required testcase not_run: TC-F04" not in report["issues"]
    assert "required testcase failed or blocked: TC-F04" in report["issues"]
