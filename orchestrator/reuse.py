"""Prompt rendering and reuse-context helpers for AutoMind.

This module owns reusable summary/preloaded context assembly and prompt file
rendering. Runtime task orchestration stays in ``orchestrator.main``.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from orchestrator.config import (
    AUTOMIND_ROOT,
    LOCAL_REUSE_INDEX_PATH,
    PRELOADED_OVERVIEW_MAX_CHARS_PER_PACK,
    PRELOADED_SUMMARY_PREFIXES,
    PROMPTS_DIR,
    SUMMARY_LESSONS_PATH,
)
from orchestrator.console import read_head, read_tail
from orchestrator.accumulate import parse_yaml_frontmatter
from orchestrator.state import ensure_dir, read_runtime_state, rel_to_root, update_runtime_state
from orchestrator.knowledge_index import ensure_phase_reuse_contexts, render_reuse_manifest




def detect_preferred_language(text: object) -> str:
    value = str(text or "")
    cjk = sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in value if ("a" <= ch.lower() <= "z"))
    if cjk >= 4 and cjk >= latin * 0.15:
        return "zh"
    return "en"


def runtime_language_instruction(user_input: object) -> str:
    language = detect_preferred_language(user_input)
    if language == "zh":
        return (
            "## Runtime communication language\n"
            "The user's request is primarily Chinese. Communicate with the user in Chinese by default: "
            "ask_user questions, concise status summaries, final replies, and user-facing explanations should be Chinese. "
            "Keep code identifiers, file paths, command names, enum values, event names, and existing English technical terms unchanged. "
            "Public AutoMind artifact headings/templates may remain English when required by the workflow, but explanatory content for this task should be Chinese where practical.\n"
        )
    return (
        "## Runtime communication language\n"
        "The user's request is primarily English or language-neutral. Communicate with the user in English by default, while preserving code identifiers, paths, command names, and domain terms.\n"
    )


def apply_runtime_language_instruction(prompt: str, user_input: object) -> str:
    instruction = runtime_language_instruction(user_input)
    if prompt.startswith("## Runtime communication language"):
        return prompt
    return instruction + "\n" + prompt


def render_prompt_template(template_name: str, **kwargs) -> str:
    """Read and render a prompt template with simple placeholder replacement.

    Use explicit ``{name}`` replacement instead of ``str.format`` so prompt
    templates may contain JSON examples or code blocks with literal braces.
    """
    template_path = PROMPTS_DIR / template_name
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    text = template_path.read_text()
    for key, value in kwargs.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def write_rendered_prompt(iter_log_dir: Path, name: str, prompt: str) -> Path:
    """Persist the exact prompt sent to an agent for observability/replay."""
    ensure_dir(iter_log_dir)
    path = iter_log_dir / name
    path.write_text(prompt)
    return path


def preloaded_prefixes_for_task_type(task_type: Optional[str]) -> list[str]:
    """Return preloaded pack prefixes relevant to a task type.

    Prefix-based discovery keeps `summaries/preloaded/` extensible: adding a new
    `ios-*`, `android-*`, `client-*`, or `common-*` pack does not require editing
    a hard-coded file list.
    """
    normalized = (task_type or "").lower()
    keys: list[str] = ["common"]
    if normalized in {"ios", "dual"}:
        keys.extend(["mobile_common", "ios"])
    if normalized in {"android", "dual"}:
        keys.extend(["mobile_common", "android"])

    prefixes: list[str] = []
    seen: set[str] = set()
    for key in keys:
        for prefix in PRELOADED_SUMMARY_PREFIXES.get(key, []):
            if prefix in seen:
                continue
            seen.add(prefix)
            prefixes.append(prefix)
    return prefixes


def preloaded_summary_files_for_task_type(task_type: Optional[str]) -> list[Path]:
    """Return public-safe preloaded summary files relevant to a task type."""
    preloaded_root = AUTOMIND_ROOT / "summaries" / "preloaded"
    if not preloaded_root.exists():
        return []

    prefixes = preloaded_prefixes_for_task_type(task_type)
    files = [child for child in preloaded_root.iterdir() if child.is_file() and child.suffix == ".md" and child.name != "README.md"]
    paths: list[Path] = []
    seen: set[Path] = set()
    for prefix in prefixes:
        for path in sorted(files, key=lambda p: p.name):
            if not path.stem.startswith(prefix):
                continue
            if path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def _parse_simple_frontmatter(text: str) -> tuple[dict, str]:
    """Parse the small YAML-like frontmatter used by preloaded README files.

    This intentionally supports only simple scalar fields and list fields; it
    avoids adding a YAML dependency to AutoMind.
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end].splitlines()
    body = text[end + 5 :]
    meta: dict[str, object] = {}
    current_key = ""
    for line in raw:
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
            if value:
                meta[current_key] = value.strip('"').strip("'")
            else:
                meta[current_key] = []
    return meta, body


def _preloaded_pack_overview(path: Path, max_chars: int = PRELOADED_OVERVIEW_MAX_CHARS_PER_PACK) -> str:
    """Return a compact progressive-loading index row for one preloaded pack.

    Reuse.md should be a navigation index, not a prompt dump. One preloaded md
    contributes its title, runtime-relative path, and one sentence summary. The
    agent reads the full pack only when the current task needs that capability.
    """
    text = path.read_text(errors="ignore") if path.exists() else ""
    meta, body = _parse_simple_frontmatter(text)
    body_title = ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            body_title = stripped.lstrip("#").strip()
            break
    title = body_title or str(meta.get("name") or path.stem)
    summary = str(meta.get("description") or "").strip()
    if not summary:
        for line in body.splitlines():
            stripped = line.strip().lstrip("- ").strip()
            if stripped and not stripped.startswith("#"):
                summary = stripped
                break
    summary = " ".join(summary.split())
    if len(summary) > 240:
        summary = summary[:237].rstrip() + "..."
    try:
        rel_path = path.relative_to(AUTOMIND_ROOT)
    except ValueError:
        rel_path = path
    return f"### {path.stem} — {title}\n- Path: `{rel_path}`\n- Summary: {summary}\n- Load: read this file on demand only when this capability is needed."


def accumulated_business_summary_files(limit_files: int = 40) -> list[Path]:
    """Return local accumulated business summaries when they exist on this machine.

    These files are local/private reuse hints. They are allowed in runtime
    ``Reuse.md`` because the user explicitly keeps them in this checkout, but they
    remain excluded from public skill exports by default.
    """
    business_root = AUTOMIND_ROOT / "summaries" / "accumulated" / "business"
    if not business_root.exists():
        return []
    files = [
        path for path in sorted(business_root.rglob("*.md"))
        if path.is_file() and not path.name.endswith("-index.md")
    ]
    return files[:limit_files]


def accumulated_technical_summary_files(limit_files: int = 40) -> list[Path]:
    """Return machine-global accumulated technical summaries when present.

    These are cross-project, business-agnostic lessons accumulated by task
    summaries in the AutoMind runtime install. They are public-safe but local to
    this machine (not maintainer-distributed like preloaded packs).
    """
    technical_root = AUTOMIND_ROOT / "summaries" / "accumulated" / "technical"
    if not technical_root.exists():
        return []
    files = [
        path
        for path in sorted(technical_root.rglob("*.md"))
        if path.is_file() and path.name != "README.md" and not path.name.endswith("-index.md")
    ]
    return files[:limit_files]


def build_accumulated_technical_context(limit_chars: int = 4_000) -> str:
    """Build bounded machine-global accumulated technical context for Reuse.md."""
    files = accumulated_technical_summary_files()
    if not files:
        return ""
    per_file_limit = max(600, limit_chars // max(len(files), 1))
    sections: list[str] = []
    total = 0
    for path in files:
        try:
            raw = path.read_text(errors="ignore")
        except OSError:
            continue
        _, body = parse_yaml_frontmatter(raw)
        body = body.strip()
        if not body:
            continue
        snippet = body[:per_file_limit]
        try:
            rel_path = path.relative_to(AUTOMIND_ROOT)
        except ValueError:
            rel_path = path
        section = f"### {rel_path}\n\n{snippet}"
        if total + len(section) > limit_chars and sections:
            break
        sections.append(section)
        total += len(section)
    return "\n\n".join(sections).strip()


def build_accumulated_business_context(limit_chars: int = 6_000) -> str:
    """Build bounded local/private accumulated business context for Reuse.md."""
    files = accumulated_business_summary_files()
    if not files:
        return ""
    per_file_limit = max(600, limit_chars // max(len(files), 1))
    sections: list[str] = []
    total = 0
    for path in files:
        try:
            raw = path.read_text(errors="ignore")
        except OSError:
            continue
        _, body = parse_yaml_frontmatter(raw)
        body = body.strip()
        if not body:
            continue
        snippet = body[:per_file_limit]
        try:
            rel_path = path.relative_to(AUTOMIND_ROOT)
        except ValueError:
            rel_path = path
        section = f"### {rel_path}\n\n{snippet}"
        if total + len(section) > limit_chars and sections:
            break
        sections.append(section)
        total += len(section)
    return "\n\n".join(sections).strip()


def build_preloaded_context(task_type: Optional[str], limit_chars: int = 6_000) -> str:
    """Build a progressive-loading preloaded index for `summaries/preloaded/*.md`.

    Reuse.md gets short pack overviews and paths only. The model should read a
    specific pack README later when it needs compile/build, real-device,
    UI-automation, log-reading, or platform-specific guidance.
    """
    files = preloaded_summary_files_for_task_type(task_type)
    if not files:
        return ""

    sections: list[str] = []
    total = 0
    for path in files:
        section = _preloaded_pack_overview(path)
        if total + len(section) > limit_chars and sections:
            break
        sections.append(section)
        total += len(section)
    return "\n\n".join(sections).strip()


def build_reuse_context(task_type: Optional[str] = None, limit_chars: int = 16_000) -> str:
    """Build bounded reuse context from local summaries and curated seed packs.

    This is local-machine memory, not public product documentation. It helps the
    next task avoid repeated environment guesses, installs, and failed tactics.
    """
    sections = []
    local_limit = int(limit_chars * 0.5)
    preloaded_limit = max(6_000, int(limit_chars * 0.25))
    remaining = max(0, limit_chars - local_limit - preloaded_limit)
    technical_limit = remaining // 2
    business_limit = remaining - technical_limit
    index_tail = read_tail(LOCAL_REUSE_INDEX_PATH, local_limit // 2)
    lessons_tail = read_tail(SUMMARY_LESSONS_PATH, local_limit // 2)
    if index_tail:
        sections.append("## Local reuse index tail\n\n" + index_tail.strip())
    if lessons_tail:
        sections.append("## Lessons learned tail\n\n" + lessons_tail.strip())
    preloaded_context = build_preloaded_context(task_type, preloaded_limit)
    if preloaded_context:
        sections.append(
            "## Preloaded seed index (progressive loading)\n\n"
            "These are public-safe generic playbooks selected by task type using pack prefixes. "
            "This section contains a compact index of preloaded summary file paths and one-line descriptions; read a specific file on demand when the task needs that capability. "
            "They are hints only; current Requirements.md, TestCases.md, and fresh evidence win.\n\n"
            "Path rule: `Path` is relative to the AutoMind runtime root (`$AUTOMIND_HOME` when installed, or the directory containing `automind.sh`), not the target app project or `.automind/summary/`. "
            "If the agent cwd is the app project, resolve it as `<AutoMind runtime root>/summaries/preloaded/...`.\n\n"
            + preloaded_context
        )
    technical_context = build_accumulated_technical_context(technical_limit)
    if technical_context:
        sections.append(
            "## Accumulated technical lessons (machine-global)\n\n"
            "Cross-project, business-agnostic lessons accumulated by past task summaries on this machine "
            "(AutoMind runtime `summaries/accumulated/technical/`). Treat them as local hints only; "
            "current Requirements.md, TestCases.md, and fresh evidence win.\n\n"
            + technical_context
        )
    business_context = build_accumulated_business_context(business_limit)
    if business_context:
        sections.append(
            "## Local accumulated business/project summaries\n\n"
            "These local/private project lessons exist in this AutoMind runtime checkout and are included for this machine's Reuse.md. "
            "They may contain project-specific commands, build paths, and domain assumptions. Treat them as local hints only; current Requirements.md, TestCases.md, and fresh evidence win. "
            "Public skill exports still exclude summaries/accumulated/business/** by default.\n\n"
            + business_context
        )
    return "\n\n".join(sections).strip()


def write_reuse_context(task_dir: Path, reason: str = "task_start") -> Path:
    """Write task-level `Reuse.md` manifest and phase-specific reuse files.

    `Reuse.md` is intentionally a compact navigation file. Long reusable
    knowledge belongs in index/raw stores and `phase-reuse/<phase>.md`, not in
    this manifest.
    """
    reuse_path = task_dir / "Reuse.md"
    phase_results = ensure_phase_reuse_contexts(task_dir, reason=reason)
    body = render_reuse_manifest(task_dir, phase_results=phase_results, reason=reason)

    # Backward-compatible fallback: if no knowledge index matches yet, preserve a
    # tiny source overview so older prompts still know where legacy reuse lives,
    # but avoid dumping long lessons into the task context.
    state = read_runtime_state(task_dir) or {}
    task_type = state.get("taskType")
    legacy_context = build_reuse_context(task_type=task_type, limit_chars=10_000)
    if legacy_context:
        body += "\n## Legacy reuse sources overview\n\n"
        body += (
            "The indexed knowledge system is authoritative for routing. "
            "The legacy sources below are included only as short migration hints; "
            "do not treat them as full context.\n\n"
        )
        body += legacy_context[:10_000].rstrip() + "\n"

    reuse_path.write_text(body)
    update_runtime_state(
        task_dir,
        reuseContext=str(reuse_path),
        reuseContextUpdatedAt=datetime.now().isoformat(timespec="seconds"),
        phaseReuseDir=str(task_dir / "phase-reuse"),
    )
    return reuse_path
