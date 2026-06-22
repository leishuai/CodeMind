"""Compact log digests for agent context and reports.

Raw logs remain on disk as evidence. Digests give agents a small, prioritized
entry point so they do not read oversized build/device logs by default.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

SMALL_LOG_BYTES = 256_000
LARGE_LOG_BYTES = 1_000_000
OVERSIZED_LOG_BYTES = 10_000_000
TAIL_BYTES = 16_000
MAX_KEY_LINES = 40
KEY_LINE_RE = re.compile(
    r"(RESULT=|PASS|FAIL|ERROR|WARN|Exception|Traceback|BUILD SUCCESS|BUILD FAILED|INSTALL_FAILED|"
    r"music_audio_finish|stop_reason|nextAction|completion-check|partial|blocked)",
    re.IGNORECASE,
)

CONTEXT_ARTIFACT_NAMES = {
    "generator-context.json",
    "generator-context.md",
    "evaluator-context.json",
    "evaluator-context.md",
    "generator-prompt.md",
    "evaluator-prompt.md",
    "log-digest.json",
    "log-digest.md",
}
GENERATED_OR_HIGH_VOLUME_HINTS = (
    "generated",
    "graph",
    "report",
    "trace",
    "events",
    "hierarchy",
    "database dump",
    "db dump",
    "log window",
)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _read_tail(path: Path, limit: int = TAIL_BYTES) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > limit:
                fh.seek(max(0, size - limit))
            data = fh.read(limit)
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _key_lines_from_tail(tail: str) -> list[str]:
    rows: list[str] = []
    for line in tail.splitlines():
        if KEY_LINE_RE.search(line):
            rows.append(line[:500])
    if len(rows) > MAX_KEY_LINES:
        return rows[-MAX_KEY_LINES:]
    return rows


def _should_skip_context_artifact(path: Path) -> bool:
    return path.name in CONTEXT_ARTIFACT_NAMES


def _artifact_kind_hint(path: Path) -> str | None:
    lower = str(path).lower()
    if path.name in {"generator.log", "evaluator.log"}:
        return "agent transcript/log; use log-digest summaries, targeted grep, or bounded tail only"
    if "hierarchy" in lower and path.suffix.lower() in {".xml", ".json"}:
        return "raw UI hierarchy; prefer summary or targeted selector/text search"
    if "db" in lower or "database" in lower or "sqlite" in lower:
        return "raw database dump; prefer extracted event summary or targeted query result"
    if path.suffix.lower() in {".html", ".htm"} and ("graph" in lower or "report" in lower or "generated" in lower):
        return "generated report/graph bundle; prefer summary, path, or targeted grep"
    if path.name in {"trace.json", "events.jsonl", "action-trace.jsonl"}:
        return "trace/event stream; prefer summary or targeted grep/tail"
    return None


def _recommend_read_mode(path: Path, size: int) -> str:
    name = path.name.lower()
    if size == 0:
        return "skip_empty"
    if name in {"commands.md", "env.json"} or name.endswith("summary.json") or name.endswith("result.json"):
        return "read_direct"
    if size <= SMALL_LOG_BYTES:
        return "read_direct"
    if size <= LARGE_LOG_BYTES:
        return "read_digest_then_tail"
    if size <= OVERSIZED_LOG_BYTES:
        return "read_digest_then_targeted_grep"
    return "oversized_targeted_grep_only"


def summarize_log_file(path: Path, task_dir: Path) -> dict[str, Any]:
    stat = path.stat()
    tail = _read_tail(path)
    item = {
        "path": _rel(path, task_dir),
        "bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "sha256": _sha256_file(path),
        "recommendedReadMode": _recommend_read_mode(path, stat.st_size),
        "keyLines": _key_lines_from_tail(tail),
    }
    hint = _artifact_kind_hint(path)
    if hint:
        item["artifactKindHint"] = hint
    return item


def recent_iteration_dirs(task_dir: Path, current_iter_dir: Path, limit: int = 4) -> list[Path]:
    logs_dir = task_dir / "logs"
    if not logs_dir.exists():
        return [current_iter_dir]
    dirs = [p for p in logs_dir.glob("iter-*") if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
    selected = dirs[-limit:]
    if current_iter_dir not in selected:
        selected.append(current_iter_dir)
    return selected


def build_log_digest(task_dir: Path, iter_log_dir: Path, *, recent_limit: int = 4) -> dict[str, Any]:
    iter_log_dir.mkdir(parents=True, exist_ok=True)
    dirs = recent_iteration_dirs(task_dir, iter_log_dir, recent_limit)
    files: list[dict[str, Any]] = []
    for directory in dirs:
        if not directory.exists():
            continue
        for path in sorted(p for p in directory.iterdir() if p.is_file()):
            if _should_skip_context_artifact(path):
                continue
            try:
                files.append(summarize_log_file(path, task_dir))
            except OSError:
                continue
    oversized = [item for item in files if item["recommendedReadMode"] == "oversized_targeted_grep_only"]
    payload = {
        "schema": "automind.log_digest.v1",
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "taskCode": task_dir.name,
        "iterationLogDir": _rel(iter_log_dir, task_dir),
        "readPriority": [
            "automind-workflow-state.json / evaluation.json / completion-report.json",
            "Validation.md / Delivery.md",
            "latest logs/iter-N/commands.md and log-digest.md",
            "*summary* / *result* / proof artifacts",
            "targeted grep/tail/line-range of specific raw artifacts only when needed",
        ],
        "policy": {
            "rawLogsRemainEvidence": True,
            "defaultModelInput": "digest first; do not inline raw logs, generated bundles, trace/event streams, raw UI hierarchy, or raw DB dumps",
            "largeLogBytes": LARGE_LOG_BYTES,
            "oversizedLogBytes": OVERSIZED_LOG_BYTES,
            "excludedContextArtifacts": sorted(CONTEXT_ARTIFACT_NAMES),
            "avoidBroadArtifactTypes": [
                "previous full agent transcripts",
                "oversized raw logs",
                "generated report/graph/html bundles",
                "raw UI hierarchy dumps",
                "raw database dumps",
                "large logcat/syslog windows",
                "full build outputs",
                "full diffs",
                "trace/event streams",
                "binary/encoded artifacts",
            ],
        },
        "files": files,
        "oversizedCount": len(oversized),
    }
    (iter_log_dir / "log-digest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    (iter_log_dir / "log-digest.md").write_text(render_log_digest_md(payload) + "\n")
    return payload


def render_log_digest_md(payload: dict[str, Any]) -> str:
    lines = [
        f"# Log Digest - {payload.get('taskCode')} {payload.get('iterationLogDir')}",
        "",
        "Raw logs remain on disk. Agents should read this digest first, then use targeted grep/tail/line ranges only when needed.",
        "",
        "## Read priority",
    ]
    lines.extend(f"- {item}" for item in payload.get("readPriority", []))
    lines.extend(["", "## Files"])
    for item in payload.get("files", []):
        lines.append(f"- `{item['path']}` — {item['bytes']} bytes; mode={item['recommendedReadMode']}")
        if item.get("artifactKindHint"):
            lines.append(f"  - note: {item['artifactKindHint']}")
        for key in item.get("keyLines", [])[:8]:
            lines.append(f"  - key: {key}")
    if payload.get("oversizedCount"):
        lines.extend(["", f"Oversized raw logs: {payload['oversizedCount']} (use targeted grep/tail; do not read whole file by default)."])
    return "\n".join(lines)
