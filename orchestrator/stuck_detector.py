"""StuckDetector — detect repetitive failure loops in the CodeAutonomy harness.

Inspired by OpenHands StuckDetector. Uses a coarse signature
``(action_kind, error_class)`` rather than full observation hashing so that
genuine retries (different signing material, different test fixture, etc.)
do not get falsely classified as stuck.

Five modes (current implementation only the first is fully active; the others
are reserved for future probes integrating with the main loop):

- ``action_observation_repeat`` : same coarse signature ≥ N consecutive times
  (default N=8). This is the primary signal for "model thrashing on the same
  failure" and the most actionable.
- ``decision_pingpong``         : alternating decisions A/B/A/B ≥ N times.
  Reserved hook.
- ``no_tool_call_streak``       : N consecutive iterations producing zero tool
  calls. Reserved hook.
- ``identical_action_streak``   : same action_kind regardless of error class
  ≥ N times. Reserved hook.
- ``error_repeat``              : same error_class across different actions
  ≥ N times. Reserved hook.

Public API: ``StuckDetector().observe(signature) -> dict | None``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Threshold defaults, kept generous to avoid false positives.
ACTION_OBSERVATION_REPEAT_THRESHOLD = 8
DECISION_PINGPONG_THRESHOLD = 6
NO_TOOL_CALL_STREAK_THRESHOLD = 5
IDENTICAL_ACTION_STREAK_THRESHOLD = 10
ERROR_REPEAT_THRESHOLD = 5


@dataclass(frozen=True)
class StuckSignature:
    """Coarse signature for one loop iteration's outcome.

    Two iterations are considered "identical" iff both fields match.
    Use empty string for fields that do not apply (e.g. successful runs).
    """

    action_kind: str
    error_class: str

    def key(self) -> tuple[str, str]:
        return (self.action_kind, self.error_class)


class StuckDetector:
    """Stateful detector for repeated identical signatures."""

    def __init__(self, threshold: int = ACTION_OBSERVATION_REPEAT_THRESHOLD) -> None:
        self.threshold = int(threshold)
        self._last_key: Optional[tuple[str, str]] = None
        self._streak = 0

    def reset(self) -> None:
        self._last_key = None
        self._streak = 0

    def observe(self, signature: StuckSignature) -> Optional[dict]:
        """Record one observation. Return a stuck-report dict iff threshold reached.

        Report shape::

            {
              "mode": "action_observation_repeat",
              "evidence": {"signature": [action_kind, error_class], "count": N},
              "recommendedNextAction": "replan",
            }

        ``None`` means "no stuck condition detected yet".
        """
        key = signature.key()
        if key == self._last_key:
            self._streak += 1
        else:
            self._last_key = key
            self._streak = 1

        if self._streak >= self.threshold:
            return {
                "mode": "action_observation_repeat",
                "evidence": {
                    "signature": list(key),
                    "count": self._streak,
                    "threshold": self.threshold,
                },
                "triageSource": "requires_model_review",
                "needsModelReview": True,
                "reason": (
                    "相同粗签名连续出现 " + str(self._streak) + " 次 >= 阈值 "
                    + str(self.threshold) + "。阈值是启发式判断；"
                    "模型应重新分析此签名下最近几次迭代是否确实卡住，"
                    "而不是不同原因但归类相同的情况。"
                ),
                "recommendedNextAction": "replan",
            }
        return None
