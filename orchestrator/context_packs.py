"""Generator/Evaluator context pack creation and validation.

Context packs are the artifact boundary between current-session work,
deterministic verifiers, isolated evaluators, and detached agent processes.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from orchestrator.config import AUTOMIND_ROOT, AUTOMIND_WORKSPACE_ROOT
from orchestrator.artifacts import requirement_contract_paths
from orchestrator.log_digest import build_log_digest
from orchestrator.state import ensure_dir, read_evaluation_json, read_runtime_state, read_text_if_exists as _read_text_if_exists, rel_to_root, update_runtime_state

GENERATOR_CORE_REQUIRED_EXCERPT_BYTES = 192_000
GENERATOR_DEFAULT_OPTIONAL_EXCERPT_BYTES = 32_000
GENERATOR_HISTORY_ARTIFACT_EXCERPT_BYTES = 48_000
EVALUATOR_CORE_REQUIRED_EXCERPT_BYTES = 256_000
EVALUATOR_DEFAULT_OPTIONAL_EXCERPT_BYTES = 64_000
EVALUATOR_HISTORY_ARTIFACT_EXCERPT_BYTES = 96_000
HISTORY_ARTIFACT_NAMES = {"Delivery.md", "Validation.md"}
CORE_CONTRACT_NAMES = {"Brainstorm.md", "Requirements.md", "TestCases.md", "Plan.md"}
STRUCTURED_KEY_LINE_RE = re.compile(
    r"(result|nextAction|failed|blocked|pass|fail|error|warning|evidence|stop_reason|music_audio|TC-|AC-|build|xcode|gradle|pytest|completion-check)",
    re.IGNORECASE,
)




def _collect_model_review_signals(evaluation: dict | None) -> list[dict]:
    """Scan evaluation.json for entries carrying needsModelReview=True.

    Also auto-elevates repeated failures (same sameProblemKey appearing >=2
    times in failedChecks/qualityChecks within one round) into model-review
    signals, because code-only classifiers keep retrying the same fix and a
    human/model re-triage is needed.

    Returns a flat list of attention-signal dicts ready to render in a
    context pack. The Evaluator/Generator prompt templates tell the model
    that these entries require its analysis rather than code-only
    classification.
    """
    if not evaluation:
        return []

    signals: list[dict] = []

    def _needs_review(entry: dict) -> bool:
        val = entry.get("needsModelReview")
        return val is True

    def _entry_signal(source: str, entry: dict, idx: int) -> dict:
        sig = {
            "source": f"{source}[{idx}]",
            "triageSource": entry.get("triageSource", "requires_model_review"),
            "reason": str(entry.get("reason", ""))[:240],
            "evidence": entry.get("evidence"),
        }
        for k in ("id", "name", "result", "category", "failureClass", "recoveryAction", "confidence"):
            v = entry.get(k)
            if v is not None:
                sig[k] = v
        spk = entry.get("sameProblemKey")
        if spk:
            sig["sameProblemKey"] = spk
        return sig

    quality = evaluation.get("qualityChecks")
    if isinstance(quality, list):
        for idx, entry in enumerate(quality):
            if isinstance(entry, dict) and _needs_review(entry):
                signals.append(_entry_signal("qualityChecks", entry, idx))

    failed = evaluation.get("failedChecks")
    if isinstance(failed, list):
        for idx, entry in enumerate(failed):
            if isinstance(entry, dict) and _needs_review(entry):
                signals.append(_entry_signal("failedChecks", entry, idx))

    # Auto-elevate: same sameProblemKey appears >= 2 times across
    # failedChecks + qualityChecks in this round → model re-triage needed.
    # Code-only classifiers cannot break out of a loop when they keep
    # misclassifying the same root cause with the same recovery action.
    spk_entries: dict[str, list[dict]] = {}
    all_check_entries: list[tuple[str, dict]] = []
    if isinstance(quality, list):
        for idx, entry in enumerate(quality):
            if isinstance(entry, dict):
                all_check_entries.append((f"qualityChecks[{idx}]", entry))
    if isinstance(failed, list):
        for idx, entry in enumerate(failed):
            if isinstance(entry, dict):
                all_check_entries.append((f"failedChecks[{idx}]", entry))
    for source, entry in all_check_entries:
        spk = str(entry.get("sameProblemKey") or "").strip()
        if not spk:
            continue
        spk_entries.setdefault(spk, []).append({"source": source, "entry": entry})
    existing_spk_signals = {str(s.get("sameProblemKey") or "") for s in signals if s.get("sameProblemKey")}
    for spk, entries in spk_entries.items():
        if len(entries) < 2:
            continue
        if spk in existing_spk_signals:
            continue
        first = entries[0]["entry"]
        signals.append({
            "source": f"repeated_failure:{spk}",
            "triageSource": "requires_model_review",
            "sameProblemKey": spk,
            "occurrenceCount": len(entries),
            "name": first.get("name") or first.get("id"),
            "category": first.get("category") or first.get("failureClass"),
            "recoveryAction": str(first.get("recoveryAction", ""))[:200] if first.get("recoveryAction") else None,
            "reason": (
                f"Same failure (sameProblemKey={spk}) appeared {len(entries)} times in this round's checks. "
                "The deterministic classifier's recommended recovery action has been tried / is not making progress. "
                "Re-triage the root cause before retrying the same fix again."
            ),
            "confidence": "medium",
            "evidence": [e["source"] for e in entries],
            "autoElevated": True,
        })

    # Top-level modelReviewSignals (if the orchestrator wrote them)
    signals_block = evaluation.get("modelReviewSignals")
    if isinstance(signals_block, dict):
        review_list = signals_block.get("signals")
        if isinstance(review_list, list):
            for entry in review_list:
                if isinstance(entry, dict):
                    signals.append({
                        "source": "modelReviewSignals",
                        **{k: str(v)[:240] if isinstance(v, str) else v for k, v in entry.items()},
                    })

    return signals


def _collect_gate_failure_signals(task_dir: Path, iteration: int) -> list[dict]:
    """Collect model-review signals from gate failures (completion/workflow).

    When a gate check has failed for >=2 iterations, the deterministic flow
    is stuck — the model needs to step back and re-analyze why the gate
    keeps failing instead of retrying the same kind of fix.

    Returns a list of attention-signal dicts (same shape as
    _collect_model_review_signals output).
    """
    if iteration < 2:
        return []

    signals: list[dict] = []
    state = read_runtime_state(task_dir) or {}

    completion_check = str(state.get("completionCheck") or "").strip().lower()
    if completion_check == "fail":
        signals.append({
            "source": "gate_failure:completion_check",
            "triageSource": "requires_model_review",
            "name": "completion_check",
            "category": "gate_blocked",
            "gateType": "completion_check",
            "iteration": iteration,
            "confidence": "medium",
            "autoElevated": True,
            "reason": (
                f"Completion check has been failing for {iteration - 1}+ iterations. "
                "The loop keeps retrying but cannot pass the completion gate. "
                "Re-examine the root cause: are we fixing the wrong thing? "
                "Is the test strategy wrong? Do we need to replan the approach?"
            ),
            "recoveryAction": (
                "Step back and re-read completion-check issues and test evidence. "
                "If the same fix has been tried multiple times, try a different approach. "
                "If the TC design or acceptance criteria are wrong, use replan to fix them."
            ),
            "evidence": ["runtime-state.json:completionCheck", "completion-check report"],
        })

    workflow_check = str(state.get("workflowCheck") or "").strip().lower()
    if workflow_check == "fail":
        signals.append({
            "source": "gate_failure:workflow_check",
            "triageSource": "requires_model_review",
            "name": "workflow_check",
            "category": "gate_blocked",
            "gateType": "workflow_check",
            "iteration": iteration,
            "confidence": "medium",
            "autoElevated": True,
            "reason": (
                f"Workflow check has been failing for {iteration - 1}+ iterations. "
                "The artifact pipeline (Rxx -> AC-xxx -> TC-* -> Plan) is out of sync "
                "or missing required structure. Re-examine the artifacts rather than "
                "retrying the same code changes."
            ),
            "recoveryAction": (
                "Re-read workflow-check issues and fix artifact drift: "
                "ensure Requirements Rxx IDs map to TestCases, Plan checklist covers all TCs, "
                "and artifact IDs are consistent across files."
            ),
            "evidence": ["runtime-state.json:workflowCheck", "workflow-check report"],
        })

    return signals


def _collect_all_failure_overview(evaluation: dict | None) -> list[dict]:
    """Collect ALL failures (not just needsModelReview) as a compact overview.

    The model should review every failure, not just the ones code couldn't
    classify. Code classification is a starting point — the model may
    confirm or correct it.

    Returns a flat list of compact failure summaries.
    """
    if not evaluation:
        return []

    failures: list[dict] = []

    failed = evaluation.get("failedChecks")
    if isinstance(failed, list):
        for idx, entry in enumerate(failed):
            if not isinstance(entry, dict):
                continue
            failures.append({
                "source": f"failedChecks[{idx}]",
                "name": entry.get("name") or entry.get("id") or f"failure-{idx}",
                "category": entry.get("category") or entry.get("failureClass"),
                "triageSource": entry.get("triageSource", "code_deterministic"),
                "needsModelReview": bool(entry.get("needsModelReview", False)),
                "recoveryAction": (str(entry.get("recoveryAction", ""))[:200] if entry.get("recoveryAction") else None),
                "sameProblemKey": entry.get("sameProblemKey"),
                "result": entry.get("result"),
                "reason": str(entry.get("reason", ""))[:200],
            })

    quality = evaluation.get("qualityChecks")
    if isinstance(quality, list):
        for idx, entry in enumerate(quality):
            if not isinstance(entry, dict):
                continue
            result = str(entry.get("result") or "").lower()
            if result not in {"fail", "warn", "blocked"}:
                continue
            failures.append({
                "source": f"qualityChecks[{idx}]",
                "name": entry.get("name") or entry.get("id") or f"quality-{idx}",
                "category": entry.get("category") or entry.get("failureClass"),
                "triageSource": entry.get("triageSource", "code_deterministic"),
                "needsModelReview": bool(entry.get("needsModelReview", False)),
                "recoveryAction": (str(entry.get("recoveryAction", ""))[:200] if entry.get("recoveryAction") else None),
                "sameProblemKey": entry.get("sameProblemKey"),
                "result": result,
                "reason": str(entry.get("reason", ""))[:200],
            })

    tests = evaluation.get("testResults")
    if isinstance(tests, list):
        for idx, entry in enumerate(tests):
            if not isinstance(entry, dict):
                continue
            result = str(entry.get("result") or "").lower()
            if result not in {"fail", "blocked", "not_run", "skipped_dependency"}:
                continue
            failures.append({
                "source": f"testResults[{idx}]",
                "name": entry.get("name") or entry.get("id") or f"test-{idx}",
                "category": entry.get("category") or entry.get("failureClass"),
                "triageSource": entry.get("triageSource", "code_deterministic"),
                "needsModelReview": bool(entry.get("needsModelReview", False)),
                "recoveryAction": (str(entry.get("recoveryAction", ""))[:200] if entry.get("recoveryAction") else None),
                "sameProblemKey": entry.get("sameProblemKey"),
                "result": result,
                "reason": str(entry.get("reason", "") or entry.get("verdictReason", ""))[:200],
            })

    return failures


def _bytes_len(text: str) -> int:
    return len(text.encode("utf-8", errors="ignore"))


def _trim_to_bytes(text: str, limit: int) -> str:
    if _bytes_len(text) <= limit:
        return text
    # ASCII-heavy markdown dominates AutoMind artifacts; char slicing keeps this dependency-free.
    return text[: max(1_000, limit)]


def _latest_markdown_sections(text: str, section_limit: int = 3) -> list[str]:
    lines = text.splitlines()
    starts = [idx for idx, line in enumerate(lines) if line.startswith("## ")]
    if not starts:
        return []
    sections: list[str] = []
    for idx, start in enumerate(starts[-section_limit:]):
        end_candidates = [n for n in starts if n > start]
        end = end_candidates[0] if end_candidates else len(lines)
        sections.append("\n".join(lines[start:end]).strip())
    return [section for section in sections if section]


def _structured_history_excerpt(text: str, limit: int) -> str:
    """Summarize long Delivery/Validation-style history without losing latest state."""
    if _bytes_len(text) <= limit:
        return text
    headings = [line.strip() for line in text.splitlines() if line.startswith("#")][:60]
    key_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and STRUCTURED_KEY_LINE_RE.search(stripped):
            key_lines.append(stripped[:500])
    latest_sections = _latest_markdown_sections(text, section_limit=4)
    parts = [
        "# Compact structured excerpt",
        "",
        "Raw artifact is authoritative on disk; this excerpt keeps headings, key result/evidence lines, and latest sections.",
        "",
    ]
    if headings:
        parts.extend(["## Headings index", *[f"- {line}" for line in headings], ""])
    if key_lines:
        parts.extend(["## Key result/evidence lines", *[f"- {line}" for line in key_lines[-120:]], ""])
    if latest_sections:
        parts.extend(["## Latest sections", *latest_sections, ""])
    excerpt = "\n".join(parts).strip() + "\n"
    if _bytes_len(excerpt) > limit:
        tail_budget = max(4_000, limit // 3)
        excerpt = _trim_to_bytes(excerpt, max(1_000, limit - tail_budget))
        excerpt += "\n\n... [structured excerpt trimmed; see raw artifact for full history] ...\n\n"
        excerpt += text[-tail_budget:]
    return excerpt


def _context_excerpt_limit(path: Path, role: str, phase: str) -> int:
    name = path.name
    if name in HISTORY_ARTIFACT_NAMES:
        return EVALUATOR_HISTORY_ARTIFACT_EXCERPT_BYTES if phase == "evaluator" else GENERATOR_HISTORY_ARTIFACT_EXCERPT_BYTES
    if name in CORE_CONTRACT_NAMES:
        return EVALUATOR_CORE_REQUIRED_EXCERPT_BYTES if phase == "evaluator" else GENERATOR_CORE_REQUIRED_EXCERPT_BYTES
    if role == "required":
        return EVALUATOR_CORE_REQUIRED_EXCERPT_BYTES if phase == "evaluator" else GENERATOR_CORE_REQUIRED_EXCERPT_BYTES
    return EVALUATOR_DEFAULT_OPTIONAL_EXCERPT_BYTES if phase == "evaluator" else GENERATOR_DEFAULT_OPTIONAL_EXCERPT_BYTES


def _compact_text_for_context(text: str, limit: int, *, path: Path | None = None) -> tuple[str, str, bool]:
    """Return bounded model context while raw artifact remains on disk."""
    if limit <= 0:
        return "", "path_only", bool(text)
    if _bytes_len(text) <= limit:
        return text, "full", False
    if path and path.name in HISTORY_ARTIFACT_NAMES:
        return _structured_history_excerpt(text, limit), "structured_history_excerpt", True
    half = max(1_000, limit // 2)
    head = text[:half]
    tail = text[-half:]
    omitted = len(text) - len(head) - len(tail)
    marker = f"\n\n... [compact excerpt: omitted approximately {max(0, omitted)} characters; read the source file directly only if needed] ...\n\n"
    return head + marker + tail, "head_tail_excerpt", True


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _json_file_reference(item: dict, context_md_path: Path) -> dict:
    """Return machine/audit metadata without embedding source markdown text.

    The markdown context pack is the agent-facing excerpt. JSON is an index:
    path, hash, size, inclusion mode, and where the agent can read the excerpt.
    """
    ref = {k: v for k, v in item.items() if k not in {"excerpt", "content"}}
    excerpt = str(item.get("excerpt") or item.get("content") or "")
    ref["excerptBytes"] = _bytes_len(excerpt)
    ref["agentFacingContextPath"] = rel_to_root(context_md_path)
    if item.get("exists"):
        ref["sourceContent"] = "omitted_from_json_use_source_path_or_agent_facing_context"
    return ref


def _task_has_generator_output(task_dir: Path, iteration: int) -> bool:
    """Return True when this evaluator round follows a Generator run."""
    delivery_exists = (task_dir / "Delivery.md").exists()
    logs_dir = task_dir / "logs"
    generator_log_exists = False
    if logs_dir.exists():
        for iter_dir in logs_dir.glob("iter-*"):
            if (iter_dir / "generator.log").exists():
                generator_log_exists = True
                break
    state = read_runtime_state(task_dir) or {}
    return bool(delivery_exists or generator_log_exists or state.get("currentOwner") == "evaluator" and iteration > 1)


def evaluator_context_policy(task_dir: Path, iteration: int) -> dict:
    """Return the explicit allowlist/required-list policy for Evaluator context.

    The goal is to make context complete, audited, and non-redundant by policy, not by prompt:
    required files are the contract needed to judge the task; optional files add
    structure when present; forbidden files are raw Generator/runtime context.
    """
    required = [
        *requirement_contract_paths(task_dir),
        task_dir / "TestCases.md",
        task_dir / "Plan.md",
        task_dir / "Validation.md",
        task_dir / "runtime-state.json",
    ]
    if _task_has_generator_output(task_dir, iteration):
        required.append(task_dir / "Delivery.md")

    optional = [
        task_dir / "Reuse.md",
        task_dir / "phase-reuse" / "evaluator.md",
        task_dir / "runtime-state.json",
        task_dir / "evaluation.json",
        task_dir / "tc-attempts.json",
        task_dir / "logs" / f"iter-{iteration}" / "iteration-purpose.md",
    ]

    forbidden_patterns = [
        "logs/iter-*/generator.log",
        "logs/iter-*/agent-*.log",
        "raw agent transcripts",
        "Generator hidden reasoning / chain-of-thought",
        "supervisor conversation history not written into this context pack",
    ]

    return {
        "requiredFiles": required,
        "optionalFiles": optional,
        "forbiddenContext": forbidden_patterns,
        "coreRequiredExcerptBytes": EVALUATOR_CORE_REQUIRED_EXCERPT_BYTES,
        "defaultOptionalExcerptBytes": EVALUATOR_DEFAULT_OPTIONAL_EXCERPT_BYTES,
        "historyArtifactExcerptBytes": EVALUATOR_HISTORY_ARTIFACT_EXCERPT_BYTES,
    }


def generator_context_policy(task_dir: Path, iteration: int) -> dict:
    """Return the Generator context pack policy.

    Unlike Evaluator context, Generator context is not isolation-oriented. It is
    a reproducibility/audit aid: it records the task artifacts and latest
    validation state the Generator is expected to use before editing code.
    """
    required = [
        task_dir / "Brainstorm.md",
        *requirement_contract_paths(task_dir),
        task_dir / "TestCases.md",
        task_dir / "Plan.md",
        task_dir / "Validation.md",
        task_dir / "runtime-state.json",
    ]
    optional = [
        task_dir / "Reuse.md",
        task_dir / "phase-reuse" / "generator.md",
        task_dir / "runtime-state.json",
        task_dir / "evaluation.json",
        task_dir / "tc-attempts.json",
        task_dir / "logs" / f"iter-{iteration}" / "iteration-purpose.md",
        task_dir / "VerificationLedger.json",
        task_dir / "Delivery.md",
    ]
    return {
        "requiredFiles": required,
        "optionalFiles": optional,
        "coreRequiredExcerptBytes": GENERATOR_CORE_REQUIRED_EXCERPT_BYTES,
        "defaultOptionalExcerptBytes": GENERATOR_DEFAULT_OPTIONAL_EXCERPT_BYTES,
        "historyArtifactExcerptBytes": GENERATOR_HISTORY_ARTIFACT_EXCERPT_BYTES,
        "purpose": "Generator receives compact task context plus artifact paths for repair; raw evidence remains on disk.",
    }


def validate_generator_context_pack(pack: dict) -> tuple[bool, list[str]]:
    """Validate that the Generator context pack includes required artifacts."""
    issues: list[str] = []
    policy = pack.get("policy") or {}
    files = pack.get("files") or []
    file_by_path = {item.get("path"): item for item in files if isinstance(item, dict)}
    for path in policy.get("requiredFiles", []):
        item = file_by_path.get(path)
        if not item or not item.get("exists"):
            issues.append(f"required generator context file missing:{path}")
        elif item.get("bytes", 0) <= 0:
            issues.append(f"required generator context file empty:{path}")
    return len(issues) == 0, issues


def build_generator_context_pack(task_dir: Path, iteration: int, iter_log_dir: Path) -> dict:
    """Create an auditable context pack for the Generator phase."""
    ensure_dir(iter_log_dir)
    log_digest = build_log_digest(task_dir, iter_log_dir)
    policy = generator_context_policy(task_dir, iteration)
    required_files = policy["requiredFiles"]
    optional_files = policy["optionalFiles"]
    files = []
    for path in required_files + optional_files:
        text = _read_text_if_exists(path)
        stat_bytes = path.stat().st_size if path.exists() else 0
        role = "required" if path in required_files else "optional"
        excerpt_limit = _context_excerpt_limit(path, role, "generator")
        excerpt, included_mode, truncated = _compact_text_for_context(text, excerpt_limit, path=path) if path.exists() else ("", "missing", False)
        files.append({
            "path": rel_to_root(path),
            "role": role,
            "exists": path.exists(),
            "bytes": stat_bytes,
            "excerptBytesLimit": excerpt_limit,
            "includedMode": included_mode,
            "truncated": truncated,
            "sha256": _sha256_text(text) if path.exists() else None,
            "excerpt": excerpt,
        })

    pack = {
        "schema": "automind.generator_context_pack.v1",
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "taskCode": task_dir.name,
        "iteration": iteration,
        "policy": {
            "requiredFiles": [rel_to_root(path) for path in required_files],
            "optionalFiles": [rel_to_root(path) for path in optional_files],
            "coreRequiredExcerptBytes": policy["coreRequiredExcerptBytes"],
            "defaultOptionalExcerptBytes": policy["defaultOptionalExcerptBytes"],
            "historyArtifactExcerptBytes": policy["historyArtifactExcerptBytes"],
            "purpose": policy["purpose"],
            "completenessRule": "All required Generator context files should exist and be non-empty before coding; excerpts are bounded and raw artifacts remain authoritative on disk.",
        },
        "latestEvaluation": read_evaluation_json(task_dir) or {},
        "logDigest": {
            "jsonPath": rel_to_root(iter_log_dir / "log-digest.json"),
            "markdownPath": rel_to_root(iter_log_dir / "log-digest.md"),
            "oversizedCount": log_digest.get("oversizedCount", 0),
            "readPriority": log_digest.get("readPriority", []),
        },
        "files": files,
    }
    ok, issues = validate_generator_context_pack(pack)
    pack["validation"] = {"ok": ok, "issues": issues}

    json_path = iter_log_dir / "generator-context.json"
    md_path = iter_log_dir / "generator-context.md"

    json_pack = dict(pack)
    json_pack["files"] = [_json_file_reference(item, md_path) for item in files]
    json_pack["agentFacingContext"] = {
        "markdownPath": rel_to_root(md_path),
        "rule": "Read the markdown context pack; JSON is machine/audit metadata and intentionally omits source file content.",
    }
    json_path.write_text(json.dumps(json_pack, ensure_ascii=False, indent=2))

    md_parts = [
        f"# Generator Context Pack - {task_dir.name} iter-{iteration}",
        "",
        "This is the compact auditable task context AutoMind expects the Generator to use before editing.",
        "Raw artifacts remain on disk; this pack intentionally contains bounded excerpts to avoid polluting coding-agent context.",
        "",
        "## Validation",
        f"- OK: `{str(ok).lower()}`",
    ]
    if issues:
        md_parts.append("- Issues:")
        md_parts.extend(f"  - {issue}" for issue in issues)

    # ── Model-Review Attention Signals (TOP PRIORITY) ─────────────────────
    # Rendered FIRST so the model sees failed/suspicious entries BEFORE
    # requirements and plan. Deterministic code does NOT make final decisions
    # on these entries — the model must triage them, produce structured root
    # cause analysis, and then decide the fix.
    review_signals = _collect_model_review_signals(read_evaluation_json(task_dir))
    review_signals.extend(_collect_gate_failure_signals(task_dir, iteration))

    md_parts.extend([
        "",
        "## ⚠️ Model-Review Attention Signals (READ FIRST)",
        f"- Signals requiring your analysis: `{len(review_signals)}`",
    ])
    if review_signals:
        md_parts.extend([
            "",
            "**YOU MUST START HERE.** These entries were NOT resolved by deterministic code. "
            "Do NOT jump straight to editing code or re-running the same command.",
            "",
            "For each signal below:",
            "1. Re-read the raw evidence at the referenced path(s)",
            "2. Determine the actual root cause (not just the surface failure)",
            "3. Classify your confidence in the root cause: `high` (direct evidence), `medium` (strong inference), `low` (speculation)",
            "4. Decide the correct recovery action — which may be different from what the deterministic classifier suggested",
            "5. Record your analysis as a structured `rootCause` block in the next round's artifacts (Delivery.md and evaluation.json)",
            "",
            "### Signals",
        ])
        for idx, signal in enumerate(review_signals[:20], 1):
            source = signal.get("source", "unknown")
            triage = signal.get("triageSource", "requires_model_review")
            evidence = signal.get("evidence") or "see evaluation.json"
            reason = signal.get("reason") or ""
            confidence = signal.get("confidence")
            extras = []
            for k in ("id", "name", "result", "category", "failureClass", "recoveryAction", "sameProblemKey", "occurrenceCount", "gateType", "iteration", "autoElevated"):
                v = signal.get(k)
                if v is not None and v != "":
                    extras.append(f"`{k}`: {v}")
            md_parts.append(
                f"\n**{idx}. [{source}]**  \n"
                f"  - triageSource: `{triage}`"
                + (f"  \n  - code-confidence: `{confidence}`" if confidence else "")
                + f"  \n  - evidence: `{evidence}`"
                + (f"  \n  - " + "; ".join(extras) if extras else "")
                + (f"  \n  - reason: {reason}" if reason else "")
            )
        md_parts.extend([
            "",
            "After analyzing all signals, proceed to the plan and requirements. "
            "If you find the deterministic classifier was wrong, correct the category and recovery action in your output.",
        ])

    # ── All Failures Overview ─────────────────────────────────────────────
    # List EVERY failure so the model can review and potentially correct
    # code-classified entries. Code classification is a starting point,
    # not the final answer. The model may confirm or override any entry.
    all_failures = _collect_all_failure_overview(read_evaluation_json(task_dir))
    code_classified = [f for f in all_failures if not f["needsModelReview"]]

    md_parts.extend([
        "",
        "### All Failures Overview (Review & Correct As Needed)",
        f"- Total failures: `{len(all_failures)}` (code-classified: `{len(code_classified)}`, model-review pending: `{len(all_failures) - len(code_classified)}`)",
    ])
    if code_classified:
        md_parts.extend([
            "",
            "The entries below were classified by deterministic code. "
            "**You may override any of them** if the evidence points to a different root cause or recovery action. "
            "Do not blindly trust the code classifier — verify with evidence.",
            "",
            "| # | Source | Category | Recovery Action | triageSource | Reason |",
            "|---|--------|----------|-----------------|--------------|--------|",
        ])
        for idx, fail in enumerate(code_classified[:30], 1):
            src = fail.get("source", "?")
            cat = fail.get("category") or "—"
            ra = fail.get("recoveryAction") or "—"
            ts = fail.get("triageSource") or "—"
            reason = (fail.get("reason") or "")[:80]
            spk = fail.get("sameProblemKey")
            if spk:
                reason = f"[{spk}] {reason}"
            md_parts.append(f"| {idx} | `{src}` | {cat} | {ra} | `{ts}` | {reason} |")
        if len(code_classified) > 30:
            md_parts.append(f"| ... | ({len(code_classified) - 30} more) | | | | |")
    else:
        md_parts.append("- No code-classified failures to review.")
    md_parts.extend([
        "",
        "If you correct any code-classified failure, update the entry in your output with:",
        "- `triageSource: model_reviewed`",
        "- `needsModelReview: false`",
        "- Corrected `category` and `recoveryAction`",
        "- A `rootCause` object with your confidence and evidence",
    ])
    md_parts.extend([
        "",
        "---",
        "",
    ])

    md_parts.extend([
        "## Contract",
        "- Generator receives compact excerpts here; raw task files/logs remain authoritative on disk.",
        "- Read this markdown file as the agent-facing context; `generator-context.json` is machine/audit metadata and intentionally omits source file content.",
        "- Read raw files only when the compact excerpt/path is insufficient for the next concrete edit.",
        "- Generator must prioritize current Requirements.md, TestCases.md, Plan.md, latest Validation.md, and evaluation.json over old Reuse.md / phase-reuse hints.",
        "- If phase-reuse/generator.md exists, use it as concise phase-specific indexed guidance, not as a requirement override.",
        "- Generator must update Delivery.md before ending this round.",
        "",
        "## Log Reading Policy",
        f"- Read `{rel_to_root(iter_log_dir / 'log-digest.md')}` before raw logs.",
        "- Prefer evaluation/runtime-state/Validation/Delivery and digest summaries; use targeted grep/tail for large raw logs.",
        "- Do not read oversized raw logs or build intermediates wholesale by default.",
        "",
    ])
    md_parts.extend([
        "",
        "## Required Files",
    ])
    for path in pack["policy"]["requiredFiles"]:
        item = next((f for f in files if f["path"] == path), None)
        md_parts.append(f"- `{path}` — {'included' if item and item['exists'] else 'missing'}" + (f" bytes={item['bytes']} mode={item['includedMode']} sha256={item['sha256']}" if item and item.get("sha256") else ""))
    md_parts.append("\n## Optional Files")
    for path in pack["policy"]["optionalFiles"]:
        item = next((f for f in files if f["path"] == path), None)
        md_parts.append(f"- `{path}` — {'included' if item and item['exists'] else 'missing'}" + (f" bytes={item['bytes']} mode={item['includedMode']} sha256={item['sha256']}" if item and item.get("sha256") else ""))
    for item in files:
        if not item["exists"]:
            continue
        md_parts.extend([
            "",
            f"## File excerpt: `{item['path']}` ({item['role']}; bytes={item['bytes']}; mode={item['includedMode']})",
            "```",
            item["excerpt"],
            "```",
        ])
    md_path.write_text("\n".join(md_parts) + "\n")

    return {
        "jsonPath": json_path,
        "markdownPath": md_path,
        "allowedFiles": [item["path"] for item in files if item["exists"]],
        "validationOk": ok,
        "validationIssues": issues,
    }


def build_evaluator_capability_surface(task_dir: Path) -> dict:
    """Describe what the context-isolated Evaluator is allowed/expected to do.

    Context is intentionally small; capability is intentionally strong. The
    Evaluator may independently inspect the project and run deterministic
    verification adapters so it is not merely reviewing Generator prose.
    """
    state = read_runtime_state(task_dir) or {}
    task_type = state.get("taskType", "")
    harness = state.get("harnessProfile") or {}
    deterministic = []

    script_command = state.get("scriptCommand") or state.get("verifyCommand")
    if isinstance(script_command, str) and script_command.strip():
        deterministic.append({
            "name": "script-command",
            "command": f"./automind.sh script-command {task_dir.name}",
            "purpose": "Run the explicit project-declared verification command and emit evaluation.json evidence.",
        })
    if task_type == "android" or (isinstance(harness, dict) and harness.get("name") == "android-v1"):
        deterministic.extend([
            {
                "name": "android-preflight",
                "command": f"./automind.sh android-preflight {task_dir.name}",
                "purpose": "Check Android device/tool readiness before blaming product code.",
            },
            {
                "name": "android-probe-flow",
                "command": f"./automind.sh android-probe-flow {task_dir.name}",
                "purpose": "Install/launch/drive Android app flow and collect UI/log/screenshot evidence.",
            },
        ])
    if task_type in {"web", "browser"} or (task_dir / "probe-flow.web.json").exists() or (isinstance(harness, dict) and harness.get("name") == "web-probe-flow"):
        deterministic.append({
            "name": "web-probe-flow",
            "command": f"./automind.sh web-probe-flow {task_dir.name}",
            "purpose": "Validate Web UI action intent, run project-native E2E command when configured, and collect Client UI action evidence.",
        })

    if task_type == "ios" or (task_dir / "probe-flow.ios.json").exists() or (isinstance(harness, dict) and harness.get("name") == "ios-probe-flow"):
        deterministic.extend([
            {
                "name": "ios-preflight",
                "command": f"./automind.sh ios-preflight {task_dir.name}",
                "purpose": "Check iOS device/signing/tool readiness before blaming product code.",
            },
            {
                "name": "ios-probe-flow",
                "command": f"./automind.sh ios-probe-flow {task_dir.name}",
                "purpose": "Run iOS probe-flow/XCUITest-backed app validation and collect evidence.",
            },
            {
                "name": "ios-xcuitest",
                "command": f"./automind.sh ios-xcuitest {task_dir.name}",
                "purpose": "Run configured XCUITest evaluator on simulator/physical device.",
            },
        ])

    deterministic.append({
        "name": "quality-check",
        "command": f"./automind.sh quality-check {task_dir.name}",
        "purpose": "Run lightweight quality checks after functional evidence exists.",
    })

    return {
        "principle": "complete audited non-redundant context + no Generator reasoning pollution + full independent verification capability",
        "canInspectProjectFiles": True,
        "canRunBuildTestDeviceCommands": True,
        "canCollectEvidence": ["logs", "screenshots", "ui_hierarchy", "dom_snapshot", "web_trace", "xcresult", "command_output", "evaluation.json", "action-trace.jsonl"],
        "deterministicEvaluators": deterministic,
        "notes": [
            "Evaluator context is not the same as Evaluator capability.",
            "Evaluator should actively verify the app/product when platform configuration is available.",
            "If phase-reuse/evaluator.md exists, use it as concise phase-specific indexed guidance, not as a requirement override.",
            "Environment/device/signing failures must be classified separately from product failures.",
        ],
    }


def validate_evaluator_context_pack(pack: dict) -> tuple[bool, list[str]]:
    """Validate that the Evaluator context pack is complete and non-leaky."""
    issues: list[str] = []
    policy = pack.get("policy") or {}
    files = pack.get("files") or []
    file_by_path = {item.get("path"): item for item in files if isinstance(item, dict)}

    if pack.get("isolation", {}).get("inheritsGeneratorContext") is not False:
        issues.append("isolation.inheritsGeneratorContext must be false")
    if pack.get("isolation", {}).get("freshProcessRequired") is not True:
        issues.append("isolation.freshProcessRequired must be true")

    for path in policy.get("requiredFiles", []):
        item = file_by_path.get(path)
        if not item or not item.get("exists"):
            issues.append(f"required context file missing:{path}")
        elif item.get("bytes", 0) <= 0:
            issues.append(f"required context file empty:{path}")

    forbidden_needles = [
        "SECRET_GENERATOR_TRANSCRIPT_SHOULD_NOT_APPEAR",
        "<generator_hidden_reasoning>",
    ]
    for item in files:
        path = str(item.get("path", ""))
        content = str(item.get("excerpt", ""))
        if path.endswith("generator.log") or "/generator.log" in path:
            issues.append(f"forbidden file included:{path}")
        for needle in forbidden_needles:
            if needle in content:
                issues.append(f"forbidden generator context leaked:{needle}")

    return len(issues) == 0, issues


def build_evaluator_context_pack(task_dir: Path, iteration: int, iter_log_dir: Path) -> dict:
    """Create the only orchestrator-provided context for an agent Evaluator.

    The Evaluator must be context-isolated from the Generator: it is invoked as
    a fresh process and receives a bounded, auditable context pack instead of
    inheriting Generator chat/transcript/log context. The pack intentionally
    includes task contracts and latest delivery notes, but excludes
    `generator.log`, hidden chain-of-thought/transcripts, and previous agent
    invocation stdout/stderr.
    """
    ensure_dir(iter_log_dir)
    log_digest = build_log_digest(task_dir, iter_log_dir)

    policy = evaluator_context_policy(task_dir, iteration)
    required_files = policy["requiredFiles"]
    optional_files = policy["optionalFiles"]
    allowed_files = required_files + optional_files

    files = []
    for path in allowed_files:
        text = _read_text_if_exists(path)
        stat_bytes = path.stat().st_size if path.exists() else 0
        role = "required" if path in required_files else "optional"
        excerpt_limit = _context_excerpt_limit(path, role, "evaluator")
        excerpt, included_mode, truncated = _compact_text_for_context(text, excerpt_limit, path=path) if path.exists() else ("", "missing", False)
        files.append({
            "path": rel_to_root(path),
            "role": role,
            "exists": path.exists(),
            "bytes": stat_bytes,
            "excerptBytesLimit": excerpt_limit,
            "includedMode": included_mode,
            "truncated": truncated,
            "sha256": _sha256_text(text) if path.exists() else None,
            "excerpt": excerpt,
        })

    pack = {
        "schema": "automind.evaluator_context_pack.v1",
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "taskCode": task_dir.name,
        "iteration": iteration,
        "isolation": {
            "freshProcessRequired": True,
            "inheritsGeneratorContext": False,
            "purpose": "Evaluator receives only auditable task artifacts and may independently inspect code/run verification commands.",
            "forbiddenContext": policy["forbiddenContext"],
        },
        "policy": {
            "requiredFiles": [rel_to_root(path) for path in required_files],
            "optionalFiles": [rel_to_root(path) for path in optional_files],
            "forbiddenContext": policy["forbiddenContext"],
            "coreRequiredExcerptBytes": policy["coreRequiredExcerptBytes"],
            "defaultOptionalExcerptBytes": policy["defaultOptionalExcerptBytes"],
            "historyArtifactExcerptBytes": policy["historyArtifactExcerptBytes"],
            "completenessRule": "All required files must exist and be non-empty before launching an agent Evaluator; context excerpts are bounded and raw artifacts remain authoritative on disk.",
            "nonRedundancyRule": "Include all required verification context, but never embed raw Generator logs/transcripts or code-authoring reasoning.",
        },
        "capabilitySurface": build_evaluator_capability_surface(task_dir),
        "logDigest": {
            "jsonPath": rel_to_root(iter_log_dir / "log-digest.json"),
            "markdownPath": rel_to_root(iter_log_dir / "log-digest.md"),
            "oversizedCount": log_digest.get("oversizedCount", 0),
            "readPriority": log_digest.get("readPriority", []),
        },
        "allowedTaskFiles": [item["path"] for item in files if item["exists"]],
        "files": files,
    }

    ok, issues = validate_evaluator_context_pack(pack)
    pack["validation"] = {"ok": ok, "issues": issues}

    json_path = iter_log_dir / "evaluator-context.json"
    md_path = iter_log_dir / "evaluator-context.md"

    json_pack = dict(pack)
    json_pack["files"] = [_json_file_reference(item, md_path) for item in files]
    json_pack["agentFacingContext"] = {
        "markdownPath": rel_to_root(md_path),
        "rule": "Read the markdown context pack; JSON is machine/audit metadata and intentionally omits source file content.",
    }
    json_path.write_text(json.dumps(json_pack, ensure_ascii=False, indent=2))

    md_parts = [
        f"# Evaluator Context Pack - {task_dir.name} iter-{iteration}",
        "",
        "This is the compact orchestrator-provided context for the Evaluator.",
        "Raw artifacts remain on disk; this pack intentionally contains bounded excerpts plus paths/hashes.",
        "",
        "## Validation",
        f"- OK: `{str(ok).lower()}`",
    ]
    if issues:
        md_parts.append("- Issues:")
        md_parts.extend(f"  - {issue}" for issue in issues)

    # ── Model-Review Attention Signals (TOP PRIORITY) ─────────────────────
    # Evaluator sees these FIRST so it triages ambiguous failures before
    # diving into verification. Code-only classifiers do NOT make final
    # decisions — the Evaluator model must produce a root cause analysis
    # with confidence, then update the entry to triageSource=model_reviewed.
    eval_review_signals = _collect_model_review_signals(read_evaluation_json(task_dir))
    eval_review_signals.extend(_collect_gate_failure_signals(task_dir, iteration))

    md_parts.extend([
        "",
        "## ⚠️ Model-Review Attention Signals (READ FIRST)",
        f"- Signals requiring your analysis: `{len(eval_review_signals)}`",
    ])
    if eval_review_signals:
        md_parts.extend([
            "",
            "**YOU MUST START HERE.** These entries were NOT resolved by deterministic code. "
            "You MUST analyze each one and produce a structured root-cause assessment.",
            "",
            "For each signal below:",
            "1. Re-read the raw evidence at the referenced path(s)",
            "2. Determine the actual root cause (not just the surface failure)",
            "3. Classify your confidence: `high` (direct evidence), `medium` (strong inference), `low` (speculation)",
            "4. Set `triageSource: model_reviewed`, `needsModelReview: false`, a concrete `category`, and a concrete `recoveryAction` in your evaluation.json output",
            "5. Include a `rootCause` object with: `summary`, `confidence` (high/medium/low), `evidence`, `correctedCategory` (if different from code classification), `recommendedAction`",
            "Do NOT leave any entry with `needsModelReview: true` in your output.",
            "",
            "### Signals",
        ])
        for idx, signal in enumerate(eval_review_signals[:20], 1):
            source = signal.get("source", "unknown")
            triage = signal.get("triageSource", "requires_model_review")
            evidence = signal.get("evidence") or "see evaluation.json"
            reason = signal.get("reason") or ""
            confidence = signal.get("confidence")
            extras = []
            for k in ("id", "name", "result", "category", "failureClass", "recoveryAction", "sameProblemKey", "occurrenceCount", "gateType", "iteration", "autoElevated"):
                v = signal.get(k)
                if v is not None and v != "":
                    extras.append(f"`{k}`: {v}")
            md_parts.append(
                f"\n**{idx}. [{source}]**  \n"
                f"  - triageSource: `{triage}`"
                + (f"  \n  - code-confidence: `{confidence}`" if confidence else "")
                + f"  \n  - evidence: `{evidence}`"
                + (f"  \n  - " + "; ".join(extras) if extras else "")
                + (f"  \n  - reason: {reason}" if reason else "")
            )
        md_parts.extend([
            "",
            "After triaging all signals, proceed to verify the current test cases. "
            "If you find the deterministic classifier was wrong, correct the category and recovery action in your output.",
        ])

    # ── All Failures Overview ─────────────────────────────────────────────
    eval_all_failures = _collect_all_failure_overview(read_evaluation_json(task_dir))
    eval_code_classified = [f for f in eval_all_failures if not f["needsModelReview"]]

    md_parts.extend([
        "",
        "### All Failures Overview (Review & Correct As Needed)",
        f"- Total failures: `{len(eval_all_failures)}` (code-classified: `{len(eval_code_classified)}`, model-review pending: `{len(eval_all_failures) - len(eval_code_classified)}`)",
    ])
    if eval_code_classified:
        md_parts.extend([
            "",
            "The entries below were classified by deterministic code. "
            "**You may override any of them** if the evidence points to a different root cause or recovery action. "
            "Do not blindly trust the code classifier — verify with evidence.",
            "",
            "| # | Source | Category | Recovery Action | triageSource | Reason |",
            "|---|--------|----------|-----------------|--------------|--------|",
        ])
        for idx, fail in enumerate(eval_code_classified[:30], 1):
            src = fail.get("source", "?")
            cat = fail.get("category") or "—"
            ra = fail.get("recoveryAction") or "—"
            ts = fail.get("triageSource") or "—"
            reason = (fail.get("reason") or "")[:80]
            spk = fail.get("sameProblemKey")
            if spk:
                reason = f"[{spk}] {reason}"
            md_parts.append(f"| {idx} | `{src}` | {cat} | {ra} | `{ts}` | {reason} |")
        if len(eval_code_classified) > 30:
            md_parts.append(f"| ... | ({len(eval_code_classified) - 30} more) | | | | |")
    else:
        md_parts.append("- No code-classified failures to review.")
    md_parts.extend([
        "",
        "If you correct any code-classified failure, update the entry in your output with:",
        "- `triageSource: model_reviewed`",
        "- `needsModelReview: false`",
        "- Corrected `category` and `recoveryAction`",
        "- A `rootCause` object with your confidence and evidence",
    ])
    md_parts.extend([
        "",
        "---",
        "",
    ])

    md_parts.extend([
        "## Isolation Contract",
        "- Evaluator is invoked in a fresh process/session.",
        "- Evaluator must not inherit Generator conversation, stdout/stderr logs, hidden reasoning, or supervisor transcript.",
        "- Evaluator may independently inspect product/source files and run verification commands to collect evidence.",
        "- Evaluator must not read `logs/iter-*/generator.log` unless a human explicitly changes this contract.",
        "",
        "## Context Policy",
        "- Required files must exist and be non-empty before launching an agent Evaluator.",
        "- Optional files are included only when present.",
        "- Raw Generator logs/transcripts are forbidden context and are not embedded.",
        "- Task artifacts are embedded as bounded excerpts in this markdown; `evaluator-context.json` is machine/audit metadata and intentionally omits source file content.",
        "- Read raw files only when needed for a concrete verification decision.",
        f"- Read `{rel_to_root(iter_log_dir / 'log-digest.md')}` before any raw log; use targeted grep/tail for large logs.",
        "",
    ])
    md_parts.extend([
        "",
        "## Capability Surface",
        f"- Principle: {pack['capabilitySurface']['principle']}",
        f"- Can inspect project files: `{str(pack['capabilitySurface']['canInspectProjectFiles']).lower()}`",
        f"- Can run build/test/device commands: `{str(pack['capabilitySurface']['canRunBuildTestDeviceCommands']).lower()}`",
        "- Deterministic evaluators:",
    ])
    for evaluator in pack["capabilitySurface"]["deterministicEvaluators"]:
        md_parts.append(f"  - `{evaluator['name']}`: `{evaluator['command']}` — {evaluator['purpose']}")
    md_parts.extend([
        "",
        "### Required Files",
    ])
    for path in pack["policy"]["requiredFiles"]:
        item = file_by_path = next((f for f in files if f["path"] == path), None)
        md_parts.append(f"- `{path}` — {'included' if item and item['exists'] else 'missing'}" + (f" bytes={item['bytes']} mode={item['includedMode']} sha256={item['sha256']}" if item and item.get("sha256") else ""))
    md_parts.append("\n### Optional Files")
    for path in pack["policy"]["optionalFiles"]:
        item = next((f for f in files if f["path"] == path), None)
        md_parts.append(f"- `{path}` — {'included' if item and item['exists'] else 'missing'}" + (f" bytes={item['bytes']} mode={item['includedMode']} sha256={item['sha256']}" if item and item.get("sha256") else ""))
    for item in files:
        if not item["exists"]:
            continue
        md_parts.extend([
            "",
            f"## File excerpt: `{item['path']}` ({item['role']}; bytes={item['bytes']}; mode={item['includedMode']})",
            "```",
            item["excerpt"],
            "```",
        ])
    md_path.write_text("\n".join(md_parts) + "\n")

    return {
        "jsonPath": json_path,
        "markdownPath": md_path,
        "allowedFiles": [item["path"] for item in files if item["exists"]],
        "forbiddenContext": policy["forbiddenContext"],
        "validationOk": ok,
        "validationIssues": issues,
    }
