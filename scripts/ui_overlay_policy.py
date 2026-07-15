#!/usr/bin/env python3
"""Shared safe auto-unblock overlay policy for CodeMind UI runners.

The policy is high-automation by default. It identifies low-risk dismiss/close
controls, app-internal first-run privacy/terms consent controls, and OS/app
permission grants (camera, microphone, photos, location, contacts, notifications,
etc.) that can be clicked as verification preconditions, while separating them
from sensitive actions such as login/account authorization,
payment/purchase/subscription, deletion/reset, and external upload. Platform
runners keep the actual execution local; this module only classifies normalized
UI elements and parses the probe-flow policy. Unknown or ambiguous overlays
should be escalated to model review with screenshot/OCR/hierarchy, button labels,
page context, and task intent instead of being decided by keywords alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OverlayRule:
    """Declarative overlay rule.

    Rules are intentionally data-shaped so future platforms/projects can extend
    policy by adding rules or task-local allow/deny hints, instead of changing
    runner control flow.
    """

    category: str
    decision: str
    terms: tuple[str, ...]
    reason: str
    priority: int


SAFE_DISMISS_KEYWORDS: tuple[str, ...] = (
    "关闭",
    "关 闭",
    "知道了",
    "我知道了",
    "明白了",
    "跳过",
    "稍后",
    "暂不",
    "暂时不用",
    "以后再说",
    "close",
    "got it",
    "skip",
    "later",
    "not now",
    "dismiss",
    "cancel",
    "no thanks",
    "×",
    "✕",
    "x",
)

GENERIC_CONFIRM_KEYWORDS: tuple[str, ...] = (
    "确定",
    "好的",
    "ok",
    "okay",
)

IMAGE_CLOSE_KEYWORDS: tuple[str, ...] = (
    "close",
    "关闭",
    "btn_close",
    "iv_close",
    "dialog_close",
    "close_button",
    "button_close",
)

SENSITIVE_KEYWORDS: tuple[str, ...] = (
    "登录",
    "登陆",
    "注册",
    "授权",
    "开启",
    "支付",
    "购买",
    "订阅",
    "开通",
    "订单",
    "账号",
    "账户",
    "会员",
    "充值",
    "删除",
    "清空",
    "重置",
    "卸载",
    "上传",
    "提交",
    "login",
    "log in",
    "sign in",
    "sign up",
    "authorize",
    "account",
    "order",
    "pay",
    "purchase",
    "subscribe",
    "delete",
    "clear",
    "reset",
    "uninstall",
    "upload",
    "submit",
)

HIGH_RISK_KEYWORDS: tuple[str, ...] = (
    "登录",
    "登陆",
    "注册",
    "授权",
    "支付",
    "购买",
    "订阅",
    "开通",
    "订单",
    "账号",
    "账户",
    "会员",
    "充值",
    "删除",
    "清空",
    "重置",
    "卸载",
    "上传",
    "login",
    "log in",
    "sign in",
    "sign up",
    "authorize",
    "account",
    "order",
    "pay",
    "purchase",
    "subscribe",
    "delete",
    "clear",
    "reset",
    "uninstall",
    "upload",
)

POSITIVE_CONSENT_KEYWORDS: tuple[str, ...] = (
    "同意",
    "允许",
    "继续",
    "下一步",
    "agree",
    "allow",
    "accept",
    "continue",
)

PERMISSION_CONTEXT_KEYWORDS: tuple[str, ...] = (
    "相机",
    "照片",
    "相册",
    "通讯录",
    "联系人",
    "位置",
    "定位",
    "麦克风",
    "通知",
    "蓝牙",
    "追踪",
    "访问",
    "camera",
    "photos",
    "contacts",
    "location",
    "microphone",
    "notifications",
    "bluetooth",
    "tracking",
    "permission",
)

DEFAULT_OVERLAY_RULES: tuple[OverlayRule, ...] = (
    OverlayRule(
        category="high_risk",
        decision="deny",
        terms=HIGH_RISK_KEYWORDS,
        reason="matches high-risk/sensitive action keyword",
        priority=10,
    ),
    OverlayRule(
        category="positive_privacy_or_terms_consent",
        decision="allow",
        terms=POSITIVE_CONSENT_KEYWORDS,
        reason="matches app-internal consent/continue keyword that can be auto-unblocked during verification",
        priority=20,
    ),
    OverlayRule(
        category="sensitive",
        decision="deny",
        terms=SENSITIVE_KEYWORDS,
        reason="matches sensitive action keyword",
        priority=30,
    ),
    OverlayRule(
        category="safe_dismiss",
        decision="allow",
        terms=SAFE_DISMISS_KEYWORDS,
        reason="matches safe dismiss/close keyword",
        priority=100,
    ),
)


def _normalize(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "on"}


def _as_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def _dedup(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(str(value).strip())
    return out


def _keyword_matches(values: list[str], keywords: list[str] | tuple[str, ...]) -> list[str]:
    normalized_values = [_normalize(value) for value in values if _normalize(value)]
    matches: list[str] = []
    for keyword in keywords:
        normalized_keyword = _normalize(keyword)
        if not normalized_keyword:
            continue
        for value in normalized_values:
            if _keyword_match(value, normalized_keyword):
                matches.append(keyword)
                break
    return _dedup(matches)


def _keyword_match(value: str, keyword: str) -> bool:
    if not value or not keyword:
        return False
    if keyword in {"x", "ok"}:
        return value == keyword
    if keyword.isascii() and len(keyword) <= 3:
        return value == keyword or re.search(rf"(^|[^a-z0-9]){re.escape(keyword)}([^a-z0-9]|$)", value) is not None
    return keyword in value


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def text_values(element: dict[str, Any]) -> list[str]:
    """Return human-facing text/identifier values for a normalized UI element."""
    keys = [
        "text",
        "label",
        "name",
        "title",
        "contentDesc",
        "content_desc",
        "contentDescription",
        "accessibilityLabel",
        "ariaLabel",
        "resourceId",
        "resource_id",
        "id",
        "testId",
        "test_id",
        "data-testid",
    ]
    values: list[str] = []
    for key in keys:
        value = element.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return _dedup(values)


def bounds_area(element: dict[str, Any]) -> int:
    bounds = element.get("bounds")
    if isinstance(bounds, list) and len(bounds) == 4:
        try:
            return max(1, (int(bounds[2]) - int(bounds[0])) * (int(bounds[3]) - int(bounds[1])))
        except (TypeError, ValueError):
            return 0
    return 0


def _policy_keywords(policy: dict[str, Any] | None, key: str, defaults: tuple[str, ...]) -> list[str]:
    if not isinstance(policy, dict):
        return list(defaults)
    extra = [str(item).strip() for item in _list(policy.get(key)) if str(item).strip()]
    return list(defaults) + extra


def _policy_context_values(policy: dict[str, Any] | None) -> list[str]:
    if not isinstance(policy, dict):
        return []
    values: list[str] = []
    for key in ["contextTexts", "visibleTexts", "pageTexts", "dialogTexts", "overlayTexts"]:
        values.extend(str(item).strip() for item in _list(policy.get(key)) if str(item).strip())
    return _dedup(values)


def _has_sensitive_context(values: list[str], policy: dict[str, Any] | None) -> dict[str, Any] | None:
    context_values = values + _policy_context_values(policy)
    matches = _keyword_matches(
        context_values,
        _policy_keywords(policy, "sensitiveKeywords", HIGH_RISK_KEYWORDS + SENSITIVE_KEYWORDS),
    )
    if not matches:
        return None
    return {
        "category": "sensitive",
        "sensitiveCategory": "sensitive_context",
        "reason": "candidate or surrounding overlay text matches sensitive/high-risk keyword",
        "matchedKeywords": matches,
    }


def _has_permission_context(values: list[str], policy: dict[str, Any] | None) -> dict[str, Any] | None:
    """Detect whether the surrounding dialog/overlay is an OS/app permission request.

    Used to disambiguate positive-consent buttons: 'Allow' on a camera-permission
    alert is a permission_grant, not a privacy/terms consent. This distinction
    feeds into the model-review signal so the Evaluator can quickly classify
    what was auto-clicked.
    """
    context_values = values + _policy_context_values(policy)
    matches = _keyword_matches(
        context_values,
        _policy_keywords(policy, "permissionKeywords", PERMISSION_CONTEXT_KEYWORDS),
    )
    if not matches:
        return None
    return {
        "contextCategory": "permission_grant",
        "reason": "surrounding overlay text indicates an OS/app permission request",
        "matchedKeywords": matches,
    }


def _looks_like_image_close_button(element: dict[str, Any], values: list[str]) -> bool:
    class_name = _normalize(element.get("className") or element.get("class") or element.get("type"))
    has_image_shape = "image" in class_name or "button" in class_name
    if not has_image_shape:
        return False
    if not _keyword_matches(values, IMAGE_CLOSE_KEYWORDS):
        return False
    area = bounds_area(element)
    return area == 0 or area <= 320 * 320


def _policy_rules(policy: dict[str, Any] | None = None) -> list[OverlayRule]:
    """Return rule list with task-local extensions.

    `uiUnblock.rules[]` can add generic terms without editing CodeMind code:

    ```json
    {"category": "safe_dismiss", "decision": "allow", "terms": ["Remind me later"]}
    ```

    Supported decisions: allow, deny, requires_authorization.
    """
    rules = list(DEFAULT_OVERLAY_RULES)
    if isinstance(policy, dict):
        for item in _list(policy.get("rules")):
            if not isinstance(item, dict):
                continue
            terms = tuple(str(term).strip() for term in _list(item.get("terms") or item.get("keywords")) if str(term).strip())
            if not terms:
                continue
            decision = str(item.get("decision") or "deny").strip().casefold()
            if decision not in {"allow", "deny", "requires_authorization"}:
                decision = "deny"
            priority = _as_int(item.get("priority"), 50, minimum=0)
            rules.append(OverlayRule(
                category=str(item.get("category") or "custom").strip() or "custom",
                decision=decision,
                terms=terms,
                reason=str(item.get("reason") or f"matches custom {decision} overlay rule"),
                priority=priority,
            ))

        # Backward-compatible shorthand for simple task-local terms.
        safe_terms = tuple(str(item).strip() for item in _list(policy.get("safeKeywords")) if str(item).strip())
        if safe_terms:
            rules.append(OverlayRule(
                category="safe_dismiss",
                decision="allow",
                terms=safe_terms,
                reason="matches task-local safe dismiss/close keyword",
                priority=90,
            ))
        sensitive_terms = tuple(str(item).strip() for item in _list(policy.get("sensitiveKeywords")) if str(item).strip())
        if sensitive_terms:
            rules.append(OverlayRule(
                category="sensitive",
                decision="deny",
                terms=sensitive_terms,
                reason="matches task-local sensitive keyword",
                priority=15,
            ))

    rules.sort(key=lambda rule: rule.priority)
    return rules


def evaluate_overlay_rules(values: list[str], policy: dict[str, Any] | None = None) -> dict[str, Any] | None:
    for rule in _policy_rules(policy):
        matched = _keyword_matches(values, rule.terms)
        if not matched:
            continue
        return {
            "decision": rule.decision,
            "category": rule.category,
            "reason": rule.reason,
            "matchedKeywords": matched,
            "priority": rule.priority,
        }
    return None


def classify_overlay_candidate(element: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify one UI element as a safe, sensitive, or unknown unblock target.

    Model-first triage contract: this classifier is intentionally conservative
    and marks every non-deterministic decision so callers can route them to
    model review. Use the returned `triageSource` field to drive triage:

    * `code_deterministic` — well-understood keyword patterns that are
      overwhelmingly safe or overwhelmingly sensitive (and safe to block).
    * `code_heuristic_blocked` — a heuristic that rejected a button / dialog
      element because its text looked like a sensitive action but the call
      site has an allow list that overrides it.
    * `requires_model_review` — the classifier could not make a confident
      decision; callers should surface screenshot/OCR/hierarchy, button labels,
      page context, and task intent to a model before acting on it.

    The returned boolean `needsModelReview` is a convenience — True whenever
    `triageSource == "requires_model_review"`.

    Decision order (context-first, not button-first):
      1. Button itself is sensitive keyword → block
      2. Surrounding dialog context is sensitive → only safe_dismiss allowed, rest block
      3. Surrounding dialog is a permission request → allow positive-consent as permission_grant
      4. Button matches an allow rule → allow
      5. Generic confirm on non-sensitive dialog → allow
      6. Image close button → allow
      7. Unknown → model review
    """
    values = text_values(element)

    enabled = not (str(element.get("enabled", "")).strip().casefold() == "false" or element.get("enabled") is False)
    center = element.get("center") if isinstance(element.get("center"), dict) else None
    clickable = element.get("clickable")
    clickable_ok = clickable is None or clickable is True or str(clickable).strip().casefold() == "true"
    if not enabled or not center or not clickable_ok:
        return {
            "allowed": False,
            "category": "not_actionable",
            "reason": "element is disabled, not clickable, or has no center point",
            "texts": values,
            "matchedKeywords": [],
            "triageSource": "code_deterministic",
            "needsModelReview": False,
        }

    rule_result = evaluate_overlay_rules(values, policy)
    sensitive_context = _has_sensitive_context(values, policy)
    permission_context = _has_permission_context(values, policy)

    def _base(triage_source: str, needs_review: bool) -> dict[str, Any]:
        out: dict[str, Any] = {
            "texts": values,
            "matchedKeywords": [],
            "triageSource": triage_source,
            "needsModelReview": needs_review,
        }
        if permission_context:
            out["contextCategory"] = permission_context["contextCategory"]
            out["contextMatchedKeywords"] = permission_context["matchedKeywords"]
        if sensitive_context:
            out["sensitiveContextKeywords"] = sensitive_context["matchedKeywords"]
        return out

    # 1. Button itself is a deny rule (sensitive or high-risk keyword) → block.
    if rule_result and rule_result.get("decision") in {"deny", "requires_authorization"}:
        category = str(rule_result.get("category") or "")
        out = _base("code_heuristic_blocked", False)
        out.update({
            "allowed": False,
            "category": "sensitive",
            "sensitiveCategory": category,
            "reason": rule_result.get("reason", ""),
            "matchedKeywords": rule_result.get("matchedKeywords") or [],
            "ruleDecision": rule_result.get("decision"),
        })
        return out

    # 2. Surrounding dialog context is sensitive.
    #    Only safe_dismiss buttons are allowed on sensitive-context dialogs;
    #    positive-consent buttons (e.g. "继续" on a login prompt) must NOT
    #    be auto-clicked.
    if sensitive_context:
        if rule_result and rule_result.get("category") == "safe_dismiss" and rule_result.get("decision") == "allow":
            out = _base("code_deterministic", False)
            out.update({
                "allowed": True,
                "category": "safe_dismiss",
                "reason": "safe dismiss/close button on a sensitive-context dialog; dismissing is non-destructive",
                "matchedKeywords": rule_result.get("matchedKeywords") or [],
                "ruleDecision": "contextual_allow",
            })
            return out
        out = _base("code_heuristic_blocked", False)
        out.update({
            "allowed": False,
            "category": "sensitive",
            "sensitiveCategory": "sensitive_context",
            "reason": "surrounding overlay text is sensitive; only safe dismiss/close is auto-clickable",
            "matchedKeywords": sensitive_context.get("matchedKeywords") or [],
            "ruleDecision": "deny",
        })
        return out

    # 3. Button matches an allow rule → allow (context is not sensitive).
    if rule_result and rule_result.get("decision") == "allow":
        category = str(rule_result.get("category") or "")
        image_close_matches = _keyword_matches(values, IMAGE_CLOSE_KEYWORDS)
        if image_close_matches and _looks_like_image_close_button(element, values):
            out = _base("code_deterministic", False)
            out.update({
                "allowed": True,
                "category": "image_close_button",
                "reason": "image/button close control matched by identifier/description and no sensitive surrounding context matched",
                "matchedKeywords": image_close_matches,
                "ruleDecision": "contextual_allow",
            })
            return out
        # Refine category: positive-consent on a permission dialog = permission_grant.
        if permission_context and category == "positive_privacy_or_terms_consent":
            category = "permission_grant"
        out = _base("code_deterministic", False)
        out.update({
            "allowed": True,
            "category": category,
            "reason": rule_result.get("reason", ""),
            "matchedKeywords": rule_result.get("matchedKeywords") or [],
            "ruleDecision": rule_result.get("decision"),
        })
        return out

    # 4. Generic confirm on a non-sensitive, non-permission dialog → allow.
    generic_confirm_matches = _keyword_matches(values, GENERIC_CONFIRM_KEYWORDS)
    if generic_confirm_matches:
        out = _base("code_deterministic", False)
        out.update({
            "allowed": True,
            "category": "safe_confirm",
            "reason": "generic confirm/dismiss keyword is allowed because no sensitive surrounding context matched",
            "matchedKeywords": generic_confirm_matches,
            "ruleDecision": "contextual_allow",
        })
        return out

    # 5. Image close button → allow.
    image_close_matches = _keyword_matches(values, IMAGE_CLOSE_KEYWORDS)
    if image_close_matches and _looks_like_image_close_button(element, values):
        out = _base("code_deterministic", False)
        out.update({
            "allowed": True,
            "category": "image_close_button",
            "reason": "image/button close control matched by identifier/description and no sensitive surrounding context matched",
            "matchedKeywords": image_close_matches,
            "ruleDecision": "contextual_allow",
        })
        return out

    # 6. Unknown → model review.
    out = _base("requires_model_review", True)
    out.update({
        "allowed": False,
        "category": "requires_model_review",
        "reason": "no safe dismiss keyword matched — surface this element's text / hierarchy to a model or a human before clicking",
        "matchedKeywords": [],
    })
    return out


def rank_overlay_candidates(elements: list[dict[str, Any]], policy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return allowed overlay candidates ordered by conservative preference."""
    rows: list[dict[str, Any]] = []
    category_rank = {
        "safe_dismiss": 0,
        "image_close_button": 1,
        "safe_confirm": 2,
        "positive_privacy_or_terms_consent": 3,
        "permission_grant": 4,
    }
    for element in elements:
        classification = classify_overlay_candidate(element, policy)
        if not classification.get("allowed"):
            continue
        row = dict(element)
        row["classification"] = classification
        center = row.get("center") if isinstance(row.get("center"), dict) else {}
        rows.append(row)
    rows.sort(
        key=lambda item: (
            category_rank.get((item.get("classification") or {}).get("category"), 99),
            bounds_area(item),
            int((item.get("center") or {}).get("y") or 0),
            int((item.get("center") or {}).get("x") or 0),
        )
    )
    return rows


def sensitive_overlay_candidates(elements: list[dict[str, Any]], policy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for element in elements:
        classification = classify_overlay_candidate(element, policy)
        if classification.get("category") == "sensitive":
            row = dict(element)
            row["classification"] = classification
            rows.append(row)
    return rows


def _authorization_scopes(flow: dict[str, Any]) -> list[str]:
    blocks = [
        flow.get("authorization"),
        (flow.get("testIntent") or {}).get("authorization") if isinstance(flow.get("testIntent"), dict) else None,
        (flow.get("intent") or {}).get("authorization") if isinstance(flow.get("intent"), dict) else None,
    ]
    values: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        values.extend(str(item).strip() for item in _list(block.get("scope")) if str(item).strip())
        values.extend(str(item).strip() for item in _list(block.get("scopes")) if str(item).strip())
    return _dedup(values)


def policy_from_flow(flow: dict[str, Any]) -> dict[str, Any]:
    """Parse top-level probe-flow uiUnblock/autoUnblockOverlay policy."""
    raw = flow.get("uiUnblock", flow.get("autoUnblockOverlay", {}))
    if raw is True:
        raw = {"enabled": True}
    if raw is False or raw is None:
        raw = {"enabled": False}
    if not isinstance(raw, dict):
        raw = {}
    scopes = [item.casefold() for item in _authorization_scopes(flow)]
    explicit_positive_scope = any(
        item in {"positive_privacy_or_terms_consent", "non_destructive_common_dialog", "safe_auto_unblock"}
        for item in scopes
    )
    has_config = bool(raw)
    enabled = _as_bool(raw.get("enabled"), default=has_config)
    return {
        "enabled": enabled,
        "policy": str(raw.get("policy") or "safe_non_destructive_only"),
        "mode": str(raw.get("mode") or "runner"),
        "maxAttempts": _as_int(raw.get("maxAttempts"), 3, minimum=0),
        "beforeActions": _as_bool(raw.get("beforeActions"), default=True),
        "afterLaunch": _as_bool(raw.get("afterLaunch"), default=True),
        "betweenActions": _as_bool(raw.get("betweenActions"), default=False),
        "allowPositiveConsent": _as_bool(raw.get("allowPositiveConsent"), default=explicit_positive_scope),
        "safeKeywords": [str(item).strip() for item in _list(raw.get("safeKeywords")) if str(item).strip()],
        "sensitiveKeywords": [str(item).strip() for item in _list(raw.get("sensitiveKeywords")) if str(item).strip()],
        "rules": [item for item in _list(raw.get("rules")) if isinstance(item, dict)],
        "authorizationScopes": scopes,
    }


def summarize_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(policy.get("enabled")),
        "policy": policy.get("policy"),
        "mode": policy.get("mode"),
        "maxAttempts": policy.get("maxAttempts"),
        "beforeActions": bool(policy.get("beforeActions")),
        "afterLaunch": bool(policy.get("afterLaunch")),
        "betweenActions": bool(policy.get("betweenActions")),
        "allowPositiveConsent": bool(policy.get("allowPositiveConsent")),
    }
