"""Lightweight TestCase attempt ledger.

The ledger is observational: it records which TC an iteration attempted and what
evidence/result was produced. It does not prescribe which iteration must run a
TC, preserving AutoMind's dynamic repair loop.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.artifacts import normalize_evidence_refs, normalize_test_result_value
from orchestrator.state import rel_to_root, update_runtime_state

LEDGER_NAME = "tc-attempts.json"


def _tc_id(row: dict[str, Any]) -> str:
    return str(row.get("testCaseId") or row.get("id") or row.get("name") or "").strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if str(x).strip()]


def _string_value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None and isinstance(row.get("attempt"), dict):
            value = row["attempt"].get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _list_value(row: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = row.get(key)
        if value is None and isinstance(row.get("attempt"), dict):
            value = row["attempt"].get(key)
        items = _string_list(value)
        if items:
            return items
    return []


def _attempt_from_result(row: dict[str, Any], iteration: int, source: str) -> dict[str, Any] | None:
    tc_id = _tc_id(row)
    if not tc_id:
        return None
    evidence = normalize_evidence_refs(row.get("evidence") or row.get("evidencePaths") or [])
    observed = _list_value(row, "observedSignals")
    missing = _list_value(row, "missingSignals")
    progress_kind = _string_value(row, "progressKind", "progress")
    if progress_kind not in {"navigation", "control_discovery", "proof", "evidence", "blocked", "unknown"}:
        progress_kind = "unknown"
    attempt_type = _string_value(row, "attemptType") or ("proof" if progress_kind == "proof" else "exploration" if progress_kind in {"navigation", "control_discovery"} else "")
    return {
        "iteration": int(row.get("attemptIteration") or iteration),
        "result": normalize_test_result_value(str(row.get("result") or "unknown")),
        "source": source,
        "progressKind": progress_kind,
        "attemptType": attempt_type,
        "hypothesis": _string_value(row, "hypothesis"),
        "actionTried": _string_value(row, "actionTried", "nextAction"),
        "expectedSignal": _string_value(row, "expectedSignal"),
        "outcome": _string_value(row, "outcome") or str(row.get("summary") or row.get("reason") or "").strip(),
        "summary": str(row.get("summary") or row.get("reason") or "").strip(),
        "evidence": evidence,
        "observedSignals": observed,
        "missingSignals": missing,
        "ruledOut": _list_value(row, "ruledOut", "ruledOutHypotheses"),
        "remainingHypotheses": _list_value(row, "remainingHypotheses"),
    }


def record_tc_attempts(task_dir: Path, evaluation: dict[str, Any], source: str = "evaluation") -> dict[str, Any]:
    """Merge evaluation.testResults into task-local tc-attempts.json."""
    path = task_dir / LEDGER_NAME
    if path.exists():
        try:
            ledger = json.loads(path.read_text())
        except json.JSONDecodeError:
            ledger = {}
    else:
        ledger = {}
    attempts = ledger.get("attempts") if isinstance(ledger.get("attempts"), dict) else {}
    iteration = int(evaluation.get("iteration") or 0)
    current_tc = None
    for row in evaluation.get("testResults") or []:
        if not isinstance(row, dict):
            continue
        attempt = _attempt_from_result(row, iteration, source)
        if not attempt:
            continue
        tc_id = _tc_id(row)
        current_tc = current_tc or tc_id
        rows = attempts.setdefault(tc_id, [])
        key = (attempt["iteration"], attempt["source"], tuple(attempt.get("evidence") or []))
        existing_keys = {(int(x.get("iteration") or 0), str(x.get("source") or ""), tuple(x.get("evidence") or [])) for x in rows if isinstance(x, dict)}
        if key in existing_keys:
            continue
        rows.append(attempt)
        rows.sort(key=lambda x: int(x.get("iteration") or 0))
    latest: dict[str, Any] = {}
    progress_by_tc: dict[str, Any] = {}
    for tc_id, rows in attempts.items():
        if not isinstance(rows, list) or not rows:
            continue
        latest[tc_id] = rows[-1]
        ruled_out: list[str] = []
        remaining: list[str] = []
        progress_kinds: list[str] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            for value in item.get("ruledOut") or []:
                if value and value not in ruled_out:
                    ruled_out.append(value)
            for value in item.get("remainingHypotheses") or []:
                if value and value not in remaining:
                    remaining.append(value)
            kind = str(item.get("progressKind") or "").strip()
            if kind and kind not in progress_kinds:
                progress_kinds.append(kind)
        progress_by_tc[tc_id] = {
            "attemptCount": len([x for x in rows if isinstance(x, dict)]),
            "progressKinds": progress_kinds,
            "ruledOut": ruled_out,
            "remainingHypotheses": remaining,
            "latestOutcome": str(rows[-1].get("outcome") or rows[-1].get("summary") or "") if isinstance(rows[-1], dict) else "",
        }
    ledger = {
        "schema": "automind.tc_attempts.v1",
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "currentTc": current_tc or ledger.get("currentTc"),
        "nextTc": current_tc or ledger.get("nextTc"),
        "attempts": attempts,
        "latest": latest,
        "progressByTc": progress_by_tc,
    }
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")
    update_runtime_state(task_dir, tcAttempts=rel_to_root(path), currentTc=ledger.get("currentTc"), nextTc=ledger.get("nextTc"))
    return ledger


def read_tc_attempts(task_dir: Path) -> dict[str, Any]:
    path = task_dir / LEDGER_NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
