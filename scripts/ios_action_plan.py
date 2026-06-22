#!/usr/bin/env python3
"""Validate and materialize iOS UI action plans.

This helper validates a declarative UI action plan and converts it into a
reviewable XCUITest Swift draft. AutoMind can execute real iOS app UI actions
when that draft is added to or used by a project/external XCUITest runner and
run with `ios-xcuitest`; this helper itself is the materialization/validation
step and does not mutate the target app project.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from automind_paths import RUNTIME_ROOT, TASKS_DIR, WORKSPACE_ROOT
from state_files import write_runtime_state

ROOT = RUNTIME_ROOT
TASKS = TASKS_DIR
SUPPORTED = {"tap", "tap_if_present", "input", "scroll", "assert_exists", "assert_text", "wait"}

try:
    from probe_flow_screenshots import ensure_per_tc_default_screenshots
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from probe_flow_screenshots import ensure_per_tc_default_screenshots  # type: ignore[no-redef]


def q(s: str) -> str:
    return s.replace('\\', '\\\\').replace('"', '\\"')


def selector_expr(sel: dict[str, Any]) -> str:
    if "accessibilityId" in sel:
        v = q(str(sel["accessibilityId"]))
        return f'app.descendants(matching: .any)["{v}"]'
    if "id" in sel:
        v = q(str(sel["id"]))
        return f'app.descendants(matching: .any)["{v}"]'
    if "text" in sel:
        v = q(str(sel["text"]))
        return f'app.staticTexts["{v}"]'
    if "button" in sel:
        v = q(str(sel["button"]))
        return f'app.buttons["{v}"]'
    if "predicate" in sel:
        v = q(str(sel["predicate"]))
        return f'app.descendants(matching: .any).matching(NSPredicate(format: "{v}")).firstMatch'
    return 'app.descendants(matching: .any).firstMatch /* TODO selector */'



def should_attach_screenshot(step: dict[str, Any], typ: str | None) -> bool:
    if step.get("screenshotAfter") is True or step.get("critical") is True:
        return True
    evidence = step.get("evidence") if isinstance(step.get("evidence"), dict) else {}
    if evidence.get("screenshotAfter") is True:
        return True
    return typ in {"tap", "tap_if_present", "input", "scroll"}


def screenshot_call(idx: int, typ: str | None, name: str) -> str:
    safe_name = q(str(name).replace("/", "_").replace(" ", "-"))
    safe_typ = q(str(typ or "action"))
    return f'        attachScreenshot("after-{idx:02d}-{safe_typ}-{safe_name}")'

def swift_for_step(step: dict[str, Any], idx: int) -> list[str]:
    typ = step.get("type")
    sel = step.get("selector") or {}
    name = step.get("name") or f"step_{idx}"
    el = selector_expr(sel if isinstance(sel, dict) else {})
    lines = [f'        // {idx}. {q(str(name))} ({typ})']
    timeout = float(step.get("timeout", 8))
    if typ == "tap":
        lines += [f'        let e{idx} = {el}', f'        XCTAssertTrue(e{idx}.waitForExistence(timeout: {timeout}), "missing element for {q(str(name))}")', f'        e{idx}.tap()']
        if should_attach_screenshot(step, typ):
            lines.append(screenshot_call(idx, typ, str(name)))
    elif typ == "tap_if_present":
        lines += [f'        let e{idx} = {el}', f'        if e{idx}.waitForExistence(timeout: {timeout}) {{ e{idx}.tap() }}']
        if should_attach_screenshot(step, typ):
            lines.append(screenshot_call(idx, typ, str(name)))
    elif typ == "input":
        text = q(str(step.get("text", "")))
        lines += [f'        let e{idx} = {el}', f'        XCTAssertTrue(e{idx}.waitForExistence(timeout: {timeout}), "missing input for {q(str(name))}")', f'        e{idx}.tap()', f'        e{idx}.typeText("{text}")']
        if should_attach_screenshot(step, typ):
            lines.append(screenshot_call(idx, typ, str(name)))
    elif typ == "scroll":
        direction = str(step.get("direction", "up"))
        count = int(step.get("count", 1))
        method = {"up":"swipeUp", "down":"swipeDown", "left":"swipeLeft", "right":"swipeRight"}.get(direction, "swipeUp")
        lines += [f'        for _ in 0..<{count} {{ app.{method}() }}']
        if should_attach_screenshot(step, typ):
            lines.append(screenshot_call(idx, typ, str(name)))
    elif typ in {"assert_exists", "assert_text"}:
        lines += [f'        let e{idx} = {el}', f'        XCTAssertTrue(e{idx}.waitForExistence(timeout: {timeout}), "assert failed for {q(str(name))}")']
        if should_attach_screenshot(step, typ):
            lines.append(screenshot_call(idx, typ, str(name)))
    elif typ == "wait":
        seconds = float(step.get("seconds", timeout))
        lines += [f'        sleep({int(seconds)})']
    return lines


def validate(plan: dict[str, Any]) -> list[str]:
    errors = []
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps must be a non-empty list")
        return errors
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            errors.append(f"step {i} must be object")
            continue
        typ = step.get("type")
        if typ not in SUPPORTED:
            errors.append(f"step {i} unsupported type: {typ}")
        if typ in {"tap", "tap_if_present", "input", "assert_exists", "assert_text"} and not isinstance(step.get("selector"), dict):
            errors.append(f"step {i} requires selector object")
        if typ == "input" and "text" not in step:
            errors.append(f"step {i} input requires text")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_code")
    parser.add_argument("plan", help="Path to action-plan.ios.json")
    parser.add_argument("--iteration", type=int, default=1)
    args = parser.parse_args()

    task_dir = TASKS / args.task_code
    log_dir = task_dir / "logs" / f"iter-{args.iteration}"
    log_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    plan_path = Path(args.plan)
    if not plan_path.is_absolute():
        plan_path = (task_dir / plan_path) if (task_dir / plan_path).exists() else Path.cwd() / plan_path

    if not plan_path.exists():
        result = {"result":"blocked", "summary":f"Action plan not found: {plan_path}", "errors":["missing_plan"]}
    else:
        plan = json.loads(plan_path.read_text())
        errors = validate(plan)
        if errors:
            result = {"result":"blocked", "summary":"iOS action plan validation failed", "errors":errors}
        else:
            # Default to one screenshot per runtime/UI TC so the generated Swift
            # attaches per-TC evidence. Best-effort, no warning when one is missing.
            ensure_per_tc_default_screenshots(plan.get("steps"))
            test_name = q(str(plan.get("name") or args.task_code))
            bundle = q(str(plan.get("bundleId") or ""))
            lines = [
                "import XCTest", "", "final class AutoMindGeneratedActionPlanTests: XCTestCase {",
                "    func attachScreenshot(_ name: String) {",
                "        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())",
                "        attachment.name = name",
                "        attachment.lifetime = .keepAlways",
                "        add(attachment)",
                "    }",
                "",
                f"    func test_{''.join(ch if ch.isalnum() else '_' for ch in test_name)}() throws {{",
                "        let app = XCUIApplication()",
            ]
            if bundle:
                lines.append(f'        // Target bundle id: {bundle}')
            lines.append("        app.launch()")
            for idx, step in enumerate(plan.get("steps", []), 1):
                lines.extend(swift_for_step(step, idx))
            lines += ["    }", "}", ""]
            swift = "\n".join(lines)
            (log_dir / "GeneratedActionPlanTests.swift").write_text(swift)
            result = {"result":"pass", "summary":"iOS action plan validated and XCUITest draft generated with screenshot attachments for key actions", "errors":[], "swift":"logs/iter-%d/GeneratedActionPlanTests.swift" % args.iteration, "screenshotAttachments": True}
    (log_dir / "ios-action-plan-summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    (log_dir / "evaluator.log").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    (log_dir / "env.json").write_text(json.dumps({"timestamp":datetime.now().isoformat(timespec="seconds"), "plan":str(plan_path), "taskCode":args.task_code}, ensure_ascii=False, indent=2)+"\n")
    (log_dir / "commands.md").write_text(f"# Commands\n\n```bash\n./automind.sh ios-action-plan {args.task_code} {args.plan} --iteration {args.iteration}\n```\n")
    if not (task_dir / "Requirements.md").exists():
        (task_dir / "Requirements.md").write_text("# Requirements - iOS Action Plan\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — iOS action plan validation\n- **AC-001**: Validate declarative iOS UI actions and generate an XCUITest draft.\n  - Verification method: ios-action-plan / TC-F01\n")
    if not (task_dir / "Plan.md").exists():
        (task_dir / "Plan.md").write_text("# Plan\n\nValidate action-plan.ios.json and materialize Swift XCUITest draft. Execute real app UI actions by running the generated/reviewed XCUITest through ios-xcuitest or a project/native runner.\n")
    if not (task_dir / "Validation.md").exists():
        (task_dir / "Validation.md").write_text("# Validation\n")
    (task_dir / "Validation.md").open("a").write(f"\n## Iteration {args.iteration} - iOS action plan\n\n- Environment: plan={plan_path}; cwd={Path.cwd()}\n- Commands: see `logs/iter-{args.iteration}/commands.md`\n- Result: {result['result'].upper()}\n- Summary: {result['summary']}\n- Evidence: `logs/iter-{args.iteration}/ios-action-plan-summary.json`, `GeneratedActionPlanTests.swift`\n- Screenshot attachments: generated Swift uses `XCTAttachment(screenshot:)` after key UI actions; executing it through XCUITest keeps screenshots in `.xcresult`.\n- Reusable findings: Prefer preserving iOS tap/input/scroll as XCUITest action plans; do not default to random coordinate taps on real apps.\n- Avoid repeating: Use coordinate actions only as fallback; prefer accessibility id/text/predicate.\n")
    evaluation={"iteration":args.iteration,"result":result['result'],"nextAction":"finish" if result['result']=='pass' else 'replan',"summary":result['summary'],"failedChecks":[] if result['result']=='pass' else [{"name":"ios_action_plan","category":"needs_replan","reason":"; ".join(result.get('errors',[])),"evidence":f"logs/iter-{args.iteration}/ios-action-plan-summary.json"}],"evidence":[{"type":"other", "note":"ios-action-plan-summary","path":f"logs/iter-{args.iteration}/ios-action-plan-summary.json"}]}
    if result.get('swift'):
        evaluation['evidence'].append({"type":"other", "note":"xcuitest-draft","path":result['swift']})
    (task_dir / "evaluation.json").write_text(json.dumps(evaluation, ensure_ascii=False, indent=2)+"\n")
    write_runtime_state(task_dir, {"taskId": args.task_code, "taskType": "ios", "status": "finished" if result['result'] == 'pass' else "blocked", "iteration": args.iteration, "nextAction": evaluation['nextAction'], "updatedAt": datetime.now().isoformat(timespec='seconds')})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['result']=='pass' else 2

if __name__ == "__main__":
    raise SystemExit(main())
