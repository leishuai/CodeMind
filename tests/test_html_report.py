from __future__ import annotations

import json
from pathlib import Path

from orchestrator.reports import generate_html_report_for_task_dir


def test_generate_html_report_includes_requirements_evidence_and_screenshots(tmp_path: Path) -> None:
    task = tmp_path / "html_report_task"
    iter_dir = task / "logs" / "iter-1"
    iter_dir.mkdir(parents=True)
    (task / "Requirements.md").write_text("""# Requirements

### R01 — Playback
- **AC-001**: Playback starts after tapping play.
""")
    (task / "Delivery.md").write_text("# Delivery\n\n- Generated player fix.\n")
    (task / "Validation.md").write_text("# Validation\n\n- Result: PASS\n- Evidence: logs/iter-1/evaluator.log\n")
    (task / "summary.md").write_text("# Summary\n\nDone.\n")
    (iter_dir / "evaluator.log").write_text("[CodeAutonomy][Verify] playbackState=playing\n")
    (iter_dir / "screen.png").write_bytes(b"fake-png")
    (task / "evaluation.json").write_text(json.dumps({
        "iteration": 1,
        "result": "pass",
        "summary": "Playback verified",
        "nextAction": "finish",
        "failedChecks": [],
        "testResults": [{"testCaseId": "TC-F01", "result": "pass", "acceptanceCriteria": ["AC-001"], "reason": "log proves playback"}],
        "evidence": [{"type": "log", "note": "evaluator", "path": "logs/iter-1/evaluator.log"}],
        "evidenceIndex": [{"path": "logs/iter-1/evaluator.log", "type": "log", "tc": "TC-F01", "signal": "playback_log"}],
    }))
    (task / "runtime-state.json").write_text(json.dumps({"status": "finished", "iteration": 1, "nextAction": "finish"}))

    report = generate_html_report_for_task_dir(task, "html_report_task")
    html = report.read_text()
    assert "html_report_task CodeAutonomy Report" in html
    assert "Playback verified" in html
    assert "R01 — Playback" in html
    assert "AC-001" in html
    assert "TC-F01" in html
    assert "Key Evidence" in html
    assert "Evidence / Screenshots / Logs" in html
    assert "Open Requirements.md" in html
    assert "Open TestCases.md" in html
    assert "All linked artifacts" in html
    assert "logs/iter-1/evaluator.log" in html
    assert "logs/iter-1/screen.png" in html
    assert "Summary / Knowledge Deposition" in html
    assert "Evidence Lookup" in html
    assert "All Artifacts Appendix" in html
    assert "Lightweight Evidence Index" not in html
    assert "Raw Artifact Appendix" not in html
    assert "playback_log" in html
    assert "Screenshots" in html
    assert "Delivery.md excerpt" in html


def test_generate_html_report_handles_failure(tmp_path: Path) -> None:
    task = tmp_path / "html_report_failed_task"
    (task / "logs" / "iter-2").mkdir(parents=True)
    (task / "Requirements.md").write_text("# Requirements\n")
    (task / "evaluation.json").write_text(json.dumps({
        "iteration": 2,
        "result": "fail",
        "summary": "Build failed",
        "nextAction": "retry_generator",
        "failedChecks": [{"name": "build", "category": "build_failure", "reason": "compile error", "evidence": "logs/iter-2/evaluator.log"}],
        "evidence": [],
    }))
    report = generate_html_report_for_task_dir(task, "html_report_failed_task")
    html = report.read_text()
    assert "Build failed" in html
    assert "build_failure" in html
    assert "retry_generator" in html


def test_generate_html_report_groups_tc_specific_artifacts_in_test_results(tmp_path: Path) -> None:
    task = tmp_path / "html_report_tc_artifacts"
    iter_dir = task / "logs" / "iter-1"
    iter_dir.mkdir(parents=True)
    (task / "Requirements.md").write_text("# Requirements\n")
    (iter_dir / "TC-F01-screen.png").write_bytes(b"fake-png")
    (iter_dir / "TC-F02.log").write_text("failure log")
    (task / "evaluation.json").write_text(json.dumps({
        "iteration": 1,
        "result": "fail",
        "summary": "One pass, one fail",
        "nextAction": "retry_generator",
        "testResults": [
            {"testCaseId": "TC-F01", "result": "pass", "reason": "first passed"},
            {"testCaseId": "TC-F02", "result": "fail", "reason": "second failed"},
        ],
        "evidence": [
            {"type": "screenshot", "note": "TC-F01 after", "path": "logs/iter-1/TC-F01-screen.png"},
            {"type": "log", "note": "TC-F02 log", "path": "logs/iter-1/TC-F02.log"},
        ],
    }))

    report = generate_html_report_for_task_dir(task, "html_report_tc_artifacts")
    html = report.read_text()
    assert "Evidence / Screenshots / Logs" in html
    assert "TC-F01 after" in html
    assert "TC-F02 log" in html
    assert "logs/iter-1/TC-F01-screen.png" in html
    assert "logs/iter-1/TC-F02.log" in html


def test_generate_html_report_promotes_machine_anchor_critical_artifacts(tmp_path: Path) -> None:
    task = tmp_path / "html_report_critical_artifacts"
    runtime_dir = task / "logs" / "iter-2" / "runtime"
    runtime_dir.mkdir(parents=True)
    (task / "Requirements.md").write_text("# Requirements\n\n### R01\n- **AC-001**: stop event\n")
    (runtime_dir / "music-events.txt").write_text("music_audio_stop stop_reason=click_next\n")
    (runtime_dir / "logcat-stream.txt").write_text("raw logcat\n")
    (runtime_dir / "TC-001-screen.png").write_bytes(b"fake-png")
    (task / "logs" / "iter-2" / "runtime-evidence.md").write_text("runtime proof\n")
    (task / "VerificationLedger.json").write_text("{}")
    (task / "completion-report.json").write_text("{}")
    (task / "summary.md").write_text("# Summary\n")
    (task / "evaluation.json").write_text(json.dumps({
        "iteration": 2,
        "result": "pass",
        "summary": "Runtime proof passed",
        "nextAction": "finish",
        "testResults": [
            {
                "testCaseId": "TC-001",
                "result": "pass",
                "acceptanceCriteria": ["AC-001"],
                "evidence": [
                    "logs/iter-2/runtime/TC-001-screen.png",
                    "logs/iter-2/runtime-evidence.md",
                    "logs/iter-2/runtime/logcat-stream.txt",
                    "logs/iter-2/runtime/music-events.txt",
                ],
                "evidenceAssessment": {
                    "verdict": "proved",
                    "machineAnchor": "music-events.txt contains music_audio_stop with stop_reason=click_next",
                    "hardMetrics": [
                        {
                            "name": "music_audio_stop_click_next_event_present",
                            "passed": True,
                            "evidence": "logs/iter-2/runtime/music-events.txt",
                            "anchor": "stop_reason=click_next",
                        }
                    ],
                },
            }
        ],
        "failedChecks": [],
    }))

    report = generate_html_report_for_task_dir(task, "html_report_critical_artifacts")
    html = report.read_text()
    assert "Critical Artifacts to Review" not in html
    assert "Critical artifact: Runtime proof" in html
    assert "Key Evidence" in html
    assert "All linked artifacts" in html
    assert "logs/iter-2/runtime/music-events.txt" in html
    assert "logs/iter-2/runtime/TC-001-screen.png" in html
    assert "stop_reason=click_next" in html
    assert "No screenshot linked for this TC" not in html


def test_generate_html_report_overview_has_iterations_testcases_and_active_duration(tmp_path: Path) -> None:
    task = tmp_path / "html_report_overview"
    (task / "logs" / "iter-1").mkdir(parents=True)
    (task / "logs" / "iter-2").mkdir(parents=True)
    (task / "Requirements.md").write_text("# Requirements\n")
    (task / "evaluation.json").write_text(json.dumps({
        "iteration": 2,
        "result": "pass",
        "summary": "two iterations",
        "nextAction": "finish",
        "failedChecks": [],
        "testResults": [
            {"testCaseId": "TC-F01", "result": "pass", "reason": "ok"},
            {"testCaseId": "TC-F02", "result": "pass", "reason": "ok"},
            {"testCaseId": "TC-F03", "result": "fail", "reason": "no"},
        ],
    }))
    (task / "runtime-state.json").write_text(json.dumps({"status": "finished", "iteration": 2, "nextAction": "finish"}))
    # Events log: a wait_for_user gap (excluded) and an active gap (counted).
    events = [
        {"id": "evt_000001", "at": "2026-06-15T10:00:00", "action": "run_generator", "status": "running"},
        # waiting on the user from 10:00:10 .. 10:05:10 (300s) must be excluded.
        {"id": "evt_000002", "at": "2026-06-15T10:00:10", "action": "wait_for_user", "status": "waiting_user"},
        {"id": "evt_000003", "at": "2026-06-15T10:05:10", "action": "run_evaluation", "status": "running"},
        {"id": "evt_000004", "at": "2026-06-15T10:05:40", "action": "finish_task", "status": "completed"},
    ]
    (task / "automind-workflow-events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n"
    )

    report = generate_html_report_for_task_dir(task, "html_report_overview")
    html = report.read_text()
    assert "Iterations" in html
    assert "TestCases passed" in html
    assert "2 / 3" in html  # 2 passed of 3 recorded
    assert "Active duration" in html
    # Total span 10:00:00..10:05:40 = 340s; waiting 300s -> active 40s.
    assert "40s" in html
    assert "total 5m 40s" in html
    assert "waiting 5m 0s" in html
