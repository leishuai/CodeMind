from __future__ import annotations

import json
from pathlib import Path

from orchestrator.completion import write_completion_ledger
from orchestrator.state import read_runtime_state


def test_write_completion_ledger_syncs_completion_report_and_task_state(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    report = {
        "task": "task",
        "result": "pass",
        "issues": [],
        "coverage": {"completionCheck": "pass"},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish", "iteration": 1},
        "completionVerdict": {"result": "pass", "overridesRawEvaluation": False, "reason": "completion-check passed"},
    }

    ledger_path = write_completion_ledger(task_dir, report)

    completion_path = task_dir / "completion-report.json"
    assert ledger_path == task_dir / "VerificationLedger.json"
    assert json.loads(ledger_path.read_text()) == report
    assert json.loads(completion_path.read_text()) == report
    state = read_runtime_state(task_dir) or {}
    assert state["completionCheck"] == "pass"
    assert state["verificationLedger"].endswith("VerificationLedger.json")
    assert state["completionReport"].endswith("completion-report.json")
    assert state["rawEvaluationClaim"]["nextAction"] == "finish"
    assert state["completionVerdict"]["overridesRawEvaluation"] is False
