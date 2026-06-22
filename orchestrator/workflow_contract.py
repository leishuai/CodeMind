"""Machine-readable workflow contract helpers for AutoMind.

`workflow.json` is the executable contract that connects the human-readable
Phase 2 artifacts (Requirements.md / TestCases.md / Plan.md) with Generator,
Evaluator, platform adapters, and completion-check.  It is intentionally a
small, deterministic projection first: Markdown remains the authoring surface,
while this module materializes a stable JSON shape for gates and future runners.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.artifacts import (
    extract_artifact_ids,
    extract_declared_testcases,
    read_requirements_contract_text,
)
from orchestrator.state import read_runtime_state, update_runtime_state
from orchestrator.phase_contracts import (
    PHASE_FILES,
    PHASE_GUIDES,
    PHASE_MACRO_GUIDES,
    PHASE_REGISTRY,
    PHASE_SCHEMAS,
    ensure_phase_contracts,
    phase_artifact_refs,
    phase_checker_name,
    phase_cluster,
    phase_dependency_status,
    phase_guide_refs,
    phase_next,
    required_phase_inputs,
    validate_phase_contracts,
    validate_phase_registry,
)

WORKFLOW_CONTRACT_VERSION = 2
RUNTIME_LEVELS = {"runtime", "device"}
RUNTIME_CAPABLE_TARGETS = {"real_device", "simulator_emulator", "both"}


def workflow_contract_path(task_dir: Path) -> Path:
    return task_dir / "workflow.json"


def _normalize_runtime_level(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if "device" in value or "真机" in value or "设备" in value:
        return "device"
    if "runtime" in value or "运行" in value or "simulator" in value or "emulator" in value or "模拟器" in value:
        return "runtime"
    if "integration" in value or "集成" in value:
        return "integration"
    if "unit" in value or "单测" in value:
        return "unit"
    if "manual" in value or "人工" in value:
        return "manual"
    if "static" in value or "静态" in value:
        return "static"
    return value or "unknown"


def _infer_executor(command: str, runtime_level: str, task_type: str) -> str:
    text = str(command or "").lower()
    if "android-probe-flow" in text:
        return "android-probe-flow"
    if "ios-probe-flow" in text:
        return "ios-probe-flow"
    if "ios-xcuitest" in text or "xcuitest" in text or "xcodebuild test" in text:
        return "ios-xcuitest"
    if "script-command" in text:
        return "script-command"
    if "xcodebuild" in text:
        return "xcodebuild"
    if "gradle" in text or "connectedandroidtest" in text:
        return "gradle/android"
    if any(token in text for token in ["pytest", "npm", "pnpm", "yarn", "python", "bash", "sh "]):
        return "project-native-command"
    if task_type == "android" and runtime_level in RUNTIME_LEVELS:
        return "android-probe-flow"
    if task_type == "ios" and runtime_level in RUNTIME_LEVELS:
        return "ios-probe-flow"
    if task_type == "dual" and runtime_level in RUNTIME_LEVELS:
        return "platform-probe-flow"
    return "manual-or-project-native"


def _split_actions(text: str) -> list[dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return []
    parts = [p.strip(" -`\n\t") for p in re.split(r"\s*(?:->|→|=>|;|\n)\s*", raw) if p.strip()]
    actions: list[dict[str, Any]] = []
    for idx, part in enumerate(parts, start=1):
        lower = part.lower()
        action_type = "step"
        if any(token in lower for token in ["preflight", "prepare", "setup", "准备", "前置"]):
            action_type = "prepare"
        elif any(token in lower for token in ["build", "install", "deploy", "构建", "安装"]):
            action_type = "build_install_deploy"
        elif any(token in lower for token in ["launch", "open", "start", "启动", "打开"]):
            action_type = "launch_open"
        elif any(token in lower for token in ["tap", "click", "input", "scroll", "swipe", "navigate", "点击", "输入", "滑动", "导航"]):
            action_type = "ui_action"
        elif any(token in lower for token in ["assert", "expect", "check", "visible", "exists", "断言", "预期", "检查", "可见", "存在"]):
            action_type = "assert"
        elif any(token in lower for token in ["screenshot", "hierarchy", "log", "report", "evidence", "截图", "层级", "日志", "证据"]):
            action_type = "collect_evidence"
        actions.append({"id": f"A{idx:02d}", "type": action_type, "description": part})
    return actions


def _approved_runtime_downgrade(decision_bundle: dict[str, Any]) -> bool:
    approval = decision_bundle.get("runtimeDowngradeApproval")
    return (
        isinstance(approval, dict)
        and bool(str(approval.get("approvedBy") or approval.get("signedBy") or "").strip())
        and bool(str(approval.get("approvedAt") or approval.get("signedAt") or "").strip())
    )


def read_decision_bundle(task_dir: Path) -> dict[str, Any]:
    state = read_runtime_state(task_dir) or {}
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
    bundle = review.get("decisionBundle") if isinstance(review.get("decisionBundle"), dict) else {}
    return dict(bundle)



def _clip_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"... [truncated {len(text) - limit} chars; see source artifact]"


def _compact_testcase_for_workflow(tc: dict[str, Any], *, task_type: str, runtime_proof_required: bool) -> dict[str, Any]:
    runtime_level = _normalize_runtime_level(tc.get("runtimeLevel", ""))
    executor = _infer_executor(tc.get("command", ""), runtime_level, task_type)
    is_runtime = runtime_level in RUNTIME_LEVELS
    skip_requires_approval = bool(runtime_proof_required and is_runtime)
    return {
        "id": tc.get("id"),
        "sourceRef": {"path": "TestCases.md", "id": tc.get("id")},
        "requirements": tc.get("requirements") or [],
        "acceptanceCriteria": tc.get("acceptanceCriteria") or [],
        "type": tc.get("type") or "",
        "required": bool(tc.get("required")),
        "quality": bool(tc.get("quality")),
        "runtimeLevel": runtime_level,
        "executor": executor,
        "command": _clip_text(tc.get("command") or "", 500),
        "dependency": _clip_text(tc.get("dependency") or "", 200),
        "intent": {
            "goal": f"Prove {tc.get('id')}",
            "preconditions": [_clip_text(tc.get("preconditions"), 500)] if tc.get("preconditions") else [],
            "actions": [_clip_text(item, 300) for item in _split_actions(tc.get("steps") or tc.get("command") or "")[:12]],
            "assertions": [_clip_text(item, 300) for item in _split_actions(tc.get("expectedEvidence") or "")[:12]],
            "expectedEvidence": _clip_text(tc.get("expectedEvidence") or "", 500),
        },
        "skipPolicy": {
            "allowed": True,
            "requiresUserApproval": skip_requires_approval,
            "approvalField": "runtimeDowngradeApproval" if skip_requires_approval else None,
            "allowedReasons": [
                "no_device_connected",
                "signing_unavailable",
                "unsafe_install_or_data_overwrite",
                "no_runnable_fixture",
                "user_explicitly_opted_out",
            ] if skip_requires_approval else [],
        },
    }

def build_workflow_contract(task_dir: Path) -> dict[str, Any]:
    """Build a deterministic v2 workflow orchestration contract from current artifacts."""
    state = read_runtime_state(task_dir) or {}
    decision_bundle = read_decision_bundle(task_dir)
    task_type = str(decision_bundle.get("taskType") or state.get("taskType") or "unknown").strip().lower() or "unknown"
    runtime_required_raw = str(decision_bundle.get("runtimeProofRequired") or "auto").strip().lower()
    runtime_proof_required = runtime_required_raw == "yes"
    verification_target = str(decision_bundle.get("verificationTarget") or "unknown").strip().lower() or "unknown"

    requirements_text = read_requirements_contract_text(task_dir)
    requirements = list(extract_artifact_ids(requirements_text, "R").values())
    acceptance_criteria = list(extract_artifact_ids(requirements_text, "AC").values())
    declared = extract_declared_testcases(task_dir)

    testcases: list[dict[str, Any]] = [
        _compact_testcase_for_workflow(tc, task_type=task_type, runtime_proof_required=runtime_proof_required)
        for tc in declared
    ]

    phase_contracts = ensure_phase_contracts(task_dir)
    phase_issues, phase_warnings = validate_phase_contracts(task_dir)
    phase_order = list(PHASE_REGISTRY)
    source_artifacts = ["Brainstorm.md", "Requirements.md", "TestCases.md", "Plan.md", "runtime-state.json"]

    def phase_status(phase: str) -> str:
        if phase in {"delivery", "evaluation", "completion"}:
            data = phase_contracts.get(phase, {})
            if phase == "delivery" and not data.get("exists"):
                return "pending"
            if phase == "evaluation" and data.get("result") == "not_run":
                return "pending"
            if phase == "completion" and data.get("result") == "not_run":
                return "pending"
        relevant_issues = [issue for issue in phase_issues if issue.startswith(f"{phase}.json") or issue.startswith(f"{PHASE_FILES.get(phase, phase)}")]
        dep = phase_dependency_status(task_dir, phase)
        if phase in {"brainstorm", "requirements", "plan", "testcases"} and (dep.get("missingInputs") or dep.get("missingOutputs")):
            return "fail"
        return "fail" if relevant_issues else "ready"

    def phase_blockers(phase: str) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        dep = phase_dependency_status(task_dir, phase)
        for ref in dep.get("missingInputs", []):
            blockers.append({"type": "missing_input", "phase": phase, "ref": ref})
        for ref in dep.get("missingOutputs", []):
            blockers.append({"type": "missing_output", "phase": phase, "ref": ref})
        for issue in phase_issues:
            if issue.startswith(f"{phase}.json") or issue.startswith(f"{PHASE_FILES.get(phase, phase)}"):
                blockers.append({"type": "gate_issue", "phase": phase, "issue": issue})
        return blockers

    phases = {
        phase: {
            "id": phase,
            "cluster": phase_cluster(phase),
            "status": phase_status(phase),
            "guideRefs": phase_guide_refs(phase),
            "artifactRefs": phase_artifact_refs(phase),
            "inputRefs": required_phase_inputs(phase),
            "outputRefs": [PHASE_FILES[phase]],
            "dependencies": phase_dependency_status(task_dir, phase),
            "blockedBy": phase_blockers(phase),
            "schema": PHASE_SCHEMAS[phase],
            "checker": {
                "name": phase_checker_name(phase),
                "result": "fail" if phase_status(phase) == "fail" else "pass" if phase_status(phase) == "ready" else "pending",
                "issues": [issue for issue in phase_issues if issue.startswith(f"{phase}.json") or issue.startswith(f"{PHASE_FILES.get(phase, phase)}")],
                "warnings": [warning for warning in phase_warnings if warning.startswith(f"{phase}.json") or warning.startswith(f"{PHASE_FILES.get(phase, phase)}")],
            },
            "next": phase_next(phase),
            "gate": {
                "required": phase in {"brainstorm", "requirements", "plan", "testcases"},
                "result": "fail" if phase_status(phase) == "fail" else "pass" if phase_status(phase) == "ready" else "pending",
                "issues": [issue for issue in phase_issues if issue.startswith(f"{phase}.json") or issue.startswith(f"{PHASE_FILES.get(phase, phase)}")],
                "warnings": [warning for warning in phase_warnings if warning.startswith(f"{phase}.json") or warning.startswith(f"{PHASE_FILES.get(phase, phase)}")],
            },
        }
        for phase in phase_order
    }

    pending_user_action = None
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
    stale_resolved_ask = (
        state.get("nextAction") == "ask_user"
        and review.get("decision") == "auto_proceed"
        and review.get("needsUserInput") is False
    )
    if planner.get("needsUserInput") or review.get("decision") == "ask_user" or (state.get("nextAction") == "ask_user" and not stale_resolved_ask):
        pending_user_action = {
            "phase": "pre_implementation_review",
            "reason": review.get("reason") or state.get("summary") or "User decision required",
            "options": review.get("options") or [],
            "resumeAfter": "workflow-check",
        }

    completed_phases = [phase for phase in phase_order if phase_status(phase) == "ready"]

    phase_graph = {
        "start": phase_order[0],
        "final": "completion",
        "nodes": phase_order,
        "edges": [[phase, nxt] for phase in phase_order for nxt in phase_next(phase)],
    }

    first_not_ready = next((phase for phase in phase_order if phase_status(phase) != "ready"), None)
    expected_next = []
    if pending_user_action:
        expected_next.append({
            "phase": pending_user_action.get("phase") or "pre_implementation_review",
            "reason": "pending_user_action",
            "requiredInputs": required_phase_inputs(pending_user_action.get("phase") or "pre_implementation_review"),
        })
    elif first_not_ready:
        expected_next.append({
            "phase": first_not_ready,
            "reason": "resolve_blocker" if phase_status(first_not_ready) == "fail" else "next_unfinished_phase",
            "requiredInputs": required_phase_inputs(first_not_ready),
        })
    else:
        for nxt in phase_next(completed_phases[-1]) if completed_phases else [phase_order[0]]:
            expected_next.append({"phase": nxt, "reason": "phase_graph_next", "requiredInputs": required_phase_inputs(nxt)})

    target = {
        "finalPhase": "completion",
        "successCondition": "completion-check pass",
        "requiredResult": "pass",
    }

    return {
        "version": WORKFLOW_CONTRACT_VERSION,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "task": {
            "code": task_dir.name,
            "type": task_type,
            "userInputRef": ".user_input.txt",
        },
        "phaseGraph": phase_graph,
        "expectedNext": expected_next,
        "target": target,
        "phases": phases,
        "taskType": task_type,
        "runtimeProofRequired": runtime_proof_required,
        "runtimeProofRequiredRaw": runtime_required_raw,
        "verificationTarget": verification_target,
        "runtimeDowngradeApproval": decision_bundle.get("runtimeDowngradeApproval"),
        "runtimeDowngradeApprovalApproved": _approved_runtime_downgrade(decision_bundle),
        "requirements": requirements,
        "acceptanceCriteria": acceptance_criteria,
        "testcases": testcases,
    }


def write_workflow_contract(task_dir: Path, *, overwrite: bool = True) -> Path:
    path = workflow_contract_path(task_dir)
    if path.exists() and not overwrite:
        return path
    contract = build_workflow_contract(task_dir)
    path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n")
    try:
        update_runtime_state(task_dir, workflowContract=str(path), workflowContractVersion=f"v{WORKFLOW_CONTRACT_VERSION}")
    except Exception:
        # Contract writing should not fail the caller solely because state update
        # is unavailable in a synthetic fixture.
        pass
    return path


def load_workflow_contract(task_dir: Path) -> dict[str, Any] | None:
    path = workflow_contract_path(task_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def ensure_workflow_contract(task_dir: Path) -> dict[str, Any]:
    """Return workflow.json, materializing it when absent.

    `workflow-check` is allowed to refresh deterministic gate metadata. The
    contract is still derived from reviewable Phase 2 artifacts; if those change,
    rerunning workflow-check regenerates the machine projection.
    """
    write_workflow_contract(task_dir, overwrite=True)
    data = load_workflow_contract(task_dir)
    return data or build_workflow_contract(task_dir)


def validate_workflow_contract(task_dir: Path, contract: dict[str, Any] | None = None) -> tuple[list[str], list[str]]:
    """Validate workflow.json against TestCases and runtime policy."""
    issues: list[str] = []
    warnings: list[str] = []
    contract = contract or load_workflow_contract(task_dir)
    if not contract:
        issues.append("workflow.json missing or unreadable; run workflow-check to materialize the executable TestCases contract")
        return issues, warnings
    if contract.get("version") != WORKFLOW_CONTRACT_VERSION:
        issues.append(f"workflow.json version unsupported: {contract.get('version')}")

    registry_issues, registry_warnings = validate_phase_registry()
    issues.extend(registry_issues)
    warnings.extend(registry_warnings)
    phase_issues, phase_warnings = validate_phase_contracts(task_dir, ["brainstorm", "requirements", "plan", "testcases", "pre_implementation_review"])
    issues.extend(phase_issues)
    warnings.extend(phase_warnings)
    phases = contract.get("phases") if isinstance(contract.get("phases"), dict) else {}
    phase_graph = contract.get("phaseGraph") if isinstance(contract.get("phaseGraph"), dict) else {}
    graph_nodes = phase_graph.get("nodes") if isinstance(phase_graph.get("nodes"), list) else []
    graph_node_set = {str(node) for node in graph_nodes}
    if not graph_nodes:
        issues.append("workflow.json missing phaseGraph.nodes")
    if phase_graph.get("final") not in PHASE_REGISTRY:
        issues.append(f"workflow.json phaseGraph.final unknown: {phase_graph.get('final')}")
    target = contract.get("target") if isinstance(contract.get("target"), dict) else {}
    if target.get("finalPhase") not in PHASE_REGISTRY:
        issues.append(f"workflow.json target.finalPhase unknown: {target.get('finalPhase')}")
    for item in contract.get("expectedNext") if isinstance(contract.get("expectedNext"), list) else []:
        if isinstance(item, dict) and item.get("phase") not in PHASE_REGISTRY:
            issues.append(f"workflow.json expectedNext references unknown phase: {item.get('phase')}")
    for phase in ["brainstorm", "requirements", "plan", "testcases", "pre_implementation_review"]:
        if phase not in phases:
            issues.append(f"workflow.json missing phase node: {phase}")
        else:
            node = phases.get(phase) if isinstance(phases.get(phase), dict) else {}
            for key in ["inputRefs", "outputRefs", "schema", "gate", "guideRefs", "artifactRefs", "checker", "next"]:
                if key not in node:
                    issues.append(f"workflow.json phase {phase} missing {key}")
            for nxt in node.get("next") if isinstance(node.get("next"), list) else []:
                if str(nxt) not in graph_node_set:
                    issues.append(f"workflow.json phase {phase} next references phase outside graph: {nxt}")

    declared = extract_declared_testcases(task_dir)
    declared_by_id = {str(tc.get("id")): tc for tc in declared}
    contract_cases = contract.get("testcases") if isinstance(contract.get("testcases"), list) else []
    contract_by_id = {str(tc.get("id")): tc for tc in contract_cases if isinstance(tc, dict) and tc.get("id")}

    missing = sorted(set(declared_by_id) - set(contract_by_id))
    extra = sorted(set(contract_by_id) - set(declared_by_id))
    if missing:
        issues.append("workflow.json missing TestCases.md cases: " + ", ".join(missing))
    if extra:
        warnings.append("workflow.json has cases not currently declared in TestCases.md: " + ", ".join(extra))

    for tc_id, declared_tc in declared_by_id.items():
        contract_tc = contract_by_id.get(tc_id)
        if not contract_tc:
            continue
        if bool(contract_tc.get("required")) != bool(declared_tc.get("required")):
            issues.append(f"workflow.json required flag drift for {tc_id}: contract={contract_tc.get('required')} TestCases.md={declared_tc.get('required')}")
        declared_level = _normalize_runtime_level(declared_tc.get("runtimeLevel", ""))
        if str(contract_tc.get("runtimeLevel") or "") != declared_level:
            issues.append(f"workflow.json runtimeLevel drift for {tc_id}: contract={contract_tc.get('runtimeLevel')} TestCases.md={declared_level}")

    runtime_required = bool(contract.get("runtimeProofRequired"))
    approved_downgrade = bool(contract.get("runtimeDowngradeApprovalApproved") or contract.get("runtimeDowngradeApprovalSigned"))
    verification_target = str(contract.get("verificationTarget") or "").strip().lower()
    runtime_cases = [tc for tc in contract_cases if isinstance(tc, dict) and str(tc.get("runtimeLevel") or "") in RUNTIME_LEVELS]
    required_runtime_cases = [tc for tc in runtime_cases if tc.get("required")]

    if runtime_required:
        if verification_target not in RUNTIME_CAPABLE_TARGETS and not approved_downgrade:
            issues.append(
                "workflow.json runtimeProofRequired=true but verificationTarget is not runtime-capable "
                f"({verification_target or 'unset'}) and runtimeDowngradeApproval is unapproved"
            )
        if not runtime_cases and not approved_downgrade:
            issues.append(
                "workflow.json runtimeProofRequired=true but no runtime/device testcase exists; "
                "add a runtime/device TC or record approved runtimeDowngradeApproval"
            )
        elif runtime_cases and not required_runtime_cases and not approved_downgrade:
            issues.append(
                "workflow.json runtimeProofRequired=true but all runtime/device testcases are optional; "
                "required runtime proof cannot be silently demoted without approved runtimeDowngradeApproval"
            )
        for tc in runtime_cases:
            policy = tc.get("skipPolicy") if isinstance(tc.get("skipPolicy"), dict) else {}
            if not policy.get("requiresUserApproval") and not approved_downgrade:
                issues.append(f"workflow.json runtime testcase {tc.get('id')} skipPolicy must require user approval under runtimeProofRequired=true")

    return issues, warnings
