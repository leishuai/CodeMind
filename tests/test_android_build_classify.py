"""Android/Kotlin build-failure classification.

Sedimented from a real ExampleApp Android task: a large set of Kotlin unresolved
references pointing at a module that still compiles on its own was a stale
module build output / classpath cache, not a source defect. The classifier must
steer the retry toward an upstream/downstream build-output reset + --rerun-tasks
instead of letting Generator start editing source. A missing Gradle task/flavor
is a verification-command error, not a product-code failure.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load(module_name: str, rel_path: str):
    sys.path.insert(0, str(Path("scripts").resolve()))
    spec = importlib.util.spec_from_file_location(module_name, Path(rel_path))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _classifier():
    return _load("failure_classifier", "scripts/failure_classifier.py")


def test_kotlin_unresolved_references_routes_to_stale_cache_reset() -> None:
    fc = _classifier()
    log = (
        "> Task :business:music:music_impl:compileFmDebugKotlinAndroid FAILED\n"
        "e: SomeFile.kt: (12, 34): unresolved reference: BEHAVIOR_DOWNLOAD\n"
        "e: Other.kt: (5, 6): unresolved reference: applyButtonCornerStyleForLite\n"
    )
    c = fc.classify("android", "build", log)
    assert c.category == "build_failure"
    assert c.nextAction == "retry_generator"
    assert c.sameProblemKey == "android.build.kotlin_unresolved_stale_cache"
    assert "--rerun-tasks" in c.reason
    assert "stale" in c.reason.lower()


def test_kotlin_unresolved_matches_on_compilekotlin_signal_without_android_platform() -> None:
    fc = _classifier()
    log = "compileKotlin FAILED\ne: unresolved reference: takeAsArgs\n"
    c = fc.classify("generic", "build", log)
    assert c.sameProblemKey == "android.build.kotlin_unresolved_stale_cache"
    assert c.nextAction == "retry_generator"


def test_gradle_task_not_found_is_verifier_command_misconfig() -> None:
    fc = _classifier()
    log = "Cannot locate tasks that match ':app:assembleLiteDebug' as task 'assembleLiteDebug' not found"
    c = fc.classify("android", "build", log)
    assert c.category == "verifier_command_misconfigured"
    assert c.nextAction == "retry_generator"
    assert c.retryableBy == "verifier"
    assert c.sameProblemKey == "android.build.gradle_task_not_found"


def test_plain_android_build_failure_still_falls_back() -> None:
    fc = _classifier()
    c = fc.classify("android", "build", "build failed: duplicate class found")
    assert c.category == "build_failure"
    assert c.sameProblemKey == "android.build.failure"
