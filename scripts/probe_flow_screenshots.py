#!/usr/bin/env python3
"""Per-TC default screenshot normalization shared by probe-flow runners.

User contract (Android/iOS/Web alike): every runtime/UI testcase referenced by
a probe-flow should capture at least one screenshot by default, so report.html
can show a per-TC screenshot and increase trust in the run. This is a best-
effort default, not a hard gate: flows without TC tags are left untouched and a
TC that already marks a screenshot step is never double-marked. No warning is
raised when a screenshot ends up missing (the user explicitly asked not to make
this strict).

The normalizer only sets ``screenshotAfter=True`` on one representative step per
TC group. Each platform runner's own capture logic already honors
``screenshotAfter`` / ``critical`` / ``evidence.screenshotAfter`` markers, so a
single normalization point keeps the three runners consistent.
"""

from __future__ import annotations

from typing import Any


def step_tc_ids(step: dict[str, Any]) -> list[str]:
    """Return the testcase ids a step is tagged with (dedup, order-preserving)."""
    raw: list[Any] = []
    for key in ("tc", "testCaseId", "testCaseIds"):
        value = step.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            raw.extend(value)
        else:
            raw.append(value)
    ids: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in ids:
            ids.append(text)
    return ids


def _step_marks_screenshot(step: dict[str, Any]) -> bool:
    if step.get("screenshotAfter") is True or step.get("critical") is True:
        return True
    evidence = step.get("evidence")
    if isinstance(evidence, dict) and evidence.get("screenshotAfter") is True:
        return True
    return step.get("type") == "screenshot"


# Step types that make a meaningful "after" screenshot. Prefer assertions (they
# capture the verified end state), then interactions; never anchor a screenshot
# on a teardown/stop step.
_PREFERRED_TYPES = ("assert_exists", "assert_text", "assert_state_change")
_ACTION_TYPES = ("tap", "tap_if_present", "tap_nth", "input", "swipe", "scroll", "scroll_until_text")
_SKIP_TYPES = {"stop", "install", "wait"}


def _pick_screenshot_step(group: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the best step within a TC group to attach a default screenshot."""
    for wanted in (_PREFERRED_TYPES, _ACTION_TYPES):
        # Iterate in reverse so the screenshot lands on the final matching step,
        # which best represents the TC's verified end state.
        for step in reversed(group):
            if step.get("type") in wanted:
                return step
    for step in reversed(group):
        if step.get("type") not in _SKIP_TYPES:
            return step
    return group[-1] if group else None


def ensure_per_tc_default_screenshots(steps: list[Any]) -> int:
    """Mark one default screenshot step per TC group, in place.

    Returns the number of TC groups that received a new default screenshot
    marker. Steps without a TC tag are left untouched. A TC group that already
    marks a screenshot anywhere is left untouched.
    """
    if not isinstance(steps, list):
        return 0
    groups: dict[str, list[dict[str, Any]]] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        for tc_id in step_tc_ids(step):
            groups.setdefault(tc_id, []).append(step)

    marked = 0
    for group in groups.values():
        if any(_step_marks_screenshot(step) for step in group):
            continue
        target = _pick_screenshot_step(group)
        if target is None:
            continue
        target["screenshotAfter"] = True
        target.setdefault("screenshotReason", "per_tc_default")
        marked += 1
    return marked
