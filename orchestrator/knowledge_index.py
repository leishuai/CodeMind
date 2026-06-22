"""Phase-aware knowledge index and phase-reuse context helpers.

This module keeps long reusable knowledge out of task prompts by using a small
index + raw-file contract:

- ``.automind/summary/index.jsonl`` and ``summaries/index.jsonl`` contain short
  routing records.
- raw markdown files contain single-responsibility experience notes.
- ``Reuse.md`` is a task-level manifest, not a dump of all old lessons.
- ``phase-reuse/<phase>.md`` is the phase-specific context agents should read
  before entering a phase.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from orchestrator.config import AUTOMIND_ROOT, AUTOMIND_WORKSPACE_ROOT, SUMMARY_DIR
from orchestrator.runtime_paths import format_failed_runtime_paths_section
from orchestrator.state import ensure_dir, read_runtime_state, rel_to_root

KNOWLEDGE_INDEX_PATH = SUMMARY_DIR / "index.jsonl"
KNOWLEDGE_RAW_DIR = SUMMARY_DIR / "raw"
GLOBAL_KNOWLEDGE_INDEX_PATH = AUTOMIND_ROOT / "summaries" / "index.jsonl"
GLOBAL_KNOWLEDGE_RAW_DIR = AUTOMIND_ROOT / "summaries" / "raw"
# Accumulated lessons keep their own sibling index.jsonl files next to the
# auto-accumulated.md bodies. They are part of the same scored retrieval pool.
ACCUMULATED_INDEX_ROOT = AUTOMIND_ROOT / "summaries" / "accumulated"

DEFAULT_PHASES = [
    "brainstorm",
    "requirements",
    "testcases",
    "plan",
    "generator",
    "evaluator",
    "summary",
]

PHASE_PURPOSES = {
    "brainstorm": "Project/context risks and reusable demand-discovery hints.",
    "requirements": "Requirement/non-goal/authorization patterns relevant to this task.",
    "testcases": "Verification path hints, runtime proof patterns, and known evidence routes.",
    "plan": "Successful/avoid build-test paths and planning guardrails.",
    "generator": "Implementation/build guardrails and known paths to avoid retry noise.",
    "evaluator": "Verification commands, evidence expectations, and failure classification hints.",
    "summary": "Existing knowledge to compare against before writing new reusable lessons.",
}

SURFACE_KEYWORDS = {
    "ios": ["ios", "iphone", "xcode", "xcodebuild", "xcuitest", "swift", "custom_build_wrapper", "真机", "苹果"],
    "android": ["android", "gradle", "adb", "apk", "emulator", "安卓"],
    "build": ["build", "compile", "xcodebuild", "gradle", "bazel", "custom_build_wrapper", "编译", "构建"],
    "signing": ["sign", "signing", "profile", "certificate", "p12", "provision", "team", "签名", "证书"],
    "install": ["install", "deploy", "apk", "ipa", "安装", "部署"],
    "launch": ["launch", "open", "start", "启动", "打开"],
    "test": ["test", "pytest", "xcuitest", "playwright", "测试", "验证"],
    "visual": ["visual", "screenshot", "snapshot", "ocr", "截图", "视觉"],
    "summary": ["summary", "reuse", "lesson", "总结", "复用", "经验"],
}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        if not value.strip():
            return []
        # Accept comma-separated index fields for hand-written indexes.
        return [part.strip() for part in re.split(r"[,;]", value) if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_tokens(values: Iterable[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        text = str(value or "").lower()
        for token in re.findall(r"[a-z0-9_./+-]+|[\u4e00-\u9fff]+", text):
            if len(token) >= 2:
                tokens.add(token)
    return tokens


def _read_jsonl(path: Path, source: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(errors="ignore").splitlines(), start=1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        data = dict(data)
        data.setdefault("id", f"{path.stem}-{line_no}")
        data["_indexPath"] = str(path)
        data["_source"] = source
        records.append(data)
    return records


def _dedupe_records_by_id(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the last record for a stable id, preserving insertion order."""
    keyed: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in records:
        key = str(record.get("id") or "").strip()
        if not key:
            key = f"{record.get('_indexPath', '')}:{len(order)}"
        if key not in keyed:
            order.append(key)
        keyed[key] = record
    return [keyed[key] for key in order]


def load_knowledge_index() -> list[dict[str, Any]]:
    """Load local workspace and runtime-level knowledge indexes.

    The runtime pool includes the top-level ``summaries/index.jsonl`` plus every
    accumulated sibling ``index.jsonl`` (technical + business/<slug>), so
    AI-promoted accumulated lessons participate in scored phase-reuse retrieval.
    Preloaded packs (``summaries/preloaded/*.md`` frontmatter) are also folded in
    so the curated first-run knowledge participates in scored retrieval, not only
    the progressive Reuse.md overview.
    """
    records: list[dict[str, Any]] = []
    records.extend(_load_preloaded_records())
    records.extend(_read_jsonl(GLOBAL_KNOWLEDGE_INDEX_PATH, "runtime"))
    if ACCUMULATED_INDEX_ROOT.exists():
        for index_path in sorted(ACCUMULATED_INDEX_ROOT.rglob("index.jsonl")):
            records.extend(_read_jsonl(index_path, "runtime"))
    records.extend(_read_jsonl(KNOWLEDGE_INDEX_PATH, "workspace"))
    return _dedupe_records_by_id(records)


PRELOADED_DIR = AUTOMIND_ROOT / "summaries" / "preloaded"
# Map preloaded filename prefixes to the surfaces/task-types they cover so the
# curated packs score against the same retrieval signals as indexed lessons.
_PRELOADED_PREFIX_HINTS = {
    # Platform packs intentionally do NOT claim the generic "build" surface:
    # the platform task_type/surface gate handles platform relevance, and adding
    # "build" would let an android pack leak into any iOS build task (and vice
    # versa) because both share the build surface.
    "ios": {"taskTypes": ["ios"], "surfaces": ["ios"]},
    "android": {"taskTypes": ["android"], "surfaces": ["android"]},
    "client": {"taskTypes": ["ios", "android"], "surfaces": ["ios", "android"]},
    "common": {"taskTypes": ["all"], "surfaces": ["build", "test"]},
}


def _parse_preloaded_frontmatter(text: str) -> dict[str, Any]:
    """Parse the small YAML-like frontmatter block at the top of a preloaded md.

    Intentionally supports only simple scalar and ``  - `` list fields, mirroring
    ``orchestrator.reuse._parse_simple_frontmatter`` without importing it (reuse
    imports this module).
    """
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    meta: dict[str, Any] = {}
    current_key = ""
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            value = line[4:].strip().strip('"').strip("'")
            meta.setdefault(current_key, [])
            if isinstance(meta[current_key], list):
                meta[current_key].append(value)
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            current_key = key.strip()
            value = value.strip()
            meta[current_key] = value.strip('"').strip("'") if value else []
    return meta


def _load_preloaded_records() -> list[dict[str, Any]]:
    """Turn curated preloaded packs into scored-retrieval records."""
    if not PRELOADED_DIR.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(PRELOADED_DIR.glob("*.md")):
        if path.name == "README.md":
            continue
        meta = _parse_preloaded_frontmatter(path.read_text(errors="ignore"))
        if not meta:
            continue
        name = str(meta.get("name") or path.stem)
        prefix = path.stem.split("-", 1)[0]
        hints = _PRELOADED_PREFIX_HINTS.get(prefix, {"taskTypes": ["all"], "surfaces": []})
        triggers: list[str] = []
        for key in ("use_when", "solves"):
            triggers.extend(_as_list(meta.get(key)))
        triggers.append(str(meta.get("description") or ""))
        triggers.append(name)
        records.append({
            "id": f"preloaded-{name}",
            "title": name,
            "description": str(meta.get("description") or "").strip(),
            "rawPath": str(path),
            "phaseApplicability": ["plan", "generator", "evaluator", "summary"],
            "taskTypes": hints.get("taskTypes", ["all"]),
            "surfaces": hints.get("surfaces", []),
            "triggers": triggers,
            "confidence": "high",
            "_source": "runtime",
            "_recordKind": "preloaded",
        })
    return records


def infer_task_query(task_dir: Path, phase: str) -> dict[str, Any]:
    """Build a lightweight retrieval query from task state and artifacts."""
    state = read_runtime_state(task_dir) or {}
    user_input = str(state.get("userInput") or "")
    task_type = str(state.get("taskType") or "").lower()
    project_name = AUTOMIND_WORKSPACE_ROOT.name
    snippets: list[str] = [user_input, task_type, project_name, phase]
    for name in ["Brainstorm.md", "Requirements.md", "TestCases.md", "Plan.md", "Validation.md"]:
        path = task_dir / name
        if path.exists():
            snippets.append(path.read_text(errors="ignore")[:2500])
    text = "\n".join(snippets).lower()

    surfaces = set()
    for surface, keywords in SURFACE_KEYWORDS.items():
        if any(keyword.lower() in text for keyword in keywords):
            surfaces.add(surface)
    if task_type in {"ios", "android"}:
        surfaces.add(task_type)
    if phase == "summary":
        surfaces.add("summary")

    return {
        "phase": phase,
        "taskType": task_type,
        "project": project_name,
        "text": text,
        "tokens": _normalize_tokens(snippets),
        "surfaces": sorted(surfaces),
    }


def _score_record(record: dict[str, Any], query: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    phase = str(query.get("phase") or "")
    task_type = str(query.get("taskType") or "")
    project = str(query.get("project") or "")
    query_surfaces = set(_as_list(query.get("surfaces")))
    query_tokens = set(query.get("tokens") or [])

    phases = {item.lower() for item in _as_list(record.get("phaseApplicability") or record.get("phases"))}
    task_types = {item.lower() for item in _as_list(record.get("taskTypes") or record.get("taskType"))}
    projects = {item.lower() for item in _as_list(record.get("projects") or record.get("project"))}
    surfaces = {item.lower() for item in _as_list(record.get("surfaces") or record.get("surface"))}
    triggers = _normalize_tokens(_as_list(record.get("triggers") or record.get("reuseTrigger")))
    title_tokens = _normalize_tokens([
        str(record.get("id") or ""),
        str(record.get("title") or ""),
        str(record.get("description") or ""),
        str(record.get("value") or ""),
    ])

    if phase and (phase in phases or "all" in phases):
        score += 6
        reasons.append(f"phase={phase}")
    if task_type and (task_type in task_types or "all" in task_types):
        score += 5
        reasons.append(f"taskType={task_type}")
    if project and (project.lower() in projects or "all" in projects):
        score += 7
        reasons.append(f"project={project}")
    overlap = query_surfaces & surfaces
    if overlap:
        score += 4 + len(overlap)
        reasons.append("surface=" + ",".join(sorted(overlap)))
    trigger_overlap = query_tokens & triggers
    if trigger_overlap:
        score += min(5, len(trigger_overlap))
        reasons.append("trigger=" + ",".join(sorted(list(trigger_overlap))[:4]))
    title_overlap = query_tokens & title_tokens
    if title_overlap:
        score += min(3, len(title_overlap))
    confidence = str(record.get("confidence") or "").lower()
    if confidence == "high":
        score += 2
    elif confidence == "medium":
        score += 1
    # Curated preloaded packs are the trusted first-run baseline. Only surface
    # them when there is real task-type/surface/trigger relevance (not the phase
    # freebie alone), then give a small boost so they rank alongside indexed
    # lessons without dominating or leaking across task types.
    if str(record.get("_recordKind") or "") == "preloaded":
        relevant = bool(
            (task_type and (task_type in task_types or "all" in task_types) and "all" not in task_types)
            or overlap
            or trigger_overlap
        )
        if not relevant:
            return 0, reasons
        score += 3
        reasons.append("preloaded(curated baseline)")
    # Generic records should still be discoverable but not dominate.
    if not phases and not task_types and not projects and not surfaces and not triggers:
        score -= 3
    return score, reasons


def search_knowledge(task_dir: Path, phase: str, limit: int = 5, min_score: int = 3) -> list[dict[str, Any]]:
    """Return scored knowledge matches for a phase."""
    query = infer_task_query(task_dir, phase)
    scored: list[dict[str, Any]] = []
    for record in load_knowledge_index():
        score, reasons = _score_record(record, query)
        if score < min_score:
            continue
        item = dict(record)
        item["_score"] = score
        item["_matchReasons"] = reasons
        scored.append(item)
    scored.sort(key=lambda item: (int(item.get("_score") or 0), str(item.get("confidence") or "")), reverse=True)
    return scored[:limit]


def resolve_raw_path(record: dict[str, Any]) -> Optional[Path]:
    raw = str(record.get("rawPath") or record.get("path") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    source = str(record.get("_source") or "")
    if source == "runtime":
        return (AUTOMIND_ROOT / path).resolve()
    # Workspace-local index paths are relative to the workspace root by default.
    return (AUTOMIND_WORKSPACE_ROOT / path).resolve()


def _rel(path: Optional[Path]) -> str:
    if not path:
        return "-"
    try:
        return rel_to_root(path)
    except Exception:
        return str(path)


def format_raw_path_for_reuse(record: dict[str, Any]) -> str:
    """Return a complete, agent-locatable path string for a matched record.

    phase-reuse/Reuse.md are read by agents whose working directory is the target
    project, not the AutoMind runtime. A bare workspace-relative path can be
    unresolvable from there, so emit absolute paths anchored on the right root
    and tag the source (runtime vs workspace) so the agent knows where it lives.
    """
    path = resolve_raw_path(record)
    if not path:
        return "-"
    source = str(record.get("_source") or "")
    label = "runtime" if source == "runtime" else "workspace" if source == "workspace" else "abs"
    return f"{path} ({label})"


def render_phase_reuse(task_dir: Path, phase: str, matches: list[dict[str, Any]]) -> str:
    generated = datetime.now().isoformat(timespec="seconds")
    purpose = PHASE_PURPOSES.get(phase, "Relevant phase-specific reuse hints.")
    lines = [
        f"# Phase Reuse: {phase}",
        "",
        f"Generated at: {generated}",
        f"Purpose: {purpose}",
        "",
        "Policy:",
        "- Treat this as evidence-backed hints, not current-task requirements.",
        "- Reuse can become stale over time (toolchain/SDK/config/code may have changed). It offers a candidate way to fix or work around the current problem, not a guaranteed answer; always defer to the actual current situation and fresh evidence.",
        "- Current Brainstorm/Requirements/TestCases/Plan and fresh evidence win.",
        "- Load raw files only when the matched entry is relevant to this phase.",
        "- If a high-confidence successful path is ignored, record why in the owning artifact.",
        "",
    ]
    if not matches:
        lines.extend([
            "## Matched knowledge",
            "",
            "- No relevant indexed knowledge matched this phase.",
            "",
        ])
        if phase in {"generator", "evaluator"}:
            try:
                from orchestrator.state import read_evaluation_json

                failed_runtime_paths = format_failed_runtime_paths_section(read_evaluation_json(task_dir))
            except Exception:
                failed_runtime_paths = ""
            if failed_runtime_paths:
                lines.extend([failed_runtime_paths.rstrip(), ""])
        return "\n".join(lines).rstrip() + "\n"

    lines.extend([
        "## Matched knowledge index entries",
        "",
        "Raw path is an absolute, agent-locatable path (the source tag shows whether",
        "it lives under the AutoMind runtime or the workspace). Open it from there.",
        "\"Why matched\" lists the concrete signals (phase/taskType/surface/trigger);",
        "a `preloaded(curated baseline)` tag means a maintainer-preloaded first-run pack.",
        "",
        "| ID | Value | Raw path (absolute) | Confidence | Why matched |",
        "|----|-------|---------------------|------------|-------------|",
    ])
    for item in matches:
        value = str(item.get("value") or item.get("description") or item.get("title") or "-").replace("\n", " ")[:160]
        reasons = ", ".join(_as_list(item.get("_matchReasons"))) or f"score={item.get('_score')}"
        lines.append(
            f"| {item.get('id', '-')} | {value} | `{format_raw_path_for_reuse(item)}` | {item.get('confidence', '-')} | {reasons} |"
        )

    reminders: list[str] = []
    avoid_paths: list[str] = []
    successful_paths: list[str] = []
    for item in matches[:5]:
        for key, target in [("importantReminders", reminders), ("reminders", reminders), ("avoidPaths", avoid_paths), ("avoid", avoid_paths), ("successfulPaths", successful_paths), ("successfulPath", successful_paths)]:
            for value in _as_list(item.get(key)):
                if value not in target and len(target) < 6:
                    target.append(value)
    if successful_paths:
        lines.extend(["", "## Top successful paths to consider", ""])
        lines.extend(f"- {item}" for item in successful_paths[:5])
    if avoid_paths:
        lines.extend(["", "## Top avoid paths", ""])
        lines.extend(f"- {item}" for item in avoid_paths[:6])
    if reminders:
        lines.extend(["", "## Important reminders", ""])
        lines.extend(f"- {item}" for item in reminders[:6])

    if phase in {"generator", "evaluator"}:
        try:
            from orchestrator.state import read_evaluation_json

            failed_runtime_paths = format_failed_runtime_paths_section(read_evaluation_json(task_dir))
        except Exception:
            failed_runtime_paths = ""
        if failed_runtime_paths:
            lines.extend(["", failed_runtime_paths.rstrip()])

    lines.extend([
        "",
        "## Required phase behavior",
        "",
        "- Prefer matched high-confidence successful paths when scope and preconditions match.",
        "- Do not retry matched avoid paths unless the listed retry condition changed.",
        "- Cite the raw path or phase-reuse path in Plan/TestCases/Delivery/Validation when used.",
    ])
    return "\n".join(lines).rstrip() + "\n"


def write_phase_reuse_context(task_dir: Path, phase: str, reason: str = "before_phase") -> dict[str, Any]:
    matches = search_knowledge(task_dir, phase)
    phase_dir = task_dir / "phase-reuse"
    ensure_dir(phase_dir)
    path = phase_dir / f"{phase}.md"
    path.write_text(render_phase_reuse(task_dir, phase, matches))
    return {
        "phase": phase,
        "path": path,
        "matches": matches,
        "reason": reason,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }


def ensure_phase_reuse_contexts(task_dir: Path, phases: Optional[list[str]] = None, reason: str = "reuse_manifest") -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for phase in phases or DEFAULT_PHASES:
        results[phase] = write_phase_reuse_context(task_dir, phase, reason=reason)
    return results


# Phases that must record an explicit reuse acknowledgement before running.
REUSE_GATE_PHASES = ("generator", "evaluator")

# Failure categories that force a reuse-first decision before ask_user/replan.
# These are the "repeated failure / signing / device / build" classes the
# operator called out: the loop must exhaust matched safe reuse paths before it
# is allowed to interrupt the human.
REUSE_GATE_FAILURE_CATEGORIES = {
    "real_device_or_signing",
    "mobile_device_unavailable",
    "permission_blocked",
    "build_blocker",
    "build_failed",
    "signing",
    "device",
    "external_runner_root_install_unsupported",
    "external_runner_bootstrap_abort",
    "external_runner_capability_blocked",
    "external_runner_signing_blocked",
}


def extract_phase_reuse_paths(matches: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Return de-duplicated successful/avoid/reminder paths from matches.

    Mirrors the rendering logic in ``render_phase_reuse`` so the gate and the
    human-readable phase-reuse file stay in sync.
    """
    reminders: list[str] = []
    avoid_paths: list[str] = []
    successful_paths: list[str] = []
    for item in (matches or [])[:5]:
        for key, target in [
            ("importantReminders", reminders),
            ("reminders", reminders),
            ("avoidPaths", avoid_paths),
            ("avoid", avoid_paths),
            ("successfulPaths", successful_paths),
            ("successfulPath", successful_paths),
        ]:
            for value in _as_list(item.get(key)):
                if value not in target and len(target) < 6:
                    target.append(value)
    return {
        "successfulPaths": [p for p in successful_paths if p not in avoid_paths][:5],
        "avoidPaths": avoid_paths[:6],
        "reminders": reminders[:6],
    }


def detect_repeated_failure(task_dir: Path) -> dict[str, Any]:
    """Detect a repeated same-category failure that should force reuse-first.

    Reads the latest evaluation result and counts how many recent iterations
    share the same blocking failure category. The operator's rule: signing /
    device / build / repeated-same-failure must read matched reuse and exhaust
    safe paths before asking the user or replanning.
    """
    from orchestrator.state import read_evaluation_json

    evaluation = read_evaluation_json(task_dir) or {}
    failed_checks = evaluation.get("failedChecks") or []
    pending = None
    state_path = task_dir / "runtime-state.json"
    try:
        state = json.loads(state_path.read_text(errors="ignore")) if state_path.exists() else {}
    except json.JSONDecodeError:
        state = {}
    pending = (state.get("pendingQuestion") or {}) if isinstance(state.get("pendingQuestion"), dict) else {}

    categories: list[str] = []
    for check in failed_checks:
        if isinstance(check, dict):
            cat = str(check.get("category") or "").strip().lower()
            if cat and cat != "unknown":
                categories.append(cat)
    pending_category = str(pending.get("category") or "").strip().lower()
    if pending_category:
        categories.append(pending_category)

    primary = next((c for c in categories if c in REUSE_GATE_FAILURE_CATEGORIES), None)
    if primary is None and categories:
        primary = categories[0]

    # Count how many recent iterations recorded the same blocking category.
    same_count = 0
    learnings_dir = task_dir / "logs" / "phase-learnings"
    if primary and learnings_dir.exists():
        for card in sorted(learnings_dir.glob("evaluator-iter-*.json")):
            try:
                data = json.loads(card.read_text(errors="ignore"))
            except json.JSONDecodeError:
                continue
            payload = data.get("payload") or {}
            if str(payload.get("nextAction") or "") == "ask_user" and str(payload.get("result") or "") == "blocked":
                same_count += 1

    sensitive_category = primary in REUSE_GATE_FAILURE_CATEGORIES if primary else False
    is_repeated = bool(primary) and (same_count >= 2 or sensitive_category)
    return {
        "detected": is_repeated,
        "category": primary,
        "isSensitiveCategory": sensitive_category,
        "sameCategoryAskUserCount": same_count,
    }


def compute_reuse_gate(task_dir: Path, phase: str, matches: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """Compute the reuse gate descriptor for a phase.

    The gate is the machine-checkable contract behind "read reuse before you
    act". It records whether an acknowledgement is required, what safe/avoid
    paths the agent must consider, and whether a repeated-failure condition
    forces exhausting reuse before ask_user/replan.
    """
    if matches is None:
        matches = search_knowledge(task_dir, phase)
    paths = extract_phase_reuse_paths(matches)
    high_confidence = [m for m in matches if str(m.get("confidence") or "").lower() == "high"]
    repeated = detect_repeated_failure(task_dir) if phase in REUSE_GATE_PHASES else {
        "detected": False,
        "category": None,
        "isSensitiveCategory": False,
        "sameCategoryAskUserCount": 0,
    }
    return {
        "phase": phase,
        "required": phase in REUSE_GATE_PHASES,
        "matchCount": len(matches),
        "highConfidenceCount": len(high_confidence),
        "safePaths": paths["successfulPaths"],
        "avoidPaths": paths["avoidPaths"],
        "reminders": paths["reminders"],
        "repeatedFailure": repeated,
        "phaseReusePath": rel_to_root(task_dir / "phase-reuse" / f"{phase}.md"),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }


def record_reuse_ack(
    task_dir: Path,
    phase: str,
    *,
    phase_reuse_read: bool,
    reuse_applied: Optional[list[str]] = None,
    reuse_ignored: Optional[list[str]] = None,
    decision: Optional[str] = None,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Record a reuse acknowledgement for a phase.

    This turns "I considered reuse" into a machine-checkable record. The agent
    must call this (via ``automind reuse-ack``) before a gated phase is allowed
    to proceed. For repeated-failure / signing / device / build cases the
    acknowledgement must show that matched safe reuse paths were applied or that
    an explicit, justified decision was made before escalating to ask_user.
    """
    from orchestrator.state import read_runtime_state as _read_state
    from orchestrator.state import update_runtime_state as _update_state

    state = _read_state(task_dir) or {}
    reuse_gate = state.get("reuseGate") if isinstance(state.get("reuseGate"), dict) else {}
    reuse_gate = dict(reuse_gate)
    gate = dict(reuse_gate.get(phase) or compute_reuse_gate(task_dir, phase))

    acknowledgement = {
        "phaseReuseRead": bool(phase_reuse_read),
        "reuseApplied": list(reuse_applied or []),
        "reuseIgnored": list(reuse_ignored or []),
        "decision": decision,
        "note": note,
        "recordedAt": datetime.now().isoformat(timespec="seconds"),
    }
    gate["acknowledged"] = bool(phase_reuse_read)
    gate["acknowledgement"] = acknowledgement
    reuse_gate[phase] = gate
    _update_state(task_dir, reuseGate=reuse_gate)
    return gate



def render_reuse_manifest(task_dir: Path, phase_results: Optional[dict[str, dict[str, Any]]] = None, reason: str = "task_start") -> str:
    if phase_results is None:
        phase_results = ensure_phase_reuse_contexts(task_dir, DEFAULT_PHASES, reason=reason)
    generated = datetime.now().isoformat(timespec="seconds")
    all_matches: dict[str, dict[str, Any]] = {}
    for result in phase_results.values():
        for match in result.get("matches", []):
            all_matches[str(match.get("id"))] = match

    lines = [
        "# Reuse Manifest",
        "",
        f"Generated at: {generated}",
        f"Reason: {reason}",
        "",
        "This file is a task-level reuse manifest: whole-task policy plus index pointers only. The actual phase-specific detail (matched values, successful/avoid paths, reminders) lives in `phase-reuse/<phase>.md`; this manifest does not duplicate it.",
        "",
        "## Policy",
        "",
        "- Current task artifacts and fresh evidence always win over historical reuse.",
        "- Reuse can become stale over time (toolchain/SDK/config/code may have changed since it was recorded). Treat each entry as a candidate way to fix or work around the current problem — a reference, not a guaranteed answer — and always verify against the actual current situation.",
        "- Read the phase-specific `phase-reuse/<phase>.md` before entering that phase.",
        "- Load raw knowledge files only when the index entry is relevant.",
        "- If a high-confidence successful path is ignored, record why in `TestCases.md`, `Plan.md`, `Delivery.md`, or `Validation.md` as appropriate.",
        "",
        "## Phase reuse files",
        "",
        "| Phase | File | Purpose | Matches |",
        "|-------|------|---------|---------|",
    ]
    for phase in DEFAULT_PHASES:
        result = phase_results.get(phase) or {}
        path = result.get("path") or (task_dir / "phase-reuse" / f"{phase}.md")
        matches = result.get("matches") or []
        lines.append(
            f"| {phase} | `{_rel(Path(path))}` | {PHASE_PURPOSES.get(phase, '-')} | {len(matches)} |"
        )

    if all_matches:
        lines.extend([
            "",
            "## Matched knowledge index entries",
            "",
            "Index pointers only. Read the per-phase `phase-reuse/<phase>.md` for the "
            "actual value, successful/avoid paths, and reminders; open the raw path "
            "only when a pointer is relevant.",
            "",
            "| ID | Applies to phases | Raw path (absolute) | Confidence |",
            "|----|-------------------|---------------------|------------|",
        ])
        for item in sorted(all_matches.values(), key=lambda x: int(x.get("_score") or 0), reverse=True)[:12]:
            applies = ", ".join(_as_list(item.get("phaseApplicability") or item.get("phases"))) or "-"
            lines.append(f"| {item.get('id', '-')} | {applies} | `{format_raw_path_for_reuse(item)}` | {item.get('confidence', '-')} |")
    else:
        lines.extend([
            "",
            "## Matched knowledge index entries",
            "",
            "- No relevant indexed knowledge matched this task yet.",
        ])

    lines.extend([
        "",
        "## Raw knowledge locations",
        "",
        f"- Workspace index: `{_rel(KNOWLEDGE_INDEX_PATH)}`",
        f"- Workspace raw directory: `{_rel(KNOWLEDGE_RAW_DIR)}`",
        f"- Runtime/global index: `{_rel(GLOBAL_KNOWLEDGE_INDEX_PATH)}`",
        f"- Runtime/global raw directory: `{_rel(GLOBAL_KNOWLEDGE_RAW_DIR)}`",
        "",
        "If an index entry points to a raw file, that raw file should be single-responsibility: e.g. iOS build only, Android build only, iOS signing only, visual verification only.",
    ])
    return "\n".join(lines).rstrip() + "\n"




def _iter_index_lines(path: Path) -> list[tuple[int, str, Optional[dict[str, Any]], Optional[str]]]:
    """Return parsed JSONL rows with parse errors for evaluation."""
    rows: list[tuple[int, str, Optional[dict[str, Any]], Optional[str]]] = []
    if not path.exists():
        return rows
    for line_no, line in enumerate(path.read_text(errors="ignore").splitlines(), start=1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            rows.append((line_no, raw, None, f"invalid_json: {exc.msg}"))
            continue
        if not isinstance(data, dict):
            rows.append((line_no, raw, None, "record_not_object"))
            continue
        rows.append((line_no, raw, data, None))
    return rows


def _issue(severity: str, code: str, message: str, *, record_id: str = "", path: str = "", line: Optional[int] = None) -> dict[str, Any]:
    item: dict[str, Any] = {"severity": severity, "code": code, "message": message}
    if record_id:
        item["id"] = record_id
    if path:
        item["path"] = path
    if line is not None:
        item["line"] = line
    return item


def evaluate_knowledge_store(*, max_raw_chars: int = 60_000, min_raw_chars: int = 40) -> dict[str, Any]:
    """Evaluate knowledge index/raw health without modifying files."""
    issues: list[dict[str, Any]] = []
    index_specs = [
        ("runtime", GLOBAL_KNOWLEDGE_INDEX_PATH, AUTOMIND_ROOT),
        ("workspace", KNOWLEDGE_INDEX_PATH, AUTOMIND_WORKSPACE_ROOT),
    ]
    raw_roots = [
        ("runtime", GLOBAL_KNOWLEDGE_RAW_DIR, AUTOMIND_ROOT),
        ("workspace", KNOWLEDGE_RAW_DIR, AUTOMIND_WORKSPACE_ROOT),
    ]
    allowed_phases = set(DEFAULT_PHASES) | {"all"}
    allowed_surfaces = set(SURFACE_KEYWORDS) | {"all", "script", "web", "backend", "frontend", "runtime", "device"}
    allowed_confidence = {"high", "medium", "low"}
    seen_ids: dict[str, list[tuple[str, int]]] = {}
    referenced_raw: set[Path] = set()
    record_count = 0
    index_count = 0

    for source, index_path, root in index_specs:
        if not index_path.exists():
            continue
        index_count += 1
        rows = _iter_index_lines(index_path)
        for line_no, _raw, data, parse_error in rows:
            if parse_error:
                issues.append(_issue("error", "invalid_index_record", parse_error, path=_rel(index_path), line=line_no))
                continue
            assert data is not None
            record_count += 1
            record_id = str(data.get("id") or "").strip()
            if not record_id:
                issues.append(_issue("error", "missing_id", "knowledge index record is missing id", path=_rel(index_path), line=line_no))
                record_id = f"{_rel(index_path)}:{line_no}"
            seen_ids.setdefault(record_id, []).append((_rel(index_path), line_no))
            if not str(data.get("title") or "").strip():
                issues.append(_issue("warning", "missing_title", "record should include a short title", record_id=record_id, path=_rel(index_path), line=line_no))
            if not str(data.get("value") or data.get("description") or "").strip():
                issues.append(_issue("warning", "missing_value", "record should include value or description", record_id=record_id, path=_rel(index_path), line=line_no))
            confidence = str(data.get("confidence") or "").lower()
            if confidence and confidence not in allowed_confidence:
                issues.append(_issue("warning", "invalid_confidence", f"confidence should be one of {sorted(allowed_confidence)}", record_id=record_id, path=_rel(index_path), line=line_no))
            phases = {item.lower() for item in _as_list(data.get("phaseApplicability") or data.get("phases"))}
            invalid_phases = sorted(phases - allowed_phases)
            if invalid_phases:
                issues.append(_issue("warning", "invalid_phase", f"unknown phase(s): {', '.join(invalid_phases)}", record_id=record_id, path=_rel(index_path), line=line_no))
            if not phases:
                issues.append(_issue("warning", "missing_phase_applicability", "record should declare phaseApplicability", record_id=record_id, path=_rel(index_path), line=line_no))
            surfaces = {item.lower() for item in _as_list(data.get("surfaces") or data.get("surface"))}
            invalid_surfaces = sorted(surfaces - allowed_surfaces)
            if invalid_surfaces:
                issues.append(_issue("info", "unknown_surface", f"non-standard surface(s): {', '.join(invalid_surfaces)}", record_id=record_id, path=_rel(index_path), line=line_no))
            if not _as_list(data.get("triggers") or data.get("reuseTrigger")):
                issues.append(_issue("info", "missing_triggers", "record has no triggers; matching may rely only on phase/project/surface", record_id=record_id, path=_rel(index_path), line=line_no))
            raw_path = resolve_raw_path({**data, "_source": source})
            if raw_path:
                referenced_raw.add(raw_path.resolve())
                raw_text = str(data.get("rawPath") or data.get("path") or "")
                if ".." in Path(raw_text).parts:
                    issues.append(_issue("error", "unsafe_raw_path", "rawPath must not contain '..'", record_id=record_id, path=_rel(index_path), line=line_no))
                if raw_path.exists():
                    if not raw_path.is_file():
                        issues.append(_issue("error", "raw_not_file", "rawPath exists but is not a file", record_id=record_id, path=_rel(raw_path), line=line_no))
                    else:
                        size = raw_path.stat().st_size
                        if size > max_raw_chars:
                            issues.append(_issue("warning", "raw_too_large", f"raw file is large ({size} bytes); consider splitting", record_id=record_id, path=_rel(raw_path), line=line_no))
                        if size < min_raw_chars:
                            issues.append(_issue("warning", "raw_too_small", f"raw file is very small ({size} bytes); consider no_action or richer evidence", record_id=record_id, path=_rel(raw_path), line=line_no))
                else:
                    issues.append(_issue("error", "missing_raw", "rawPath does not exist", record_id=record_id, path=_rel(raw_path), line=line_no))
            else:
                issues.append(_issue("warning", "missing_raw_path", "record has no rawPath/path; phase-reuse can only show index summary", record_id=record_id, path=_rel(index_path), line=line_no))

    for record_id, locations in sorted(seen_ids.items()):
        if len(locations) > 1:
            loc_text = ", ".join(f"{path}:{line}" for path, line in locations)
            issues.append(_issue("warning", "duplicate_id", f"duplicate id; latest load wins: {loc_text}", record_id=record_id))

    for source, raw_root, _root in raw_roots:
        if not raw_root.exists():
            continue
        for raw_file in raw_root.rglob("*.md"):
            resolved = raw_file.resolve()
            if resolved not in referenced_raw:
                issues.append(_issue("info", "orphan_raw", "raw file is not referenced by any loaded index record", path=_rel(raw_file)))
            size = raw_file.stat().st_size
            if size > max_raw_chars:
                issues.append(_issue("warning", "raw_too_large", f"raw file is large ({size} bytes); consider splitting", path=_rel(raw_file)))

    severity_counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
    for item in issues:
        severity = str(item.get("severity") or "info")
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
    return {
        "ok": severity_counts.get("error", 0) == 0,
        "recordCount": record_count,
        "indexCount": index_count,
        "referencedRawCount": len(referenced_raw),
        "issueCount": len(issues),
        "severityCounts": severity_counts,
        "issues": issues,
    }


def find_knowledge_record(record_id: str) -> Optional[dict[str, Any]]:
    """Find one knowledge index record by id."""
    for record in load_knowledge_index():
        if str(record.get("id") or "") == record_id:
            return record
    return None


def summarize_knowledge_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a stable, CLI-friendly view of an index record without raw content."""
    raw_path = resolve_raw_path(record)
    return {
        "id": record.get("id"),
        "title": record.get("title"),
        "description": record.get("description"),
        "value": record.get("value"),
        "rawPath": _rel(raw_path),
        "confidence": record.get("confidence"),
        "phaseApplicability": _as_list(record.get("phaseApplicability") or record.get("phases")),
        "taskTypes": _as_list(record.get("taskTypes") or record.get("taskType")),
        "projects": _as_list(record.get("projects") or record.get("project")),
        "surfaces": _as_list(record.get("surfaces") or record.get("surface")),
        "triggers": _as_list(record.get("triggers") or record.get("reuseTrigger")),
        "successfulPaths": _as_list(record.get("successfulPaths") or record.get("successfulPath")),
        "avoidPaths": _as_list(record.get("avoidPaths") or record.get("avoid")),
        "importantReminders": _as_list(record.get("importantReminders") or record.get("reminders")),
        "evidenceRefs": _as_list(record.get("evidenceRefs") or record.get("evidence")),
        "source": record.get("_source"),
        "indexPath": record.get("_indexPath"),
        "score": record.get("_score"),
        "matchReasons": _as_list(record.get("_matchReasons")),
    }


def read_raw_excerpt(record: dict[str, Any], max_chars: int = 4000) -> Optional[str]:
    """Read a bounded raw excerpt for explicit CLI inspection only."""
    raw_path = resolve_raw_path(record)
    if not raw_path or not raw_path.exists() or not raw_path.is_file():
        return None
    text = raw_path.read_text(errors="ignore")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n...[truncated raw knowledge]..."


def append_knowledge_index_record(record: dict[str, Any], index_path: Path = KNOWLEDGE_INDEX_PATH) -> Path:
    """Upsert one compact index record. Raw knowledge should live elsewhere."""
    ensure_dir(index_path.parent)
    clean = dict(record)
    clean.setdefault("schema", "automind.knowledge_index.v1")
    clean.setdefault("createdAt", datetime.now().isoformat(timespec="seconds"))
    clean.setdefault("confidence", "medium")
    record_id = str(clean.get("id") or "").strip()
    new_line = json.dumps(clean, ensure_ascii=False)
    preserved: list[str] = []
    replaced = False
    if index_path.exists():
        for line in index_path.read_text(errors="ignore").splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                existing = json.loads(raw)
            except json.JSONDecodeError:
                preserved.append(line)
                continue
            if record_id and str(existing.get("id") or "").strip() == record_id:
                if not replaced:
                    preserved.append(new_line)
                    replaced = True
                continue
            preserved.append(line)
    if not replaced:
        preserved.append(new_line)
    index_path.write_text("\n".join(preserved).rstrip() + "\n")
    return index_path


def append_summary_knowledge_candidate(
    task_dir: Path,
    task_code: str,
    successful_paths: list[dict[str, Any]],
    avoid_paths: list[dict[str, Any]],
    summary_path: Path,
) -> Optional[Path]:
    """Best-effort bridge from existing summary extraction to new index/raw.

    It writes only compact index records. Detailed long-form knowledge remains in
    task summary/run cards until a future AI refiner promotes it to raw files.
    """
    if not successful_paths and not avoid_paths:
        return None
    state = read_runtime_state(task_dir) or {}
    task_type = str(state.get("taskType") or "")
    project = AUTOMIND_WORKSPACE_ROOT.name
    surfaces = set(["summary"])
    text = " ".join([json.dumps(successful_paths, ensure_ascii=False), json.dumps(avoid_paths, ensure_ascii=False)]).lower()
    for surface, keywords in SURFACE_KEYWORDS.items():
        if any(keyword.lower() in text for keyword in keywords):
            surfaces.add(surface)
    if task_type:
        surfaces.add(task_type)
    record = {
        "id": f"task-{task_code}-summary",
        "title": f"Summary reuse candidate from {task_code}",
        "rawPath": rel_to_root(summary_path),
        "description": "Task summary contains evidence-backed successful/avoid paths worth checking before similar work.",
        "value": "Avoid repeating prior command discovery or failed verification paths.",
        "taskTypes": [task_type] if task_type else [],
        "projects": [project],
        "surfaces": sorted(surfaces),
        "phaseApplicability": ["testcases", "plan", "generator", "evaluator", "summary"],
        "triggers": [task_type, project, task_code, "build", "test", "verification"],
        "confidence": "medium",
        "evidenceRefs": [rel_to_root(summary_path)],
        "successfulPaths": [str(item.get("command") or item.get("purpose") or "").strip() for item in successful_paths[:3] if str(item.get("command") or item.get("purpose") or "").strip()],
        "avoidPaths": [str(item.get("path") or item.get("reason") or "").strip() for item in avoid_paths[:3] if str(item.get("path") or item.get("reason") or "").strip()],
        "importantReminders": ["Read the referenced summary only if this task matches the same project/task type/surface."],
    }
    return append_knowledge_index_record(record)
