from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    sys.path.insert(0, str(Path("orchestrator").resolve()))
    path = Path("orchestrator/context_packs.py")
    spec = importlib.util.spec_from_file_location("context_packs", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_signals_when_evaluation_empty() -> None:
    module = _load_module()
    assert module._collect_model_review_signals(None) == []
    assert module._collect_model_review_signals({}) == []


def test_needs_model_review_entries_are_collected() -> None:
    module = _load_module()
    evaluation = {
        "failedChecks": [
            {
                "name": "hollow pass",
                "category": "hollow_pass",
                "needsModelReview": True,
                "triageSource": "requires_model_review",
                "reason": "only placeholder tests ran",
                "confidence": "high",
                "sameProblemKey": "ios.xcuitest.hollow_pass",
                "recoveryAction": "fix the runner assembly",
                "evidence": ["logs/iter-1/ios-pass-substance.json"],
            },
            {
                "name": "build failed",
                "category": "build_failed",
                "needsModelReview": False,
                "triageSource": "code_deterministic",
                "reason": "compilation error",
            },
        ],
    }
    signals = module._collect_model_review_signals(evaluation)
    assert len(signals) == 1
    sig = signals[0]
    assert sig["source"] == "failedChecks[0]"
    assert sig["triageSource"] == "requires_model_review"
    assert sig["confidence"] == "high"
    assert sig["sameProblemKey"] == "ios.xcuitest.hollow_pass"


def test_repeated_failure_same_problem_key_auto_elevated() -> None:
    """Same sameProblemKey appearing >= 2 times across checks in one round
    gets auto-elevated to a model-review signal so the loop doesn't keep
    retrying the same misclassified fix."""
    module = _load_module()
    evaluation = {
        "failedChecks": [
            {
                "name": "build fail 1",
                "category": "pod_install_failed",
                "triageSource": "code_deterministic",
                "needsModelReview": False,
                "sameProblemKey": "ios.build.cocoapods.repo_outdated",
                "recoveryAction": "pod repo update",
                "reason": "pod not found",
            },
            {
                "name": "build fail 2",
                "category": "pod_install_failed",
                "triageSource": "code_deterministic",
                "needsModelReview": False,
                "sameProblemKey": "ios.build.cocoapods.repo_outdated",
                "recoveryAction": "pod install --repo-update",
                "reason": "pod still not found after repo update",
            },
        ],
    }
    signals = module._collect_model_review_signals(evaluation)
    assert len(signals) == 1
    sig = signals[0]
    assert sig["source"].startswith("repeated_failure:")
    assert sig["sameProblemKey"] == "ios.build.cocoapods.repo_outdated"
    assert sig["occurrenceCount"] == 2
    assert sig["confidence"] == "medium"
    assert sig["autoElevated"] is True
    assert sig["triageSource"] == "requires_model_review"


def test_single_occurrence_not_auto_elevated() -> None:
    module = _load_module()
    evaluation = {
        "failedChecks": [
            {
                "name": "build fail",
                "category": "pod_install_failed",
                "triageSource": "code_deterministic",
                "needsModelReview": False,
                "sameProblemKey": "ios.build.cocoapods.repo_outdated",
                "reason": "pod not found",
            },
        ],
    }
    signals = module._collect_model_review_signals(evaluation)
    assert len(signals) == 0


def test_already_has_needs_model_review_not_double_counted() -> None:
    """If a sameProblemKey already has an explicit needsModelReview entry,
    don't add a redundant repeated_failure signal."""
    module = _load_module()
    evaluation = {
        "failedChecks": [
            {
                "name": "fail 1",
                "category": "hollow_pass",
                "needsModelReview": True,
                "triageSource": "requires_model_review",
                "sameProblemKey": "ios.xcuitest.hollow_pass",
                "reason": "only placeholders",
            },
            {
                "name": "fail 2",
                "category": "hollow_pass",
                "needsModelReview": False,
                "triageSource": "code_deterministic",
                "sameProblemKey": "ios.xcuitest.hollow_pass",
                "reason": "still only placeholders",
            },
        ],
    }
    signals = module._collect_model_review_signals(evaluation)
    assert len(signals) == 1
    assert signals[0]["source"] == "failedChecks[0]"


def test_quality_checks_needs_model_review_collected() -> None:
    module = _load_module()
    evaluation = {
        "qualityChecks": [
            {
                "id": "q1",
                "result": "warn",
                "needsModelReview": True,
                "triageSource": "requires_model_review",
                "failureClass": "soft_crash_keyword",
                "reason": "found 'crash' keyword in log",
                "evidence": ["logs/iter-1/xcodebuild-ui-test.log"],
            },
        ],
    }
    signals = module._collect_model_review_signals(evaluation)
    assert len(signals) == 1
    sig = signals[0]
    assert sig["source"] == "qualityChecks[0]"
    assert sig["failureClass"] == "soft_crash_keyword"


def test_model_review_signals_top_level_collected() -> None:
    module = _load_module()
    evaluation = {
        "modelReviewSignals": {
            "signals": [
                {"id": "s1", "reason": "loop exit reason unknown"},
            ],
        },
    }
    signals = module._collect_model_review_signals(evaluation)
    assert len(signals) == 1
    assert signals[0]["source"] == "modelReviewSignals"


def _make_task_dir(tmp_path: Path, *, completion_check: str | None = None, workflow_check: str | None = None) -> Path:
    import json as _json
    task_dir = tmp_path / "tasks" / "test-task"
    task_dir.mkdir(parents=True, exist_ok=True)
    state: dict = {}
    if completion_check is not None:
        state["completionCheck"] = completion_check
    if workflow_check is not None:
        state["workflowCheck"] = workflow_check
    (task_dir / "runtime-state.json").write_text(_json.dumps(state, indent=2))
    return task_dir


def test_gate_failure_no_signal_before_iteration_2(tmp_path: Path) -> None:
    module = _load_module()
    task_dir = _make_task_dir(tmp_path, completion_check="fail", workflow_check="fail")
    signals = module._collect_gate_failure_signals(task_dir, iteration=0)
    assert signals == []
    signals = module._collect_gate_failure_signals(task_dir, iteration=1)
    assert signals == []


def test_gate_failure_completion_check_triggers_at_iteration_2(tmp_path: Path) -> None:
    module = _load_module()
    task_dir = _make_task_dir(tmp_path, completion_check="fail")
    signals = module._collect_gate_failure_signals(task_dir, iteration=2)
    assert len(signals) == 1
    sig = signals[0]
    assert sig["source"] == "gate_failure:completion_check"
    assert sig["gateType"] == "completion_check"
    assert sig["category"] == "gate_blocked"
    assert sig["confidence"] == "medium"
    assert sig["autoElevated"] is True
    assert sig["triageSource"] == "requires_model_review"
    assert "completion check" in sig["reason"].lower()


def test_gate_failure_workflow_check_triggers_at_iteration_2(tmp_path: Path) -> None:
    module = _load_module()
    task_dir = _make_task_dir(tmp_path, workflow_check="fail")
    signals = module._collect_gate_failure_signals(task_dir, iteration=3)
    assert len(signals) == 1
    sig = signals[0]
    assert sig["source"] == "gate_failure:workflow_check"
    assert sig["gateType"] == "workflow_check"
    assert sig["category"] == "gate_blocked"
    assert sig["autoElevated"] is True


def test_gate_failure_both_gates_fail(tmp_path: Path) -> None:
    module = _load_module()
    task_dir = _make_task_dir(tmp_path, completion_check="fail", workflow_check="fail")
    signals = module._collect_gate_failure_signals(task_dir, iteration=5)
    assert len(signals) == 2
    sources = {s["source"] for s in signals}
    assert "gate_failure:completion_check" in sources
    assert "gate_failure:workflow_check" in sources


def test_gate_failure_pass_does_not_trigger(tmp_path: Path) -> None:
    module = _load_module()
    task_dir = _make_task_dir(tmp_path, completion_check="pass", workflow_check="pass")
    signals = module._collect_gate_failure_signals(task_dir, iteration=5)
    assert signals == []


def test_gate_failure_missing_state(tmp_path: Path) -> None:
    module = _load_module()
    task_dir = tmp_path / "tasks" / "empty-task"
    task_dir.mkdir(parents=True, exist_ok=True)
    signals = module._collect_gate_failure_signals(task_dir, iteration=3)
    assert signals == []


def test_all_failure_overview_empty() -> None:
    module = _load_module()
    assert module._collect_all_failure_overview(None) == []
    assert module._collect_all_failure_overview({}) == []


def test_all_failure_overview_collects_all_failed_checks() -> None:
    module = _load_module()
    evaluation = {
        "failedChecks": [
            {
                "name": "build failed",
                "category": "build_failed",
                "triageSource": "code_deterministic",
                "needsModelReview": False,
                "recoveryAction": "fix compilation error",
                "sameProblemKey": "ios.build.compilation_error",
                "reason": "compilation error at line 42",
                "result": "fail",
            },
            {
                "name": "hollow pass",
                "category": "hollow_pass",
                "triageSource": "requires_model_review",
                "needsModelReview": True,
                "recoveryAction": "fix runner assembly",
                "reason": "only placeholder tests",
                "result": "fail",
            },
        ],
    }
    failures = module._collect_all_failure_overview(evaluation)
    assert len(failures) == 2
    code_classified = [f for f in failures if not f["needsModelReview"]]
    model_pending = [f for f in failures if f["needsModelReview"]]
    assert len(code_classified) == 1
    assert code_classified[0]["name"] == "build failed"
    assert code_classified[0]["category"] == "build_failed"
    assert code_classified[0]["sameProblemKey"] == "ios.build.compilation_error"
    assert len(model_pending) == 1
    assert model_pending[0]["name"] == "hollow pass"


def test_all_failure_overview_quality_warn_and_fail() -> None:
    module = _load_module()
    evaluation = {
        "qualityChecks": [
            {
                "id": "q1",
                "result": "warn",
                "triageSource": "code_deterministic",
                "needsModelReview": False,
                "failureClass": "soft_timeout_keyword",
                "reason": "timeout-like pattern found",
            },
            {
                "id": "q2",
                "result": "pass",
                "triageSource": "code_deterministic",
                "needsModelReview": False,
                "failureClass": "architecture_ok",
                "reason": "architecture looks fine",
            },
        ],
    }
    failures = module._collect_all_failure_overview(evaluation)
    assert len(failures) == 1
    assert failures[0]["source"] == "qualityChecks[0]"
    assert failures[0]["result"] == "warn"


def test_all_failure_overview_skips_non_dict_entries() -> None:
    module = _load_module()
    evaluation = {
        "failedChecks": [
            "not a dict",
            {"name": "real failure", "category": "build_failed", "triageSource": "code_deterministic"},
            None,
        ],
    }
    failures = module._collect_all_failure_overview(evaluation)
    assert len(failures) == 1
    assert failures[0]["name"] == "real failure"


def test_all_failure_overview_collects_test_results_failures() -> None:
    module = _load_module()
    evaluation = {
        "testResults": [
            {
                "id": "TC-01",
                "name": "test_login",
                "result": "fail",
                "triageSource": "code_deterministic",
                "needsModelReview": False,
                "category": "test_failed",
                "reason": "assertion failed",
            },
            {
                "id": "TC-02",
                "name": "test_logout",
                "result": "pass",
                "triageSource": "code_deterministic",
                "needsModelReview": False,
            },
            {
                "id": "TC-03",
                "name": "test_payment",
                "result": "blocked",
                "triageSource": "code_deterministic",
                "needsModelReview": True,
                "category": "environment_blocked",
                "verdictReason": "device not available",
            },
            {
                "id": "TC-04",
                "name": "test_dependency",
                "result": "skipped_dependency",
                "triageSource": "code_deterministic",
                "needsModelReview": False,
                "category": "dependency_skipped",
            },
        ],
    }
    failures = module._collect_all_failure_overview(evaluation)
    assert len(failures) == 3
    sources = {f["source"] for f in failures}
    assert sources == {"testResults[0]", "testResults[2]", "testResults[3]"}
    tc03 = next(f for f in failures if f["source"] == "testResults[2]")
    assert tc03["reason"] == "device not available"
    assert tc03["needsModelReview"] is True
