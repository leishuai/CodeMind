"""Lightweight iteration purpose and exploration-convergence context."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.state import ensure_dir, read_evaluation_json, read_runtime_state, rel_to_root, update_runtime_state
from orchestrator.tc_attempts import read_tc_attempts


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _target_testcases_from_evaluation(evaluation: dict[str, Any], limit: int = 6) -> list[str]:
    targets: list[str] = []
    for row in _safe_list(evaluation.get("testResults")):
        if not isinstance(row, dict):
            continue
        tc_id = str(row.get("testCaseId") or row.get("id") or row.get("name") or "").strip()
        if not tc_id:
            continue
        result = str(row.get("result") or "").strip().lower()
        candidate = str(row.get("candidateResult") or "").strip().lower()
        if result not in {"pass"} or candidate in {"pass_candidate", "missing_evidence"}:
            if tc_id not in targets:
                targets.append(tc_id)
    return targets[:limit]


def build_exploration_context(task_dir: Path, limit: int = 8) -> dict[str, Any]:
    """Summarize TC attempt convergence without enforcing a guard."""
    ledger = read_tc_attempts(task_dir)
    progress = ledger.get("progressByTc") if isinstance(ledger.get("progressByTc"), dict) else {}
    latest = ledger.get("latest") if isinstance(ledger.get("latest"), dict) else {}
    rows: list[dict[str, Any]] = []
    for tc_id, item in progress.items():
        if not isinstance(item, dict):
            continue
        latest_attempt = latest.get(tc_id) if isinstance(latest.get(tc_id), dict) else {}
        rows.append({
            "testCaseId": tc_id,
            "attemptCount": int(item.get("attemptCount") or 0),
            "progressKinds": _safe_list(item.get("progressKinds")),
            "ruledOut": _safe_list(item.get("ruledOut")),
            "remainingHypotheses": _safe_list(item.get("remainingHypotheses")),
            "nextSelectorCandidates": _safe_list(item.get("nextSelectorCandidates")),
            "narrowingRounds": int(item.get("narrowingRounds") or 0),
            "latestOutcome": str(item.get("latestOutcome") or ""),
            "latestHypothesis": str(latest_attempt.get("hypothesis") or ""),
            "latestExpectedSignal": str(latest_attempt.get("expectedSignal") or ""),
        })
    rows.sort(key=lambda row: (-int(row.get("attemptCount") or 0), str(row.get("testCaseId") or "")))
    return {
        "schema": "automind.exploration_context.v1",
        "source": rel_to_root(task_dir / "tc-attempts.json") if (task_dir / "tc-attempts.json").exists() else "",
        "currentTc": ledger.get("currentTc"),
        "nextTc": ledger.get("nextTc"),
        "items": rows[:limit],
        "rule": "Use ruledOut/remainingHypotheses to narrow exploration. This is advisory context, not a hard guard.",
    }


def build_iteration_purpose(task_dir: Path, iteration: int, phase: str) -> dict[str, Any]:
    state = read_runtime_state(task_dir) or {}
    evaluation = read_evaluation_json(task_dir) or {}
    exploration = build_exploration_context(task_dir)
    targets = _target_testcases_from_evaluation(evaluation)
    if not targets:
        for key in ["nextTc", "currentTc"]:
            value = exploration.get(key) or state.get(key)
            if value and value not in targets:
                targets.append(str(value))

    has_synthesis = isinstance(evaluation.get("evidenceSynthesis"), dict)
    has_attempts = bool(exploration.get("items"))
    if has_synthesis and phase == "generator":
        mode = "focused_gap_retry"
        purpose = "Use synthesized TC evidence to repair only the remaining proof gaps; avoid broad re-exploration."
        expected = "Delivery.md records a focused change or verifier action for missing/partial TC rows."
    elif phase == "evaluator":
        mode = "proof_or_convergence_update"
        purpose = "Verify the current target TC evidence and record ruled-out/remaining hypotheses when a path fails."
        expected = "evaluation.json.testResults and tc-attempts.json show proof, missing signals, or narrowed hypotheses."
    elif has_attempts:
        mode = "exploration_convergence"
        purpose = "Continue from remaining hypotheses and avoid retrying paths already ruled out unless new evidence changes them."
        expected = "The next attempt either proves a TC signal or narrows remaining hypotheses."
    else:
        mode = "contract_driven_iteration"
        purpose = "Run the next concrete Generator/Evaluator step from Requirements, TestCases, Plan, and latest evaluation."
        expected = "This iteration produces Delivery/evaluation evidence tied to declared TestCases."

    return {
        "schema": "automind.iteration_purpose.v1",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "taskCode": task_dir.name,
        "iteration": int(iteration),
        "phase": phase,
        "mode": mode,
        "purpose": purpose,
        "targetTestCases": targets[:6],
        "expectedSignal": expected,
        "stopCondition": "Stop this phase when the expected signal is produced, a blocker is classified, or the remaining hypotheses are narrowed.",
        "explorationContext": exploration,
        "doesNotEnableRepeatGuard": True,
    }


def render_iteration_purpose_md(purpose: dict[str, Any]) -> str:
    lines = [
        f"# Iteration Purpose - iter-{purpose.get('iteration')} {purpose.get('phase')}",
        "",
        f"- Mode: `{purpose.get('mode')}`",
        f"- Purpose: {purpose.get('purpose')}",
        f"- Expected signal: {purpose.get('expectedSignal')}",
        f"- Stop condition: {purpose.get('stopCondition')}",
        f"- Target TestCases: {', '.join(purpose.get('targetTestCases') or []) or '-'}",
        "",
        "## Exploration convergence context",
        "- This context is advisory. Do not implement a hard repeat guard here.",
    ]
    exploration = purpose.get("explorationContext") if isinstance(purpose.get("explorationContext"), dict) else {}
    items = exploration.get("items") if isinstance(exploration.get("items"), list) else []
    if not items:
        lines.append("- No prior TC attempts recorded yet.")
    for item in items:
        if not isinstance(item, dict):
            continue
        lines.extend([
            f"- `{item.get('testCaseId')}` attempts={item.get('attemptCount', 0)} narrowingRounds={item.get('narrowingRounds', 0)} kinds={', '.join(item.get('progressKinds') or []) or '-'}",
            f"  - latest hypothesis: {item.get('latestHypothesis') or '-'}",
            f"  - latest expected signal: {item.get('latestExpectedSignal') or '-'}",
            f"  - latest outcome: {item.get('latestOutcome') or '-'}",
            f"  - ruled out: {', '.join(item.get('ruledOut') or []) or '-'}",
            f"  - remaining hypotheses: {', '.join(item.get('remainingHypotheses') or []) or '-'}",
            f"  - next selector candidates: {', '.join(item.get('nextSelectorCandidates') or []) or '-'}",
        ])
        if int(item.get("attemptCount") or 0) >= 2 and int(item.get("narrowingRounds") or 0) == 0:
            lines.append(
                "  - WARNING: repeated attempts but nothing ruled out and no new candidate proposed. "
                "This is an invalid retry pattern — narrow the search space (record ruledOut / "
                "remainingHypotheses / nextSelectorCandidates from observed evidence) or change the approach."
            )
    return "\n".join(lines).rstrip() + "\n"


def write_iteration_purpose(task_dir: Path, iteration: int, phase: str, iter_log_dir: Path) -> dict[str, Any]:
    ensure_dir(iter_log_dir)
    purpose = build_iteration_purpose(task_dir, iteration, phase)
    json_path = iter_log_dir / "iteration-purpose.json"
    md_path = iter_log_dir / "iteration-purpose.md"
    json_path.write_text(json.dumps(purpose, ensure_ascii=False, indent=2) + "\n")
    md_path.write_text(render_iteration_purpose_md(purpose))
    update_runtime_state(
        task_dir,
        latestIterationPurpose={
            "iteration": iteration,
            "phase": phase,
            "mode": purpose.get("mode"),
            "purpose": purpose.get("purpose"),
            "expectedSignal": purpose.get("expectedSignal"),
            "targetTestCases": purpose.get("targetTestCases"),
            "path": rel_to_root(md_path),
        },
        explorationContext={
            "source": purpose.get("explorationContext", {}).get("source"),
            "items": purpose.get("explorationContext", {}).get("items", []),
            "rule": purpose.get("explorationContext", {}).get("rule"),
        },
    )
    return purpose
