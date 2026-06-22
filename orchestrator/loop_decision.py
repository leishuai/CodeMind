"""Loop decision classifier (R02/AC-003/AC-004).

Classify why a harness-loop iteration ended into:

- ``recoverable``  : transient cause; loop should ``continue`` and bump retry_count
- ``unrecoverable``: clean stop required (KeyboardInterrupt, completion, hard error)
- ``unknown``      : default conservative classification

Centralizing the decision lets ``run_harness_loop`` route 13+ ``return False``
sites consistently and lets us unit-test the policy without spinning up the full
loop. ``classify_loop_exit`` is pure: it never reads the filesystem.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Reasons known to be transient/external; loop should retry next iteration.
RECOVERABLE_REASONS: frozenset[str] = frozenset({
    "dependency_check_warning",
    "agent_unavailable",
    "agent_timeout",
    "network_timeout",
    "cli_crash",
    "build_failure_transient",
    "test_failure_transient",
    "probe_flow_failure",
    "signing_renew_pending",
    "platform_self_repair_inconclusive",
    "evaluator_evidence_only",
})

# Reasons that must stop the loop immediately.
UNRECOVERABLE_REASONS: frozenset[str] = frozenset({
    "keyboard_interrupt",
    "system_exit",
    "permission_denied",
    "unauthorized_destructive",
    "user_blocked",
    "completion_succeeded",
    "completion_check_failed_hard",
    "iteration_limit_exhausted",
})


@dataclass(frozen=True)
class LoopDecision:
    """Outcome of classifying one loop-exit attempt."""

    classification: str  # "recoverable" | "unrecoverable" | "unknown"
    should_continue: bool
    bump_retry: bool
    interrupted_by_user: bool
    reason: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "classification": self.classification,
            "shouldContinue": self.should_continue,
            "bumpRetry": self.bump_retry,
            "interruptedByUser": self.interrupted_by_user,
            "reason": self.reason,
            "detail": self.detail,
        }


def classify_loop_exit(
    reason: str,
    exception: Optional[BaseException] = None,
) -> LoopDecision:
    """Classify a loop-exit attempt.

    Parameters
    ----------
    reason
        Short identifier for why the iteration is exiting (e.g.
        ``"dependency_check_warning"``, ``"completion_succeeded"``). Empty
        string is allowed.
    exception
        Optional in-flight exception. ``KeyboardInterrupt`` / ``SystemExit``
        always classify as unrecoverable regardless of ``reason``.
    """
    reason_norm = (reason or "").strip().lower()

    # Highest priority: in-flight Python exceptions that must stop the loop.
    if isinstance(exception, KeyboardInterrupt):
        return LoopDecision(
            classification="unrecoverable",
            should_continue=False,
            bump_retry=False,
            interrupted_by_user=True,
            reason="keyboard_interrupt",
            detail=str(exception or ""),
        )
    if isinstance(exception, SystemExit):
        return LoopDecision(
            classification="unrecoverable",
            should_continue=False,
            bump_retry=False,
            interrupted_by_user=False,
            reason="system_exit",
            detail=str(exception or ""),
        )

    if reason_norm in RECOVERABLE_REASONS:
        return LoopDecision(
            classification="recoverable",
            should_continue=True,
            bump_retry=True,
            interrupted_by_user=False,
            reason=reason_norm,
        )
    if reason_norm in UNRECOVERABLE_REASONS:
        return LoopDecision(
            classification="unrecoverable",
            should_continue=False,
            bump_retry=False,
            interrupted_by_user=(reason_norm == "keyboard_interrupt"),
            reason=reason_norm,
        )
    return LoopDecision(
        classification="unknown",
        should_continue=False,
        bump_retry=False,
        interrupted_by_user=False,
        reason=reason_norm or "unspecified",
    )
