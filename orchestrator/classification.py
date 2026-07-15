"""Lightweight request/content classification helpers for CodeMind.

These helpers intentionally stay generic and product-agnostic. They are
shared by scaffolding and workflow gates to avoid business-specific rules in
the CLI entrypoint.

Every classification exposes a ``triageSource`` field so callers can tell
apart deterministic patterns (code-decided) from ambiguous signals that the
model/human must review.
"""
from __future__ import annotations


# Phrases that explicitly negate the need for mobile/device verification.
_NEGATIVE_PHRASES_LOWER = [
    "without requiring ios/android",
    "do not require android/ios",
    "avoids mobile devices",
    "not android/ios capability",
    "no mobile device",
    "no device",
]
_NEGATIVE_PHRASES_CASE_SENSITIVE = [
    "不需要Android/iOS",
    "不需要安卓/iOS",
    "不需要安卓",
    "不需要iOS",
    "无需安卓",
    "无需iOS",
    "不依赖安卓",
    "不依赖iOS",
    "不需要移动设备",
    "不需要设备",
    "避免移动设备",
]

# Phrases that explicitly signal mobile/device verification is wanted.
_POSITIVE_PHRASES_LOWER = [
    "real device",
    "on device",
    "with device",
    "run on android",
    "run on ios",
    "run on the device",
    "actual device",
    "physical device",
    "真机",
    "模拟器",
    "安卓",
    "android",
    "ios",
]


def _matched(phrase: str, text: str, text_lower: str) -> str | None:
    """Return the matched phrase if present in text, else None."""
    if phrase in _NEGATIVE_PHRASES_LOWER and phrase in text_lower:
        return phrase
    if phrase in _NEGATIVE_PHRASES_CASE_SENSITIVE and phrase in text:
        return phrase
    return None


def classify_mobile_signal(text: str) -> dict:
    """Classify text's mobile-device intent with model-first triage.

    Returns
    -------
    dict with the following fields:
    - ``explicitlyDisabled``: bool — text clearly says mobile/device
      verification is not required.
    - ``explicitlyEnabled``: bool — text clearly says mobile/device
      verification is required.
    - ``matchedPhrases``: list[str] — deterministic phrases matched.
    - ``triageSource``: str — ``"code_deterministic"`` when a clear
      positive or negative phrase was matched; ``"requires_model_review"``
      when no phrase matches so the caller should route to the model for
      a contextual decision.
    - ``needsModelReview``: bool — True when the code signal is weak/
      absent and the model/human must decide.
    - ``summary``: str — short human-readable note.

    Notes
    -----
    The classifier is conservative: it only returns a code-deterministic
    result for explicit yes/no phrases. Ambiguous text (mentions of
    "app", "ui", "page" without an explicit device signal) surfaces
    ``needsModelReview=True`` so the planner can weigh the decision.
    """
    raw = text or ""
    text_lower = raw.lower()

    matched: list[str] = []
    for phrase in _NEGATIVE_PHRASES_LOWER:
        if phrase in text_lower:
            matched.append(phrase)
    for phrase in _NEGATIVE_PHRASES_CASE_SENSITIVE:
        if phrase in raw:
            matched.append(phrase)

    if matched:
        return {
            "explicitlyDisabled": True,
            "explicitlyEnabled": False,
            "matchedPhrases": matched,
            "triageSource": "code_deterministic",
            "needsModelReview": False,
            "summary": "text explicitly says mobile/device verification is not required",
        }

    # Explicit device / platform affirmative phrases are code-deterministic
    # only when they appear together with a concrete intent word such as
    # "test", "verify", "run", "launch", "build" — otherwise a mention of
    # "android" alone does not mean mobile verification is requested.
    intent_lower = text_lower
    has_intent = any(
        token in intent_lower
        for token in ["test", "verify", "run", "launch", "build", "install", "打包", "测试", "验证", "启动", "安装"]
    )
    positive_matches: list[str] = []
    for phrase in _POSITIVE_PHRASES_LOWER:
        if phrase in text_lower:
            positive_matches.append(phrase)

    if positive_matches and has_intent:
        return {
            "explicitlyDisabled": False,
            "explicitlyEnabled": True,
            "matchedPhrases": positive_matches,
            "triageSource": "code_deterministic",
            "needsModelReview": False,
            "summary": "text explicitly asks for device/mobile verification",
        }

    return {
        "explicitlyDisabled": False,
        "explicitlyEnabled": False,
        "matchedPhrases": positive_matches or [],
        "triageSource": "requires_model_review",
        "needsModelReview": True,
        "summary": "no explicit mobile-device signal found; let the model decide from context",
    }


def has_negative_mobile_device_signal(text: str) -> bool:
    """Return whether text explicitly says mobile/device verification is not required.

    This is the legacy boolean API kept for backward compatibility. New
    callers should prefer :func:`classify_mobile_signal` for the richer
    triage information.
    """
    result = classify_mobile_signal(text)
    return bool(result.get("explicitlyDisabled"))
