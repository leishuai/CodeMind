import json
from pathlib import Path

from orchestrator.completion import build_completion_report
from orchestrator.tc_attempts import record_tc_attempts, read_tc_attempts


def write_testcases(task_dir: Path) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "Requirements.md").write_text("# Requirements\n\n## R01\n- AC-001: runtime proof.\n")
    (task_dir / "TestCases.md").write_text("""
# TestCases

| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |
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


def test_record_tc_attempts_extracts_nested_ui_exploration(tmp_path: Path) -> None:
    """P0-A: convergence fields nested under testResults[].uiExploration must be
    extracted, not silently dropped (the evaluator prompt writes them there)."""
    task_dir = tmp_path / "task02"
    write_testcases(task_dir)
    evaluation = {
        "iteration": 3,
        "result": "fail",
        "nextAction": "retry_generator",
        "testResults": [{
            "testCaseId": "TC-F04",
            "result": "fail",
            "attemptIteration": 3,
            "uiExploration": {
                "mode": "control_discovery",
                "attempts": [{
                    "progressKind": "control_discovery",
                    "hypothesis": "Pause control lives on the now-playing bar.",
                    "ruledOut": ["home tab pause button"],
                    "remainingHypotheses": ["mini player bar"],
                    "nextSelectorCandidates": ["type==.button AND label CONTAINS '暂停'"],
                }],
            },
        }],
    }

    record_tc_attempts(task_dir, evaluation, source="evaluation")
    saved = read_tc_attempts(task_dir)
    progress = saved["progressByTc"]["TC-F04"]
    assert progress["ruledOut"] == ["home tab pause button"]
    assert progress["remainingHypotheses"] == ["mini player bar"]
    assert progress["nextSelectorCandidates"] == ["type==.button AND label CONTAINS '暂停'"]
    assert progress["narrowingRounds"] == 1


def test_record_tc_attempts_surfaces_failed_checks_recovery_action(tmp_path: Path) -> None:
    """When evaluator writes a recoveryAction under failedChecks[] (the
    canonical place for failure-triage data), the TC convergence ledger must
    expose it so the next Generator round sees it. ExampleApp-style build-failure
    loop breaks because the recovery action ('run pod install') reaches the
    Generator instead of being lost."""
    task_dir = tmp_path / "task03"
    write_testcases(task_dir)
    evaluation = {
        "iteration": 31,
        "result": "fail",
        "nextAction": "retry_generator",
        "testResults": [{
            "testCaseId": "TC-F04",
            "result": "fail",
            "attemptIteration": 31,
            "summary": "Build failed.",
        }],
        "failedChecks": [{
            "testCaseIds": ["TC-F04"],
            "category": "dependency_missing",
            "recoveryAction": "Run `pod install` at the workspace root.",
            "sameProblemKey": "ios.build.pod.SSUGCoinWidget_missing",
            "specificErrors": [
                "TTReadingWidgetExtension.swift:11:8: error: unable to find module dependency: 'SSUGCoinWidget'",
                "NotificationService.m:10:9: error: 'BDUGPushSDK/BDUGPushExtension.h' file not found",
            ],
        }],
    }

    record_tc_attempts(task_dir, evaluation, source="evaluation")
    saved = read_tc_attempts(task_dir)
    progress = saved["progressByTc"]["TC-F04"]

    assert any("Run `pod install`" in action for action in (progress.get("recoveryActions") or []))
    assert "dependency_missing" in (progress.get("failureCategories") or [])
    assert "ios.build.pod.SSUGCoinWidget_missing" in (progress.get("sameProblemKeys") or [])
    assert len(progress.get("specificErrors") or []) == 2
    assert progress.get("hasSpecificRecovery") is True


def test_record_tc_attempts_no_recovery_is_invalid_triage(tmp_path: Path) -> None:
    """When failedChecks[] repeats the same broad 'build failed' without
    specific recovery, the ledger must still record the (empty) triage info
    and flag that no specific recovery is available. The Generator can then
    decide to read the log itself."""
    task_dir = tmp_path / "task04"
    write_testcases(task_dir)
    evaluation = {
        "iteration": 35,
        "result": "fail",
        "nextAction": "retry_generator",
        "testResults": [{
            "testCaseId": "TC-F04",
            "result": "fail",
            "attemptIteration": 35,
            "summary": "Build failed.",
        }],
        "failedChecks": [{
            "testCaseIds": ["TC-F04"],
            "category": "build_failure",
            "summary": "Build failed.",
            "sameProblemKey": "ios.build.failure",
        }],
    }

    record_tc_attempts(task_dir, evaluation, source="evaluation")
    saved = read_tc_attempts(task_dir)
    progress = saved["progressByTc"]["TC-F04"]

    assert progress.get("recoveryActions") == []
    assert progress.get("specificErrors") == []
    assert progress.get("hasSpecificRecovery") is False


def test_record_tc_attempts_flags_no_narrowing(tmp_path: Path) -> None:
    """P0-B: repeated failing attempts that rule nothing out and propose no new
    candidate must show narrowingRounds == 0 (invalid retry pattern)."""
    task_dir = tmp_path / "task03"
    write_testcases(task_dir)
    for it in (1, 2, 3):
        record_tc_attempts(task_dir, {
            "iteration": it,
            "result": "fail",
            "nextAction": "retry_generator",
            "testResults": [{
                "testCaseId": "TC-F04",
                "result": "fail",
                "attemptIteration": it,
                "summary": "stop control not found",
            }],
        }, source="evaluation")
    progress = read_tc_attempts(task_dir)["progressByTc"]["TC-F04"]
    assert progress["attemptCount"] == 3
    assert progress["narrowingRounds"] == 0
    assert progress["ruledOut"] == []
    assert progress["remainingHypotheses"] == []
