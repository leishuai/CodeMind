from __future__ import annotations

import json
from pathlib import Path

from orchestrator.artifacts import primary_requirements_path, requirement_contract_paths, task_uses_single_file_requirements
from orchestrator.completion import build_completion_report
from orchestrator.context_packs import build_evaluator_context_pack, build_generator_context_pack
from orchestrator.records import check_task_records
from orchestrator.summary import build_summary_refiner_seed


def _write_common_task(task_dir: Path) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "Brainstorm.md").write_text("# Brainstorm\n\nauto_proceed\n")
    (task_dir / "Requirements.md").write_text(
        "# Requirements\n\n"
        "## Requirements with inline Acceptance Criteria\n\n"
        "### R01 — canonical single-file contract\n"
        "- **AC-001**: completion reads ACs from Requirements.md\n"
        "  - Verification method: TC-F01\n"
    )
    (task_dir / "TestCases.md").write_text(
        "# TestCases\n\n"
        "| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n"
        "|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n"
        "| TC-F01 | R01 / AC-001 | Functional | runtime | preflight ready | command: pytest | run command | logs/iter-1/evidence.txt exists and pass | none | yes |\n"
    )
    (task_dir / "Plan.md").write_text("# Plan\n\n## Implementation Checklist\n\n| ID | Status |\n|----|--------|\n| T01 | done |\n\n## Verification Checklist\n\n| ID | Status | Evidence |\n|----|--------|----------|\n| TC-F01 | pass | logs/iter-1/evidence.txt |\n")
    (task_dir / "Delivery.md").write_text("# Delivery\n\nImplemented R01.\n")
    (task_dir / "Validation.md").write_text(
        "# Validation\n\n"
        "## Status\n<!-- Finished -->\n\n"
        "## Environment\nok\n\n## Commands\npytest\n\n## Evidence\nlogs/iter-1/evidence.txt\n\n"
        "## Reusable findings\n- Use canonical Requirements.md.\n\n## Avoid repeating\n- none\n"
    )
    (task_dir / "runtime-state.json").write_text(json.dumps({"taskId": task_dir.name, "status": "finished", "iteration": 1}))
    iter_dir = task_dir / "logs" / "iter-1"
    iter_dir.mkdir(parents=True, exist_ok=True)
    (iter_dir / "evidence.txt").write_text("passed")
    (iter_dir / "env.json").write_text("{}")
    (iter_dir / "commands.md").write_text("```bash\npytest\n```\n")
    (iter_dir / "evaluator.log").write_text("pass")
    (iter_dir / "tc-f01-after.png").write_bytes(b"fake-png")
    (task_dir / "evaluation.json").write_text(json.dumps({
        "iteration": 1,
        "result": "pass",
        "summary": "canonical Requirements.md task passed",
        "failedChecks": [],
        "nextAction": "finish",
        "testResults": [{
            "testCaseId": "TC-F01",
            "result": "pass",
            "acceptanceCriteria": ["AC-001"],
            "evidence": ["logs/iter-1/evidence.txt", "logs/iter-1/tc-f01-after.png"],
            "evidenceAssessment": {"verdict": "proved", "machineAnchor": "logs/iter-1/evidence.txt", "hardMetrics": [{"name": "exit_code", "passed": True}]},
        }],
        "evidence": ["logs/iter-1/evidence.txt", "logs/iter-1/tc-f01-after.png"],
    }))


def test_requirement_contract_helpers_prefer_requirements_md(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    _write_common_task(task_dir)
    assert task_uses_single_file_requirements(task_dir) is True
    assert [p.name for p in requirement_contract_paths(task_dir)] == ["Requirements.md"]
    assert primary_requirements_path(task_dir).name == "Requirements.md"


def test_completion_reads_acs_from_requirements_without_legacy_require(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    _write_common_task(task_dir)
    report, enriched = build_completion_report(task_dir)
    assert report["result"] == "pass", report
    assert report["coverage"]["acceptanceCriteriaRequired"] == ["AC-001"]
    assert "AC-001" in report["coverage"]["acceptanceCriteriaCovered"]
    assert enriched["nextAction"] == "finish"


def test_context_packs_require_requirements_not_legacy_pair(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    _write_common_task(task_dir)
    iter_dir = task_dir / "logs" / "iter-2"
    gen = build_generator_context_pack(task_dir, 2, iter_dir)
    ev = build_evaluator_context_pack(task_dir, 2, iter_dir)
    assert gen["validationOk"] is True, gen
    assert ev["validationOk"] is True, ev
    gen_json = json.loads((iter_dir / "generator-context.json").read_text())
    ev_json = json.loads((iter_dir / "evaluator-context.json").read_text())
    assert any(path.endswith("Requirements.md") for path in gen_json["policy"]["requiredFiles"])
    assert not any(path.endswith("Require.md") for path in gen_json["policy"]["requiredFiles"])
    assert any(path.endswith("Requirements.md") for path in ev_json["policy"]["requiredFiles"])
    assert not any(path.endswith("Require.md") for path in ev_json["policy"]["requiredFiles"])


def test_record_check_accepts_single_file_protocol(monkeypatch, tmp_path: Path) -> None:
    task_dir = tmp_path / ".automind" / "tasks" / "task"
    _write_common_task(task_dir)
    import orchestrator.state as state_mod
    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")
    ok, issues = check_task_records("task")
    assert ok is True, issues


def test_summary_seed_includes_requirements_snippet(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    _write_common_task(task_dir)
    completion_report, _ = build_completion_report(task_dir)
    seed = build_summary_refiner_seed(
        task_code="task",
        task_dir=task_dir,
        reason="test",
        evaluation=json.loads((task_dir / "evaluation.json").read_text()),
        completion_report=completion_report,
        workflow_report={"result": "pass", "issues": [], "warnings": []},
        record_ok=True,
        record_issues=[],
        iter_dirs=[task_dir / "logs" / "iter-1"],
        successful_paths=[],
        avoid_paths=[],
        reusable=[],
        downgrade=[],
    )
    assert "Requirements.md" in seed["artifactSnippets"]
    assert "Require.md" not in seed["artifactSnippets"]
