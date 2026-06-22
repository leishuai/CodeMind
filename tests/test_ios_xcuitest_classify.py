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
