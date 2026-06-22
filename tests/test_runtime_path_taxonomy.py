from __future__ import annotations

import json
from pathlib import Path

from orchestrator.knowledge_index import render_phase_reuse
from orchestrator.runtime_paths import (
    extract_runtime_path_attempts,
    format_failed_runtime_paths_section,
    normalize_failure_class,
    runtime_path_workflow_warnings,
)


def _write_eval(task_dir: Path, data: dict) -> None:
    (task_dir / "evaluation.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def test_runtime_failure_class_aliases_and_unknown() -> None:
    assert normalize_failure_class("wrong-surface-clicked") == "wrong_surface_or_target"
    assert normalize_failure_class("entered_but_no_playback") == "entered_but_no_actionable_state"
    assert normalize_failure_class("project_specific_weird") == "unknown"


def test_extract_runtime_path_attempts_from_evaluation() -> None:
    evaluation = {
        "testResults": [
            {
                "testCaseId": "TC-F01",
                "result": "blocked",
                "runtimePath": "ios.deep_link.history",
                "failureClass": "proof_mismatch",
                "observedSignals": {"related_event": 1, "required_event": 0},
                "shouldRetry": False,
                "retryAdvice": "change trigger",
            }
        ]
    }

    attempts = extract_runtime_path_attempts(evaluation)

    assert attempts == [{
        "runtimePath": "ios.deep_link.history",
        "failureClass": "proof_mismatch",
        "result": "blocked",
        "testCaseId": "TC-F01",
        "observedSignals": {"related_event": 1, "required_event": 0},
        "retryAdvice": "change trigger",
        "shouldRetry": False,
        "evidence": None,
    }]


def test_phase_reuse_includes_recent_failed_runtime_paths_without_knowledge_matches(tmp_path: Path) -> None:
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    _write_eval(task_dir, {
        "testResults": [{
            "testCaseId": "TC-F02",
            "result": "fail",
            "runtimePath": "web.checkout.submit",
            "failureClass": "action_failed",
            "observedSignals": {"clicked": True, "order_created": False},
            "retryAdvice": "try API-backed fixture or tighter selector",
        }]
    })

    text = render_phase_reuse(task_dir, "generator", [])

    assert "Recent runtime paths to avoid or change" in text
    assert "web.checkout.submit" in text
    assert "action_failed" in text
    assert "order_created=False" in text


def test_runtime_path_workflow_warning_for_low_value_repeat() -> None:
    evaluation = {
        "testResults": [{
            "testCaseId": "TC-F01",
            "result": "partial",
            "runtimePath": "ios.music_record.history",
            "failureClass": "proof_mismatch",
            "observedSignals": {"music_audio_finish": 1, "music_audio_stop": 0},
        }]
    }

    warnings = runtime_path_workflow_warnings(
        Path("/tmp/task01"),
        evaluation,
        plan_text="Retry ios.music_record.history once more",
        delivery_text="",
    )

    assert len(warnings) == 1
    assert "runtime_path_repeat_risk" in warnings[0]
    assert "proof_mismatch" in warnings[0]


def test_runtime_path_workflow_warning_suppressed_when_override_documented() -> None:
    evaluation = {
        "testResults": [{
            "testCaseId": "TC-F01",
            "result": "partial",
            "runtimePath": "ios.music_record.history",
            "failureClass": "proof_mismatch",
        }]
    }

    warnings = runtime_path_workflow_warnings(
        Path("/tmp/task01"),
        evaluation,
        plan_text="Retry ios.music_record.history with changed selector and overrideReason: mini player is now visible",
        delivery_text="",
    )

    assert warnings == []


def test_failed_runtime_paths_section_is_empty_without_runtime_shape() -> None:
    assert format_failed_runtime_paths_section({"testResults": [{"testCaseId": "TC-F01", "result": "fail"}]}) == ""
