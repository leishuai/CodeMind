"""Machine-global lesson accumulation for CodeMind.

When a task finishes, ``generate_summary`` already writes project-local memory
under the workspace ``.automind/summary/``. This module adds the second tier the
user asked for: durable, cross-task lessons that live in the CodeMind runtime
install under ``summaries/accumulated/``.

First-principles design:

- Users cannot push to the CodeMind repo, so ``summaries/preloaded/`` (the
  maintainer-distributed pack) is read-only for them. The only place a user can
  durably accumulate is ``summaries/accumulated/`` (install-exclude protected).
- Therefore there are exactly two auto-sink destinations, split by a single
  question — "is this lesson useful to any project, or only this one?":
    * ``technical/``         cross-project, business-agnostic, public-safe.
    * ``business/<slug>/``   bound to this project; carries app ids / local
                              paths / domain specifics; reused across this
                              project's tasks but never globally generic.
- Promotion ``accumulated -> preloaded`` is a maintainer-only, git-distributed
  step and is intentionally NOT automated here.

Anti-bloat is intentionally simple: append-with-dedup by a canonical key, and a
per-file entry cap. No scoring, no eviction.

File contract (aligned with workspace summary/index files):

- ``auto-accumulated.md`` — full entries (human-readable body). Each entry
  carries an inline ``<!-- entry: {json} -->`` marker, so this single file is
  both the human view and the machine-scannable list (no separate index md).
- It starts with a YAML frontmatter block (``---\n...\n---``):
  schemaVersion, scope, slug, entryCount, maxEntries, updatedAt, note.
- ``index.jsonl`` — sibling phase-reuse routing records, populated only for
  AI-judged high-value (``ai_refined``) entries.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from orchestrator.config import (
    ACCUMULATED_BUSINESS_DIR,
    ACCUMULATED_MAX_ENTRIES_PER_FILE,
    ACCUMULATED_TECHNICAL_DIR,
    AUTOMIND_ROOT,
    AUTOMIND_WORKSPACE_ROOT,
)
from orchestrator.knowledge_index import SURFACE_KEYWORDS, append_knowledge_index_record
from orchestrator.state import ensure_dir

_SCHEMA_VERSION = 1
# File-level frontmatter marker: first ``---`` ... ``---`` block at the top.
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# Key extraction: accepts both legacy HTML comments and new entry metadata.
_KEY_INLINE_RE = re.compile(r"<!--\s*key:\s*([0-9a-f]{12})\s*-->")
_ENTRY_META_RE = re.compile(r"<!--\s*entry:\s*(\{.*?\})\s*-->")
# Signals that a lesson is bound to a specific project/machine and therefore
# must not pollute the cross-project ``technical/`` pack.
_BUSINESS_SIGNAL_RE = re.compile(
    r"(?:\b(?:com|cn|io|org|net|app)\.[a-zA-Z][\w.]+)"  # bundle/package id
    r"|(?:/Users/[^\s`]+)|(?:/home/[^\s`]+)"            # absolute machine paths
    r"|(?:\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4})"  # UDID-ish prefix
)


# ---------------------------------------------------------------------------
# YAML frontmatter helpers (tiny hand-rolled; avoids adding a dependency).
# ---------------------------------------------------------------------------


def _yaml_quote(value: str) -> str:
    """Quote a YAML scalar only when necessary."""
    if value is None:
        return ""
    s = str(value)
    needs_quote = (
        s == ""
        or s.strip() != s
        or any(c in s for c in (":", "#", "\"", "'", "{", "}", "[", "]", ",", "|", ">", "&", "*", "?", "!", "%", "@", "`"))
        or s.lower() in {"true", "false", "null", "yes", "no", "on", "off"}
        or s.lstrip("-").strip() != s
    )
    if needs_quote:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _yaml_frontmatter(**fields) -> str:
    """Render a small ``---\n...\n---\n`` block from flat keyword fields."""
    lines = ["---"]
    for key, value in fields.items():
        lines.append(f"{key}: {_yaml_quote(str(value))}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def parse_yaml_frontmatter(text: str) -> tuple[dict, str]:
    """Split leading ``---\n...\n---`` block from the body of a file.

    Returns ``(metadata_dict, body)``. If no frontmatter is present,
    ``metadata_dict`` is ``{}`` and ``body`` is the original text.

    This is intentionally small: flat scalar fields only, no nesting, and
    quoted/trimmed values — sufficient for the fields we ourselves write.
    """
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return {}, text or ""
    raw = match.group(1)
    body = text[match.end():]
    metadata: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        metadata[key.strip()] = value
    return metadata, body


def _update_frontmatter_in_place(path: Path, **updates) -> None:
    """Rewrite the top-level frontmatter of an existing file in place.

    Used after append (to reflect new ``entryCount`` / ``updatedAt``).
    Preserves any existing fields; ``updates`` override or add.
    """
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="ignore")
    existing, body = parse_yaml_frontmatter(text)
    merged = {**existing, **{k: str(v) for k, v in updates.items()}}
    path.write_text(_yaml_frontmatter(**merged) + body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Slug / key routing (unchanged semantics, added index awareness).
# ---------------------------------------------------------------------------


def project_slug(workspace_root: Path | None = None) -> str:
    """Return a filesystem-safe slug for the current project workspace."""
    root = workspace_root or AUTOMIND_WORKSPACE_ROOT
    name = Path(root).name or "project"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug or "project"


def _canonical_key(text: str) -> str:
    """Stable short key for dedup; ignores case, punctuation, and whitespace."""
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def looks_business_specific(text: str) -> bool:
    """True when a lesson carries project/machine/domain identifiers."""
    if not text:
        return False
    if _BUSINESS_SIGNAL_RE.search(text):
        return True
    slug_token = project_slug().replace("-", "")
    compact = re.sub(r"[^a-z0-9]+", "", text.lower())
    return bool(slug_token) and slug_token in compact


def _existing_keys(path: Path) -> set[str]:
    """Collect canonical keys from a file (legacy inline + new entry meta)."""
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8", errors="ignore")
    keys: set[str] = set(_KEY_INLINE_RE.findall(text))
    for meta_json in _ENTRY_META_RE.findall(text):
        try:
            meta = json.loads(meta_json)
            if isinstance(meta, dict) and "key" in meta:
                keys.add(str(meta["key"]))
        except Exception:
            pass
    return keys


def _entry_count(path: Path) -> int:
    return len(_existing_keys(path))


# ---------------------------------------------------------------------------
# File initialization with frontmatter.
# ---------------------------------------------------------------------------


def _ensure_body_file(path: Path, *, scope: str, title: str, note: str,
                       max_entries: int, slug: str | None = None) -> None:
    """Create ``auto-accumulated.md`` with YAML frontmatter if missing."""
    if path.exists():
        return
    ensure_dir(path.parent)
    header = _yaml_frontmatter(
        schemaVersion=_SCHEMA_VERSION,
        scope=scope,
        slug=(slug or ""),
        entryCount=0,
        maxEntries=max_entries,
        updatedAt=datetime.now().isoformat(timespec="seconds"),
        title=title,
        note=note,
    )
    body = (
        f"# {title}\n\n{note}\n\n"
        "Entries below are auto-accumulated by CodeMind task summaries. "
        "Treat them as local reuse hints; current Requirements/TestCases and "
        "fresh evidence always win.\n"
    )
    path.write_text(header + body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry writing.
# ---------------------------------------------------------------------------


def _entry_meta_block(key: str, *, kind: str, task_code: str, source: str = "deterministic", **extra) -> str:
    """Render a small metadata marker placed right above the entry body.

    Shape: ``<!-- entry: {json} -->`` — invisible in standard markdown
    renderers, but machine-scannable. It replaces the previous
    ``<!-- key: xxxx -->`` line while remaining backward-compatible
    (``_existing_keys`` still scans both forms).

    ``source`` records whether the lesson came from AI summary refinement
    (``ai_refined``) or deterministic extraction (``deterministic``).
    """
    meta = {
        "key": key,
        "kind": kind,
        "source": source,
        "taskCode": task_code,
        "createdAt": datetime.now().isoformat(timespec="seconds"),
    }
    for k, v in extra.items():
        if v is not None:
            meta[k] = v
    return f"<!-- entry: {json.dumps(meta, ensure_ascii=False)} -->\n"


def _append_entry(
    body_file: Path,
    *,
    key: str,
    kind: str,
    task_code: str,
    body: str,
    max_entries: int,
    scope: str,
    slug: str | None = None,
    source: str = "deterministic",
) -> bool:
    """Append one entry to the body file.

    Returns ``False`` (no-op) when the key already exists or the file is
    at capacity. On success, returns ``True`` and updates frontmatter
    ``entryCount`` / ``updatedAt``.

    Each entry carries an inline ``<!-- entry: {json} -->`` marker, so the body
    file is both the human-readable view and the machine-scannable list; a
    separate index markdown is no longer maintained.
    """
    if key in _existing_keys(body_file):
        return False
    if _entry_count(body_file) >= max_entries:
        return False

    meta = _entry_meta_block(key, kind=kind, task_code=task_code, source=source)
    entry_block = f"\n{meta}{body.rstrip()}\n"
    with body_file.open("a", encoding="utf-8") as fh:
        fh.write(entry_block)

    new_count = _entry_count(body_file)
    _update_frontmatter_in_place(
        body_file,
        entryCount=new_count,
        updatedAt=datetime.now().isoformat(timespec="seconds"),
    )
    return True


# ---------------------------------------------------------------------------
# Entry formatters.
# ---------------------------------------------------------------------------


def _format_successful_path(item: dict, task_code: str, evidence_ref: str) -> tuple[str, list[str]]:
    body = (
        f"### Successful path — {item.get('purpose', 'verification')} ({task_code})\n"
        f"- command: `{item.get('command', '-')}`\n"
        f"- cwd: `{item.get('cwd', '-')}`\n"
        f"- preconditions: {item.get('preconditions', '-')}\n"
        f"- scope: {item.get('scope', '-')}; confidence: {item.get('confidence', 'low')}\n"
        f"- evidence: {item.get('evidence', evidence_ref)}\n"
    )
    bullets = [
        f"purpose: {item.get('purpose', '-')}",
        f"command: `{item.get('command', '-')}`",
        f"scope: {item.get('scope', '-')}",
        f"confidence: {item.get('confidence', 'low')}",
    ]
    return body, bullets


def _format_avoid_path(item: dict, task_code: str) -> tuple[str, list[str]]:
    body = (
        f"### Avoid path — {item.get('failureCategory', 'failure')} ({task_code})\n"
        f"- path: `{item.get('path', '-')}`\n"
        f"- doNotRetryUnless: {item.get('doNotRetryUnless', '-')}\n"
        f"- evidence: {item.get('evidence', '-')}\n"
    )
    bullets = [
        f"failureCategory: {item.get('failureCategory', '-')}",
        f"path: `{item.get('path', '-')}`",
    ]
    return body, bullets


def _format_lesson(text: str, task_code: str, kind: str) -> tuple[str, list[str]]:
    body = f"### {kind} ({task_code})\n- {text}\n"
    bullets = [f"summary: {text[:80]}"]
    return body, bullets


# ---------------------------------------------------------------------------
# Phase-reuse index bridge.
#
# Accumulated lessons that an AI refiner marks high-value are additionally
# upserted into a sibling ``index.jsonl`` so phase-reuse retrieval (scored,
# phase-aware) can discover them — not just the file-level Reuse.md overview.
# The md file stays the raw body; index.jsonl is the routing/retrieval layer.
# ---------------------------------------------------------------------------

_KIND_PHASES = {
    "successful_path": ["testcases", "plan", "generator", "evaluator"],
    "avoid_path": ["plan", "generator", "evaluator"],
    "avoid_repeating": ["generator", "evaluator"],
    "lesson": ["all"],
}


def _index_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9_./+-]+|[\u4e00-\u9fff]+", str(text or "").lower()):
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
    return tokens[:12]


def _build_index_record(
    *,
    key: str,
    kind: str,
    task_code: str,
    text: str,
    index_bullets: list[str],
    body_file: Path,
    scope: str,
    slug: str | None,
) -> dict:
    """Build one compact phase-reuse index record for an accumulated entry.

    ``id`` is content-derived (via ``key``) so re-sinking the same lesson
    upserts the same record instead of duplicating it.
    """
    project = AUTOMIND_WORKSPACE_ROOT.name
    value = "; ".join(index_bullets[:2]) or text[:150]
    triggers = [t for t in [kind, scope, slug, project, task_code] if t]
    triggers.extend(_index_tokens(text))
    try:
        raw_path = str(body_file.relative_to(AUTOMIND_ROOT))
    except ValueError:
        raw_path = str(body_file)
    return {
        "id": f"acc-{scope}-{slug or 'global'}-{key}",
        "title": f"Accumulated {kind.replace('_', ' ')} ({task_code})",
        "rawPath": raw_path,
        "value": value[:200],
        "description": text[:200],
        "taskTypes": [],
        "projects": [project] if scope == "business" else [],
        "surfaces": sorted(set(["summary"]) | set(_index_tokens(text)) & set(SURFACE_KEYWORDS)),
        "phaseApplicability": _KIND_PHASES.get(kind, ["all"]),
        "triggers": triggers,
        "confidence": "high",
        "source": "ai_refined",
    }


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def sink_accumulated_lessons(
    task_code: str,
    *,
    final_result: str,
    successful_paths: list[dict] | None = None,
    avoid_paths: list[dict] | None = None,
    reusable: list[str] | None = None,
    downgrade: list[str] | None = None,
    evidence_ref: str = "-",
    workspace_root: Path | None = None,
) -> dict:
    """Sink durable lessons into machine-global accumulated summaries.

    Best-effort: callers should wrap this so a failure never breaks summary
    generation. Returns a small report of what was written.
    """
    successful_paths = successful_paths or []
    avoid_paths = avoid_paths or []
    reusable = reusable or []
    downgrade = downgrade or []

    slug = project_slug(workspace_root)
    business_file = ACCUMULATED_BUSINESS_DIR / slug / "auto-accumulated.md"
    business_jsonl = ACCUMULATED_BUSINESS_DIR / slug / "index.jsonl"
    technical_file = ACCUMULATED_TECHNICAL_DIR / "auto-accumulated.md"
    technical_jsonl = ACCUMULATED_TECHNICAL_DIR / "index.jsonl"
    max_entries = ACCUMULATED_MAX_ENTRIES_PER_FILE

    written = {"technical": 0, "business": 0, "indexed": 0, "slug": slug}

    def _maybe_index(
        *, key: str, kind: str, text: str, index_bullets: list[str],
        body_file: Path, index_path: Path, scope: str, slug: str | None, source: str,
    ) -> None:
        """Upsert a phase-reuse index record for AI-judged high-value entries.

        Gate: only ``ai_refined`` lessons enter the scored retrieval pool, so
        the sibling ``index.jsonl`` stays small and high-signal. Deterministic
        lessons remain in the md body + Reuse.md overview only.
        """
        if source != "ai_refined":
            return
        record = _build_index_record(
            key=key, kind=kind, task_code=task_code, text=text,
            index_bullets=index_bullets, body_file=body_file, scope=scope, slug=slug,
        )
        try:
            append_knowledge_index_record(record, index_path=index_path)
            written["indexed"] += 1
        except OSError:
            pass

    def route(text: str, *, kind: str, body: str, index_bullets: list[str], source: str = "deterministic") -> None:
        key = _canonical_key(text)
        if looks_business_specific(text):
            _ensure_body_file(
                business_file,
                scope="business",
                title=f"Accumulated business lessons — {slug}",
                note=(
                    "Project-bound lessons reusable across this project's tasks. "
                    "These carry app ids / local paths / domain specifics and are "
                    "kept local to this machine (excluded from public skill exports)."
                ),
                max_entries=max_entries,
                slug=slug,
            )
            if _append_entry(
                business_file,
                key=key, kind=kind, task_code=task_code,
                body=body,
                max_entries=max_entries, scope="business", slug=slug,
                source=source,
            ):
                written["business"] += 1
            _maybe_index(
                key=key, kind=kind, text=text, index_bullets=index_bullets,
                body_file=business_file, index_path=business_jsonl,
                scope="business", slug=slug, source=source,
            )
        else:
            _ensure_body_file(
                technical_file,
                scope="technical",
                title="Accumulated technical lessons",
                note="Cross-project, business-agnostic, public-safe reuse lessons.",
                max_entries=max_entries,
                slug=None,
            )
            if _append_entry(
                technical_file,
                key=key, kind=kind, task_code=task_code,
                body=body,
                max_entries=max_entries, scope="technical", slug=None,
                source=source,
            ):
                written["technical"] += 1
            _maybe_index(
                key=key, kind=kind, text=text, index_bullets=index_bullets,
                body_file=technical_file, index_path=technical_jsonl,
                scope="technical", slug=None, source=source,
            )

    def _item_source(item: dict) -> str:
        return "ai_refined" if item.get("aiRefined") or item.get("source") == "ai_refined" else "deterministic"

    def _text_source(text: str) -> str:
        return "ai_refined" if text.strip().lower().startswith("ai refined:") or text.strip().lower().startswith("ai ") else "deterministic"

    result_ok = str(final_result or "").strip().lower() in {"pass", "passed", "success"}

    if result_ok:
        for item in successful_paths[:5]:
            if not isinstance(item, dict):
                continue
            text = f"{item.get('purpose', '')} {item.get('command', '')}".strip()
            if not text:
                continue
            body, bullets = _format_successful_path(item, task_code, evidence_ref)
            route(text, kind="successful_path", body=body, index_bullets=bullets, source=_item_source(item))

    for item in avoid_paths[:5]:
        if not isinstance(item, dict):
            continue
        text = f"{item.get('failureCategory', '')} {item.get('path', '')}".strip()
        if not text:
            continue
        body, bullets = _format_avoid_path(item, task_code)
        route(text, kind="avoid_path", body=body, index_bullets=bullets, source=_item_source(item))

    for text in reusable[:5]:
        text = str(text).strip()
        if text:
            body, bullets = _format_lesson(text, task_code, "Reusable lesson")
            route(text, kind="lesson", body=body, index_bullets=bullets, source=_text_source(text))

    for text in downgrade[:3]:
        text = str(text).strip()
        if text:
            body, bullets = _format_lesson(text, task_code, "Avoid repeating")
            route(text, kind="avoid_repeating", body=body, index_bullets=bullets, source=_text_source(text))

    return written
