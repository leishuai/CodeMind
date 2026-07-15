"""Summary extraction, reuse-path extraction, and AI summary refinement."""
from __future__ import annotations

from datetime import datetime
import json
import os
import re
from pathlib import Path
from typing import Optional

from orchestrator.agents import run_agent
from orchestrator.artifacts import (
    evidence_path_exists,
    extract_first_command_block,
    normalize_evidence_refs,
    normalize_test_result_value,
    primary_requirements_path,
    requirement_contract_paths,
    resolve_task_artifact_path,
)
from orchestrator.completion import normalize_verification_unblock_changes
from orchestrator.accumulate import sink_accumulated_lessons
from orchestrator.config import AUTOMIND_WORKSPACE_ROOT, LOCAL_REUSE_INDEX_PATH, SUMMARY_DIR, SUMMARY_LESSONS_PATH
from orchestrator.console import read_tail, success, warn
from orchestrator.reuse import render_prompt_template, write_rendered_prompt
from orchestrator.records import check_task_records
from orchestrator.reports import print_report_manifest
from orchestrator.session.trace import write_trace
from orchestrator.state import ensure_dir, get_runtime_state_path, get_task_dir, read_evaluation_json, read_runtime_state, rel_to_root, update_runtime_state
from orchestrator.workflow import check_workflow_consistency
from orchestrator.workflow_state import finalize_workflow_state_if_terminal
from orchestrator.hooks import run_after_phase_hooks, run_before_phase_hooks
from orchestrator.knowledge_index import KNOWLEDGE_RAW_DIR, append_knowledge_index_record, append_summary_knowledge_candidate


def append_run_card_to_summary_store(task_dir: Path, card: dict) -> Path:
    ensure_dir(SUMMARY_DIR)
    path = SUMMARY_DIR / "run-cards.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(card, ensure_ascii=False) + "\n")
    return path


def read_run_cards(limit: int = 80) -> list[dict]:
    path = SUMMARY_DIR / "run-cards.jsonl"
    if not path.exists():
        return []
    cards: list[dict] = []
    for line in path.read_text(errors="ignore").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            cards.append(item)
    return cards[-limit:]


def build_improve_suggestions(limit: int = 80) -> list[dict]:
    cards = read_run_cards(limit=limit)
    suggestions: list[dict] = []
    if not cards:
        return suggestions
    runtime_downgrades = [c for c in cards if c.get("runtimeDowngradeCount", 0)]
    if len(runtime_downgrades) >= 2:
        suggestions.append({
            "area": "runtime-proof",
            "priority": "high",
            "suggestion": "Multiple recent runs downgraded or missed runtime/device proof; strengthen TestCases/phase3 prompts or add project-specific runtime verifier scripts.",
            "evidence": [c.get("taskCode") for c in runtime_downgrades[-5:]],
        })
    repeated_failures: dict[str, int] = {}
    for card in cards:
        for item in card.get("failedAttempts", []) or []:
            category = item.get("failureCategory") or "unknown"
            repeated_failures[category] = repeated_failures.get(category, 0) + 1
    for category, count in sorted(repeated_failures.items(), key=lambda kv: kv[1], reverse=True)[:5]:
        if count >= 2 and category != "none":
            suggestions.append({
                "area": "process-eval",
                "priority": "medium",
                "suggestion": f"Failure category `{category}` repeated {count} times; consider adding a process-check rule, prompt guard, or reusable skill note.",
                "evidence": [c.get("taskCode") for c in cards if any((i.get("failureCategory") or "unknown") == category for i in (c.get("failedAttempts") or []))][-5:],
            })
    high_conf_paths = [c for c in cards if any((p.get("confidence") == "high") for p in (c.get("successfulPaths") or []))]
    if len(high_conf_paths) >= 2:
        suggestions.append({
            "area": "reuse",
            "priority": "medium",
            "suggestion": "Recent runs contain high-confidence successful paths; consider promoting repeated project-local commands/patterns into Reuse.md, phase docs, or a CodeMind skill note after review.",
            "evidence": [c.get("taskCode") for c in high_conf_paths[-5:]],
        })
    return suggestions


def render_improve_suggestions(limit: int = 80) -> str:
    suggestions = build_improve_suggestions(limit=limit)
    lines = ["# CodeMind Improve Suggestions", ""]
    if not suggestions:
        lines.append("- No suggestions yet. Finish more tasks and run `automind summary <task>` to populate run cards.")
        return "\n".join(lines)
    for idx, item in enumerate(suggestions, start=1):
        lines.append(f"## {idx}. {item.get('area')} [{item.get('priority')}]" )
        lines.append(f"- Suggestion: {item.get('suggestion')}")
        evidence = item.get("evidence") or []
        lines.append(f"- Evidence tasks: {', '.join(str(e) for e in evidence) if evidence else '-'}")
        lines.append("")
    return "\n".join(lines).rstrip()


def infer_reuse_confidence(final_result: str, evidence_refs: list[str], completion_report: dict) -> str:
    """Conservatively score whether a path should be reused by future tasks."""
    if final_result == "pass" and completion_report.get("result") == "pass" and evidence_refs:
        return "high"
    if final_result == "pass" and evidence_refs:
        return "medium"
    if evidence_refs:
        return "low"
    return "low"


def summarize_evidence_refs_for_reuse(task_dir: Path, refs: list[str], limit: int = 4) -> str:
    """Return concise evidence refs, preferring refs that exist."""
    if not refs:
        return "-"
    ordered: list[str] = []
    for ref in refs:
        if ref and ref not in ordered:
            ordered.append(ref)
    existing = [ref for ref in ordered if evidence_path_exists(task_dir, ref)]
    chosen = (existing or ordered)[:limit]
    return ", ".join(f"`{rel_to_root(resolve_task_artifact_path(task_dir, ref) or ref)}`" for ref in chosen)


def collect_commands_from_iter_dirs(iter_dirs: list[Path]) -> list[dict]:
    """Collect command snippets from iteration `commands.md` files."""
    commands: list[dict] = []
    for iter_dir in iter_dirs:
        cmd_path = iter_dir / "commands.md"
        if not cmd_path.exists():
            continue
        text = cmd_path.read_text(errors="ignore")
        command = extract_first_command_block(text)
        if not command:
            continue
        cwd_match = re.search(r"cwd:\s*`?([^`\n]+)`?", text, flags=re.IGNORECASE)
        commands.append({
            "iteration": iter_dir.name,
            "command": command,
            "cwd": cwd_match.group(1).strip() if cwd_match else "-",
            "path": cmd_path,
        })
    return commands


def extract_manual_intervention_lessons(logs_dir: Path, limit: int = 6) -> list[str]:
    """Recover lessons from human-intervention log dirs the iter-* scan misses.

    When an engineer manually unblocks a task (clearing a build cache, cleaning
    a working tree, forcing a full rebuild) the evidence lands in sibling dirs
    such as ``logs/manual-cache-reset/``, ``logs/cleanup/`` or
    ``logs/manual-cleanup-*/`` — never inside ``logs/iter-*/commands.md``. The
    deterministic reuse extractor therefore loses these high-value lessons, and
    if the AI summary refiner is unavailable (quota/timeout) they evaporate
    entirely. This scanner makes those manual-intervention dirs first-class
    reusable signals without depending on the AI refiner.
    """
    if not logs_dir.exists():
        return []
    lessons: list[str] = []
    # Match human-intervention dirs: manual-*, cleanup, *-cleanup, cache-reset.
    candidate_dirs = sorted(
        d for d in logs_dir.glob("*")
        if d.is_dir() and (
            d.name.startswith("manual")
            or "cleanup" in d.name
            or "cache-reset" in d.name
        )
    )
    for d in candidate_dirs:
        artifacts = sorted(
            p.name for p in d.glob("*")
            if p.is_file()
        )
        if not artifacts:
            continue
        # Keep the lesson concise but actionable: name the intervention dir and
        # its evidence files so a future task can read the exact recovery path.
        shown = ", ".join(artifacts[:6])
        more = f" (+{len(artifacts) - 6} more)" if len(artifacts) > 6 else ""
        lessons.append(
            f"Manual intervention recorded under `{rel_to_root(d)}/` "
            f"(evidence: {shown}{more}); a human had to bypass a blocker here. "
            f"Read these logs before re-running the same step — the standard "
            f"iter-* extractor does not capture this recovery path."
        )
        if len(lessons) >= limit:
            break
    return lessons


def extract_validation_reuse_lines(validation_text: str, heading: str, limit: int = 6) -> list[str]:
    """Extract short bullet lines under a Validation.md reuse/avoid heading."""
    lines: list[str] = []
    pattern = re.compile(rf"^[-*]\s*(?:\*\*)?{re.escape(heading)}(?:\*\*)?\s*:?\s*(.*)$", flags=re.IGNORECASE | re.MULTILINE)
    for match in pattern.finditer(validation_text or ""):
        value = match.group(1).strip()
        if value:
            lines.append(value)
    # Also support headings followed by bullets.
    section_pattern = re.compile(rf"^#+\s*{re.escape(heading)}\s*$", flags=re.IGNORECASE | re.MULTILINE)
    section_match = section_pattern.search(validation_text or "")
    if section_match:
        start = section_match.end()
        next_heading = re.search(r"^#+\s+", validation_text[start:], flags=re.MULTILINE)
        section = validation_text[start:start + next_heading.start()] if next_heading else validation_text[start:]
        for line in section.splitlines():
            stripped = line.strip()
            if stripped.startswith(("- ", "* ")):
                lines.append(stripped[2:].strip())
    deduped: list[str] = []
    for line in lines:
        if line and line not in deduped:
            deduped.append(line[:300])
    return deduped[:limit]


def _command_core(command: str) -> str:
    """Return a short identifying core of a command (executable + subcommand).

    e.g. ``xcodebuild test -scheme ...`` -> ``xcodebuild test``. Used to match a
    command against blocked-path signatures without over-matching on short words.
    """
    tokens = [t for t in str(command or "").strip().split() if not t.startswith("-")]
    return " ".join(tokens[:2]).lower()


def _command_is_blocked(command: str, blocked_signatures: list[str]) -> bool:
    """True when a command's core appears inside any blocked-path signature."""
    core = _command_core(command)
    if not core:
        return False
    for sig in blocked_signatures:
        sig_lower = str(sig or "").strip().lower()
        if sig_lower and core in sig_lower:
            return True
    return False


def _select_latest_non_blocked_command(commands: list[dict], blocked_signatures: list[str]) -> Optional[dict]:
    """Pick the most recent command that is not associated with a blocked path.

    P0-4 anti-pollution: a blocked/failed verification command (e.g. an
    `xcodebuild test` run that hit Root install) must never be promoted into
    successfulPaths just because the overall task result is pass.
    """
    for record in reversed(commands):
        if not _command_is_blocked(record.get("command", ""), blocked_signatures):
            return record
    return None


def _collect_cached_ui_path_records(task_dir: Path) -> list[dict]:
    """Build reuse records from this task's verified UI path cache, if any.

    The cache holds action sequences proven on-device during Android
    probe-flow. Surfacing them as successful-path records lets the next task's
    Reuse.md point at a reusable UI navigation for the same app/screen.
    """
    try:
        from orchestrator.ui_path_cache import get_ui_path_cache_file, read_ui_path_cache
    except Exception:
        return []
    cache = read_ui_path_cache(task_dir)
    if not isinstance(cache, dict) or not cache:
        return []
    cache_file = get_ui_path_cache_file(task_dir)
    records: list[dict] = []
    for tc_id, entry in cache.items():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("validity", "valid")).lower() != "valid":
            continue
        steps = entry.get("actionSequence")
        step_count = len(steps) if isinstance(steps, list) else 0
        if step_count == 0:
            continue
        records.append({
            "purpose": f"verified UI navigation path for {tc_id} ({step_count} steps)",
            "command": "reuse cached probe-flow steps; regenerated only if UI fingerprint changes",
            "cwd": "-",
            "preconditions": "Reuse only when the target app/screen and UI fingerprint match; a source/UI change invalidates the cache.",
            "evidence": f"`{cache_file}`",
            "scope": f"UI path cache: {tc_id}",
            "confidence": "medium",
        })
    return records


def build_reuse_path_records(
    task_dir: Path,
    evaluation: dict,
    completion_report: dict,
    iter_dirs: list[Path],
    val_text: str,
    delivery_text: str,
) -> tuple[list[dict], list[dict]]:
    """Build structured successful/avoid path records for summary/reuse index.

    Extraction is intentionally conservative: it records commands/evidence that
    already appear in task artifacts, and avoids inventing a build command.
    """
    final_result = str(evaluation.get("result", "")).lower()
    evidence_refs = normalize_evidence_refs(evaluation.get("evidence", []))
    test_results = evaluation.get("testResults") if isinstance(evaluation.get("testResults"), list) else []
    for item in test_results:
        if isinstance(item, dict):
            evidence_refs.extend(normalize_evidence_refs(item.get("evidence")))
    commands = collect_commands_from_iter_dirs(iter_dirs)
    successful: list[dict] = []
    avoid: list[dict] = []

    passed_cases = []
    failed_cases = []
    for item in test_results:
        if not isinstance(item, dict):
            continue
        result = normalize_test_result_value(str(item.get("result", "")))
        testcase_id = str(item.get("testCaseId") or item.get("id") or "").strip()
        if result == "pass" and testcase_id:
            passed_cases.append(testcase_id)
        elif result in {"fail", "blocked", "skipped", "not_run"} and testcase_id:
            failed_cases.append(testcase_id)

    # P0-4: collect blocked-path signatures so a failed/blocked command never
    # gets promoted into successfulPaths. Sources: failed checks, blocked/failed
    # test results, and human-written "Avoid repeating" lines.
    blocked_signatures: list[str] = []
    for check in evaluation.get("failedChecks", []) or []:
        if isinstance(check, dict):
            for key in ("command", "evidence", "reason", "name"):
                val = str(check.get(key) or "").strip()
                if val:
                    blocked_signatures.append(val)
    for item in test_results:
        if not isinstance(item, dict):
            continue
        if normalize_test_result_value(str(item.get("result", ""))) in {"fail", "blocked", "skipped", "not_run"}:
            for key in ("command", "evidence", "note"):
                val = str(item.get(key) or "").strip()
                if val:
                    blocked_signatures.append(val)
    blocked_signatures.extend(extract_validation_reuse_lines(val_text + "\n" + delivery_text, "Avoid repeating"))

    confidence = infer_reuse_confidence(final_result, evidence_refs, completion_report)
    latest_command = commands[-1] if commands else None
    success_command = _select_latest_non_blocked_command(commands, blocked_signatures)
    if final_result == "pass" and (success_command or evidence_refs or passed_cases):
        successful.append({
            "purpose": "required verification/build path" if completion_report.get("result") == "pass" else "latest successful verification path",
            "command": success_command.get("command") if success_command else "See evidence path; exact command not recorded",
            "cwd": success_command.get("cwd") if success_command else "-",
            "preconditions": "Match current task scope, cwd, tools/device/runtime, fixtures, and required TC/AC before reuse.",
            "evidence": summarize_evidence_refs_for_reuse(task_dir, evidence_refs or ([str(success_command.get("path"))] if success_command else [])),
            "scope": ", ".join(passed_cases[:8]) if passed_cases else "latest task verification",
            "confidence": confidence,
        })

    for line in extract_validation_reuse_lines(val_text + "\n" + delivery_text, "Reusable findings"):
        if not line or line.lower() in {"-", "none", "n/a"}:
            continue
        # Convert human-written reusable findings into records without pretending
        # they are commands.
        if all(line != item.get("purpose") for item in successful):
            successful.append({
                "purpose": line,
                "command": success_command.get("command") if success_command else "method/lesson; no exact command recorded",
                "cwd": success_command.get("cwd") if success_command else "-",
                "preconditions": "Apply only when the current task matches this lesson and fresh evidence does not contradict it.",
                "evidence": summarize_evidence_refs_for_reuse(task_dir, evidence_refs),
                "scope": "Validation.md reusable finding",
                "confidence": "medium" if final_result == "pass" else "low",
            })

    for check in evaluation.get("failedChecks", []) or []:
        if not isinstance(check, dict):
            continue
        evidence = str(check.get("evidence") or "")
        reason = str(check.get("reason") or check.get("name") or "failed check")
        category = str(check.get("category") or "unknown")
        path_desc = latest_command.get("command") if latest_command else reason
        avoid.append({
            "path": path_desc,
            "failureCategory": category,
            "evidence": summarize_evidence_refs_for_reuse(task_dir, [evidence] if evidence else evidence_refs),
            "doNotRetryUnless": "the root cause or preconditions changed; classify product vs environment vs harness before retrying",
            "reason": reason,
        })
    for line in extract_validation_reuse_lines(val_text + "\n" + delivery_text, "Avoid repeating"):
        if not line or line.lower() in {"-", "none", "n/a"}:
            continue
        avoid.append({
            "path": line,
            "failureCategory": "avoid_repeat_lesson",
            "evidence": summarize_evidence_refs_for_reuse(task_dir, evidence_refs),
            "doNotRetryUnless": "current evidence proves the old blocker is resolved or this task explicitly requires retesting it",
            "reason": line,
        })

    # Promote verified UI path cache entries so a later task on the same app can
    # discover that a proven action sequence already exists (cross-task reuse).
    if final_result == "pass":
        for entry in _collect_cached_ui_path_records(task_dir):
            successful.append(entry)

    # Deduplicate by path/purpose to keep Reuse.md readable.
    dedup_success: list[dict] = []
    seen_success: set[str] = set()
    for item in successful:
        key = (str(item.get("purpose", "")) + "\n" + str(item.get("command", "")))[:500]
        if key in seen_success:
            continue
        seen_success.add(key)
        dedup_success.append(item)
    dedup_avoid: list[dict] = []
    seen_avoid: set[str] = set()
    for item in avoid:
        key = (str(item.get("path", "")) + "\n" + str(item.get("failureCategory", "")))[:500]
        if key in seen_avoid:
            continue
        seen_avoid.add(key)
        dedup_avoid.append(item)
    return dedup_success[:8], dedup_avoid[:8]


def parse_json_object_from_text(text: str) -> Optional[dict]:
    """Extract a JSON object from possibly noisy agent output."""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    # Common agent fallback: a single fenced json block.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        try:
            data = json.loads(fence.group(1))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(raw[start:end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def sanitize_ai_summary_text(value, limit: int = 800) -> str:
    """Keep AI-refined summary text bounded and non-empty."""
    text = str(value or "").replace("\x00", "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def sanitize_ai_evidence_refs(task_dir: Path, value) -> list[str]:
    """Keep evidence refs as strings; prefer refs that exist but allow pointers."""
    refs = normalize_evidence_refs(value)
    deduped: list[str] = []
    for ref in refs:
        clean = sanitize_ai_summary_text(ref, 300)
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped[:6]



def sanitize_ai_raw_path(value, *, default_name: str = "summary-lesson.md") -> str:
    """Return a safe workspace-local knowledge raw path."""
    text = sanitize_ai_summary_text(value, 260)
    if not text:
        text = default_name
    text = text.replace("\\", "/")
    text = re.sub(r"^/+", "", text)
    has_raw_prefix = text.startswith(".automind/summary/raw/")
    if has_raw_prefix:
        text = text[len(".automind/summary/raw/"):]
    parts = [part for part in text.split("/") if part not in {"", ".", "..", ".automind", "summary", "raw"}]
    if not parts:
        parts = [default_name]
    cleaned_parts = []
    for part in parts:
        cleaned = re.sub(r"[^A-Za-z0-9._+-]+", "-", part).strip(".-")
        if cleaned:
            cleaned_parts.append(cleaned[:80])
    if not cleaned_parts:
        cleaned_parts = [default_name]
    path = "/".join(cleaned_parts)
    path = ".automind/summary/raw/" + path
    if not Path(path).suffix:
        path += ".md"
    return path


def sanitize_ai_index_record(raw, warnings: list[str], *, raw_path: str = "") -> dict:
    """Sanitize a compact knowledge index record suggested by AI."""
    if not isinstance(raw, dict):
        warnings.append("ignored non-object AI index record")
        return {}
    record: dict = {}
    text_keys = {"id", "title", "description", "value", "confidence"}
    list_keys = {"taskTypes", "projects", "surfaces", "phaseApplicability", "triggers", "successfulPaths", "avoidPaths", "importantReminders", "evidenceRefs"}
    for key in text_keys:
        if key in raw:
            record[key] = sanitize_ai_summary_text(raw.get(key), 500)
    for key in list_keys:
        values = []
        source = raw.get(key)
        if isinstance(source, list):
            iterable = source
        elif source:
            iterable = [source]
        else:
            iterable = []
        for item in iterable[:12]:
            clean = sanitize_ai_summary_text(item, 500)
            if clean and clean not in values:
                values.append(clean)
        if values:
            record[key] = values
    if raw_path:
        record["rawPath"] = raw_path
    elif raw.get("rawPath"):
        record["rawPath"] = sanitize_ai_raw_path(raw.get("rawPath"))
    confidence = str(record.get("confidence") or "medium").lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    record["confidence"] = confidence
    if not record.get("id"):
        title = record.get("title") or Path(record.get("rawPath") or "summary-lesson").stem
        record["id"] = re.sub(r"[^a-z0-9._+-]+", "-", title.lower()).strip("-")[:80] or "summary-lesson"
    return record


def clean_knowledge_actions(task_dir: Path, raw_actions, warnings: list[str], max_items: int = 5) -> list[dict]:
    """Validate v2 AI knowledge actions for raw/index promotion."""
    if not isinstance(raw_actions, list):
        return []
    actions: list[dict] = []
    allowed = {"no_action", "upsert_raw", "merge_raw", "upsert_index"}
    for raw in raw_actions[:max_items]:
        if not isinstance(raw, dict):
            warnings.append("ignored non-object AI knowledge action")
            continue
        action = sanitize_ai_summary_text(raw.get("action"), 60).lower() or "no_action"
        if action not in allowed:
            warnings.append(f"ignored invalid knowledge action: {action}")
            continue
        item = {
            "action": action,
            "reason": sanitize_ai_summary_text(raw.get("reason"), 700),
            "evidence": sanitize_ai_evidence_refs(task_dir, raw.get("evidence")),
        }
        if action == "no_action":
            actions.append(item)
            continue
        raw_path = sanitize_ai_raw_path(raw.get("rawPath") or raw.get("targetRawPath"))
        item["rawPath"] = raw_path
        if action in {"upsert_raw", "merge_raw"}:
            content = sanitize_ai_summary_text(raw.get("content") or raw.get("patch"), 12_000)
            if len(content) < 40:
                warnings.append(f"ignored {action} with too-short content")
                continue
            item["content"] = content
            item["mode"] = "append" if action == "merge_raw" else "replace"
        if action in {"upsert_raw", "merge_raw", "upsert_index"}:
            index_record = sanitize_ai_index_record(raw.get("indexRecord") or {}, warnings, raw_path=raw_path)
            if not index_record.get("title") and not index_record.get("value"):
                warnings.append(f"ignored {action} without useful indexRecord title/value")
                continue
            index_record.setdefault("rawPath", raw_path)
            item["indexRecord"] = index_record
        actions.append(item)
    return actions


def apply_ai_knowledge_actions(task_dir: Path, ai_refinement: Optional[dict]) -> tuple[list[dict], list[str]]:
    """Apply validated AI knowledge actions to index/raw stores."""
    applied: list[dict] = []
    warnings: list[str] = []
    if not ai_refinement:
        return applied, warnings
    for item in ai_refinement.get("knowledgeActions", []) or []:
        action = item.get("action")
        if action == "no_action":
            applied.append({"action": "no_action", "reason": item.get("reason", "")})
            continue
        raw_path_text = item.get("rawPath") or ""
        raw_path = (AUTOMIND_WORKSPACE_ROOT / raw_path_text).resolve()
        try:
            raw_path.relative_to(AUTOMIND_WORKSPACE_ROOT.resolve())
        except ValueError:
            warnings.append(f"skipped knowledge action outside workspace: {raw_path_text}")
            continue
        if action in {"upsert_raw", "merge_raw"}:
            ensure_dir(raw_path.parent)
            header = f"\
\
<!-- CodeMind summary refiner: {datetime.now().isoformat(timespec='seconds')} task={task_dir.name} action={action} -->\
"
            content = str(item.get("content") or "").rstrip() + "\
"
            if action == "merge_raw" and raw_path.exists():
                raw_path.write_text(raw_path.read_text(errors="ignore").rstrip() + header + content)
            else:
                raw_path.write_text(content)
        if action in {"upsert_raw", "merge_raw", "upsert_index"}:
            record = dict(item.get("indexRecord") or {})
            record.setdefault("rawPath", raw_path_text)
            record.setdefault("evidenceRefs", item.get("evidence") or [rel_to_root(task_dir / "summary.md")])
            append_knowledge_index_record(record, index_path=SUMMARY_DIR / "index.jsonl")
        applied.append({"action": action, "rawPath": raw_path_text, "indexId": (item.get("indexRecord") or {}).get("id")})
    return applied, warnings
def validate_ai_summary_refinement(task_dir: Path, data: dict) -> tuple[dict, list[str]]:
    """Validate and normalize AI summary refinement output.

    The AI is allowed to classify and condense, but not to create an unbounded
    or schema-free memory record. This function is the deterministic filter.
    """
    warnings: list[str] = []
    if not isinstance(data, dict):
        return {"schema": "automind.ai_summary_refinement.v1", "result": "blocked"}, ["AI output is not an object"]
    schema = data.get("schema")
    if schema != "automind.ai_summary_refinement.v1":
        warnings.append(f"unexpected schema: {schema}")
    result = str(data.get("result") or "ok").strip().lower()
    if result not in {"ok", "no_action", "blocked"}:
        warnings.append(f"invalid result: {result}")
        result = "blocked"

    def clean_items(raw_items, allowed_keys: set[str], max_items: int = 8) -> list[dict]:
        items: list[dict] = []
        if not isinstance(raw_items, list):
            return items
        for raw in raw_items[:max_items]:
            if not isinstance(raw, dict):
                warnings.append("ignored non-object AI summary item")
                continue
            item: dict = {}
            for key in allowed_keys:
                if key == "evidence":
                    item[key] = sanitize_ai_evidence_refs(task_dir, raw.get(key))
                else:
                    item[key] = sanitize_ai_summary_text(raw.get(key), 900)
            if any(value not in ([], "") for value in item.values()):
                items.append(item)
        return items

    cleaned = {
        "schema": "automind.ai_summary_refinement.v1",
        "taskCode": sanitize_ai_summary_text(data.get("taskCode"), 120),
        "result": result,
        "summary": sanitize_ai_summary_text(data.get("summary"), 500),
        "successfulPaths": clean_items(data.get("successfulPaths"), {
            "purpose", "command", "cwd", "preconditions", "evidence", "scope", "confidence", "reason",
        }),
        "avoidPaths": clean_items(data.get("avoidPaths"), {
            "path", "failureCategory", "evidence", "doNotRetryUnless", "reason",
        }),
        "lessons": clean_items(data.get("lessons"), {
            "title", "lesson", "appliesWhen", "evidence", "confidence",
        }),
        "downgradeOrRetract": clean_items(data.get("downgradeOrRetract"), {
            "pathOrLesson", "action", "condition", "reason",
        }),
        "promotionSuggestions": clean_items(data.get("promotionSuggestions"), {
            "target", "reason",
        }, max_items=5),
        "knowledgeActions": clean_knowledge_actions(task_dir, data.get("knowledgeActions"), warnings),
    }
    return cleaned, warnings


def build_summary_refiner_seed(
    task_code: str,
    task_dir: Path,
    reason: str,
    evaluation: dict,
    completion_report: dict,
    workflow_report: dict,
    record_ok: bool,
    record_issues: list[str],
    iter_dirs: list[Path],
    successful_paths: list[dict],
    avoid_paths: list[dict],
    reusable: list[str],
    downgrade: list[str],
) -> dict:
    """Build deterministic, bounded seed for optional AI summary refinement."""
    def rel(path: Path | str) -> str:
        try:
            return rel_to_root(path)
        except Exception:
            return str(path)

    command_records = []
    for record in collect_commands_from_iter_dirs(iter_dirs[-5:]):
        command_records.append({
            "iteration": record.get("iteration"),
            "cwd": record.get("cwd"),
            "command": record.get("command"),
            "path": rel(record.get("path", "")),
        })

    artifact_snippets = {}
    for path in [*requirement_contract_paths(task_dir), task_dir / "Plan.md", task_dir / "Delivery.md", task_dir / "Validation.md"]:
        if path.exists():
            artifact_snippets[path.name] = read_tail(path, 4000)

    return {
        "schema": "automind.summary_refiner_seed.v1",
        "taskCode": task_code,
        "reason": reason,
        "taskDir": rel(task_dir),
        "recordCheck": {"ok": record_ok, "issues": record_issues[:20]},
        "workflowCheck": {
            "result": workflow_report.get("result"),
            "issues": workflow_report.get("issues", [])[:20],
            "warnings": workflow_report.get("warnings", [])[:20],
        },
        "completionCheck": {
            "result": completion_report.get("result"),
            "issues": completion_report.get("issues", [])[:20],
            "coverage": completion_report.get("coverage", {}),
        },
        "evaluation": {
            "iteration": evaluation.get("iteration"),
            "result": evaluation.get("result"),
            "nextAction": evaluation.get("nextAction"),
            "summary": evaluation.get("summary"),
            "failedChecks": evaluation.get("failedChecks", [])[:12] if isinstance(evaluation.get("failedChecks"), list) else [],
            "evidence": evaluation.get("evidence", [])[:12] if isinstance(evaluation.get("evidence"), list) else [],
            "testResults": evaluation.get("testResults", [])[:20] if isinstance(evaluation.get("testResults"), list) else [],
            "verificationUnblockChanges": normalize_verification_unblock_changes(evaluation)[:8],
        },
        "deterministicExtraction": {
            "successfulPaths": successful_paths,
            "avoidPaths": avoid_paths,
            "reusableLessons": reusable,
            "downgradeOrRetract": downgrade,
            "commands": command_records,
        },
        "artifactSnippets": artifact_snippets,
    }


def run_ai_summary_refiner(task_code: str, task_dir: Path, reason: str, seed: dict, agent: str = "codex") -> tuple[Optional[dict], list[str]]:
    """Run optional AI Summary Refiner and return validated output.

    This is best-effort. Deterministic summary remains the baseline if the agent
    is unavailable, returns invalid JSON, or produces no useful refinements.
    """
    warnings: list[str] = []
    summary_dir = task_dir / "logs" / "summary-refiner"
    ensure_dir(summary_dir)
    seed_json_path = summary_dir / "summary-refiner-seed.json"
    seed_md_path = summary_dir / "summary-refiner-seed.md"
    seed_json_path.write_text(json.dumps(seed, ensure_ascii=False, indent=2))
    seed_md_path.write_text(
        "# CodeMind Summary Refiner Seed\n\n"
        "This seed is deterministically filtered before AI refinement.\n\n"
        "```json\n"
        + json.dumps(seed, ensure_ascii=False, indent=2)[:60_000]
        + "\n```\n"
    )
    prompt = render_prompt_template(
        "summary_refiner_prompt.md",
        task_dir=task_dir,
        task_code=task_code,
        reason=reason,
        seed_json_path=seed_json_path,
        seed_md_path=seed_md_path,
    )
    write_rendered_prompt(summary_dir, "summary-refiner-prompt.md", prompt)
    code, output = run_agent("cli", agent, prompt, task_dir, phase="summary")
    (summary_dir / "summary-refiner-output.log").write_text(output)
    if code != 0:
        warnings.append(f"AI summary refiner failed: agent={agent} code={code}")
        return None, warnings
    parsed = parse_json_object_from_text(output)
    if parsed is None:
        warnings.append("AI summary refiner returned invalid JSON")
        return None, warnings
    cleaned, validation_warnings = validate_ai_summary_refinement(task_dir, parsed)
    warnings.extend(validation_warnings)
    (summary_dir / "summary-refiner.json").write_text(json.dumps(cleaned, ensure_ascii=False, indent=2))
    return cleaned, warnings


# ============================================================
# Task summary generation
# ============================================================

def generate_summary(task_code: str, reason: str = "manual", ai_agent: Optional[str] = None):
    """\u751f\u6210 Critic-Refiner \u98ce\u683cTask\u603b\u7ed3"""
    task_dir = get_task_dir(task_code)
    run_before_phase_hooks(task_dir, "summary", reason=f"before_summary:{reason}")
    summary_path = task_dir / "summary.md"

    req_path = primary_requirements_path(task_dir)
    plan_path = task_dir / "Plan.md"
    delivery_path = task_dir / "Delivery.md"
    val_path = task_dir / "Validation.md"
    evaluation_path = task_dir / "evaluation.json"
    ledger_path = task_dir / "VerificationLedger.json"
    state_path = get_runtime_state_path(task_dir)
    logs_dir = task_dir / "logs"

    user_input = (task_dir / ".user_input.txt").read_text() if (task_dir / ".user_input.txt").exists() else ""
    record_ok, record_issues = check_task_records(task_code)
    workflow_ok, workflow_report = check_workflow_consistency(task_code)
    completion_report = {}
    if ledger_path.exists():
        try:
            completion_report = json.loads(ledger_path.read_text())
        except json.JSONDecodeError:
            completion_report = {"result": "invalid_json", "coverage": {}, "issues": ["VerificationLedger.json invalid JSON"]}
    update_runtime_state(
        task_dir,
        recordCheck={
            "ok": record_ok,
            "reason": f"summary:{reason}",
            "checkedAt": datetime.now().isoformat(timespec="seconds"),
            "issues": record_issues,
        }
    )
    update_runtime_state(
        task_dir,
        workflowCheck={
            "ok": workflow_ok,
            "reason": f"summary:{reason}",
            "checkedAt": datetime.now().isoformat(timespec="seconds"),
            "issues": workflow_report.get("issues", []),
            "warnings": workflow_report.get("warnings", []),
        }
    )
    state = read_runtime_state(task_dir) or {}
    evaluation = read_evaluation_json(task_dir) or {}

    iter_dirs = sorted(logs_dir.glob("iter-*"), key=lambda path: path.name) if logs_dir.exists() else []
    iter_count = len(iter_dirs)
    final_status = state.get("status", "unknown")
    final_result = evaluation.get("result", state.get("lastResult", "unknown"))
    final_summary = evaluation.get("summary", "-")

    evidence_items = evaluation.get("evidence", [])
    evidence_index_items = evaluation.get("evidenceIndex", []) if isinstance(evaluation.get("evidenceIndex"), list) else []
    failed_checks = evaluation.get("failedChecks", [])

    def rel(path: Path | str) -> str:
        try:
            return rel_to_root(path)
        except Exception:
            return str(path)

    def read_section(path: Path, heading: str, max_chars: int = 4000) -> str:
        if not path.exists():
            return "-"
        text = path.read_text()
        marker = f"## {heading}"
        if marker not in text:
            return "-"
        part = text.split(marker, 1)[1]
        next_idx = part.find("\n## ")
        if next_idx != -1:
            part = part[:next_idx]
        return part.strip()[:max_chars] or "-"

    evidence_md = ""
    if evidence_items:
        for item in evidence_items:
            if isinstance(item, dict):
                evidence_md += f"- **{item.get('type', 'evidence')}**: `{item.get('path', '-')}`"
                if item.get("note"):
                    evidence_md += f" — {item.get('note')}"
                evidence_md += "\n"
    else:
        for iter_dir in iter_dirs[-3:]:
            evidence_md += f"- `{rel(iter_dir)}/`\n"
    if not evidence_md:
        evidence_md = "- No evidence recorded yet\n"

    evidence_index_md = ""
    for item in evidence_index_items[:20]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        tc = item.get("tc")
        if isinstance(tc, list):
            tc_text = ", ".join(str(x) for x in tc if str(x).strip()) or "-"
        else:
            tc_text = str(tc or "-").strip() or "-"
        evidence_index_md += (
            f"- `{path}` — type=`{item.get('type') or 'hint'}`, "
            f"signal=`{item.get('signal') or '-'}`, tc=`{tc_text}`\n"
        )
    if evidence_index_items and len(evidence_index_items) > 20:
        evidence_index_md += f"- ... {len(evidence_index_items) - 20} more entries in `evaluation.json`\n"
    if not evidence_index_md:
        evidence_index_md = "- No lightweight evidenceIndex recorded.\n"

    failed_md = ""
    if failed_checks:
        for check in failed_checks:
            if isinstance(check, dict):
                failed_md += (
                    f"### {check.get('name', 'failed_check')}\n"
                    f"- **Category**: `{check.get('category', 'unknown')}`\n"
                    f"- **Reason**: {check.get('reason', '-')}\n"
                )
                if check.get("evidence"):
                    failed_md += f"- **Evidence**: `{check.get('evidence')}`\n"
                failed_md += "- **Current status**: handle according to next action.\n\n"
    else:
        # Try to preserve useful failure history from Validation.md.
        failure_analysis = read_section(val_path, "Failure Analysis")
        if failure_analysis != "-":
            failed_md = failure_analysis + "\n"
        else:
            failed_md = "- No current failure. See Validation.md for historical failures.\n"

    reusable = []
    downgrade = []

    # Conservative extraction from Validation.md if present.
    val_text = val_path.read_text() if val_path.exists() else ""
    delivery_text = delivery_path.read_text(errors="ignore") if delivery_path.exists() else ""
    successful_paths, avoid_paths = build_reuse_path_records(
        task_dir,
        evaluation,
        completion_report,
        iter_dirs,
        val_text,
        delivery_text,
    )
    if "content-desc" in val_text or "resource-id" in val_text:
        reusable.append("Prefer Android UI selectors content-desc / resource-id; use text only as fallback.")
    if "human/device readiness" in val_text or "\u4fdd\u6301\u4eae\u5c4f" in val_text or "\u89e3\u9501" in val_text:
        reusable.append("Physical-device verification needs human/device readiness: unlocked device, screen-on, authorization complete, and no system overlay.")
    if "com.android.systemui" in val_text:
        downgrade.append("Do not misclassify `com.android.systemui` overlay as app-code failure; classify it as device-state/permission blocker first.")
    if "\u53ea\u7528 text selector" in val_text or "text selector" in val_text:
        downgrade.append("Downgrade strategies that rely only on text selectors because system casing/localization can change.")

    # Recover human-intervention recovery paths (manual-*/cleanup/cache-reset
    # dirs) that the iter-* command scan never sees. This runs deterministically
    # so the lesson survives even when the AI summary refiner is unavailable.
    for lesson in extract_manual_intervention_lessons(logs_dir):
        if lesson not in reusable:
            reusable.append(lesson)

    if not reusable:
        reusable.append("Reuse this task's successful verification command, evidence paths, and failure classification method.")
    if not downgrade:
        downgrade.append("No explicit lessons need downgrade or retraction.")

    ai_refinement = None
    ai_refinement_warnings: list[str] = []
    ai_knowledge_actions: list[dict] = []
    ai_knowledge_action_warnings: list[str] = []
    if ai_agent:
        seed = build_summary_refiner_seed(
            task_code,
            task_dir,
            reason,
            evaluation,
            completion_report,
            workflow_report,
            record_ok,
            record_issues,
            iter_dirs,
            successful_paths,
            avoid_paths,
            reusable,
            downgrade,
        )
        ai_refinement, ai_refinement_warnings = run_ai_summary_refiner(task_code, task_dir, reason, seed, agent=ai_agent)
        if ai_refinement:
            for item in ai_refinement.get("successfulPaths", []) or []:
                successful_paths.append({
                    "purpose": item.get("purpose") or item.get("reason") or "AI refined successful path",
                    "command": item.get("command") or "method/lesson; no exact command recorded",
                    "cwd": item.get("cwd") or "-",
                    "preconditions": item.get("preconditions") or "Apply only when scope/preconditions match current task.",
                    "evidence": ", ".join(f"`{ref}`" for ref in item.get("evidence", []) or []) or "-",
                    "scope": item.get("scope") or "AI summary refinement",
                    "confidence": item.get("confidence") or "low",
                    "aiRefined": True,
                })
            for item in ai_refinement.get("avoidPaths", []) or []:
                avoid_paths.append({
                    "path": item.get("path") or item.get("reason") or "AI refined avoid path",
                    "failureCategory": item.get("failureCategory") or "unknown",
                    "evidence": ", ".join(f"`{ref}`" for ref in item.get("evidence", []) or []) or "-",
                    "doNotRetryUnless": item.get("doNotRetryUnless") or "current evidence proves conditions changed",
                    "reason": item.get("reason") or "AI summary refinement",
                    "aiRefined": True,
                })
            for item in ai_refinement.get("lessons", []) or []:
                lesson = item.get("lesson") or item.get("title")
                if lesson:
                    reusable.append(f"AI refined: {lesson}")
            for item in ai_refinement.get("downgradeOrRetract", []) or []:
                target = item.get("pathOrLesson") or item.get("reason")
                if target:
                    downgrade.append(f"AI {item.get('action') or 'downgrade'}: {target} — {item.get('reason') or '-'}")

    validation_results = read_section(val_path, "\u9a8c\u6536Result")
    next_steps = read_section(val_path, "Next action")

    summary = f"""# Summary: {task_code}

## 1. Final result

- **User request**: {user_input or '-'}
- **Iterations**: {iter_count}
- **\u6700\u7ec8Status**: {final_status}
- **Final result**: {final_result}
- **Latest conclusion**: {final_summary}
- **Generated reason**: {reason}

## 2. Record completeness

- **record-check**: {"PASS" if record_ok else "ISSUES"}
- **Issue count**: {len(record_issues)}
- **workflow-check**: {"PASS" if workflow_ok else "ISSUES"} - issues: {len(workflow_report.get("issues", []))}, warnings: {len(workflow_report.get("warnings", []))}
- **completion-check**: {completion_report.get("result", "not_run")} - open AC: {len((completion_report.get("coverage") or {}).get("acceptanceCriteriaOpen", [])) if isinstance(completion_report.get("coverage"), dict) else "-"}

{chr(10).join(f"- `{issue}`" for issue in record_issues[:20]) if record_issues else "- Records are complete and reusable for the next local task."}

## 2.1 Workflow continuity

- Requirement IDs: {", ".join(workflow_report.get("ids", {}).get("requirements", [])) or "-"}
- Acceptance criteria IDs: {", ".join(workflow_report.get("ids", {}).get("acceptanceCriteria", [])) or "-"}
- TestCase IDs: {", ".join(workflow_report.get("ids", {}).get("testCases", [])) or "-"}
{chr(10).join(f"- Issue: `{issue}`" for issue in workflow_report.get("issues", [])[:20]) if workflow_report.get("issues") else "- Workflow artifacts are connected enough to continue/finish."}
{chr(10).join(f"- Warning: `{warning}`" for warning in workflow_report.get("warnings", [])[:20]) if workflow_report.get("warnings") else ""}

## 3. Key evidence

- Require: `{rel(req_path)}`
- Plan: `{rel(plan_path)}`
- Delivery: `{rel(delivery_path)}`
- Validation: `{rel(val_path)}`
- Evaluation: `{rel(evaluation_path)}`
- Completion ledger: `{rel(ledger_path) if ledger_path.exists() else '-'}`
- Runtime state: `{rel(state_path)}`
- Logs: `{rel(logs_dir)}/`

{evidence_md.rstrip()}

## 3.1 Lightweight Evidence Index

{evidence_index_md.rstrip()}

## 4. Key failures and repair path

{failed_md.rstrip()}

## 5. Acceptance results

{validation_results}

## 6. Reusable lesson

"""
    for item in reusable:
        summary += f"- {item}\n"

    summary += "\n## 6. Lessons to downgrade or retract\n\n"
    for item in downgrade:
        summary += f"- {item}\n"

    summary += "\n## 6.1 AI Summary Refiner\n\n"
    if ai_agent:
        summary += f"- Agent: `{ai_agent}`\n"
        summary += f"- Result: `{(ai_refinement or {}).get('result', 'unavailable')}`\n"
        if ai_refinement and ai_refinement.get("summary"):
            summary += f"- Summary: {ai_refinement.get('summary')}\n"
        if ai_refinement_warnings:
            summary += "- Warnings:\n"
            for warning in ai_refinement_warnings[:8]:
                summary += f"  - {warning}\n"
        elif ai_refinement:
            summary += "- Warnings: none\n"
    else:
        summary += "- Not requested. Deterministic summary extraction was used.\n"

    summary += "\n## 7. Known successful verification/build paths\n\n"
    if successful_paths:
        summary += "| Purpose | Command / method | Preconditions | Evidence | Scope | Reuse confidence |\n"
        summary += "|---------|------------------|---------------|----------|-------|------------------|\n"
        for item in successful_paths:
            summary += (
                f"| {item.get('purpose', '-')} | `{item.get('command', '-')}` | "
                f"{item.get('preconditions', '-')} cwd=`{item.get('cwd', '-')}` | "
                f"{item.get('evidence', '-')} | {item.get('scope', '-')} | {item.get('confidence', 'low')} |\n"
            )
    else:
        summary += "- No successful path could be extracted from recorded commands/evidence.\n"

    summary += "\n## 8. Known failed/deprecated paths\n\n"
    if avoid_paths:
        summary += "| Path | Failure category | Evidence | Do not retry unless |\n"
        summary += "|------|------------------|----------|---------------------|\n"
        for item in avoid_paths:
            summary += (
                f"| `{item.get('path', '-')}` | `{item.get('failureCategory', '-')}` | "
                f"{item.get('evidence', '-')} | {item.get('doNotRetryUnless', '-')} |\n"
            )
    else:
        summary += "- No failed/deprecated path extracted.\n"

    summary += f"\n## 9. Follow-up action\n\n{next_steps}\n"

    summary_path.write_text(summary)
    success(f"Critic-Refiner summary generated: {summary_path}")

    lessons_path = SUMMARY_LESSONS_PATH
    local_reuse_path = LOCAL_REUSE_INDEX_PATH
    ensure_dir(SUMMARY_DIR)
    with lessons_path.open("a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%d')} - {task_code}\n")
        f.write(f"- Generated reason: {reason}\n")
        f.write(f"- Final result: {final_result} - {final_summary}\n")
        for item in reusable[:3]:
            f.write(f"- Reusable lesson: {item}\n")
        for item in downgrade[:2]:
            f.write(f"- Downgrade/retract: {item}\n")

    with local_reuse_path.open("a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} - {task_code}\n")
        f.write(f"- Generated reason: `{reason}`\n")
        f.write(f"- Task directory: `{rel(task_dir)}`\n")
        f.write(f"- Result: `{final_result}` - {final_summary}\n")
        f.write(f"- record-check: `{'PASS' if record_ok else 'ISSUES'}` - issueCount: {len(record_issues)}\n")
        f.write(f"- workflow-check: `{'PASS' if workflow_ok else 'ISSUES'}` - issueCount: {len(workflow_report.get('issues', []))}, warningCount: {len(workflow_report.get('warnings', []))}\n")
        f.write(f"- completion-check: `{completion_report.get('result', 'not_run')}` - openAC: {len((completion_report.get('coverage') or {}).get('acceptanceCriteriaOpen', [])) if isinstance(completion_report.get('coverage'), dict) else '-'}\n")
        if iter_dirs:
            f.write(f"- Latest evidence dir: `{rel(iter_dirs[-1])}/`\n")
            env_path = iter_dirs[-1] / "env.json"
            cmd_path = iter_dirs[-1] / "commands.md"
            if env_path.exists():
                f.write(f"- Local environment snapshot: `{rel(env_path)}`\n")
            if cmd_path.exists():
                f.write(f"- Reusable command: `{rel(cmd_path)}`\n")
        if ai_agent:
            f.write(f"- AI summary refiner: `{ai_agent}` - result: `{(ai_refinement or {}).get('result', 'unavailable')}`\n")
            for warning in ai_refinement_warnings[:5]:
                f.write(f"- AI summary warning: {warning}\n")
        for item in successful_paths[:5]:
            f.write(
                "- Successful path: "
                f"purpose={item.get('purpose', '-')}; "
                f"command={item.get('command', '-')}; "
                f"cwd={item.get('cwd', '-')}; "
                f"preconditions={item.get('preconditions', '-')}; "
                f"evidence={item.get('evidence', '-')}; "
                f"scope={item.get('scope', '-')}; "
                f"confidence={item.get('confidence', 'low')}\n"
            )
        for item in avoid_paths[:5]:
            f.write(
                "- Avoid path: "
                f"path={item.get('path', '-')}; "
                f"failureCategory={item.get('failureCategory', '-')}; "
                f"evidence={item.get('evidence', '-')}; "
                f"doNotRetryUnless={item.get('doNotRetryUnless', '-')}\n"
            )
        for item in reusable[:5]:
            f.write(f"- Reusable: {item}\n")
        for item in downgrade[:3]:
            f.write(f"- Avoid repeating: {item}\n")

    trace = write_trace(task_code, task_dir)
    runtime_downgrade_count = len([item for item in downgrade if "runtime" in str(item).lower() or "device" in str(item).lower()])
    run_card = {
        "schemaVersion": 1,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "taskCode": task_code,
        "taskDir": rel(task_dir),
        "summaryPath": rel(summary_path),
        "tracePath": rel(task_dir / "trace.json"),
        "result": final_result,
        "status": final_status,
        "iterations": iter_count,
        "recordCheck": {"ok": record_ok, "issueCount": len(record_issues)},
        "workflowCheck": {"ok": workflow_ok, "issueCount": len(workflow_report.get("issues", [])), "warningCount": len(workflow_report.get("warnings", []))},
        "completionCheck": {"result": completion_report.get("result", "not_run")},
        "traceSummary": trace.get("summary", {}),
        "successfulPaths": successful_paths[:10],
        "failedAttempts": avoid_paths[:10],
        "lessons": reusable[:10],
        "avoidRepeating": downgrade[:10],
        "runtimeDowngradeCount": runtime_downgrade_count,
    }
    run_card_path = task_dir / "run-card.json"
    run_card_path.write_text(json.dumps(run_card, ensure_ascii=False, indent=2) + "\n")
    run_cards_index = append_run_card_to_summary_store(task_dir, run_card)
    knowledge_index_path = append_summary_knowledge_candidate(task_dir, task_code, successful_paths, avoid_paths, summary_path)
    summary += f"\n## 10. Run Card / Learning Card\n\n- Run card: `{rel(run_card_path)}`\n- Trace: `{rel(task_dir / 'trace.json')}`\n- Run cards index: `{rel(run_cards_index)}`\n"
    if knowledge_index_path:
        summary += f"- Knowledge index candidate: `{rel(knowledge_index_path)}`\n"
    after_summary_hook = run_after_phase_hooks(
        task_dir,
        "summary",
        payload={
            "reason": reason,
            "summaryPath": rel(summary_path),
            "runCardPath": rel(run_card_path),
            "runCardsIndex": rel(run_cards_index),
            "aiRefinementResult": (ai_refinement or {}).get("result") if ai_refinement else "not_requested",
            "aiKnowledgeActions": ai_knowledge_actions,
        },
        reason=f"after_summary:{reason}",
    )
    summary += f"- Phase learning: `{after_summary_hook.get('phaseLearningPath')}`\n"
    summary_path.write_text(summary)

    # Compact append-only stores so they don't grow unbounded or duplicate.
    try:
        compact_global_summary_stores()
    except Exception as exc:
        warn(f"Global summary compaction skipped: {exc}")

    # Sink durable, cross-task lessons into the machine-global accumulated store
    # (CodeMind runtime install). Project-bound lessons route to
    # business/<slug>/, business-agnostic ones to technical/. Best-effort.
    try:
        evidence_ref = rel(iter_dirs[-1]) + "/" if iter_dirs else "-"
        accumulated = sink_accumulated_lessons(
            task_code,
            final_result=final_result,
            successful_paths=successful_paths,
            avoid_paths=avoid_paths,
            reusable=reusable,
            downgrade=downgrade,
            evidence_ref=evidence_ref,
        )
        if accumulated.get("technical") or accumulated.get("business"):
            summary += (
                "\n## 11. Accumulated global lessons\n\n"
                f"- technical entries: {accumulated.get('technical', 0)}\n"
                f"- business/{accumulated.get('slug', 'project')} entries: {accumulated.get('business', 0)}\n"
            )
            summary_path.write_text(summary)
    except Exception as exc:
        warn(f"Global lesson accumulation skipped for {task_code}: {exc}")

    update_runtime_state(
        task_dir,
        summary={
            "path": rel(summary_path),
            "reason": reason,
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "lessonsPath": rel(lessons_path),
            "reuseIndexPath": rel(local_reuse_path),
            "aiRefinerAgent": ai_agent or None,
            "aiRefinerResult": (ai_refinement or {}).get("result") if ai_agent else None,
            "aiRefinerWarnings": ai_refinement_warnings,
            "aiKnowledgeActions": ai_knowledge_actions,
        }
    )
    # When the summary is generated for an authoritative terminal pass, close
    # the workflow control state. The verification-loop routing emits an
    # intermediate ``complete_task`` event (status=running) before summary, so
    # without this terminal event ``automind-workflow-state.json`` stays stuck
    # at status=running/currentAction=complete_task even though the task is done.
    try:
        finalize_workflow_state_if_terminal(task_dir, reason=reason)
    except Exception as exc:
        warn(f"Workflow terminal-state finalize skipped for {task_code}: {exc}")
    return summary_path


def _split_dated_sections(text: str) -> list[tuple[str, str]]:
    """Split append-only summary text into ``(header, body)`` task sections.

    Sections start with ``## YYYY-MM-DD`` (lessons-learned) or
    ``## YYYY-MM-DD HH:MM`` (local-reuse-index) followed by ` - <task_code>`.
    Anything before the first dated section is preserved as a `__preamble__`
    block so existing manual notes survive compaction.
    """
    if not text:
        return []
    pattern = re.compile(r"^## \d{4}-\d{2}-\d{2}.*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return [("__preamble__", text)] if text.strip() else []
    sections: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("__preamble__", preamble + "\n"))
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[m.start():end].rstrip() + "\n"
        sections.append((m.group(0).strip(), block))
    return sections


def _section_task_code(header: str) -> Optional[str]:
    if " - " not in header:
        return None
    return header.rsplit(" - ", 1)[-1].strip() or None


def compact_global_summary_stores(
    *,
    keep_recent_tasks: int = 60,
    max_chars: int = 200_000,
) -> dict:
    """Deduplicate and trim ``lessons-learned.md`` / ``local-reuse-index.md``.

    Append-only writers in ``generate_summary`` will otherwise accumulate
    duplicate task sections (re-running summary for the same task code
    produces identical headers) and grow without bound. This compactor:

    1. Splits the file into dated task sections.
    2. Keeps only the LAST section per task code (latest summary wins).
    3. Keeps at most ``keep_recent_tasks`` most-recent task sections.
    4. If the file is still over ``max_chars``, drops oldest sections until
       it fits.

    Returns a dict with ``{path, kept, dropped, dedup, bytes_before,
    bytes_after}`` per file for record-keeping.
    """
    report: dict = {}
    for path in (SUMMARY_LESSONS_PATH, LOCAL_REUSE_INDEX_PATH):
        if not path.exists():
            report[path.name] = {"skipped": "missing"}
            continue
        original = path.read_text(errors="ignore")
        bytes_before = len(original)
        sections = _split_dated_sections(original)

        # Step 1: dedup by task code, latest wins.
        seen_task: dict[str, int] = {}
        for idx, (header, _body) in enumerate(sections):
            if header == "__preamble__":
                continue
            tc = _section_task_code(header)
            if tc:
                seen_task[tc] = idx
        kept_sections: list[tuple[str, str]] = []
        dedup_dropped = 0
        for idx, (header, body) in enumerate(sections):
            if header == "__preamble__":
                kept_sections.append((header, body))
                continue
            tc = _section_task_code(header)
            if tc and seen_task.get(tc) != idx:
                dedup_dropped += 1
                continue
            kept_sections.append((header, body))

        # Step 2: keep the most recent N task sections.
        preamble = [s for s in kept_sections if s[0] == "__preamble__"]
        dated = [s for s in kept_sections if s[0] != "__preamble__"]
        recent_dropped = 0
        if len(dated) > keep_recent_tasks:
            recent_dropped = len(dated) - keep_recent_tasks
            dated = dated[-keep_recent_tasks:]

        # Step 3: char budget — drop oldest until under max_chars.
        char_dropped = 0
        while True:
            rebuilt = "".join(b for _h, b in preamble + dated)
            if len(rebuilt) <= max_chars or not dated:
                break
            dated.pop(0)
            char_dropped += 1

        rebuilt = "".join(b for _h, b in preamble + dated)
        path.write_text(rebuilt)
        report[path.name] = {
            "path": str(path),
            "kept": len(dated),
            "dedupDropped": dedup_dropped,
            "recentDropped": recent_dropped,
            "charBudgetDropped": char_dropped,
            "bytesBefore": bytes_before,
            "bytesAfter": len(rebuilt),
        }
    return report


def _resolve_summary_ai_agent(task_dir: Path) -> Optional[str]:
    """Resolve an agent for best-effort AI summary refinement at loop end.

    Order: explicit env override -> the agent recorded for this task's primary
    session -> ``auto`` (probe codex/claude/trae). Returns ``None`` only when
    the user explicitly disables it. AI refinement itself is best-effort: if the
    resolved agent is unavailable or fails, ``run_ai_summary_refiner`` falls back
    to the deterministic summary without raising.
    """
    override = (os.environ.get("AUTOMIND_SUMMARY_AI_AGENT") or "").strip().lower()
    if override in {"off", "none", "0", "false", "disabled"}:
        return None
    if override:
        return override
    try:
        state = read_runtime_state(task_dir) or {}
        sessions = state.get("agentSessions") if isinstance(state.get("agentSessions"), dict) else {}
        primary = sessions.get("primary") if isinstance(sessions.get("primary"), dict) else {}
        agent = str(primary.get("agent") or "").strip().lower()
        if agent and agent != "auto":
            return agent
    except Exception:
        pass
    return "auto"


def ensure_summary_generated(task_code: str, reason: str = "loop_terminal", ai_agent: Optional[str] = "__resolve__") -> Optional[Path]:
    """Generate summary for any terminal/paused loop state without requiring success.

    The auto loop end now defaults to best-effort AI summary refinement so the
    model's reasoning (distilling scattered commands/failures into reusable
    lessons) also feeds the machine-global accumulated store, not just manual
    ``summary --ai`` runs. Disable via ``AUTOMIND_SUMMARY_AI_AGENT=off`` or by
    passing ``ai_agent=None`` (offline smoke does this).
    """
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        return None
    resolved_ai_agent = _resolve_summary_ai_agent(task_dir) if ai_agent == "__resolve__" else ai_agent
    try:
        path = generate_summary(task_code, reason=reason, ai_agent=resolved_ai_agent)
        print_report_manifest(task_code, heading="CodeMind reports for user review")
        return path
    except Exception as exc:
        warn(f"Summary generation failed for {task_code}: {exc}")
        update_runtime_state(
            task_dir,
            summary={
                "ok": False,
                "reason": reason,
                "error": str(exc),
                "failedAt": datetime.now().isoformat(timespec="seconds"),
            }
        )
        print_report_manifest(task_code, heading="Available CodeMind reports for user review")
        return None
