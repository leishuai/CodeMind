"""Lightweight runtime-path failure classification helpers.

AutoMind intentionally keeps runtime-path memory small: no separate ledger is
required for v1. Evaluators/Generators can add ``runtimePath`` and
``failureClass`` directly to existing ``evaluation.json`` testcase rows (or
failedChecks). These helpers normalize the common taxonomy, render concise
phase-reuse hints, and surface workflow-check warnings when a low-value path is
likely to be repeated without a changed trigger.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


RUNTIME_FAILURE_TAXONOMY: dict[str, dict[str, str]] = {
    "unknown": {
        "meaning": "Runtime path failed or was inconclusive, but the cause is not classified yet.",
        "next": "Classify with fresh evidence before retrying the same path; record what changed if retrying.",
    },
    "entry_invalid": {
        "meaning": "The app/service entry, route, URL, fixture, or starting state did not reach the intended target.",
        "next": "Change the entry target or precondition; do not retry the same entry unchanged.",
    },
    "entered_but_no_actionable_state": {
        "meaning": "The target surface opened, but the expected actionable/ready state was absent or ambiguous.",
        "next": "Add readiness waits/assertions, seed fixture state, or choose a more stable entry.",
    },
    "action_target_not_found": {
        "meaning": "The intended control/selector/API/action target was not found on the reached surface.",
        "next": "Improve selectors or change driver/backend before retrying the same action path.",
    },
    "wrong_surface_or_target": {
        "meaning": "Automation acted on the wrong page/control/surface, often due to generic labels or ambiguous selectors.",
        "next": "Tighten selectors, page signatures, and post-action assertions; avoid generic button/text matching.",
    },
    "action_failed": {
        "meaning": "The action was attempted but did not produce the expected state transition or side effect.",
        "next": "Change the trigger mechanism or add diagnostics to determine whether the action reached product code.",
    },
    "automation_timeout": {
        "meaning": "The driver/script timed out before reaching a decisive pass/fail signal.",
        "next": "Bound the script, add explicit waits, split the path, or switch backend before retrying.",
    },
    "signal_missing": {
        "meaning": "The journey ran, but the expected machine-checkable signal/log/event/output was missing.",
        "next": "Verify instrumentation/log filters and add a controlled diagnostic or stronger assertion.",
    },
    "proof_mismatch": {
        "meaning": "Evidence proves a related behavior, but not the required behavior/acceptance criterion.",
        "next": "Do not promote the related signal as proof; target the missing signal or downgrade only with approval.",
    },
    "environment_blocked": {
        "meaning": "Device, browser, network, account, permission, signing, or service state blocked the path.",
        "next": "Run/read preflight, reuse known recovery paths, or ask the user only for the specific external action needed.",
    },
    "authorization_blocked": {
        "meaning": "The path needs consent, login, destructive action, external upload, payment, or another explicit boundary crossing.",
        "next": "Stop and ask_user with the exact requested action and scope; do not bypass via automation.",
    },
    "diagnostic_needed": {
        "meaning": "Current black-box evidence cannot distinguish product bug, selector issue, or missing instrumentation.",
        "next": "Add a bounded temporary diagnostic/log or request a scoped manual action, then remove/promote deliberately.",
    },
}

LOW_VALUE_RETRY_CLASSES = {
    "entry_invalid",
    "entered_but_no_actionable_state",
    "action_target_not_found",
    "wrong_surface_or_target",
    "action_failed",
    "automation_timeout",
    "signal_missing",
    "proof_mismatch",
    "unknown",
}

_OVERRIDE_TOKENS = [
    "overrideReason",
    "changed trigger",
    "changed selector",
    "new selector",
    "different trigger",
    "new diagnostic",
    "manual action",
    "ask_user",
    "runtimeDowngradeApproval",
    "换路径",
    "换触发",
    "新选择器",
    "诊断日志",
    "手动触发",
]


def normalize_failure_class(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw:
        return "unknown"
    aliases = {
        "entered_but_no_playback": "entered_but_no_actionable_state",
        "playback_started_but_no_stop_control": "action_target_not_found",
        "stop_attempt_failed": "action_failed",
        "wrong_surface_clicked": "wrong_surface_or_target",
        "mobile_device_unavailable": "environment_blocked",
        "permission_blocked": "environment_blocked",
        "signing": "environment_blocked",
        "build_blocker": "environment_blocked",
    }
    return aliases.get(raw, raw if raw in RUNTIME_FAILURE_TAXONOMY else "unknown")


def taxonomy_markdown() -> str:
    lines = [
        "## Runtime path failure taxonomy",
        "",
        "Use these generic `failureClass` values in `evaluation.json.testResults[]`, failedChecks, or Validation.md when a runtime path is failed/partial/blocked:",
        "",
        "| failureClass | Meaning | Next-step guidance |",
        "|--------------|---------|--------------------|",
    ]
    for key, spec in RUNTIME_FAILURE_TAXONOMY.items():
        lines.append(f"| `{key}` | {spec['meaning']} | {spec['next']} |")
    return "\n".join(lines) + "\n"


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _extract_path_from_blob(item: dict[str, Any]) -> str:
    for key in ["runtimePath", "runtime_path", "pathId", "path_id", "journey", "entry"]:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    entry = item.get("entry") if isinstance(item.get("entry"), dict) else {}
    for key in ["url", "route", "command", "name", "id"]:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_signals(item: dict[str, Any]) -> dict[str, Any]:
    for key in ["observedSignals", "observed", "signals", "keyword_counts", "keywordCounts"]:
        value = item.get(key)
        if isinstance(value, dict):
            return value
    assessment = item.get("evidenceAssessment") if isinstance(item.get("evidenceAssessment"), dict) else {}
    metrics = assessment.get("hardMetrics") if isinstance(assessment.get("hardMetrics"), list) else []
    signals: dict[str, Any] = {}
    for metric in metrics:
        if isinstance(metric, dict) and metric.get("name"):
            signals[str(metric.get("name"))] = metric.get("value")
    return signals


def extract_runtime_path_attempts(evaluation: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract compact runtime-path attempts from existing evaluation fields."""
    if not isinstance(evaluation, dict):
        return []
    candidates: list[dict[str, Any]] = []
    for key in ["testResults", "failedChecks", "skippedChecks", "runtimePathAttempts", "runtimePaths"]:
        value = evaluation.get(key)
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
    attempts: list[dict[str, Any]] = []
    for item in candidates:
        path = _extract_path_from_blob(item)
        failure_class = normalize_failure_class(item.get("failureClass") or item.get("failure_class") or item.get("category"))
        signals = _extract_signals(item)
        retry_advice = str(item.get("retryAdvice") or item.get("retry_advice") or item.get("reason") or item.get("summary") or "").strip()
        result = str(item.get("result") or item.get("status") or "").strip().lower()
        should_retry_raw = item.get("shouldRetry") if "shouldRetry" in item else item.get("should_retry")
        should_retry = should_retry_raw if isinstance(should_retry_raw, bool) else None
        has_runtime_shape = bool(path or item.get("failureClass") or item.get("runtimePath") or item.get("observedSignals"))
        if not has_runtime_shape:
            continue
        attempts.append({
            "runtimePath": path or "unknown",
            "failureClass": failure_class,
            "result": result or "unknown",
            "testCaseId": item.get("testCaseId") or item.get("id") or item.get("name"),
            "observedSignals": signals,
            "retryAdvice": retry_advice,
            "shouldRetry": should_retry,
            "evidence": item.get("evidence") or item.get("path") or item.get("logs"),
        })
    return attempts


def format_failed_runtime_paths_section(evaluation: dict[str, Any] | None, *, limit: int = 6) -> str:
    attempts = extract_runtime_path_attempts(evaluation)
    if not attempts:
        return ""
    lines = [
        "## Recent runtime paths to avoid or change",
        "",
        "These are current-task attempts from `evaluation.json`. They are not hard failures by themselves, but Generator/Evaluator should not repeat them unchanged.",
        "",
    ]
    seen: set[tuple[str, str]] = set()
    count = 0
    for attempt in attempts:
        result = str(attempt.get("result") or "")
        if result in {"pass", "passed", "success"}:
            continue
        key = (str(attempt.get("runtimePath")), str(attempt.get("failureClass")))
        if key in seen:
            continue
        seen.add(key)
        count += 1
        signals = attempt.get("observedSignals") if isinstance(attempt.get("observedSignals"), dict) else {}
        signal_text = ", ".join(f"{k}={v}" for k, v in list(signals.items())[:6]) or "-"
        lines.extend([
            f"{count}. `{attempt.get('runtimePath') or 'unknown'}`",
            f"   - failureClass: `{attempt.get('failureClass') or 'unknown'}`",
            f"   - result: `{attempt.get('result') or 'unknown'}`; TC: `{attempt.get('testCaseId') or '-'}`",
            f"   - observedSignals: {signal_text}",
            f"   - retryAdvice: {attempt.get('retryAdvice') or RUNTIME_FAILURE_TAXONOMY.get(str(attempt.get('failureClass')), RUNTIME_FAILURE_TAXONOMY['unknown'])['next']}",
        ])
        if count >= limit:
            break
    if count == 0:
        return ""
    return "\n".join(lines).rstrip() + "\n"


def runtime_path_workflow_warnings(task_dir: Path, evaluation: dict[str, Any] | None, plan_text: str = "", delivery_text: str = "") -> list[str]:
    """Warn when low-value runtime paths are likely to be repeated unchanged."""
    attempts = extract_runtime_path_attempts(evaluation)
    if not attempts:
        return []
    text = (plan_text or "") + "\n" + (delivery_text or "")
    lower_text = text.lower()
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for attempt in attempts:
        result = str(attempt.get("result") or "").lower()
        failure_class = normalize_failure_class(attempt.get("failureClass"))
        if result in {"pass", "passed", "success"}:
            continue
        runtime_path = str(attempt.get("runtimePath") or "unknown")
        key = (runtime_path, failure_class)
        if key in seen:
            continue
        seen.add(key)
        should_retry = attempt.get("shouldRetry")
        low_value = should_retry is False or failure_class in LOW_VALUE_RETRY_CLASSES
        if not low_value:
            continue
        path_mentioned = runtime_path != "unknown" and runtime_path.lower() in lower_text
        override_documented = any(token.lower() in lower_text for token in _OVERRIDE_TOKENS)
        if path_mentioned and override_documented:
            continue
        condition = "marked shouldRetry=false" if should_retry is False else f"classified as {failure_class}"
        warnings.append(
            "runtime_path_repeat_risk: "
            f"`{runtime_path}` is {condition}; next Generator/Evaluator should avoid repeating it unchanged, "
            "or document changed selector/trigger/diagnostic/manual action/overrideReason in Plan.md or Delivery.md."
        )
    return warnings[:6]
