"""Lightweight TestCase attempt ledger.

The ledger is observational: it records which TC an iteration attempted and what
evidence/result was produced. It does not prescribe which iteration must run a
TC, preserving CodeMind's dynamic repair loop.
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


def _lookup_sources(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Ordered fallback sources for convergence fields.

    The evaluator prompt instructs the model to write hypothesis/ruledOut/
    remainingHypotheses either flat on the testResult row or nested under
    ``uiExploration`` (and its latest ``attempts[]`` entry). Read all of these so
    convergence context is not silently dropped depending on where the model put
    it.
    """
    sources: list[dict[str, Any]] = [row]
    attempt = row.get("attempt")
    if isinstance(attempt, dict):
        sources.append(attempt)
    ui = row.get("uiExploration")
    if isinstance(ui, dict):
        sources.append(ui)
        attempts = ui.get("attempts")
        if isinstance(attempts, list):
            for entry in reversed(attempts):
                if isinstance(entry, dict):
                    sources.append(entry)
                    break
    return sources


def _string_value(row: dict[str, Any], *keys: str) -> str:
    for source in _lookup_sources(row):
        for key in keys:
            value = source.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _list_value(row: dict[str, Any], *keys: str) -> list[str]:
    for source in _lookup_sources(row):
        for key in keys:
            items = _string_list(source.get(key))
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
        "nextSelectorCandidates": _list_value(row, "nextSelectorCandidates", "selectorCandidates"),
        "recoveryAction": _string_value(row, "recoveryAction"),
        "failureCategory": _string_value(row, "category", "failureClass"),
        "sameProblemKey": _string_value(row, "sameProblemKey"),
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
    # Second pass: pull structured failure-triage data from failedChecks[].
    # Evaluator writes recoveryAction / failureCategory / sameProblemKey on
    # failedCheck entries rather than testResult rows, so convergence tracking
    # must pick them up here. This is how the "build failed because of missing
    # pod" insight flows into tc-attempts.json for the budget guard and next
    # round's generator to see.
    failed_checks = list(evaluation.get("failedChecks") or [])
    recovery_by_tc: dict[str, dict[str, Any]] = {}
    for fc in failed_checks:
        if not isinstance(fc, dict):
            continue
        tcs = fc.get("testCaseIds") or fc.get("testCases") or []
        if not isinstance(tcs, list):
            tcs = [tcs]
        if not tcs:
            # When failedChecks have no explicit TC ids, associate with
            # the most-tried TC so the insight is not silently lost.
            tcs = sorted(attempts.keys())[:1]
        recovery_raw = (fc.get("recoveryAction") or "").strip()
        failure_category = (fc.get("category") or "").strip()
        spk = (fc.get("sameProblemKey") or "").strip()
        specific_errors = fc.get("specificErrors") or []
        if not recovery_raw and not failure_category:
            continue
        for tc_id in tcs:
            tc_id = str(tc_id)
            entry = recovery_by_tc.setdefault(tc_id, {
                "recoveryActions": [],
                "failureCategories": [],
                "sameProblemKeys": [],
                "specificErrors": [],
            })
            if recovery_raw and recovery_raw not in entry["recoveryActions"]:
                entry["recoveryActions"].append(recovery_raw)
            if failure_category and failure_category not in entry["failureCategories"]:
                entry["failureCategories"].append(failure_category)
            if spk and spk not in entry["sameProblemKeys"]:
                entry["sameProblemKeys"].append(spk)
            for err in specific_errors:
                if isinstance(err, str) and err and err not in entry["specificErrors"]:
                    entry["specificErrors"].append(err)
    latest: dict[str, Any] = {}
    progress_by_tc: dict[str, Any] = {}
    for tc_id, rows in attempts.items():
        if not isinstance(rows, list) or not rows:
            continue
        latest[tc_id] = rows[-1]
        ruled_out: list[str] = []
        remaining: list[str] = []
        selector_candidates: list[str] = []
        progress_kinds: list[str] = []
        narrowing_rounds = 0
        for item in rows:
            if not isinstance(item, dict):
                continue
            item_narrowed = False
            for value in item.get("ruledOut") or []:
                if value and value not in ruled_out:
                    ruled_out.append(value)
                    item_narrowed = True
            for value in item.get("remainingHypotheses") or []:
                if value and value not in remaining:
                    remaining.append(value)
                    item_narrowed = True
            for value in item.get("nextSelectorCandidates") or []:
                if value and value not in selector_candidates:
                    selector_candidates.append(value)
                    item_narrowed = True
            item_recovery = (item.get("recoveryAction") or "").strip()
            if item_recovery:
                item_narrowed = True
            if item_narrowed:
                narrowing_rounds += 1
            kind = str(item.get("progressKind") or "").strip()
            if kind and kind not in progress_kinds:
                progress_kinds.append(kind)
        attempt_count = len([x for x in rows if isinstance(x, dict)])
        recovery = recovery_by_tc.get(tc_id) or {}
        # A "triage-ful" round is one where failedChecks produced a
        # specific recovery action (not just "triage_needed" placeholder).
        triage_actions = [
            a for a in (recovery.get("recoveryActions") or [])
            if a and a != "triage_needed"
        ]
        progress_by_tc[tc_id] = {
            "attemptCount": attempt_count,
            "progressKinds": progress_kinds,
            "ruledOut": ruled_out,
            "remainingHypotheses": remaining,
            "nextSelectorCandidates": selector_candidates,
            # How many attempts actually narrowed the search space. When this
            # stays at 0 across repeated failing attempts, the loop is retrying
            # without convergence (an "invalid retry" pattern the budget guard
            # surfaces as a no-narrowing warning).
            "narrowingRounds": narrowing_rounds,
            # Recovery-action surface area: structured triage output from the
            # evaluator's failedChecks[] rows. These feed the next Generator's
            # "recovery-action first" rule so it runs `pod install` /
            # `custom_build_wrapper.sh` / the project's own dependency-command rather than
            # re-issuing the same failing xcodebuild command.
            "recoveryActions": recovery.get("recoveryActions") or [],
            "failureCategories": recovery.get("failureCategories") or [],
            "sameProblemKeys": recovery.get("sameProblemKeys") or [],
            "specificErrors": recovery.get("specificErrors") or [],
            "hasSpecificRecovery": bool(triage_actions),
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
