"""Lightweight request/content classification helpers for AutoMind.

These helpers intentionally stay generic and product-agnostic. They are
shared by scaffolding and workflow gates to avoid business-specific rules in
the CLI entrypoint.
"""
from __future__ import annotations


def has_negative_mobile_device_signal(text: str) -> bool:
    """Return whether text explicitly says mobile/device verification is not required."""
    lower = (text or "").lower()
    return any(phrase in lower for phrase in [
        "without requiring ios/android",
        "do not require android/ios",
        "avoids mobile devices",
        "not android/ios capability",
        "no mobile device",
        "no device",
    ]) or any(phrase in (text or "") for phrase in [
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
    ])
