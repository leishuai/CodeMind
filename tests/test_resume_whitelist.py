"""TC-R07: resume 白名单 6 类。

AC-014: 6 类 mock error → is_recoverable_external_interruption 返回 True。
AC-015: permission_denied → False。
"""
from __future__ import annotations

from orchestrator.resume import is_recoverable_external_interruption


def _eval_with(category: str) -> dict:
    return {
        "iteration": 1,
        "result": "fail",
        "failedChecks": [{"category": category, "detail": "fixture"}],
    }


def test_six_recoverable_categories_all_return_true():
    """AC-014: 6 类 recoverable category 全部返回 True。"""
    recoverable = [
        "agent_unavailable",
        "agent_timeout",
        "network_timeout",
        "cli_crash",
        "build_failure_transient",
        "test_failure_transient",
        "probe_flow_failure",
        "signing_renew_pending",
    ]
    results = []
    for cat in recoverable:
        ev = _eval_with(cat)
        ok = is_recoverable_external_interruption(ev)
        results.append((cat, ok))
        assert ok is True, f"category={cat} should be recoverable but got {ok}"
    # 至少覆盖 6 类（含原有 2 类共 8 类）
    assert sum(1 for _, ok in results if ok) >= 6


def test_permission_denied_returns_false():
    """AC-015: permission_denied → False（不能自动恢复）。"""
    ev = _eval_with("permission_denied")
    assert is_recoverable_external_interruption(ev) is False


def test_unauthorized_destructive_returns_false():
    """AC-015 衍生: 危险类不能自动恢复。"""
    ev = _eval_with("unauthorized_destructive")
    assert is_recoverable_external_interruption(ev) is False


def test_none_evaluation_returns_false():
    assert is_recoverable_external_interruption(None) is False
    assert is_recoverable_external_interruption({}) is False
