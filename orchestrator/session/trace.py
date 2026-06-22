"""Formal trace helpers for AutoMind runs.

The durable source remains task-local artifacts (`events.jsonl`, workflow.json,
logs).  This module projects them into an OpenTelemetry-friendly trace shape so
TUI, CLI evals, metrics, and summary/reuse can share one vocabulary.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.session.events import read_events
from orchestrator.phase_transition import build_phase_transition_summary
from orchestrator.state import read_runtime_state
from orchestrator.workflow_state import read_workflow_state

TRACE_SCHEMA_VERSION = 1
MAX_TRACE_STRING_CHARS = 2_000
MAX_TRACE_LIST_ITEMS = 50
MAX_TRACE_DICT_ITEMS = 80
MAX_TRACE_JSON_SPANS = 100
MAX_TRACE_JSON_BYTES = 2_000_000


def _safe_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(errors="ignore"))
    except json.JSONDecodeError:
        return None



def _compact_value(value: Any, *, depth: int = 0) -> Any:
    """Keep trace fields diagnostic without embedding huge payloads/logs."""
    if depth > 4:
        return "[truncated: max depth]"
    if isinstance(value, str):
        if len(value) <= MAX_TRACE_STRING_CHARS:
            return value
        return value[:MAX_TRACE_STRING_CHARS].rstrip() + f"... [truncated {len(value) - MAX_TRACE_STRING_CHARS} chars]"
    if isinstance(value, list):
        items = [_compact_value(item, depth=depth + 1) for item in value[:MAX_TRACE_LIST_ITEMS]]
        if len(value) > MAX_TRACE_LIST_ITEMS:
            items.append({"truncatedItems": len(value) - MAX_TRACE_LIST_ITEMS})
        return items
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= MAX_TRACE_DICT_ITEMS:
                out["truncatedKeys"] = len(value) - MAX_TRACE_DICT_ITEMS
                break
            out[str(key)] = _compact_value(item, depth=depth + 1)
        return out
    return value


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _event_span(event: dict[str, Any], index: int, task_code: str) -> dict[str, Any]:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    compact_data = _compact_value(data) if data else {}
    phase = event.get("phase") or data.get("phase") or "task"
    span_id = event.get("spanId") or data.get("spanId") or f"event-{index:04d}"
    parent_span_id = event.get("parentSpanId") or data.get("parentSpanId") or (f"phase:{phase}" if phase != "task" else "task")
    status = event.get("status") or data.get("status")
    if not status:
        status = "error" if event.get("level") == "error" or str(event.get("type", "")).endswith("failed") else "ok"
    return {
        "spanId": span_id,
        "parentSpanId": parent_span_id,
        "name": event.get("type") or "event",
        "phase": phase,
        "actor": event.get("source") or "automind",
        "action": event.get("action") or data.get("action") or event.get("type") or "event",
        "status": status,
        "startTime": event.get("ts"),
        "endTime": event.get("ts"),
        "durationMs": event.get("durationMs") or data.get("durationMs"),
        "message": _compact_value(event.get("message", "")),
        "artifactRefs": event.get("artifactRefs") or data.get("artifactRefs") or [],
        "evidenceRefs": event.get("evidenceRefs") or data.get("evidenceRefs") or [],
        "attributes": {
            "taskCode": task_code,
            "level": event.get("level", "info"),
            "iteration": event.get("iteration"),
            "replaceKey": event.get("replaceKey"),
            **({"eventData": compact_data} if compact_data else {}),
        },
    }


def build_trace(task_code: str, task_dir: Path) -> dict[str, Any]:
    task_state = read_runtime_state(task_dir) or {}
    workflow = _safe_json(task_dir / "workflow.json") or {}
    workflow_control_state = read_workflow_state(task_dir)
    phase_summary = build_phase_transition_summary(task_dir)
    events = read_events(task_dir)
    trace_id = str(task_state.get("traceId") or task_code)
    run_id = str(task_state.get("runId") or "run-001")

    spans: list[dict[str, Any]] = [
        {
            "spanId": "task",
            "parentSpanId": None,
            "name": "AutoMind task",
            "phase": "task",
            "actor": "automind",
            "action": "orchestrate_task",
            "status": workflow_control_state.get("status") or task_state.get("status") or phase_summary.get("currentStatus") or "unknown",
            "startTime": task_state.get("createdAt") or (events[0].get("ts") if events else None),
            "endTime": task_state.get("updatedAt") or (events[-1].get("ts") if events else None),
            "durationMs": None,
            "message": task_state.get("userInput") or task_code,
            "artifactRefs": ["automind-workflow-state.json", "stages/*-stage-state.json", "workflow.json"],
            "evidenceRefs": [],
            "attributes": {
                "taskCode": task_code,
                "currentOwner": workflow_control_state.get("currentOwner") or task_state.get("currentOwner"),
                "nextAction": workflow_control_state.get("nextAction") or task_state.get("nextAction"),
                "workflowCurrentStage": workflow_control_state.get("currentStage"),
                "workflowCurrentPhase": workflow_control_state.get("currentPhase"),
                "workflowNextPhase": workflow_control_state.get("nextPhase"),
                "phaseSummaryCurrentPhase": phase_summary.get("currentPhase"),
                "phaseSummaryNextPhase": phase_summary.get("nextPhase"),
            },
        }
    ]

    phases = workflow.get("phases") if isinstance(workflow.get("phases"), list) else []
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        phase_id = phase.get("id") or phase.get("phase")
        if not phase_id:
            continue
        spans.append({
            "spanId": f"phase:{phase_id}",
            "parentSpanId": "task",
            "name": f"Phase: {phase_id}",
            "phase": phase_id,
            "actor": "automind",
            "action": "phase_gate",
            "status": phase.get("status") or (phase.get("gate") or {}).get("status") or "unknown",
            "startTime": None,
            "endTime": None,
            "durationMs": None,
            "message": (phase.get("gate") or {}).get("message") or "",
            "artifactRefs": list((phase.get("artifactRefs") or {}).values()) if isinstance(phase.get("artifactRefs"), dict) else [],
            "evidenceRefs": [],
            "attributes": {
                "checker": phase.get("checker"),
                "cluster": phase.get("cluster"),
                "blockedBy": phase.get("blockedBy") or [],
                "dependencies": phase.get("dependencies") or [],
            },
        })

    for idx, event in enumerate(events, start=1):
        spans.append(_event_span(event, idx, task_code))

    times = [_parse_ts(span.get("startTime")) for span in spans]
    times = [t for t in times if t]
    start = min(times).isoformat(timespec="seconds") if times else None
    end = max(times).isoformat(timespec="seconds") if times else None
    duration_ms = None
    if start and end:
        a = _parse_ts(start)
        b = _parse_ts(end)
        if a and b:
            duration_ms = int((b - a).total_seconds() * 1000)

    return {
        "schemaVersion": TRACE_SCHEMA_VERSION,
        "traceId": trace_id,
        "runId": run_id,
        "taskCode": task_code,
        "taskDir": str(task_dir),
        "startTime": start,
        "endTime": end,
        "durationMs": duration_ms,
        "summary": summarize_trace_spans(spans),
        "spans": spans,
    }


def summarize_trace_spans(spans: list[dict[str, Any]]) -> dict[str, Any]:
    by_phase: dict[str, int] = {}
    errors: list[dict[str, Any]] = []
    for span in spans:
        phase = str(span.get("phase") or "task")
        by_phase[phase] = by_phase.get(phase, 0) + 1
        if span.get("status") in {"error", "failed", "timeout"}:
            errors.append({"spanId": span.get("spanId"), "phase": phase, "message": span.get("message")})
    return {
        "spanCount": len(spans),
        "phaseCounts": by_phase,
        "errorCount": len(errors),
        "errors": errors[:20],
    }


def write_trace(task_code: str, task_dir: Path) -> dict[str, Any]:
    trace = build_trace(task_code, task_dir)
    path = task_dir / "trace.json"
    spans = trace.get("spans") if isinstance(trace.get("spans"), list) else []
    encoded = json.dumps(trace, ensure_ascii=False, indent=2) + "\n"
    if len(spans) > MAX_TRACE_JSON_SPANS or len(encoded.encode("utf-8", errors="ignore")) > MAX_TRACE_JSON_BYTES:
        spans_path = task_dir / "trace-spans.jsonl"
        compact_spans = [_compact_value(span) for span in spans]
        _write_jsonl(spans_path, compact_spans)
        slim = {k: v for k, v in trace.items() if k != "spans"}
        slim["spans"] = compact_spans[:MAX_TRACE_JSON_SPANS]
        slim["spansTruncated"] = max(0, len(compact_spans) - len(slim["spans"]))
        slim["spansRef"] = "trace-spans.jsonl"
        slim["retentionPolicy"] = {
            "mode": "index-plus-jsonl",
            "maxTraceJsonSpans": MAX_TRACE_JSON_SPANS,
            "maxTraceJsonBytes": MAX_TRACE_JSON_BYTES,
            "payloadPolicy": "large strings/lists/dicts are clipped; raw artifacts stay in task files referenced by paths",
        }
        path.write_text(json.dumps(slim, ensure_ascii=False, indent=2) + "\n")
        return slim
    path.write_text(encoded)
    return trace


def render_trace_text(trace: dict[str, Any], *, limit: int = 80) -> str:
    lines = [
        f"Trace: {trace.get('taskCode')}  traceId={trace.get('traceId')}  runId={trace.get('runId')}",
        f"Duration: {trace.get('durationMs') or '-'} ms  spans={trace.get('summary', {}).get('spanCount', 0)}  errors={trace.get('summary', {}).get('errorCount', 0)}",
        "",
        "Spans:",
    ]
    spans = trace.get("spans") if isinstance(trace.get("spans"), list) else []
    for span in spans[:limit]:
        parent = span.get("parentSpanId") or "-"
        status = span.get("status") or "-"
        phase = span.get("phase") or "-"
        actor = span.get("actor") or "-"
        msg = (span.get("message") or "").replace("\n", " ")[:100]
        lines.append(f"- {span.get('spanId')} <- {parent} [{status}] {phase}/{actor}: {span.get('action')} {msg}")
    if len(spans) > limit:
        lines.append(f"... {len(spans) - limit} more spans omitted; use --json for full trace")
    return "\n".join(lines)
