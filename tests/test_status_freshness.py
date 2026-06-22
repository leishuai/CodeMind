from __future__ import annotations

import json
from pathlib import Path

from orchestrator.commands.status import _freshness_summary, _latest_evidence_iteration


def _task(tmp_path: Path) -> Path:
    task = tmp_path / ".automind" / "tasks" / "task01"
    (task / "logs" / "iter-5").mkdir(parents=True)
    (task / "logs" / "iter-8").mkdir(parents=True)
    (task / "logs" / "iter-5" / "summary.json").write_text("{}\n")
    (task / "logs" / "iter-8" / "evaluator.log").write_text("fresh evaluator evidence\n")
    return task


def test_latest_evidence_iteration_counts_non_summary_evaluator_logs(tmp_path: Path) -> None:
    task = _task(tmp_path)

    assert _latest_evidence_iteration(task) == 8


def test_freshness_marks_iteration_lag_nonblocking_when_completion_passed(tmp_path: Path) -> None:
    task = _task(tmp_path)
    (task / "evaluation.json").write_text(json.dumps({"iteration": 5, "result": "pass", "nextAction": "finish", "testResults": []}) + "\n")
    (task / "VerificationLedger.json").write_text(json.dumps({"result": "pass", "generatedAt": "now"}) + "\n")

    summary = _freshness_summary(task, {"iteration": 5})

    assert summary["latestEvidenceIteration"] == 8
    assert any("non-blocking metadata lag" in issue for issue in summary["issues"])
