#!/usr/bin/env python3
"""Materialize iOS probe-flow action intent into a Swift XCUITest draft.

This is a bridge between protocol-level `testIntent.steps[]` and executable
XCUITest code. It does not modify the target app project. It writes a generated
Swift file into the CodeMind task log directory so a human/agent can review it,
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

# Interactive XCUIElement types worth surfacing as selector candidates when
# parsing a prior round's UI hierarchy dump. Static/decoration types are kept
# too (they carry the labels a predicate often matches on) but ranked lower.
_INTERACTIVE_TYPES = {"Button", "Cell", "Link", "MenuItem", "SegmentedControl", "Switch", "Slider", "TabBar"}
_LABELLED_TYPES = _INTERACTIVE_TYPES | {"StaticText", "Image", "Other"}
_HIERARCHY_TYPE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9]*)\b")
_HIERARCHY_LABEL_RE = re.compile(r"label:\s*'([^']*)'")
_HIERARCHY_IDENTIFIER_RE = re.compile(r"identifier:\s*'([^']*)'")


def parse_ui_hierarchy(text: str) -> list[dict[str, str]]:
    """Extract observed controls from an XCUITest debug-description dump.

    Lines look like ``Button, 0x..., {{x,y},{w,h}}, label: '暂停', identifier: 'id'``.
    Returns de-duplicated control descriptors with type/label/identifier so the
    next probe-flow round can narrow selectors from real on-screen controls
    instead of retrying a blind predicate.
    """
    controls: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in text.splitlines():
        type_match = _HIERARCHY_TYPE_RE.match(raw)
        if not type_match:
            continue
        el_type = type_match.group(1)
        if el_type not in _LABELLED_TYPES:
            continue
        label_match = _HIERARCHY_LABEL_RE.search(raw)
        id_match = _HIERARCHY_IDENTIFIER_RE.search(raw)
        label = (label_match.group(1) if label_match else "").strip()
        identifier = (id_match.group(1) if id_match else "").strip()
        if not label and not identifier:
            continue
        key = (el_type, label, identifier)
        if key in seen:
            continue
        seen.add(key)
        controls.append({"type": el_type, "label": label, "identifier": identifier})
    return controls


def collect_source_ui_map(task_dir: pathlib.Path, iteration: int, limit: int = 60) -> dict[str, Any]:
    """Scan recent iteration logs for UI hierarchy dumps and build a control map.

    Looks at the current and prior iterations (most recent first) for any text
    artifact whose name suggests a UI hierarchy / accessibility / debug
    description dump, parses observed controls, and ranks interactive ones first.
    Best-effort: returns an empty map when no dump is available.
    """
    logs_dir = task_dir / "logs"
    if not logs_dir.is_dir():
        return {}
    iter_dirs: list[tuple[int, pathlib.Path]] = []
    for child in logs_dir.iterdir():
        if not child.is_dir() or not child.name.startswith("iter-"):
            continue
        try:
            num = int(child.name.split("iter-", 1)[1])
        except ValueError:
            continue
        if num <= iteration:
            iter_dirs.append((num, child))
    iter_dirs.sort(key=lambda x: -x[0])

    controls: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    source_files: list[str] = []
    for _num, idir in iter_dirs:
        for path in sorted(idir.rglob("*.txt")):
            lower = path.name.lower()
            if not any(token in lower for token in ("hierarchy", "accessibility", "debug description", "debug-description", "snapshot")):
                # Also accept exported xcresult attachment txts whose human name
                # was hierarchy/debug-description (they keep uuid filenames), by
                # sniffing the first lines cheaply.
                try:
                    head = path.read_text(errors="ignore")[:400]
                except OSError:
                    continue
                if "label:" not in head and "Application," not in head and "Window" not in head:
                    continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            parsed = parse_ui_hierarchy(text)
            if not parsed:
                continue
            try:
                source_files.append(str(path.relative_to(task_dir)))
            except ValueError:
                source_files.append(str(path))
            for control in parsed:
                key = (control["type"], control["label"], control["identifier"])
                if key in seen:
                    continue
                seen.add(key)
                controls.append(control)
        if controls:
            # First iteration that yields controls is the freshest signal; stop.
            break
    if not controls:
        return {}
    controls.sort(key=lambda c: (0 if c["type"] in _INTERACTIVE_TYPES else 1, c["type"]))
    return {
        "schema": "automind.source_ui_map.v1",
        "sourceFiles": source_files,
        "controlCount": len(controls),
        "controls": controls[:limit],
    }


def derived_selector_candidates(ui_map: dict[str, Any], limit: int = 12) -> list[str]:
    """Turn observed controls into concrete XCUITest predicate candidates.

    Interactive controls with an identifier/label become precise predicates the
    next round can try; this is the hierarchy-derived narrowing that replaces a
    blind label-substring retry.
    """
    candidates: list[str] = []
    for control in ui_map.get("controls") or []:
        if not isinstance(control, dict):
            continue
        identifier = str(control.get("identifier") or "").strip()
        label = str(control.get("label") or "").strip()
        if identifier:
            cand = f"identifier == '{identifier}'"
        elif label:
            cand = f"label == '{label}'"
        else:
            continue
        if cand not in candidates:
            candidates.append(cand)
    return candidates[:limit]


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


def generate_swift(flow: dict[str, Any], app_config: dict[str, Any], intent: dict[str, Any], derived_candidates: list[str] | None = None) -> str:
    test_name = swift_identifier(str(flow.get("name") or intent.get("goal") or "ProbeFlow"))
    target_bundle = q(app_config.get("targetBundleId") or app_config.get("target_bundle_id") or flow.get("targetBundleId") or "")
    # If target app bundle is absent, use runner app launch. External runner flows should set targetBundleId, e.g. com.example.app.
    launch_line = f'        let app = XCUIApplication(bundleIdentifier: "{target_bundle}")' if target_bundle else "        let app = XCUIApplication()"
    header = [
        "import XCTest",
        "",
        "// Auto-generated by CodeMind ios_probe_flow_materialize.py",
        "// Review before copying into an external runner or target app project.",
        f"// Goal: {q(intent.get('goal') or '')}",
        f"// Sources: {q(', '.join(intent.get('sources') or []))}",
    ]
    if derived_candidates:
        header.append("// Hierarchy-derived selector candidates from the prior round (use these to narrow a failing selector):")
        for cand in derived_candidates:
            header.append(f"//   - {q(cand)}")
    lines = header + [
        "",
        "final class AutoMindProbeFlowGeneratedTests: XCTestCase {",
        "    func attachScreenshot(_ name: String) {",
        "        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())",
        "        attachment.name = name",
        "        attachment.lifetime = .keepAlways",
        "        add(attachment)",
        "    }",
        "",
        "    // System-alert classification keywords. Deterministic code handles clear",
        "    // cases (permission = allow, sensitive = block) and captures evidence",
        "    // for model review on ambiguous ones — the model decides the rest.",
        "    private let permissionAlertKeywords: [String] = [",
        '        "相机", "照片", "相册", "通讯录", "联系人", "位置", "定位",',
        '        "麦克风", "通知", "蓝牙", "追踪",',
        '        "Camera", "Photos", "Contacts", "Location",',
        '        "Microphone", "Notifications", "Bluetooth", "Tracking",',
        "    ]",
        "    private let sensitiveAlertKeywords: [String] = [",
        '        "登录", "注册", "支付", "购买", "订阅", "付费", "确认支付",',
        '        "删除", "卸载", "重置", "清空", "账号", "账户", "授权",',
        '        "Sign In", "Sign Up", "Log In", "Sign in with Apple",',
        '        "Pay", "Buy", "Purchase", "Subscribe", "Delete", "Reset", "Account", "Authorize",',
        "    ]",
        "    // Positive / allow buttons — used on permission and benign consent alerts.",
        "    private let positiveConsentButtons: [String] = [",
        '        "允许", "始终允许", "使用App期间允许", "仅使用期间允许", "允许一次",',
        '        "好", "好的", "同意", "继续", "确定", "下一步",',
        '        "Allow", "Allow Once", "Allow While Using App", "OK", "Agree", "Accept", "Continue",',
        "    ]",
        "    // Safe dismiss buttons — close the overlay without side effects.",
        "    private let safeDismissButtons: [String] = [",
        '        "取消", "以后", "稍后", "暂不", "关闭", "跳过", "不用了", "下次再说",',
        '        "知道了", "我知道了", "明白了",',
        '        "Cancel", "Later", "Not Now", "Close", "Skip", "Dismiss", "No Thanks",',
        "    ]",
        "",
        "    private func alertText(_ element: XCUIElement) -> String {",
        '        let staticTexts = element.staticTexts.allElementsBoundByIndex.compactMap { $0.label }',
        "        return staticTexts.joined(separator: \" \")",
        "    }",
        "",
        "    private func alertContains(_ element: XCUIElement, keywords: [String]) -> Bool {",
        "        let text = alertText(element).lowercased()",
        "        return keywords.contains { keyword in",
        "            text.localizedCaseInsensitiveContains(keyword)",
        "        }",
        "    }",
        "",
        "    private func tapFirstExistingButton(_ element: XCUIElement, labels: [String], screenshotPrefix: String) -> Bool {",
        "        let buttons = element.buttons",
        "        for label in labels {",
        "            let button = buttons[label]",
        "            if button.exists {",
        "                attachScreenshot(\"\\(screenshotPrefix)-\\(label.replacingOccurrences(of: \" \", with: \"-\"))\")",
        "                button.tap()",
        "                return true",
        "            }",
        "        }",
        "        return false",
        "    }",
        "",
        "    // Register an interruption monitor that auto-dismisses system alerts",
        "    // following the high-automation policy:",
        "    //   1. Sensitive alerts (login / payment / delete / account) — block, screenshot, let model review",
        "    //   2. Permission alerts (camera / photos / location / etc.) — auto-allow",
        "    //   3. Everything else — tap the most user-like button to dismiss (positive > dismiss)",
        "    // Returns the monitor token so the caller can remove it.",
        "    @discardableResult",
        "    private func installSafeAlertMonitor() -> NSObjectProtocol {",
        '        return addUIInterruptionMonitor(withDescription: "CodeMind system alert handler") { [weak self] element in',
        "            guard let self = self else { return false }",
        "            // 1. Sensitive alert? — block immediately, capture evidence for model review.",
        "            if self.alertContains(element, keywords: self.sensitiveAlertKeywords) {",
        "                self.attachScreenshot(\"system-alert-sensitive-blocked\")",
        "                return false",
        "            }",
        "            // 2. Permission alert? — auto-allow so verification can proceed.",
        "            if self.alertContains(element, keywords: self.permissionAlertKeywords) {",
        "                return self.tapFirstExistingButton(",
        "                    element,",
        "                    labels: self.positiveConsentButtons,",
        '                    screenshotPrefix: "system-alert-permission-allow"',
        "                )",
        "            }",
        "            // 3. Benign alert — tap the most likely user-tap button to dismiss.",
        "            //    Prefer positive/confirm (most common user choice for non-threatening",
        "            //    dialogs), fall back to dismiss/cancel.",
        "            if self.tapFirstExistingButton(",
        "                element,",
        "                labels: self.positiveConsentButtons,",
        '                screenshotPrefix: "system-alert-benign-positive"',
        "            ) { return true }",
        "            if self.tapFirstExistingButton(",
        "                element,",
        "                labels: self.safeDismissButtons,",
        '                screenshotPrefix: "system-alert-benign-dismiss"',
        "            ) { return true }",
        "            // 4. Unrecognized — block and screenshot for model review.",
        "            self.attachScreenshot(\"system-alert-unknown-model-review\")",
        "            return false",
        "        }",
        "    }",
        "",
        "    override func setUp() {",
        "        super.setUp()",
        "        continueAfterFailure = false",
        "        installSafeAlertMonitor()",
        "    }",
        "",
        f"    func test_{test_name}() throws {{",
        launch_line,
        "        app.launch()",
        "        // Nudge the app so a pending system interruption surfaces and the",
        "        // monitor above gets a chance to handle it before the journey runs.",
        "        app.tap()",
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

    # P1-A: feed the prior round's observed UI hierarchy back into this round so
    # a failing blind selector can be narrowed from real on-screen controls.
    ui_map = collect_source_ui_map(task_dir, int(args.iteration))
    derived_candidates = derived_selector_candidates(ui_map) if ui_map else []
    if ui_map:
        write_json(iter_dir / "source-ui-map.json", {
            **ui_map,
            "derivedSelectorCandidates": derived_candidates,
        })

    swift = generate_swift(flow, app_config, intent, derived_candidates)
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
    if ui_map:
        result["sourceUiMap"] = f"logs/iter-{args.iteration}/source-ui-map.json"
        result["derivedSelectorCandidates"] = derived_candidates
        result["observedControlCount"] = ui_map.get("controlCount", 0)
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
