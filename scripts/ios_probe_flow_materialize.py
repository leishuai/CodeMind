#!/usr/bin/env python3
"""Materialize iOS probe-flow action intent into a Swift XCUITest draft.

This is a bridge between protocol-level `testIntent.steps[]` and executable
XCUITest code. It does not modify the target app project. It writes a generated
Swift file into the AutoMind task log directory so a human/agent can review it,
copy it into an external runner project, or run it via an available XCUITest
runner.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
from datetime import datetime
from typing import Any

from automind_paths import RUNTIME_ROOT, TASKS_DIR, WORKSPACE_ROOT

ROOT = RUNTIME_ROOT

try:
    from probe_flow_screenshots import ensure_per_tc_default_screenshots
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from probe_flow_screenshots import ensure_per_tc_default_screenshots  # type: ignore[no-redef]

SUPPORTED_ACTIONS = {"tap", "tap_if_present", "input", "scroll", "assert_exists", "assert_text", "wait"}
SELECTOR_ACTIONS = {"tap", "tap_if_present", "input", "assert_exists", "assert_text"}


def q(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def swift_identifier(value: str) -> str:
    name = re.sub(r"[^0-9A-Za-z_]", "_", value).strip("_") or "ProbeFlow"
    if name[0].isdigit():
        name = "Flow_" + name
    return name[:80]


def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_flow(flow: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    app = flow.get("app") or flow.get("iosApp") or {}
    if not isinstance(app, dict):
        app = {}
    test_plan = flow.get("testPlan") or {}
    if isinstance(test_plan, dict):
        merged = dict(app)
        merged.update({k: v for k, v in test_plan.items() if v not in (None, "")})
        app = merged

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
        # Default to one screenshot per runtime/UI TC so the generated Swift
        # attaches per-TC evidence. Best-effort, no warning when one is missing.
        ensure_per_tc_default_screenshots(steps)
    normalized_intent = {
        "goal": intent.get("goal") or flow.get("name") or "",
        "sources": [str(v) for v in as_list(sources) if str(v).strip()],
        "acceptanceCriteria": [str(v) for v in as_list(acceptance) if str(v).strip()],
        "authorization": intent.get("authorization") or flow.get("authorization") or {},
        "steps": steps if isinstance(steps, list) else [],
        "postChecks": post_checks if isinstance(post_checks, list) else [],
    }
    return app, normalized_intent


def validate(intent: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    steps = intent.get("steps") or []
    if not steps:
        errors.append("testIntent.steps/actionPlan.steps must be non-empty to materialize Swift")
    if steps and not intent.get("goal"):
        errors.append("testIntent.goal is required")
    if steps and not intent.get("sources"):
        errors.append("testIntent.sources/source is required")
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
    return errors


def selector_expr(selector: dict[str, Any]) -> str:
    if "accessibilityId" in selector:
        v = q(selector["accessibilityId"])
        return f'app.descendants(matching: .any)["{v}"]'
    if "id" in selector:
        v = q(selector["id"])
        return f'app.descendants(matching: .any)["{v}"]'
    if "button" in selector:
        v = q(selector["button"])
        return f'app.buttons["{v}"]'
    if "text" in selector:
        v = q(selector["text"])
        return f'app.staticTexts["{v}"]'
    if "predicate" in selector:
        v = q(selector["predicate"])
        return f'app.descendants(matching: .any).matching(NSPredicate(format: "{v}")).firstMatch'
    return 'app.descendants(matching: .any).firstMatch /* TODO: refine selector */'



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
    name = q(step.get("name") or f"step {idx}")
    timeout = float(step.get("timeout", 8))
    selector = step.get("selector") if isinstance(step.get("selector"), dict) else {}
    el = selector_expr(selector)
    lines = [f'        // {idx}. {name} ({typ})']
    if typ == "wait":
        seconds = int(float(step.get("seconds", timeout)))
        return lines + [f"        sleep({seconds})"]
    if typ == "scroll":
        direction = str(step.get("direction", "up"))
        count = int(step.get("count", 1))
        method = {"up": "swipeUp", "down": "swipeDown", "left": "swipeLeft", "right": "swipeRight"}.get(direction, "swipeUp")
        lines += [f"        for _ in 0..<{count} {{ app.{method}() }}"]
        if should_attach_screenshot(step, typ):
            lines.append(screenshot_call(idx, typ, name))
        return lines
    if typ == "tap":
        lines += [
            f"        let e{idx} = {el}",
            f'        XCTAssertTrue(e{idx}.waitForExistence(timeout: {timeout}), "missing element for {name}")',
            f"        e{idx}.tap()",
        ]
        if should_attach_screenshot(step, typ):
            lines.append(screenshot_call(idx, typ, name))
        return lines
    if typ == "tap_if_present":
        lines += [
            f"        let e{idx} = {el}",
            f"        if e{idx}.waitForExistence(timeout: {timeout}) {{ e{idx}.tap(); sleep(1) }}",
        ]
        if should_attach_screenshot(step, typ):
            lines.append(screenshot_call(idx, typ, name))
        return lines
    if typ == "input":
        text = q(step.get("text", ""))
        lines += [
            f"        let e{idx} = {el}",
            f'        XCTAssertTrue(e{idx}.waitForExistence(timeout: {timeout}), "missing input for {name}")',
            f"        e{idx}.tap()",
            f'        e{idx}.typeText("{text}")',
        ]
        if should_attach_screenshot(step, typ):
            lines.append(screenshot_call(idx, typ, name))
        return lines
    if typ in {"assert_exists", "assert_text"}:
        lines += [
            f"        let e{idx} = {el}",
            f'        XCTAssertTrue(e{idx}.waitForExistence(timeout: {timeout}), "assert failed for {name}")',
        ]
        if should_attach_screenshot(step, typ):
            lines.append(screenshot_call(idx, typ, name))
        return lines
    return lines + ["        // unsupported step skipped by generator"]


def generate_swift(flow: dict[str, Any], app_config: dict[str, Any], intent: dict[str, Any]) -> str:
    test_name = swift_identifier(str(flow.get("name") or intent.get("goal") or "ProbeFlow"))
    target_bundle = q(app_config.get("targetBundleId") or app_config.get("target_bundle_id") or flow.get("targetBundleId") or "")
    # If target app bundle is absent, use runner app launch. External runner flows should set targetBundleId, e.g. com.example.app.
    launch_line = f'        let app = XCUIApplication(bundleIdentifier: "{target_bundle}")' if target_bundle else "        let app = XCUIApplication()"
    lines = [
        "import XCTest",
        "",
        "// Auto-generated by AutoMind ios_probe_flow_materialize.py",
        "// Review before copying into an external runner or target app project.",
        f"// Goal: {q(intent.get('goal') or '')}",
        f"// Sources: {q(', '.join(intent.get('sources') or []))}",
        "",
        "final class AutoMindProbeFlowGeneratedTests: XCTestCase {",
        "    func attachScreenshot(_ name: String) {",
        "        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())",
        "        attachment.name = name",
        "        attachment.lifetime = .keepAlways",
        "        add(attachment)",
        "    }",
        "",
        "    override func setUp() {",
        "        super.setUp()",
        "        continueAfterFailure = false",
        "    }",
        "",
        f"    func test_{test_name}() throws {{",
        launch_line,
        "        app.launch()",
    ]
    for idx, step in enumerate(intent.get("steps") or [], 1):
        lines.extend(swift_for_step(step, idx))
    lines += [
        "        XCTAssertTrue(app.state == .runningForeground || app.state == .runningBackground, \"target app should remain running\")",
        "    }",
        "}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_code")
    parser.add_argument("iteration", nargs="?", type=int, default=1)
    parser.add_argument("--flow", default="probe-flow.ios.json")
    parser.add_argument("--target-bundle-id", help="Target app bundle id driven by external XCUITest runner, e.g. com.example.app")
    args = parser.parse_args()

    task_dir = TASKS_DIR / args.task_code
    iter_dir = task_dir / "logs" / f"iter-{args.iteration}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    flow_path = pathlib.Path(args.flow)
    if not flow_path.is_absolute():
        candidate = task_dir / flow_path
        flow_path = candidate if candidate.exists() else ((WORKSPACE_ROOT / flow_path).resolve() if (WORKSPACE_ROOT / flow_path).exists() else ROOT / flow_path)

    if not flow_path.exists():
        result = {"result": "blocked", "summary": f"Flow not found: {flow_path}", "errors": ["missing_flow"]}
        write_json(iter_dir / "ios-probe-flow-materialize-summary.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    flow = read_json(flow_path)
    app_config, intent = normalize_flow(flow)
    if args.target_bundle_id:
        app_config["targetBundleId"] = args.target_bundle_id
    errors = validate(intent)
    if errors:
        result = {"result": "blocked", "summary": "Cannot materialize invalid iOS probe-flow intent", "errors": errors}
        write_json(iter_dir / "ios-probe-flow-materialize-summary.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    swift = generate_swift(flow, app_config, intent)
    swift_path = iter_dir / "GeneratedProbeFlowIntentTests.swift"
    swift_path.write_text(swift)
    result = {
        "result": "pass",
        "summary": "iOS probe-flow action intent materialized to Swift XCUITest draft with screenshot attachments for key actions",
        "flow": str(flow_path),
        "swift": f"logs/iter-{args.iteration}/GeneratedProbeFlowIntentTests.swift",
        "stepCount": len(intent.get("steps") or []),
        "screenshotAttachments": True,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(iter_dir / "ios-probe-flow-materialize-summary.json", result)
    (iter_dir / "commands-materialize.md").write_text(
        "# Commands\n\n```bash\n"
        f"./automind.sh ios-probe-flow-materialize {args.task_code} {args.iteration} --flow {args.flow}"
        + (f" --target-bundle-id {args.target_bundle_id}" if args.target_bundle_id else "")
        + "\n```\n"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
