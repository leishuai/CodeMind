from __future__ import annotations

import json
from pathlib import Path

from scripts import quality_evaluator as qe


def _task(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path
    task = root / ".automind" / "tasks" / "task01"
    log_dir = task / "logs" / "iter-1"
    log_dir.mkdir(parents=True)
    (task / "runtime-state.json").write_text(json.dumps({"iteration": 1}) + "\n")
    return root, task, log_dir


def test_verifier_timeout_is_warn_not_hard_fail(tmp_path: Path) -> None:
    root, task, log_dir = _task(tmp_path)
    (log_dir / "action.json").write_text("TimeoutExpired(['python3', 'ax_probe.py'], 95)\n")

    checks, _ = qe.quality_checks(root, task, log_dir)

    by_id = {c["id"]: c for c in checks}
    assert by_id["timeout-signal-nonblocking"]["result"] == "warn"
    assert by_id["timeout-signal-nonblocking"]["failureClass"] == "automation_or_system_timeout_signal"
    assert "timeout" not in by_id
    assert qe.worst_result(checks) == "warn"


def test_raw_crash_keyword_without_stack_page_is_warn(tmp_path: Path) -> None:
    root, task, log_dir = _task(tmp_path)
    (log_dir / "syslog-filtered.log").write_text('key:hmd_app_exit_reason params:{"reason_des":"OOMCrash"}\n')

    checks, _ = qe.quality_checks(root, task, log_dir)

    by_id = {c["id"]: c for c in checks}
    assert by_id["crash-signal-needs-stack"]["result"] == "warn"
    assert by_id["crash-signal-needs-stack"]["failureClass"] == "crash_signal_needs_stack"
    assert "crash" not in by_id
    assert qe.worst_result(checks) == "warn"


def test_structured_crash_with_stack_and_page_is_hard_fail(tmp_path: Path) -> None:
    root, task, log_dir = _task(tmp_path)
    (log_dir / "crash-report.log").write_text(
        "Exception Type: EXC_CRASH (SIGABRT)\n"
        "Thread 0 Crashed:\n"
        "0 MyApp FooViewController bar\n"
        "last_scene=AudioPageViewController\n"
    )

    checks, _ = qe.quality_checks(root, task, log_dir)

    by_id = {c["id"]: c for c in checks}
    assert by_id["crash_with_stack"]["result"] == "fail"
    assert by_id["crash_with_stack"]["failureClass"] == "product_crash_with_stack"
    assert qe.worst_result(checks) == "fail"


def test_quality_merge_clears_stale_quality_failed_check_when_latest_warn(tmp_path: Path) -> None:
    root, task, log_dir = _task(tmp_path)
    eval_path = task / "evaluation.json"
    eval_path.write_text(json.dumps({
        "iteration": 1,
        "result": "fail",
        "nextAction": "retry_generator",
        "summary": "Required TCs passed. Quality check: fail.",
        "failedChecks": [{"name": "quality-check", "category": "validation_failure", "reason": "old"}],
        "testResults": [{"testCaseId": "TC-F01", "required": True, "result": "pass"}],
    }) + "\n")
    summary_path = log_dir / "quality-summary.json"
    summary = {"result": "warn", "iteration": 1, "qualityChecks": [{"id": "timeout-signal-nonblocking", "result": "warn"}]}
    summary_path.write_text(json.dumps(summary) + "\n")

    merged = qe.merge_evaluation(task, summary_path, summary)

    assert merged["result"] == "pass"
    assert merged["nextAction"] == "finish"
    assert merged["failedChecks"] == []
    assert any("stale failure cleared" in w for w in merged.get("warnings", []))
    audit = merged.get("staleQualityClear")
    assert isinstance(audit, dict)
    assert audit["from"] == "fail"
    assert audit["basisRequiredTestCases"] == ["TC-F01"]
    assert audit.get("clearedAt")


def test_quality_merge_does_not_clear_when_required_tc_failing(tmp_path: Path) -> None:
    root, task, log_dir = _task(tmp_path)
    eval_path = task / "evaluation.json"
    eval_path.write_text(json.dumps({
        "iteration": 1,
        "result": "fail",
        "nextAction": "retry_generator",
        "summary": "A required TC is failing.",
        "failedChecks": [{"name": "quality-check", "category": "validation_failure", "reason": "old"}],
        "testResults": [{"testCaseId": "TC-F01", "required": True, "result": "fail"}],
    }) + "\n")
    summary_path = log_dir / "quality-summary.json"
    summary = {"result": "warn", "iteration": 1, "qualityChecks": [{"id": "timeout-signal-nonblocking", "result": "warn"}]}
    summary_path.write_text(json.dumps(summary) + "\n")

    merged = qe.merge_evaluation(task, summary_path, summary)

    assert merged["result"] == "fail"
    assert merged["nextAction"] == "retry_generator"
    assert "staleQualityClear" not in merged


def test_crash_first_window_without_stack_still_fails_when_later_window_has_stack(tmp_path: Path) -> None:
    root, task, log_dir = _task(tmp_path)
    filler = "x" * 2000
    (log_dir / "syslog-filtered.log").write_text(
        "crash keyword far from context\n"
        + filler
        + "\nException Type: EXC_CRASH (SIGABRT)\n"
        "Thread 0 Crashed: crash here\n"
        "last_scene=AudioPageViewController\n"
    )

    checks, _ = qe.quality_checks(root, task, log_dir)

    by_id = {c["id"]: c for c in checks}
    assert "crash_with_stack" in by_id
    assert by_id["crash_with_stack"]["result"] == "fail"
    assert qe.worst_result(checks) == "fail"
