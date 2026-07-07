"""TC-R09: Audit Log tests.

Test audit event recording, audit.json generation, and decision log enhancements.
"""
from __future__ import annotations

from pathlib import Path

from orchestrator.audit import (
    AUDIT_SCHEMA_VERSION,
    append_audit_entry,
    audit_path,
    build_audit_summary,
    read_audit_log,
    record_action,
    record_branch,
    record_decision,
    record_gate,
    record_policy,
    record_recovery,
    write_audit_report,
)
from orchestrator.state import (
    append_decision_log,
    format_recent_decisions,
    read_runtime_state,
    write_runtime_state,
)


def _make_task_dir(tmp_path: Path) -> Path:
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "ready"})
    return task_dir


def test_append_audit_entry_writes_entry(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    entry = append_audit_entry(
        task_dir,
        "decision_made",
        iteration=1,
        phase="evaluator",
        message="Test decision",
        decision_type="gate",
        reason="test",
        action="finish",
        risk_level="high",
    )
    assert entry["type"] == "decision_made"
    assert entry["iteration"] == 1
    assert entry["phase"] == "evaluator"
    assert entry["message"] == "Test decision"
    assert entry["decisionType"] == "gate"
    assert entry["reason"] == "test"
    assert entry["action"] == "finish"
    assert entry["riskLevel"] == "high"
    assert "ts" in entry


def test_read_audit_log_returns_entries(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    append_audit_entry(task_dir, "decision_made", iteration=1, phase="evaluator", message="d1")
    append_audit_entry(task_dir, "action_executed", iteration=1, phase="generator", message="a1")
    entries = read_audit_log(task_dir)
    assert len(entries) == 2
    assert entries[0]["type"] == "decision_made"
    assert entries[1]["type"] == "action_executed"


def test_record_decision_writes_entry(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    record_decision(
        task_dir,
        iteration=1,
        phase="evaluator",
        message="Task completed",
        decision_type="finish",
        reason="all tests passed",
        context={"tc_passed": 5, "tc_total": 5},
        action="finish",
        risk_level="low",
    )
    entries = read_audit_log(task_dir)
    assert len(entries) == 1
    assert entries[0]["type"] == "decision_made"
    assert entries[0]["decisionType"] == "finish"
    assert entries[0]["details"]["tc_passed"] == 5


def test_record_branch_writes_entry(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    record_branch(
        task_dir,
        iteration=1,
        phase="evaluator",
        condition="result == 'pass'",
        outcome="finish",
        alternatives=["retry", "replan"],
        reason="test",
    )
    entries = read_audit_log(task_dir)
    assert len(entries) == 1
    assert entries[0]["type"] == "branch_taken"
    assert entries[0]["details"]["condition"] == "result == 'pass'"
    assert entries[0]["details"]["outcome"] == "finish"
    assert entries[0]["details"]["alternatives"] == ["retry", "replan"]


def test_record_action_writes_entry(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    record_action(
        task_dir,
        iteration=1,
        phase="generator",
        action_type="agent_execution",
        target="codex",
        result="success",
        details={"exitCode": 0, "durationMs": 1234},
    )
    entries = read_audit_log(task_dir)
    assert len(entries) == 1
    assert entries[0]["type"] == "action_executed"
    assert entries[0]["details"]["actionType"] == "agent_execution"
    assert entries[0]["details"]["target"] == "codex"


def test_record_gate_writes_entry(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    record_gate(
        task_dir,
        iteration=1,
        phase="evaluator",
        gate_type="completion_gate",
        passed=True,
        message="Completion check passed",
        details={"issues": []},
    )
    entries = read_audit_log(task_dir)
    assert len(entries) == 1
    assert entries[0]["type"] == "gate_result"
    assert entries[0]["details"]["gateType"] == "completion_gate"
    assert entries[0]["details"]["passed"] is True
    assert entries[0]["riskLevel"] == "low"


def test_record_gate_failed_has_high_risk(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    record_gate(
        task_dir,
        iteration=1,
        phase="evaluator",
        gate_type="completion_gate",
        passed=False,
        message="Completion check failed",
    )
    entries = read_audit_log(task_dir)
    assert entries[0]["riskLevel"] == "high"


def test_record_policy_writes_entry(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    record_policy(
        task_dir,
        iteration=1,
        phase="evaluator",
        policy_name="retry_policy",
        input_data={"attempt": 3, "max_retries": 5},
        output_data={"should_retry": True},
        decision="retry_generator",
    )
    entries = read_audit_log(task_dir)
    assert len(entries) == 1
    assert entries[0]["type"] == "policy_evaluation"
    assert entries[0]["details"]["policyName"] == "retry_policy"


def test_record_recovery_writes_entry(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    record_recovery(
        task_dir,
        iteration=1,
        phase="evaluator",
        attempt=1,
        strategy="platform_self_repair",
        result="success",
        reason="build failed due to missing dependency",
    )
    entries = read_audit_log(task_dir)
    assert len(entries) == 1
    assert entries[0]["type"] == "recovery_attempt"
    assert entries[0]["details"]["strategy"] == "platform_self_repair"
    assert entries[0]["riskLevel"] == "high"


def test_build_audit_summary_aggregates_entries(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    record_decision(task_dir, iteration=1, phase="evaluator", message="d1", decision_type="finish")
    record_action(task_dir, iteration=1, phase="generator", action_type="agent", target="codex", result="success")
    record_gate(task_dir, iteration=1, phase="evaluator", gate_type="completion_gate", passed=True)
    record_decision(task_dir, iteration=2, phase="evaluator", message="d2", decision_type="retry", risk_level="high")

    summary = build_audit_summary(task_dir)
    assert summary["schemaVersion"] == AUDIT_SCHEMA_VERSION
    assert summary["entryCount"] == 4
    assert summary["summary"]["decisionCount"] == 2
    assert summary["summary"]["actionCount"] == 1
    assert summary["summary"]["gateCount"] == 1
    assert summary["summary"]["highRiskCount"] == 1
    assert len(summary["highRiskEntries"]) == 1


def test_write_audit_report_creates_file(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    record_decision(task_dir, iteration=1, phase="evaluator", message="test", decision_type="finish")
    path = write_audit_report(task_dir)
    assert path.exists()
    assert path == task_dir / "audit.json"


def test_decision_log_with_structured_fields(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    append_decision_log(
        task_dir,
        iteration=1,
        phase="generator",
        text="Implement T1",
        decision_type="action",
        reason="user requirement",
        context={"tc": "T1"},
        action="code_generation",
        risk_level="low",
    )
    state = read_runtime_state(task_dir)
    log = state["decisionLog"]
    assert len(log) == 1
    entry = log[0]
    assert entry["decisionType"] == "action"
    assert entry["reason"] == "user requirement"
    assert entry["context"]["tc"] == "T1"
    assert entry["action"] == "code_generation"
    assert entry["riskLevel"] == "low"


def test_decision_log_risk_level_auto_inference(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    append_decision_log(task_dir, iteration=1, phase="evaluator", text="finish", decision_type="finish")
    append_decision_log(task_dir, iteration=2, phase="evaluator", text="retry", decision_type="retry")
    append_decision_log(task_dir, iteration=3, phase="evaluator", text="gate", decision_type="gate")

    state = read_runtime_state(task_dir)
    assert state["decisionLog"][0]["riskLevel"] == "medium"
    assert state["decisionLog"][1]["riskLevel"] == "low"
    assert state["decisionLog"][2]["riskLevel"] == "high"


def test_decision_log_risk_level_auto_inference_with_fail_reason(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    append_decision_log(task_dir, iteration=1, phase="evaluator", text="finish failed", decision_type="finish", reason="completion failed")
    state = read_runtime_state(task_dir)
    assert state["decisionLog"][0]["riskLevel"] == "high"


def test_format_recent_decisions_with_decision_type(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    append_decision_log(task_dir, iteration=1, phase="generator", text="Implement T1", decision_type="action")
    append_decision_log(task_dir, iteration=2, phase="evaluator", text="Verify T1", decision_type="gate")

    state = read_runtime_state(task_dir)
    rendered = format_recent_decisions(state)
    assert "type=action" in rendered
    assert "type=gate" in rendered


def test_audit_redacts_sensitive_data(tmp_path):
    task_dir = _make_task_dir(tmp_path)
    record_decision(
        task_dir,
        iteration=1,
        phase="evaluator",
        message="Test",
        decision_type="gate",
        context={"api_key": "sk-abc123", "secret": "secret123"},
    )
    entries = read_audit_log(task_dir)
    assert entries[0]["details"]["api_key"] == "<redacted>"
    assert entries[0]["details"]["secret"] == "<redacted>"