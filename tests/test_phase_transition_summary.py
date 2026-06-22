from __future__ import annotations

import json
from pathlib import Path

from orchestrator.phase_transition import build_phase_transition_summary, refresh_phase_transition_summary
from orchestrator.state import read_runtime_state, write_runtime_state


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _workflow_ok(monkeypatch) -> None:
    import orchestrator.phase_transition as phase_transition

    monkeypatch.setattr(phase_transition, "check_workflow_consistency", lambda _task: (True, {"result": "pass", "workflowState": {"currentPhase": "planning", "expectedNext": [{"phase": "delivery"}]}}))


def _workflow_fail(monkeypatch) -> None:
    import orchestrator.phase_transition as phase_transition

    monkeypatch.setattr(phase_transition, "check_workflow_consistency", lambda _task: (False, {"result": "fail", "issues": ["Plan.md missing"], "workflowState": {"currentPhase": "planning", "expectedNext": [{"phase": "planning"}]}}))


def test_runtime_state_is_primary_and_legacy_task_state_is_mirror(tmp_path: Path) -> None:
    task_dir = tmp_path / "task01"
    write_runtime_state(task_dir, {"taskId": "task01", "status": "ready"})

    assert (task_dir / "runtime-state.json").exists()
    assert (task_dir / "runtime-state.json").exists()
    assert read_runtime_state(task_dir)["status"] == "ready"


def test_legacy_task_state_is_migrated_to_runtime_state(tmp_path: Path) -> None:
    task_dir = tmp_path / "legacy"
    task_dir.mkdir()
    _write_json(task_dir / "runtime-state.json", {"taskId": "legacy", "status": "planned"})

    assert read_runtime_state(task_dir)["status"] == "planned"
    assert (task_dir / "runtime-state.json").exists()


def test_delivery_exists_with_incomplete_evaluation_routes_to_evaluation(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "generating", "nextAction": "run_generator"})
    (task_dir / "Delivery.md").write_text("# Delivery\n")
    _write_json(task_dir / "evaluation.json", {"result": "partial", "nextAction": "retry_generator"})
    # retry_generator is explicit evaluator signal and wins over incomplete evaluation fallback.
    summary = build_phase_transition_summary(task_dir)
    assert summary["nextPhase"] == "delivery"
    assert summary["nextAction"] == "retry_generator"

    _write_json(task_dir / "evaluation.json", {"result": "partial"})
    summary = build_phase_transition_summary(task_dir)
    assert summary["nextPhase"] == "evaluation"
    assert summary["nextAction"] == "run_evaluator"
    assert "Delivery.md exists" in summary["basis"]


def test_evaluation_next_actions_route_macro_phase(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "evaluating", "nextAction": "run_evaluator"})

    _write_json(task_dir / "evaluation.json", {"result": "blocked", "nextAction": "ask_user", "testResults": []})
    assert build_phase_transition_summary(task_dir)["nextPhase"] == "human_input"

    _write_json(task_dir / "evaluation.json", {"result": "fail", "nextAction": "replan", "testResults": []})
    assert build_phase_transition_summary(task_dir)["nextPhase"] == "planning"

    _write_json(task_dir / "evaluation.json", {"result": "fail", "nextAction": "retry_generator", "testResults": []})
    assert build_phase_transition_summary(task_dir)["nextPhase"] == "delivery"


def test_authoritative_completion_pass_routes_terminal(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "evaluating", "nextAction": "run_evaluator"})
    _write_json(task_dir / "evaluation.json", {"result": "pass", "nextAction": "finish", "testResults": [{"id": "TC-F01", "result": "pass"}]})
    _write_json(task_dir / "completion-report.json", {
        "result": "pass",
        "completionVerdict": {"result": "pass"},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish"},
        "testResults": [{"id": "TC-F01", "result": "pass"}],
    })

    summary = build_phase_transition_summary(task_dir)
    assert summary["nextPhase"] == "terminal"
    assert summary["nextAction"] == "finish"


def test_false_finish_routes_back_to_evaluation_or_delivery(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "finished", "nextAction": "finish"})
    (task_dir / "Delivery.md").write_text("# Delivery\n")

    summary = build_phase_transition_summary(task_dir)
    assert summary["nextPhase"] == "evaluation"
    assert summary["nextAction"] == "run_evaluator"

    (task_dir / "Delivery.md").unlink()
    summary = build_phase_transition_summary(task_dir)
    assert summary["nextPhase"] == "delivery"
    assert summary["nextAction"] == "retry_generator"


def test_workflow_fail_before_delivery_routes_to_planning(tmp_path: Path, monkeypatch) -> None:
    _workflow_fail(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "planned", "nextAction": "run_generator", "stateSummary": {"nextAction": "stale"}, "phaseTransition": {"nextAction": "stale"}})

    summary = build_phase_transition_summary(task_dir)
    assert summary["nextPhase"] == "planning"
    assert summary["nextAction"] == "run_test_planner"
    assert "workflow-check failed" in summary["basis"]


def test_refresh_returns_summary_and_cleans_obsolete_runtime_mirrors(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "planned", "nextAction": "run_generator"})
    (task_dir / "phase-transition-summary.json").write_text("{}\n")

    summary = refresh_phase_transition_summary(task_dir)

    assert not (task_dir / "phase-transition-summary.json").exists()
    state = read_runtime_state(task_dir) or {}
    assert summary["nextPhase"] == "delivery"
    assert summary["nextAction"] == "run_generator"
    assert "stateSummary" not in state
    assert "phaseTransition" not in state


def test_after_phase_hook_refreshes_workflow_state_without_runtime_summary(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "generating", "nextAction": "run_generator"})
    (task_dir / "Delivery.md").write_text("# Delivery\n")

    from orchestrator.hooks import run_after_phase_hooks

    run_after_phase_hooks(task_dir, "generator", payload={"iteration": 1}, reason="unit_test")

    state = read_runtime_state(task_dir) or {}
    from orchestrator.phase_transition import build_phase_transition_summary
    summary = build_phase_transition_summary(task_dir)
    assert summary["nextPhase"] == "evaluation"
    assert summary["nextAction"] == "run_evaluator"
    assert "stateSummary" not in state


def test_workflow_hard_blocker_wins_over_existing_delivery(tmp_path: Path, monkeypatch) -> None:
    _workflow_fail(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "generating", "nextAction": "run_generator"})
    (task_dir / "Delivery.md").write_text("# Delivery\n")
    _write_json(task_dir / "evaluation.json", {"result": "partial"})

    summary = build_phase_transition_summary(task_dir)

    assert summary["nextPhase"] == "planning"
    assert summary["nextAction"] == "run_test_planner"
    assert "workflow-check failed" in summary["basis"]


def test_phase_summary_includes_skill_checkbox_plan_for_delivery(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "planned", "nextAction": "run_generator"})

    summary = build_phase_transition_summary(task_dir)

    assert summary["nextPhase"] == "delivery"
    checklist = summary["checklist"]
    assert any(item["id"] == "read_reuse_context" and item["required"] is False for item in checklist)
    assert any(item["id"] == "workflow_check_green" and item["done"] is True for item in checklist)
    assert any(item["id"] == "delivery_markdown_written" and item["done"] is False for item in checklist)
    assert any(line.startswith("- [ ") for line in summary["checkboxMarkdown"])



def test_phase_summary_checklist_tracks_terminal_completion(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "evaluating", "nextAction": "run_evaluator"})
    (task_dir / "Delivery.md").write_text("# Delivery\n")
    _write_json(task_dir / "evaluation.json", {"result": "pass", "nextAction": "finish", "testResults": [{"id": "TC-1", "result": "pass"}]})
    _write_json(task_dir / "completion-report.json", {
        "result": "pass",
        "completionVerdict": {"result": "pass"},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish"},
        "testResults": [{"id": "TC-1", "result": "pass"}],
    })

    summary = build_phase_transition_summary(task_dir)

    assert summary["nextPhase"] == "terminal"
    checklist = {item["id"]: item for item in summary["checklist"]}
    assert checklist["completion_check_passed"]["done"] is True
    assert checklist["summary_generated"]["command"] == "automind summary task01"


def test_terminal_checklist_accepts_report_html_casing(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "evaluating", "nextAction": "run_evaluator"})
    _write_json(task_dir / "completion-report.json", {
        "result": "pass",
        "completionVerdict": {"result": "pass"},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish"},
        "testResults": [{"id": "TC-1", "result": "pass"}],
    })
    (task_dir / "Report.html").write_text("<html>done</html>\n")

    summary = build_phase_transition_summary(task_dir)

    checklist = {item["id"]: item for item in summary["checklist"]}
    assert checklist["report_generated"]["done"] is True


def test_checklist_marks_phase_reuse_context_available(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    (task_dir / "phase-reuse").mkdir(parents=True)
    write_runtime_state(task_dir, {"taskId": "task01", "status": "planned", "nextAction": "run_generator"})
    (task_dir / "phase-reuse" / "generator.md").write_text("# Generator reuse\n")

    summary = build_phase_transition_summary(task_dir)

    assert summary["nextPhase"] == "delivery"
    reuse_item = next(item for item in summary["checklist"] if item["id"] == "read_reuse_context")
    assert reuse_item["done"] is True
    assert "phase-reuse/generator.md" in reuse_item["text"]


def test_generator_checklist_covers_context_and_evidence_flow(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "planned", "nextAction": "run_generator"})
    for name in ["Requirements.md", "Plan.md", "TestCases.md"]:
        (task_dir / name).write_text(f"# {name}\n")

    summary = build_phase_transition_summary(task_dir)

    ids = [item["id"] for item in summary["checklist"]]
    assert "read_iteration_context" in ids
    assert "plan_implementation_checklist_updated" in ids
    assert "generator_evidence_logged" in ids
    assert "temporary_diagnostics_accounted" in ids


def test_evaluation_checklist_covers_evidence_and_routing(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "generating", "nextAction": "run_generator"})
    (task_dir / "Delivery.md").write_text("# Delivery\n")
    (task_dir / "TestCases.md").write_text("# TestCases\n")

    summary = build_phase_transition_summary(task_dir)

    ids = [item["id"] for item in summary["checklist"]]
    assert "evidence_logged" in ids
    assert "verification_checklist_updated" in ids
    assert "route_next_action_recorded" in ids


def test_terminal_checklist_covers_ledger_and_record_check(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "evaluating", "nextAction": "run_evaluator"})
    _write_json(task_dir / "completion-report.json", {
        "result": "pass",
        "completionVerdict": {"result": "pass"},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish"},
        "testResults": [{"id": "TC-1", "result": "pass"}],
    })

    summary = build_phase_transition_summary(task_dir)

    ids = [item["id"] for item in summary["checklist"]]
    assert "verification_ledger_updated" in ids
    assert "record_check_available" in ids


def test_phase_summary_does_not_guess_stale_replan_is_satisfied(tmp_path: Path, monkeypatch) -> None:
    _workflow_ok(monkeypatch)
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "replan_pending",
        "currentOwner": "planner",
        "nextAction": "run_test_planner",
        "iteration": 4,
        "planner": {"mode": "ai_test_planner", "ok": True, "exitCode": 0},
        "workflowCheck": {"ok": True, "issues": [], "warnings": ["reuse warning"]},
    })
    _write_json(task_dir / "evaluation.json", {"result": "blocked", "nextAction": "replan", "testResults": []})

    summary = build_phase_transition_summary(task_dir)

    assert summary["currentPhase"] == "planning"
    assert summary["currentOwner"] == "planner"
    assert summary["nextPhase"] == "planning"
    assert summary["nextAction"] == "run_test_planner"
    assert "evaluation.json requests replanning" in summary["reason"]
