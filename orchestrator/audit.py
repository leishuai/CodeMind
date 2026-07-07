"""Audit logging for AutoMind key decisions and operations.

Records:
- decision_made: 关键决策（分类、原因、上下文）
- branch_taken: 逻辑分支选择（条件、结果）
- action_executed: 操作执行（类型、目标、结果）
- gate_result: 门检查结果（类型、通过/失败）
- policy_evaluation: 策略评估（规则、输入、输出）

All sensitive data is redacted using the same utilities as events.jsonl.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.session.events import redact_sensitive_obj, redact_sensitive_text

AUDIT_SCHEMA_VERSION = 1
MAX_AUDIT_ENTRIES = 1000


AUDIT_EVENT_TYPES = {
    "decision_made": "关键决策",
    "branch_taken": "逻辑分支选择",
    "action_executed": "操作执行",
    "gate_result": "门检查结果",
    "policy_evaluation": "策略评估",
    "recovery_attempt": "恢复尝试",
    "fallback_triggered": "降级触发",
    "skip_decision": "跳过决策",
}


def audit_path(task_dir: Path) -> Path:
    return task_dir / "audit.jsonl"


def read_audit_log(task_dir: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    path = audit_path(task_dir)
    if not path.exists():
        return []
    lines = path.read_text(errors="ignore").splitlines()
    if limit is not None:
        lines = lines[-limit:]
    entries: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def append_audit_entry(
    task_dir: Path,
    event_type: str,
    *,
    iteration: int | None = None,
    phase: str | None = None,
    message: str | None = None,
    details: dict[str, Any] | None = None,
    decision_type: str | None = None,
    reason: str | None = None,
    action: str | None = None,
    risk_level: str | None = None,
    source: str = "automind",
) -> dict[str, Any]:
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "type": event_type,
        "source": source,
    }
    if iteration is not None:
        entry["iteration"] = iteration
    if phase:
        entry["phase"] = phase
    if message:
        entry["message"] = redact_sensitive_text(message)
    if details:
        entry["details"] = redact_sensitive_obj(details)
    if decision_type:
        entry["decisionType"] = decision_type
    if reason:
        entry["reason"] = redact_sensitive_text(reason)
    if action:
        entry["action"] = action
    if risk_level:
        entry["riskLevel"] = risk_level

    with audit_path(task_dir).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return entry


def record_decision(
    task_dir: Path,
    *,
    iteration: int | None = None,
    phase: str | None = None,
    message: str,
    decision_type: str,
    reason: str | None = None,
    context: dict[str, Any] | None = None,
    action: str | None = None,
    risk_level: str | None = None,
) -> dict[str, Any]:
    """Record a key decision."""
    return append_audit_entry(
        task_dir,
        "decision_made",
        iteration=iteration,
        phase=phase,
        message=message,
        decision_type=decision_type,
        reason=reason,
        action=action,
        risk_level=risk_level,
        details=context,
    )


def record_branch(
    task_dir: Path,
    *,
    iteration: int | None = None,
    phase: str | None = None,
    condition: str,
    outcome: str,
    alternatives: list[str] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Record a logical branch decision."""
    details = {"condition": condition, "outcome": outcome}
    if alternatives:
        details["alternatives"] = alternatives
    return append_audit_entry(
        task_dir,
        "branch_taken",
        iteration=iteration,
        phase=phase,
        message=f"Branch: {condition} -> {outcome}",
        decision_type="branch",
        reason=reason,
        details=details,
    )


def record_action(
    task_dir: Path,
    *,
    iteration: int | None = None,
    phase: str | None = None,
    action_type: str,
    target: str,
    result: str = "completed",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record an executed action."""
    action_details = {"actionType": action_type, "target": target, "result": result}
    if details:
        action_details.update(details)
    return append_audit_entry(
        task_dir,
        "action_executed",
        iteration=iteration,
        phase=phase,
        message=f"Action: {action_type} on {target} -> {result}",
        decision_type="action",
        details=action_details,
    )


def record_gate(
    task_dir: Path,
    *,
    iteration: int | None = None,
    phase: str | None = None,
    gate_type: str,
    passed: bool,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a gate check result."""
    result = "passed" if passed else "failed"
    msg = message or f"Gate {gate_type} {result}"
    return append_audit_entry(
        task_dir,
        "gate_result",
        iteration=iteration,
        phase=phase,
        message=msg,
        decision_type="gate",
        action="continue" if passed else "blocked",
        risk_level="high" if not passed else "low",
        details={"gateType": gate_type, "passed": passed, "result": result, **(details or {})},
    )


def record_policy(
    task_dir: Path,
    *,
    iteration: int | None = None,
    phase: str | None = None,
    policy_name: str,
    input_data: dict[str, Any] | None = None,
    output_data: dict[str, Any] | None = None,
    decision: str | None = None,
) -> dict[str, Any]:
    """Record a policy evaluation."""
    details = {"policyName": policy_name}
    if input_data:
        details["input"] = input_data
    if output_data:
        details["output"] = output_data
    msg = f"Policy {policy_name}"
    if decision:
        msg += f" -> {decision}"
    return append_audit_entry(
        task_dir,
        "policy_evaluation",
        iteration=iteration,
        phase=phase,
        message=msg,
        decision_type="policy",
        action=decision,
        details=details,
    )


def record_recovery(
    task_dir: Path,
    *,
    iteration: int | None = None,
    phase: str | None = None,
    attempt: int,
    strategy: str,
    result: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a recovery attempt."""
    recovery_details = {"attempt": attempt, "strategy": strategy, "result": result}
    if details:
        recovery_details.update(details)
    return append_audit_entry(
        task_dir,
        "recovery_attempt",
        iteration=iteration,
        phase=phase,
        message=f"Recovery attempt {attempt}: {strategy} -> {result}",
        decision_type="recovery",
        reason=reason,
        action="retry" if result == "success" else "fail",
        risk_level="high",
        details=recovery_details,
    )


def build_audit_summary(task_dir: Path) -> dict[str, Any]:
    """Build a summary of all audit entries."""
    entries = read_audit_log(task_dir)
    if not entries:
        return {"schemaVersion": AUDIT_SCHEMA_VERSION, "entryCount": 0, "summary": {}, "highRiskEntries": []}

    by_type: dict[str, int] = {}
    by_phase: dict[str, int] = {}
    by_risk: dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    high_risk: list[dict] = []
    actions: list[dict] = []
    gates: list[dict] = []
    decisions: list[dict] = []

    for entry in entries:
        etype = entry.get("type") or "unknown"
        by_type[etype] = by_type.get(etype, 0) + 1
        phase = entry.get("phase") or "unknown"
        by_phase[phase] = by_phase.get(phase, 0) + 1
        risk = entry.get("riskLevel") or "medium"
        if risk in by_risk:
            by_risk[risk] += 1

        if risk in {"high", "critical"}:
            high_risk.append({
                "ts": entry.get("ts"),
                "type": etype,
                "phase": phase,
                "message": entry.get("message"),
                "reason": entry.get("reason"),
            })

        if etype == "action_executed":
            actions.append({
                "ts": entry.get("ts"),
                "actionType": entry.get("details", {}).get("actionType"),
                "target": entry.get("details", {}).get("target"),
                "result": entry.get("details", {}).get("result"),
            })

        if etype == "gate_result":
            gates.append({
                "ts": entry.get("ts"),
                "gateType": entry.get("details", {}).get("gateType"),
                "passed": entry.get("details", {}).get("passed"),
                "message": entry.get("message"),
            })

        if etype == "decision_made":
            decisions.append({
                "ts": entry.get("ts"),
                "decisionType": entry.get("decisionType"),
                "phase": phase,
                "message": entry.get("message"),
                "action": entry.get("action"),
            })

    return {
        "schemaVersion": AUDIT_SCHEMA_VERSION,
        "entryCount": len(entries),
        "summary": {
            "byType": by_type,
            "byPhase": by_phase,
            "byRiskLevel": by_risk,
            "actionCount": len(actions),
            "gateCount": len(gates),
            "decisionCount": len(decisions),
            "highRiskCount": len(high_risk),
        },
        "highRiskEntries": high_risk[:50],
        "recentActions": actions[-20:],
        "recentGates": gates[-20:],
        "recentDecisions": decisions[-20:],
    }


def write_audit_report(task_dir: Path) -> Path:
    """Write audit.json with summary and recent entries."""
    summary = build_audit_summary(task_dir)
    path = task_dir / "audit.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return path