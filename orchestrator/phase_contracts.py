"""Phase JSON sidecar contracts for CodeAutonomy.

Markdown remains the human-facing authoring surface.  These sidecars are the
minimum machine-readable inputs/outputs that let workflow.json orchestrate the
long-running loop deterministically.
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
    extract_markdown_section,
    extract_plan_checklist_rows,
    read_requirements_contract_text,
)
from orchestrator.state import read_runtime_state, update_runtime_state

PHASE_CONTRACT_VERSION = 1
from orchestrator.phase_registry import (
    PHASE_REGISTRY,
    PRE_IMPLEMENTATION_DECISIONS,
)

PHASES = list(PHASE_REGISTRY)
PHASE_FILES = {phase: meta["json"] for phase, meta in PHASE_REGISTRY.items()}
PHASE_SCHEMAS = {phase: meta["schema"] for phase, meta in PHASE_REGISTRY.items()}
PHASE_GUIDES = {phase: meta["phaseGuide"] for phase, meta in PHASE_REGISTRY.items()}
PHASE_MACRO_GUIDES = {phase: meta["macroGuide"] for phase, meta in PHASE_REGISTRY.items()}
PHASE_CHECKERS = {phase: meta["checker"] for phase, meta in PHASE_REGISTRY.items()}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def phase_meta(phase: str) -> dict[str, Any]:
    if phase not in PHASE_REGISTRY:
        raise ValueError(f"unknown phase: {phase}")
    return PHASE_REGISTRY[phase]


def phase_guide_refs(phase: str) -> dict[str, str]:
    meta = phase_meta(phase)
    return {
        "workflow": "docs/workflow.md",
        "macro": str(meta["macroGuide"]),
        "phase": str(meta["phaseGuide"]),
    }


def phase_artifact_refs(phase: str) -> dict[str, str]:
    meta = phase_meta(phase)
    return {
        "markdown": str(meta["markdown"]),
        "json": str(meta["json"]),
        "schema": str(meta["schema"]),
    }


def phase_next(phase: str) -> list[str]:
    return list(phase_meta(phase).get("next") or [])


def phase_checker_name(phase: str) -> str:
    return str(phase_meta(phase).get("checker") or f"{phase}_contract")


def phase_cluster(phase: str) -> str:
    return str(phase_meta(phase).get("cluster") or "")

def validate_phase_registry() -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    root = project_root()
    for phase, meta in PHASE_REGISTRY.items():
        for key in ["cluster", "macroGuide", "phaseGuide", "markdown", "json", "schema", "checker", "next"]:
            if key not in meta:
                issues.append(f"phase registry {phase} missing {key}")
        for ref_key in ["macroGuide", "phaseGuide", "schema"]:
            ref = meta.get(ref_key)
            if ref and not (root / str(ref)).exists():
                issues.append(f"phase registry {phase} {ref_key} missing: {ref}")
        for nxt in meta.get("next") or []:
            if nxt not in PHASE_REGISTRY:
                issues.append(f"phase registry {phase} next references unknown phase: {nxt}")
        checker = str(meta.get("checker") or "")
        if checker not in CHECKER_REGISTRY:
            issues.append(f"phase registry {phase} checker not registered: {checker}")
    return issues, warnings



def phase_contract_path(task_dir: Path, phase: str) -> Path:
    if phase not in PHASE_FILES:
        raise ValueError(f"unknown phase: {phase}")
    return task_dir / PHASE_FILES[phase]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read(task_dir: Path, name: str) -> str:
    path = task_dir / name
    return path.read_text(errors="ignore") if path.exists() else ""



def _clip_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"... [truncated {len(text) - limit} chars; see source markdown]"


def _source_ref(path: str, artifact_id: str | None = None) -> dict[str, Any]:
    ref: dict[str, Any] = {"path": path}
    if artifact_id:
        ref["id"] = artifact_id
    return ref

def _extract_first_heading_summary(text: str, fallback: str = "") -> str:
    for line in (text or "").splitlines():
        stripped = line.strip(" #\t")
        if stripped and not stripped.startswith("<!--"):
            return stripped[:300]
    return fallback


def _line_items(section: str) -> list[str]:
    items: list[str] = []
    for line in (section or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(('-', '*')):
            item = stripped.lstrip('-*').strip()
            if item:
                items.append(item)
    return items


def _demand_analysis_from_brainstorm(text: str) -> dict[str, Any]:
    intent = extract_markdown_section(text, "User intent digest")
    if not intent:
        intent = extract_markdown_section(text, "Demand digestion")
    business = extract_markdown_section(text, "Business/product suggestions")
    if not business:
        business = extract_markdown_section(text, "Business suggestions")
    risks = extract_markdown_section(text, "Risk and opportunity register")
    if not risks:
        risks = extract_markdown_section(text, "Risks")
    approaches = extract_markdown_section(text, "Approach options")
    if not approaches:
        approaches = extract_markdown_section(text, "Proactive design expansion")
    recommendation = extract_markdown_section(text, "Recommendation")
    return {
        "intentDigest": _line_items(intent),
        "businessSuggestions": _line_items(business),
        "riskAndOpportunityRegister": _line_items(risks),
        "approachOptions": _line_items(approaches),
        "recommendation": _extract_first_heading_summary(recommendation, ""),
        "hasIntentDigest": bool(intent.strip()),
        "hasBusinessSuggestions": bool(business.strip()),
        "hasRiskRegister": bool(risks.strip()),
        "hasApproachOptions": bool(approaches.strip()),
    }


def _repository_context_from_brainstorm(text: str) -> dict[str, Any]:
    section = extract_markdown_section(text, "Project/context observations")
    if not section:
        section = extract_markdown_section(text, "Repository context")
    items = _line_items(section)
    lower = section.lower()
    return {
        "observations": items,
        "agentsInstructions": [item for item in items if "agents.md" in item.lower() or "agent" in item.lower()],
        "docsRead": [item for item in items if "readme" in item.lower() or "doc" in item.lower() or "runbook" in item.lower()],
        "scriptsAndTools": [item for item in items if any(token in item.lower() for token in ["script", "tool", "makefile", "gradle", "package", "fastlane", "ci", "workflow", "bin/"])],
        "constraints": [item for item in items if any(token in item.lower() for token in ["constraint", "must", "should", "avoid", "risk", "permission", "signing", "device"])],
        "sectionPresent": bool(section.strip()),
        "mentionsAgents": "agents.md" in lower,
        "mentionsScriptsOrDocs": any(token in lower for token in ["readme", "docs/", "script", "tools/", "makefile", "gradle", "package", "fastlane", "ci"]),
    }


def _extract_requirement_blocks(text: str) -> list[dict[str, Any]]:
    """Extract compact requirement metadata without duplicating Requirements.md."""
    ids = list(extract_artifact_ids(text, "R").values())
    requirements: list[dict[str, Any]] = []
    if not ids:
        return requirements
    lines = (text or "").splitlines()
    for rid in ids:
        line_index = next((idx for idx, line in enumerate(lines) if re.search(rf"\b{re.escape(rid)}\b", line, re.IGNORECASE)), -1)
        title = rid
        block = ""
        if line_index >= 0:
            title = lines[line_index].strip(" #|	") or rid
            following: list[str] = []
            for line in lines[line_index : min(len(lines), line_index + 40)]:
                if following and re.search(r"\bR[-_]?\d{2,3}\b", line, re.IGNORECASE):
                    break
                following.append(line)
            block = "\n".join(following)
        local_ac = list(extract_artifact_ids(block, "AC").values())
        requirements.append({
            "id": rid,
            "title": _clip_text(title, 240),
            "acceptanceCriteria": [{"id": ac, "sourceRef": _source_ref("Requirements.md", ac)} for ac in local_ac],
            "acceptanceCriteriaRefs": local_ac,
            "sourceRef": _source_ref("Requirements.md", rid),
        })
    return requirements


def _runtime_policy_from_state(task_dir: Path) -> dict[str, Any]:
    state = read_runtime_state(task_dir) or {}
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
    bundle = review.get("decisionBundle") if isinstance(review.get("decisionBundle"), dict) else {}
    approval = bundle.get("runtimeDowngradeApproval") if isinstance(bundle.get("runtimeDowngradeApproval"), dict) else None
    return {
        "taskType": str(bundle.get("taskType") or state.get("taskType") or "unknown"),
        "runtimeProofRequired": str(bundle.get("runtimeProofRequired") or "auto"),
        "verificationTarget": str(bundle.get("verificationTarget") or "unknown"),
        "runtimeDowngradeApproval": approval,
        "runtimeDowngradeApprovalApproved": bool(
            approval and (approval.get("approvedBy") or approval.get("signedBy")) and (approval.get("approvedAt") or approval.get("signedAt"))
        ),
    }


def build_brainstorm_contract(task_dir: Path) -> dict[str, Any]:
    text = _read(task_dir, "Brainstorm.md")
    state = read_runtime_state(task_dir) or {}
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
    return {
        "version": PHASE_CONTRACT_VERSION,
        "phase": "brainstorm",
        "generatedAt": _now(),
        "sourceRefs": [".user_input.txt", "Brainstorm.md", "runtime-state.json"],
        "dependencies": phase_dependency_status(task_dir, "brainstorm"),
        "summary": _extract_first_heading_summary(extract_markdown_section(text, "Proactive design expansion"), _extract_first_heading_summary(text, "Brainstorm")),
        "demandAnalysis": _demand_analysis_from_brainstorm(text),
        "assumptions": _line_items(extract_markdown_section(text, "Assumptions")),
        "questions": _line_items(extract_markdown_section(text, "Clarification questions / decisions")),
        "decisions": _line_items(extract_markdown_section(text, "Pre-implementation user review")),
        "needsUserInput": bool(planner.get("needsUserInput") or review.get("needsUserInput")),
        "preImplementationReview": review or {},
        "repositoryContext": _repository_context_from_brainstorm(text),
    }


def build_requirements_contract(task_dir: Path) -> dict[str, Any]:
    text = read_requirements_contract_text(task_dir)
    reqs = _extract_requirement_blocks(text)
    ac_ids = list(extract_artifact_ids(text, "AC").values())
    return {
        "version": PHASE_CONTRACT_VERSION,
        "phase": "requirements",
        "generatedAt": _now(),
        "sourceRefs": ["Requirements.md"],
        "dependencies": phase_dependency_status(task_dir, "requirements"),
        "goal": _extract_first_heading_summary(text, ""),
        "scope": _line_items(extract_markdown_section(text, "Scope")),
        "nonGoals": _line_items(extract_markdown_section(text, "Non-goals")),
        "requirements": reqs,
        "acceptanceCriteria": [{"id": ac, "sourceRef": _source_ref("Requirements.md", ac)} for ac in ac_ids],
    }


def build_plan_contract(task_dir: Path) -> dict[str, Any]:
    text = _read(task_dir, "Plan.md")
    impl = extract_plan_checklist_rows(text, "Implementation Checklist")
    ver = extract_plan_checklist_rows(text, "Verification Checklist")
    lower = text.lower()
    script_discovery_checked = any(token in lower for token in ["script", "runbook", "readme", "makefile", "gradle", "package", "fastlane", "ci"])
    return {
        "version": PHASE_CONTRACT_VERSION,
        "phase": "plan",
        "generatedAt": _now(),
        "sourceRefs": ["Plan.md", "Reuse.md"],
        "dependencies": phase_dependency_status(task_dir, "plan"),
        "firstFunctionalBatch": extract_markdown_section(text, "First functional batch") or extract_markdown_section(text, "Functional batch"),
        "implementationChecklist": impl,
        "verificationChecklist": ver,
        "verificationStrategy": {
            "scriptDiscovery": {
                "checked": script_discovery_checked,
                "evidence": "Plan.md mentions script/runbook/docs/CI/build tool discovery" if script_discovery_checked else "",
                "selected": [],
                "ignored": [],
            },
            "commands": _line_items(extract_markdown_section(text, "Verification command")) or _line_items(extract_markdown_section(text, "Verification")),
        },
        "risks": _line_items(extract_markdown_section(text, "Risks")),
        "blockedRoutes": _line_items(extract_markdown_section(text, "Blocked")),
    }


def build_testcases_contract(task_dir: Path) -> dict[str, Any]:
    cases = extract_declared_testcases(task_dir)
    return {
        "version": PHASE_CONTRACT_VERSION,
        "phase": "testcases",
        "generatedAt": _now(),
        "sourceRefs": ["TestCases.md", "Requirements.md", "Plan.md"],
        "dependencies": phase_dependency_status(task_dir, "testcases"),
        "runtimePolicy": _runtime_policy_from_state(task_dir),
        "testcases": [
            {
                "id": tc.get("id"),
                "requirementRefs": tc.get("requirements") or [],
                "acceptanceCriteriaRefs": tc.get("acceptanceCriteria") or [],
                "type": tc.get("type") or "",
                "required": bool(tc.get("required")),
                "quality": bool(tc.get("quality")),
                "runtimeLevel": tc.get("runtimeLevel") or "unknown",
                "executor": _clip_text(tc.get("command") or "", 500),
                "runbook": {
                    "preconditions": [_clip_text(tc.get("preconditions"), 500)] if tc.get("preconditions") else [],
                    "command": _clip_text(tc.get("command") or "", 500),
                    "steps": [_clip_text(tc.get("steps"), 800)] if tc.get("steps") else [],
                    "assertions": [_clip_text(tc.get("expectedEvidence"), 800)] if tc.get("expectedEvidence") else [],
                    "expectedEvidence": [_clip_text(tc.get("expectedEvidence"), 800)] if tc.get("expectedEvidence") else [],
                },
                "dependency": tc.get("dependency") or "",
                "skipPolicy": {"requiresUserApproval": False},
            }
            for tc in cases
        ],
    }


def build_pre_implementation_review_contract(task_dir: Path) -> dict[str, Any]:
    state = read_runtime_state(task_dir) or {}
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
    decision = str(review.get("decision") or ("ask_user" if planner.get("needsUserInput") else "auto_proceed")).strip() or "auto_proceed"
    bundle = review.get("decisionBundle") if isinstance(review.get("decisionBundle"), dict) else {}
    question = review.get("question") or state.get("askUserQuestion") or None
    options = review.get("options") or []
    return {
        "version": PHASE_CONTRACT_VERSION,
        "phase": "pre_implementation_review",
        "generatedAt": _now(),
        "sourceRefs": [
            "Brainstorm.md",
            "brainstorm.json",
            "Requirements.md",
            "requirements.json",
            "Plan.md",
            "plan.json",
            "TestCases.md",
            "testcases.json",
            "workflow.json",
            "runtime-state.json",
        ],
        "dependencies": phase_dependency_status(task_dir, "pre_implementation_review"),
        "decision": decision,
        "needsUserInput": bool(planner.get("needsUserInput") or decision == "ask_user"),
        "reviewedRefs": ["Requirements.md", "requirements.json", "TestCases.md", "testcases.json", "Plan.md", "plan.json", "workflow.json"],
        "question": question,
        "options": options if isinstance(options, list) else [],
        "approval": {
            "confirmedAt": bundle.get("confirmedAt"),
            "confirmedBy": bundle.get("confirmedBy"),
        },
        "decisionBundle": bundle,
        "issues": review.get("issues") if isinstance(review.get("issues"), list) else [],
        "nextAction": "ask_user" if decision == "ask_user" else "replan" if decision == "replan" else "delivery",
        "resumeAfter": "pre_implementation_review",
    }


def build_delivery_contract(task_dir: Path) -> dict[str, Any]:
    text = _read(task_dir, "Delivery.md")
    return {
        "version": PHASE_CONTRACT_VERSION,
        "phase": "delivery",
        "generatedAt": _now(),
        "sourceRefs": ["Delivery.md"],
        "dependencies": phase_dependency_status(task_dir, "delivery"),
        "exists": bool(text.strip()),
        "summary": _extract_first_heading_summary(text, ""),
        "changedFiles": _line_items(extract_markdown_section(text, "Changed files")),
        "implementedRequirements": list(extract_artifact_ids(text, "R").values()),
        "touchedTestcases": list(extract_artifact_ids(text, "TC").values()),
        "commandsRun": _line_items(extract_markdown_section(text, "Commands")),
        "knownRisks": _line_items(extract_markdown_section(text, "Risks")),
    }


def build_evaluation_contract(task_dir: Path) -> dict[str, Any]:
    path = task_dir / "evaluation.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                data.setdefault("version", PHASE_CONTRACT_VERSION)
                data.setdefault("phase", "evaluation")
                return data
        except Exception:
            pass
    return {
        "version": PHASE_CONTRACT_VERSION,
        "phase": "evaluation",
        "generatedAt": _now(),
        "dependencies": phase_dependency_status(task_dir, "evaluation"),
        "result": "not_run",
        "nextAction": "run_evaluator",
        "testResults": [],
        "evidence": [],
        "failedChecks": [],
    }


def build_completion_contract(task_dir: Path) -> dict[str, Any]:
    path = task_dir / "completion-report.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                data.setdefault("version", PHASE_CONTRACT_VERSION)
                data.setdefault("phase", "completion")
                return data
        except Exception:
            pass
    return {
        "version": PHASE_CONTRACT_VERSION,
        "phase": "completion",
        "generatedAt": _now(),
        "dependencies": phase_dependency_status(task_dir, "completion"),
        "result": "not_run",
        "issues": [],
        "requiredTestcases": [],
        "acceptanceCriteriaCoverage": [],
        "runtimeProof": {},
        "evidenceCheck": {},
    }


def required_phase_inputs(phase: str) -> list[str]:
    return {
        "brainstorm": ["Brainstorm.md"],
        "requirements": ["Brainstorm.md", "brainstorm.json", "Requirements.md"],
        "testcases": ["Requirements.md", "requirements.json", "TestCases.md"],
        "plan": ["Requirements.md", "requirements.json", "TestCases.md", "testcases.json", "Plan.md"],
        "pre_implementation_review": ["Brainstorm.md", "brainstorm.json", "Requirements.md", "requirements.json", "TestCases.md", "testcases.json", "Plan.md", "plan.json"],
        "delivery": ["workflow.json", "pre-implementation-review.json", "requirements.json", "plan.json", "testcases.json"],
        "evaluation": ["workflow.json", "delivery.json", "testcases.json"],
        "completion": ["workflow.json", "evaluation.json", "VerificationLedger.json", "delivery.json"],
    }.get(phase, [])


def phase_output_refs(phase: str) -> list[str]:
    return [PHASE_FILES[phase]] if phase in PHASE_FILES else []


def required_phase_outputs(phase: str) -> list[str]:
    # Pre-generator phases must have sidecars. Later phases are pending until
    # their owner runs, so their outputs are references rather than hard gates.
    return phase_output_refs(phase) if phase in {"brainstorm", "requirements", "plan", "testcases", "pre_implementation_review"} else []


def phase_file_exists(task_dir: Path, ref: str) -> bool:
    return (task_dir / ref).exists()


def phase_dependency_status(task_dir: Path, phase: str) -> dict[str, Any]:
    inputs = required_phase_inputs(phase)
    outputs = required_phase_outputs(phase)
    missing_inputs = [ref for ref in inputs if not phase_file_exists(task_dir, ref)]
    # Back-compat: synthetic/older tasks may keep the original request only in
    # runtime-state.json can carry the original request for synthetic/older tasks. Treat that as satisfying the brainstorm input contract.
    if phase == "brainstorm" and ".user_input.txt" in missing_inputs:
        state = read_runtime_state(task_dir) or {}
        if str(state.get("userInput") or "").strip():
            missing_inputs = [ref for ref in missing_inputs if ref != ".user_input.txt"]
    missing_outputs = [ref for ref in outputs if not phase_file_exists(task_dir, ref)]
    return {
        "inputRefs": inputs,
        "outputRefs": phase_output_refs(phase),
        "requiredOutputs": outputs,
        "missingInputs": missing_inputs,
        "missingOutputs": missing_outputs,
        "ready": not missing_inputs and not missing_outputs,
    }


def validate_phase_dependencies(task_dir: Path, phases: list[str] | None = None) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    for phase in phases or PHASES:
        dep = phase_dependency_status(task_dir, phase)
        for ref in dep["missingInputs"]:
            # Later phases may be pending before Generator/Evaluator runs.
            if phase in {"delivery", "evaluation", "completion"}:
                warnings.append(f"{phase}.json pending input missing: {ref}")
            else:
                issues.append(f"{phase}.json missing required input: {ref}")
        for ref in dep["missingOutputs"]:
            issues.append(f"{phase}.json missing required output: {ref}")
    return issues, warnings


def build_phase_contract(task_dir: Path, phase: str) -> dict[str, Any]:
    builders = {
        "brainstorm": build_brainstorm_contract,
        "requirements": build_requirements_contract,
        "plan": build_plan_contract,
        "testcases": build_testcases_contract,
        "pre_implementation_review": build_pre_implementation_review_contract,
        "delivery": build_delivery_contract,
        "evaluation": build_evaluation_contract,
        "completion": build_completion_contract,
    }
    if phase not in builders:
        raise ValueError(f"unknown phase: {phase}")
    return builders[phase](task_dir)


def write_phase_contract(task_dir: Path, phase: str, *, overwrite: bool = True) -> Path:
    path = phase_contract_path(task_dir, phase)
    if path.exists() and not overwrite:
        return path
    # evaluation.json is the real Evaluator output consumed by existing gates.
    # Do not create a placeholder before the Evaluator runs; workflow.json can
    # still mark the phase pending by referencing the future output.
    if phase == "evaluation" and not path.exists():
        return path
    data = build_phase_contract(task_dir, phase)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return path


def ensure_phase_contracts(task_dir: Path, phases: list[str] | None = None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for phase in phases or PHASES:
        path = write_phase_contract(task_dir, phase, overwrite=True)
        if phase == "evaluation" and not path.exists():
            result[phase] = build_evaluation_contract(task_dir)
            continue
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
        result[phase] = data if isinstance(data, dict) else {}
    try:
        update_runtime_state(task_dir, phaseContracts={phase: str(phase_contract_path(task_dir, phase)) for phase in (phases or PHASES)})
    except Exception:
        pass
    return result


def _base_contract_issues(phase: str, data: dict[str, Any]) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return [f"{phase}.json is not an object"], warnings
    if data.get("version") != PHASE_CONTRACT_VERSION:
        issues.append(f"{PHASE_FILES.get(phase, phase + '.json')} version unsupported: {data.get('version')}")
    if data.get("phase") != phase:
        issues.append(f"{PHASE_FILES.get(phase, phase + '.json')} phase mismatch: {data.get('phase')}")
    return issues, warnings


def check_brainstorm_contract(data: dict[str, Any], task_dir: Path | None = None) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    review = data.get("preImplementationReview")
    if not isinstance(review, dict) or not review.get("decision"):
        issues.append("brainstorm.json missing preImplementationReview.decision")
    if not str(data.get("summary") or "").strip():
        warnings.append("brainstorm.json summary is empty")
    demand = data.get("demandAnalysis") if isinstance(data.get("demandAnalysis"), dict) else {}
    if not demand.get("hasIntentDigest"):
        warnings.append("brainstorm.json demandAnalysis missing User intent digest; planner should translate the request into goal/scope/success/user impact before requirements")
    if not demand.get("hasApproachOptions"):
        warnings.append("brainstorm.json demandAnalysis missing approach options/trade-offs; planner should propose alternatives before requirements")
    if not demand.get("hasRiskRegister"):
        warnings.append("brainstorm.json demandAnalysis missing risk/opportunity register; planner should surface project/business/verification risks")
    if not demand.get("hasBusinessSuggestions"):
        warnings.append("brainstorm.json demandAnalysis missing business/product suggestions; planner should note non-obvious product improvements or explicitly say none apply")
    repo = data.get("repositoryContext") if isinstance(data.get("repositoryContext"), dict) else {}
    if not repo.get("sectionPresent"):
        warnings.append("brainstorm.json repositoryContext missing Project/context observations; Brainstorm.md should record repo analysis")
    else:
        if not repo.get("mentionsAgents"):
            warnings.append("brainstorm.json repositoryContext should mention AGENTS.md or agent instructions when present/checked")
        if not repo.get("mentionsScriptsOrDocs"):
            warnings.append("brainstorm.json repositoryContext should mention README/docs/scripts/tools/CI discovery")
    return issues, warnings


def check_requirements_contract(data: dict[str, Any], task_dir: Path | None = None) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    reqs = data.get("requirements") if isinstance(data.get("requirements"), list) else []
    if not reqs:
        issues.append("requirements.json missing requirements[]")
    for req in reqs:
        if not isinstance(req, dict) or not req.get("id"):
            issues.append("requirements.json has requirement without id")
        if isinstance(req, dict) and not req.get("acceptanceCriteria"):
            issues.append(f"requirements.json requirement {req.get('id')} missing acceptanceCriteria")
    return issues, warnings


def check_plan_contract(data: dict[str, Any], task_dir: Path | None = None) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    strategy = data.get("verificationStrategy") if isinstance(data.get("verificationStrategy"), dict) else {}
    discovery = strategy.get("scriptDiscovery") if isinstance(strategy.get("scriptDiscovery"), dict) else {}
    if discovery.get("checked") is not True:
        warnings.append("plan.json scriptDiscovery.checked is not true; planner should inspect project scripts/runbooks before command choice")
    if not data.get("implementationChecklist"):
        issues.append("plan.json missing implementationChecklist")
    if not data.get("verificationChecklist"):
        issues.append("plan.json missing verificationChecklist")
    return issues, warnings


def check_testcases_contract(data: dict[str, Any], task_dir: Path | None = None) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    cases = data.get("testcases") if isinstance(data.get("testcases"), list) else []
    if not cases:
        issues.append("testcases.json missing testcases[]")
    for tc in cases:
        if not isinstance(tc, dict):
            issues.append("testcases.json has non-object testcase")
            continue
        tc_id = tc.get("id") or "<missing>"
        if not tc.get("id"):
            issues.append("testcases.json testcase missing id")
        if tc.get("required") and not tc.get("acceptanceCriteriaRefs"):
            issues.append(f"testcases.json required testcase {tc_id} missing acceptanceCriteriaRefs")
        runbook = tc.get("runbook") if isinstance(tc.get("runbook"), dict) else {}
        if tc.get("required") and not (runbook.get("command") or runbook.get("steps")):
            issues.append(f"testcases.json required testcase {tc_id} missing runbook command/steps")
        if tc.get("required") and not runbook.get("expectedEvidence"):
            issues.append(f"testcases.json required testcase {tc_id} missing expectedEvidence")
    return issues, warnings


def check_pre_implementation_review_contract(data: dict[str, Any], task_dir: Path | None = None) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    decision = str(data.get("decision") or "")
    if decision not in PRE_IMPLEMENTATION_DECISIONS:
        issues.append(f"pre-implementation-review.json invalid decision: {decision}")
    reviewed = set(data.get("reviewedRefs") if isinstance(data.get("reviewedRefs"), list) else [])
    for ref in ["Requirements.md", "TestCases.md", "Plan.md"]:
        if ref not in reviewed:
            issues.append(f"pre-implementation-review.json missing reviewedRefs entry: {ref}")
    if decision == "ask_user":
        if not (str(data.get("question") or "").strip() or data.get("options")):
            issues.append("pre-implementation-review.json ask_user decision missing question/options")
        if data.get("nextAction") != "ask_user":
            issues.append("pre-implementation-review.json ask_user decision requires nextAction=ask_user")
    elif decision == "replan":
        if data.get("nextAction") != "replan":
            issues.append("pre-implementation-review.json replan decision requires nextAction=replan")
        if not data.get("issues"):
            warnings.append("pre-implementation-review.json replan decision should explain issues[]")
    elif decision == "auto_proceed":
        if data.get("nextAction") != "delivery":
            issues.append("pre-implementation-review.json auto_proceed decision requires nextAction=delivery")
        approval = data.get("approval") if isinstance(data.get("approval"), dict) else {}
        if not (approval.get("confirmedAt") and approval.get("confirmedBy")):
            warnings.append("pre-implementation-review.json auto_proceed lacks confirmedAt/confirmedBy; allowed for legacy auto policy but should be explicit")
    return issues, warnings


def check_delivery_contract(data: dict[str, Any], task_dir: Path | None = None) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    if data.get("exists") and not str(data.get("summary") or "").strip():
        warnings.append("delivery.json exists but summary is empty")
    return issues, warnings


def check_evaluation_contract(data: dict[str, Any], task_dir: Path | None = None) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    if "nextAction" not in data:
        issues.append("evaluation.json missing nextAction")
    if "testResults" not in data:
        issues.append("evaluation.json missing testResults")
    return issues, warnings


def check_completion_contract(data: dict[str, Any], task_dir: Path | None = None) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    if "result" not in data:
        issues.append("completion result missing from summary-stage-state completion or completion-report.json")
    return issues, warnings


CHECKER_REGISTRY = {
    "brainstorm_contract": check_brainstorm_contract,
    "requirements_contract": check_requirements_contract,
    "plan_contract": check_plan_contract,
    "testcases_contract": check_testcases_contract,
    "pre_implementation_review_contract": check_pre_implementation_review_contract,
    "delivery_contract": check_delivery_contract,
    "evaluation_contract": check_evaluation_contract,
    "completion_contract": check_completion_contract,
}


def validate_phase_contract(phase: str, data: dict[str, Any], task_dir: Path | None = None) -> tuple[list[str], list[str]]:
    issues, warnings = _base_contract_issues(phase, data)
    if issues and not isinstance(data, dict):
        return issues, warnings
    checker_name = phase_checker_name(phase)
    checker = CHECKER_REGISTRY.get(checker_name)
    if not checker:
        issues.append(f"{PHASE_FILES.get(phase, phase + '.json')} checker not registered: {checker_name}")
        return issues, warnings
    phase_issues, phase_warnings = checker(data, task_dir)
    issues.extend(phase_issues)
    warnings.extend(phase_warnings)
    return issues, warnings


def validate_phase_contracts(task_dir: Path, phases: list[str] | None = None) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    dep_issues, dep_warnings = validate_phase_dependencies(task_dir, phases)
    issues.extend(dep_issues)
    warnings.extend(dep_warnings)
    for phase, data in ensure_phase_contracts(task_dir, phases).items():
        phase_issues, phase_warnings = validate_phase_contract(phase, data, task_dir)
        issues.extend(phase_issues)
        warnings.extend(phase_warnings)
    return issues, warnings
