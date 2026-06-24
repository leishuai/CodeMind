from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_runner():
    sys.path.insert(0, str(Path("scripts").resolve()))
    path = Path("scripts/ios_xcuitest_runner.py")
    spec = importlib.util.spec_from_file_location("ios_xcuitest_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_root_install_style_is_runner_delivery_blocker_not_device_absence() -> None:
    """P0-6: the PoC disproved that test-without-building hits a Root-install
    *device* blocker on retail devices. The runner must delegate to the central
    classifier, which routes this as an external_runner delivery issue to
    Generator rather than collapsing it into mobile_device_unavailable."""
    runner = _load_runner()
    result, next_action, category, reason = runner.classify(65, """
    Signing Identity: Apple Development
    Provisioning Profile: iOS Team Provisioning Profile
    Root install style is not supported on this device
    To install internal content, the device must allow installing app bundles and roots.
    """)
    assert category == "external_runner_root_install_unsupported"
    assert next_action == "retry_generator"
    assert result == "fail"
    assert category != "mobile_device_unavailable"
    assert "dry-run" in reason
    assert "test-without-building" in reason
    assert "external UI runner" in reason
    assert "iOS Simulator" in reason


def test_ide_interface_capability_blocker_routes_to_replan() -> None:
    """The real dead end found in the PoC: runner started but no IDE-side helper
    implements XCTestManager_IDEInterface, so it disconnects."""
    runner = _load_runner()
    result, next_action, category, reason = runner.classify(1, """
    Runner started.
    channel canceled for XCTestManager_IDEInterface
    Exiting due to IDE disconnection
    """)
    assert category == "external_runner_capability_blocked"
    assert next_action == "replan"
    assert result == "blocked"


def test_runner_signing_blocker_first_tries_existing_material() -> None:
    """Request D: a runner code-signing failure should first try to re-sign with
    signing material that already exists in the project/machine (retry_generator)
    rather than immediately asking the user. The runner does not pass exhausted
    context, so it always takes this self-heal-first path."""
    runner = _load_runner()
    result, classification = runner.classify_detailed(1, """
    Code signing failed: errSecInternalComponent
    No valid signing identities found.
    """)
    assert classification.category == "external_runner_signing_blocked"
    assert classification.nextAction == "retry_generator"
    assert classification.sameProblemKey == "ios.xcuitest.runner.signing_use_existing"
    assert result == "fail"


def test_bootstrap_abort_routes_to_generator() -> None:
    runner = _load_runner()
    result, next_action, category, reason = runner.classify(1, """
    Cannot initiate shared session more than once
    """)
    assert category == "external_runner_bootstrap_abort"
    assert next_action == "retry_generator"
    assert result == "fail"


def test_clean_pass_is_finish() -> None:
    runner = _load_runner()
    result, next_action, category, reason = runner.classify(0, "Testing succeeded")
    assert result == "pass"
    assert next_action == "finish"


def _write_intent(task_dir: Path, iteration: int, steps: list) -> None:
    iter_dir = task_dir / "logs" / f"iter-{iteration}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (iter_dir / "ios-action-intent-summary.json").write_text(_json.dumps({
        "intent": {"goal": "verify music cover size", "steps": steps},
    }))


def test_hollow_pass_downgraded_when_only_placeholder_executed(tmp_path, monkeypatch) -> None:
    """A green xcodebuild that ran ONLY testExternalTargetLaunches while the
    round expected a multi-step product journey must be downgraded with a
    model-review signal instead of being trusted as a pass."""
    runner = _load_runner()
    task_dir = tmp_path / "task"
    _write_intent(task_dir, 1, steps=[{"type": "tap"}, {"type": "assert_exists"}])
    monkeypatch.setattr(runner, "extract_executed_tests", lambda _p: [
        {"name": "testExternalTargetLaunches()", "id": "X/testExternalTargetLaunches", "result": "Passed"},
    ])
    payload = runner.evaluate_pass_substance(task_dir, 1, task_dir / "TestResults.xcresult")
    assert payload is not None
    assert payload["category"] == "hollow_pass"
    assert payload["confidence"] == "high"
    assert payload["journey"]["stepCount"] == 2
    assert payload["needsModelReview"] is True
    assert payload["triageSource"] == "requires_model_review"
    assert payload["placeholderCount"] == 1
    assert payload["nonPlaceholderCount"] == 0


def test_project_native_template_hollow_pass_detected(tmp_path, monkeypatch) -> None:
    """Project-native test targets with only Xcode template testExample
    methods are also hollow when a business journey was expected."""
    runner = _load_runner()
    task_dir = tmp_path / "task"
    _write_intent(task_dir, 1, steps=[{"type": "tap"}, {"type": "input"}, {"type": "assert_exists"}])
    monkeypatch.setattr(runner, "extract_executed_tests", lambda _p: [
        {"name": "testExample()", "id": "X/testExample", "result": "Passed"},
        {"name": "testLaunchPerformance()", "id": "X/testLaunchPerformance", "result": "Passed"},
    ])
    payload = runner.evaluate_pass_substance(task_dir, 1, task_dir / "TestResults.xcresult")
    assert payload is not None
    assert payload["category"] == "hollow_pass"
    assert payload["confidence"] == "high"
    assert payload["placeholderCount"] == 2
    assert payload["nonPlaceholderCount"] == 0
    assert payload["needsModelReview"] is True


def test_substantive_pass_preserved_when_business_test_executed(tmp_path, monkeypatch) -> None:
    """If a real (non-placeholder) business test method executed, the pass stands."""
    runner = _load_runner()
    task_dir = tmp_path / "task"
    _write_intent(task_dir, 1, steps=[{"type": "tap"}])
    monkeypatch.setattr(runner, "extract_executed_tests", lambda _p: [
        {"name": "test_verify_music_cover_size()", "id": "X/test_verify", "result": "Passed"},
    ])
    assert runner.evaluate_pass_substance(task_dir, 1, task_dir / "TestResults.xcresult") is None


def test_suspicious_pass_when_few_tests_vs_many_steps(tmp_path, monkeypatch) -> None:
    """Expected many journey steps but only one non-placeholder test ran →
    medium-confidence signal for model review, not an outright fail."""
    runner = _load_runner()
    task_dir = tmp_path / "task"
    _write_intent(task_dir, 1, steps=[{"type": "tap"}, {"type": "input"}, {"type": "scroll"}, {"type": "assert_exists"}])
    monkeypatch.setattr(runner, "extract_executed_tests", lambda _p: [
        {"name": "testExternalTargetLaunches()", "id": "X/testExternalTargetLaunches", "result": "Passed"},
        {"name": "testSingleFlow()", "id": "X/testSingleFlow", "result": "Passed"},
    ])
    payload = runner.evaluate_pass_substance(task_dir, 1, task_dir / "TestResults.xcresult")
    assert payload is not None
    assert payload["category"] == "hollow_pass"
    assert payload["confidence"] == "medium"
    assert payload["needsModelReview"] is True
    assert payload["nonPlaceholderCount"] == 1
    assert payload["placeholderCount"] == 1


def test_launch_only_testcase_pass_preserved_without_intent(tmp_path, monkeypatch) -> None:
    """No probe-flow intent summary (e.g. a direct launch-only run) => no
    journey expected => a placeholder-only pass is preserved."""
    runner = _load_runner()
    task_dir = tmp_path / "task"
    (task_dir / "logs" / "iter-1").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runner, "extract_executed_tests", lambda _p: [
        {"name": "testExternalTargetLaunches()", "id": "X", "result": "Passed"},
    ])
    assert runner.evaluate_pass_substance(task_dir, 1, task_dir / "TestResults.xcresult") is None


def test_zero_executed_tests_with_journey_is_hollow_pass(tmp_path, monkeypatch) -> None:
    """Expected a journey but xcresult has zero readable test methods →
    high-confidence hollow pass signal (tool unreadable or target empty)."""
    runner = _load_runner()
    task_dir = tmp_path / "task"
    _write_intent(task_dir, 1, steps=[{"type": "tap"}, {"type": "assert_exists"}])
    monkeypatch.setattr(runner, "extract_executed_tests", lambda _p: [])
    payload = runner.evaluate_pass_substance(task_dir, 1, task_dir / "TestResults.xcresult")
    assert payload is not None
    assert payload["category"] == "hollow_pass"
    assert payload["confidence"] == "high"
    assert payload["needsModelReview"] is True
    assert payload["executedCount"] == 0


def test_placeholder_method_pattern_matching() -> None:
    """_is_placeholder_method covers exact match and common template prefixes."""
    runner = _load_runner()
    for name in [
        "testExternalTargetLaunches",
        "testExample",
        "testExampleWithSwiftConcurrency",
        "testLaunch",
        "testLaunchPerformance",
        "testSmokeBasic",
        "testTemplateFoo",
        "testPlaceholderBar",
    ]:
        assert runner._is_placeholder_method(name + "()") is True, f"expected placeholder: {name}"
    for name in [
        "test_verify_music_cover_size",
        "testMusicPlayerNextSong",
        "testLoginFlow",
        "testSomething",
    ]:
        assert runner._is_placeholder_method(name + "()") is False, f"expected non-placeholder: {name}"
