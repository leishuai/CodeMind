"""Workflow v2 phase sidecar contract tests."""
from __future__ import annotations

import json
from pathlib import Path

from orchestrator.phase_contracts import ensure_phase_contracts, validate_phase_contracts, validate_phase_registry
from orchestrator.workflow import check_workflow_consistency
from orchestrator.phase_transition import refresh_phase_transition_summary
from tests.test_runtime_proof_gate import _write_workflow_task


def test_phase_sidecars_are_materialized_by_workflow_check(monkeypatch, tmp_path: Path) -> None:
    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    import orchestrator.state as state_mod

    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")

    ok, report = check_workflow_consistency("task01")

    assert ok is True, report
    for name in ["brainstorm.json", "requirements.json", "plan.json", "testcases.json", "pre-implementation-review.json", "workflow.json"]:
        assert (task_dir / name).exists(), name
    workflow = json.loads((task_dir / "workflow.json").read_text())
    assert workflow["version"] == 2
    assert "overallStatus" not in workflow
    assert "currentPhase" not in workflow
    assert "current" not in workflow
    assert "progress" not in workflow
    assert "blockedBy" not in workflow
    assert "pendingUserAction" not in workflow
    assert workflow["phases"]["requirements"]["inputRefs"] == ["Brainstorm.md", "brainstorm.json", "Requirements.md"]
    assert workflow["phaseGraph"]["start"] == "brainstorm"
    assert workflow["phaseGraph"]["final"] == "completion"
    assert ["requirements", "testcases"] in workflow["phaseGraph"]["edges"]
    assert ["testcases", "plan"] in workflow["phaseGraph"]["edges"]
    assert ["plan", "pre_implementation_review"] in workflow["phaseGraph"]["edges"]
    assert ["pre_implementation_review", "delivery"] in workflow["phaseGraph"]["edges"]
    assert workflow["target"]["finalPhase"] == "completion"
    assert workflow["expectedNext"][0]["phase"] == "delivery"
    review_node = workflow["phases"]["pre_implementation_review"]
    assert review_node["checker"]["name"] == "pre_implementation_review_contract"
    assert review_node["next"] == ["delivery"]
    testcases_node = workflow["phases"]["testcases"]
    assert testcases_node["schema"] == "schemas/testcases.schema.json"
    assert testcases_node["cluster"] == "phase2-verification-execution-planning"
    assert testcases_node["guideRefs"]["macro"] == "docs/phase2-requirement.md"
    assert testcases_node["guideRefs"]["phase"] == "docs/phases/testcases.md"
    assert testcases_node["artifactRefs"]["markdown"] == "TestCases.md"
    assert testcases_node["checker"]["name"] == "testcases_contract"
    assert testcases_node["next"] == ["plan"]
    assert testcases_node["dependencies"]["ready"] is True
    assert testcases_node["gate"]["result"] == "pass"


def test_phase_contract_validation_fails_missing_required_testcase_evidence(tmp_path: Path) -> None:
    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    contracts = ensure_phase_contracts(task_dir, ["testcases"])
    testcases = contracts["testcases"]
    testcases["testcases"][0]["runbook"]["expectedEvidence"] = []
    issues, warnings = validate_phase_contracts(task_dir, ["testcases"])
    # validate_phase_contracts regenerates from source artifacts, so direct
    # mutation above should not poison derived state. Validate the explicit object
    # through the public single-phase validator instead.
    from orchestrator.phase_contracts import validate_phase_contract

    issues, warnings = validate_phase_contract("testcases", testcases, task_dir)
    assert any("missing expectedEvidence" in issue for issue in issues)


def test_workflow_exposes_blocked_phase_when_required_input_missing(monkeypatch, tmp_path: Path) -> None:
    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    (task_dir / "Requirements.md").unlink()
    import orchestrator.state as state_mod

    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")

    ok, report = check_workflow_consistency("task01")
    workflow = json.loads((task_dir / "workflow.json").read_text())

    assert ok is False
    assert "overallStatus" not in workflow
    assert "progress" not in workflow
    assert "blockedBy" not in workflow
    assert report["workflowState"]["result"] == "fail"
    assert report["workflowState"]["issueCount"] > 0
    assert any("missing required input" in issue for issue in report["issues"])


def test_phase_registry_references_exist() -> None:
    issues, warnings = validate_phase_registry()
    assert issues == []



def test_brainstorm_contract_projects_repository_context(tmp_path: Path) -> None:
    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    brainstorm = task_dir / "Brainstorm.md"
    brainstorm.write_text(
        brainstorm.read_text()
        + "\n## Project/context observations\n"
        + "- Checked AGENTS.md: use project-native verification.\n"
        + "- Read README.md and docs/setup.md for setup/runbook.\n"
        + "- Found scripts/test.sh and package scripts for verification.\n"
        + "\n## User intent digest\n"
        + "- Goal: improve onboarding flow so users reach activation faster.\n"
        + "- Success signal: activation event is emitted after the guided path.\n"
        + "\n## Business/product suggestions\n"
        + "- Add a lightweight empty-state hint instead of a blocking modal.\n"
        + "\n## Risk and opportunity register\n"
        + "- Risk: changing onboarding copy can affect analytics baselines.\n"
        + "- Opportunity: reuse existing activation analytics test fixtures.\n"
        + "\n## Approach options\n"
        + "- Option A: minimal copy update; low risk.\n"
        + "- Option B: guided onboarding state; higher impact.\n"
        + "\n## Recommendation\n"
        + "Choose Option A first because it fits the current workspace constraints.\n"
    )
    contracts = ensure_phase_contracts(task_dir, ["brainstorm"])
    repo = contracts["brainstorm"]["repositoryContext"]
    demand = contracts["brainstorm"]["demandAnalysis"]

    assert repo["sectionPresent"] is True
    assert repo["mentionsAgents"] is True
    assert repo["mentionsScriptsOrDocs"] is True
    assert any("AGENTS.md" in item for item in repo["agentsInstructions"])
    assert demand["hasIntentDigest"] is True
    assert demand["hasBusinessSuggestions"] is True
    assert demand["hasRiskRegister"] is True
    assert demand["hasApproachOptions"] is True
    assert "Option A" in demand["recommendation"]


def test_pre_implementation_review_ask_user_blocks_workflow(monkeypatch, tmp_path: Path) -> None:
    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state["status"] = "human_input_pending"
    state["currentOwner"] = "human"
    state["nextAction"] = "ask_user"
    state["planner"]["needsUserInput"] = True
    state["planner"]["preImplementationReview"].update({
        "decision": "ask_user",
        "needsUserInput": True,
        "question": "Confirm implementation direction?",
        "options": [{"id": "confirm", "label": "Confirm", "impact": "Proceed"}],
    })
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    import orchestrator.state as state_mod

    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")

    ok, report = check_workflow_consistency("task01")
    workflow = json.loads((task_dir / "workflow.json").read_text())

    assert ok is False
    assert "overallStatus" not in workflow
    assert "pendingUserAction" not in workflow
    assert report["workflowState"]["result"] == "fail"
    assert report["workflowState"]["issueCount"] > 0
    phase_summary = refresh_phase_transition_summary(task_dir)
    assert phase_summary["currentOwner"] == "human"
    assert phase_summary["currentPhase"] == "human_input"
    assert phase_summary["nextAction"] == "ask_user"
    assert any("pre-implementation user review is unresolved" in issue for issue in report["issues"])


def test_non_trivial_implementation_requires_ask_user_without_explicit_full_auto(monkeypatch, tmp_path: Path) -> None:
    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state["userInput"] = "修复Media停止上报，要求真机验证"
    state["planner"]["needsUserInput"] = False
    review = state["planner"]["preImplementationReview"]
    review.update({
        "decision": "auto_proceed",
        "needsUserInput": False,
        "questions": [],
    })
    review.get("decisionBundle", {}).pop("confirmedAt", None)
    review.get("decisionBundle", {}).pop("confirmedBy", None)
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    import orchestrator.state as state_mod

    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")

    ok, report = check_workflow_consistency("task01")
    assert ok is False
    assert any("must ask_user once for non-trivial implementation" in issue for issue in report["issues"])


def test_pre_implementation_review_replan_checker_warns_without_issues(tmp_path: Path) -> None:
    from orchestrator.phase_contracts import validate_phase_contract

    data = {
        "version": 1,
        "phase": "pre_implementation_review",
        "decision": "replan",
        "needsUserInput": False,
        "reviewedRefs": ["Requirements.md", "Plan.md", "TestCases.md"],
        "nextAction": "replan",
        "issues": [],
    }
    issues, warnings = validate_phase_contract("pre_implementation_review", data, tmp_path)

    assert issues == []
    assert any("should explain issues" in warning for warning in warnings)

def test_workflow_check_emits_terminal_guard_for_false_finish(monkeypatch, tmp_path: Path) -> None:
    import orchestrator.workflow as workflow

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "Brainstorm.md").write_text("# Brainstorm\n\nUser Decision Bundle\n")
    (task_dir / "Requirements.md").write_text(
        "# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n"
        "### R01 — runtime\n- **AC-001**: runtime evidence required\n  - Verification method: TC-F01\n"
    )
    (task_dir / "TestCases.md").write_text(
        "# TestCases\n\n"
        "| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n"
        "|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n"
        "| TC-F01 | R01 / AC-001 | Functional | static | preflight ready | command: pytest | run command | logs/iter-1/evidence.txt exists and pass | none | yes |\n"
    )
    (task_dir / "Plan.md").write_text(
        "# Plan\n\n## Implementation Checklist\n\n| ID | Status |\n|----|--------|\n| T01 | done |\n\n"
        "## Verification Checklist\n\n| ID | Status | Evidence |\n|----|--------|----------|\n| TC-F01 | not_run | - |\n"
    )
    (task_dir / "Delivery.md").write_text("# Delivery\n\nImplemented R01.\n")
    state = {
        "taskId": "task01",
        "status": "completed",
        "currentOwner": "ai",
        "nextAction": "done",
        "completionCheck": "fail",
        "completionVerdict": {"result": "fail", "overridesRawEvaluation": True},
        "planner": {"preImplementationReview": {"decision": "auto_proceed", "needsUserInput": False}},
    }
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    (task_dir / "brainstorm.json").write_text(json.dumps({
        "goal": "demo",
        "userNeed": "demo",
        "userStory": {"asA": "user", "iWant": "stop report", "soThat": "observe"},
        "demandAnalysis": {"summary": "ok", "goals": ["g"], "scope": ["s"], "nonGoals": ["n"], "risks": ["r"], "assumptions": ["a"], "userImpact": "u", "businessSuggestions": ["none"]},
        "repositoryContext": {"summary": "AGENTS.md and docs checked", "constraints": ["c"], "dependencies": ["d"]},
        "decisionBundle": {"summary": "ok"},
    }, ensure_ascii=False, indent=2))
    (task_dir / "requirements.json").write_text(json.dumps({"requirements": [{"id": "R01", "title": "req", "acceptanceCriteria": [{"id": "AC-001", "text": "ac"}]}]}, ensure_ascii=False, indent=2))
    (task_dir / "testcases.json").write_text(json.dumps({"testcases": [{"id": "TC-F01", "title": "tc", "requirementIds": ["R01"], "acceptanceCriteria": ["AC-001"], "priority": "P0", "required": True, "level": "functional", "runtimeLevel": "static", "runbook": {"steps": ["do"], "assertions": ["ok"]}}]}, ensure_ascii=False, indent=2))
    (task_dir / "plan.json").write_text(json.dumps({"tasks": [{"id": "T01", "title": "do", "status": "done", "testCaseRefs": ["TC-F01"], "requirementIds": ["R01"], "acceptanceCriteria": ["AC-001"], "owner": "generator"}]}, ensure_ascii=False, indent=2))
    (task_dir / "pre-implementation-review.json").write_text(json.dumps({"decision": "auto_proceed", "needsUserInput": False, "nextAction": "delivery"}, ensure_ascii=False, indent=2))
    (task_dir / "workflow.json").write_text(json.dumps({"current": {"owner": "generator", "nextAction": "run_generator", "phase": "generator", "activePhases": ["delivery"]}}, ensure_ascii=False, indent=2))
    (task_dir / "evaluation.json").write_text(json.dumps({"result": None, "nextAction": None, "testResults": []}, ensure_ascii=False, indent=2))

    monkeypatch.setattr(workflow, "get_task_dir", lambda _task_code: task_dir)
    ok, report = workflow.check_workflow_consistency("task01")

    assert ok is False
    assert report["terminalGuard"]["terminalStateObserved"] is True
    assert report["terminalGuard"]["allowTerminal"] is False
    assert report["terminalGuard"]["reason"] == "workflow_red_terminal_state"
    assert any("false_finish" in issue for issue in report["issues"])


def test_workflow_check_contract_refs_include_workflow_control_state(monkeypatch, tmp_path: Path) -> None:
    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    import orchestrator.state as state_mod

    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")

    ok, report = check_workflow_consistency("task01")

    assert ok is True, report
    refs = report["workflowState"]["contractRefs"]
    assert refs["workflowControlState"] == "automind-workflow-state.json"
    assert refs["stageState"] == "stages/*-stage-state.json"
    assert refs["phaseSummary"] == "in-memory CLI guidance projection"
    assert refs["stateSummary"] == "runtime-state.json#stateSummary (obsolete fallback)"


def test_status_guidance_exposes_workflow_control_state(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.reports import build_status_guidance
    from orchestrator.workflow_state import emit_workflow_event
    import orchestrator.state as state_mod
    import orchestrator.workflow as workflow_mod
    import orchestrator.reports as reports_mod

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "retry_pending",
        "currentOwner": "generator",
        "nextAction": "retry_generator",
        "iteration": 2,
    }, ensure_ascii=False, indent=2))
    emit_workflow_event(task_dir, {
        "type": "iteration_failed_retryable",
        "phase": "evaluation",
        "action": "judge_evidence",
        "nextAction": "retry_generator",
        "nextPhase": "delivery",
        "plannedNextPhase": "evaluation",
        "iteration": 3,
        "retryable": True,
    })
    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")
    monkeypatch.setattr(workflow_mod, "check_workflow_consistency", lambda _task: (True, {"result": "pass", "workflowState": {}}))
    monkeypatch.setattr(reports_mod, "check_workflow_consistency", lambda _task: (True, {"result": "pass", "workflowState": {}}))

    guidance = build_status_guidance("task01")

    assert guidance["workflowControlState"]["currentStage"] == "verification_loop"
    assert guidance["workflowControlState"]["nextPhase"] == "delivery"
