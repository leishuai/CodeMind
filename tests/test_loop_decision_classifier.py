"""TC-R02: 主循环 13 处 return False 重排。

AC-003: dependency-check warning fixture → loop 不退出、retry_count 自增。
AC-004: KeyboardInterrupt → 立即返回 False 且 runtime-state 状态文件含 interrupted_by_user=true。
"""
from __future__ import annotations

from orchestrator.loop_decision import classify_loop_exit


def test_dependency_check_warning_is_recoverable():
    """AC-003: dependency-check warning → continue + bump retry。"""
    decision = classify_loop_exit("dependency_check_warning")
    assert decision.classification == "recoverable"
    assert decision.should_continue is True
    assert decision.bump_retry is True
    assert decision.interrupted_by_user is False


def test_keyboard_interrupt_is_unrecoverable_and_user_interrupted():
    """AC-004: KeyboardInterrupt → 立即停止 + interrupted_by_user=True。"""
    exc = KeyboardInterrupt()
    decision = classify_loop_exit("anything", exception=exc)
    assert decision.classification == "unrecoverable"
    assert decision.should_continue is False
    assert decision.interrupted_by_user is True
    assert decision.reason == "keyboard_interrupt"
    # runtime-state 序列化形式
    payload = decision.as_dict()
    assert payload["interruptedByUser"] is True


def test_completion_success_is_unrecoverable_clean_stop():
    decision = classify_loop_exit("completion_succeeded")
    assert decision.classification == "unrecoverable"
    assert decision.should_continue is False
    assert decision.interrupted_by_user is False


def test_unauthorized_destructive_must_not_continue():
    decision = classify_loop_exit("unauthorized_destructive")
    assert decision.classification == "unrecoverable"
    assert decision.should_continue is False


def test_unknown_reason_defaults_to_unrecoverable_safe():
    """Unknown reasons must NOT silently continue (fail closed)."""
    decision = classify_loop_exit("totally_made_up")
    assert decision.classification == "unknown"
    assert decision.should_continue is False
    assert decision.bump_retry is False


def test_six_recoverable_reasons_all_continue():
    """6 类 recoverable reason 全部 continue=True。"""
    reasons = [
        "agent_unavailable",
        "network_timeout",
        "cli_crash",
        "build_failure_transient",
        "test_failure_transient",
        "probe_flow_failure",
    ]
    for r in reasons:
        d = classify_loop_exit(r)
        assert d.should_continue is True, f"{r} should continue"
        assert d.bump_retry is True, f"{r} should bump retry"


def test_system_exit_is_unrecoverable():
    decision = classify_loop_exit("anything", exception=SystemExit(0))
    assert decision.classification == "unrecoverable"
    assert decision.should_continue is False
    assert decision.interrupted_by_user is False
