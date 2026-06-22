from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path("scripts").resolve()))

from probe_flow_screenshots import (  # noqa: E402
    ensure_per_tc_default_screenshots,
    step_tc_ids,
)


def test_step_tc_ids_dedup_and_order() -> None:
    assert step_tc_ids({"tc": "TC-01"}) == ["TC-01"]
    assert step_tc_ids({"testCaseId": "TC-02"}) == ["TC-02"]
    assert step_tc_ids({"testCaseIds": ["TC-03", "TC-03", "TC-04"]}) == ["TC-03", "TC-04"]
    assert step_tc_ids({"tc": "TC-05", "testCaseIds": ["TC-05", "TC-06"]}) == ["TC-05", "TC-06"]
    assert step_tc_ids({"name": "no tc"}) == []


def test_marks_one_screenshot_per_tc_on_assertion_step() -> None:
    steps = [
        {"type": "tap", "tc": "TC-01"},
        {"type": "assert_exists", "tc": "TC-01"},
        {"type": "stop", "tc": "TC-01"},
    ]
    marked = ensure_per_tc_default_screenshots(steps)
    assert marked == 1
    # Prefer the assertion step (verified end state) over tap/stop.
    assert steps[1]["screenshotAfter"] is True
    assert steps[1]["screenshotReason"] == "per_tc_default"
    assert "screenshotAfter" not in steps[0]
    assert "screenshotAfter" not in steps[2]


def test_falls_back_to_action_step_when_no_assertion() -> None:
    steps = [
        {"type": "input", "tc": "TC-02"},
        {"type": "tap", "tc": "TC-02"},
        {"type": "wait", "tc": "TC-02"},
    ]
    assert ensure_per_tc_default_screenshots(steps) == 1
    # Last action step (tap) gets the marker; wait is skipped.
    assert steps[1]["screenshotAfter"] is True
    assert "screenshotAfter" not in steps[0]
    assert "screenshotAfter" not in steps[2]


def test_skips_tc_group_that_already_has_screenshot() -> None:
    steps = [
        {"type": "tap", "tc": "TC-03", "screenshotAfter": True},
        {"type": "assert_exists", "tc": "TC-03"},
    ]
    assert ensure_per_tc_default_screenshots(steps) == 0
    assert "screenshotAfter" not in steps[1]


def test_critical_marker_counts_as_existing_screenshot() -> None:
    steps = [{"type": "tap", "tc": "TC-04", "critical": True}]
    assert ensure_per_tc_default_screenshots(steps) == 0


def test_untagged_steps_are_left_untouched() -> None:
    steps = [{"type": "tap"}, {"type": "assert_exists"}]
    assert ensure_per_tc_default_screenshots(steps) == 0
    assert all("screenshotAfter" not in step for step in steps)


def test_multiple_tc_groups_each_get_one_screenshot() -> None:
    steps = [
        {"type": "tap", "tc": "TC-05"},
        {"type": "assert_text", "tc": "TC-05"},
        {"type": "tap", "tc": "TC-06"},
        {"type": "assert_exists", "tc": "TC-06"},
    ]
    assert ensure_per_tc_default_screenshots(steps) == 2
    assert steps[1]["screenshotAfter"] is True
    assert steps[3]["screenshotAfter"] is True


def test_group_with_only_skip_types_uses_last_step() -> None:
    steps = [{"type": "wait", "tc": "TC-07"}, {"type": "stop", "tc": "TC-07"}]
    # No preferred/action step; falls back to last non-skip, then last step.
    assert ensure_per_tc_default_screenshots(steps) == 1
    assert steps[-1]["screenshotAfter"] is True


def test_non_list_input_returns_zero() -> None:
    assert ensure_per_tc_default_screenshots(None) == 0  # type: ignore[arg-type]
    assert ensure_per_tc_default_screenshots({}) == 0  # type: ignore[arg-type]
