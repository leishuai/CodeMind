"""Smoke/regression CLI command handlers."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from orchestrator.completion import build_completion_report, write_completion_ledger
from orchestrator.config import AUTOMIND_ROOT, AUTOMIND_WORKSPACE_ROOT, LOCAL_REUSE_INDEX_PATH, MAX_ITERATIONS, TASKS_DIR
from orchestrator.evaluation_result import apply_evaluation_result
from orchestrator.probe_records import write_json_file
from orchestrator.validation_history import append_validation_history
from orchestrator.console import error, read_tail, success, warn
from orchestrator.context_packs import build_evaluator_context_pack
from orchestrator.records import check_task_records, reconcile_validation_status
from orchestrator.reports import build_status_guidance, build_summary_reuse_status
from orchestrator.reuse import accumulated_business_summary_files, render_prompt_template, write_reuse_context
from orchestrator.state import ensure_dir, read_evaluation_json, read_runtime_state, update_runtime_state, write_evaluation_json, write_runtime_state
from orchestrator.summary import build_summary_refiner_seed, ensure_summary_generated, generate_summary, validate_ai_summary_refinement
from orchestrator.workflow import check_workflow_consistency, validate_planner_artifacts
from orchestrator.workflow_contract import write_workflow_contract


def _main_attr(name: str):
    from orchestrator import main as _main
    return getattr(_main, name)


def create_task(*args, **kwargs):
    return _main_attr("create_task")(*args, **kwargs)


def generate_brainstorm_md(*args, **kwargs):
    return _main_attr("generate_brainstorm_md")(*args, **kwargs)


def generate_requirements_md(*args, **kwargs):
    return _main_attr("generate_requirements_md")(*args, **kwargs)


def generate_testcases_md(*args, **kwargs):
    return _main_attr("generate_testcases_md")(*args, **kwargs)


def generate_plan_md(*args, **kwargs):
    return _main_attr("generate_plan_md")(*args, **kwargs)


def generate_validation_md(*args, **kwargs):
    return _main_attr("generate_validation_md")(*args, **kwargs)


def normalize_evaluation(*args, **kwargs):
    return _main_attr("normalize_evaluation")(*args, **kwargs)


def run_harness_loop(*args, **kwargs):
    return _main_attr("run_harness_loop")(*args, **kwargs)


def cmd_delivery_gate_smoke(*args, **kwargs):
    return _main_attr("cmd_delivery_gate_smoke")(*args, **kwargs)


def cmd_dependency_setup_smoke(*args, **kwargs):
    return _main_attr("cmd_dependency_setup_smoke")(*args, **kwargs)


def cmd_mobile_review_gate_smoke(*args, **kwargs):
    return _main_attr("cmd_mobile_review_gate_smoke")(*args, **kwargs)


def cmd_ui_action_capability_smoke(*args, **kwargs):
    return _main_attr("cmd_ui_action_capability_smoke")(*args, **kwargs)

def cmd_context_pack_smoke():
    """Create a tiny task and verify Evaluator context pack excludes Generator logs."""
    task_code = "context_isolation_smoke"
    task_dir = TASKS_DIR / task_code
    ensure_dir(task_dir)
    for name, text in {
        "Requirements.md": "# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — Evaluator context isolation\n- **AC-001**: Evaluator context isolation smoke passes.\n  - Verification method: context-pack validation\n",
        "Plan.md": "# Plan\n\nCreate an evaluator context pack.\n",
        "Delivery.md": "# Delivery\n\nGenerator says the smoke is ready.\n",
        "Validation.md": "# Validation\n",
        "evaluation.json": json.dumps({"iteration": 0, "result": "in_progress", "summary": "seed", "failedChecks": [], "nextAction": "retry_generator"}, ensure_ascii=False, indent=2),
        "runtime-state.json": json.dumps({"taskId": task_code, "status": "evaluating", "iteration": 1}, ensure_ascii=False, indent=2),
    }.items():
        (task_dir / name).write_text(text)
    iter_log_dir = task_dir / "logs" / "iter-1"
    ensure_dir(iter_log_dir)
    (iter_log_dir / "generator.log").write_text("SECRET_GENERATOR_TRANSCRIPT_SHOULD_NOT_APPEAR")
    pack = build_evaluator_context_pack(task_dir, 1, iter_log_dir)
    md = Path(pack["markdownPath"]).read_text(errors="ignore")
    js = json.loads(Path(pack["jsonPath"]).read_text())
    if "SECRET_GENERATOR_TRANSCRIPT_SHOULD_NOT_APPEAR" in md or "SECRET_GENERATOR_TRANSCRIPT_SHOULD_NOT_APPEAR" in json.dumps(js):
        error("context pack leaked generator.log content")
        sys.exit(1)
    if js.get("isolation", {}).get("inheritsGeneratorContext") is not False:
        error("context pack missing isolation flag")
        sys.exit(1)
    success("context-isolation smoke passed")

def cmd_demo_retry():
    """\u8fd0\u884c\u6700\u5c0f fail -> retry -> pass demo"""
    user_input = "demo: fail once then pass"
    task_code, task_dir = create_task(user_input)
    generate_brainstorm_md(task_dir, user_input)
    generate_requirements_md(task_dir, user_input)
    generate_testcases_md(task_dir, user_input)
    generate_plan_md(task_dir, user_input)
    generate_validation_md(task_dir)
    update_runtime_state(task_dir,
        status="planned",
        currentOwner="planner",
        nextAction="run_generator"
    )

    success(f"Starting demo task: {task_code}")
    result = run_harness_loop(task_code, agent="codex", mock_sequence=["fail", "pass"])
    if result:
        ensure_summary_generated(task_code, reason="demo_retry_finish", ai_agent=None)
        success("Demo completed: fail -> retry -> pass verified")
    else:
        warn("Demo failed")

    print(f"DEMO_TASK={task_code}")

def cmd_planner_smoke():
    """Verify planner template rendering and deterministic artifact validation offline."""
    task_code = "planner_refiner_smoke"
    task_dir = TASKS_DIR / task_code
    if task_dir.exists():
        shutil.rmtree(task_dir)
    ensure_dir(task_dir)
    user_input = "Build a calculator that adds, subtracts, multiplies, divides, and handles divide-by-zero. verifyCommand: `python3 -m pytest`"
    task_code_created, created_dir = create_task(user_input)
    if created_dir != task_dir:
        if task_dir.exists():
            shutil.rmtree(task_dir)
        created_dir.rename(task_dir)
        task_code = task_dir.name
        state = read_runtime_state(task_dir) or {}
        state["taskId"] = task_code
        write_runtime_state(task_dir, state)
    generate_brainstorm_md(task_dir, user_input)
    generate_requirements_md(task_dir, user_input)
    generate_testcases_md(task_dir, user_input)
    generate_plan_md(task_dir, user_input)
    write_workflow_contract(task_dir)
    generate_validation_md(task_dir)
    prompt = render_prompt_template("phase2_planner_prompt.md", task_dir=task_dir, user_input=user_input)
    if "{task_dir}" in prompt or "{user_input}" in prompt:
        error("planner template placeholders were not rendered")
        sys.exit(1)
    ok, issues = validate_planner_artifacts(task_dir)
    if not ok:
        error("planner scaffold validation failed: " + "; ".join(issues))
        sys.exit(1)
    ensure_dir(task_dir / "logs" / "planner")
    (task_dir / "logs" / "planner" / "planner.log").write_text(prompt)
    success("planner-refiner smoke passed")

def cmd_unblock_gate_smoke():
    """Verify completion/workflow/record gates reject bad temporary unblock records."""
    task_code = "verification_unblock_gate_smoke"
    task_dir = TASKS_DIR / task_code
    if task_dir.exists():
        shutil.rmtree(task_dir)
    ensure_dir(task_dir / "logs" / "iter-1")
    iter_dir = task_dir / "logs" / "iter-1"
    for name, text in {
        "Brainstorm.md": "# Brainstorm\n\n## Assumptions / Questions\n- No open questions.\n\n## Pre-implementation user review decision\n- decision: auto_proceed\n- reason: gate smoke uses fixture files only.\n",
        "Requirements.md": "# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — Build verification unblock records are gated\n- **AC-001**: Completion rejects active or unrecorded temporary unblock changes.\n  - Verification method: completion-check / workflow-check / record-check\n",
        "TestCases.md": "# TestCases\n\nQuality coverage: not applicable for this gate fixture; the only purpose is machine gate behavior.\n\n| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeAutonomy command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n| TC-F01 | R01 / AC-001 | Functional | runtime | Prepare fixture task and logs. | `automind completion-check verification_unblock_gate_smoke` | Run completion-check, workflow-check, and record-check against the fixture. | PASS only when VUC records have checkpoint/diff, restore evidence, verification evidence, and no active status. | - | yes |\n",
        "Plan.md": "# Plan\n\n## First functional batch\n- TC-F01\n\n## Verification command\n- `automind completion-check verification_unblock_gate_smoke`\n- `automind workflow-check verification_unblock_gate_smoke`\n- `automind record-check verification_unblock_gate_smoke`\n\n## Verification unblock policy\n- Temporary verification unblock changes must be recorded and restored/promoted before finish.\n\n## Implementation Checklist\n| ID | Source | Status | Owner | Evidence | Notes |\n|----|--------|--------|-------|----------|-------|\n| T01 | R01 / AC-001 / TC-F01 | done | generator | logs/iter-1/generator.log | Fixture created. |\n\n## Verification Checklist\n| ID | Required | Status | Owner | Evidence | Notes |\n|----|----------|--------|-------|----------|-------|\n| TC-F01 | yes | pass | evaluator | logs/iter-1/evaluator.log | Gate fixture verified. |\n",
        "Delivery.md": "# Delivery\n\n## Iteration 1 - Gate fixture\n- Environment: fixture task\n- Commands: completion-check / workflow-check / record-check\n- Evidence: logs/iter-1/evaluator.log\n- Covered testcases: TC-F01\n- Reusable findings: VUC records need hard gates.\n- Avoid repeating: Do not finish with active or unrecorded temporary unblock changes.\n",
        "Validation.md": "# Validation\n\n## Iteration 1 - Gate fixture\n- Environment: fixture task\n- Commands: completion-check / workflow-check / record-check\n- Result: PASS\n- Failure category: none\n- Evidence: logs/iter-1/evaluator.log\n- Temporary verification unblock changes: VUC-001 status=restored\n- Reusable findings: VUC records need hard gates.\n- Avoid repeating: Do not finish with active or unrecorded temporary unblock changes.\n",
    }.items():
        (task_dir / name).write_text(text)
    (iter_dir / "env.json").write_text(json.dumps({"cwd": str(str(AUTOMIND_WORKSPACE_ROOT))}, ensure_ascii=False, indent=2))
    (iter_dir / "commands.md").write_text("# Commands\n\n- gate smoke fixture\n")
    (iter_dir / "generator.log").write_text("fixture generator log\n")
    (iter_dir / "evaluator.log").write_text("fixture evaluator log\n")
    (iter_dir / "vuc-diff.patch").write_text("diff --git a/tmp b/tmp\n")
    (iter_dir / "restore-check.txt").write_text("temporary files restored\n")
    (iter_dir / "build.log").write_text("verification command passed\n")
    write_runtime_state(task_dir, {
        "taskId": task_code,
        "userInput": "verification unblock gate smoke",
        "taskType": "script",
        "status": "finished",
        "iteration": 1,
        "currentOwner": "evaluator",
        "nextAction": "finish",
        "planner": {
            "mode": "gate_smoke",
            "artifactsRefined": True,
            "needsUserInput": False,
            "preImplementationReview": {
                "required": True,
                "decision": "auto_proceed",
                "confidence": "high",
                "reason": "Local fixture only.",
                "questions": [],
            },
        },
    })

    base_evaluation = {
        "iteration": 1,
        "result": "pass",
        "summary": "Temporary verification unblock gate smoke passed.",
        "failedChecks": [],
        "evidence": [{"type": "log", "path": "logs/iter-1/evaluator.log"}],
        "testResults": [{
            "testCaseId": "TC-F01",
            "result": "pass",
            "required": True,
            "acceptanceCriteria": ["AC-001"],
            "evidence": ["logs/iter-1/evaluator.log"],
            "evidenceAssessment": {
                "verdict": "proved",
                "assessor": "gate-smoke-fixture",
                "reason": "fixture evidence proves gate behavior",
                "hardMetrics": [
                    {"name": "exit_code", "value": 0, "expected": 0, "passed": True, "evidence": "logs/iter-1/evaluator.log"}
                ],
            },
        }],
        "nextAction": "finish",
    }

    # Negative 1: mentions temporary unblock but lacks structured records.
    write_evaluation_json(task_dir, {
        **base_evaluation,
        "summary": "Used a temporary workspace and temporary stub but forgot structured VUC records.",
    })
    report, _ = build_completion_report(task_dir, read_evaluation_json(task_dir), allow_synthesize_pass=False)
    if report.get("result") != "fail" or not any("verificationUnblockChanges is missing" in issue for issue in report.get("issues", [])):
        error("unblock-gate smoke failed: missing VUC record was not rejected")
        sys.exit(1)

    # Negative 2: active VUC with missing evidence blocks workflow/completion/record.
    write_evaluation_json(task_dir, {
        **base_evaluation,
        "verificationUnblockChanges": [{
            "id": "VUC-001",
            "status": "active",
            "files": ["tmp/temporary-stub.swift"],
            "reason": "simulate active temporary change",
            "checkpoint": "logs/iter-1/vuc-diff.patch",
        }],
    })
    workflow_ok, workflow_report = check_workflow_consistency(task_code)
    record_ok, record_issues = check_task_records(task_code)
    report, _ = build_completion_report(task_dir, read_evaluation_json(task_dir), allow_synthesize_pass=False)
    if workflow_ok or report.get("result") != "fail" or record_ok:
        error("unblock-gate smoke failed: active VUC was not rejected by gates")
        print(json.dumps({"workflow": workflow_report, "completion": report, "recordIssues": record_issues}, ensure_ascii=False, indent=2))
        sys.exit(1)

    # Positive: restored VUC with snapshot, restore evidence, and verification evidence passes.
    write_evaluation_json(task_dir, {
        **base_evaluation,
        "verificationUnblockChanges": [{
            "id": "VUC-001",
            "status": "restored",
            "files": ["tmp/temporary-stub.swift"],
            "reason": "simulate restored temporary verification unblock",
            "scope": "test harness",
            "diff": "logs/iter-1/vuc-diff.patch",
            "restoreEvidence": "logs/iter-1/restore-check.txt",
            "verificationEvidence": "logs/iter-1/build.log",
            "risk": "fixture only; proves gate behavior",
        }],
    })
    workflow_ok, workflow_report = check_workflow_consistency(task_code)
    report, enriched = build_completion_report(task_dir, read_evaluation_json(task_dir), allow_synthesize_pass=False)
    write_evaluation_json(task_dir, enriched)
    reconcile_validation_status(task_dir)
    record_ok, record_issues = check_task_records(task_code)
    if not workflow_ok or report.get("result") != "pass" or not record_ok:
        error("unblock-gate smoke failed: restored VUC should pass")
        print(json.dumps({"workflow": workflow_report, "completion": report, "recordIssues": record_issues}, ensure_ascii=False, indent=2))
        sys.exit(1)
    success("unblock-gate smoke passed")

def cmd_reuse_playbook_smoke():
    """Verify successful/avoid paths are summarized and surfaced in Reuse.md."""
    task_code = "reuse_playbook_smoke"
    task_dir = TASKS_DIR / task_code
    if task_dir.exists():
        shutil.rmtree(task_dir)
    ensure_dir(task_dir / "logs" / "iter-1")
    iter_dir = task_dir / "logs" / "iter-1"
    for name, text in {
        "Brainstorm.md": "# Brainstorm\n\n## Assumptions / Questions\n- No open questions.\n\n## Pre-implementation user review decision\n- decision: auto_proceed\n- reason: reuse smoke uses fixture files only.\n",
        "Requirements.md": "# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — Preserve successful verification paths for reuse\n- **AC-001**: Summary and Reuse.md contain a successful path.\n  - Verification method: summary + workflow-check\n",
        "TestCases.md": "# TestCases\n\nQuality coverage: not applicable for this reuse fixture.\n\n| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeAutonomy command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n| TC-F01 | R01 / AC-001 | Functional | runtime | Prepare fixture logs and evaluation. | `python3 -m pytest tests/reuse_smoke.py` | Prepare -> run command -> assert summary/reuse successful path -> collect evaluator log. | `logs/iter-1/evaluator.log` and `summary.md` show reusable path. | - | yes |\n",
        "Plan.md": "# Plan\n\n## First functional batch\n- TC-F01\n\n## Verification command\n- `python3 -m pytest tests/reuse_smoke.py`\n\n## Implementation Checklist\n| ID | Source | Status | Owner | Evidence | Notes |\n|----|--------|--------|-------|----------|-------|\n| T01 | R01 / AC-001 / TC-F01 | done | generator | logs/iter-1/generator.log | Fixture created. |\n\n## Verification Checklist\n| ID | Required | Status | Owner | Evidence | Notes |\n|----|----------|--------|-------|----------|-------|\n| TC-F01 | yes | pass | evaluator | logs/iter-1/evaluator.log | Reuse fixture verified. |\n",
        "Delivery.md": "# Delivery\n\n## Iteration 1 - Reuse fixture\n- Environment: fixture task\n- Commands: `python3 -m pytest tests/reuse_smoke.py`\n- Evidence: logs/iter-1/evaluator.log\n- Reusable findings: pytest command works for this fixture style.\n- Avoid repeating: Do not replace a known-good pytest path with static inspection only.\n",
        "Validation.md": "# Validation\n\n## Iteration 1 - Reuse fixture\n- Environment: cwd=fixture, python=python3\n- Commands:\n  ```bash\n  python3 -m pytest tests/reuse_smoke.py\n  ```\n- Result: PASS\n- Failure category: none\n- Evidence:\n  - `logs/iter-1/evaluator.log`\n- Reusable findings: pytest command works for this fixture style.\n- Avoid repeating: Do not replace a known-good pytest path with static inspection only.\n- Next step: finish\n",
    }.items():
        (task_dir / name).write_text(text)
    (iter_dir / "env.json").write_text(json.dumps({"cwd": str(str(AUTOMIND_WORKSPACE_ROOT)), "python": sys.executable}, ensure_ascii=False, indent=2))
    (iter_dir / "commands.md").write_text("# Iteration 1 Commands\n\n- cwd: `" + str(str(AUTOMIND_WORKSPACE_ROOT)) + "`\n- runner:\n```bash\npython3 -m pytest tests/reuse_smoke.py\n```\n")
    (iter_dir / "generator.log").write_text("fixture generator log\n")
    (iter_dir / "evaluator.log").write_text("fixture evaluator log\n")
    write_runtime_state(task_dir, {
        "taskId": task_code,
        "userInput": "reuse playbook smoke",
        "taskType": "script",
        "status": "finished",
        "iteration": 1,
        "currentOwner": "evaluator",
        "nextAction": "finish",
        "planner": {
            "mode": "reuse_smoke",
            "artifactsRefined": True,
            "needsUserInput": False,
            "preImplementationReview": {
                "required": True,
                "decision": "auto_proceed",
                "confidence": "high",
                "reason": "Local fixture only.",
                "questions": [],
            },
        },
    })
    write_evaluation_json(task_dir, {
        "iteration": 1,
        "result": "pass",
        "summary": "Reuse playbook smoke passed.",
        "failedChecks": [],
        "evidence": [{"type": "log", "path": "logs/iter-1/evaluator.log"}],
        "testResults": [{
            "testCaseId": "TC-F01",
            "result": "pass",
            "required": True,
            "acceptanceCriteria": ["AC-001"],
            "evidence": ["logs/iter-1/evaluator.log"],
            "evidenceAssessment": {
                "verdict": "proved",
                "assessor": "gate-smoke-fixture",
                "reason": "fixture evidence proves gate behavior",
                "hardMetrics": [
                    {"name": "exit_code", "value": 0, "expected": 0, "passed": True, "evidence": "logs/iter-1/evaluator.log"}
                ],
            },
        }],
        "nextAction": "finish",
    })
    report, enriched = build_completion_report(task_dir, read_evaluation_json(task_dir), allow_synthesize_pass=False)
    write_completion_ledger(task_dir, report)
    write_evaluation_json(task_dir, enriched)
    if report.get("result") != "pass":
        error("reuse-playbook smoke failed: completion fixture should pass")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(1)
    summary_reuse_before = build_summary_reuse_status(task_dir)
    if summary_reuse_before.get("ok"):
        error("reuse-playbook smoke failed: summary/reuse should be missing before summary generation")
        print(json.dumps(summary_reuse_before, ensure_ascii=False, indent=2))
        sys.exit(1)
    guidance_before = build_status_guidance(task_code)
    if guidance_before.get("summaryReuse", {}).get("ok"):
        error("reuse-playbook smoke failed: status guidance did not expose missing summary/reuse")
        print(json.dumps(guidance_before, ensure_ascii=False, indent=2))
        sys.exit(1)
    generate_summary(task_code, reason="reuse_playbook_smoke")
    summary_reuse_after = build_summary_reuse_status(task_dir)
    if not summary_reuse_after.get("ok"):
        error("reuse-playbook smoke failed: summary/reuse should pass after summary generation")
        print(json.dumps(summary_reuse_after, ensure_ascii=False, indent=2))
        sys.exit(1)
    summary_text = (task_dir / "summary.md").read_text(errors="ignore")
    reuse_index_text = read_tail(LOCAL_REUSE_INDEX_PATH, 6000)
    if "Known successful verification/build paths" not in summary_text or "Successful path:" not in reuse_index_text:
        error("reuse-playbook smoke failed: successful path was not persisted")
        sys.exit(1)

    # New task should receive Reuse.md with the successful path and workflow-check should warn
    # if planning artifacts ignore it.
    next_task = TASKS_DIR / "reuse_playbook_consumer_smoke"
    if next_task.exists():
        shutil.rmtree(next_task)
    ensure_dir(next_task)
    for name, text in {
        "Brainstorm.md": "# Brainstorm\n\n## Assumptions / Questions\n- No open questions.\n\n## Pre-implementation user review decision\n- decision: auto_proceed\n- reason: fixture only.\n",
        "Requirements.md": "# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — Consumer task has valid artifacts\n- **AC-001**: Consumer workflow-check passes with reuse warning.\n  - Verification method: workflow-check\n",
        "TestCases.md": "# TestCases\n\nQuality coverage: not applicable for this fixture.\n\n| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeAutonomy command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n| TC-F01 | R01 / AC-001 | Functional | runtime | Prepare fixture. | `python3 -m pytest tests/reuse_smoke.py` | Prepare -> run command -> assert pass -> collect log. | `logs/iter-1/evaluator.log` | - | yes |\n",
        "Plan.md": "# Plan\n\n## First functional batch\n- TC-F01\n\n## Verification command\n- `python3 -m pytest tests/reuse_smoke.py`\n\n## Implementation Checklist\n| ID | Source | Status | Owner | Evidence | Notes |\n|----|--------|--------|-------|----------|-------|\n| T01 | R01 / AC-001 / TC-F01 | todo | generator | - | - |\n\n## Verification Checklist\n| ID | Required | Status | Owner | Evidence | Notes |\n|----|----------|--------|-------|----------|-------|\n| TC-F01 | yes | todo | evaluator | - | - |\n",
    }.items():
        (next_task / name).write_text(text)
    write_runtime_state(next_task, {
        "taskId": next_task.name,
        "userInput": "reuse consumer smoke",
        "taskType": "script",
        "status": "planned",
        "iteration": 0,
        "planner": {
            "needsUserInput": False,
            "preImplementationReview": {
                "decision": "auto_proceed",
                "reason": "fixture only",
                "questions": [],
            },
        },
    })
    write_reuse_context(next_task, reason="reuse_playbook_smoke")
    reuse_text = (next_task / "Reuse.md").read_text(errors="ignore")
    workflow_ok, workflow_report = check_workflow_consistency(next_task.name)
    if "# Reuse Manifest" not in reuse_text or "phase-reuse/plan.md" not in reuse_text:
        error("reuse-playbook smoke failed: new Reuse.md did not include manifest phase-reuse pointers")
        sys.exit(1)
    if accumulated_business_summary_files() and "Legacy reuse sources overview" not in reuse_text:
        error("reuse-playbook smoke failed: local accumulated business summaries were not surfaced as legacy overview in Reuse.md")
        sys.exit(1)
    if not workflow_ok or not any("matched reuse" in warning for warning in workflow_report.get("warnings", [])):
        error("reuse-playbook smoke failed: workflow-check did not warn about unconsidered reuse/phase-reuse path")
        print(json.dumps(workflow_report, ensure_ascii=False, indent=2))
        sys.exit(1)
    success("reuse-playbook smoke passed")

def cmd_summary_refiner_smoke():
    """Verify AI summary refinement validator/seed path without launching an agent."""
    task_code = "summary_refiner_smoke"
    task_dir = TASKS_DIR / task_code
    if task_dir.exists():
        shutil.rmtree(task_dir)
    ensure_dir(task_dir / "logs" / "iter-1")
    iter_dir = task_dir / "logs" / "iter-1"
    for name, text in {
        "Brainstorm.md": "# Brainstorm\n\n## Assumptions / Questions\n- No open questions.\n\n## Pre-implementation user review decision\n- decision: auto_proceed\n- reason: summary refiner fixture only.\n",
        "Requirements.md": "# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — Summary refiner validates AI output\n- **AC-001**: AI summary refinement JSON is filtered before reuse.\n  - Verification method: summary-refiner smoke\n",
        "TestCases.md": "# TestCases\n\nQuality coverage: not applicable for this fixture.\n\n| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeAutonomy command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n| TC-F01 | R01 / AC-001 | Functional | runtime | Prepare fixture. | `python3 -m pytest tests/summary_refiner_smoke.py` | Prepare -> validate AI JSON -> generate summary -> collect log. | `logs/iter-1/evaluator.log` | - | yes |\n",
        "Plan.md": "# Plan\n\n## First functional batch\n- TC-F01\n\n## Verification command\n- `python3 -m pytest tests/summary_refiner_smoke.py`\n\n## Implementation Checklist\n| ID | Source | Status | Owner | Evidence | Notes |\n|----|--------|--------|-------|----------|-------|\n| T01 | R01 / AC-001 / TC-F01 | done | generator | logs/iter-1/generator.log | Fixture created. |\n\n## Verification Checklist\n| ID | Required | Status | Owner | Evidence | Notes |\n|----|----------|--------|-------|----------|-------|\n| TC-F01 | yes | pass | evaluator | logs/iter-1/evaluator.log | Summary refiner fixture verified. |\n",
        "Delivery.md": "# Delivery\n\n## Iteration 1 - Summary refiner fixture\n- Environment: fixture task\n- Commands: `python3 -m pytest tests/summary_refiner_smoke.py`\n- Evidence: logs/iter-1/evaluator.log\n- Reusable findings: summary refiner seed plus validator keeps AI output bounded.\n- Avoid repeating: Do not append raw AI prose directly into reuse memory.\n",
        "Validation.md": "# Validation\n\n## Iteration 1 - Summary refiner fixture\n- Environment: cwd=fixture, python=python3\n- Commands:\n  ```bash\n  python3 -m pytest tests/summary_refiner_smoke.py\n  ```\n- Result: PASS\n- Failure category: none\n- Evidence:\n  - `logs/iter-1/evaluator.log`\n- Reusable findings: summary refiner seed plus validator keeps AI output bounded.\n- Avoid repeating: Do not append raw AI prose directly into reuse memory.\n- Next step: finish\n",
    }.items():
        (task_dir / name).write_text(text)
    (iter_dir / "env.json").write_text(json.dumps({"cwd": str(str(AUTOMIND_WORKSPACE_ROOT))}, ensure_ascii=False, indent=2))
    (iter_dir / "commands.md").write_text("# Commands\n\n```bash\npython3 -m pytest tests/summary_refiner_smoke.py\n```\n")
    (iter_dir / "generator.log").write_text("fixture generator log\n")
    (iter_dir / "evaluator.log").write_text("fixture evaluator log\n")
    write_runtime_state(task_dir, {
        "taskId": task_code,
        "userInput": "summary refiner smoke",
        "taskType": "script",
        "status": "finished",
        "iteration": 1,
        "currentOwner": "evaluator",
        "nextAction": "finish",
        "planner": {"needsUserInput": False, "preImplementationReview": {"decision": "auto_proceed", "questions": []}},
    })
    write_evaluation_json(task_dir, {
        "iteration": 1,
        "result": "pass",
        "summary": "Summary refiner smoke passed.",
        "failedChecks": [],
        "evidence": [{"type": "log", "path": "logs/iter-1/evaluator.log"}],
        "testResults": [{"testCaseId": "TC-F01", "result": "pass", "required": True, "acceptanceCriteria": ["AC-001"], "evidence": ["logs/iter-1/evaluator.log"], "evidenceAssessment": {"verdict": "proved", "assessor": "smoke-fixture", "reason": "fixture evaluator evidence proves TC-F01", "hardMetrics": [{"name": "exit_code", "value": 0, "expected": 0, "passed": True, "evidence": "logs/iter-1/evaluator.log"}]}}],
        "nextAction": "finish",
    })
    report, enriched = build_completion_report(task_dir, read_evaluation_json(task_dir), allow_synthesize_pass=False)
    write_completion_ledger(task_dir, report)
    write_evaluation_json(task_dir, enriched)
    if report.get("result") != "pass":
        error("summary-refiner smoke failed: completion fixture should pass")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(1)
    seed = build_summary_refiner_seed(
        task_code,
        task_dir,
        "summary_refiner_smoke",
        read_evaluation_json(task_dir) or {},
        report,
        check_workflow_consistency(task_code)[1],
        True,
        [],
        [iter_dir],
        [{"purpose": "fixture", "command": "python3 -m pytest tests/summary_refiner_smoke.py", "evidence": "logs/iter-1/evaluator.log"}],
        [],
        ["summary refiner validator keeps AI bounded"],
        [],
    )
    cleaned, warnings = validate_ai_summary_refinement(task_dir, {
        "schema": "automind.ai_summary_refinement.v1",
        "taskCode": task_code,
        "result": "ok",
        "summary": "AI found the successful pytest path reusable.",
        "successfulPaths": [{
            "purpose": "summary refiner verification",
            "command": "python3 -m pytest tests/summary_refiner_smoke.py",
            "cwd": str(str(AUTOMIND_WORKSPACE_ROOT)),
            "preconditions": "fixture logs exist",
            "evidence": ["logs/iter-1/evaluator.log"],
            "scope": "TC-F01 / AC-001",
            "confidence": "high",
            "reason": "fixture evidence exists",
        }],
        "avoidPaths": [],
        "lessons": [{"title": "Bounded AI summary", "lesson": "Filter AI output before writing reuse memory.", "evidence": ["logs/iter-1/evaluator.log"], "confidence": "high"}],
        "downgradeOrRetract": [],
        "promotionSuggestions": [{"target": "accumulated/technical", "reason": "summary-system pattern"}],
        "knowledgeActions": [{
            "action": "upsert_raw",
            "reason": "fixture has evidence-backed reusable summary-refiner lesson",
            "rawPath": ".automind/summary/raw/script-summary/summary-refiner-smoke.md",
            "content": "# Summary Refiner Smoke\n\nFilter AI output before writing reuse memory. Evidence: logs/iter-1/evaluator.log.",
            "evidence": ["logs/iter-1/evaluator.log"],
            "indexRecord": {
                "id": "summary-refiner-smoke",
                "title": "Summary refiner smoke",
                "value": "Validate AI summary output before reuse writes.",
                "phaseApplicability": ["summary"],
                "surfaces": ["summary", "reuse"],
                "triggers": ["summary", "refiner", "reuse"],
                "confidence": "high",
            },
        }],
    })
    no_action_cleaned, no_action_warnings = validate_ai_summary_refinement(task_dir, {
        "schema": "automind.ai_summary_refinement.v1",
        "taskCode": task_code,
        "result": "no_action",
        "summary": "No high-value reusable knowledge beyond deterministic summary.",
        "knowledgeActions": [{"action": "no_action", "reason": "deterministic summary is enough"}],
    })
    if no_action_warnings or no_action_cleaned.get("knowledgeActions", [{}])[0].get("action") != "no_action":
        error("summary-refiner smoke failed: no_action knowledge action was rejected")
        print(json.dumps({"cleaned": no_action_cleaned, "warnings": no_action_warnings}, ensure_ascii=False, indent=2))
        sys.exit(1)
    if cleaned.get("result") != "ok" or not cleaned.get("successfulPaths") or not cleaned.get("knowledgeActions"):
        error("summary-refiner smoke failed: validator rejected valid refinement")
        print(json.dumps({"cleaned": cleaned, "warnings": warnings, "seed": seed}, ensure_ascii=False, indent=2))
        sys.exit(1)
    generate_summary(task_code, reason="summary_refiner_smoke")
    summary_text = (task_dir / "summary.md").read_text(errors="ignore")
    if "AI Summary Refiner" not in summary_text or "Deterministic summary extraction" not in summary_text:
        error("summary-refiner smoke failed: summary did not record deterministic/AI mode")
        sys.exit(1)
    success("summary-refiner smoke passed")

def cmd_resume_recovery_smoke():
    """Verify stage-level resume can continue from an interrupted Evaluator."""
    task_code = "resume_recovery_smoke"
    task_dir = TASKS_DIR / task_code
    if task_dir.exists():
        shutil.rmtree(task_dir)
    ensure_dir(task_dir)
    for name, text in {
        ".user_input.txt": "resume recovery smoke",
        "Brainstorm.md": "# Brainstorm\n\n## Assumptions / Questions\n- Assumption: this fixture simulates an external interruption after Generator has completed.\n- Open questions: none.\n\n## Pre-implementation user review decision\n- decision: auto_proceed\n- reason: fixture-only recovery smoke; no product/runtime code changes.\n",
        "Requirements.md": "# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — Evaluator resume\n- **AC-001**: Evaluator resume succeeds without rerunning Generator.\n  - Verification method: TC-001\n",
        "TestCases.md": "# Test Cases\n\nQuality coverage: not applicable for this recovery fixture.\n\n| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeAutonomy command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n| TC-001 | R01 / AC-001 | Functional | runtime | Fixture generator output exists; logs/iter-1/generator.log is present; runtime-state says Evaluator is next. | mock evaluator | Resume interrupted Evaluator -> run mock evaluator -> assert completion-check pass -> assert generator.log was not overwritten. | logs/iter-1/evaluator.log, VerificationLedger.json, and preserved logs/iter-1/generator.log | - | yes |\n",
        "Plan.md": "# Plan\n\n## First functional batch\n- TC-001\n\n## Verification command\n- `automind smoke resume-recovery`\n\n## Implementation Checklist\n| ID | Source | Status | Owner | Evidence | Notes |\n|----|--------|--------|-------|----------|-------|\n| T01 | R01 / AC-001 / TC-001 | done | generator | logs/iter-1/generator.log | Fixture generator output exists before resume. |\n\n## Verification Checklist\n| ID | Required | Status | Owner | Evidence | Notes |\n|----|----------|--------|-------|----------|-------|\n| TC-001 | yes | todo | evaluator | - | Resume should rerun Evaluator only. |\n",
        "Delivery.md": "# Delivery\n\nGenerator completed before the interruption and targets TC-001.\n",
        "Validation.md": "# Validation\n\n## Status\n<!-- In Progress -->\n\n## Iteration 1 - Resume recovery fixture\n- Environment: fixture task\n- Commands:\n  ```bash\n  mock evaluator\n  ```\n- Result: IN PROGRESS\n- Evidence:\n  - `logs/iter-1/evaluator.log`\n- Reusable findings: Resume can restart from persisted runtime-state/evaluation/artifacts instead of hidden chat memory.\n- Avoid repeating: Do not rerun Generator when generator.log already exists and the interruption occurred in Evaluator.\n- Next step: rerun Evaluator\n",
        "runtime-state.json": json.dumps({
            "taskId": task_code,
            "status": "evaluating",
            "iteration": 1,
            "currentOwner": "evaluator",
            "nextAction": "run_evaluator",
            "planner": {
                "mode": "ai_test_planner",
                "needsUserInput": False,
                "preImplementationReview": {"decision": "auto_proceed"},
            },
        }, ensure_ascii=False, indent=2),
        "evaluation.json": json.dumps({
            "iteration": 1,
            "result": "blocked",
            "summary": "Evaluator agent preflight or execution failed",
            "failedChecks": [{"name": "agent_execution", "reason": "simulated interruption", "category": "agent_unavailable"}],
            "nextAction": "stop",
        }, ensure_ascii=False, indent=2),
    }.items():
        (task_dir / name).write_text(text)
    iter_log_dir = task_dir / "logs" / "iter-1"
    ensure_dir(iter_log_dir)
    (iter_log_dir / "env.json").write_text(json.dumps({"cwd": str(str(AUTOMIND_WORKSPACE_ROOT)), "mode": "resume-recovery-smoke"}, ensure_ascii=False, indent=2))
    (iter_log_dir / "commands.md").write_text("# Commands\n\n```bash\nmock evaluator\n```\n")
    (iter_log_dir / "generator.log").write_text("generator completed once; should not be rerun")

    result = run_harness_loop(task_code, agent="codex", mock_sequence=["pass"])
    if not result:
        error("resume-recovery smoke failed: loop did not finish from evaluator resume")
        sys.exit(1)
    state = read_runtime_state(task_dir) or {}
    recovery = state.get("resumeRecovery") if isinstance(state.get("resumeRecovery"), dict) else {}
    if recovery.get("stage") != "evaluator":
        error(f"resume-recovery smoke failed: expected evaluator recovery, got {recovery}")
        sys.exit(1)
    generator_log = iter_log_dir / "generator.log"
    if generator_log.read_text() != "generator completed once; should not be rerun":
        error("resume-recovery smoke failed: generator.log was overwritten")
        sys.exit(1)
    success("resume-recovery smoke passed")

def cmd_agent_session_policy_smoke():
    """Verify Planner/Generator reuse primary session while Evaluator stays fresh."""
    from orchestrator.agents import build_agent_cli_command, record_agent_session_after_run

    def verify_agent(agent: str, expected_resume_prefix: list[str], session_output: str):
        task_code = f"agent_session_policy_{agent}_smoke"
        task_dir = TASKS_DIR / task_code
        if task_dir.exists():
            shutil.rmtree(task_dir)
        ensure_dir(task_dir)
        write_runtime_state(task_dir, {"taskId": task_code, "status": "ready", "iteration": 0})

        cmd1, meta1 = build_agent_cli_command(agent, "planner prompt", task_dir, phase="planner")
        if meta1.get("sessionRole") != "primary" or meta1.get("sessionAction") != "new":
            error(f"agent-session-policy smoke failed: expected new primary planner session for {agent}, got {meta1}")
            sys.exit(1)
        record_agent_session_after_run(task_dir, meta1, session_output, 0)
        session_id = str((read_runtime_state(task_dir) or {}).get("agentSessions", {}).get("primary", {}).get("sessionId") or "")
        if not session_id:
            error(f"agent-session-policy smoke failed: primary session id not recorded for {agent}; cmd={cmd1} meta={meta1}")
            sys.exit(1)

        cmd2, meta2 = build_agent_cli_command(agent, "generator prompt", task_dir, phase="generator")
        if meta2.get("sessionRole") != "primary" or meta2.get("sessionAction") != "resume" or meta2.get("sessionId") != session_id:
            error(f"agent-session-policy smoke failed: expected Generator to resume primary session for {agent}, got {meta2}")
            sys.exit(1)
        if cmd2[:len(expected_resume_prefix)] != expected_resume_prefix:
            error(f"agent-session-policy smoke failed: resume command not used for {agent}: {cmd2[:8]}")
            sys.exit(1)
        if agent == "trae" and "--resume" not in cmd2:
            error(f"agent-session-policy smoke failed: Trae resume flag missing: {cmd2[:8]}")
            sys.exit(1)
        if agent == "claude" and "--session-id" not in cmd2:
            error(f"agent-session-policy smoke failed: Claude session-id flag missing: {cmd2[:8]}")
            sys.exit(1)

        cmd3, meta3 = build_agent_cli_command(agent, "evaluator prompt", task_dir, phase="evaluator")
        if meta3.get("sessionRole") != "evaluator" or meta3.get("sessionPolicy") != "fresh-isolated" or session_id in cmd3:
            error(f"agent-session-policy smoke failed: Evaluator must be fresh-isolated for {agent}, got meta={meta3} cmd={cmd3[:8]}")
            sys.exit(1)
        if meta3.get("agentExecutionBypass") is not True:
            error(f"agent-session-policy smoke failed: Evaluator must be bypassed for {agent}, got meta={meta3}")
            sys.exit(1)
        if agent == "codex" and "--dangerously-bypass-approvals-and-sandbox" not in cmd3:
            error(f"agent-session-policy smoke failed: Codex evaluator missing dangerous bypass: {cmd3[:8]}")
            sys.exit(1)
        if agent == "claude" and "--dangerously-skip-permissions" not in cmd3:
            error(f"agent-session-policy smoke failed: Claude evaluator missing bypass permissions: {cmd3[:8]}")
            sys.exit(1)
        if agent == "trae" and "--yolo" not in cmd3:
            error(f"agent-session-policy smoke failed: Trae evaluator missing yolo: {cmd3[:8]}")
            sys.exit(1)
        record_agent_session_after_run(task_dir, meta3, "session id: 019e0000-0000-7000-8000-00000000eeee\nOK", 0)
        sessions = (read_runtime_state(task_dir) or {}).get("agentSessions") or {}
        if sessions.get("primary", {}).get("sessionId") != session_id:
            error(f"agent-session-policy smoke failed: primary session changed unexpectedly for {agent}: {sessions}")
            sys.exit(1)
        fresh_runs = sessions.get("freshRuns") or []
        if not fresh_runs or fresh_runs[-1].get("policy") != "fresh-isolated" or fresh_runs[-1].get("phase") != "evaluator":
            error(f"agent-session-policy smoke failed: evaluator fresh run not audited for {agent}: {sessions}")
            sys.exit(1)

    verify_agent("codex", ["codex", "exec", "resume"], "session id: 019e0000-0000-7000-8000-000000000001\nOK")
    verify_agent("claude", ["claude", "--print", "--dangerously-skip-permissions", "--permission-mode"], "OK")
    verify_agent("trae", ["coco", "-p"], "OK")
    success("agent-session-policy smoke passed")

def cmd_agent_failure_records_smoke():
    """Verify agent/runtime failures still leave reusable iteration records."""
    task_code = "agent_failure_records_smoke"
    task_dir = TASKS_DIR / task_code
    if task_dir.exists():
        shutil.rmtree(task_dir)
    ensure_dir(task_dir)
    for name, text in {
        ".user_input.txt": "agent failure records smoke",
        "Brainstorm.md": "# Brainstorm\n\n## Assumptions / Questions\n- Assumption: this fixture simulates an external agent/runtime failure after Phase 2 artifacts are complete.\n- Open questions: none.\n\n## Pre-implementation user review decision\n- decision: auto_proceed\n- reason: fixture-only smoke; no product/runtime code changes.\n",
        "Requirements.md": "# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — Agent/runtime failure records\n- **AC-001**: Agent failure produces env.json, commands.md, blocked Delivery.md, and record-check passes.\n  - Verification method: run `automind smoke agent-failure-records` and inspect generated failure records.\n",
        "TestCases.md": "# Test Cases\n\nQuality coverage: not applicable for this fixture.\n\n| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeAutonomy command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n| TC-001 | R01 / AC-001 | Functional | runtime | Fixture task exists and Phase 2 artifacts are complete. | `automind smoke agent-failure-records` | Run loop in unimplemented LLM mode to simulate agent failure; inspect iteration records; run record-check. | `logs/iter-1/env.json`, `logs/iter-1/commands.md`, `Delivery.md`, and `evaluation.json` exist. | - | yes |\n",
        "Plan.md": "# Plan\n\n## First functional batch\n- TC-001\n\n## Verification command\n- `automind smoke agent-failure-records`\n\n## Implementation Checklist\n| ID | Source | Status | Owner | Evidence | Notes |\n|----|--------|--------|-------|----------|-------|\n| T01 | R01 / AC-001 / TC-001 | todo | generator | - | Simulate external agent failure. |\n\n## Verification Checklist\n| ID | Required | Status | Owner | Evidence | Notes |\n|----|----------|--------|-------|----------|-------|\n| TC-001 | yes | todo | evaluator | - | Verify failure records. |\n",
        "Validation.md": "# Validation Report\n\n## Status\n<!-- In Progress -->\n\n## Latest Verification Summary\n- Latest verification time: -\n- Latest conclusion: IN PROGRESS\n- Key evidence: -\n- Next step: run agent-failure fixture.\n\n## Record Protocol\nEach validation round must record Environment, Commands, Evidence, Reusable findings, and Avoid repeating.\n\n## Failure Analysis\n",
        "runtime-state.json": json.dumps({
            "taskId": task_code,
            "status": "ready",
            "iteration": 0,
            "currentOwner": "generator",
            "nextAction": "run_generator",
            "planner": {
                "mode": "ai_test_planner",
                "ok": True,
                "needsUserInput": False,
                "preImplementationReview": {"decision": "auto_proceed"},
            },
        }, ensure_ascii=False, indent=2),
        "evaluation.json": json.dumps({
            "iteration": 0,
            "result": "in_progress",
            "summary": "seed",
            "failedChecks": [],
            "nextAction": "retry_generator",
        }, ensure_ascii=False, indent=2),
    }.items():
        (task_dir / name).write_text(text)

    result = run_harness_loop(task_code, agent="codex", mode="llm")
    if result:
        error("agent-failure-records smoke failed: simulated agent failure unexpectedly finished")
        sys.exit(1)

    iter_log_dir = task_dir / "logs" / "iter-1"
    missing = [
        str(path.relative_to(task_dir))
        for path in [
            iter_log_dir / "generator.log",
            iter_log_dir / "env.json",
            iter_log_dir / "commands.md",
            task_dir / "Delivery.md",
            task_dir / "evaluation.json",
        ]
        if not path.exists()
    ]
    if missing:
        error(f"agent-failure-records smoke failed: missing {missing}")
        sys.exit(1)

    delivery_text = (task_dir / "Delivery.md").read_text(errors="ignore")
    if "BLOCKED" not in delivery_text or "does not claim a completed implementation" not in delivery_text:
        error("agent-failure-records smoke failed: Delivery.md does not clearly record blocked Generator")
        sys.exit(1)

    ok, issues = check_task_records(task_code)
    if not ok:
        error(f"agent-failure-records smoke failed: record-check issues {issues}")
        sys.exit(1)
    success("agent-failure-records smoke passed")

def cmd_policy_guards_smoke():
    """Verify broad autonomy guardrails and hard completion/release gates."""
    if MAX_ITERATIONS != int(os.environ.get("AUTOMIND_MAX_ITERATIONS", "1000")):
        error(f"policy-guards smoke failed: MAX_ITERATIONS={MAX_ITERATIONS}")
        sys.exit(1)
    if MAX_ITERATIONS < 100:
        error(f"policy-guards smoke failed: max iteration guard should be >=100, got {MAX_ITERATIONS}")
        sys.exit(1)

    task_code = "policy_guards_smoke"
    task_dir = TASKS_DIR / task_code
    if task_dir.exists():
        shutil.rmtree(task_dir)
    ensure_dir(task_dir / "logs" / "iter-1")
    (task_dir / "Requirements.md").write_text("# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — Runtime evidence\n- **AC-001**: Required runtime testcase must pass from executed evidence, not blocker classification.\n  - Verification method: TC-F01\n")
    (task_dir / "TestCases.md").write_text("# TestCases\n\n| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeAutonomy command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n| TC-F01 | R01 / AC-001 | Functional | runtime | fixture | fixture | Run fixture verification. | logs/iter-1/evaluator.log | - | yes |\n")
    (task_dir / "logs" / "iter-1" / "evaluator.log").write_text("classified environment blocker accepted by AC-001")
    false_pass = {
        "iteration": 1,
        "result": "pass",
        "summary": "False pass: classified environment blocker accepted.",
        "failedChecks": [],
        "evidence": [{"type": "log", "path": "logs/iter-1/evaluator.log"}],
        "testResults": [{
            "testCaseId": "TC-F01",
            "result": "pass",
            "reason": "classified environment_blocked blocker accepted instead of executed verification",
            "category": "environment_blocked",
            "evidence": ["logs/iter-1/evaluator.log"],
            "evidenceAssessment": {"verdict": "blocked", "blockerCategory": "environment_blocked", "reason": "classified blocker, not proof"},
            "acceptanceCriteria": ["AC-001"],
        }],
        "nextAction": "finish",
    }
    report, _enriched = build_completion_report(task_dir, false_pass, allow_synthesize_pass=False)
    if report.get("result") != "fail" or not any("blocker evidence assessment" in issue for issue in report.get("issues", [])):
        error("policy-guards smoke failed: completion-check accepted blocker-assessed required testcase")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(1)

    task_code_runtime = "policy_runtime_evidence_smoke"
    task_dir_runtime = TASKS_DIR / task_code_runtime
    if task_dir_runtime.exists():
        shutil.rmtree(task_dir_runtime)
    ensure_dir(task_dir_runtime / "logs" / "iter-1")
    (task_dir_runtime / "Requirements.md").write_text("# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n### R02 — Runtime UI behavior\n- **AC-002**: App/UI behavior must be proven by runtime execution evidence.\n  - Verification method: TC-F02\n")
    (task_dir_runtime / "TestCases.md").write_text("# TestCases\n\n| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeAutonomy command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n| TC-F02 | R02 / AC-002 | Functional App/UI | device/runtime | fixture app available | app runtime verifier | Build/install/deploy/start -> launch/open app -> tap/click target -> assert screen state. | screenshot, UI hierarchy, XCUITest/probe-flow/app launch log. | - | yes |\n")
    (task_dir_runtime / "logs" / "iter-1" / "source-audit.txt").write_text("static source audit only; no app launch")
    runtime_false_pass = {
        "iteration": 1,
        "result": "pass",
        "summary": "False pass: only static source evidence for App/UI runtime testcase.",
        "failedChecks": [],
        "evidence": [{"type": "log", "path": "logs/iter-1/source-audit.txt"}],
        "testResults": [{
            "testCaseId": "TC-F02",
            "result": "pass",
            "reason": "source audit found UI code",
            "evidence": ["logs/iter-1/source-audit.txt"],
            "acceptanceCriteria": ["AC-002"],
        }],
        "nextAction": "finish",
    }
    runtime_report, _runtime_enriched = build_completion_report(task_dir_runtime, runtime_false_pass, allow_synthesize_pass=False)
    if runtime_report.get("result") != "fail" or not any("evidenceAssessment" in issue for issue in runtime_report.get("issues", [])):
        error("policy-guards smoke failed: completion-check accepted App/UI required testcase without positive evidenceAssessment")
        print(json.dumps(runtime_report, ensure_ascii=False, indent=2))
        sys.exit(1)

    task_code_build = "policy_clean_build_smoke"
    task_dir_build = TASKS_DIR / task_code_build
    if task_dir_build.exists():
        shutil.rmtree(task_dir_build)
    ensure_dir(task_dir_build / "logs" / "iter-1")
    (task_dir_build / "Requirements.md").write_text("# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n### R03 — Clean build evidence\n- **AC-003**: Release/merge confidence requires a clean build success.\n  - Verification method: TC-B01\n")
    (task_dir_build / "TestCases.md").write_text("# TestCases\n\n| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeAutonomy command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n| TC-B01 | R03 / AC-003 | Functional build/integration | runtime build | project dependencies available | xcodebuild build | Run clean build/release build and require successful exit. | build log with BUILD SUCCEEDED / xcodebuild exit code 0. | - | yes |\n")
    (task_dir_build / "logs" / "iter-1" / "xcodebuild.log").write_text("xcodebuild failed with exit 65; CoreSimulator/SystemConfiguration environment_blocked")
    build_false_pass = {
        "iteration": 1,
        "result": "pass",
        "summary": "False pass: clean build accepted through environment blocker.",
        "failedChecks": [],
        "evidence": [{"type": "log", "path": "logs/iter-1/xcodebuild.log"}],
        "testResults": [{
            "testCaseId": "TC-B01",
            "result": "pass",
            "reason": "classified CoreSimulator exit 65 as environment_blocked",
            "category": "environment_blocked",
            "evidence": ["logs/iter-1/xcodebuild.log"],
            "evidenceAssessment": {"verdict": "blocked", "blockerCategory": "environment_blocked", "reason": "classified build blocker, not proof"},
            "acceptanceCriteria": ["AC-003"],
        }],
        "nextAction": "finish",
    }
    build_report, _build_enriched = build_completion_report(task_dir_build, build_false_pass, allow_synthesize_pass=False)
    if build_report.get("result") != "fail" or not any("blocker evidence assessment" in issue for issue in build_report.get("issues", [])):
        error("policy-guards smoke failed: completion-check accepted release/build testcase with blocker evidenceAssessment")
        print(json.dumps(build_report, ensure_ascii=False, indent=2))
        sys.exit(1)

    (task_dir_build / "logs" / "iter-1" / "xcodebuild-success.log").write_text("xcodebuild exit code 0\nBUILD SUCCEEDED\n")
    build_true_pass = {
        **build_false_pass,
        "summary": "Clean build succeeded.",
        "evidence": [{"type": "log", "path": "logs/iter-1/xcodebuild-success.log"}],
        "testResults": [{
            "testCaseId": "TC-B01",
            "result": "pass",
            "reason": "deterministic fixture clean build evidence attached",
            "evidence": ["logs/iter-1/xcodebuild-success.log"],
            "evidenceAssessment": {
                "verdict": "proved",
                "assessor": "policy-smoke-model-fixture",
                "reason": "Model/evaluator assessment says the attached build log proves the clean-build TC.",
                "hardMetrics": [
                    {"name": "build_succeeded", "value": True, "expected": True, "passed": True, "evidence": "logs/iter-1/xcodebuild-success.log"}
                ],
            },
            "acceptanceCriteria": ["AC-003"],
        }],
    }
    build_pass_report, _build_pass_enriched = build_completion_report(task_dir_build, build_true_pass, allow_synthesize_pass=False)
    if build_pass_report.get("result") != "pass":
        error("policy-guards smoke failed: completion-check rejected clean build success evidence")
        print(json.dumps(build_pass_report, ensure_ascii=False, indent=2))
        sys.exit(1)

    task_code_status = "policy_validation_status_smoke"
    task_dir_status = TASKS_DIR / task_code_status
    if task_dir_status.exists():
        shutil.rmtree(task_dir_status)
    ensure_dir(task_dir_status / "logs" / "iter-1")
    (task_dir_status / "Requirements.md").write_text("# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n### R04 — Status consistency\n- **AC-004**: Validation.md report marker must match finished/pass machine state.\n  - Verification method: TC-F04\n")
    (task_dir_status / "Brainstorm.md").write_text("# Brainstorm\n\n## Pre-Implementation Review\nauto_proceed (smoke fixture)\n")
    (task_dir_status / "TestCases.md").write_text("# TestCases\n\n## TC-F04 — Validation status marker (required, manual)\n- runtimeLevel: static\n- preparation: smoke fixture\n- assertions: Validation.md status marker matches finished state\n- evidence: smoke fixture\n")
    (task_dir_status / "Plan.md").write_text("# Plan\n")
    (task_dir_status / "Validation.md").write_text("# Validation Report\n\n## Status\n<!-- In Progress -->\n\n## Record Protocol\nEnvironment Commands Evidence Reusable findings Avoid repeating\n")
    (task_dir_status / "runtime-state.json").write_text(json.dumps({"taskId": task_code_status, "status": "finished", "iteration": 1, "nextAction": "finish"}, ensure_ascii=False, indent=2))
    (task_dir_status / "evaluation.json").write_text(json.dumps({"iteration": 1, "result": "pass", "summary": "done", "failedChecks": [], "nextAction": "finish"}, ensure_ascii=False, indent=2))
    (task_dir_status / "VerificationLedger.json").write_text(json.dumps({"result": "pass"}, ensure_ascii=False, indent=2))
    (task_dir_status / "logs" / "iter-1" / "env.json").write_text(json.dumps({"cwd": str(AUTOMIND_WORKSPACE_ROOT), "mode": "status-smoke"}, ensure_ascii=False, indent=2))
    (task_dir_status / "logs" / "iter-1" / "commands.md").write_text("# Commands\n\n```bash\nstatus marker fixture\n```\n")
    (task_dir_status / "logs" / "iter-1" / "evaluator.log").write_text("status marker fixture\n")
    ok_status, issues_status = check_task_records(task_code_status)
    if ok_status or not any("status marker mismatch" in issue for issue in issues_status):
        error(f"policy-guards smoke failed: record-check did not catch stale Validation.md marker: {issues_status}")
        sys.exit(1)
    reconcile_validation_status(task_dir_status)
    ok_status_after, issues_status_after = check_task_records(task_code_status)
    if not ok_status_after:
        error(f"policy-guards smoke failed: validation status reconcile did not fix record-check: {issues_status_after}")
        sys.exit(1)

    normalized, errors = normalize_evaluation(task_dir, {
        "iteration": 1,
        "result": "blocked",
        "summary": "Device unavailable",
        "failedChecks": [{"name": "device", "category": "mobile_device_unavailable", "reason": "no device"}],
        "nextAction": "stop",
    }, 1)
    if normalized.get("nextAction") != "ask_user" or not normalized.get("askUserQuestion"):
        error(f"policy-guards smoke failed: recoverable blocker stop was not normalized to ask_user: {normalized}, errors={errors}")
        sys.exit(1)

    env_normalized, env_errors = normalize_evaluation(task_dir, {
        "iteration": 1,
        "result": "blocked",
        "summary": "Build environment blocked but may be unblocked/replanned",
        "failedChecks": [{"name": "build_env", "category": "environment_blocked", "reason": "missing local dependency cache"}],
        "nextAction": "stop",
    }, 1)
    if env_normalized.get("nextAction") != "replan":
        error(f"policy-guards smoke failed: environment_blocked stop should become replan, not stop/pass: {env_normalized}, errors={env_errors}")
        sys.exit(1)

    task_code_old = "policy_guards_old_limit_smoke"
    task_dir_old = TASKS_DIR / task_code_old
    if task_dir_old.exists():
        shutil.rmtree(task_dir_old)
    ensure_dir(task_dir_old)
    write_runtime_state(task_dir_old, {
        "taskId": task_code_old,
        "status": "failed",
        "iteration": 3,
        "maxIterations": 20,
        "currentOwner": "supervisor",
        "nextAction": "stop",
    })
    write_evaluation_json(task_dir_old, {
        "iteration": 3,
        "result": "blocked",
        "summary": "Old task stopped after environment blocker.",
        "failedChecks": [{"name": "build_env", "category": "environment_blocked", "reason": "old terminal policy"}],
        "nextAction": "stop",
    })
    old_state = read_runtime_state(task_dir_old) or {}
    old_loop_limit = max(MAX_ITERATIONS, int(old_state.get("maxIterations", 0) or 0))
    if old_loop_limit < 100:
        error(f"policy-guards smoke failed: old task loop limit was not widened: {old_loop_limit}")
        sys.exit(1)
    old_normalized, _old_errors = normalize_evaluation(task_dir_old, read_evaluation_json(task_dir_old), 3)
    if old_normalized.get("nextAction") != "replan":
        error(f"policy-guards smoke failed: old environment-blocked stop was not resumable/replan: {old_normalized}")
        sys.exit(1)

    success("policy-guards smoke passed")

def cmd_smoke(name: str):
    """\u8fd0\u884c\u5df2\u9a8c\u8bc1\u7684 smoke test"""
    smoke_specs = {
        "android-self-repair": {
            "script": AUTOMIND_ROOT / "scripts" / "android_self_repair_smoke.sh",
            "task": "android_self_repair_smoke",
            "success": "android-self-repair smoke passed",
            "failure": "android-self-repair smoke failed",
        },
        "android-probe-flow-self-repair": {
            "script": AUTOMIND_ROOT / "scripts" / "android_probe_flow_self_repair_smoke.sh",
            "task": "android_probe_flow_self_repair_smoke",
            "success": "android-probe-flow-self-repair smoke passed",
            "failure": "android-probe-flow-self-repair smoke failed",
        },
        "offline-demo": {
            "script": AUTOMIND_ROOT / "scripts" / "offline_demo_smoke.py",
            "task": "offline_demo_smoke",
            "success": "offline-demo smoke passed",
            "failure": "offline-demo smoke failed",
        },
        "context-isolation": {
            "builtin": cmd_context_pack_smoke,
        },
        "delivery-gate": {
            "builtin": cmd_delivery_gate_smoke,
        },
        "mobile-review-gate": {
            "builtin": cmd_mobile_review_gate_smoke,
        },
        "dependency-setup": {
            "builtin": cmd_dependency_setup_smoke,
        },
        "ui-action-capability": {
            "builtin": cmd_ui_action_capability_smoke,
        },
        "planner-refiner": {
            "builtin": cmd_planner_smoke,
        },
        "unblock-gate": {
            "builtin": cmd_unblock_gate_smoke,
        },
        "reuse-playbook": {
            "builtin": cmd_reuse_playbook_smoke,
        },
        "summary-refiner": {
            "builtin": cmd_summary_refiner_smoke,
        },
        "resume-recovery": {
            "builtin": cmd_resume_recovery_smoke,
        },
        "agent-session-policy": {
            "builtin": cmd_agent_session_policy_smoke,
        },
        "agent-failure-records": {
            "builtin": cmd_agent_failure_records_smoke,
        },
        "policy-guards": {
            "builtin": cmd_policy_guards_smoke,
        },
    }
    spec = smoke_specs.get(name)
    if not spec:
        error(f"Unknown smoke test: {name}")
        print("Available smoke tests: " + ", ".join(sorted(smoke_specs)))
        sys.exit(1)

    if "builtin" in spec:
        spec["builtin"]()
        return

    script = spec["script"]
    if not script.exists():
        error(f"Smoke script not found: {script}")
        sys.exit(1)
    task_dir = TASKS_DIR / spec["task"]
    ensure_dir(task_dir)
    env = os.environ.copy()
    env["TASK_DIR"] = str(task_dir)
    result = subprocess.run([str(script)], cwd=str(AUTOMIND_WORKSPACE_ROOT), env=env)
    if result.returncode == 0:
        success(spec["success"])
    else:
        error(spec["failure"])
        sys.exit(result.returncode)
