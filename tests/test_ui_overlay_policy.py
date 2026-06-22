from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path("scripts").resolve()))

from ui_overlay_policy import classify_overlay_candidate, evaluate_overlay_rules, policy_from_flow, rank_overlay_candidates  # noqa: E402


def _element(text: str, *, y: int = 100, clickable: bool = True) -> dict:
    return {
        "text": text,
        "clickable": clickable,
        "enabled": True,
        "bounds": [10, y, 110, y + 40],
        "center": {"x": 60, "y": y + 20},
    }


def _image_close_element(resource_id: str = "com.example:id/iv_close") -> dict:
    return {
        "text": "",
        "resourceId": resource_id,
        "className": "android.widget.ImageButton",
        "clickable": True,
        "enabled": True,
        "bounds": [980, 120, 1040, 180],
        "center": {"x": 1010, "y": 150},
    }


def test_safe_dismiss_labels_are_auto_unblockable() -> None:
    for label in ["知道了", "跳过", "×", "Not now", "Dismiss", "Cancel"]:
        result = classify_overlay_candidate(_element(label))
        assert result["allowed"] is True
        assert result["category"] == "safe_dismiss"


def test_sensitive_labels_are_not_auto_clicked_by_default() -> None:
    for label in ["允许访问通讯录", "同意隐私协议", "登录", "支付", "删除", "Allow Photos", "Continue", "Cancel order", "关闭账号"]:
        result = classify_overlay_candidate(_element(label))
        assert result["allowed"] is False
        assert result["category"] == "sensitive"


def test_generic_confirm_words_are_safe_without_sensitive_context() -> None:
    for label in ["确定", "OK", "Okay"]:
        result = classify_overlay_candidate(_element(label))
        assert result["allowed"] is True
        assert result["category"] == "safe_confirm"


def test_generic_confirm_words_block_when_surrounding_context_is_sensitive() -> None:
    result = classify_overlay_candidate(_element("OK"), {"contextTexts": ["删除当前账号？"]})
    assert result["allowed"] is False
    assert result["category"] == "sensitive"


def test_image_close_button_can_be_auto_unblocked() -> None:
    result = classify_overlay_candidate(_image_close_element())
    assert result["allowed"] is True
    assert result["category"] == "image_close_button"


def test_image_close_button_still_closes_sensitive_overlay() -> None:
    result = classify_overlay_candidate(_image_close_element(), {"contextTexts": ["同意隐私协议后继续"]})
    assert result["allowed"] is True
    assert result["category"] == "image_close_button"


def test_positive_consent_requires_explicit_policy_authorization() -> None:
    default_result = classify_overlay_candidate(_element("Agree"))
    assert default_result["allowed"] is False

    authorized_result = classify_overlay_candidate(_element("Agree"), {"allowPositiveConsent": True})
    assert authorized_result["allowed"] is True
    assert authorized_result["category"] == "positive_privacy_or_terms_consent"


def test_rank_prefers_safe_dismiss_over_authorized_consent() -> None:
    ranked = rank_overlay_candidates([
        _element("Agree", y=10),
        _element("Skip", y=200),
    ], {"allowPositiveConsent": True})
    assert ranked[0]["text"] == "Skip"


def test_policy_from_flow_defaults_and_authorization_scope() -> None:
    assert policy_from_flow({})["enabled"] is False

    policy = policy_from_flow({
        "uiUnblock": {"enabled": True, "maxAttempts": 2},
        "authorization": {"scopes": ["positive_privacy_or_terms_consent"]},
    })
    assert policy["enabled"] is True
    assert policy["maxAttempts"] == 2
    assert policy["allowPositiveConsent"] is True


def test_policy_supports_task_local_rules_without_code_changes() -> None:
    policy = policy_from_flow({
        "uiUnblock": {
            "enabled": True,
            "rules": [{
                "category": "safe_dismiss",
                "decision": "allow",
                "terms": ["Remind me later"],
                "priority": 5,
            }],
        },
    })
    result = classify_overlay_candidate(_element("Remind me later"), policy)
    assert result["allowed"] is True
    assert result["category"] == "safe_dismiss"
    assert evaluate_overlay_rules(["Remind me later"], policy)["decision"] == "allow"
