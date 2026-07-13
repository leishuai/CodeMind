"""Phase registry and action constants for CodeAutonomy workflow orchestration."""
from __future__ import annotations

from typing import Any

PRE_IMPLEMENTATION_DECISIONS = {"auto_proceed", "ask_user", "replan"}
EVALUATION_NEXT_ACTIONS = {
    "finish",
    "retry_generator",
    "replan",
    "ask_user",
    "stop",
    "stop_blocked",
    "pause_for_external",
}
TASK_STATE_NEXT_ACTIONS = {
    "generate_requirements",
    "run_test_planner",
    "run_generator",
    "retry_generator",
    "run_evaluator",
    "replan",
    "ask_user",
    "generate_summary",
    "finish",
    "stop",
    "stop_blocked",
    "pause_for_external",
}

PHASE_REGISTRY: dict[str, dict[str, Any]] = {
    "brainstorm": {
        "cluster": "phase2-demand-definition",
        "macroGuide": "docs/phase2-requirement.md",
        "phaseGuide": "docs/phases/brainstorm.md",
        "markdown": "Brainstorm.md",
        "json": "brainstorm.json",
        "schema": "schemas/brainstorm.schema.json",
        "checker": "brainstorm_contract",
        "next": ["requirements"],
    },
    "requirements": {
        "cluster": "phase2-demand-definition",
        "macroGuide": "docs/phase2-requirement.md",
        "phaseGuide": "docs/phases/requirements.md",
        "markdown": "Requirements.md",
        "json": "requirements.json",
        "schema": "schemas/requirements.schema.json",
        "checker": "requirements_contract",
        "next": ["testcases"],
    },
    "testcases": {
        "cluster": "phase2-verification-execution-planning",
        "macroGuide": "docs/phase2-requirement.md",
        "phaseGuide": "docs/phases/testcases.md",
        "markdown": "TestCases.md",
        "json": "testcases.json",
        "schema": "schemas/testcases.schema.json",
        "checker": "testcases_contract",
        "next": ["plan"],
    },
    "plan": {
        "cluster": "phase2-verification-execution-planning",
        "macroGuide": "docs/phase2-requirement.md",
        "phaseGuide": "docs/phases/plan.md",
        "markdown": "Plan.md",
        "json": "plan.json",
        "schema": "schemas/plan.schema.json",
        "checker": "plan_contract",
        "next": ["pre_implementation_review"],
    },
    "pre_implementation_review": {
        "cluster": "phase2-verification-execution-planning",
        "macroGuide": "docs/phase2-requirement.md",
        "phaseGuide": "docs/phases/pre-implementation-review.md",
        "markdown": "Brainstorm.md",
        "json": "pre-implementation-review.json",
        "schema": "schemas/pre-implementation-review.schema.json",
        "checker": "pre_implementation_review_contract",
        "next": ["delivery"],
    },
    "delivery": {
        "cluster": "phase3-verification",
        "macroGuide": "docs/phase3-verification.md",
        "phaseGuide": "docs/phases/delivery.md",
        "markdown": "Delivery.md",
        "json": "delivery.json",
        "schema": "schemas/delivery.schema.json",
        "checker": "delivery_contract",
        "next": ["evaluation"],
    },
    "evaluation": {
        "cluster": "phase3-verification",
        "macroGuide": "docs/phase3-verification.md",
        "phaseGuide": "docs/phases/evaluation.md",
        "markdown": "Validation.md",
        "json": "evaluation.json",
        "schema": "schemas/evaluation.schema.json",
        "checker": "evaluation_contract",
        "next": ["completion"],
    },
    "completion": {
        "cluster": "phase4-summary",
        "macroGuide": "docs/phase4-summary.md",
        "phaseGuide": "docs/phases/completion.md",
        "markdown": "summary.md",
        "json": "completion-report.json",
        "schema": "schemas/completion-report.schema.json",
        "checker": "completion_contract",
        "next": [],
    },
}


# Workflow State Model v1 ----------------------------------------------------
# These registries are the machine-readable bridge between CodeAutonomy's existing
# phase docs/checklists and the task workflow state files. They intentionally do
# not replace the artifact-oriented PHASE_REGISTRY above; instead they add a
# control-state view used by CLI and Skill mode.
STAGE_REGISTRY: dict[str, dict[str, Any]] = {
    "initialization": {
        "macroGuide": "docs/phase1-initialization.md",
        "stateFile": "stages/initialization-stage-state.json",
        "phases": ["task_setup", "context_load", "environment_readiness"],
    },
    "requirement": {
        "macroGuide": "docs/phase2-requirement.md",
        "stateFile": "stages/requirement-stage-state.json",
        "phases": ["brainstorm", "requirements", "testcases", "plan", "pre_implementation_review"],
    },
    "verification_loop": {
        "macroGuide": "docs/phase3-verification.md",
        "stateFile": "stages/verification-loop-stage-state.json",
        "phases": ["delivery", "evaluation"],
    },
    "summary": {
        "macroGuide": "docs/phase4-summary.md",
        "stateFile": "stages/summary-stage-state.json",
        "phases": ["completion"],
    },
}

INITIALIZATION_PHASES: dict[str, dict[str, Any]] = {
    "task_setup": {
        "stage": "initialization",
        "macroGuide": "docs/phase1-initialization.md",
        "phaseGuide": "docs/phase1-initialization.md",
        "checklistRefs": ["docs/phase1-initialization.md#checklist"],
        "owners": ["runtime"],
        "allowedActions": ["create_task"],
        "next": ["context_load"],
    },
    "context_load": {
        "stage": "initialization",
        "macroGuide": "docs/phase1-initialization.md",
        "phaseGuide": "docs/phase1-initialization.md",
        "checklistRefs": ["docs/phase1-initialization.md#checklist"],
        "owners": ["runtime"],
        "allowedActions": ["load_context"],
        "next": ["environment_readiness"],
    },
    "environment_readiness": {
        "stage": "initialization",
        "macroGuide": "docs/phase1-initialization.md",
        "phaseGuide": "docs/phase1-initialization.md",
        "checklistRefs": ["docs/phase1-initialization.md#checklist"],
        "owners": ["runtime", "host", "tool"],
        "allowedActions": ["check_readiness", "check_service_ready", "check_device_ready", "check_simulator_ready"],
        "next": ["brainstorm", "requirements"],
    },
}

CONTROL_PHASE_REGISTRY: dict[str, dict[str, Any]] = {
    **{key: dict(value) for key, value in PHASE_REGISTRY.items()},
    **{key: dict(value) for key, value in INITIALIZATION_PHASES.items()},
}

_PHASE_STATE_META: dict[str, dict[str, Any]] = {
    "brainstorm": {"stage": "requirement", "owners": ["planner"], "allowedActions": ["run_brainstorm", "analyze_requirement"]},
    "requirements": {"stage": "requirement", "owners": ["planner"], "allowedActions": ["analyze_requirement"]},
    "testcases": {"stage": "requirement", "owners": ["planner"], "allowedActions": ["create_testcases"]},
    "plan": {"stage": "requirement", "owners": ["planner"], "allowedActions": ["create_plan"]},
    "pre_implementation_review": {"stage": "requirement", "owners": ["planner", "user"], "allowedActions": ["run_pre_implementation_review", "request_user_decision", "wait_for_user"]},
    "delivery": {"stage": "verification_loop", "owners": ["generator"], "allowedActions": ["run_generator", "retry_generator"]},
    "evaluation": {"stage": "verification_loop", "owners": ["evaluator"], "allowedActions": ["run_evaluation", "run_verification", "judge_evidence", "retry_delivery"]},
    "completion": {"stage": "summary", "owners": ["runtime", "reporter"], "allowedActions": ["complete_task", "archive_task", "update_reuse_index", "finish_task"]},
}
for _phase, _meta in _PHASE_STATE_META.items():
    if _phase in CONTROL_PHASE_REGISTRY:
        CONTROL_PHASE_REGISTRY[_phase].update(_meta)
        CONTROL_PHASE_REGISTRY[_phase].setdefault("checklistRefs", [f"{CONTROL_PHASE_REGISTRY[_phase].get('phaseGuide', '')}#checklist"])

PHASE_TO_STAGE = {phase: meta.get("stage") for phase, meta in CONTROL_PHASE_REGISTRY.items() if meta.get("stage")}

WORKFLOW_STATUSES = {"created", "running", "waiting_user", "waiting_tool", "paused", "completed", "failed", "cancelled"}
STAGE_STATUSES = {"created", "active", "waiting_user", "waiting_tool", "completed", "failed_retryable", "failed", "paused", "cancelled"}
STATE_HEALTH = {"ok", "degraded", "reconciling", "invalid"}
WORKFLOW_OWNERS = {"runtime", "planner", "generator", "evaluator", "reporter", "user", "tool", "host"}
WORKFLOW_ACTIONS = {
    "create_task", "load_context", "check_readiness",
    "run_brainstorm", "analyze_requirement", "create_plan", "create_testcases", "run_pre_implementation_review", "request_user_decision",
    "run_generator", "retry_generator",
    "run_evaluation", "run_verification", "judge_evidence", "retry_delivery",
    "complete_task", "archive_task", "update_reuse_index",
    "wait_for_user", "wait_for_tool", "pause_task", "resume_task", "cancel_task", "fail_task", "finish_task",
    "check_service_ready", "check_device_ready", "check_simulator_ready",
    "run_unit_tests", "run_script_verification", "run_android_probe", "run_ios_probe", "run_browser_check",
}
