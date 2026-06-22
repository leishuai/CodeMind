from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_main():
    path = Path("orchestrator/main.py")
    spec = importlib.util.spec_from_file_location("automind_main", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_android_probe_flow_validation_includes_key_action_evidence(tmp_path: Path) -> None:
    main = _load_main()
    task_dir = tmp_path / "task"
    iter_log_dir = task_dir / "logs" / "iter-1"
    probe_dir = iter_log_dir / "probe-flow"
    probe_dir.mkdir(parents=True)
    (task_dir / "Validation.md").write_text("# Validation\n")
    for rel in [
        "evaluator.log",
        "commands.md",
        "env.json",
        "probe-flow/probe-flow-summary.json",
        "probe-flow/action-trace.jsonl",
    ]:
        (iter_log_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (iter_log_dir / rel).write_text("fixture")
    (task_dir / "evaluation.json").write_text("{}")

    summary = {
        "actionTrace": [
            {
                "stepIndex": 3,
                "type": "tap",
                "name": "Tap play button",
                "intent": "start playback",
                "selector": {"resource_id": "com.example.app:id/play_button"},
                "resolvedNode": {
                    "resource_id": "com.example.app:id/play_button",
                    "class": "android.widget.ImageView",
                    "bounds": [520, 1840, 620, 1940],
                },
                "evidenceAfter": {
                    "screenshot": "logs/iter-1/probe-flow/action-03-after.png",
                    "hierarchy": "logs/iter-1/probe-flow/action-03-after-hierarchy.xml",
                },
                "ok": True,
                "detail": "//*[@resource-id=\"com.example.app:id/play_button\"]",
            }
        ]
    }
    evaluation = {"result": "pass", "summary": "Android probe-flow runner passed", "nextAction": "finish", "failedChecks": []}

    main.append_android_probe_flow_validation(task_dir, 1, evaluation, iter_log_dir, summary)

    text = (task_dir / "Validation.md").read_text()
    assert "## Iteration 1 - Android Probe Flow" in text
    assert "### Client UI action evidence" in text
    assert "Tap play button" in text
    assert "resource_id=com.example.app:id/play_button" in text
    assert "after screenshot" in text
    assert "action-trace.jsonl" in text

    # Resume/idempotency: do not duplicate the same iteration section.
    main.append_android_probe_flow_validation(task_dir, 1, evaluation, iter_log_dir, summary)
    assert (task_dir / "Validation.md").read_text().count("## Iteration 1 - Android Probe Flow") == 1


def test_android_probe_summary_classifies_install_signature_conflict_as_blocked() -> None:
    main = _load_main()
    evaluation = main.probe_summary_to_evaluation({
        "result": "fail",
        "checks": [{
            "name": "install apk",
            "ok": False,
            "detail": "AdbInstallError('Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Package ai.openclaw.automind.demo signatures do not match previously installed version; ignoring!]')",
            "evidence": "step-1-failure-screenshot.png",
        }],
        "stepResults": [],
    }, 2, Path("logs/iter-2/probe-flow/probe-flow-summary.json"))
    assert evaluation["result"] == "blocked"
    assert evaluation["nextAction"] == "ask_user"
    assert evaluation["failedChecks"][0]["category"] == "environment_blocked"
    assert "different signature" in evaluation["failedChecks"][0]["reason"]


def test_android_probe_summary_pass_with_missing_strong_post_check_becomes_partial() -> None:
    main = _load_main()
    evaluation = main.probe_summary_to_evaluation({
        "result": "pass",
        "checks": [{"name": "startup", "ok": True, "detail": "ok"}],
        "stepResults": [],
        "postChecks": [{
            "type": "requires_refined_music_stop_flow",
            "strength": "strong",
            "expectedSignals": [
                "music playback action path selected",
                "music_audio_finish emitted",
                "stop_reason present in reporting/logcat/sink evidence",
            ],
            "observedSignals": [],
        }],
    }, 3, Path("logs/iter-3/probe-flow/probe-flow-summary.json"))
    assert evaluation["result"] == "partial"
    assert evaluation["nextAction"] == "retry_generator"
    assert evaluation["strongPostChecks"][0]["status"] == "missing"
    assert "music_audio_finish emitted" in evaluation["semanticVerdict"]["missingEvidence"]


def test_android_probe_summary_reads_missing_strong_post_checks_from_flow_file(tmp_path: Path) -> None:
    main = _load_main()
    flow_path = tmp_path / "probe-flow.android.json"
    flow_path.write_text(
        """
{
  "platform": "android",
  "postChecks": [
    {
      "type": "requires_refined_music_stop_flow",
      "strength": "strong",
      "expectedSignals": ["music_audio_finish emitted", "stop_reason present in reporting/logcat/sink evidence"],
      "observedSignals": []
    }
  ]
}
""".strip()
    )
    evaluation = main.probe_summary_to_evaluation({
        "result": "pass",
        "flow": str(flow_path),
        "checks": [{"name": "startup", "ok": True, "detail": "ok"}],
        "stepResults": [],
    }, 4, tmp_path / "logs" / "iter-4" / "probe-flow" / "probe-flow-summary.json")
    assert evaluation["result"] == "partial"
    assert evaluation["nextAction"] == "retry_generator"
    assert evaluation["strongPostChecks"][0]["type"] == "requires_refined_music_stop_flow"
    assert "stop_reason present in reporting/logcat/sink evidence" in evaluation["semanticVerdict"]["missingEvidence"]


def test_completion_gate_fails_when_strong_post_check_missing(tmp_path: Path) -> None:
    from orchestrator.completion import build_completion_report

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "TestCases.md").write_text(
        "| ID | Requirement | Type | RuntimeLevel | Required |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| TC-F04 | AC-004 | functional | runtime | yes |\n"
    )
    (task_dir / "Requirements.md").write_text("AC-004 runtime proof\n")
    (task_dir / "probe.json").write_text("{}")
    evaluation = {
        "iteration": 1,
        "result": "pass",
        "summary": "adapter passed startup only",
        "nextAction": "finish",
        "failedChecks": [],
        "testResults": [{
            "testCaseId": "TC-F04",
            "result": "pass",
            "required": True,
            "acceptanceCriteria": ["AC-004"],
            "evidence": ["probe.json"],
            "evidenceAssessment": {
                "verdict": "proved",
                "assessor": "deterministic_adapter",
                "hardMetrics": [{"name": "probe", "status": "pass", "evidence": "probe.json"}],
            },
        }],
        "strongPostChecks": [{
            "type": "requires_refined_music_stop_flow",
            "strength": "strong",
            "status": "missing",
            "missingSignals": ["music_audio_finish emitted", "stop_reason present in reporting/logcat/sink evidence"],
        }],
    }
    report, _enriched = build_completion_report(task_dir, evaluation, allow_synthesize_pass=False)
    assert report["result"] == "fail"
    assert any("strong post-check not proved" in item for item in report["issues"])
