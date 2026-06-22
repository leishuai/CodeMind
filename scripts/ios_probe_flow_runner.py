#!/usr/bin/env python3
"""iOS probe-flow runner.

This runner keeps iOS execution on the proven XCUITest backend while making the
task action intent explicit and reviewable. A probe-flow can now carry:

- app / testPlan: XCUITest project and device configuration
- testIntent / intent: goal, sources, acceptance criteria, authorization
- actionPlan / steps: concrete UI actions grounded in that intent

Dry-run validates both backend config and task intent without requiring a
connected device. Real execution still delegates to ios_xcuitest_runner.py.
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

try:
    from probe_flow_screenshots import ensure_per_tc_default_screenshots
except ModuleNotFoundError:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from probe_flow_screenshots import ensure_per_tc_default_screenshots  # type: ignore[no-redef]

ROOT = RUNTIME_ROOT

SUPPORTED_ACTIONS = {"tap", "tap_if_present", "input", "scroll", "assert_exists", "assert_text", "assert_page", "wait"}


def runtime_timeout(env_var: str = "AUTOMIND_CMD_TIMEOUT", default: int = 43200) -> int:
    raw = os.environ.get(env_var) or os.environ.get("AUTOMIND_CMD_TIMEOUT") or str(default)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default

SELECTOR_ACTIONS = {"tap", "tap_if_present", "input", "assert_exists", "assert_text"}
SENSITIVE_KEYWORDS = {
    "payment",
    "purchase",
    "refund",
    "delete",
    "uninstall",
    "credential",
    "password",
    "token",
    "secret",
    "p12",
    "keychain",
}


def write_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def add_evidence_index_entry(container: dict[str, Any], path: str | None, *, ev_type: str = "other", tc: Any = None, signal: Any = None) -> None:
    if not path:
        return
    entry: dict[str, Any] = {"path": str(path), "type": ev_type}
    if tc:
        entry["tc"] = tc
    if signal:
        entry["signal"] = signal
    entries = container.setdefault("evidenceIndex", [])
    if entry not in entries:
        entries.append(entry)


def add_ios_probe_flow_evidence_index(
    container: dict[str, Any],
    iteration: int,
    *,
    include_summary: bool = True,
    include_action_plan: bool = False,
    include_attempts: bool = False,
) -> None:
    if include_summary:
        add_evidence_index_entry(container, f"logs/iter-{iteration}/ios-probe-flow-summary.json", ev_type="summary", signal="ios_probe_flow_summary")
    add_evidence_index_entry(container, f"logs/iter-{iteration}/ios-action-intent-summary.json", ev_type="summary", signal="ios_action_intent_summary")
    add_evidence_index_entry(container, f"logs/iter-{iteration}/action-trace.jsonl", ev_type="trace", signal="ui_action_trace_recorded")
    if include_action_plan:
        add_evidence_index_entry(container, f"logs/iter-{iteration}/action-plan.materialized.ios.json", ev_type="summary", signal="ios_action_plan_materialized")
    if include_attempts:
        add_evidence_index_entry(container, f"logs/iter-{iteration}/ios-probe-flow.log", ev_type="log", signal="ios_probe_flow_log")
        add_evidence_index_entry(container, f"logs/iter-{iteration}/ios-probe-flow-attempts.json", ev_type="summary", signal="ios_probe_flow_attempts")


def append_jsonl(path: pathlib.Path, item: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")


def is_critical_action(step_type: str | None, step: dict[str, Any]) -> bool:
    if step.get("critical") is True:
        return True
    if step.get("screenshotAfter") is True or step.get("evidence", {}).get("screenshotAfter") is True:
        return True
    return step_type in {"launch", "tap", "tap_if_present", "input", "scroll"}


def selector_target(selector: Any) -> str:
    if not isinstance(selector, dict):
        return "-"
    for key in ["accessibilityIdentifier", "identifier", "label", "text", "predicate", "xpath"]:
        if selector.get(key):
            return f"{key}={selector.get(key)}"
    if {"x", "y"}.issubset(selector):
        return f"coord=({selector.get('x')},{selector.get('y')})"
    return "-"


def build_action_trace(intent: dict[str, Any], trace_path: pathlib.Path, phase: str) -> list[dict[str, Any]]:
    """Write a compact iOS UI action trace from the reviewable action plan.

    iOS execution remains XCUITest-backed. This trace records the planned
    action intent/selector/result relationship so Validation.md can use the
    same evidence shape as Android. Per-step screenshots/accessibility dumps
    should be filled by project XCUITest artifacts when available.
    """
    trace_path.write_text("")
    actions: list[dict[str, Any]] = []
    steps = intent.get("steps") or []
    if not isinstance(steps, list):
        return actions
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        step_type = step.get("type")
        critical = is_critical_action(step_type, step)
        if not critical:
            continue
        entry: dict[str, Any] = {
            "stepIndex": index,
            "name": step.get("name") or step.get("description") or f"{index}:{step_type}",
            "type": step_type,
            "intent": step.get("intent") or step.get("goal") or step.get("name") or step.get("description"),
            "selector": step.get("selector"),
            "target": selector_target(step.get("selector")),
            "critical": True,
            "platform": "ios",
            "backend": "xcuitest",
            "evidencePhase": phase,
            "ok": None,
            "detail": "planned action; actual execution evidence is in XCUITest logs/xcresult",
            "evidenceAfter": {
                "screenshot": step.get("screenshotAfter") or step.get("evidence", {}).get("screenshotAfter"),
                "accessibility": step.get("accessibilityAfter") or step.get("evidence", {}).get("accessibilityAfter"),
                "xcresult": None,
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
        target = action.get("target") or selector_target(action.get("selector"))
        detail = str(action.get("detail") or "").strip()
        detail_text = f"; detail: {detail[:180]}" if detail else ""
        after = action.get("evidenceAfter") if isinstance(action.get("evidenceAfter"), dict) else {}
        shot = after.get("screenshot") or "see XCUITest xcresult/project attachments if available"
        accessibility = after.get("accessibility") or "see XCUITest logs/xcresult if available"
        lines.append(f"- Step {idx} `{typ}` {result}: {name}; target: `{target}`{detail_text}")
        lines.append(f"  - after screenshot: `{shot}`")
        lines.append(f"  - after accessibility: `{accessibility}`")
    if len(actions) > limit:
        lines.append(f"- ... {len(actions) - limit} more critical actions in `action-trace.jsonl`.")
    return lines


def append_probe_flow_validation(task_dir: pathlib.Path, iteration: int, evaluation: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    validation = task_dir / "Validation.md"
    existing = validation.read_text(errors="ignore") if validation.exists() else "# Validation\n"
    marker = f"## Iteration {iteration} - iOS Probe Flow"
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

- `logs/iter-{iteration}/ios-probe-flow-summary.json`
- `logs/iter-{iteration}/ios-action-intent-summary.json`
- `logs/iter-{iteration}/action-trace.jsonl`
- `logs/iter-{iteration}/ios-probe-flow.log`
- `logs/iter-{iteration}/TestResults.xcresult` when XCUITest ran
- `evaluation.json`

### Client UI action evidence

{action_block}
"""
    validation.write_text(existing.rstrip() + "\n" + block.lstrip())


def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def read_json_object_from_log(path: pathlib.Path) -> dict[str, Any]:
    """Read a JSON object from a normal JSON file or a log with trailing text."""
    text = path.read_text(errors="ignore")
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def runtime_proof_required(task_dir: pathlib.Path) -> bool:
    state_path = task_dir / "runtime-state.json"
    if not state_path.exists():
        return False
    try:
        state = read_json(state_path)
    except Exception:
        return False
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
    bundle = review.get("decisionBundle") if isinstance(review.get("decisionBundle"), dict) else {}
    value = bundle.get("runtimeProofRequired") or state.get("runtimeProofRequired")
    return str(value or "").strip().lower() in {"yes", "true", "required", "1"}


def latest_signing_recommendation(task_dir: pathlib.Path) -> dict[str, Any]:
    """Return the newest usable ios-signing-preflight recommendation, if any."""
    candidates = sorted(
        [
            *(task_dir / "logs").glob("iter-*/ios-signing-preflight*.json"),
            *(task_dir / "logs").glob("iter-*/ios-signing-preflight*.log"),
        ],
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for path in candidates:
        data = read_json_object_from_log(path)
        rec = data.get("recommendation") if isinstance(data.get("recommendation"), dict) else {}
        team = str(rec.get("recommendedTeam") or "").strip()
        if team and rec.get("canRetryWithExistingMaterial") is not False:
            return {
                "source": str(path.relative_to(task_dir)),
                "recommendedTeam": team,
                "recommendedCodeSignStyle": rec.get("recommendedCodeSignStyle") or "Automatic",
                "automaticSigningViable": bool(rec.get("automaticSigningViable", True)),
                "rebuildHint": rec.get("rebuildHint"),
            }
    return {}


def apply_signing_recommendation(task_dir: pathlib.Path, app: dict[str, str]) -> dict[str, Any]:
    """Forward a better DEVELOPMENT_TEAM hint to the XCUITest runner.

    The probe-flow may carry a stale/default demo DEVELOPMENT_TEAM. If signing
    preflight discovered existing local material and recommends a Team, pass that
    Team through so the runner has a concrete target. The runner's own
    `resolve_signing_plan` remains the single source of truth for the signing
    *strategy* (simulator_no_sign / manual_reuse / automatic / blocked); this
    helper only supplies the Team hint and no longer gates on
    `automaticSigningViable`, so Manual-reuse recommendations are forwarded too.
    """
    rec = latest_signing_recommendation(task_dir)
    if not rec:
        return {"applied": False, "reason": "no signing recommendation"}
    team = str(rec.get("recommendedTeam") or "").strip()
    old_team = str(app.get("team") or "").strip()
    if not team or team == old_team:
        return {"applied": False, "reason": "team already matches recommendation", "recommendation": rec}
    app["team"] = team
    return {
        "applied": True,
        "previousTeam": old_team,
        "team": team,
        "recommendation": rec,
        "reason": "forwarded ios-signing-preflight Team hint to the runner; runner.resolve_signing_plan owns the strategy",
    }


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def run_preflight(task_code: str, iteration: int, core_device_id: str | None = None) -> tuple[bool, dict[str, Any], str]:
    cmd = [sys.executable, str(ROOT / "scripts" / "ios_preflight.py"), task_code, str(iteration)]
    if core_device_id:
        cmd += ["--device-id", core_device_id]
    proc = subprocess.run(cmd, cwd=str(WORKSPACE_ROOT), text=True, capture_output=True, timeout=runtime_timeout("AUTOMIND_PREFLIGHT_TIMEOUT"))
    output = proc.stdout + proc.stderr
    evaluation_path = TASKS_DIR / task_code / "evaluation.json"
    evaluation = read_json(evaluation_path) if evaluation_path.exists() else {
        "iteration": iteration,
        "result": "blocked",
        "summary": "iOS preflight did not produce evaluation.json",
        "failedChecks": [{"name": "ios_preflight", "category": "invalid_evaluation_output", "reason": output[-1000:]}],
        "nextAction": "replan",
    }
    return (
        evaluation.get("result") in {"pass", "in_progress"}
        and evaluation.get("nextAction") in {"finish", "retry_generator"}
    ), evaluation, output


def normalize_app(flow: dict[str, Any]) -> dict[str, str]:
    app = flow.get("app") or flow.get("iosApp") or {}
    if not isinstance(app, dict):
        app = {}
    test_plan = flow.get("testPlan") or {}
    if isinstance(test_plan, dict):
        # testPlan values override app for backwards compatibility with older probe-flow files.
        merged = dict(app)
        merged.update({k: v for k, v in test_plan.items() if v not in (None, "")})
        app = merged
    mapping = {
        "project_path": app.get("projectPath") or app.get("project_path"),
        "workspace_path": app.get("workspacePath") or app.get("workspace_path"),
        "scheme": app.get("scheme"),
        "device_id": app.get("xcodebuildDeviceId") or app.get("deviceId") or app.get("device_id"),
        "core_device_id": app.get("coreDeviceId") or app.get("core_device_id"),
        "team": app.get("team"),
        "bundle_id": app.get("bundleId") or app.get("bundle_id"),
        "target_bundle_id": app.get("targetBundleId") or app.get("target_bundle_id"),
        "configuration": app.get("configuration") or "Debug",
        "destination_type": app.get("destinationType") or app.get("destination_type"),
    }
    return {k: str(v) for k, v in mapping.items() if v}


def normalize_intent(flow: dict[str, Any]) -> dict[str, Any]:
    """Normalize task intent/action steps from an iOS probe-flow.

    Supported shapes are intentionally permissive so older files keep working:
    - testIntent: {goal, source/sources, acceptanceCriteria, steps}
    - intent: {goal, source/sources, acceptanceCriteria}
    - actionPlan: {steps}
    - steps: [...]
    """
    intent = flow.get("testIntent") or flow.get("intent") or {}
    if not isinstance(intent, dict):
        intent = {"goal": str(intent)}
    action_plan = flow.get("actionPlan") or {}
    if not isinstance(action_plan, dict):
        action_plan = {}

    steps = intent.get("steps") or action_plan.get("steps") or flow.get("steps") or []
    post_checks = intent.get("postChecks") or action_plan.get("postChecks") or flow.get("postChecks") or []
    acceptance = intent.get("acceptanceCriteria") or intent.get("acceptance") or flow.get("acceptanceCriteria") or []
    sources = intent.get("sources") or intent.get("source") or flow.get("intentSources") or []

    if isinstance(steps, list):
        # Default to one screenshot per runtime/UI TC so report.html can show
        # per-TC evidence. Best-effort: no warning when a TC ends up without one.
        ensure_per_tc_default_screenshots(steps)

    return {
        "goal": intent.get("goal") or flow.get("name") or "",
        "sources": [str(v) for v in as_list(sources) if str(v).strip()],
        "acceptanceCriteria": [str(v) for v in as_list(acceptance) if str(v).strip()],
        "preconditions": [str(v) for v in as_list(intent.get("preconditions") or flow.get("preconditions")) if str(v).strip()],
        "authorization": intent.get("authorization") or flow.get("authorization") or {},
        "steps": steps if isinstance(steps, list) else [],
        "postChecks": post_checks if isinstance(post_checks, list) else [],
    }


def validate_intent(intent: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return errors and warnings for task action intent."""
    errors: list[str] = []
    warnings: list[str] = []
    steps = intent.get("steps") or []

    if steps and not str(intent.get("goal") or "").strip():
        errors.append("testIntent.goal is required when action steps are present")
    if steps and not intent.get("sources"):
        errors.append("testIntent.sources/source is required so the runner does not invent a product flow")
    if steps and not intent.get("acceptanceCriteria") and not intent.get("postChecks"):
        warnings.append("action steps have no acceptanceCriteria/postChecks; post-action validation may drift")

    authorization = intent.get("authorization") or {}
    if authorization and not isinstance(authorization, dict):
        errors.append("authorization must be an object when present")
        authorization = {}
    allowed_scopes = {str(v).lower() for v in as_list(authorization.get("scopes") or authorization.get("scope"))}

    for idx, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            errors.append(f"step {idx} must be object")
            continue
        typ = step.get("type")
        if typ not in SUPPORTED_ACTIONS:
            errors.append(f"step {idx} unsupported action type: {typ}")
        if typ in SELECTOR_ACTIONS and not isinstance(step.get("selector"), dict):
            errors.append(f"step {idx} action {typ} requires selector object")
        if typ == "input" and "text" not in step:
            errors.append(f"step {idx} input action requires text")

        name = str(step.get("name") or step.get("description") or "").lower()
        tags = {str(v).lower() for v in as_list(step.get("tags") or step.get("risk") or step.get("category"))}
        sensitive = bool(step.get("sensitive") or step.get("destructive"))
        if any(k in name for k in SENSITIVE_KEYWORDS) or (tags & SENSITIVE_KEYWORDS):
            sensitive = True
        if sensitive:
            scope = str(step.get("authorizationScope") or step.get("scope") or "").lower()
            if not scope or scope not in allowed_scopes:
                errors.append(
                    f"step {idx} is sensitive/destructive but authorization.scopes does not include `{scope or '<missing>'}`"
                )
    return errors, warnings


def build_action_plan(flow: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    app = flow.get("app") or flow.get("iosApp") or {}
    if not isinstance(app, dict):
        app = {}
    test_plan = flow.get("testPlan") if isinstance(flow.get("testPlan"), dict) else {}
    action_plan = flow.get("actionPlan") or {}
    if not isinstance(action_plan, dict):
        action_plan = {}
    return {
        "name": action_plan.get("name") or flow.get("name") or intent.get("goal") or "iOS probe-flow action plan",
        "bundleId": action_plan.get("bundleId") or app.get("bundleId") or app.get("bundle_id") or test_plan.get("bundleId") or "",
        "strategy": action_plan.get("strategy") or "xcuitest-intent",
        "intent": {
            "goal": intent.get("goal"),
            "sources": intent.get("sources"),
            "acceptanceCriteria": intent.get("acceptanceCriteria"),
            "preconditions": intent.get("preconditions"),
        },
        "authorization": intent.get("authorization"),
        "steps": intent.get("steps") or [],
        "postChecks": intent.get("postChecks") or [],
    }


def blocked(task_dir: pathlib.Path, iteration: int, summary: str, name: str, reason: str, evidence: list[dict[str, Any]] | None = None) -> int:
    evaluation = {
        "iteration": iteration,
        "result": "blocked",
        "summary": summary,
        "failedChecks": [{"name": name, "category": "needs_replan", "reason": reason}],
        "evidence": evidence or [],
        "nextAction": "replan",
    }
    write_json(task_dir / "evaluation.json", evaluation)
    print(json.dumps(evaluation, ensure_ascii=False, indent=2))
    return 2



def run_ui_evidence_gate(task_dir: pathlib.Path, iteration: int, evaluation: dict[str, Any]) -> dict[str, Any]:
    """Run evidence completeness check and gate pass results on hard evidence failures."""
    script = ROOT / "scripts" / "ui_evidence_check.py"
    proc = subprocess.run(
        [sys.executable, str(script), task_dir.name, str(iteration), "--json"],
        cwd=str(WORKSPACE_ROOT),
        text=True,
        capture_output=True,
        timeout=runtime_timeout("AUTOMIND_UI_EVIDENCE_TIMEOUT", 300),
    )
    report_path = f"logs/iter-{iteration}/ui-evidence-check.json"
    evidence = evaluation.setdefault("evidence", [])
    if not any(isinstance(item, dict) and item.get("path") == report_path for item in evidence):
        evidence.append({"type": "other", "note": "ui-evidence-check", "path": report_path})
    report = read_json(task_dir / report_path) if (task_dir / report_path).exists() else {}
    if proc.returncode != 0 and report.get("result") != "fail":
        report = {"result": "fail", "summary": (proc.stdout + proc.stderr)[-1000:] or "ui-evidence-check failed to run"}
    if report.get("result") == "fail":
        failed_checks = evaluation.setdefault("failedChecks", [])
        failed_checks.append({
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
    parser = argparse.ArgumentParser(description="Run iOS probe-flow")
    parser.add_argument("task_code")
    parser.add_argument("iteration", nargs="?", type=int, default=1)
    parser.add_argument("--flow", default="probe-flow.ios.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retries", type=int, default=0, help="Retry failed XCUITest command up to N times without changing the probe-flow")
    args = parser.parse_args()

    task_dir = TASKS_DIR / args.task_code
    iter_dir = task_dir / "logs" / f"iter-{args.iteration}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    flow_path = pathlib.Path(args.flow)
    if not flow_path.is_absolute():
        candidate = task_dir / flow_path
        flow_path = candidate if candidate.exists() else ((WORKSPACE_ROOT / flow_path).resolve() if (WORKSPACE_ROOT / flow_path).exists() else ROOT / flow_path)

    if not flow_path.exists():
        return blocked(
            task_dir,
            args.iteration,
            "Missing iOS probe-flow file",
            "ios_probe_flow",
            f"Flow file not found: {flow_path}",
        )

    flow = read_json(flow_path)
    adapter = flow.get("adapter") or flow.get("runner") or "xcuitest"
    if adapter != "xcuitest":
        return blocked(
            task_dir,
            args.iteration,
            f"Unsupported iOS probe-flow adapter: {adapter}",
            "ios_probe_flow_adapter",
            "Only adapter=xcuitest is supported in v1",
            [{"type": "other", "note": "ios-probe-flow", "path": str(flow_path)}],
        )

    app = normalize_app(flow)
    missing = []
    if not (app.get("project_path") or app.get("workspace_path")):
        missing.append("projectPath or workspacePath")
    for key in ["scheme", "device_id"]:
        if not app.get(key):
            missing.append(key)
    if missing:
        return blocked(
            task_dir,
            args.iteration,
            "Missing iOS XCUITest config in probe-flow",
            "ios_probe_flow_config",
            f"Missing config: {', '.join(missing)}",
            [{"type": "other", "note": "ios-probe-flow", "path": str(flow_path)}],
        )

    intent = normalize_intent(flow)
    intent_errors, intent_warnings = validate_intent(intent)
    action_plan = build_action_plan(flow, intent)
    write_json(iter_dir / "ios-action-intent-summary.json", {
        "flow": str(flow_path),
        "adapter": adapter,
        "app": app,
        "intent": intent,
        "actionPlan": action_plan,
        "errors": intent_errors,
        "warnings": intent_warnings,
    })
    actions = build_action_trace(intent, iter_dir / "action-trace.jsonl", "dry-run" if args.dry_run else "xcuitest")
    if action_plan["steps"]:
        write_json(iter_dir / "action-plan.materialized.ios.json", action_plan)
    if intent_errors:
        return blocked(
            task_dir,
            args.iteration,
            "iOS probe-flow action intent validation failed",
            "ios_probe_flow_intent",
            "; ".join(intent_errors),
            [{"type": "other", "note": "ios-action-intent-summary", "path": f"logs/iter-{args.iteration}/ios-action-intent-summary.json"}],
        )

    # Real execution requires platform readiness. Dry-run skips preflight so
    # config/schema/intent checks can run without a connected device.
    if not args.dry_run:
        ok, preflight_eval, preflight_output = run_preflight(args.task_code, args.iteration, app.get("core_device_id"))
        (iter_dir / "ios-preflight-before-probe-flow.log").write_text(preflight_output)
        if not ok:
            evidence = preflight_eval.setdefault("evidence", [])
            evidence.append({"type": "log", "path": f"logs/iter-{args.iteration}/ios-preflight-before-probe-flow.log"})
            evidence.append({"type": "other", "note": "ios-action-intent-summary", "path": f"logs/iter-{args.iteration}/ios-action-intent-summary.json"})
            preflight_eval["summary"] = "iOS probe-flow blocked by preflight: " + str(preflight_eval.get("summary", "preflight failed"))
            preflight_eval["nextAction"] = "ask_user" if preflight_eval.get("nextAction") == "ask_user" else "replan"
            write_json(task_dir / "evaluation.json", preflight_eval)
            print(json.dumps(preflight_eval, ensure_ascii=False, indent=2))
            return 2

    if args.dry_run:
        if runtime_proof_required(task_dir):
            summary = {
                "result": "blocked",
                "adapter": "xcuitest",
                "flow": str(flow_path),
                "app": app,
                "intentGoal": intent.get("goal"),
                "actionSteps": len(action_plan.get("steps") or []),
                "postChecks": len(action_plan.get("postChecks") or []),
                "criticalActions": len(actions),
                "actionTrace": f"logs/iter-{args.iteration}/action-trace.jsonl",
                "warnings": intent_warnings,
                "dryRun": True,
                "runtimeProofRequired": True,
            }
            add_ios_probe_flow_evidence_index(summary, args.iteration, include_action_plan=bool(action_plan["steps"]))
            write_json(iter_dir / "ios-probe-flow-summary.json", summary)
            evaluation = {
                "iteration": args.iteration,
                "result": "blocked",
                "summary": (
                    "iOS probe-flow dry-run validated action intent only, but runtimeProofRequired=yes "
                    "requires real device/XCUITest execution. Do not finish from dry-run; retry a real "
                    "runner path or replan to a runnable iOS UI strategy."
                ),
                "failedChecks": [{
                    "name": "ios_probe_flow_dry_run_runtime_proof",
                    "category": "validation_failure",
                    "reason": "dryRun=true cannot satisfy runtimeProofRequired=yes",
                    "evidence": f"logs/iter-{args.iteration}/ios-probe-flow-summary.json",
                }],
                "evidence": [
                    {"type": "other", "note": "ios-probe-flow-summary", "path": f"logs/iter-{args.iteration}/ios-probe-flow-summary.json"},
                    {"type": "other", "note": "ios-action-intent-summary", "path": f"logs/iter-{args.iteration}/ios-action-intent-summary.json"},
                    {"type": "other", "note": "ui-action-trace", "path": f"logs/iter-{args.iteration}/action-trace.jsonl"},
                ],
                "nextAction": "retry_generator",
            }
            if action_plan["steps"]:
                evaluation["evidence"].append({"type": "other", "note": "ios-action-plan", "path": f"logs/iter-{args.iteration}/action-plan.materialized.ios.json"})
            add_ios_probe_flow_evidence_index(evaluation, args.iteration, include_action_plan=bool(action_plan["steps"]))
            write_json(task_dir / "evaluation.json", evaluation)
            append_probe_flow_validation(task_dir, args.iteration, evaluation, actions)
            print(json.dumps(evaluation, ensure_ascii=False, indent=2))
            return 1

        summary = {
            "result": "pass",
            "adapter": "xcuitest",
            "flow": str(flow_path),
            "app": app,
            "intentGoal": intent.get("goal"),
            "actionSteps": len(action_plan.get("steps") or []),
            "postChecks": len(action_plan.get("postChecks") or []),
            "criticalActions": len(actions),
            "actionTrace": f"logs/iter-{args.iteration}/action-trace.jsonl",
            "warnings": intent_warnings,
            "dryRun": True,
        }
        add_ios_probe_flow_evidence_index(summary, args.iteration, include_action_plan=bool(action_plan["steps"]))
        write_json(iter_dir / "ios-probe-flow-summary.json", summary)
        evaluation = {
            "iteration": args.iteration,
            "result": "pass",
            "summary": "iOS probe-flow dry-run passed with action intent validation",
            "failedChecks": [],
            "evidence": [
                {"type": "other", "note": "ios-probe-flow-summary", "path": f"logs/iter-{args.iteration}/ios-probe-flow-summary.json"},
                {"type": "other", "note": "ios-action-intent-summary", "path": f"logs/iter-{args.iteration}/ios-action-intent-summary.json"},
                {"type": "other", "note": "ui-action-trace", "path": f"logs/iter-{args.iteration}/action-trace.jsonl"},
            ],
            "nextAction": "finish",
        }
        if action_plan["steps"]:
            evaluation["evidence"].append({"type": "other", "note": "ios-action-plan", "path": f"logs/iter-{args.iteration}/action-plan.materialized.ios.json"})
        add_ios_probe_flow_evidence_index(evaluation, args.iteration, include_action_plan=bool(action_plan["steps"]))
        evaluation = attach_required_test_results(
            task_dir,
            evaluation,
            "pass",
            [f"logs/iter-{args.iteration}/ios-probe-flow-summary.json", f"logs/iter-{args.iteration}/ios-action-intent-summary.json", f"logs/iter-{args.iteration}/action-trace.jsonl"],
            "iOS probe-flow dry-run validated action intent evidence.",
            source="ios-probe-flow",
            observed_signals=["ios action intent valid", "ui action trace recorded"],
            metric_name="ios_probe_flow_dry_run",
        )
        write_json(task_dir / "evaluation.json", evaluation)
        append_probe_flow_validation(task_dir, args.iteration, evaluation, actions)
        evaluation = run_ui_evidence_gate(task_dir, args.iteration, evaluation)
        write_json(task_dir / "evaluation.json", evaluation)
        print(json.dumps(evaluation, ensure_ascii=False, indent=2))
        return 0 if evaluation.get("result") == "pass" else 1

    signing_recommendation = apply_signing_recommendation(task_dir, app)

    cmd = [sys.executable, str(ROOT / "scripts" / "ios_xcuitest_runner.py"), args.task_code, str(args.iteration)]
    if app.get("project_path"):
        cmd += ["--project-path", app["project_path"]]
    if app.get("workspace_path"):
        cmd += ["--workspace-path", app["workspace_path"]]
    cmd += ["--scheme", app["scheme"], "--device-id", app["device_id"]]
    if app.get("team"):
        cmd += ["--team", app["team"]]
    if app.get("bundle_id"):
        cmd += ["--bundle-id", app["bundle_id"]]
    if app.get("target_bundle_id"):
        cmd += ["--target-bundle-id", app["target_bundle_id"]]
    if app.get("configuration"):
        cmd += ["--configuration", app["configuration"]]
    if app.get("destination_type"):
        cmd += ["--destination-type", app["destination_type"]]

    write_json(iter_dir / "ios-probe-flow-command.json", {
        "cmd": cmd,
        "flow": str(flow_path),
        "app": app,
        "intent": intent,
        "signingRecommendation": signing_recommendation,
    })
    attempts: list[dict[str, Any]] = []
    output = ""
    final_returncode = 1
    max_attempts = max(1, int(args.retries) + 1)
    evaluation_path = task_dir / "evaluation.json"
    for attempt in range(1, max_attempts + 1):
        if evaluation_path.exists():
            evaluation_path.unlink()
        proc = subprocess.run(cmd, cwd=str(WORKSPACE_ROOT), text=True, capture_output=True, timeout=runtime_timeout("AUTOMIND_EVALUATOR_TIMEOUT"))
        attempt_output = proc.stdout + proc.stderr
        attempt_eval_result = None
        if evaluation_path.exists():
            try:
                attempt_eval_result = read_json(evaluation_path).get("result")
            except Exception:
                attempt_eval_result = "invalid_json"
        attempt_log = iter_dir / f"ios-probe-flow-attempt-{attempt}.log"
        attempt_log.write_text(attempt_output)
        attempts.append({
            "attempt": attempt,
            "exitCode": proc.returncode,
            "evaluationResult": attempt_eval_result,
            "log": f"logs/iter-{args.iteration}/ios-probe-flow-attempt-{attempt}.log",
            "retried": proc.returncode != 0 and attempt < max_attempts,
        })
        output += f"\n--- attempt {attempt}/{max_attempts} exit={proc.returncode} evaluation={attempt_eval_result} ---\n" + attempt_output
        final_returncode = proc.returncode
        if proc.returncode == 0 and attempt_eval_result in {"pass", None}:
            break
    (iter_dir / "ios-probe-flow.log").write_text(output)

    if evaluation_path.exists():
        evaluation = read_json(evaluation_path)
    else:
        evaluation = {
            "iteration": args.iteration,
            "result": "blocked",
            "summary": "ios_xcuitest_runner did not produce evaluation.json",
            "failedChecks": [{
                "name": "ios_xcuitest_runner",
                "category": "invalid_evaluation_output",
                "reason": output[-1000:] or f"exit code {final_returncode}",
                "evidence": f"logs/iter-{args.iteration}/ios-probe-flow.log",
            }],
            "nextAction": "replan",
        }
    evaluation["signingRecommendation"] = signing_recommendation
    first_category = ""
    failed_checks = evaluation.get("failedChecks") if isinstance(evaluation.get("failedChecks"), list) else []
    if failed_checks and isinstance(failed_checks[0], dict):
        first_category = str(failed_checks[0].get("category") or "")
    if first_category == "external_runner_root_install_unsupported":
        evaluation["summary"] = (
            str(evaluation.get("summary") or "")
            + " Do not downgrade this real-device task to dry-run. This blocker is specific to retail physical devices; "
            "the external UI runner still supports execution on iOS Simulator. For real-device proof, retry with a different "
            "iOS UI runner delivery strategy: project/native UI test target, build-for-testing + devicectl install + "
            "test-without-building, or WDA/go-ios."
        ).strip()
        evaluation["nextAction"] = "retry_generator"
        evaluation["recommendedRunnerStrategy"] = {
            "reason": "retail physical device rejects Root install style for the external XCUITest runner; the external UI runner supports iOS Simulator execution",
            "doNotUse": ["probe-flow --dry-run as runtime proof", "same xcodebuild test root-install path for real-device proof"],
            "try": [
                "project/native UI test target through xcodebuild",
                "build-for-testing -> devicectl install -> test-without-building",
                "WebDriverAgent/go-ios based runner with a complete IDE-side channel",
                "external UI runner on iOS Simulator when simulator coverage is acceptable for the TC",
            ],
        }
    evidence = evaluation.setdefault("evidence", [])
    evidence.append({"type": "other", "note": "ios-probe-flow", "path": str(flow_path)})
    evidence.append({"type": "other", "note": "ios-action-intent-summary", "path": f"logs/iter-{args.iteration}/ios-action-intent-summary.json"})
    evidence.append({"type": "other", "note": "ui-action-trace", "path": f"logs/iter-{args.iteration}/action-trace.jsonl"})
    evidence.append({"type": "log", "note": "ios-probe-flow-log", "path": f"logs/iter-{args.iteration}/ios-probe-flow.log"})
    for item in attempts:
        evidence.append({"type": "log", "note": f"ios-probe-flow-attempt-{item['attempt']}", "path": item["log"]})
    write_json(iter_dir / "ios-probe-flow-attempts.json", {"attempts": attempts, "retries": max_attempts - 1})
    evidence.append({"type": "other", "note": "ios-probe-flow-attempts", "path": f"logs/iter-{args.iteration}/ios-probe-flow-attempts.json"})
    add_ios_probe_flow_evidence_index(evaluation, args.iteration, include_summary=False, include_action_plan=bool(action_plan["steps"]), include_attempts=True)
    # Mark planned actions with the final adapter result. Step-level verdicts
    # remain in XCUITest/project logs unless the project emits finer evidence.
    final_ok = evaluation.get("result") == "pass"
    for action in actions:
        action["ok"] = final_ok if evaluation.get("result") in {"pass", "fail"} else None
        if isinstance(action.get("evidenceAfter"), dict):
            action["evidenceAfter"]["xcresult"] = f"logs/iter-{args.iteration}/TestResults.xcresult"
    trace_path = iter_dir / "action-trace.jsonl"
    trace_path.write_text("")
    for action in actions:
        append_jsonl(trace_path, action)
    if final_ok:
        evaluation = attach_required_test_results(
            task_dir,
            evaluation,
            "pass",
            [f"logs/iter-{args.iteration}/ios-probe-flow.log", f"logs/iter-{args.iteration}/ios-probe-flow-attempts.json", f"logs/iter-{args.iteration}/action-trace.jsonl"],
            "iOS probe-flow/XCUITest adapter returned pass.",
            source="ios-probe-flow",
            observed_signals=["ios probe-flow adapter passed", "ui action trace recorded"],
            metric_name="ios_probe_flow_result",
        )
    write_json(task_dir / "evaluation.json", evaluation)
    append_probe_flow_validation(task_dir, args.iteration, evaluation, actions)
    evaluation = run_ui_evidence_gate(task_dir, args.iteration, evaluation)
    write_json(task_dir / "evaluation.json", evaluation)
    print(json.dumps(evaluation, ensure_ascii=False, indent=2))
    return 0 if evaluation.get("result") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
