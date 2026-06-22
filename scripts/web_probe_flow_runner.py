#!/usr/bin/env python3
"""Web probe-flow runner for AutoMind Client UI evidence.

This is intentionally lightweight. It does not install browsers or introduce a
new browser automation framework. It validates reviewable web UI action intent,
records the shared Client UI action evidence contract, and optionally delegates
real execution to a project-native E2E command such as Playwright/Cypress.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any

from automind_paths import RUNTIME_ROOT, TASKS_DIR, WORKSPACE_ROOT

sys.path.insert(0, str(RUNTIME_ROOT))
from orchestrator.evidence_contract import attach_required_test_results  # noqa: E402
from ui_overlay_policy import policy_from_flow, summarize_policy  # noqa: E402

try:
    from probe_flow_screenshots import ensure_per_tc_default_screenshots
except ModuleNotFoundError:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from probe_flow_screenshots import ensure_per_tc_default_screenshots  # type: ignore[no-redef]

SUPPORTED_ACTIONS = {"open", "navigate", "click", "tap", "input", "assert_text", "assert_selector", "assert_page", "screenshot", "wait"}
INTERACTION_ACTIONS = {"open", "navigate", "click", "tap", "input"}
SELECTOR_ACTIONS = {"click", "tap", "input", "assert_selector"}


def write_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def add_evidence_index_entry(container: dict[str, Any], path: str | None, *, ev_type: str = "other", signal: Any = None) -> None:
    if not path:
        return
    entry: dict[str, Any] = {"path": str(path), "type": ev_type}
    if signal:
        entry["signal"] = signal
    entries = container.setdefault("evidenceIndex", [])
    if entry not in entries:
        entries.append(entry)


def add_web_probe_flow_evidence_index(container: dict[str, Any], iteration: int, *, include_attempts: list[dict[str, Any]] | None = None) -> None:
    add_evidence_index_entry(container, f"logs/iter-{iteration}/web-probe-flow-summary.json", ev_type="summary", signal="web_probe_flow_summary")
    add_evidence_index_entry(container, f"logs/iter-{iteration}/web-action-intent-summary.json", ev_type="summary", signal="web_action_intent_summary")
    add_evidence_index_entry(container, f"logs/iter-{iteration}/action-trace.jsonl", ev_type="trace", signal="ui_action_trace_recorded")
    if include_attempts is not None:
        add_evidence_index_entry(container, f"logs/iter-{iteration}/web-probe-flow.log", ev_type="log", signal="web_probe_flow_log")
        for item in include_attempts:
            add_evidence_index_entry(container, item.get("log"), ev_type="log", signal=f"web_probe_flow_attempt_{item.get('attempt')}")


def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def append_jsonl(path: pathlib.Path, item: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def runtime_timeout(default: int = 43200) -> int:
    raw = os.environ.get("AUTOMIND_WEB_PROBE_TIMEOUT") or os.environ.get("AUTOMIND_CMD_TIMEOUT") or str(default)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def load_flow(task_dir: pathlib.Path, flow_arg: str) -> tuple[pathlib.Path, dict[str, Any]]:
    flow_path = pathlib.Path(flow_arg)
    if not flow_path.is_absolute():
        candidates = [task_dir / flow_path, WORKSPACE_ROOT / flow_path]
        for candidate in candidates:
            if candidate.exists():
                flow_path = candidate
                break
    if not flow_path.exists():
        raise FileNotFoundError(f"Flow file not found: {flow_path}")
    return flow_path, read_json(flow_path)


def normalize_intent(flow: dict[str, Any]) -> dict[str, Any]:
    intent = flow.get("testIntent") or flow.get("intent") or {}
    if not isinstance(intent, dict):
        intent = {"goal": str(intent)}
    action_plan = flow.get("actionPlan") or {}
    if not isinstance(action_plan, dict):
        action_plan = {}
    steps = intent.get("steps") or action_plan.get("steps") or flow.get("steps") or []
    post_checks = intent.get("postChecks") or action_plan.get("postChecks") or flow.get("postChecks") or []
    acceptance = intent.get("acceptanceCriteria") or flow.get("acceptanceCriteria") or []
    sources = intent.get("sources") or intent.get("source") or flow.get("intentSources") or []
    if isinstance(steps, list):
        # Default to one screenshot per runtime/UI TC so report.html can show
        # per-TC evidence. Actual capture is the project-native E2E command's
        # job; this only records the intent. Best-effort, no missing warning.
        ensure_per_tc_default_screenshots(steps)
    return {
        "goal": intent.get("goal") or flow.get("name") or "",
        "sources": [str(v) for v in as_list(sources) if str(v).strip()],
        "acceptanceCriteria": [str(v) for v in as_list(acceptance) if str(v).strip()],
        "preconditions": [str(v) for v in as_list(intent.get("preconditions") or flow.get("preconditions")) if str(v).strip()],
        "steps": steps if isinstance(steps, list) else [],
        "postChecks": post_checks if isinstance(post_checks, list) else [],
    }


def planned_web_ui_unblock(flow: dict[str, Any]) -> dict[str, Any]:
    policy = policy_from_flow(flow)
    if not policy.get("enabled"):
        return {
            **summarize_policy(policy),
            "result": "disabled",
            "mode": "project_e2e_contract",
            "reason": "uiUnblock is not enabled in probe-flow.web.json",
        }
    return {
        **summarize_policy(policy),
        "mode": "project_e2e_contract",
        "result": "planned",
        "reason": "Web probe-flow delegates execution to the project-native E2E command; close/dismiss safe overlays before target actions and record project trace/screenshots/DOM evidence.",
        "safeExamples": ["Close", "Got it", "OK", "Skip", "Later", "Not now", "Dismiss", "Cancel", "×"],
        "sensitiveExamples": ["Login", "Sign in", "Allow", "Agree", "Pay", "Delete", "Reset"],
    }


def command_from_flow(flow: dict[str, Any]) -> str | None:
    for key in ["command", "webCommand", "e2eCommand", "testCommand"]:
        value = flow.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    app = flow.get("app") or flow.get("webApp") or {}
    if isinstance(app, dict):
        for key in ["command", "webCommand", "e2eCommand", "testCommand"]:
            value = app.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def validate_intent(intent: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    steps = intent.get("steps") or []
    if steps and not str(intent.get("goal") or "").strip():
        errors.append("testIntent.goal is required when action steps are present")
    if steps and not intent.get("sources"):
        warnings.append("testIntent.sources/source is recommended so web UI action intent is traceable to TC/AC")
    if steps and not intent.get("acceptanceCriteria") and not intent.get("postChecks"):
        warnings.append("action steps have no acceptanceCriteria/postChecks; post-action validation may drift")
    for idx, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            errors.append(f"step {idx} must be object")
            continue
        typ = step.get("type")
        if typ not in SUPPORTED_ACTIONS:
            errors.append(f"step {idx} unsupported web action type: {typ}")
        if typ in SELECTOR_ACTIONS and not isinstance(step.get("selector"), dict):
            errors.append(f"step {idx} action {typ} requires selector object")
        if typ == "input" and "text" not in step:
            errors.append(f"step {idx} input action requires text")
        if typ in {"open", "navigate"} and not (step.get("url") or step.get("path")):
            warnings.append(f"step {idx} {typ} should include url or path")
    return errors, warnings


def is_critical_action(step_type: str | None, step: dict[str, Any]) -> bool:
    if step.get("critical") is True:
        return True
    if step.get("screenshotAfter") is True or step.get("evidence", {}).get("screenshotAfter") is True:
        return True
    return step_type in INTERACTION_ACTIONS


def selector_target(selector: Any, step: dict[str, Any]) -> str:
    if step.get("url"):
        return f"url={step.get('url')}"
    if step.get("path"):
        return f"path={step.get('path')}"
    if not isinstance(selector, dict):
        return "-"
    for key in ["testId", "test_id", "data-testid", "role", "name", "text", "css", "selector", "aria", "xpath"]:
        if selector.get(key):
            return f"{key}={selector.get(key)}"
    return "-"


def build_action_trace(intent: dict[str, Any], trace_path: pathlib.Path, phase: str, ui_unblock: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    trace_path.write_text("")
    actions: list[dict[str, Any]] = []
    if ui_unblock and ui_unblock.get("result") != "disabled":
        entry = {
            "stepIndex": "overlay-0",
            "name": "Auto-unblock safe web overlays",
            "type": "overlay_unblock",
            "intent": "Close safe non-destructive overlays before target web UI actions",
            "selector": None,
            "target": "safe dismiss controls only",
            "critical": False,
            "platform": "web",
            "backend": "project-e2e",
            "evidencePhase": phase,
            "ok": None,
            "uiUnblock": ui_unblock,
            "detail": "planned overlay-unblock contract; actual close/dismiss actions must be executed by the project-native E2E command and evidenced by its trace/screenshots/DOM",
            "evidenceAfter": {
                "screenshot": "see project E2E report/trace if available",
                "dom": "see project E2E report/DOM snapshot if available",
                "trace": "project E2E report/trace if configured",
            },
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        actions.append(entry)
        append_jsonl(trace_path, entry)
    for index, step in enumerate(intent.get("steps") or [], start=1):
        if not isinstance(step, dict):
            continue
        step_type = step.get("type")
        if not is_critical_action(step_type, step):
            continue
        entry = {
            "stepIndex": index,
            "name": step.get("name") or step.get("description") or f"{index}:{step_type}",
            "type": step_type,
            "intent": step.get("intent") or step.get("goal") or step.get("name") or step.get("description"),
            "selector": step.get("selector"),
            "target": selector_target(step.get("selector"), step),
            "critical": True,
            "platform": "web",
            "backend": "project-e2e",
            "evidencePhase": phase,
            "ok": None,
            "detail": "planned web action; actual execution evidence is project E2E log/trace/screenshot when available",
            "evidenceAfter": {
                "screenshot": step.get("screenshotAfter") or step.get("evidence", {}).get("screenshotAfter"),
                "dom": step.get("domAfter") or step.get("evidence", {}).get("domAfter"),
                "trace": step.get("trace") or step.get("evidence", {}).get("trace"),
            },
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        actions.append(entry)
        append_jsonl(trace_path, entry)
    return actions


def format_action_evidence(actions: list[dict[str, Any]], limit: int = 8) -> list[str]:
    lines: list[str] = []
    for action in actions[:limit]:
        idx = action.get("stepIndex", "?")
        typ = action.get("type") or "action"
        name = action.get("name") or action.get("intent") or "UI action"
        result_raw = action.get("ok")
        result = "PASS" if result_raw is True else "FAIL" if result_raw is False else "PLANNED"
        target = action.get("target") or selector_target(action.get("selector"), action)
        detail = str(action.get("detail") or "").strip()
        detail_text = f"; detail: {detail[:180]}" if detail else ""
        after = action.get("evidenceAfter") if isinstance(action.get("evidenceAfter"), dict) else {}
        screenshot = after.get("screenshot") or "see project E2E report/trace if available"
        dom = after.get("dom") or "see project E2E report/DOM snapshot if available"
        trace = after.get("trace") or "see project E2E trace if available"
        lines.append(f"- Step {idx} `{typ}` {result}: {name}; target: `{target}`{detail_text}")
        lines.append(f"  - after screenshot: `{screenshot}`")
        lines.append(f"  - after DOM: `{dom}`")
        lines.append(f"  - trace: `{trace}`")
    if len(actions) > limit:
        lines.append(f"- ... {len(actions) - limit} more critical actions in `action-trace.jsonl`.")
    return lines


def append_validation(task_dir: pathlib.Path, iteration: int, evaluation: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    validation = task_dir / "Validation.md"
    existing = validation.read_text(errors="ignore") if validation.exists() else "# Validation\n"
    marker = f"## Iteration {iteration} - Web Probe Flow"
    if marker in existing:
        return
    result = str(evaluation.get("result", "unknown")).upper()
    failed = evaluation.get("failedChecks")
    category = "none"
    if isinstance(failed, list) and failed and isinstance(failed[0], dict):
        category = str(failed[0].get("category") or "unknown")
    action_block = "\n".join(format_action_evidence(actions)) if actions else "- No critical UI action trace was produced."
    block = f"""

{marker}

### Result

{result}. {evaluation.get('summary', '')}

- failure category: `{category}`
- nextAction: `{evaluation.get('nextAction', '-')}`

### Evidence

- `logs/iter-{iteration}/web-probe-flow-summary.json`
- `logs/iter-{iteration}/web-action-intent-summary.json`
- `logs/iter-{iteration}/action-trace.jsonl`
- `logs/iter-{iteration}/web-probe-flow.log` when project E2E command ran
- `evaluation.json`

### Client UI action evidence

{action_block}
"""
    validation.write_text(existing.rstrip() + "\n" + block.lstrip())



def run_ui_evidence_gate(task_dir: pathlib.Path, iteration: int, evaluation: dict[str, Any]) -> dict[str, Any]:
    """Run evidence completeness check and gate pass results on hard evidence failures."""
    script = pathlib.Path(__file__).resolve().parent / "ui_evidence_check.py"
    proc = subprocess.run(
        [sys.executable, str(script), task_dir.name, str(iteration), "--json"],
        cwd=str(WORKSPACE_ROOT),
        text=True,
        capture_output=True,
        timeout=300,
    )
    report_path = f"logs/iter-{iteration}/ui-evidence-check.json"
    evidence = evaluation.setdefault("evidence", [])
    if not any(isinstance(item, dict) and item.get("path") == report_path for item in evidence):
        evidence.append({"type": "other", "note": "ui-evidence-check", "path": report_path})
    report = read_json(task_dir / report_path) if (task_dir / report_path).exists() else {}
    if proc.returncode != 0 and report.get("result") != "fail":
        report = {"result": "fail", "summary": (proc.stdout + proc.stderr)[-1000:] or "ui-evidence-check failed to run"}
    if report.get("result") == "fail":
        evaluation.setdefault("failedChecks", []).append({
            "name": "ui_evidence_check",
            "category": "evidence_incomplete",
            "reason": report.get("summary") or "UI evidence completeness check failed",
            "evidence": report_path,
        })
        if evaluation.get("result") == "pass" or evaluation.get("nextAction") == "finish":
            evaluation["result"] = "blocked"
            evaluation["nextAction"] = "retry_generator"
            evaluation["summary"] = "UI evidence check blocked completion: " + str(report.get("summary") or "evidence incomplete")
    return evaluation

def main() -> int:
    parser = argparse.ArgumentParser(description="Run Web probe-flow")
    parser.add_argument("task_code")
    parser.add_argument("iteration", nargs="?", type=int, default=1)
    parser.add_argument("--flow", default="probe-flow.web.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retries", type=int, default=0, help="Retry failed project-native E2E command up to N times")
    args = parser.parse_args()

    task_dir = TASKS_DIR / args.task_code
    iter_dir = task_dir / "logs" / f"iter-{args.iteration}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)

    try:
        flow_path, flow = load_flow(task_dir, args.flow)
    except FileNotFoundError as exc:
        evaluation = {
            "iteration": args.iteration,
            "result": "blocked",
            "summary": "Missing Web probe-flow file",
            "failedChecks": [{"name": "web_probe_flow", "category": "needs_replan", "reason": str(exc)}],
            "evidence": [],
            "nextAction": "replan",
        }
        write_json(task_dir / "evaluation.json", evaluation)
        print(json.dumps(evaluation, ensure_ascii=False, indent=2))
        return 2

    if flow.get("platform") not in {"web", "browser", None}:
        evaluation = {
            "iteration": args.iteration,
            "result": "blocked",
            "summary": f"Unsupported Web probe-flow platform: {flow.get('platform')}",
            "failedChecks": [{"name": "web_probe_flow_platform", "category": "needs_replan", "reason": "Use platform=web"}],
            "evidence": [{"type": "other", "note": "web-probe-flow", "path": str(flow_path)}],
            "nextAction": "replan",
        }
        write_json(task_dir / "evaluation.json", evaluation)
        print(json.dumps(evaluation, ensure_ascii=False, indent=2))
        return 2

    intent = normalize_intent(flow)
    errors, warnings = validate_intent(intent)
    command = command_from_flow(flow)
    ui_unblock = planned_web_ui_unblock(flow)
    actions = build_action_trace(intent, iter_dir / "action-trace.jsonl", "dry-run" if args.dry_run else "project-e2e", ui_unblock)
    write_json(iter_dir / "web-action-intent-summary.json", {
        "flow": str(flow_path),
        "intent": intent,
        "uiUnblock": ui_unblock,
        "command": command,
        "errors": errors,
        "warnings": warnings,
    })

    if errors:
        evaluation = {
            "iteration": args.iteration,
            "result": "blocked",
            "summary": "Web probe-flow action intent validation failed",
            "failedChecks": [{"name": "web_probe_flow_intent", "category": "needs_replan", "reason": "; ".join(errors)}],
            "evidence": [
                {"type": "other", "note": "web-action-intent-summary", "path": f"logs/iter-{args.iteration}/web-action-intent-summary.json"},
                {"type": "other", "note": "ui-action-trace", "path": f"logs/iter-{args.iteration}/action-trace.jsonl"},
            ],
            "nextAction": "replan",
        }
        write_json(task_dir / "evaluation.json", evaluation)
        append_validation(task_dir, args.iteration, evaluation, actions)
        evaluation = run_ui_evidence_gate(task_dir, args.iteration, evaluation)
        write_json(task_dir / "evaluation.json", evaluation)
        print(json.dumps(evaluation, ensure_ascii=False, indent=2))
        return 2

    summary = {
        "result": "pass" if args.dry_run else "pending",
        "flow": str(flow_path),
        "intentGoal": intent.get("goal"),
        "actionSteps": len(intent.get("steps") or []),
        "criticalActions": len(actions),
        "actionTrace": f"logs/iter-{args.iteration}/action-trace.jsonl",
        "command": command,
        "warnings": warnings,
        "uiUnblock": ui_unblock,
        "dryRun": args.dry_run,
    }
    add_web_probe_flow_evidence_index(summary, args.iteration)

    if args.dry_run:
        write_json(iter_dir / "web-probe-flow-summary.json", summary)
        evaluation = {
            "iteration": args.iteration,
            "result": "pass",
            "summary": "Web probe-flow dry-run passed with Client UI action evidence",
            "failedChecks": [],
            "evidence": [
                {"type": "other", "note": "web-probe-flow-summary", "path": f"logs/iter-{args.iteration}/web-probe-flow-summary.json"},
                {"type": "other", "note": "web-action-intent-summary", "path": f"logs/iter-{args.iteration}/web-action-intent-summary.json"},
                {"type": "other", "note": "ui-action-trace", "path": f"logs/iter-{args.iteration}/action-trace.jsonl"},
            ],
            "nextAction": "finish",
        }
        add_web_probe_flow_evidence_index(evaluation, args.iteration)
        evaluation = attach_required_test_results(
            task_dir,
            evaluation,
            "pass",
            [f"logs/iter-{args.iteration}/web-probe-flow-summary.json", f"logs/iter-{args.iteration}/action-trace.jsonl"],
            "Web probe-flow dry-run validated Client UI action evidence.",
            source="web-probe-flow",
            observed_signals=["web action intent valid", "ui action trace recorded"],
            metric_name="web_probe_flow_dry_run",
        )
        write_json(task_dir / "evaluation.json", evaluation)
        append_validation(task_dir, args.iteration, evaluation, actions)
        evaluation = run_ui_evidence_gate(task_dir, args.iteration, evaluation)
        write_json(task_dir / "evaluation.json", evaluation)
        print(json.dumps(evaluation, ensure_ascii=False, indent=2))
        return 0 if evaluation.get("result") == "pass" else 1

    if not command:
        summary["result"] = "blocked"
        write_json(iter_dir / "web-probe-flow-summary.json", summary)
        evaluation = {
            "iteration": args.iteration,
            "result": "blocked",
            "summary": "Web probe-flow needs a project-native E2E command or --dry-run",
            "failedChecks": [{"name": "web_probe_flow_command", "category": "needs_replan", "reason": "Set command/webCommand/e2eCommand/testCommand in probe-flow.web.json"}],
            "evidence": [
                {"type": "other", "note": "web-probe-flow-summary", "path": f"logs/iter-{args.iteration}/web-probe-flow-summary.json"},
                {"type": "other", "note": "ui-action-trace", "path": f"logs/iter-{args.iteration}/action-trace.jsonl"},
            ],
            "nextAction": "replan",
        }
        add_web_probe_flow_evidence_index(evaluation, args.iteration)
        write_json(task_dir / "evaluation.json", evaluation)
        append_validation(task_dir, args.iteration, evaluation, actions)
        evaluation = run_ui_evidence_gate(task_dir, args.iteration, evaluation)
        write_json(task_dir / "evaluation.json", evaluation)
        print(json.dumps(evaluation, ensure_ascii=False, indent=2))
        return 2

    attempts: list[dict[str, Any]] = []
    max_attempts = max(1, int(args.retries) + 1)
    output = ""
    final_returncode = 1
    for attempt in range(1, max_attempts + 1):
        proc = subprocess.run(["bash", "-lc", command], cwd=str(WORKSPACE_ROOT), text=True, capture_output=True, timeout=runtime_timeout())
        attempt_output = proc.stdout + proc.stderr
        attempt_log = iter_dir / f"web-probe-flow-attempt-{attempt}.log"
        attempt_log.write_text(attempt_output)
        attempts.append({
            "attempt": attempt,
            "exitCode": proc.returncode,
            "log": f"logs/iter-{args.iteration}/web-probe-flow-attempt-{attempt}.log",
            "retried": proc.returncode != 0 and attempt < max_attempts,
        })
        output += f"\n--- attempt {attempt}/{max_attempts} exit={proc.returncode} ---\n" + attempt_output
        final_returncode = proc.returncode
        if proc.returncode == 0:
            break
    (iter_dir / "web-probe-flow.log").write_text(output)
    (iter_dir / "evaluator.log").write_text(output)
    final_ok = final_returncode == 0
    for action in actions:
        action["ok"] = final_ok if action.get("type") != "overlay_unblock" else None
        if isinstance(action.get("evidenceAfter"), dict) and not action["evidenceAfter"].get("trace"):
            action["evidenceAfter"]["trace"] = "project E2E report/trace if configured"
    trace_path = iter_dir / "action-trace.jsonl"
    trace_path.write_text("")
    for action in actions:
        append_jsonl(trace_path, action)
    summary["result"] = "pass" if final_ok else "fail"
    summary["exitCode"] = final_returncode
    summary["attempts"] = attempts
    summary["retries"] = max_attempts - 1
    add_web_probe_flow_evidence_index(summary, args.iteration, include_attempts=attempts)
    write_json(iter_dir / "web-probe-flow-summary.json", summary)

    evaluation = {
        "iteration": args.iteration,
        "result": "pass" if final_ok else "fail",
        "summary": "Web project E2E command passed" if final_ok else f"Web project E2E command failed with exit code {final_returncode} after {len(attempts)} attempt(s)",
        "failedChecks": [] if final_ok else [{
            "name": "web_project_e2e_command",
            "category": "test_failure",
            "reason": output[-1000:] or f"exit code {final_returncode}",
            "evidence": f"logs/iter-{args.iteration}/web-probe-flow.log",
        }],
        "evidence": [
            {"type": "other", "note": "web-probe-flow-summary", "path": f"logs/iter-{args.iteration}/web-probe-flow-summary.json"},
            {"type": "other", "note": "web-action-intent-summary", "path": f"logs/iter-{args.iteration}/web-action-intent-summary.json"},
            {"type": "other", "note": "ui-action-trace", "path": f"logs/iter-{args.iteration}/action-trace.jsonl"},
            {"type": "log", "note": "web-probe-flow-log", "path": f"logs/iter-{args.iteration}/web-probe-flow.log"},
            *[{"type": "log", "note": f"web-probe-flow-attempt-{item['attempt']}", "path": item["log"]} for item in attempts],
        ],
        "nextAction": "finish" if final_ok else "retry_generator",
    }
    add_web_probe_flow_evidence_index(evaluation, args.iteration, include_attempts=attempts)
    if final_ok:
        evaluation = attach_required_test_results(
            task_dir,
            evaluation,
            "pass",
            [f"logs/iter-{args.iteration}/web-probe-flow.log", f"logs/iter-{args.iteration}/web-probe-flow-summary.json", f"logs/iter-{args.iteration}/action-trace.jsonl"],
            "Web project E2E command passed.",
            source="web-probe-flow",
            observed_signals=["web project E2E command passed", "ui action trace recorded"],
            metric_name="web_probe_flow_result",
        )
    write_json(task_dir / "evaluation.json", evaluation)
    append_validation(task_dir, args.iteration, evaluation, actions)
    evaluation = run_ui_evidence_gate(task_dir, args.iteration, evaluation)
    write_json(task_dir / "evaluation.json", evaluation)
    print(json.dumps(evaluation, ensure_ascii=False, indent=2))
    return 0 if evaluation.get("result") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
