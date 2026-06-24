"""Status guidance and user-facing report manifests for AutoMind.

This module is presentation/reporting oriented. It reads task artifacts and
gate reports to explain the next action, but it does not drive the harness
loop or mutate product/runtime code.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from orchestrator.completion import build_completion_report
from orchestrator.phase_transition import refresh_phase_transition_summary
from orchestrator.config import LOCAL_REUSE_INDEX_PATH, SUMMARY_DIR, SUMMARY_LESSONS_PATH
from orchestrator.artifacts import merge_verification_status_from_completion, summarize_plan_checklists
from orchestrator.resume import build_resume_recovery_entry
from orchestrator.artifacts import requirement_contract_paths
from orchestrator.state import get_runtime_state_path, get_task_dir, read_evaluation_json, read_runtime_state, rel_to_root
from orchestrator.workflow import check_workflow_consistency
from orchestrator.workflow_state import read_workflow_state
from orchestrator.workflow_contract import _normalize_runtime_level



def _runtime_state_ref(task_dir: Path) -> str:
    return rel_to_root(get_runtime_state_path(task_dir))


def _runtime_state_read_files(task_dir: Path) -> list[str]:
    return [_runtime_state_ref(task_dir)]

def latest_iter_dir(task_dir: Path) -> Optional[Path]:
    """Return the latest logs/iter-* directory when present."""
    logs_dir = task_dir / "logs"
    if not logs_dir.exists():
        return None
    iters = [p for p in logs_dir.glob("iter-*") if p.is_dir()]
    if not iters:
        return None
    def key(path: Path):
        match = re.search(r"iter-(\d+)$", path.name)
        return int(match.group(1)) if match else -1
    return sorted(iters, key=key)[-1]


def _iter_number_from_dir(path: Optional[Path]) -> int | None:
    if path is None:
        return None
    match = re.search(r"iter-(\d+)$", path.name)
    return int(match.group(1)) if match else None


def _count_iterations(task_dir: Path) -> int | None:
    """Total verification iterations attempted (logs/iter-* directories)."""
    logs_dir = task_dir / "logs"
    if not logs_dir.exists():
        return None
    iters = {
        int(m.group(1))
        for p in logs_dir.glob("iter-*")
        if p.is_dir() and (m := re.search(r"iter-(\d+)$", p.name))
    }
    return len(iters) or None


def _testcase_pass_counts(test_results: list) -> tuple[int, int]:
    """Return (total, passed) over recorded testResults[] rows."""
    total = 0
    passed = 0
    for item in test_results:
        if not isinstance(item, dict):
            continue
        if not (item.get("testCaseId") or item.get("id")):
            continue
        total += 1
        if str(item.get("result") or "").strip().lower() == "pass":
            passed += 1
    return total, passed


_WAIT_ACTIONS = {"wait_for_user", "wait_for_tool", "ask_user", "pause_for_external"}
_WAIT_STATUSES = {"waiting_user", "waiting_tool"}


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _active_duration(task_dir: Path) -> dict | None:
    """Wall-clock span of the run minus time spent waiting on the user/tool.

    Reads the append-only workflow-events log. For each consecutive event pair
    the elapsed interval is attributed to "active" work unless the earlier event
    left the loop in a waiting_user / waiting_tool / ask_user state, in which
    case the interval is excluded (the agent was blocked on the user, not
    working).
    """
    events_path = task_dir / "automind-workflow-events.jsonl"
    if not events_path.exists():
        return None
    stamps: list[tuple[datetime, str, str]] = []
    for line in events_path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict) or not ev.get("at"):
            continue
        try:
            when = datetime.fromisoformat(str(ev.get("at")))
        except Exception:
            continue
        action = str(ev.get("nextAction") or ev.get("action") or "").strip().lower()
        status = str(ev.get("status") or "").strip().lower()
        stamps.append((when, action, status))
    if len(stamps) < 2:
        return None
    stamps.sort(key=lambda x: x[0])
    total = (stamps[-1][0] - stamps[0][0]).total_seconds()
    waiting = 0.0
    for (start, action, status), (nxt, _, _) in zip(stamps, stamps[1:]):
        gap = (nxt - start).total_seconds()
        if gap <= 0:
            continue
        if action in _WAIT_ACTIONS or status in _WAIT_STATUSES:
            waiting += gap
    active = max(total - waiting, 0.0)
    return {
        "active": active,
        "total": total,
        "waiting": waiting,
        "activeText": _format_duration(active),
        "totalText": _format_duration(total),
        "waitingText": _format_duration(waiting),
    }


def build_summary_reuse_status(task_dir: Path) -> dict:
    """Return whether Phase 4 summary and workspace reuse memory exist.

    This is intentionally separate from completion-check: completion proves the
    current task, while summary/reuse proves the result has been preserved for
    future tasks.
    """
    state = read_runtime_state(task_dir) or {}
    summary_meta = state.get("summary") if isinstance(state.get("summary"), dict) else {}
    summary_path = task_dir / "summary.md"
    lessons_path = SUMMARY_LESSONS_PATH
    reuse_index_path = LOCAL_REUSE_INDEX_PATH
    missing: list[str] = []
    if not summary_path.exists():
        missing.append(rel_to_root(summary_path))
    if not lessons_path.exists():
        missing.append(rel_to_root(lessons_path))
    if not reuse_index_path.exists():
        missing.append(rel_to_root(reuse_index_path))
    result = "pass" if not missing else "missing"
    return {
        "result": result,
        "ok": result == "pass",
        "missing": missing,
        "summaryPath": rel_to_root(summary_path),
        "lessonsPath": rel_to_root(lessons_path),
        "reuseIndexPath": rel_to_root(reuse_index_path),
        "generatedAt": summary_meta.get("generatedAt", "-"),
        "reason": "summary/reuse memory generated" if result == "pass" else "summary/reuse memory must be generated before final handoff",
    }



def _read_text(path: Path, limit: int = 40_000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(errors="ignore")
    return text if len(text) <= limit else text[:limit].rstrip() + "\n\n...[truncated]"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(errors="ignore"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _html(text: object) -> str:
    return html.escape(str(text if text is not None else ""))


def _link(task_dir: Path, path_value: object, label: str | None = None) -> str:
    if not path_value:
        return "-"
    raw = str(path_value)
    path = Path(raw)
    href = raw if path.is_absolute() else raw
    if path.is_absolute():
        try:
            href = path.relative_to(task_dir).as_posix()
        except ValueError:
            href = path.as_posix()
    return f'<a href="{_html(href)}">{_html(label or raw)}</a>'


def _extract_requirements(md: str, limit: int = 12) -> list[dict]:
    rows: list[dict] = []
    current = ""
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("### ") or stripped.startswith("## R"):
            current = stripped.lstrip("# ").strip()
            if current and len(rows) < limit:
                rows.append({"requirement": current, "acceptance": []})
        elif "AC-" in stripped:
            if not rows and current:
                rows.append({"requirement": current, "acceptance": []})
            if rows and len(rows[-1]["acceptance"]) < 8:
                rows[-1]["acceptance"].append(stripped.lstrip("- ").strip())
    return rows[:limit]


def _collect_latest_files(task_dir: Path, limit: int = 80) -> list[Path]:
    logs_dir = task_dir / "logs"
    if not logs_dir.exists():
        return []
    files = [p for p in logs_dir.rglob("*") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}




def _task_relative_path(task_dir: Path, path_value: object) -> str:
    if not path_value:
        return ""
    raw = str(path_value)
    if "#" in raw:
        raw = raw.split("#", 1)[0]
    task_prefix = f".automind/tasks/{task_dir.name}/"
    if raw.startswith(task_prefix):
        return raw[len(task_prefix):]
    path = Path(raw)
    if path.is_absolute():
        try:
            return path.relative_to(task_dir).as_posix()
        except ValueError:
            return path.as_posix()
    return raw


def _extract_tc_ids(value: object) -> list[str]:
    text = ""
    if isinstance(value, dict):
        direct = []
        for key in ["testCaseId", "testcaseId", "testCase", "tcId", "id"]:
            if value.get(key):
                direct.append(str(value.get(key)))
        for key in ["testCaseIds", "testCases", "tcIds", "acceptanceCriteria"]:
            raw = value.get(key)
            if isinstance(raw, list):
                direct.extend(str(item) for item in raw)
        text = " ".join(direct + [str(value.get("note") or ""), str(value.get("name") or ""), str(value.get("path") or value.get("file") or value.get("evidence") or "")])
    else:
        text = str(value or "")
    seen: list[str] = []
    for match in re.findall(r"\bTC-[A-Za-z0-9_.-]+\b", text):
        tc = match.upper()
        if tc not in seen:
            seen.append(tc)
    return seen


def _short_artifact_link(task_dir: Path, path_value: object, note: str = "", kind: str = "") -> str:
    rel = _task_relative_path(task_dir, path_value)
    if not rel:
        return ""
    label = note or Path(rel).name or rel
    suffix = f" <span class='muted'>({_html(kind)})</span>" if kind else ""
    return f"<li>{_link(task_dir, rel, label)}{suffix}</li>"


def _artifact_kind(path_value: object, explicit: object = "") -> str:
    kind = str(explicit or "").strip()
    if kind:
        return kind
    suffix = Path(str(path_value or "")).suffix.lower()
    if suffix in {".log", ".txt"}:
        return "log"
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return "screenshot"
    if suffix in {".json", ".jsonl"}:
        return "evidence"
    return "artifact"


_ARTIFACT_PATH_RE = re.compile(
    r"(?:\.automind/tasks/[^\s`'\"<>]+/)?(?:logs/|VerificationLedger\.json|completion-report\.json|evaluation\.json|summary\.md|Report\.html|Delivery\.md|Validation\.md)[^\s`'\"<>)]*"
)


def _extract_artifact_paths(value: object) -> list[str]:
    """Extract likely task artifact paths from nested evaluation/report fields."""
    paths: list[str] = []

    def add_path(raw: object) -> None:
        text = str(raw or "").strip().strip("`'\".,;)")
        if not text:
            return
        no_fragment = text.split("#", 1)[0]
        path_candidate = Path(no_fragment)
        if path_candidate.is_absolute() and any(no_fragment.lower().endswith(ext) for ext in [".json", ".jsonl", ".txt", ".log", ".md", ".png", ".jpg", ".jpeg", ".webp", ".html"]):
            if no_fragment not in paths:
                paths.append(no_fragment)
            return
        for match in _ARTIFACT_PATH_RE.findall(text):
            candidate = match.strip().strip("`'\".,;)")
            if candidate and candidate not in paths:
                paths.append(candidate)
        if re.search(r"\s", text):
            return
        if not paths or text.startswith(("/", ".", "logs/", "VerificationLedger", "Report.html", "summary.md", "evaluation.json")):
            if any(token in text for token in ["/", ".json", ".jsonl", ".txt", ".log", ".md", ".png", ".jpg", ".jpeg", ".webp", ".html"]):
                if text not in paths:
                    paths.append(text)

    if isinstance(value, dict):
        for key in ["path", "file", "evidence", "evidencePath", "artifact", "reportPath", "output"]:
            if key in value:
                for item in _extract_artifact_paths(value.get(key)):
                    if item not in paths:
                        paths.append(item)
        for key in ["evidencePaths", "artifacts", "outputs"]:
            if key in value:
                for item in _extract_artifact_paths(value.get(key)):
                    if item not in paths:
                        paths.append(item)
    elif isinstance(value, list):
        for item in value:
            for path in _extract_artifact_paths(item):
                if path not in paths:
                    paths.append(path)
    else:
        add_path(value)
    return paths


def _artifact_exists(task_dir: Path, path_value: object) -> bool:
    rel = _task_relative_path(task_dir, path_value)
    if not rel:
        return False
    raw = str(path_value or "")
    if raw.startswith(".automind/tasks/"):
        return Path(raw).exists() or (task_dir.parent.parent.parent / raw).exists()
    path = Path(rel)
    return (path if path.is_absolute() else task_dir / rel).exists()


def _critical_kind(path_value: str) -> str:
    rel = path_value.lower()
    name = Path(rel).name.lower()
    if name == "report.html":
        return "Human report"
    if name in {"verificationledger.json", "completion-report.json"}:
        return "Completion ledger"
    if "music-events" in name or "event" in name:
        return "Runtime proof"
    if "logcat" in name:
        return "Full runtime log"
    if "runtime-evidence" in name:
        return "Runtime evidence summary"
    if rel.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "Screenshot"
    if "build" in name:
        return "Build proof"
    if name == "summary.md":
        return "Final summary"
    return _artifact_kind(path_value).title()


def build_critical_artifacts(task_dir: Path, evaluation: dict | None = None, max_items: int = 10) -> list[dict]:
    """Return the most important artifacts to show in final handoff/report.

    Critical paths are derived from TC-level evidenceAssessment machine anchors
    and hardMetrics first, then from explicit TC evidence and final ledgers.
    """
    evaluation = evaluation or read_evaluation_json(task_dir) or _load_json(task_dir / "evaluation.json")
    test_results = evaluation.get("testResults") if isinstance(evaluation.get("testResults"), list) else []
    candidates: list[dict] = []

    def add(path_value: object, *, tc_id: str = "", anchor: str = "", source: str = "", title: str = "") -> None:
        for path in _extract_artifact_paths(path_value):
            rel = _task_relative_path(task_dir, path)
            if not rel:
                continue
            candidates.append({
                "path": rel,
                "tcIds": [tc_id] if tc_id else [],
                "anchor": anchor,
                "source": source,
                "title": title or _critical_kind(rel),
                "exists": _artifact_exists(task_dir, rel),
            })

    for row in test_results:
        if not isinstance(row, dict):
            continue
        tc_id = str(row.get("testCaseId") or row.get("id") or "").strip()
        assessment = row.get("evidenceAssessment") if isinstance(row.get("evidenceAssessment"), dict) else {}
        anchor_text = str(assessment.get("machineAnchor") or assessment.get("reason") or row.get("reason") or "").strip()
        for metric in assessment.get("hardMetrics") or []:
            if not isinstance(metric, dict):
                continue
            metric_anchor = str(metric.get("anchor") or metric.get("name") or anchor_text or "").strip()
            add(metric.get("evidence") or metric.get("evidencePath") or metric.get("artifact"), tc_id=tc_id, anchor=metric_anchor, source="hardMetric", title=_critical_kind(str(metric.get("evidence") or "")))
        add(assessment.get("machineAnchor"), tc_id=tc_id, anchor=anchor_text, source="machineAnchor")
        for ref in row.get("evidence") or []:
            add(ref, tc_id=tc_id, anchor=anchor_text, source="testResultEvidence")

    for ref in [
        task_dir / "Report.html",
        task_dir / "VerificationLedger.json",
        task_dir / "completion-report.json",
        task_dir / "summary.md",
    ]:
        if ref.exists():
            add(ref, title=_critical_kind(ref.name), source="finalArtifact")

    merged: dict[str, dict] = {}
    for item in candidates:
        rel = item["path"]
        current = merged.setdefault(rel, {**item, "tcIds": [], "anchors": [], "sources": []})
        for tc_id in item.get("tcIds") or []:
            if tc_id and tc_id not in current["tcIds"]:
                current["tcIds"].append(tc_id)
        if item.get("anchor") and item["anchor"] not in current["anchors"]:
            current["anchors"].append(item["anchor"])
        if item.get("source") and item["source"] not in current["sources"]:
            current["sources"].append(item["source"])
        current["exists"] = current.get("exists") or item.get("exists")

    def priority(item: dict) -> tuple[int, str]:
        path = str(item.get("path") or "").lower()
        title = str(item.get("title") or "").lower()
        if "runtime proof" in title or "music-events" in path or "event" in path:
            return (0, path)
        if "runtime-evidence" in path:
            return (1, path)
        if "logcat" in path:
            return (2, path)
        if "verificationledger" in path or "completion-report" in path:
            return (3, path)
        if "report.html" in path:
            return (4, path)
        if path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            return (5, path)
        return (6, path)

    return sorted(merged.values(), key=priority)[:max_items]


def _render_critical_artifacts(task_dir: Path, critical: list[dict]) -> str:
    if not critical:
        return "<p class='muted'>No critical artifact anchors were derived from evaluation.json.</p>"
    rows = []
    for item in critical:
        tc_text = ", ".join(item.get("tcIds") or []) or "-"
        anchors = "; ".join(item.get("anchors") or []) or "-"
        exists = "ready" if item.get("exists") else "missing"
        rows.append(
            f"<tr><td>{_html(item.get('title') or _critical_kind(item.get('path') or ''))}</td>"
            f"<td><span class='badge {'pass' if item.get('exists') else 'blocked'}'>{exists}</span></td>"
            f"<td>{tc_text}</td><td>{_link(task_dir, item.get('path'))}</td><td>{_html(anchors)}</td></tr>"
        )
    return "<table><thead><tr><th>Why review it</th><th>Status</th><th>TC</th><th>Path</th><th>Machine anchor / signal</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def _render_artifacts(task_dir: Path, artifacts: list[dict], max_items: int = 14) -> str:
    if not artifacts:
        return "<span class='muted'>No linked artifacts.</span>"
    items = []
    thumbs = []
    seen = set()
    for artifact in artifacts:
        rel = _task_relative_path(task_dir, artifact.get("path"))
        if not rel or rel in seen:
            continue
        seen.add(rel)
        kind = _artifact_kind(rel, artifact.get("type"))
        note = str(artifact.get("note") or Path(rel).name)
        if kind == "screenshot" and len(thumbs) < 3:
            thumbs.append(f"<a href='{_html(rel)}'><img class='thumb' src='{_html(rel)}' loading='lazy' alt='{_html(note)}'></a>")
        if len(items) < max_items:
            items.append(_short_artifact_link(task_dir, rel, note=note, kind=kind))
    if not items and not thumbs:
        return "<span class='muted'>No linked artifacts.</span>"
    more = ""
    if len(seen) > len(items):
        more = f"<div class='muted'>+ {len(seen) - len(items)} more artifact(s) in task logs.</div>"
    thumbs_html = f"<div class='thumbs'>{''.join(thumbs)}</div>" if thumbs else ""
    return thumbs_html + "<ul class='artifacts'>" + "".join(items) + "</ul>" + more


def _artifact_count(artifacts: list[dict]) -> int:
    seen = set()
    for artifact in artifacts:
        rel = str(artifact.get("path") or "").strip()
        if rel:
            seen.add(rel)
    return len(seen)


def _render_artifact_details(task_dir: Path, artifacts: list[dict]) -> str:
    count = _artifact_count(artifacts)
    if count == 0:
        return "<span class='muted'>No linked artifacts.</span>"
    return (
        f"<details class='artifact-details'><summary>All linked artifacts ({count})</summary>"
        f"{_render_artifacts(task_dir, artifacts, max_items=18)}</details>"
    )


def _key_artifact_score(artifact: dict) -> int:
    note = str(artifact.get("note") or "").lower()
    path = str(artifact.get("path") or "").lower()
    kind = _artifact_kind(path, artifact.get("type"))
    if kind == "screenshot":
        return 0
    if "critical artifact" in note:
        return 1
    if "music-events" in path or "runtime-evidence" in path:
        return 2
    if "logcat" in path:
        return 3
    if "verificationledger" in path:
        return 4
    return 9


def _render_key_evidence(task_dir: Path, item: dict, artifacts: list[dict], screenshot_note: str) -> str:
    """Render a concise, human-first summary for one TC row."""
    assessment = item.get("evidenceAssessment") if isinstance(item.get("evidenceAssessment"), dict) else {}
    signals: list[str] = []
    machine_anchor = str(assessment.get("machineAnchor") or "").strip()
    if machine_anchor:
        signals.append(machine_anchor)
    for metric in assessment.get("hardMetrics") or []:
        if not isinstance(metric, dict):
            continue
        if metric.get("passed") is False:
            continue
        name = str(metric.get("name") or "").strip()
        anchor = str(metric.get("anchor") or "").strip()
        text = ": ".join(part for part in [name, anchor] if part)
        if text and text not in signals:
            signals.append(text)

    key_artifacts: list[dict] = []
    seen = set()
    for artifact in sorted(artifacts, key=_key_artifact_score):
        rel = _task_relative_path(task_dir, artifact.get("path"))
        if not rel or rel in seen:
            continue
        if _key_artifact_score(artifact) > 4 and len(key_artifacts) >= 3:
            continue
        seen.add(rel)
        key_artifacts.append(artifact)
        if len(key_artifacts) >= 5:
            break

    parts: list[str] = []
    screenshots = [a for a in key_artifacts if _artifact_kind(a.get("path"), a.get("type")) == "screenshot"]
    if screenshots:
        parts.append("<div class='key-thumbs'>" + "".join(
            f"<a href='{_html(_task_relative_path(task_dir, shot.get('path')))}'><img class='thumb' src='{_html(_task_relative_path(task_dir, shot.get('path')))}' loading='lazy' alt='{_html(shot.get('note') or 'screenshot')}'></a>"
            for shot in screenshots[:2]
        ) + "</div>")
    if signals:
        parts.append("<ul class='key-signals'>" + "".join(f"<li>{_html(signal)}</li>" for signal in signals[:4]) + "</ul>")
    non_screenshot = [a for a in key_artifacts if _artifact_kind(a.get("path"), a.get("type")) != "screenshot"]
    if non_screenshot:
        parts.append("<ul class='artifacts key-files'>" + "".join(
            _short_artifact_link(task_dir, _task_relative_path(task_dir, a.get("path")), note=str(a.get("note") or Path(str(a.get("path") or "")).name), kind=_artifact_kind(a.get("path"), a.get("type")))
            for a in non_screenshot[:4]
        ) + "</ul>")
    if screenshot_note:
        parts.append(screenshot_note)
    return "".join(parts) or "<span class='muted'>No key evidence summary recorded.</span>"


def _collect_test_artifacts(task_dir: Path, evidence: list[dict], latest_files: list[Path]) -> list[dict]:
    artifacts: list[dict] = []
    for item in evidence:
        artifacts.append({
            "type": item.get("type") or _artifact_kind(item.get("path")),
            "note": item.get("note") or item.get("type") or "evidence",
            "path": item.get("path"),
            "tcIds": _extract_tc_ids(item),
        })
    for path in latest_files:
        rel = path.relative_to(task_dir).as_posix()
        kind = _artifact_kind(rel)
        if kind in {"log", "screenshot", "evidence"}:
            artifacts.append({"type": kind, "note": path.name, "path": rel, "tcIds": _extract_tc_ids(rel)})
    return artifacts


def _row_artifacts_from_result(task_dir: Path, item: dict) -> list[dict]:
    artifacts: list[dict] = []
    tc_ids = _extract_tc_ids(item)

    def add(path_value: object, note: str, explicit_type: str = "", critical: bool = False) -> None:
        for path in _extract_artifact_paths(path_value):
            rel = _task_relative_path(task_dir, path)
            if rel:
                label = note or Path(rel).name
                if critical:
                    label = f"Critical artifact: {_critical_kind(rel)}" + (f" ({label})" if label else "")
                artifacts.append({
                    "type": explicit_type or _artifact_kind(rel),
                    "note": label,
                    "path": rel,
                    "tcIds": tc_ids,
                })

    assessment = item.get("evidenceAssessment") if isinstance(item.get("evidenceAssessment"), dict) else {}
    add(assessment.get("machineAnchor"), "machineAnchor")
    for metric in assessment.get("hardMetrics") or []:
        if isinstance(metric, dict):
            metric_note = str(metric.get("name") or "hardMetric")
            metric_anchor = str(metric.get("anchor") or "").strip()
            if metric_anchor:
                metric_note = f"{metric_note}: {metric_anchor}"
            add(metric.get("evidence") or metric.get("evidencePath") or metric.get("artifact"), metric_note, critical=True)
    for ref in item.get("evidence") or []:
        add(ref, "TC evidence")
    ui = item.get("uiExploration") if isinstance(item.get("uiExploration"), dict) else {}
    for attempt in ui.get("attempts") or []:
        if not isinstance(attempt, dict):
            continue
        for ref in attempt.get("evidence") or []:
            add(ref, "UI action evidence")
    return artifacts


def _has_screenshot_artifact(artifacts: list[dict]) -> bool:
    return any(_artifact_kind(item.get("path"), item.get("type")) == "screenshot" for item in artifacts if isinstance(item, dict))


def _global_screenshot_artifacts(task_dir: Path, screenshots: list[Path]) -> list[dict]:
    artifacts: list[dict] = []
    for path in screenshots:
        try:
            rel = path.relative_to(task_dir).as_posix()
        except ValueError:
            continue
        artifacts.append({"type": "screenshot", "note": "Default TC screenshot", "path": rel, "tcIds": []})
    return artifacts


def _summary_deposition_rows(task_dir: Path, summary_md: str) -> str:
    phase_dir = task_dir / "logs" / "phase-learnings"
    phase_cards = sorted(phase_dir.glob("*.json")) if phase_dir.exists() else []
    rows = [
        ("Task summary", task_dir / "summary.md", "Final human/task summary for this task."),
        ("Lessons learned", SUMMARY_LESSONS_PATH, "Workspace-level lessons appended by summary generation."),
        ("Local reuse index", LOCAL_REUSE_INDEX_PATH, "Compact reusable run cards for future tasks."),
        ("Knowledge index", SUMMARY_DIR / "index.jsonl", "Durable knowledge records promoted from summaries/refiner actions."),
    ]
    html_rows = ""
    for title, path, meaning in rows:
        exists = path.exists()
        link_path = path
        try:
            link = _link(task_dir, link_path, path.name)
        except Exception:
            link = _html(path.as_posix())
        html_rows += f"<tr><td>{_html(title)}</td><td><span class='badge {'pass' if exists else 'blocked'}'>{'ready' if exists else 'missing'}</span></td><td>{link}</td><td>{_html(meaning)}</td></tr>"
    if phase_cards:
        links = "<ul class='artifacts'>" + "".join(_short_artifact_link(task_dir, card, note=card.name, kind="phase-learning") for card in phase_cards[:10]) + "</ul>"
        if len(phase_cards) > 10:
            links += f"<div class='muted'>+ {len(phase_cards) - 10} more phase-learning card(s).</div>"
    else:
        links = "<span class='muted'>No phase-learning cards found.</span>"
    html_rows += f"<tr><td>Phase hook learning cards</td><td><span class='badge {'pass' if phase_cards else 'blocked'}'>{len(phase_cards)}</span></td><td>{links}</td><td>After-phase hooks write task-local learning cards first; Phase 4 summary/refiner may promote valuable items into .automind/summary/raw and index.jsonl.</td></tr>"
    if summary_md.strip():
        html_rows += f"<tr><td>Summary excerpt</td><td colspan='3'><pre class='mini'>{_html(summary_md[:2000])}</pre></td></tr>"
    return html_rows


def _evidence_rows(evaluation: dict) -> list[dict]:
    rows: list[dict] = []
    for item in evaluation.get("evidence") or []:
        if isinstance(item, dict):
            rows.append({
                "type": item.get("type", "other"),
                "note": item.get("note") or item.get("name") or "-",
                "path": item.get("path") or item.get("file") or item.get("evidence") or "",
            })
        elif isinstance(item, str):
            rows.append({"type": "other", "note": "evidence", "path": item})
    return rows


def _evidence_index_rows(evaluation: dict) -> list[dict]:
    rows: list[dict] = []
    for item in evaluation.get("evidenceIndex") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        tc = item.get("tc")
        if isinstance(tc, list):
            tc_text = ", ".join(str(x) for x in tc if str(x).strip())
        else:
            tc_text = str(tc or "").strip()
        rows.append({
            "type": item.get("type") or "hint",
            "signal": item.get("signal") or "-",
            "tc": tc_text or "-",
            "path": path,
        })
    return rows


def generate_html_report_for_task_dir(task_dir: Path, task_code: str | None = None) -> Path:
    """Generate a human-readable HTML report for success/failure/pause handoff."""
    task_code = task_code or task_dir.name
    state = read_runtime_state(task_dir) or {}
    evaluation = read_evaluation_json(task_dir) or _load_json(task_dir / "evaluation.json")
    completion_report, _ = build_completion_report(task_dir, evaluation or {}, allow_synthesize_pass=False) if task_dir.exists() else ({}, [])
    requirements_md = _read_text(next((p for p in requirement_contract_paths(task_dir) if p.exists()), task_dir / "Requirements.md"))
    delivery_md = _read_text(task_dir / "Delivery.md")
    validation_md = _read_text(task_dir / "Validation.md")
    summary_md = _read_text(task_dir / "summary.md")
    requirements = _extract_requirements(requirements_md)
    latest_files = _collect_latest_files(task_dir)
    screenshots = [p for p in latest_files if _is_image(p)][:24]
    evidence = _evidence_rows(evaluation)
    evidence_index = _evidence_index_rows(evaluation)
    test_results = evaluation.get("testResults") if isinstance(evaluation.get("testResults"), list) else []
    failed_checks = evaluation.get("failedChecks") if isinstance(evaluation.get("failedChecks"), list) else []
    test_artifacts = _collect_test_artifacts(task_dir, evidence, latest_files)
    quality_checks = evaluation.get("qualityChecks") if isinstance(evaluation.get("qualityChecks"), list) else []
    status = state.get("status") or evaluation.get("result") or "unknown"
    result = evaluation.get("result") or "unknown"
    completion_result = completion_report.get("result") or "unknown"
    next_action = evaluation.get("nextAction") or state.get("nextAction") or "-"
    eval_iteration = evaluation.get("iteration") or "-"
    state_iteration = state.get("iteration") or "-"
    latest_evidence_iteration = _iter_number_from_dir(latest_iter_dir(task_dir))
    iteration_values = []
    for value in [eval_iteration, state_iteration, latest_evidence_iteration]:
        try:
            iteration_values.append(int(value))
        except Exception:
            pass
    display_iteration = max(iteration_values) if iteration_values else "-"
    iteration_note = ""
    try:
        if latest_evidence_iteration is not None and int(eval_iteration) < latest_evidence_iteration:
            iteration_note = f"Latest evidence is iter-{latest_evidence_iteration}; evaluation.json reports iter-{eval_iteration}."
    except Exception:
        pass
    completion_issues = completion_report.get("issues") if isinstance(completion_report.get("issues"), list) else []
    completion_warnings_raw = completion_report.get("warnings") if isinstance(completion_report.get("warnings"), list) else []
    # Report is for human confidence. Screenshot absence should be an internal
    # workflow confidence warning, not a visible negative callout in the final
    # report. When screenshots exist, Test Results already embeds thumbnails.
    completion_warnings = [
        w for w in completion_warnings_raw
        if "missing screenshot evidence" not in str(w).lower()
        and "no-screenshot" not in str(w).lower()
        and "noscreenshotreason" not in str(w).lower()
    ]
    generated_at = datetime.now().isoformat(timespec="seconds")
    iteration_total = _count_iterations(task_dir)
    tc_total, tc_passed = _testcase_pass_counts(test_results)
    duration = _active_duration(task_dir)
    global_screenshots = _global_screenshot_artifacts(task_dir, screenshots)
    runtime_ui_tc_ids = {
        str(tc.get("id") or "").upper()
        for tc in completion_report.get("testCases", [])
        if isinstance(tc, dict) and _normalize_runtime_level(tc.get("runtimeLevel")) in {"runtime", "device"}
    }

    def pre_block(title: str, body: str) -> str:
        if not body.strip():
            return ""
        return f"<section><h2>{_html(title)}</h2><pre>{_html(body)}</pre></section>"

    req_html = "".join(
        "<li><strong>" + _html(row["requirement"]) + "</strong>" +
        ("<ul>" + "".join(f"<li>{_html(ac)}</li>" for ac in row["acceptance"]) + "</ul>" if row["acceptance"] else "") +
        "</li>"
        for row in requirements
    ) or "<li>No parsed requirements/AC. See Requirements.md.</li>"

    if test_results:
        result_items = [item for item in test_results if isinstance(item, dict)]
    else:
        result_items = [{
            "testCaseId": "Overall Verification",
            "result": result,
            "reason": evaluation.get("summary") or "No explicit testResults[] recorded; grouped evidence is shown at overall verification level.",
            "acceptanceCriteria": [],
        }]

    explicit_tc_artifacts = any(artifact.get("tcIds") for artifact in test_artifacts)
    test_rows_parts = []
    for item in result_items:
        tc_id = str(item.get("testCaseId") or item.get("id") or "Overall Verification")
        item_tc_ids = _extract_tc_ids(item) or ([tc_id.upper()] if tc_id.upper().startswith("TC-") else [])
        row_specific_artifacts = _row_artifacts_from_result(task_dir, item)
        if item_tc_ids and explicit_tc_artifacts:
            row_artifacts = [artifact for artifact in test_artifacts if set(artifact.get("tcIds") or []) & set(item_tc_ids)]
        elif not test_results or len(result_items) == 1 or not explicit_tc_artifacts:
            row_artifacts = test_artifacts
        else:
            row_artifacts = []
        row_artifacts = [*row_specific_artifacts, *row_artifacts]
        screenshot_expected = tc_id.upper() in runtime_ui_tc_ids
        if screenshot_expected and not _has_screenshot_artifact(row_artifacts) and global_screenshots:
            row_artifacts = [*global_screenshots[:3], *row_artifacts]
        # Do not show a negative per-TC missing-screenshot callout in Report.
        # If screenshots exist, they are displayed as thumbnails; if they do
        # not, completion-check may still carry an internal confidence warning.
        screenshot_note = ""
        ac = item.get("acceptanceCriteria") or item.get("acceptance") or []
        if isinstance(ac, str):
            ac = [ac]
        key_evidence = _render_key_evidence(task_dir, item, row_artifacts, screenshot_note)
        test_rows_parts.append(
            f"<tr><td><strong>{_html(tc_id)}</strong></td>"
            f"<td><span class='badge {_html(str(item.get('result','unknown')).lower())}'>{_html(item.get('result','unknown'))}</span></td>"
            f"<td>{_html(item.get('reason') or item.get('summary') or '')}</td>"
            f"<td>{_html(', '.join(str(x) for x in ac) or '-')}</td>"
            f"<td>{key_evidence}</td>"
            f"<td>{_render_artifact_details(task_dir, row_artifacts)}</td></tr>"
        )
    test_rows = "".join(test_rows_parts)

    failed_rows = "".join(
        f"<tr><td>{_html(item.get('name') or '-')}</td><td>{_html(item.get('category') or '-')}</td><td>{_html(item.get('reason') or '')}</td><td>{_link(task_dir, item.get('evidence'))}</td></tr>"
        for item in failed_checks if isinstance(item, dict)
    ) or "<tr><td colspan='4'>No failed checks.</td></tr>"

    quality_rows = "".join(
        f"<tr><td>{_html(item.get('name') or item.get('id') or '-')}</td><td>{_html(item.get('result') or item.get('status') or '-')}</td><td>{_html(item.get('category') or '-')}</td><td>{_html(item.get('reason') or '')}</td></tr>"
        for item in quality_checks if isinstance(item, dict)
    ) or "<tr><td colspan='4'>No qualityChecks[] recorded.</td></tr>"

    evidence_html = "".join(
        f"<tr><td>{_html(row['type'])}</td><td>{_html(row['note'])}</td><td>{_link(task_dir, row['path'])}</td></tr>"
        for row in evidence
    ) or "<tr><td colspan='3'>No evaluation.evidence[] recorded.</td></tr>"

    evidence_index_html = "".join(
        f"<tr><td>{_html(row['type'])}</td><td>{_html(row['signal'])}</td><td>{_html(row['tc'])}</td><td>{_link(task_dir, row['path'])}</td></tr>"
        for row in evidence_index
    ) or "<tr><td colspan='4'>No evaluation.evidenceIndex[] recorded.</td></tr>"

    latest_html = "".join(
        f"<tr><td>{_link(task_dir, p.relative_to(task_dir).as_posix())}</td><td>{_html(p.suffix or '-')}</td><td>{p.stat().st_size}</td></tr>"
        for p in latest_files[:50]
    ) or "<tr><td colspan='3'>No logs/iter-* files found.</td></tr>"

    screenshots_html = "".join(
        f"<figure><a href='{_html(p.relative_to(task_dir).as_posix())}'><img src='{_html(p.relative_to(task_dir).as_posix())}' loading='lazy'></a><figcaption>{_html(p.relative_to(task_dir).as_posix())}</figcaption></figure>"
        for p in screenshots
    ) or "<p>No screenshot/image artifacts found under logs/.</p>"

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_html(task_code)} Automind Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; color: #172033; background: #f6f7fb; }}
    header {{ background: #121826; color: white; padding: 28px 36px; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px; }}
    section {{ background: white; border: 1px solid #e3e7ef; border-radius: 12px; padding: 18px 20px; margin: 16px 0; box-shadow: 0 1px 2px rgba(0,0,0,.03); }}
    h1, h2 {{ margin-top: 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
    .card {{ background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; }}
    .label {{ color: #667085; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    .value {{ font-size: 18px; font-weight: 650; margin-top: 4px; word-break: break-word; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid #edf0f5; padding: 9px 8px; text-align: left; vertical-align: top; }}
    th {{ color: #475467; background: #f8fafc; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #0b1020; color: #d8e1ff; padding: 14px; border-radius: 10px; max-height: 520px; overflow: auto; }}
    a {{ color: #2563eb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; background: #eef2ff; font-weight: 650; }}
    .pass, .finished {{ background: #dcfce7; color: #166534; }} .fail, .failed {{ background: #fee2e2; color: #991b1b; }} .blocked, .ask_user {{ background: #fef3c7; color: #92400e; }}
    .screens {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; }}
    figure {{ margin: 0; border: 1px solid #e5e7eb; border-radius: 10px; padding: 8px; background: #fafafa; }}
    figure img {{ width: 100%; max-height: 260px; object-fit: contain; background: #111827; border-radius: 6px; }}
    figcaption {{ font-size: 12px; color: #667085; margin-top: 6px; word-break: break-all; }}
    .muted {{ color: #667085; font-size: 12px; }}
    .warn {{ color: #92400e; margin-top: 6px; }}
    .artifacts {{ margin: 0; padding-left: 18px; }}
    .artifact-details summary {{ cursor: pointer; color: #2563eb; font-weight: 650; }}
    .key-signals {{ margin: 0 0 8px; padding-left: 18px; }}
    .key-files {{ margin-top: 8px; }}
    .key-thumbs {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }}
    .thumbs {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }}
    .thumb {{ width: 104px; max-height: 96px; object-fit: contain; background: #111827; border-radius: 6px; border: 1px solid #d0d5dd; }}
    .mini {{ max-height: 220px; font-size: 12px; }}
  </style>
</head>
<body>
<header>
  <h1>{_html(task_code)} Automind Report</h1>
  <div>Generated: {_html(generated_at)}</div>
</header>
<main>
  <section>
    <h2>Overview</h2>
    <div class="grid">
      <div class="card"><div class="label">Task status</div><div class="value"><span class="badge {_html(str(status).lower())}">{_html(status)}</span></div></div>
      <div class="card"><div class="label">Evaluation result</div><div class="value"><span class="badge {_html(str(result).lower())}">{_html(result)}</span></div></div>
      <div class="card"><div class="label">Iterations</div><div class="value">{_html(iteration_total if iteration_total is not None else '-')}</div></div>
      <div class="card"><div class="label">TestCases passed</div><div class="value">{_html(tc_passed)} / {_html(tc_total)}</div></div>
      <div class="card"><div class="label">Active duration</div><div class="value">{_html(duration['activeText'] if duration else '-')}</div>{('<div class="muted">total ' + _html(duration['totalText']) + ' · waiting ' + _html(duration['waitingText']) + '</div>') if duration else ''}</div>
      <div class="card"><div class="label">Completion gate result</div><div class="value"><span class="badge {_html(str(completion_result).lower())}">{_html(completion_result)}</span></div></div>
    </div>
    {('<p class="warn"><strong>Freshness:</strong> ' + _html(iteration_note) + '</p>') if iteration_note else ''}
    {('<p class="warn"><strong>Completion issues:</strong> ' + _html('; '.join(str(x) for x in completion_issues[:5])) + '</p>') if completion_issues else ''}
    {('<p class="muted"><strong>Completion warnings:</strong> ' + _html('; '.join(str(x) for x in completion_warnings[:5])) + '</p>') if completion_warnings else ''}
    <p><strong>Summary:</strong> {_html(evaluation.get('summary') or state.get('summary') or '-')}</p>
  </section>

  <section><h2>Completed / Target Requirements</h2><ul>{req_html}</ul><p>{_link(task_dir, 'Requirements.md', 'Open Requirements.md')}</p></section>

  <section><h2>Generated / Changed Artifacts</h2><ul>
    <li>{_link(task_dir, 'Delivery.md', 'Delivery.md')} — generated/changed implementation report</li>
    <li>{_link(task_dir, 'Validation.md', 'Validation.md')} — verification report</li>
    <li>{_link(task_dir, 'evaluation.json', 'evaluation.json')} — machine-readable result</li>
    <li>{_link(task_dir, 'VerificationLedger.json', 'VerificationLedger.json')} — completion coverage ledger</li>
    <li>{_link(task_dir, 'summary.md', 'summary.md')} — final summary / reusable findings</li>
  </ul></section>

  <section><h2>Test Results</h2><p class="muted">Each row shows a concise Key Evidence summary first: screenshot thumbnails, machine anchors, hardMetric signals, and the few files users should inspect. The final column keeps the full artifact list for traceability.</p><p>{_link(task_dir, 'TestCases.md', 'Open TestCases.md')}</p><table><thead><tr><th>TestCase</th><th>Result</th><th>Reason</th><th>AC</th><th>Key Evidence</th><th>Evidence / Screenshots / Logs</th></tr></thead><tbody>{test_rows}</tbody></table></section>
  <section><h2>Screenshots</h2><p class="muted">Runtime/UI verification should capture screenshots by default for each executed TC. This gallery embeds every screenshot/image artifact found under logs/.</p><div class="screens">{screenshots_html}</div></section>
  <section><h2>Failed Checks / Blockers</h2><table><thead><tr><th>Name</th><th>Category</th><th>Reason</th><th>Evidence</th></tr></thead><tbody>{failed_rows}</tbody></table></section>
  <section><h2>Quality Checks</h2><table><thead><tr><th>Name</th><th>Result</th><th>Category</th><th>Reason</th></tr></thead><tbody>{quality_rows}</tbody></table></section>
  <section><h2>Evaluation Evidence</h2><table><thead><tr><th>Type</th><th>Note</th><th>Path</th></tr></thead><tbody>{evidence_html}</tbody></table></section>
  <section><h2>Evidence Lookup</h2><p class="muted">Quick TC/signal-to-artifact map from evaluation.evidenceIndex[]. Use Test Results first; use this lookup when you need to trace a specific signal to its source file.</p><table><thead><tr><th>Type</th><th>Signal</th><th>TC</th><th>Path</th></tr></thead><tbody>{evidence_index_html}</tbody></table><details class="artifact-details"><summary>All Artifacts Appendix</summary><p class="muted">Complete raw file list for navigation/debugging only. Primary screenshots/evidence/logs are grouped in Test Results above.</p><table><thead><tr><th>Path</th><th>Type</th><th>Bytes</th></tr></thead><tbody>{latest_html}</tbody></table></details></section>
  <section><h2>Summary / Knowledge Deposition</h2><p class="muted">This summarizes what was preserved for future reuse: task summary, lessons, local reuse index, durable knowledge index, and phase hook learning cards.</p><table><thead><tr><th>Item</th><th>Status</th><th>Path / Excerpt</th><th>Meaning</th></tr></thead><tbody>{_summary_deposition_rows(task_dir, summary_md)}</tbody></table></section>
  {pre_block('Delivery.md excerpt', delivery_md)}
  {pre_block('Validation.md excerpt', validation_md)}
</main>
</body>
</html>
"""
    out = task_dir / "Report.html"
    out.write_text(html_doc, encoding="utf-8")
    return out


def generate_html_report(task_code: str) -> Path:
    return generate_html_report_for_task_dir(get_task_dir(task_code), task_code)

def build_report_manifest(task_code: str) -> list[dict]:
    """Return user-facing reports that should be surfaced at handoff points."""
    task_dir = get_task_dir(task_code)

    def item(kind: str, title: str, filename: str, purpose: str, required: bool = True) -> dict:
        path = task_dir / filename
        return {
            "kind": kind,
            "title": title,
            "path": rel_to_root(path),
            "exists": path.exists(),
            "purpose": purpose,
            "required": required,
        }

    state_filename = "runtime-state.json"
    state_title = "Runtime state"
    state_purpose = "Current status, owner, next action, resume/recovery metadata."

    reports = [
        item("development", "Development report", "Delivery.md", "What Generator changed, why, mapped TC targets, self-tests, risks.", required=False),
        item("validation", "Validation report", "Validation.md", "Human-readable verification history, commands, evidence, result, reusable findings."),
        item("evaluation", "Machine-readable evaluator result", "evaluation.json", "Loop control signal: result, failedChecks, evidence, testResults, nextAction."),
        item("completion", "Completion / acceptance ledger", "VerificationLedger.json", "Final TC/AC/evidence coverage produced by completion-check.", required=False),
        item("summary", "Final task summary", "summary.md", "Final outcome, evidence, failure/repair path, reusable successful/avoid paths.", required=False),
        item("state", state_title, state_filename, state_purpose),
        item("html", "Human-readable HTML report", "Report.html", "Open this first: one-page task report with per-TC screenshots, key evidence/logs, requirements, generated artifacts, and checks.", required=False),
    ]
    latest = latest_iter_dir(task_dir)
    if latest:
        reports.append({
            "kind": "evidence",
            "title": "Latest evidence/log directory",
            "path": rel_to_root(latest),
            "exists": latest.exists(),
            "purpose": "Raw evidence logs for the latest iteration.",
            "required": False,
        })
    return reports


def print_report_manifest(task_code: str, heading: str = "Reports to inspect / share"):
    """Print user-facing report paths at completion, pause, failure, and status."""
    try:
        generate_html_report(task_code)
    except Exception:
        # Reporting must never block the harness loop. Missing/partial task files
        # are common during failure handoff; manifest still shows raw artifacts.
        pass
    reports = build_report_manifest(task_code)
    visible = [report for report in reports if report.get("exists") or report.get("required")]
    if not visible:
        return
    from orchestrator.tui.style import CYAN, GREEN, YELLOW, style

    def _marker(exists: bool) -> str:
        return (
            style("ready", GREEN, bold=True)
            if exists
            else style("missing", YELLOW, bold=True)
        )

    print("")
    print(style(f"━━ {heading} ━━", CYAN, bold=True))
    for report in visible:
        print(f"- [{_marker(bool(report.get('exists')))}] {style(report.get('title'), bold=True)}: {report.get('path')} — {report.get('purpose')}")
    task_dir = get_task_dir(task_code)
    critical = build_critical_artifacts(task_dir, max_items=6)
    if critical:
        print("")
        print(style("Suggested key files to review:", CYAN, bold=True))
        for item in critical:
            tc_text = ", ".join(item.get("tcIds") or [])
            anchor = "; ".join(item.get("anchors") or [])
            suffix = f" — {tc_text}" if tc_text else ""
            if anchor:
                suffix += f" — {anchor}"
            print(f"- [{_marker(bool(item.get('exists')))}] {style(item.get('title'), bold=True)}: {rel_to_root(task_dir / item.get('path'))}{suffix}")


def build_status_guidance(task_code: str) -> dict:
    """Build self-explanatory next-step guidance for `status` output.

    The guidance is intentionally action-oriented so slash-command/current-session
    agents can keep the end-to-end loop moving instead of stopping at status.
    """
    task_dir = get_task_dir(task_code)
    phase_transition = refresh_phase_transition_summary(task_dir) if task_dir.exists() else {}
    state = read_runtime_state(task_dir) or {}
    evaluation = read_evaluation_json(task_dir)
    workflow_ok, workflow_report = check_workflow_consistency(task_code) if task_dir.exists() else (False, {"issues": ["task missing"], "warnings": []})
    completion_report = None
    if evaluation is not None or (task_dir / "TestCases.md").exists():
        completion_report, _ = build_completion_report(task_dir, evaluation or {}, allow_synthesize_pass=False)
    checklist_summary = summarize_plan_checklists(task_dir)
    checklist_summary = merge_verification_status_from_completion(task_dir, checklist_summary, completion_report)
    summary_reuse = build_summary_reuse_status(task_dir)

    status = state.get("status", "unknown")
    next_action = state.get("nextAction", "-")
    owner = state.get("currentOwner", "-")
    iteration = int(state.get("iteration", 0) or 0)
    latest_iter = latest_iter_dir(task_dir)
    latest_iter_rel = rel_to_root(latest_iter) if latest_iter else ""

    recommended: list[str] = []
    commands: list[str] = []
    read_files: list[str] = []
    reason = ""

    if status == "finished":
        if completion_report and completion_report.get("result") != "pass":
            reason = "Task is marked finished but completion-check is not passing; repair false finish."
            recommended.append("Inspect VerificationLedger/evaluation coverage and continue the harness loop.")
            commands.append(f"./automind.sh completion-check {task_code}")
            commands.append(f"./automind.sh resume {task_code} <agent>")
            read_files.extend([rel_to_root(task_dir / "VerificationLedger.json"), rel_to_root(task_dir / "evaluation.json")])
        else:
            if summary_reuse.get("ok"):
                reason = "Task is finished, completion coverage is satisfied, and summary/reuse memory exists."
                recommended.append("Inspect record completeness before final handoff.")
            else:
                reason = "Task is finished and completion coverage is satisfied, but summary/reuse memory is not generated yet."
                recommended.append("Run summary now so successful paths, avoid paths, and lessons seed the next task's Reuse.md before final handoff.")
                for missing_path in summary_reuse.get("missing", []):
                    recommended.append(f"Missing summary/reuse artifact: {missing_path}")
            commands.append(f"./automind.sh summary {task_code}")
            commands.append(f"./automind.sh record-check {task_code}")
            read_files.extend([rel_to_root(task_dir / "summary.md"), rel_to_root(task_dir / "Validation.md")])
    elif status == "human_input_pending" or next_action == "ask_user":
        reason = "Human input is required before AutoMind can safely continue."
        recommended.append("Read askUserQuestion, ask the user for the missing decision, then update task artifacts/evaluation and resume.")
        ask = state.get("askUserQuestion")
        if not isinstance(ask, dict) and isinstance(evaluation, dict):
            ask = evaluation.get("askUserQuestion")
        if isinstance(ask, dict):
            question = str(ask.get("question", "")).strip()
            recommended_text = str(ask.get("recommended", "")).strip()
            if question:
                recommended.append("Question to ask user: " + question)
            if recommended_text:
                recommended.append("Recommended option: " + recommended_text)
        read_files.extend([rel_to_root(task_dir / "evaluation.json"), rel_to_root(task_dir / "Brainstorm.md"), *_runtime_state_read_files(task_dir)])
    elif status == "replan_pending" or next_action == "replan":
        reason = "The task requires replanning before more code changes."
        recommended.append("Run/continue Phase 2 Refiner, fix workflow-check issues, then resume Generator.")
        commands.append(f"./automind.sh plan {task_code} <agent>")
        commands.append(f"./automind.sh workflow-check {task_code}")
        commands.append(f"./automind.sh resume {task_code} <agent>")
        read_files.extend([rel_to_root(task_dir / "Brainstorm.md"), *(rel_to_root(path) for path in requirement_contract_paths(task_dir)), rel_to_root(task_dir / "TestCases.md"), rel_to_root(task_dir / "Plan.md")])
    elif status in {"created", "planned", "ready"}:
        if next_action == "run_evaluator" or owner == "evaluator":
            reason = "Task is planned and evaluator-ready; run the selected verifier before making code changes."
            recommended.append("Execute deterministic verification when available; then inspect evaluation.json and completion-check.")
            if state.get("taskType") == "script" or (state.get("harnessProfile") or {}).get("name") == "script-command":
                commands.append(f"./automind.sh script-command {task_code} {iteration + 1}")
            else:
                commands.append(f"./automind.sh context-pack {task_code} {iteration + 1}")
            commands.append(f"./automind.sh completion-check {task_code}")
            read_files.extend([rel_to_root(task_dir / "Plan.md"), rel_to_root(task_dir / "TestCases.md"), rel_to_root(task_dir / "Validation.md")])
        elif not workflow_ok:
            reason = "Planning artifacts are not yet coherent enough for implementation."
            recommended.append("Refine Phase 2 artifacts until workflow-check passes, then start/resume the loop.")
            commands.append(f"./automind.sh workflow-check {task_code}")
            commands.append(f"./automind.sh plan {task_code} <agent>")
        else:
            reason = "Task is planned/ready; continue into Generator and verification."
            recommended.append("Resume the end-to-end harness loop or, in current-session mode, implement against Plan/TestCases and verify.")
            commands.append(f"./automind.sh resume {task_code} <agent>")
        read_files.extend([rel_to_root(task_dir / "Plan.md"), rel_to_root(task_dir / "TestCases.md"), rel_to_root(task_dir / "Reuse.md")])
    elif status in {"generating", "retry_pending"} or next_action == "retry_generator":
        reason = "Generator should repair or continue implementation based on latest validation evidence."
        recommended.append("Read latest evaluation/Validation/log evidence, update code and Delivery.md, then run the selected verifier again.")
        read_files.extend([rel_to_root(task_dir / "evaluation.json"), rel_to_root(task_dir / "Validation.md"), rel_to_root(task_dir / "Delivery.md")])
        if latest_iter_rel:
            read_files.append(latest_iter_rel)
        commands.append(f"./automind.sh resume {task_code} <agent>")
        if state.get("taskType") == "script" or (state.get("harnessProfile") or {}).get("name") == "script-command":
            commands.append(f"./automind.sh script-command {task_code} {iteration + 1}")
        commands.append(f"./automind.sh completion-check {task_code}")
        if evaluation:
            failed_blob = json.dumps(evaluation.get("failedChecks", []), ensure_ascii=False).lower()
            if any(token in failed_blob for token in ["build_failure", "compile", "xcodebuild", "gradle", "build failed", "编译", "构建"]):
                recommended.append("Build verification failed: classify product vs environment vs unrelated workspace/harness. If unrelated and safe, checkpoint, make minimal verification unblock changes, rerun, then restore or promote them.")
                commands.append(f"./automind.sh checkpoint create {task_code} 'before verification unblock changes'")
    elif status == "evaluating" or next_action == "run_evaluator":
        reason = "Evaluator/verification is the next required step."
        recommended.append("Run deterministic verifier if available; otherwise create evaluator context and launch an isolated evaluator.")
        commands.append(f"./automind.sh context-pack {task_code} {max(iteration, 0) + 1}")
        if state.get("taskType") == "script" or (state.get("harnessProfile") or {}).get("name") == "script-command":
            commands.append(f"./automind.sh script-command {task_code} {max(iteration, 0) + 1}")
        commands.append(f"./automind.sh completion-check {task_code}")
        read_files.extend([rel_to_root(task_dir / "Delivery.md"), rel_to_root(task_dir / "TestCases.md"), rel_to_root(task_dir / "Plan.md")])
    elif status == "failed":
        recovery_entry = build_resume_recovery_entry(task_dir, state, evaluation)
        if recovery_entry.get("recoverable"):
            reason = "Task is failed because the agent/runtime was unavailable; it can be stage-resumed after the external issue is fixed."
            recommended.append(recovery_entry.get("reason", "Recoverable external interruption detected."))
            recommended.append("Resume uses runtime-state/evaluation/log artifacts; it does not depend on hidden chat memory.")
            commands.append(f"./automind.sh resume {task_code} <agent>")
        else:
            reason = "Task reached a failed terminal state or max iterations."
            recommended.append("Inspect failure evidence; decide whether to replan, ask the user, or start a new task.")
            commands.append(f"./automind.sh summary {task_code}")
            commands.append(f"./automind.sh record-check {task_code}")
        read_files.extend([rel_to_root(task_dir / "evaluation.json"), rel_to_root(task_dir / "Validation.md"), rel_to_root(task_dir / "summary.md")])
    elif status == "aborted":
        reason = "Task is aborted; AutoMind should not resume without explicit user direction."
        recommended.append("Ask the user whether to restart as a new task or explicitly resume from a checkpoint.")
        read_files.extend(_runtime_state_read_files(task_dir))
    else:
        reason = "Status is unknown; inspect task files and workflow consistency."
        recommended.append("Run workflow-check and inspect runtime-state/evaluation before taking action.")
        commands.append(f"./automind.sh workflow-check {task_code}")
        read_files.extend([*_runtime_state_read_files(task_dir), rel_to_root(task_dir / "evaluation.json")])

    # Add coverage-aware guidance when completion is known to fail.
    if checklist_summary.get("implementationRows") or checklist_summary.get("verificationRows"):
        impl_counts = checklist_summary.get("implementation", {})
        ver_counts = checklist_summary.get("verification", {})
        recommended.append(
            "Plan checklist: implementation "
            + (", ".join(f"{key}={value}" for key, value in sorted(impl_counts.items())) or "empty")
            + "; verification "
            + (", ".join(f"{key}={value}" for key, value in sorted(ver_counts.items())) or "empty")
        )
        if rel_to_root(task_dir / "Plan.md") not in read_files:
            read_files.append(rel_to_root(task_dir / "Plan.md"))

    if completion_report and completion_report.get("result") == "fail":
        completion_issues = completion_report.get("issues", [])
        open_ac = completion_report.get("coverage", {}).get("acceptanceCriteriaOpen", [])
        not_run = completion_report.get("coverage", {}).get("requiredTestCasesNotRun", [])
        failed = completion_report.get("coverage", {}).get("requiredTestCasesFailed", [])
        skipped = completion_report.get("coverage", {}).get("requiredTestCasesSkipped", [])
        missing = [issue for issue in completion_issues if "evidence" in issue]
        if open_ac or not_run or failed or skipped or missing:
            recommended.append("Completion is not proven yet; close required TC/AC/evidence gaps before claiming finish.")
            if open_ac:
                recommended.append("Open AC: " + ", ".join(open_ac[:8]))
            if failed:
                recommended.append("Failed required TC: " + ", ".join(failed[:8]))
            if skipped:
                recommended.append("Skipped required TC: " + ", ".join(skipped[:8]))
            if not_run:
                recommended.append("Not-run required TC: " + ", ".join(not_run[:8]))
            if missing:
                recommended.append("Missing evidence: " + "; ".join(missing[:3]))
            if f"./automind.sh completion-check {task_code}" not in commands:
                commands.append(f"./automind.sh completion-check {task_code}")
            read_files.append(rel_to_root(task_dir / "VerificationLedger.json"))
        if any("verification unblock" in issue or "verificationUnblockChanges" in issue for issue in completion_issues):
            recommended.append("Temporary verification unblock changes must be recorded in evaluation.json.verificationUnblockChanges and restored or promoted before finish.")
            commands.append(f"./automind.sh checkpoint list {task_code}")
            if rel_to_root(task_dir / "VerificationLedger.json") not in read_files:
                read_files.append(rel_to_root(task_dir / "VerificationLedger.json"))

    if workflow_report.get("issues"):
        recommended.append("Workflow issues: " + "; ".join(workflow_report.get("issues", [])[:5]))
    elif workflow_report.get("warnings"):
        recommended.append("Workflow warnings: " + "; ".join(workflow_report.get("warnings", [])[:3]))

    # De-duplicate while preserving order.
    def dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if not item or item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    return {
        "reason": reason,
        "recommended": dedupe(recommended),
        "commands": dedupe(commands),
        "readFiles": dedupe(read_files),
        "workflowCheck": {
            "result": workflow_report.get("result", "pass" if workflow_ok else "fail"),
            "issueCount": len(workflow_report.get("issues", [])),
            "warningCount": len(workflow_report.get("warnings", [])),
        },
        "workflowControlState": read_workflow_state(task_dir),
        "phaseSummary": phase_transition,
        "stateSummary": phase_transition,
        "workflowState": workflow_report.get("workflowState", {}),
        "completionCheck": {
            "result": completion_report.get("result") if completion_report else "not_run",
            "issueCount": len(completion_report.get("issues", [])) if completion_report else 0,
        },
        "summaryReuse": summary_reuse,
        "planChecklist": {
            "implementation": checklist_summary.get("implementation", {}),
            "verification": checklist_summary.get("verification", {}),
        },
    }
