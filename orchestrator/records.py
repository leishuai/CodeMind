"""Record completeness checks for AutoMind task artifacts.

Record-check is the final audit that task artifacts are reusable by future
tasks. It is distinct from workflow-check and completion-check.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from orchestrator.completion import build_completion_report, validate_verification_unblock_changes
from orchestrator.artifacts import requirement_contract_paths, task_uses_single_file_requirements
from orchestrator.console import success, warn
from orchestrator.state import (
    get_task_dir,
    notify_user,
    read_evaluation_json,
    read_runtime_state,
    update_runtime_state,
)


# reason -> (severity, action, message). action 是终态语义标签，
# 外部 supervisor / `automind doctor` 通过 notifications.jsonl 拿到 action 即可路由。
_REASON_NOTIFICATION_MAP: dict[str, tuple[str, str, str]] = {
    "finish": ("info", "finish", "Task finished"),
    "loop_end": ("info", "loop_end", "Harness loop ended"),
    "max_iterations": ("warn", "max_iterations", "Reached max iterations without finish"),
    "ask_user": ("warn", "ask_user", "Hard interrupt: ask_user requested"),
    "pre_implementation_ask_user": ("warn", "ask_user", "Pre-implementation gate requires user decision"),
    "auto_replan_ask_user": ("warn", "ask_user", "Auto-replan escalated to ask_user"),
    "stop": ("error", "stop", "Task stopped"),
    "stop_blocked": ("error", "stop_blocked", "Task stopped: blocked"),
    "loop_preflight_blocked": ("error", "stop_blocked", "Loop preflight blocked"),
    "evaluator_context_invalid": ("error", "stop_blocked", "Evaluator context invalid"),
    "pause_for_external": ("warn", "pause_for_external", "Paused waiting for external condition"),
    "agent_unavailable": ("warn", "agent_unavailable", "Agent runtime unavailable"),
    "generator_agent_unavailable": ("warn", "agent_unavailable", "Generator agent unavailable"),
    "evaluator_agent_unavailable": ("warn", "agent_unavailable", "Evaluator agent unavailable"),
}


def _notification_for_reason(reason: str) -> tuple[str, str, str]:
    if reason in _REASON_NOTIFICATION_MAP:
        return _REASON_NOTIFICATION_MAP[reason]
    if "agent_unavailable" in reason:
        return ("warn", "agent_unavailable", f"Agent unavailable: {reason}")
    if reason.startswith("stop"):
        return ("error", "stop_blocked", f"Stop: {reason}")
    if "ask_user" in reason:
        return ("warn", "ask_user", f"Ask user: {reason}")
    if "pause" in reason:
        return ("warn", "pause_for_external", f"Pause: {reason}")
    return ("info", reason, f"Loop terminal: {reason}")


VALIDATION_STATUS_RE = re.compile(r"<!--\s*(In Progress|Fail|Finished)\s*-->", flags=re.IGNORECASE)


def expected_validation_status_marker(task_dir: Path) -> str | None:
    """Return the expected Validation.md marker for proven terminal success."""
    evaluation = read_evaluation_json(task_dir) or {}
    state = read_runtime_state(task_dir) or {}
    ledger: dict = {}
    ledger_path = task_dir / "VerificationLedger.json"
    if ledger_path.exists():
        try:
            ledger = json.loads(ledger_path.read_text(errors="ignore"))
        except Exception:
            ledger = {}
    if (
        evaluation.get("result") == "pass"
        and evaluation.get("nextAction") == "finish"
        and (state.get("status") == "finished" or ledger.get("result") == "pass")
    ):
        return "Finished"
    return None


def read_validation_status_marker(task_dir: Path) -> str | None:
    path = task_dir / "Validation.md"
    if not path.exists():
        return None
    match = VALIDATION_STATUS_RE.search(path.read_text(errors="ignore"))
    if not match:
        return None
    value = match.group(1).strip().lower()
    if value == "in progress":
        return "In Progress"
    return value.title()


def set_validation_status_marker(task_dir: Path, status: str) -> None:
    path = task_dir / "Validation.md"
    content = path.read_text(errors="ignore") if path.exists() else "# Validation Report\n\n## Status\n"
    replacement = f"<!-- {status} -->"
    if VALIDATION_STATUS_RE.search(content):
        content = VALIDATION_STATUS_RE.sub(replacement, content, count=1)
    elif re.search(r"^##\s+Status\s*$", content, flags=re.MULTILINE):
        content = re.sub(r"(^##\s+Status\s*$)", rf"\1\n{replacement}", content, count=1, flags=re.MULTILINE)
    else:
        content = content.rstrip() + f"\n\n## Status\n{replacement}\n"
    path.write_text(content)


def reconcile_validation_status(task_dir: Path) -> tuple[bool, str | None, str | None]:
    """Update Validation.md marker when machine state proves finish/pass."""
    expected = expected_validation_status_marker(task_dir)
    current = read_validation_status_marker(task_dir)
    if expected and current != expected:
        set_validation_status_marker(task_dir, expected)
        return True, expected, current
    return False, expected, current


def validation_status_issues(task_dir: Path) -> list[str]:
    expected = expected_validation_status_marker(task_dir)
    if not expected:
        return []
    current = read_validation_status_marker(task_dir)
    if current != expected:
        return [f"Validation.md status marker mismatch: expected {expected}, found {current or 'missing'}"]
    return []


def finalize_task_records(task_code: str, reason: str = "loop_end") -> list[str]:
    """Task结束/暂停时检查记录完整性，并把缺口写回 runtime-state 兼容镜像。"""
    task_dir = get_task_dir(task_code)
    if reason == "finish":
        reconcile_validation_status(task_dir)
    ok, issues = check_task_records(task_code)
    completion_report = None
    if reason == "finish":
        completion_report, _ = build_completion_report(task_dir, allow_synthesize_pass=False)
    update_runtime_state(
        task_dir,
        recordCheck={
            "ok": ok,
            "reason": reason,
            "checkedAt": datetime.now().isoformat(timespec="seconds"),
            "issues": issues,
        },
        completionCheck=completion_report.get("result") if completion_report else (read_runtime_state(task_dir) or {}).get("completionCheck")
    )
    if ok:
        success(f"Record check passed: {task_code}")
    else:
        warn(f"Record check found {len(issues)} issues: {task_code}")
        for issue in issues[:10]:
            warn(f"  - {issue}")
        if len(issues) > 10:
            warn(f"  ... more {len(issues) - 10} issues")
    if completion_report and completion_report.get("result") != "pass":
        warn(f"Completion check has issues at finalize: {len(completion_report.get('issues', []))}")

    # Long-run signal: emit a single terminal notification per finalize so external
    # supervisors / `automind doctor` see why the loop ended without scanning logs.
    severity, action, base_message = _notification_for_reason(reason)
    message = base_message
    if not ok:
        message = f"{base_message} (record-check: {len(issues)} issues)"
        if severity == "info":
            severity = "warn"
    payload: dict = {"reason": reason, "recordCheckOk": ok, "issueCount": len(issues)}
    if completion_report:
        payload["completionResult"] = completion_report.get("result")
    try:
        notify_user(task_dir, message, severity=severity, action=action, payload=payload)
    except Exception:
        # Notifications are best-effort; never block finalize on IO errors.
        pass

    return issues


def check_task_records(task_code: str) -> tuple[bool, list[str]]:
    """\u68c0\u67e5 task \u8bb0\u5f55\u662f\u5426\u6ee1\u8db3\u672c\u673a\u4e0b\u4e00\u4e2a task \u590d\u7528\u7684\u6700\u5c0f\u8981\u6c42。"""
    task_dir = get_task_dir(task_code)
    issues: list[str] = []
    if not task_dir.exists():
        return False, [f"Task does not exist: {task_code}"]

    required_paths = [
        *requirement_contract_paths(task_dir),
        task_dir / "Plan.md",
        task_dir / "Validation.md",
        task_dir / "evaluation.json",
    ]
    for path in required_paths:
        if not path.exists():
            issues.append(f"missing:{path.name}")
    if not (task_dir / "runtime-state.json").exists():
        issues.append("missing:runtime-state.json")

    state_for_spec = read_runtime_state(task_dir) or {}
    if task_uses_single_file_requirements(task_dir) or (task_dir / "TestCases.md").exists():
        phase2_names = ["Brainstorm.md", "TestCases.md"]
        for name in phase2_names:
            if not (task_dir / name).exists():
                issues.append(f"missing:{name}")

    validation = (task_dir / "Validation.md").read_text(errors="ignore") if (task_dir / "Validation.md").exists() else ""
    required_keyword_groups = [
        ("Environment", ["Environment", "\u73af\u5883"]),
        ("Commands", ["Commands", "\u547d\u4ee4"]),
        ("Evidence", ["Evidence", "\u8bc1\u636e"]),
        ("Reusable findings", ["Reusable findings", "Reusable finding", "\u590d\u7528\u7ed3\u8bba"]),
        ("Avoid repeating", ["Avoid repeating", "\u907f\u514d\u91cd\u590d"]),
    ]
    validation_lower = validation.lower()
    for label, alternatives in required_keyword_groups:
        if not any(keyword.lower() in validation_lower for keyword in alternatives):
            issues.append(f"Validation.md missing section/keyword:{label}")
    issues.extend(validation_status_issues(task_dir))

    evaluation = read_evaluation_json(task_dir)
    if evaluation is None:
        issues.append("evaluation.json missing_or_invalid")
    else:
        for key in ["iteration", "result", "summary", "failedChecks", "nextAction"]:
            if key not in evaluation:
                issues.append(f"evaluation.json missing:{key}")
        failed_checks = evaluation.get("failedChecks", [])
        if isinstance(failed_checks, list):
            for idx, check in enumerate(failed_checks):
                if isinstance(check, dict) and "category" not in check:
                    issues.append(f"evaluation.failedChecks[{idx}] missing:category")
        unblock_issues, _unblock_warnings = validate_verification_unblock_changes(task_dir, evaluation)
        issues.extend(unblock_issues)

    logs_dir = task_dir / "logs"
    if logs_dir.exists():
        iter_dirs = sorted(logs_dir.glob("iter-*"))
        for iter_dir in iter_dirs:
            if not (iter_dir / "env.json").exists():
                issues.append(f"{iter_dir.name} missing:env.json")
            if not (iter_dir / "commands.md").exists():
                issues.append(f"{iter_dir.name} missing:commands.md")
            if not ((iter_dir / "evaluator.log").exists() or (iter_dir / "generator.log").exists()):
                issues.append(f"{iter_dir.name} missing:evaluator.log_or_generator.log")
    else:
        issues.append("missing:logs/")

    # Delivery.md becomes mandatory only when Generator actually ran.
    state = read_runtime_state(task_dir) or {}
    generator_ran = False
    logs_dir = task_dir / "logs"
    if logs_dir.exists():
        generator_ran = any(iter_dir.is_dir() and (iter_dir / "generator.log").exists() for iter_dir in logs_dir.glob("iter-*"))
    if generator_ran and not (task_dir / "Delivery.md").exists():
        issues.append("missing:Delivery.md (required after Generator runs)")

    return len(issues) == 0, issues
