"""Shared structured evidence contract helpers.

Small, dependency-light helpers used by platform/script adapters to normalize
raw evidence artifacts into completion-check consumable TestCase rows.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.artifacts import extract_declared_testcases


def _unique_strings(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


def attach_required_test_results(
    task_dir: Path,
    evaluation: dict[str, Any],
    result: str,
    evidence_refs: list[str],
    reason: str,
    *,
    source: str,
    observed_signals: list[str] | None = None,
    metric_name: str = "adapter_result",
) -> dict[str, Any]:
    """Attach declared TestCase rows with precise evidence anchors.

    Required rows receive the adapter result plus an ``evidenceAssessment`` whose
    hard metric points to a concrete artifact. Optional rows remain ``not_run``.
    Existing explicit ``testResults`` are preserved; this helper is for adapters
    that only emitted top-level result/evidence.
    """
    if isinstance(evaluation.get("testResults"), list) and evaluation.get("testResults"):
        return evaluation

    declared = extract_declared_testcases(task_dir)
    if not declared:
        return evaluation

    normalized_result = "pass" if result == "pass" else "partial" if result == "partial" else "fail" if result == "fail" else "blocked"
    primary_evidence = evidence_refs[0] if evidence_refs else ""
    signals = _unique_strings(observed_signals or ([reason] if normalized_result == "pass" and reason else []))
    rows: list[dict[str, Any]] = []
    for tc in declared:
        if tc.get("required"):
            rows.append({
                "testCaseId": tc["id"],
                "result": normalized_result,
                "required": True,
                "quality": bool(tc.get("quality", False)),
                "acceptanceCriteria": tc.get("acceptanceCriteria", []),
                "evidence": evidence_refs,
                "observedSignals": signals if normalized_result == "pass" else [],
                "missingSignals": [] if normalized_result == "pass" else [reason],
                "reason": reason,
                "source": source,
                "evidenceAssessment": {
                    "verdict": "proved" if normalized_result == "pass" else "not_proved",
                    "assessor": source,
                    "reason": reason,
                    "machineAnchor": primary_evidence,
                    "hardMetrics": [{
                        "name": metric_name,
                        "value": normalized_result,
                        "expected": "pass",
                        "passed": normalized_result == "pass",
                        "evidence": primary_evidence,
                    }],
                },
            })
        else:
            rows.append({
                "testCaseId": tc["id"],
                "result": "not_run",
                "required": False,
                "quality": bool(tc.get("quality", False)),
                "acceptanceCriteria": tc.get("acceptanceCriteria", []),
                "evidence": [],
                "reason": f"Optional testcase not selected by {source}.",
            })
    return {**evaluation, "testResults": rows}
