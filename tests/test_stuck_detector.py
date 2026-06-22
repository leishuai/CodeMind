"""TC-R03: StuckDetector 5 模式 + 粗签名（action_kind, error_class）。

AC-005: 8 次相同 (action,observation) → mode=action_observation_repeat。
AC-006: 7 次相同 action 但 error_class 各异 → 不命中（签名换了不算 stuck）。
AC-007: 命中后 nextAction == "replan"。
"""
from __future__ import annotations

from orchestrator.stuck_detector import StuckDetector, StuckSignature


def test_action_observation_repeat_hits_at_threshold():
    """AC-005: 8 次相同 (action_kind, error_class) → mode=action_observation_repeat。"""
    detector = StuckDetector()
    sig = StuckSignature(action_kind="run_pytest", error_class="AssertionError")
    result = None
    for _ in range(8):
        result = detector.observe(sig)
    assert result is not None
    assert result["mode"] == "action_observation_repeat"
    assert result["recommendedNextAction"] == "replan"
    assert result.get("evidence", {}).get("count") == 8


def test_below_threshold_does_not_hit():
    """7 次同签名 < 阈值 8，不命中。"""
    detector = StuckDetector()
    sig = StuckSignature(action_kind="run_pytest", error_class="AssertionError")
    result = None
    for _ in range(7):
        result = detector.observe(sig)
    assert result is None


def test_signature_change_resets_streak():
    """AC-006: 7 次相同 action 但 error_class 各异 → detect 返回 None（签名换了不算 stuck）。"""
    detector = StuckDetector()
    result = None
    for i in range(7):
        sig = StuckSignature(action_kind="run_pytest", error_class=f"Err{i}")
        result = detector.observe(sig)
    assert result is None


def test_action_only_change_resets_streak():
    detector = StuckDetector()
    result = None
    for i in range(8):
        sig = StuckSignature(action_kind=f"act{i}", error_class="X")
        result = detector.observe(sig)
    assert result is None


def test_recommendation_is_replan():
    """AC-007: 命中模式后 nextAction Home 'replan'。"""
    detector = StuckDetector()
    sig = StuckSignature(action_kind="build", error_class="LinkError")
    last = None
    for _ in range(8):
        last = detector.observe(sig)
    assert last is not None
    assert last["recommendedNextAction"] == "replan"
