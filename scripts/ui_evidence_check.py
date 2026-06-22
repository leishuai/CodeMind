#!/usr/bin/env python3
"""Check UI automation evidence completeness for AutoMind tasks.

This is intentionally lightweight: it does not decide product correctness. It
checks whether a UI verification run left enough auditable evidence to support
or debug its result.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

from automind_paths import TASKS_DIR

PASS = "pass"
WARN = "warning"
FAIL = "fail"


def read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        except json.JSONDecodeError:
            rows.append({"_parseError": line[:200]})
    return rows


def rel(task_dir: pathlib.Path, path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(task_dir))
    except ValueError:
        return str(path)


def add(checks: list[dict[str, Any]], name: str, ok: bool, severity: str, detail: str, evidence: str | None = None) -> None:
    checks.append({
        "name": name,
        "ok": bool(ok),
        "severity": severity,
        "detail": detail,
        **({"evidence": evidence} if evidence else {}),
    })


def resolve_evidence_path(task_dir: pathlib.Path, raw: Any) -> pathlib.Path | None:
    if not raw or not isinstance(raw, str):
        return None
    p = pathlib.Path(raw)
    if p.is_absolute():
        return p
    return task_dir / p


def find_file(task_dir: pathlib.Path, iteration: int, candidates: list[str]) -> pathlib.Path | None:
    for item in candidates:
        p = task_dir / item.format(iteration=iteration)
        if p.exists():
            return p
    return None


def detect_platform(task_dir: pathlib.Path, iteration: int, evaluation: dict[str, Any]) -> str:
    evidence_blob = json.dumps(evaluation.get("evidence", []), ensure_ascii=False).lower()
    if "probe-flow/action-trace" in evidence_blob or (task_dir / "logs" / f"iter-{iteration}" / "probe-flow" / "action-trace.jsonl").exists():
        return "android"
    if "xcresult" in evidence_blob or (task_dir / "logs" / f"iter-{iteration}" / "TestResults.xcresult").exists():
        return "ios"
    if "web-probe-flow" in evidence_blob or (task_dir / "logs" / f"iter-{iteration}" / "web-probe-flow-summary.json").exists():
        return "web"
    if (task_dir / "logs" / f"iter-{iteration}" / "action-trace.jsonl").exists():
        return "client"
    return "unknown"


def check_action_trace(task_dir: pathlib.Path, iteration: int, checks: list[dict[str, Any]]) -> tuple[pathlib.Path | None, list[dict[str, Any]]]:
    candidates = [
        "logs/iter-{iteration}/probe-flow/action-trace.jsonl",
        "logs/iter-{iteration}/action-trace.jsonl",
    ]
    trace = find_file(task_dir, iteration, candidates)
    if not trace:
        add(checks, "action_trace_present", False, WARN, "No action-trace.jsonl found for this iteration")
        return None, []
    rows = read_jsonl(trace)
    add(checks, "action_trace_present", True, PASS, f"Found {len(rows)} action trace entries", rel(task_dir, trace))
    parse_errors = [row for row in rows if row.get("_parseError")]
    add(checks, "action_trace_parseable", not parse_errors, FAIL, "Action trace JSONL is parseable" if not parse_errors else f"{len(parse_errors)} JSONL parse errors", rel(task_dir, trace))
    critical = [row for row in rows if row.get("critical") is True]
    add(checks, "critical_actions_recorded", bool(critical), WARN, f"Critical actions recorded: {len(critical)}", rel(task_dir, trace))
    return trace, rows


def check_android(task_dir: pathlib.Path, iteration: int, rows: list[dict[str, Any]], checks: list[dict[str, Any]]) -> None:
    iter_dir = task_dir / "logs" / f"iter-{iteration}" / "probe-flow"
    summary = iter_dir / "probe-flow-summary.json"
    add(checks, "android_probe_summary_present", summary.exists(), FAIL, "Android probe-flow summary present", rel(task_dir, summary) if summary.exists() else None)
    for row in [r for r in rows if r.get("critical") is True]:
        idx = row.get("stepIndex", "?")
        after = row.get("evidenceAfter") if isinstance(row.get("evidenceAfter"), dict) else {}
        shot = resolve_evidence_path(task_dir, after.get("screenshot"))
        hierarchy = resolve_evidence_path(task_dir, after.get("hierarchy"))
        add(checks, f"android_step_{idx}_after_screenshot", bool(shot and shot.exists()), WARN, f"Critical Android step {idx} after screenshot exists", rel(task_dir, shot) if shot else None)
        add(checks, f"android_step_{idx}_after_hierarchy", bool(hierarchy and hierarchy.exists()), WARN, f"Critical Android step {idx} after hierarchy exists", rel(task_dir, hierarchy) if hierarchy else None)
    page_sig_files = sorted(iter_dir.glob("step-*-page-signature.json"))
    if page_sig_files:
        add(checks, "android_page_signature_evidence", True, PASS, f"Page signature evidence files: {len(page_sig_files)}", rel(task_dir, page_sig_files[0]))


def check_ios(task_dir: pathlib.Path, iteration: int, checks: list[dict[str, Any]]) -> None:
    iter_dir = task_dir / "logs" / f"iter-{iteration}"
    xclog = iter_dir / "xcodebuild-ui-test.log"
    xcresult = iter_dir / "TestResults.xcresult"
    test_summary = iter_dir / "test-summary.txt"
    add(checks, "ios_xcodebuild_log_present", xclog.exists(), FAIL, "iOS xcodebuild UI test log present", rel(task_dir, xclog) if xclog.exists() else None)
    add(checks, "ios_xcresult_present", xcresult.exists(), FAIL, "iOS .xcresult present", rel(task_dir, xcresult) if xcresult.exists() else None)
    add(checks, "ios_test_summary_present", test_summary.exists(), WARN, "iOS test summary present", rel(task_dir, test_summary) if test_summary.exists() else None)
    text = xclog.read_text(errors="ignore") if xclog.exists() else ""
    add(checks, "ios_xctest_ran", "Running tests" in text or "Test Suite" in text, FAIL, "XCUITest appears to have run", rel(task_dir, xclog) if xclog.exists() else None)
    generated = sorted(iter_dir.glob("Generated*Tests.swift"))
    if generated:
        body = "\n".join(p.read_text(errors="ignore") for p in generated)
        add(checks, "ios_generated_screenshot_attachments", "XCTAttachment(screenshot:" in body and ".keepAlways" in body, WARN, "Generated Swift includes screenshot attachments for key actions", rel(task_dir, generated[0]))


def check_web(task_dir: pathlib.Path, iteration: int, rows: list[dict[str, Any]], checks: list[dict[str, Any]]) -> None:
    iter_dir = task_dir / "logs" / f"iter-{iteration}"
    summary = iter_dir / "web-probe-flow-summary.json"
    log = iter_dir / "web-probe-flow.log"
    add(checks, "web_probe_summary_present", summary.exists(), FAIL, "Web probe-flow summary present", rel(task_dir, summary) if summary.exists() else None)
    if summary.exists():
        data = read_json(summary)
        if data.get("dryRun") is True:
            add(checks, "web_project_e2e_log_present", True, WARN, "Dry-run mode: project E2E log not expected")
        else:
            add(checks, "web_project_e2e_log_present", log.exists(), WARN, "Web project E2E log present", rel(task_dir, log) if log.exists() else None)
    for row in [r for r in rows if r.get("critical") is True]:
        idx = row.get("stepIndex", "?")
        after = row.get("evidenceAfter") if isinstance(row.get("evidenceAfter"), dict) else {}
        has_visual_ref = bool(after.get("screenshot") or after.get("dom") or after.get("trace"))
        add(checks, f"web_step_{idx}_after_reference", has_visual_ref, WARN, f"Critical Web step {idx} has screenshot/DOM/trace reference")


def append_validation(task_dir: pathlib.Path, iteration: int, report: dict[str, Any]) -> None:
    path = task_dir / "Validation.md"
    text = path.read_text(errors="ignore") if path.exists() else "# Validation\n"
    marker = f"## Iteration {iteration} - UI Evidence Check"
    if marker in text:
        return
    failed = [c for c in report["checks"] if not c.get("ok") and c.get("severity") == FAIL]
    warnings = [c for c in report["checks"] if not c.get("ok") and c.get("severity") == WARN]
    lines = [
        "",
        marker,
        "",
        f"- Result: {report['result'].upper()}",
        f"- Platform: `{report['platform']}`",
        f"- Failed checks: {len(failed)}",
        f"- Warnings: {len(warnings)}",
        f"- Evidence summary: `logs/iter-{iteration}/ui-evidence-check.json`",
    ]
    if failed:
        lines.append("- Failures:")
        lines.extend(f"  - `{item['name']}`: {item['detail']}" for item in failed[:8])
    if warnings:
        lines.append("- Warnings:")
        lines.extend(f"  - `{item['name']}`: {item['detail']}" for item in warnings[:8])
    path.write_text(text.rstrip() + "\n" + "\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check UI automation evidence completeness")
    parser.add_argument("task_code")
    parser.add_argument("iteration", nargs="?", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    task_dir = TASKS_DIR / args.task_code
    if not task_dir.exists():
        print(json.dumps({"result": "fail", "summary": f"Task not found: {args.task_code}"}, ensure_ascii=False, indent=2))
        return 2
    evaluation = read_json(task_dir / "evaluation.json")
    iteration = args.iteration or int(evaluation.get("iteration") or 1)
    iter_dir = task_dir / "logs" / f"iter-{iteration}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []

    add(checks, "evaluation_json_present", (task_dir / "evaluation.json").exists(), FAIL, "evaluation.json present", "evaluation.json" if (task_dir / "evaluation.json").exists() else None)
    add(checks, "validation_md_present", (task_dir / "Validation.md").exists(), WARN, "Validation.md present", "Validation.md" if (task_dir / "Validation.md").exists() else None)

    platform = detect_platform(task_dir, iteration, evaluation)
    _trace, rows = check_action_trace(task_dir, iteration, checks)
    if platform == "android":
        check_android(task_dir, iteration, rows, checks)
    elif platform == "ios":
        check_ios(task_dir, iteration, checks)
    elif platform == "web":
        check_web(task_dir, iteration, rows, checks)

    failed = [c for c in checks if not c.get("ok") and c.get("severity") == FAIL]
    warnings = [c for c in checks if not c.get("ok") and c.get("severity") == WARN]
    result = "pass" if not failed else "fail"
    report = {
        "taskCode": args.task_code,
        "iteration": iteration,
        "platform": platform,
        "result": result,
        "summary": f"UI evidence check {result}: {len(failed)} failed checks, {len(warnings)} warnings",
        "failedCount": len(failed),
        "warningCount": len(warnings),
        "checks": checks,
    }
    write_path = iter_dir / "ui-evidence-check.json"
    write_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    append_validation(task_dir, iteration, report)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else report["summary"])
    return 0 if result == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
