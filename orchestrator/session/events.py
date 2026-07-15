"""Durable event timeline for CodeMind sessions.

The event stream is shared by CLI TUI and skill mode. Events are append-only in
`events.jsonl`. Renderers keep chronological order by default; only heartbeat
status events use `replaceKey=heartbeat:status` to update one visible row.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

SENSITIVE_KEY_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|auth[_-]?token|access[_-]?token|private[_-]?key|secret[_-]?key)")
SENSITIVE_VALUE_RE = re.compile(r"(?i)(sk-[A-Za-z0-9_\-]{12,}|figd_[A-Za-z0-9_\-]{12,}|[A-Za-z0-9_\-]{24,}\.[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{12,})")


def redact_sensitive_text(text: str) -> str:
    out = []
    for line in str(text or "").splitlines():
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            if SENSITIVE_KEY_RE.search(key):
                out.append(f"{key}=<redacted>")
                continue
        out.append(SENSITIVE_VALUE_RE.sub("<redacted>", line))
    return "\n".join(out)


def redact_sensitive_obj(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, list):
        return [redact_sensitive_obj(v) for v in value]
    if isinstance(value, dict):
        redacted = {}
        for k, v in value.items():
            if SENSITIVE_KEY_RE.search(str(k)):
                redacted[k] = "<redacted>"
            else:
                redacted[k] = redact_sensitive_obj(v)
        return redacted
    return value


def events_path(task_dir: Path) -> Path:
    return task_dir / "events.jsonl"


def append_event(
    task_dir: Path,
    event_type: str,
    message: str,
    *,
    level: str = "info",
    phase: str | None = None,
    iteration: int | None = None,
    replace_key: str | None = None,
    source: str = "automind",
    data: dict[str, Any] | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    action: str | None = None,
    status: str | None = None,
    duration_ms: int | None = None,
    artifact_refs: list[str] | None = None,
    evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    task_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "type": event_type,
        "level": level,
        "message": redact_sensitive_text(message),
        "source": source,
    }
    if phase:
        event["phase"] = phase
    if iteration is not None:
        event["iteration"] = iteration
    if replace_key:
        event["replaceKey"] = replace_key
    if span_id:
        event["spanId"] = span_id
    if parent_span_id:
        event["parentSpanId"] = parent_span_id
    if action:
        event["action"] = action
    if status:
        event["status"] = status
    if duration_ms is not None:
        event["durationMs"] = duration_ms
    if artifact_refs:
        event["artifactRefs"] = artifact_refs
    if evidence_refs:
        event["evidenceRefs"] = evidence_refs
    if data:
        event["data"] = redact_sensitive_obj(data)
    with events_path(task_dir).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def read_events(task_dir: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    path = events_path(task_dir)
    if not path.exists():
        return []
    lines = path.read_text(errors="ignore").splitlines()
    if limit is not None:
        lines = lines[-limit:]
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def render_timeline_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return TUI timeline events.

    Keep ordinary events strictly chronological. The only replaceable row is the
    live heartbeat status because it is a dashboard indicator, not a historical
    log entry.
    """
    rendered: list[dict[str, Any]] = []
    heartbeat_index: int | None = None
    for event in events:
        if event.get("replaceKey") == "heartbeat:status":
            if heartbeat_index is None:
                heartbeat_index = len(rendered)
                rendered.append(event)
            else:
                rendered[heartbeat_index] = event
            continue
        rendered.append(event)
    return rendered
