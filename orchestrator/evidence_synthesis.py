"""Evidence-summary synthesis into partial TC-level evaluation progress.

This module is deliberately conservative: it never marks the task finished and
never upgrades task-level result to pass.  It only turns existing proof summary
artifacts into a partial `evaluation.json.testResults[]` ledger so humans and
subsequent completion/evaluator steps can see current TC progress.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.artifacts import extract_declared_testcases, normalize_artifact_id
from orchestrator.state import rel_to_root, read_evaluation_json, read_runtime_state, update_runtime_state, write_evaluation_json
from orchestrator.tc_attempts import read_tc_attempts, record_tc_attempts

# Results that represent a real, evaluator-grade conclusion for a TC. Evidence
# synthesis must preserve these instead of diluting them back to "not_run".
_REAL_TC_RESULTS = {"fail", "pass", "blocked"}

SUMMARY_NAME_RE = re.compile(r"(summary|result)\.json$", re.IGNORECASE)
TC_RE = re.compile(r"TC-[A-Z0-9]+", re.IGNORECASE)
STOP_REASON_RE = re.compile(r"stop_reason['\"=:\s]+([a-zA-Z0-9_\-]+)")
EVENT_RE = re.compile(r"\b(music_audio_stop|music_audio_finish|v3_audio_over|v3_audio_fail|BUILD SUCCESS|BUILD FAILED|INSTALL_FAILED|Success)\b", re.IGNORECASE)


def _safe_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(errors="ignore"))
    except Exception:
        return None


def _iter_num(path: Path) -> int:
    for part in path.parts:
        if part.startswith("iter-"):
            try:
                return int(part.split("-", 1)[1])
            except ValueError:
                return 0
    return 0


def _summary_paths(task_dir: Path, max_files: int = 200) -> list[Path]:
    logs = task_dir / "logs"
    if not logs.exists():
        return []
    paths = [p for p in logs.glob("iter-*/*") if p.is_file() and SUMMARY_NAME_RE.search(p.name)]
    paths.extend(p for p in logs.glob("iter-*/*/*") if p.is_file() and SUMMARY_NAME_RE.search(p.name))
    paths = sorted(set(paths), key=lambda p: (_iter_num(p), p.stat().st_mtime if p.exists() else 0, str(p)))
    return paths[-max_files:]


def _json_strings(value: Any, limit: int = 120_000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        text = str(value)
    return text[:limit]


def _extract_tc_ids(path: Path, data: Any) -> list[str]:
    text = f"{path.as_posix()} {_json_strings(data)}"
    ids: list[str] = []
    for match in TC_RE.findall(text):
        tc = match.upper()
        if tc not in ids:
            ids.append(tc)
    return ids


def _observed_signals(data: Any) -> list[str]:
    text = _json_strings(data)
    signals: list[str] = []
    for event in EVENT_RE.findall(text):
        normalized = event if event.isupper() else event.lower()
        if normalized not in signals:
            signals.append(normalized)
    for reason in STOP_REASON_RE.findall(text):
        signal = f"stop_reason={reason}"
        if signal not in signals:
            signals.append(signal)
    for key in ["exitCode", "result", "apk", "pid", "playbackState", "audioStarted"]:
        if f'"{key}"' in text and key not in signals:
            signals.append(key)
    return signals[:20]


def _artifact_candidate_result(data: Any) -> str:
    text = _json_strings(data).lower()
    if any(token in text for token in ["build failed", "install_failed", '"exitcode": 1', '"result": "fail"', '"result":"fail"']):
        return "fail"
    if any(token in text for token in ["music_audio_stop", "stop_reason", '"exitcode": 0', '"result": "pass"', '"result":"pass"', "success"]):
        return "pass_candidate"
    return "evidence_seen"


def _as_string_list(value: Any) -> list[str]:
    """Normalize lightweight evidenceIndex string-or-array hint fields."""
    if value is None:
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_as_string_list(item))
        dedup: list[str] = []
        for item in result:
            if item and item not in dedup:
                dedup.append(item)
        return dedup
    text = str(value).strip()
    return [text] if text else []


def _normalize_evidence_path(task_dir: Path, raw: Any) -> str:
    """Return a compact task-relative evidence path when possible."""
    value = str(raw or "").strip()
    if not value:
        return ""
    path = Path(value).expanduser()
    if path.is_absolute():
        try:
            return path.relative_to(task_dir).as_posix()
        except ValueError:
            return path.as_posix()
    return value.replace("\\", "/")


def _walk_evidence_index(value: Any) -> list[dict[str, Any]]:
    """Extract lightweight evidenceIndex entries from nested summary/result JSON.

    The preferred shape is a list of objects under `evidenceIndex`. For
    compatibility with early drafts, also accepts `{artifacts: [...]}`. Nested
    summaries are scanned so runner-specific sections can expose their own
    index without a new file.
    """
    entries: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            idx = node.get("evidenceIndex")
            if isinstance(idx, list):
                for item in idx:
                    if isinstance(item, dict):
                        entries.append(item)
            elif isinstance(idx, dict) and isinstance(idx.get("artifacts"), list):
                for item in idx.get("artifacts") or []:
                    if isinstance(item, dict):
                        entries.append(item)
            for child in node.values():
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(value)
    dedup: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = set()
    for item in entries:
        path = str(item.get("path") or item.get("file") or item.get("evidence") or "").strip()
        if not path:
            continue
        tc_vals = tuple(_as_string_list(item.get("tc") or item.get("testCaseId") or item.get("testCaseIds")))
        sig_vals = tuple(_as_string_list(item.get("signal") or item.get("signals") or item.get("observedSignals")))
        key = (path, str(item.get("type") or ""), tc_vals, sig_vals)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
    return dedup


def _signals_from_evidence_index(entries: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    observed: list[str] = []
    missing: list[str] = []
    for item in entries:
        signals = _as_string_list(item.get("signal") or item.get("signals") or item.get("observedSignals"))
        signals.extend(_as_string_list(item.get("missingSignals")))
        for signal in signals:
            if signal.startswith("missing:"):
                value = signal.split(":", 1)[1].strip()
                if value and value not in missing:
                    missing.append(value)
            elif signal and signal not in observed:
                observed.append(signal)
    return observed, missing


def _merge_candidate(existing: dict[str, Any] | None, row: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        return row
    merged = dict(existing)
    for key in ["evidence", "observedSignals", "missingSignals", "sources", "evidenceIndex"]:
        vals: list[Any] = []
        for source in [existing.get(key), row.get(key)]:
            if isinstance(source, list):
                vals.extend(source)
        dedup: list[Any] = []
        for val in vals:
            if val not in dedup:
                dedup.append(val)
        merged[key] = dedup
    merged["attemptIteration"] = max(int(existing.get("attemptIteration") or 0), int(row.get("attemptIteration") or 0))
    if row.get("candidateResult") == "fail" or existing.get("candidateResult") == "fail":
        merged["candidateResult"] = "fail"
        merged["result"] = "partial"
    elif row.get("candidateResult") == "pass_candidate" or existing.get("candidateResult") == "pass_candidate":
        merged["candidateResult"] = "pass_candidate"
        merged["result"] = "partial"
    merged["summary"] = "; ".join(x for x in [existing.get("summary"), row.get("summary")] if x)[:1000]
    return merged


def _prior_real_tc_results(task_dir: Path) -> dict[str, dict[str, Any]]:
    """Map normalized TC id -> latest real (fail/pass/blocked) ledger conclusion.

    Reads tc-attempts.json so synthesis can carry forward a genuine prior verdict
    instead of overwriting it with an optimistic ``not_run`` when no fresh
    evidence is mapped this pass. Only the most recent real conclusion per TC is
    returned; exploratory/unknown attempts are ignored.
    """
    ledger = read_tc_attempts(task_dir)
    attempts = ledger.get("attempts") if isinstance(ledger.get("attempts"), dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for tc_id, rows in attempts.items():
        if not isinstance(rows, list):
            continue
        chosen: dict[str, Any] | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            result = str(row.get("result") or "").strip().lower()
            if result not in _REAL_TC_RESULTS:
                continue
            if chosen is None or int(row.get("iteration") or 0) >= int(chosen.get("iteration") or 0):
                chosen = row
        if chosen is None:
            continue
        norm = normalize_artifact_id(str(tc_id))
        out[norm] = {
            "result": str(chosen.get("result") or "").strip().lower(),
            "iteration": int(chosen.get("iteration") or 0),
            "evidence": list(chosen.get("evidence") or []),
            "observedSignals": list(chosen.get("observedSignals") or []),
            "missingSignals": list(chosen.get("missingSignals") or []),
            "summary": str(chosen.get("summary") or chosen.get("outcome") or "").strip(),
        }
    return out


def build_tc_evidence_matrix(task_dir: Path) -> dict[str, Any]:
    """Return a conservative TC matrix from existing summary/result artifacts."""
    declared = extract_declared_testcases(task_dir)
    by_norm = {tc["normalizedId"]: tc for tc in declared}
    matrix: dict[str, dict[str, Any]] = {}
    evidence_paths = _summary_paths(task_dir)
    latest_iter = max([_iter_num(p) for p in evidence_paths], default=0)

    evidence_index_all: list[dict[str, Any]] = []

    for path in evidence_paths:
        data = _safe_json(path)
        if data is None:
            continue
        rel = rel_to_root(path)
        index_entries = _walk_evidence_index(data)
        normalized_entries: list[dict[str, Any]] = []
        for entry in index_entries:
            ev_path = _normalize_evidence_path(task_dir, entry.get("path") or entry.get("file") or entry.get("evidence"))
            if not ev_path:
                continue
            compact = {"path": ev_path}
            if entry.get("type"):
                compact["type"] = str(entry.get("type"))
            tc_hint = _as_string_list(entry.get("tc") or entry.get("testCaseId") or entry.get("testCaseIds"))
            if tc_hint:
                compact["tc"] = tc_hint[0] if len(tc_hint) == 1 else tc_hint
            signal_hint = _as_string_list(entry.get("signal") or entry.get("signals") or entry.get("observedSignals"))
            if signal_hint:
                compact["signal"] = signal_hint[0] if len(signal_hint) == 1 else signal_hint
            normalized_entries.append(compact)
            if compact not in evidence_index_all:
                evidence_index_all.append(compact)

        tc_ids = _extract_tc_ids(path, data)
        for entry in normalized_entries:
            for tc_hint in _as_string_list(entry.get("tc")):
                tc = tc_hint.upper()
                if tc not in tc_ids:
                    tc_ids.append(tc)
        signals = _observed_signals(data)
        candidate = _artifact_candidate_result(data)
        if not tc_ids:
            # Keep unassigned evidence in metadata; do not create fake TC rows.
            continue
        for tc_id in tc_ids:
            norm = normalize_artifact_id(tc_id)
            tc = by_norm.get(norm, {})
            relevant_entries = [
                entry for entry in normalized_entries
                if norm in {normalize_artifact_id(tc_hint) for tc_hint in _as_string_list(entry.get("tc"))}
            ]
            index_observed, index_missing = _signals_from_evidence_index(relevant_entries)
            observed = []
            for signal in [*signals, *index_observed]:
                if signal and signal not in observed:
                    observed.append(signal)
            missing = index_missing[:]
            if not observed and not missing:
                missing = ["structured evidence summary did not expose machine signals"]
            evidence_refs = [rel]
            for entry in relevant_entries:
                ev_path = str(entry.get("path") or "").strip()
                if ev_path and ev_path not in evidence_refs:
                    evidence_refs.append(ev_path)
            row = {
                "testCaseId": tc.get("id") or tc_id,
                "result": "partial" if candidate != "fail" else "partial",
                "candidateResult": candidate,
                "required": bool(tc.get("required", True)),
                "quality": bool(tc.get("quality", False)),
                "acceptanceCriteria": tc.get("acceptanceCriteria") or [],
                "attemptIteration": _iter_num(path),
                "evidence": evidence_refs,
                "observedSignals": observed,
                "missingSignals": missing,
                "summary": f"Evidence summary observed in {rel}; final evaluator judgment still required.",
                "sources": ["evidence-summary-synthesis"],
                "evidenceIndex": relevant_entries,
                "evidenceAssessment": {
                    "verdict": "candidate" if candidate == "pass_candidate" else "not_proved",
                    "assessor": "evidence-summary-synthesis",
                    "reason": "Derived from existing summary/result artifact; does not change task-level conclusion.",
                    "machineAnchor": rel,
                    "hardMetrics": [{
                        "name": "summary_artifact_present",
                        "value": candidate,
                        "expected": "pass",
                        "passed": False,
                        "evidence": rel,
                    }],
                },
            }
            matrix[norm] = _merge_candidate(matrix.get(norm), row)

    # Add missing required rows so the ledger shows what remains unknown/not_run.
    # P0-5: never dilute a real prior conclusion. If the tc-attempts ledger
    # already recorded a fail/pass/blocked result for a TC with no fresh
    # evidence this pass, carry that conclusion forward instead of overwriting it
    # with an optimistic "not_run".
    prior_results = _prior_real_tc_results(task_dir)
    for tc in declared:
        norm = tc["normalizedId"]
        if norm not in matrix and tc.get("required"):
            prior = prior_results.get(norm)
            if prior:
                matrix[norm] = {
                    "testCaseId": tc["id"],
                    "result": prior["result"],
                    "candidateResult": prior["result"],
                    "required": True,
                    "quality": bool(tc.get("quality", False)),
                    "acceptanceCriteria": tc.get("acceptanceCriteria") or [],
                    "attemptIteration": prior.get("iteration") or latest_iter or None,
                    "evidence": prior.get("evidence") or [],
                    "observedSignals": prior.get("observedSignals") or [],
                    "missingSignals": prior.get("missingSignals") or [],
                    "summary": prior.get("summary") or f"Carried forward prior {prior['result']} conclusion from tc-attempts ledger; no fresh evidence this pass.",
                    "sources": ["evidence-summary-synthesis", "tc-attempts-carry-forward"],
                }
                continue
            matrix[norm] = {
                "testCaseId": tc["id"],
                "result": "not_run",
                "candidateResult": "missing_evidence",
                "required": True,
                "quality": bool(tc.get("quality", False)),
                "acceptanceCriteria": tc.get("acceptanceCriteria") or [],
                "attemptIteration": latest_iter or None,
                "evidence": [],
                "observedSignals": [],
                "missingSignals": ["no summary/result evidence mapped to this testcase yet"],
                "summary": "No existing evidence summary mapped to this testcase yet.",
                "sources": ["evidence-summary-synthesis"],
            }

    return {
        "latestEvidenceIteration": latest_iter,
        "summaryArtifacts": [rel_to_root(p) for p in evidence_paths],
        "evidenceIndex": evidence_index_all,
        "testResults": [matrix[key] for key in sorted(matrix)],
    }


def should_synthesize_evaluation(task_dir: Path, evaluation: dict[str, Any] | None = None) -> bool:
    evaluation = evaluation if evaluation is not None else (read_evaluation_json(task_dir) or {})
    matrix = build_tc_evidence_matrix(task_dir)
    latest_iter = int(matrix.get("latestEvidenceIteration") or 0)
    if latest_iter <= 0:
        return False
    eval_iter = int(evaluation.get("iteration") or 0) if isinstance(evaluation, dict) else 0
    has_results = isinstance(evaluation.get("testResults") if isinstance(evaluation, dict) else None, list) and bool(evaluation.get("testResults"))
    if not has_results:
        return True
    return eval_iter < latest_iter


def synthesize_evaluation_from_evidence(task_dir: Path, *, reason: str = "evidence_summary_synthesis") -> dict[str, Any] | None:
    """Write partial evaluation progress from summaries, without claiming finish."""
    current = read_evaluation_json(task_dir) or {}
    matrix = build_tc_evidence_matrix(task_dir)
    latest_iter = int(matrix.get("latestEvidenceIteration") or 0)
    if latest_iter <= 0:
        return None
    if not should_synthesize_evaluation(task_dir, current):
        return None

    state = read_runtime_state(task_dir) or {}
    previous_iter = int(current.get("iteration") or state.get("iteration") or 1)
    iteration = max(previous_iter, latest_iter, 1)
    test_results = matrix.get("testResults") or []
    candidate_count = sum(1 for row in test_results if row.get("candidateResult") == "pass_candidate")
    missing_count = sum(1 for row in test_results if row.get("result") in {"not_run", "blocked"} or row.get("candidateResult") == "missing_evidence")

    # P0-5: do not hard-downgrade a real prior classification. Preserve an
    # existing real failedChecks list and a real non-retry nextAction
    # (ask_user/stop/replan/...) instead of overwriting them with the generic
    # synthesis placeholder. Synthesis only adds progress; it never claims finish
    # and never upgrades to pass.
    synthesis_check = {
        "name": "tc_evidence_synthesis_pending_final_judgment",
        "reason": "Existing evidence summaries were converted into TC-level progress, but task-level finish/pass is not claimed until evaluator/completion-check validates them.",
        "category": "validation_failure",
    }
    prior_checks = [c for c in (current.get("failedChecks") or []) if isinstance(c, dict) and str(c.get("name") or "") != synthesis_check["name"]]
    failed_checks = prior_checks + [synthesis_check] if prior_checks else [synthesis_check]

    prior_action = str(current.get("nextAction") or "").strip()
    preserve_actions = {"ask_user", "stop", "stop_blocked", "replan", "pause_for_external"}
    next_action = prior_action if prior_action in preserve_actions else "retry_generator"

    prior_result = str(current.get("result") or "").strip().lower()
    if prior_result in {"fail", "blocked"}:
        result = prior_result
    else:
        result = "blocked" if missing_count else "in_progress"

    evaluation = {
        **current,
        "iteration": iteration,
        "result": result,
        "summary": f"Evidence summaries collected through iter-{latest_iter}; synthesized {len(test_results)} TC progress rows ({candidate_count} pass candidates, {missing_count} missing). Final evaluator/completion judgment still required.",
        "failedChecks": failed_checks,
        "nextAction": next_action,
        "testResults": test_results,
        "evidenceIndex": matrix.get("evidenceIndex") or current.get("evidenceIndex") or [],
        "evidenceSynthesis": {
            "schema": "automind.evidence_synthesis.v1",
            "reason": reason,
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "latestEvidenceIteration": latest_iter,
            "summaryArtifactCount": len(matrix.get("summaryArtifacts") or []),
            "summaryArtifacts": matrix.get("summaryArtifacts") or [],
            "doesNotChangeTaskLevelConclusion": True,
        },
    }
    write_evaluation_json(task_dir, evaluation)
    record_tc_attempts(task_dir, evaluation, source="evidence-summary-synthesis")
    update_runtime_state(task_dir, evidenceSynthesis=evaluation["evidenceSynthesis"])
    return evaluation
