#!/usr/bin/env python3
"""Lightweight AutoMind quality evaluator.

Runs after functional evaluation. It reads task evidence and repo diff, produces a
quality summary, and can merge qualityChecks into the task's evaluation.json.
V1 is intentionally lenient: heuristic findings default to warn; only obvious
hard failures such as timeouts/crashes/stuck loading are fail.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from datetime import datetime
from typing import Any


def _normalize_result_value(raw: Any) -> str:
    """Normalize a testResult value, reusing the canonical orchestrator logic.

    Falls back to a local alias map when ``orchestrator`` is not importable so
    this script keeps working when run standalone, but prefers the shared
    function to avoid required/pass口径 drifting from completion-check.
    """
    try:
        from automind_paths import RUNTIME_ROOT  # type: ignore

        if str(RUNTIME_ROOT) not in sys.path:
            sys.path.insert(0, str(RUNTIME_ROOT))
        from orchestrator.artifacts import normalize_test_result_value  # type: ignore

        return normalize_test_result_value(raw)
    except Exception:
        value = str(raw or "").strip().lower()
        aliases = {"passed": "pass", "ok": "pass", "success": "pass", "failed": "fail", "failure": "fail"}
        return aliases.get(value, value)


HARD_FAIL_PATTERNS = [
    ("stuck_loading", re.compile(r"\b(stuck loading|loading stuck|infinite loading)\b", re.I)),
]
# Crash/timeout are intentionally not generic hard keyword matches. They are
# promoted to hard quality failures only when structured current-run evidence
# carries enough product context (stack/page/process for crashes, user-visible
# app hang for timeouts). Generic raw syslog / verifier timeout keywords become
# warnings so they do not create false completion failures.
CRASH_SIGNAL_RE = re.compile(r"\b(crash|fatal exception|segmentation fault|SIGABRT|SIGSEGV|OOMCrash)\b", re.I)
TIMEOUT_SIGNAL_RE = re.compile(r"\b(timeout|timed out|TimeoutError|TimeoutExpired)\b", re.I)
CRASH_STACK_RE = re.compile(r"(Exception Type:|Triggered by Thread|Last Exception Backtrace|Thread \d+ Crashed|backtrace|call stack|崩溃堆栈|堆栈)", re.I)
CRASH_PAGE_RE = re.compile(r"(page|screen|viewcontroller|scene|last_scene|当前页面|发生页面|页面)", re.I)
PRODUCT_TIMEOUT_RE = re.compile(r"(app unresponsive|ui hang|main thread hang|watchdog|stuck loading|loading stuck|infinite loading|ANR|卡死|无响应)", re.I)
VERIFIER_TIMEOUT_RE = re.compile(r"(TimeoutExpired|subprocess|automation|accessibility|probe-flow|xcuitest|idevicesyslog|adb|devicectl|verifier|runner|script)", re.I)
GENERIC_NETWORK_TIMEOUT_RE = re.compile(r"(CFNetwork|TTNet|NSURLErrorDomain|request was timeout|storekitd|runningboardd|mDNSResponder|assertion.*timeout)", re.I)
WARN_PATTERNS = [
    ("retry", re.compile(r"\b(retry|retried|retries)\b", re.I)),
    ("slow", re.compile(r"\b(slow|jank|lag|\u5361\u987f|\u8017\u65f6\u8fc7\u957f)\b", re.I)),
]
ARCH_WARN_PATTERNS = [
    ("todo_or_hack", re.compile(r"\b(TODO|FIXME|HACK|temporary|\u4e34\u65f6|hack)\b", re.I)),
    ("direct_network_keyword", re.compile(r"\b(fetch\(|axios\.|URLSession|OkHttpClient|Retrofit\(|request\()", re.I)),
]
DURATION_RE = re.compile(r"(?:duration_ms|elapsed_ms|latency_ms|cost_ms|time_ms|first_screen_ms|loading_duration_ms)[\"'=:\s]+(\d+)", re.I)

QUALITY_EVIDENCE_SUFFIXES = {".log", ".txt", ".md", ".json"}
QUALITY_EVIDENCE_EXCLUDED_NAMES = {
    "brainstorm.md",
    "delivery.md",
    "evaluation.json",
    "generator.log",
    "generator-context.md",
    "generator-context.json",
    "generator-prompt.md",
    "plan.md",
    "require.md",
    "spec.md",
    "runtime-state.json",
    "testcases.md",
    "validation.md",
    "verificationledger.json",
    "evaluator-context.md",
    "evaluator-context.json",
    "evaluator-prompt.md",
    "quality-check.log",
    "quality-summary.json",
    "log-digest.md",
    "log-digest.json",
}


def load_json(path: pathlib.Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_git(root: pathlib.Path, args: list[str]) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, timeout=20)
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def latest_iteration(task_dir: pathlib.Path) -> int:
    state = load_json(task_dir / "runtime-state.json") or {}
    try:
        return max(1, int(state.get("iteration") or 1))
    except Exception:
        return 1


def iter_log_dir(task_dir: pathlib.Path, iteration: int | None) -> pathlib.Path:
    it = iteration or latest_iteration(task_dir)
    return task_dir / "logs" / f"iter-{it}"


def should_scan_text_evidence(path: pathlib.Path, task_dir: pathlib.Path) -> bool:
    """Return whether a text evidence file should feed quality heuristics.

    Quality checks should judge the current functional evidence, not AutoMind's
    own prompts, context packs, Generator transcripts, or previous
    quality-summary output. Those orchestration files contain policy words such
    as "timeout" and "retry" that can otherwise create false hard failures.
    """
    if path.suffix.lower() not in QUALITY_EVIDENCE_SUFFIXES:
        return False
    name = path.name.lower()
    if name in QUALITY_EVIDENCE_EXCLUDED_NAMES:
        return False
    if name.startswith("generator-") or name.startswith("agent-"):
        return False
    if name.endswith("-prompt.md") or "-context." in name:
        return False
    if name.startswith("quality-"):
        return False
    try:
        rel_parts = [part.lower() for part in path.relative_to(task_dir).parts]
    except ValueError:
        rel_parts = [part.lower() for part in path.parts]
    if any(part in {"summary.md"} for part in rel_parts):
        return False
    return True


def collect_evidence_paths_from_evaluation(task_dir: pathlib.Path) -> list[pathlib.Path]:
    """Return evidence paths explicitly declared by the current evaluation."""
    evaluation = load_json(task_dir / "evaluation.json")
    if not isinstance(evaluation, dict):
        return []
    paths: list[pathlib.Path] = []
    workspace_root = task_dir
    if len(task_dir.parents) >= 3 and task_dir.parent.name == "tasks" and task_dir.parent.parent.name == ".automind":
        workspace_root = task_dir.parents[2]

    def add_path(raw: Any) -> None:
        if not isinstance(raw, str) or not raw.strip():
            return
        raw_path = raw.strip()
        p = pathlib.Path(raw_path)
        if not p.is_absolute():
            if raw_path.startswith(".automind/") or raw_path.startswith("logs/"):
                # AutoMind artifacts use both workspace-relative
                # `.automind/tasks/...` paths and task-relative `logs/...`
                # paths. Resolve both without falling back to unrelated files.
                p = (workspace_root / p) if raw_path.startswith(".automind/") else (task_dir / p)
            else:
                p = task_dir / p
        try:
            resolved = p.resolve()
            task_root = task_dir.resolve()
            if not resolved.is_relative_to(task_root):
                return
            p = resolved
        except Exception:
            pass
        if p.is_file():
            paths.append(p)

    for item in evaluation.get("evidence", []) or []:
        if isinstance(item, dict):
            add_path(item.get("path"))
        else:
            add_path(item)
    for result in evaluation.get("testResults", []) or []:
        if isinstance(result, dict):
            for item in result.get("evidence", []) or []:
                add_path(item)
    return paths


def collect_text_evidence(task_dir: pathlib.Path, log_dir: pathlib.Path, max_chars: int = 200_000) -> str:
    chunks: list[str] = []
    candidates: list[pathlib.Path] = []
    # Current iteration evidence first. Do not recurse through the whole task
    # directory: prior iterations and context packs are history/control-plane
    # data, not proof of current runtime quality.
    if log_dir.exists():
        candidates.extend([p for p in log_dir.rglob("*") if p.is_file()])
    candidates.extend(collect_evidence_paths_from_evaluation(task_dir))
    for path in sorted(set(candidates)):
        if not should_scan_text_evidence(path, task_dir):
            continue
        if any(secret in path.name.lower() for secret in ["secret", "token", "password", "credential", "key"]):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        chunks.append(f"\n--- {path.relative_to(task_dir)} ---\n{text[:20000]}")
        if sum(len(c) for c in chunks) > max_chars:
            break
    return "\n".join(chunks)[:max_chars]





def _matching_excerpts(text: str, pattern: re.Pattern, radius: int = 280, limit: int = 8) -> list[str]:
    """Return windowed excerpts around every signal occurrence (not just the first).

    Classification should not hinge on a single first-match window: a crash/timeout
    keyword can appear far from its stack/page context, so we evaluate each
    occurrence's own neighborhood and let the caller pick the strongest verdict.
    """
    excerpts: list[str] = []
    for match in pattern.finditer(text):
        start = max(0, match.start() - radius)
        end = min(len(text), match.end() + radius)
        excerpts.append(text[start:end].replace("\n", " ")[:700])
        if len(excerpts) >= limit:
            break
    return excerpts


def _evidence_scope_for_signal(excerpt: str) -> str:
    lowered = excerpt.lower()
    if VERIFIER_TIMEOUT_RE.search(excerpt):
        return "automation_or_verifier"
    if GENERIC_NETWORK_TIMEOUT_RE.search(excerpt):
        return "generic_system_or_network_log"
    if "log-digest" in lowered or "generator.log" in lowered or "context" in lowered:
        return "control_plane_or_digest"
    return "runtime_log"


def classify_crash_timeout_quality(text: str, log_dir: pathlib.Path, task_dir: pathlib.Path) -> list[dict]:
    """Classify crash/timeout signals without turning raw keywords into false hard fails.

    A crash is a hard quality failure only when the current evidence includes a
    stack-like artifact and page/screen/process context. Otherwise it is a
    warning that asks Evaluator to collect stack/page evidence before treating it
    as product failure. A timeout is hard only for product-visible hangs; verifier
    or generic network/syslog timeouts are warnings.
    """
    checks: list[dict] = []
    crash_excerpts = _matching_excerpts(text, CRASH_SIGNAL_RE)
    if crash_excerpts:
        evidence = str(log_dir.relative_to(task_dir)) if log_dir.exists() else "task evidence"
        # Evaluate every crash occurrence's own neighborhood and keep the
        # strongest verdict: a single hard window promotes to fail, otherwise
        # the best-scoped warning excerpt is reported.
        hard_excerpt: str | None = None
        hard_scope = ""
        for excerpt in crash_excerpts:
            scope = _evidence_scope_for_signal(excerpt)
            has_stack = bool(CRASH_STACK_RE.search(excerpt))
            has_page = bool(CRASH_PAGE_RE.search(excerpt))
            if has_stack and has_page and scope == "runtime_log":
                hard_excerpt = excerpt
                hard_scope = scope
                break
        if hard_excerpt is not None:
            checks.append({
                "id": "crash_with_stack",
                "category": "stability",
                "result": "fail",
                "failureClass": "product_crash_with_stack",
                "reason": "Structured product crash evidence found with stack/page context; record stack/page and route to repair if reproducible.",
                "evidence": evidence,
                "diagnostic": {"scope": hard_scope, "hasStack": True, "hasPage": True, "excerpt": hard_excerpt},
            })
        else:
            excerpt = crash_excerpts[0]
            scope = _evidence_scope_for_signal(excerpt)
            checks.append({
                "id": "crash-signal-needs-stack",
                "category": "stability",
                "result": "warn",
                "failureClass": "crash_signal_needs_stack",
                "reason": "Crash-like keyword found, but not enough current product stack/page evidence to treat as a hard failure. Collect crash stack and occurred page if stable/reproducible.",
                "evidence": evidence,
                "diagnostic": {
                    "scope": scope,
                    "hasStack": bool(CRASH_STACK_RE.search(excerpt)),
                    "hasPage": bool(CRASH_PAGE_RE.search(excerpt)),
                    "excerpt": excerpt,
                },
            })

    timeout_excerpts = _matching_excerpts(text, TIMEOUT_SIGNAL_RE)
    if timeout_excerpts:
        evidence = str(log_dir.relative_to(task_dir)) if log_dir.exists() else "task evidence"
        hang_excerpt: str | None = None
        for excerpt in timeout_excerpts:
            scope = _evidence_scope_for_signal(excerpt)
            if bool(PRODUCT_TIMEOUT_RE.search(excerpt)) and scope == "runtime_log":
                hang_excerpt = excerpt
                break
        if hang_excerpt is not None:
            checks.append({
                "id": "product-timeout-or-hang",
                "category": "stability",
                "result": "fail",
                "failureClass": "product_timeout_or_hang",
                "reason": "Product-visible timeout/hang evidence found; record page/state and route to repair if reproducible.",
                "evidence": evidence,
                "diagnostic": {"scope": "runtime_log", "excerpt": hang_excerpt},
            })
        else:
            excerpt = timeout_excerpts[0]
            scope = _evidence_scope_for_signal(excerpt)
            checks.append({
                "id": "timeout-signal-nonblocking",
                "category": "stability",
                "result": "warn",
                "failureClass": "automation_or_system_timeout_signal",
                "reason": "Timeout-like keyword found in verifier/system/network/control evidence; treat as automation/runtime-path signal, not product quality failure unless reproduced with page/state evidence.",
                "evidence": evidence,
                "diagnostic": {"scope": scope, "excerpt": excerpt},
            })
    return checks


def parse_quality_testcases(task_dir: pathlib.Path) -> list[dict]:
    path = task_dir / "TestCases.md"
    if not path.exists():
        return []
    cases: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line.startswith("|") or "Quality" not in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 5 or cells[0].lower().startswith("\u7528\u4f8b") or set(cells[0]) <= {"-"}:
            continue
        kind = cells[2]
        lowered = kind.lower()
        if "performance" in lowered:
            category = "performance"
        elif "ux" in lowered or "smooth" in lowered:
            category = "ux"
        elif "stability" in lowered:
            category = "stability"
        elif "architecture" in lowered:
            category = "architecture"
        elif "maintain" in lowered:
            category = "maintainability"
        else:
            category = "other"
        cases.append({
            "id": cells[0],
            "requirement": cells[1],
            "kind": kind,
            "category": category,
            "method": cells[3],
            "expected": cells[4],
        })
    return cases

def extract_durations(text: str) -> list[int]:
    return [int(m.group(1)) for m in DURATION_RE.finditer(text)]


def result_rank(value: str) -> int:
    return {"pass": 0, "warn": 1, "fail": 2, "blocked": 3}.get(value, 1)


def worst_result(checks: list[dict]) -> str:
    worst = "pass"
    for check in checks:
        if result_rank(check.get("result", "warn")) > result_rank(worst):
            worst = check.get("result", "warn")
    return worst


def quality_checks(root: pathlib.Path, task_dir: pathlib.Path, log_dir: pathlib.Path) -> tuple[list[dict], dict]:
    text = collect_text_evidence(task_dir, log_dir)
    checks: list[dict] = []
    quality_cases = parse_quality_testcases(task_dir)
    for case in quality_cases:
        checks.append({
            "id": f"{case['id']}-intent-present",
            "testCaseId": case["id"],
            "category": case.get("category", "other"),
            "qualityCategory": case.get("category", "other"),
            "result": "pass",
            "reason": f"Quality testcase declared: {case['kind']} / {case['method']}",
        })

    durations = extract_durations(text)
    if durations:
        max_duration = max(durations)
        checks.append({
            "id": "duration-ms-present",
            "category": "performance",
            "result": "warn" if max_duration > 5000 else "pass",
            "failureClass": "performance_baseline_missing" if max_duration > 5000 else "none",
            "actual": max_duration,
            "unit": "ms",
            "reason": "Captured duration_ms-like evidence; warn only when over a lenient 5s heuristic without baseline.",
            "evidence": str(log_dir.relative_to(task_dir)) if log_dir.exists() else "task evidence",
        })
    else:
        checks.append({
            "id": "duration-ms-missing",
            "category": "performance",
            "result": "warn",
            "failureClass": "performance_metric_missing",
            "reason": "No duration_ms-like metric found. V1 warns instead of failing because many tasks lack performance instrumentation.",
        })

    checks.extend(classify_crash_timeout_quality(text, log_dir, task_dir))

    for check_id, pattern in HARD_FAIL_PATTERNS:
        if pattern.search(text):
            checks.append({
                "id": check_id,
                "category": "stability" if check_id != "stuck_loading" else "ux",
                "result": "fail",
                "failureClass": "product_stuck_loading",
                "reason": f"Hard quality failure pattern detected: {check_id}",
                "evidence": str(log_dir.relative_to(task_dir)) if log_dir.exists() else "task evidence",
            })

    for check_id, pattern in WARN_PATTERNS:
        if pattern.search(text):
            checks.append({
                "id": check_id,
                "category": "ux",
                "result": "warn",
                "reason": f"Soft quality signal detected: {check_id}",
                "evidence": str(log_dir.relative_to(task_dir)) if log_dir.exists() else "task evidence",
            })

    diff_names = run_git(root, ["diff", "--name-only"])
    diff_stat = run_git(root, ["diff", "--stat"])
    changed_files = [line.strip() for line in diff_names.splitlines() if line.strip()]
    if changed_files:
        checks.append({
            "id": "changed-files-scope",
            "category": "architecture",
            "result": "warn" if len(changed_files) > 20 else "pass",
            "actual": len(changed_files),
            "reason": "Changed file count is a lightweight architecture scope signal; V1 only warns on broad diffs.",
            "evidence": "git diff --name-only",
        })
        diff_text = run_git(root, ["diff", "--", *changed_files[:80]])
        for check_id, pattern in ARCH_WARN_PATTERNS:
            if pattern.search(diff_text):
                checks.append({
                    "id": check_id,
                    "category": "architecture",
                    "result": "warn",
                    "reason": f"Heuristic architecture/maintainability signal detected: {check_id}",
                    "evidence": "git diff",
                })
    else:
        checks.append({
            "id": "changed-files-scope",
            "category": "architecture",
            "result": "pass",
            "actual": 0,
            "reason": "No git diff detected in AutoMind repo; architecture scope check has no changed files to review.",
        })

    category_batches: dict[str, list[str]] = {}
    for case in quality_cases:
        category_batches.setdefault(case.get("category", "other"), []).append(case.get("id", ""))

    meta = {
        "durations": durations[:50],
        "changedFiles": changed_files[:200],
        "diffStat": diff_stat[:4000],
        "qualityTestCases": quality_cases,
        "qualityCategoryBatches": category_batches,
    }
    return checks, meta


def _test_results_support_finish(evaluation: dict) -> bool:
    """Return True only when required testResults unambiguously support finish.

    Stronger than a pure "all required rows pass" check: it requires at least one
    required row, rejects any required row that is not normalized-pass, and
    explicitly rejects any required row carrying a fail/blocked/partial result.
    This guards the stale-quality auto-clear from flipping a task whose real
    failure lives in a required testResult rather than in failedChecks.
    """
    rows = evaluation.get("testResults") if isinstance(evaluation.get("testResults"), list) else []
    required_rows = [row for row in rows if isinstance(row, dict) and row.get("required") is True]
    if not required_rows:
        return False
    for row in required_rows:
        normalized = _normalize_result_value(row.get("result"))
        if normalized in {"fail", "blocked", "partial"}:
            return False
        if normalized != "pass":
            return False
    return True


def merge_evaluation(task_dir: pathlib.Path, summary_path: pathlib.Path, quality_summary: dict) -> dict:
    evaluation_path = task_dir / "evaluation.json"
    evaluation = load_json(evaluation_path) or {
        "iteration": quality_summary.get("iteration", 1),
        "result": "pass",
        "summary": "Functional evaluation missing; quality-check produced evidence only.",
        "failedChecks": [],
        "nextAction": "finish",
    }

    existing = evaluation.get("qualityChecks")
    if not isinstance(existing, list):
        existing = []
    # Replace previous checks from this evaluator to keep latest run tidy.
    existing = [c for c in existing if c.get("source") != "quality_evaluator.py"]
    for check in quality_summary.get("qualityChecks", []):
        c = dict(check)
        c["source"] = "quality_evaluator.py"
        existing.append(c)
    evaluation["qualityChecks"] = existing

    evidence = evaluation.get("evidence") if isinstance(evaluation.get("evidence"), list) else []
    rel = str(summary_path.relative_to(task_dir))
    if not any(e.get("path") == rel for e in evidence if isinstance(e, dict)):
        evidence.append({"type": "other", "path": rel, "note": "quality-check summary"})
    evaluation["evidence"] = evidence

    failed = evaluation.get("failedChecks") if isinstance(evaluation.get("failedChecks"), list) else []
    # Quality checks are recomputed each run; remove stale quality-check blockers
    # before applying the latest summary. This prevents a previous heuristic fail
    # from permanently pinning evaluation.json to fail after evidence improves.
    failed_without_quality = [c for c in failed if not (isinstance(c, dict) and c.get("name") == "quality-check")]

    quality_result = quality_summary.get("result", "pass")
    current_result = str(evaluation.get("result", "pass"))
    current_next = str(evaluation.get("nextAction", "finish"))
    if quality_result == "fail":
        if current_result == "pass" or not failed_without_quality:
            evaluation["result"] = "fail"
            evaluation["nextAction"] = "retry_generator" if current_next == "finish" else current_next
        failed_without_quality.append({
            "name": "quality-check",
            "category": "validation_failure",
            "reason": "Quality check found a hard failure. See qualityChecks and quality-summary.json.",
            "evidence": rel,
        })
        evaluation["failedChecks"] = failed_without_quality
    else:
        evaluation["failedChecks"] = failed_without_quality
        warnings = evaluation.get("warnings") if isinstance(evaluation.get("warnings"), list) else []
        if quality_result == "warn":
            msg = "Quality check produced warnings; final result remains controlled by functional required TC evidence under lenient V1 rules."
            if msg not in warnings:
                warnings.append(msg)
        if current_result == "fail" and not failed_without_quality and _test_results_support_finish(evaluation):
            rows = evaluation.get("testResults") if isinstance(evaluation.get("testResults"), list) else []
            basis = [
                str(row.get("testCaseId") or row.get("id") or "")
                for row in rows
                if isinstance(row, dict) and row.get("required") is True
            ]
            evaluation["result"] = "pass"
            evaluation["nextAction"] = "finish"
            evaluation["staleQualityClear"] = {
                "from": "fail",
                "fromNextAction": current_next,
                "clearedAt": datetime.utcnow().isoformat() + "Z",
                "qualityResult": quality_result,
                "basisRequiredTestCases": [b for b in basis if b],
                "reason": "Only-remaining failure was a stale quality-check; required testResults still pass.",
            }
            msg = "Quality-check stale failure cleared after latest quality run; required testResults remain pass."
            if msg not in warnings:
                warnings.append(msg)
        evaluation["warnings"] = warnings

    base_summary = str(evaluation.get("summary", "")).strip()
    # Keep reruns tidy by replacing any previous quality-check suffix.
    base_summary = re.sub(r"\s*Quality check: (pass|warn|fail|blocked)\. ?", "", base_summary).strip()
    base_summary = re.sub(r"\s*Quality check: (pass|warn|fail|blocked)\.?", "", base_summary).strip()
    suffix = f" Quality check: {quality_result}."
    evaluation["summary"] = (base_summary + suffix).strip() if base_summary else suffix.strip()

    write_json(evaluation_path, evaluation)
    return evaluation

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_code")
    parser.add_argument("--root", default=".", help="AutoMind repo root. Default: current directory.")
    parser.add_argument("--iteration", type=int, help="Iteration number. Default: runtime-state iteration or 1.")
    parser.add_argument("--merge", action="store_true", help="Merge qualityChecks into task evaluation.json.")
    args = parser.parse_args()

    root = pathlib.Path(args.root).expanduser().resolve()
    task_dir = root / ".automind" / "tasks" / args.task_code
    if not task_dir.exists():
        raise SystemExit(f"task not found: {task_dir}")

    iteration = args.iteration or latest_iteration(task_dir)
    log_dir = iter_log_dir(task_dir, iteration)
    checks, meta = quality_checks(root, task_dir, log_dir)
    quality_result = worst_result(checks)
    if quality_result == "blocked":
        quality_result = "fail"

    summary = {
        "result": quality_result,
        "iteration": iteration,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "policy": "lenient-v1",
        "summary": "Lightweight quality-check after functional evaluation; warnings do not fail the loop unless hard failure patterns are detected.",
        "regressionPolicy": {
            "name": "safe-default-v1",
            "default": "After quality-driven runtime/product code changes, the next iteration reruns the selected/affected functional batch first, then reruns the relevant Quality category batch/check. This is not necessarily the entire product suite.",
            "safeSkipOnlyFor": ["docs", "comments", "diagnostic text", "test thresholds/config", "quality evaluator metadata"],
            "requiresSkipReasonInEvaluation": True,
            "futureWork": "dependency-aware selective rerun beyond category batching",
        },
        "qualityChecks": checks,
        "meta": meta,
    }
    summary_path = log_dir / "quality-summary.json"
    write_json(summary_path, summary)

    merged = None
    if args.merge:
        merged = merge_evaluation(task_dir, summary_path, summary)

    output = {
        "result": summary["result"],
        "summaryPath": str(summary_path),
        "checks": len(checks),
        "merged": bool(args.merge),
        "evaluationResult": merged.get("result") if isinstance(merged, dict) else None,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
