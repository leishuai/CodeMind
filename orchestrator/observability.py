"""Validated ingestion for external CodeMind audit and metric observations."""
from __future__ import annotations

import json
import math
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from orchestrator.audit import AUDIT_EVENT_TYPES, append_audit_entry, write_audit_report
from orchestrator.metrics import merge_metric_observations

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]


MAX_OBSERVATION_BYTES = 64 * 1024
MAX_AUDIT_ITEMS = 20
MAX_METRIC_ITEMS = 50
MAX_DETAILS_DEPTH = 4
MAX_DETAILS_ITEMS = 50
MAX_STRING_LENGTH = 1000

ALLOWED_SOURCES = {"lark-bridge", "automind"}
ALLOWED_PHASES = {"conversation", "scheduler", "discovery", "build", "evaluation"}
ALLOWED_RISK_LEVELS = {"low", "medium", "high", "critical"}
ALLOWED_UNITS = {"", "count", "seconds", "bytes"}

ALLOWED_AUDIT_DETAIL_KEYS = {
    "entrypoint",
    "taskCode",
    "workItemId",
    "capability",
    "actionType",
    "target",
    "result",
    "status",
    "actionCount",
    "taskBound",
    "confirmationRequired",
    "reasonCode",
    "decision",
    "sideEffectCommitted",
    "duplicateSuppressed",
    "parseStatus",
    "policyName",
    "input",
    "output",
    "attempt",
    "strategy",
    "gateType",
    "passed",
}

FORBIDDEN_DETAIL_KEY_PARTS = {
    "prompt",
    "content",
    "document",
    "credential",
    "password",
    "secret",
    "token",
    "cookie",
    "path",
    "usertext",
    "messagebody",
}

ALLOWED_METRIC_NAMES = {
    "conversation_turn_count",
    "capability_action_count",
    "capability_no_action_count",
    "capability_parse_failure_count",
    "capability_schema_reject_count",
    "capability_policy_reject_count",
    "capability_confirmation_count",
    "capability_confirmation_cancel_count",
    "capability_executor_failure_count",
    "capability_duplicate_suppressed_count",
    "capability_duplicate_side_effect_count",
    "capability_plan_duration",
    "capability_executor_duration",
    "capability_result_response_duration",
    "capability_total_duration",
    "unsafe_capability_effect_count",
}


class ObservationValidationError(ValueError):
    """Raised when an external observation violates the bounded contract."""


@contextmanager
def _observation_lock(task_dir: Path) -> Iterator[None]:
    task_dir.mkdir(parents=True, exist_ok=True)
    lock_path = task_dir / ".observe.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _validate_details(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_DETAILS_DEPTH:
        raise ObservationValidationError("audit details exceed maximum depth")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ObservationValidationError("audit details contain non-finite number")
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise ObservationValidationError("audit detail string is too long")
        return value
    if isinstance(value, list):
        if len(value) > MAX_DETAILS_ITEMS:
            raise ObservationValidationError("audit details list is too large")
        return [_validate_details(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > MAX_DETAILS_ITEMS:
            raise ObservationValidationError("audit details object is too large")
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            lowered = key.replace("_", "").lower()
            if key not in ALLOWED_AUDIT_DETAIL_KEYS:
                raise ObservationValidationError(f"audit detail key is not allowed: {key}")
            if any(part in lowered for part in FORBIDDEN_DETAIL_KEY_PARTS):
                raise ObservationValidationError(f"sensitive audit detail key is not allowed: {key}")
            result[key] = _validate_details(item, depth=depth + 1)
        return result
    raise ObservationValidationError("audit details contain unsupported value")


def validate_observation_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ObservationValidationError("observation must be a JSON object")
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_OBSERVATION_BYTES:
        raise ObservationValidationError("observation exceeds size limit")

    source = str(payload.get("source") or "")
    if source not in ALLOWED_SOURCES:
        raise ObservationValidationError("observation source is not allowed")

    raw_audit = payload.get("audit") or []
    raw_metrics = payload.get("metrics") or []
    if not isinstance(raw_audit, list) or len(raw_audit) > MAX_AUDIT_ITEMS:
        raise ObservationValidationError("audit must be a bounded array")
    if not isinstance(raw_metrics, list) or len(raw_metrics) > MAX_METRIC_ITEMS:
        raise ObservationValidationError("metrics must be a bounded array")

    audit_items: list[dict[str, Any]] = []
    for raw in raw_audit:
        if not isinstance(raw, dict):
            raise ObservationValidationError("audit item must be an object")
        event_type = str(raw.get("type") or "")
        if event_type not in AUDIT_EVENT_TYPES:
            raise ObservationValidationError(f"audit type is not allowed: {event_type}")
        phase = str(raw.get("phase") or "conversation")
        if phase not in ALLOWED_PHASES:
            raise ObservationValidationError(f"audit phase is not allowed: {phase}")
        risk = str(raw.get("riskLevel") or "low")
        if risk not in ALLOWED_RISK_LEVELS:
            raise ObservationValidationError(f"audit risk level is not allowed: {risk}")
        item = {
            "type": event_type,
            "phase": phase,
            "message": str(raw.get("message") or "")[:MAX_STRING_LENGTH],
            "decisionType": str(raw.get("decisionType") or "")[:100],
            "reason": str(raw.get("reasonCode") or raw.get("reason") or "")[:200],
            "action": str(raw.get("action") or "")[:100],
            "riskLevel": risk,
            "details": _validate_details(raw.get("details") or {}),
        }
        audit_items.append(item)

    metric_items: list[dict[str, Any]] = []
    for raw in raw_metrics:
        if not isinstance(raw, dict):
            raise ObservationValidationError("metric item must be an object")
        name = str(raw.get("name") or "")
        if name not in ALLOWED_METRIC_NAMES:
            raise ObservationValidationError(f"metric name is not allowed: {name}")
        value = raw.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ObservationValidationError(f"metric value must be numeric: {name}")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ObservationValidationError(f"metric value is invalid: {name}")
        unit = str(raw.get("unit") or "")
        if unit not in ALLOWED_UNITS:
            raise ObservationValidationError(f"metric unit is not allowed: {unit}")
        metric_items.append({"name": name, "value": value, "unit": unit})

    return {"source": source, "audit": audit_items, "metrics": metric_items}


def ingest_observation(task_dir: Path, payload: Any) -> dict[str, Any]:
    validated = validate_observation_payload(payload)
    with _observation_lock(task_dir):
        for item in validated["audit"]:
            append_audit_entry(
                task_dir,
                item["type"],
                phase=item["phase"],
                message=item["message"] or None,
                details=item["details"] or None,
                decision_type=item["decisionType"] or None,
                reason=item["reason"] or None,
                action=item["action"] or None,
                risk_level=item["riskLevel"],
                source=validated["source"],
            )
        merge_metric_observations(task_dir, validated["metrics"])
        audit_report = write_audit_report(task_dir)
    return {
        "result": "ok",
        "auditCount": len(validated["audit"]),
        "metricCount": len(validated["metrics"]),
        "auditRef": str(audit_report.name),
        "metricsRef": "metrics.json",
        "observedAt": datetime.now().isoformat(timespec="seconds"),
        "pid": os.getpid(),
    }
