#!/usr/bin/env python3
"""Run iOS XCUITest as a CodeMind evaluator.

This is the first reusable iOS adapter. It intentionally focuses on the
validated P0 path: xcodebuild test on a physical iPhone, with xcodebuild logs
and .xcresult as evidence. Screenshot is not part of this runner yet.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
from typing import Any

from automind_paths import RUNTIME_ROOT, TASKS_DIR, WORKSPACE_ROOT
from state_files import read_runtime_state, write_runtime_state

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from failure_classifier import classify as classify_failure

ROOT = RUNTIME_ROOT

# nextAction values that mean the run could not complete on its own and needs a
# human/replan decision rather than another Generator code-repair pass.
_BLOCKED_NEXT_ACTIONS = {"ask_user", "replan"}


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(errors="replace")
    except FileNotFoundError:
        return ""


def write_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def load_task_state(task_dir: pathlib.Path) -> dict[str, Any]:
    return read_runtime_state(task_dir)


def extract_config(task_dir: pathlib.Path, args: argparse.Namespace) -> dict[str, str]:
    state = load_task_state(task_dir)
    ios = state.get("iosApp") or state.get("iosDevice") or {}

    config = {
        "project_path": args.project_path or ios.get("projectPath") or ios.get("project_path") or "",
        "workspace_path": args.workspace_path or ios.get("workspacePath") or ios.get("workspace_path") or "",
        "scheme": args.scheme or ios.get("scheme") or "",
        "device_id": args.device_id or ios.get("xcodebuildDeviceId") or ios.get("xcodebuild_device_id") or ios.get("deviceId") or ios.get("device_id") or "",
        "team": args.team or ios.get("team") or "",
        "bundle_id": args.bundle_id or ios.get("bundleId") or ios.get("bundle_id") or "",
        "target_bundle_id": args.target_bundle_id or ios.get("targetBundleId") or ios.get("target_bundle_id") or "",
        "configuration": args.configuration or ios.get("configuration") or "Debug",
    }
    return {k: str(v) for k, v in config.items() if v is not None}


# Test methods that only prove the app launched (launch-smoke placeholders).
# A run that executed ONLY these is not proof of a product journey.
# Includes both the external UI runner's built-in placeholder and common
# project-native boilerplate (Xcode template testExample, basic launch tests).
PLACEHOLDER_TEST_METHODS = {
    "testExternalTargetLaunches",
    "testExample",
    "testLaunch",
    "testLaunchPerformance",
    "testSmoke",
}

# Substring patterns that strongly suggest a placeholder / template test method
# rather than a business-journey test. Used when exact name match misses.
PLACEHOLDER_NAME_PATTERNS = (
    "testExample",
    "testLaunch",
    "testSmoke",
    "testTemplate",
    "testPlaceholder",
    "testDefault",
    "testDemo",
    "testSample",
)


def _is_placeholder_method(method_name: str) -> bool:
    """Heuristic: does this test method look like a placeholder / template?

    Checks exact match first, then pattern prefix match. Conservative —
    only returns True for names that clearly look like boilerplate.
    """
    base = _test_method_base(method_name)
    if base in PLACEHOLDER_TEST_METHODS:
        return True
    lower = base.lower()
    for pattern in PLACEHOLDER_NAME_PATTERNS:
        if lower.startswith(pattern.lower()):
            return True
    return False


def _test_method_base(name: str) -> str:
    """`testFoo()` / `Suite/testFoo()` -> `testFoo`."""
    base = str(name or "").split("(", 1)[0].strip()
    if "/" in base:
        base = base.rsplit("/", 1)[-1]
    return base


def extract_executed_tests(xcresult_path: pathlib.Path) -> list[dict[str, str]]:
    """Read the test methods that actually executed from a .xcresult bundle.

    Uses `xcrun xcresulttool get test-results tests` (Xcode 16+/26). Returns a
    flat list of executed Test Case nodes ({name,id,result}); best-effort, [] on
    any failure so a missing/legacy tool never turns a real pass into a fail.
    """
    if not xcresult_path.exists():
        return []
    try:
        proc = subprocess.run(
            ["xcrun", "xcresulttool", "get", "test-results", "tests", "--path", str(xcresult_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        data = json.loads(proc.stdout)
    except Exception:
        return []
    out: list[dict[str, str]] = []

    def _walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("nodeType") == "Test Case":
            out.append({
                "name": str(node.get("name", "")),
                "id": str(node.get("nodeIdentifier") or node.get("nodeIdentifierURL") or ""),
                "result": str(node.get("result", "")),
            })
        for child in node.get("children") or []:
            _walk(child)

    for top in data.get("testNodes") or []:
        _walk(top)
    return out


def detect_business_journey_expected(task_dir: pathlib.Path, iteration: int) -> dict[str, Any]:
    """Did this round expect a product journey beyond a bare launch-smoke?

    Reads the probe-flow intent summary the probe-flow runner writes before it
    invokes this runner. A non-empty `intent.steps` means the task encoded a
    real product journey, so a launch-smoke-only xcresult is not enough.

    Conservative: when no intent summary exists (e.g. a direct project-target
    run), returns expected=False so a legitimate launch-only pass is preserved.
    """
    summary_path = task_dir / "logs" / f"iter-{iteration}" / "ios-action-intent-summary.json"
    if not summary_path.exists():
        return {"expected": False, "stepCount": 0, "goal": ""}
    try:
        data = json.loads(summary_path.read_text())
    except Exception:
        return {"expected": False, "stepCount": 0, "goal": ""}
    intent = data.get("intent") if isinstance(data.get("intent"), dict) else {}
    steps = intent.get("steps") if isinstance(intent.get("steps"), list) else []
    return {
        "expected": len(steps) > 0,
        "stepCount": len(steps),
        "goal": str(intent.get("goal") or "")[:200],
    }


def classify_detailed(exit_code: int, log: str):
    """Return (result, Classification) using the shared classifier.

    `result` is the runner-level pass/fail/blocked column; the Classification
    carries category/nextAction/reason/askUserQuestion from the central
    taxonomy so the runner does not re-invent iOS wording.
    """
    c = classify_failure("ios", "test", log, exit_code=exit_code)
    next_action = c.nextAction
    result = "blocked" if next_action in _BLOCKED_NEXT_ACTIONS else "fail"
    return result, c


def classify(exit_code: int, log: str) -> tuple[str, str, str, str]:
    """Return result, nextAction, category, reason.

    Delegates to the shared CodeMind failure classifier so the iOS XCUITest
    runner stays consistent with the central iOS/external-runner taxonomy
    (P0-2/P0-6). In particular, this corrects the PoC-disproven assumption that
    `test-without-building` hits a Root-install device blocker: on retail
    devices the real dead end is the missing IDE-side XCTestManager channel, so
    these are now classified as `external_runner_*` categories with the right
    nextAction (retry_generator / replan / ask_user) instead of being collapsed
    into `mobile_device_unavailable`.
    """
    if exit_code == 0:
        return (
            "pass",
            "finish",
            "",
            "iOS XCUITest passed: xcodebuild exit code 0; testing succeeded; UI automation/accessibility evidence captured.",
        )
    result, c = classify_detailed(exit_code, log)
    return result, c.nextAction, c.category, c.reason


def evaluate_pass_substance(
    task_dir: pathlib.Path,
    iteration: int,
    xcresult_path: pathlib.Path,
) -> dict[str, Any] | None:
    """Guard against a hollow pass: xcodebuild exit 0 but no business journey ran.

    Multi-level detection (deterministic code handles clear cases, ambiguous
    cases are surfaced as needsModelReview so the next-round model decides):

      Level 1 — NO tests executed when a journey was expected → hollow pass.
      Level 2 — ALL executed tests are placeholders (exact match + pattern) →
                hollow pass (covers external runner and project-native templates).
      Level 3 — executed test count < journey steps and names don't look like
                business tests → ambiguous, mark needsModelReview.
      Level 0 — substantive pass (real business test methods ran) → None.

    Returns a downgrade payload when the pass is hollow or suspicious, with:
      - category: hollow_pass
      - confidence: "high" / "medium" / "low"
      - triageSource / needsModelReview: routes to model for ambiguous cases
      - observed: concrete facts (executed test names, expected step count, etc.)
      - recoveryAction: hint for the next round
      - journey: the expected journey metadata

    Returns None when the pass is clearly substantive or cannot be judged
    (e.g. no journey expected, tool unavailable on a non-probe-flow run).
    """
    journey = detect_business_journey_expected(task_dir, iteration)
    if not journey.get("expected"):
        return None

    executed = extract_executed_tests(xcresult_path)
    bases = sorted({_test_method_base(t.get("name", "")) for t in executed})
    placeholder_count = sum(1 for name in bases if _is_placeholder_method(name))
    non_placeholder_count = len(bases) - placeholder_count

    def _payload(
        confidence: str,
        reason: str,
        recovery_action: str,
        *,
        needs_model_review: bool = True,
        category: str = "hollow_pass",
    ) -> dict[str, Any]:
        return {
            "category": category,
            "confidence": confidence,
            "reason": reason,
            "recoveryAction": recovery_action,
            "journey": journey,
            "executedTests": [t.get("name", "") for t in executed[:20]],
            "executedCount": len(executed),
            "placeholderCount": placeholder_count,
            "nonPlaceholderCount": non_placeholder_count,
            "needsModelReview": needs_model_review,
            "triageSource": "requires_model_review" if needs_model_review else "code_deterministic",
        }

    # Level 1: zero tests executed when a journey was expected.
    if not executed:
        return _payload(
            confidence="high",
            reason=(
                "xcodebuild exit code 0 but no test methods could be read from "
                f"{rel_path(task_dir, xcresult_path)}; this round expected a product journey with "
                f"{journey['stepCount']} step(s), so a bare exit-code pass is not trusted."
            ),
            recovery_action=(
                "Verify that the runner scheme/target includes the task's business XCUITest "
                "(from probe-flow.ios.json). Check xcodebuild log for test-target compilation errors, "
                "wrong scheme, or skipped test plan. Do not rely on exit code 0 alone."
            ),
            needs_model_review=True,
            category="hollow_pass",
        )

    # Level 2: ALL executed methods are placeholders → clearly hollow.
    if non_placeholder_count == 0 and len(bases) > 0:
        return _payload(
            confidence="high",
            reason=(
                "Hollow pass: xcodebuild exit code 0 but all "
                f"{len(bases)} executed test method(s) were launch-smoke placeholders "
                f"({sorted(bases)[:5]}) while this round expected a product journey of "
                f"{journey['stepCount']} step(s) (goal: {journey['goal'] or 'see probe-flow.ios.json'}). "
                "The task's business XCUITest was never compiled into / run by the executed runner scheme."
            ),
            recovery_action=(
                "Assemble the task's business XCUITest (the materialized GeneratedProbeFlowIntentTests.swift / "
                "the journey in probe-flow.ios.json) into the runner project/scheme that xcodebuild actually runs, "
                "so the executed xcresult contains the business test method(s) and per-step evidence. "
                "Common causes: wrong scheme selected, test target not added to plan, Generated*.swift not compiled."
            ),
            needs_model_review=True,
            category="hollow_pass",
        )

    # Level 3: mostly placeholders or very few non-placeholder methods relative
    # to journey steps → suspicious, let the model judge.
    journey_step_count = int(journey.get("stepCount") or 0)
    if journey_step_count > 1 and non_placeholder_count <= 1:
        return _payload(
            confidence="medium",
            reason=(
                f"Suspicious pass: expected a product journey of {journey_step_count} step(s) but only "
                f"{non_placeholder_count} non-placeholder test method(s) executed "
                f"(placeholders: {placeholder_count}, total: {len(bases)}). "
                "This could be a hollow pass or a single end-to-end test method — "
                "needs human/model judgment on whether the executed tests cover the journey."
            ),
            recovery_action=(
                "Re-read the xcresult test list and the probe-flow intent. If the non-placeholder test "
                "method is a single end-to-end test that covers all journey steps, the pass is substantive. "
                "If it is an unrelated or minimal test, the pass is hollow and the runner assembly needs fixing."
            ),
            needs_model_review=True,
            category="hollow_pass",
        )

    # Level 0: substantive — non-placeholder methods ran and the count looks reasonable.
    return None


def rel_path(task_dir: pathlib.Path, path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(task_dir))
    except ValueError:
        return str(path)


def build_ask_user_question(category: str, reason: str, config: dict[str, str]) -> dict[str, Any] | None:
    if category == "permission_blocked" and "signing" in reason.lower():
        return {
            "question": "iOS XCUITest is blocked by signing/provisioning. Which signing path should CodeMind use next?",
            "reason": reason,
            "options": [
                {"id": "configure_signing_and_retry", "label": "Configure signing and retry", "impact": "Use valid Team/profile/certificate, then rerun real-device verification.", "requiresConfirmation": True},
                {"id": "use_simulator_first", "label": "Use simulator first", "impact": "Collect simulator evidence while real-device signing is fixed; real-device coverage remains unresolved.", "requiresConfirmation": False},
                {"id": "replan_verification", "label": "Replan verification", "impact": "Revise TestCases/Plan for a runnable target.", "requiresConfirmation": False},
            ],
            "recommended": "configure_signing_and_retry",
            "deviceId": config.get("device_id", ""),
            "defaultAction": "ask_user",
        }
    if category == "permission_blocked":
        return {
            "question": "iOS XCUITest is blocked by device permission/readiness. Can the device be unlocked, trusted, and allowed for UI Automation?",
            "reason": reason,
            "options": [
                {"id": "fix_device_permission_and_retry", "label": "Fix device permission and retry", "impact": "Enable Developer Mode/UI Automation, unlock/trust the device, then continue real-device verification.", "requiresConfirmation": False},
                {"id": "use_simulator_first", "label": "Use simulator first", "impact": "Collect simulator evidence while real-device permission is fixed.", "requiresConfirmation": False},
                {"id": "replan_verification", "label": "Replan verification", "impact": "Revise the verification target or runner.", "requiresConfirmation": False},
            ],
            "recommended": "fix_device_permission_and_retry",
            "deviceId": config.get("device_id", ""),
            "defaultAction": "ask_user",
        }
    if category == "mobile_device_unavailable":
        return {
            "question": "iOS XCUITest cannot find/use the selected device. Should CodeMind retry after device discovery is fixed or switch target?",
            "reason": reason,
            "options": [
                {"id": "fix_destination_and_retry", "label": "Fix device destination and retry", "impact": "Connect/unlock/trust the iPhone, verify the Xcode destination id, then continue real-device verification.", "requiresConfirmation": False},
                {"id": "switch_to_detected_device", "label": "Use detected device id", "impact": "Update runtime-state iosApp.xcodebuildDeviceId/coreDeviceId when a different connected device is discovered.", "requiresConfirmation": False},
                {"id": "use_simulator_first", "label": "Use simulator first", "impact": "Collect simulator evidence; real-device coverage remains unresolved unless later approved.", "requiresConfirmation": False},
            ],
            "recommended": "fix_destination_and_retry",
            "deviceId": config.get("device_id", ""),
            "defaultAction": "ask_user",
        }
    return None


def extract_summary(log: str) -> str:
    interesting = []
    keys = [
        "test case",
        "test suite",
        "passed",
        "failed",
        "error:",
        "signing",
        "provisioning",
        "developer mode",
        "locked",
        "testing started",
        "testing failed",
        "testing succeeded",
        "automation mode",
        "running tests",
    ]
    for line in log.splitlines():
        if any(k in line.lower() for k in keys):
            interesting.append(line)
    return "\n".join(interesting[-260:])


def append_validation(task_dir: pathlib.Path, iteration: int, config: dict[str, str], result: str, category: str, reason: str) -> None:
    validation = task_dir / "Validation.md"
    existing = read_text(validation)
    lines = [
        f"\n### Iteration {iteration} - iOS XCUITest evaluator",
        f"- Time: {now()}",
        f"- Environment: project=`{config.get('project_path') or config.get('workspace_path')}`; scheme=`{config.get('scheme')}`; xcodebuild device id=`{config.get('device_id')}`; team=`{config.get('team')}`; bundle id=`{config.get('bundle_id')}`.",
        "- Preconditions: Developer Mode enabled; developer profile trusted; UI Automation enabled; iPhone unlocked and screen-on.",
        "- Commands: ",
        "  ```bash",
        "  xcodebuild test ...",
        "  ```",
        f"- Result: {result.upper()}.{reason}",
        f"- Failure category: {category or 'none'}.",
        "- Evidence: ",
        f"  - `logs/iter-{iteration}/xcodebuild-ui-test.log`",
        f"  - `logs/iter-{iteration}/evaluator.log`",
        f"  - `logs/iter-{iteration}/env.json`",
        f"  - `logs/iter-{iteration}/commands.md`",
        f"  - `logs/iter-{iteration}/test-summary.txt`",
        f"  - `logs/iter-{iteration}/TestResults.xcresult`",
        "- Reusable findings: XCUITest is the P0 path for iOS device UI/accessibility evidence, preferred over unstable screenshot backends.",
        "- Avoid repeating: A `Root install style is not supported` line on a retail device is a runner-delivery issue, not a device-absence blocker; switch to build-for-testing + devicectl install + test-without-building instead of asking the user to fix the device. `channel canceled for XCTestManager_IDEInterface` / `Exiting due to IDE disconnection` means the chosen runner has no IDE-side helper (e.g. pymobiledevice3 dvt xcuitest cannot drive an arbitrary runner) — replan onto WebDriverAgent/go-ios or the project test target rather than retrying the same runner.",
    ]
    validation.write_text((existing.rstrip() + "\n" + "\n".join(lines) + "\n").lstrip())


def resolve_signing_plan(config: dict[str, str], destination_type: str) -> dict[str, Any]:
    """Compute the concrete signing strategy this build should use.

    Reuses ios_signing_preflight as the single source of truth: detect the
    Xcode Apple ID login + managed Teams, scan codesigning identities and all
    readable provisioning profiles, then run the shared `build_signing_plan`
    decision ladder (simulator_no_sign -> manual_reuse -> automatic -> blocked).

    This replaces the old behavior of unconditionally forcing
    `CODE_SIGN_STYLE=Automatic`, which fought projects that ship Manual profiles
    and silently failed when no Apple ID was signed in / managing the Team.

    Mutates config['team'] when no Team was configured but one can be derived.
    Best-effort: on any failure returns a conservative Automatic plan so the
    build still attempts (matching prior behavior) rather than hard-blocking.
    """
    try:
        import ios_signing_preflight as sp  # local import; same SCRIPT_DIR on sys.path

        identities = sp.list_identities()
        accounts = sp.detect_xcode_accounts()
        roots = [
            pathlib.Path.cwd(),
            pathlib.Path.home() / "Library" / "MobileDevice" / "Provisioning Profiles",
            # Xcode 16/26 also persist managed profiles here.
            pathlib.Path.home() / "Library" / "Developer" / "Xcode" / "UserData" / "Provisioning Profiles",
        ]
        device_id = config.get("device_id") or None
        profiles = sp.collect_all_profiles(roots, device_id)
        usable = [p for p in profiles if not p.expired and (p.includes_device is not False)]
        xcode = sp.discover_xcode_signing([pathlib.Path.cwd()])

        team = config.get("team") or ""
        if not team:
            teams = sp.recommend_team(identities, xcode)
            if teams:
                team = teams[0]
                config["team"] = team

        plan = sp.build_signing_plan(
            destination_type=destination_type,
            identities=identities,
            accounts=accounts,
            usable_profiles=usable,
            xcode=xcode,
            team=team,
            target_team=team,
            bundle_id=config.get("bundle_id", ""),
        )
        plan["attempted"] = True
        return plan
    except Exception as exc:  # never let signing discovery break the build attempt
        clear_hardcoded = [] if destination_type == "simulator" else [
            # Clear any pbxproj-hardcoded Manual profile/identity so the
            # Automatic fallback does not hit "conflicting provisioning settings".
            "PROVISIONING_PROFILE_SPECIFIER=",
            "PROVISIONING_PROFILE=",
            "CODE_SIGN_IDENTITY=Apple Development",
        ]
        return {
            "attempted": True,
            "strategy": "automatic_fallback",
            "error": repr(exc),
            "askUser": False,
            "buildSettings": ["CODE_SIGNING_ALLOWED=YES", "CODE_SIGN_STYLE=Automatic"] + clear_hardcoded,
            "extraFlags": ["-allowProvisioningUpdates"] if destination_type != "simulator" else [],
            "summary": f"Signing preflight failed ({exc!r}); falling back to Automatic signing attempt.",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run iOS XCUITest evaluator")
    parser.add_argument("task_code")
    parser.add_argument("iteration", nargs="?", type=int, default=1)
    parser.add_argument("--project-path")
    parser.add_argument("--workspace-path")
    parser.add_argument("--scheme")
    parser.add_argument("--device-id")
    parser.add_argument("--team")
    parser.add_argument("--bundle-id")
    parser.add_argument(
        "--target-bundle-id",
        help=(
            "Bundle id of the app the UI test should drive (the app under test, "
            "e.g. com.example.app). Injected into the on-device test runner process as "
            "AUTOMIND_TARGET_BUNDLE_ID via the TEST_RUNNER_ prefix so "
            "XCUIApplication(bundleIdentifier:) opens the right app instead of "
            "the default demo. A plain shell env var is NOT inherited by the "
            "XCUITest process, so this must go through xcodebuild."
        ),
    )
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--allow-provisioning-updates", action="store_true", default=True)
    parser.add_argument(
        "--destination-type",
        choices=["device", "simulator"],
        default="device",
        help="Run target. 'simulator' skips code signing entirely (CODE_SIGNING_ALLOWED=NO); 'device' resolves a signing strategy from existing material / Apple ID.",
    )
    args = parser.parse_args()

    task_dir = TASKS_DIR / args.task_code
    log_dir = task_dir / "logs" / f"iter-{args.iteration}"
    log_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)

    config = extract_config(task_dir, args)
    missing = []
    if not (config.get("project_path") or config.get("workspace_path")):
        missing.append("project_path or workspace_path")
    for key in ["scheme", "device_id"]:
        if not config.get(key):
            missing.append(key)
    if missing:
        evaluation = {
            "iteration": args.iteration,
            "result": "blocked",
            "summary": f"Missing iOS XCUITest config: {', '.join(missing)}",
            "failedChecks": [
                {
                    "name": "ios-xcuitest config",
                    "category": "needs_replan",
                    "reason": f"Missing config: {', '.join(missing)}",
                }
            ],
            "evidence": [],
            "nextAction": "replan",
        }
        write_json(task_dir / "evaluation.json", evaluation)
        return 2

    # Resolve a concrete signing strategy from existing material + Apple ID
    # login, instead of blindly forcing Automatic. This both reuses Manual
    # profiles offline and detects when signing genuinely cannot proceed.
    signing_plan = resolve_signing_plan(config, args.destination_type)

    # If signing cannot proceed (no usable material AND Automatic not possible
    # because no Apple ID manages the Team), do not burn a doomed build — route
    # straight to ask_user with a precise, actionable explanation.
    if signing_plan.get("askUser"):
        reason = signing_plan.get("summary", "iOS signing is blocked.")
        category = signing_plan.get("category", "signing_material_blocked")
        evaluation = {
            "iteration": args.iteration,
            "result": "blocked",
            "summary": reason,
            "failedChecks": [
                {
                    "name": "ios signing preflight",
                    "category": category,
                    "reason": reason,
                    "evidence": [f"logs/iter-{args.iteration}/signing-plan.json"],
                }
            ],
            "evidence": [{"type": "other", "note": "signing-plan", "path": f"logs/iter-{args.iteration}/signing-plan.json"}],
            "nextAction": "ask_user",
            "askUserQuestion": build_ask_user_question("permission_blocked", "signing: " + reason, config),
        }
        write_json(log_dir / "signing-plan.json", signing_plan)
        write_json(task_dir / "evaluation.json", evaluation)
        state = load_task_state(task_dir)
        state.update({"taskId": args.task_code, "taskType": "ios", "status": "human_input_pending", "iteration": args.iteration, "nextAction": "ask_user"})
        write_runtime_state(task_dir, state)
        print(json.dumps(evaluation, ensure_ascii=False, indent=2))
        return 1

    write_json(log_dir / "signing-plan.json", signing_plan)

    derived = log_dir / "DerivedData"
    result_bundle = log_dir / "TestResults.xcresult"
    cmd = ["xcodebuild", "test"]
    if config.get("project_path"):
        cmd += ["-project", config["project_path"]]
    else:
        cmd += ["-workspace", config["workspace_path"]]
    cmd += [
        "-scheme",
        config["scheme"],
        "-configuration",
        config.get("configuration", "Debug"),
        "-destination",
        f"id={config['device_id']}",
        "-derivedDataPath",
        str(derived),
        "-resultBundlePath",
        str(result_bundle),
    ]
    # Extra xcodebuild flags from the signing plan (e.g. -allowProvisioningUpdates
    # only for the automatic strategy, never for manual_reuse / simulator).
    for flag in signing_plan.get("extraFlags") or []:
        if flag not in cmd:
            cmd.append(flag)
    if config.get("bundle_id"):
        cmd.append(f"PRODUCT_BUNDLE_IDENTIFIER={config['bundle_id']}")
    # The app-under-test bundle id must reach the on-device XCUITest runner
    # process. A plain shell env var is NOT inherited by that process; xcodebuild
    # forwards variables prefixed with TEST_RUNNER_ into the runner with the
    # prefix stripped. So TEST_RUNNER_AUTOMIND_TARGET_BUNDLE_ID arrives as
    # AUTOMIND_TARGET_BUNDLE_ID, which the runner reads for
    # XCUIApplication(bundleIdentifier:).
    if config.get("target_bundle_id"):
        cmd.append(f"TEST_RUNNER_AUTOMIND_TARGET_BUNDLE_ID={config['target_bundle_id']}")
    # Signing build settings come from the resolved plan: simulator ->
    # CODE_SIGNING_ALLOWED=NO; manual_reuse -> DEVELOPMENT_TEAM + Manual;
    # automatic -> DEVELOPMENT_TEAM + Automatic. No more hardcoded Automatic.
    for setting in signing_plan.get("buildSettings") or []:
        cmd.append(setting)
    for spec in signing_plan.get("profileSpecifiers") or []:
        cmd.append(f"PROVISIONING_PROFILE_SPECIFIER={spec}")
        break  # xcodebuild takes one specifier; the plan lists them by preference

    (log_dir / "commands.md").write_text("# Commands\n\n```bash\n" + " \\\n  ".join(cmd) + "\n```\n")

    with (log_dir / "xcodebuild-ui-test.log").open("w") as out:
        proc = subprocess.run(cmd, cwd=WORKSPACE_ROOT, stdout=out, stderr=subprocess.STDOUT, text=True)
    exit_code = proc.returncode
    (log_dir / "exit-code.txt").write_text(str(exit_code) + "\n")
    shutil.copyfile(log_dir / "xcodebuild-ui-test.log", log_dir / "evaluator.log")

    log = read_text(log_dir / "xcodebuild-ui-test.log")
    if exit_code == 0:
        result, next_action, category, reason = classify(exit_code, log)
        classification = None
    else:
        result, classification = classify_detailed(exit_code, log)
        next_action = classification.nextAction
        category = classification.category
        reason = classification.reason
    (log_dir / "test-summary.txt").write_text(extract_summary(log))

    # Substance gate: a green xcodebuild (exit 0) that only ran a launch-smoke
    # placeholder while the round expected a product journey is a hollow pass.
    # Downgrade it to retry_generator and hand root-cause analysis to the model
    # via needsModelReview rather than statically prescribing a single fix.
    hollow_pass = None
    if result == "pass":
        hollow_pass = evaluate_pass_substance(task_dir, args.iteration, result_bundle)
        if hollow_pass is not None:
            result = "fail"
            next_action = "retry_generator"
            category = hollow_pass["category"]
            reason = hollow_pass["reason"]
            write_json(log_dir / "ios-pass-substance.json", hollow_pass)

    env = {
        "timestamp": now(),
        "cwd": str(WORKSPACE_ROOT),
        "os": platform.platform(),
        "tools": {name: shutil.which(name) or "missing" for name in ["xcodebuild", "xcrun", "xcodebuildmcp"]},
        "ios": config,
        "signingPlan": signing_plan,
        "resultBundle": f"logs/iter-{args.iteration}/TestResults.xcresult",
        "xcodebuildExitCode": exit_code,
    }
    write_json(log_dir / "env.json", env)

    failed = []
    if result != "pass":
        check = {
            "name": "xcodebuild physical XCUITest",
            "category": category,
            "reason": reason,
            "exitCode": exit_code,
            "evidence": [f"logs/iter-{args.iteration}/xcodebuild-ui-test.log"],
        }
        if classification is not None and classification.sameProblemKey:
            check["sameProblemKey"] = classification.sameProblemKey
        if hollow_pass is not None:
            # Hand this to the model: it was NOT classified by a log pattern, so
            # the next round must re-read the gap and decide the assembly fix,
            # not blindly retry the same xcodebuild invocation.
            check["triageSource"] = hollow_pass.get("triageSource", "requires_model_review")
            check["needsModelReview"] = hollow_pass.get("needsModelReview", True)
            check["recoveryAction"] = hollow_pass["recoveryAction"]
            check["sameProblemKey"] = "ios.xcuitest.hollow_pass"
            check["confidence"] = hollow_pass.get("confidence", "high")
            check["observed"] = {
                "expectedJourneySteps": hollow_pass["journey"].get("stepCount"),
                "executedTests": hollow_pass.get("executedTests", []),
                "executedCount": hollow_pass.get("executedCount", 0),
                "placeholderCount": hollow_pass.get("placeholderCount", 0),
                "nonPlaceholderCount": hollow_pass.get("nonPlaceholderCount", 0),
            }
            check["evidence"].append(f"logs/iter-{args.iteration}/ios-pass-substance.json")
            check["evidence"].append(f"logs/iter-{args.iteration}/TestResults.xcresult")
        failed.append(check)
    evaluation = {
        "iteration": args.iteration,
        "result": result,
        "summary": reason,
        "failedChecks": failed,
        "evidence": [
            {"type": "log", "path": f"logs/iter-{args.iteration}/xcodebuild-ui-test.log"},
            {"type": "log", "path": f"logs/iter-{args.iteration}/evaluator.log"},
            {"type": "other", "note": "env", "path": f"logs/iter-{args.iteration}/env.json"},
            {"type": "command", "path": f"logs/iter-{args.iteration}/commands.md"},
            {"type": "other", "note": "test-summary", "path": f"logs/iter-{args.iteration}/test-summary.txt"},
            {"type": "other", "note": "xcresult", "path": f"logs/iter-{args.iteration}/TestResults.xcresult"},
        ],
        "nextAction": next_action,
    }
    # Prefer the central classifier's ask_user payload (P0-2/P0-6); fall back to
    # the runner-local builder only when the central classifier did not supply
    # one. This keeps external_runner_* signing/capability blockers routed with
    # the corrected category and avoids re-asking with stale wording.
    if result == "blocked" and next_action == "ask_user":
        ask = (classification.askUserQuestion if classification is not None else None) \
            or build_ask_user_question(category, reason, config)
        if ask:
            evaluation["askUserQuestion"] = ask

    write_json(task_dir / "evaluation.json", evaluation)
    state = load_task_state(task_dir)
    state.update(
        {
            "taskId": args.task_code,
            "taskType": "ios",
            "status": "finished" if result == "pass" else ("human_input_pending" if next_action == "ask_user" else result),
            "iteration": args.iteration,
            "nextAction": next_action,
            "iosApp": {
                "projectPath": config.get("project_path", ""),
                "workspacePath": config.get("workspace_path", ""),
                "scheme": config.get("scheme", ""),
                "xcodebuildDeviceId": config.get("device_id", ""),
                "team": config.get("team", ""),
                "bundleId": config.get("bundle_id", ""),
                "configuration": config.get("configuration", "Debug"),
            },
        }
    )
    write_runtime_state(task_dir, state)
    append_validation(task_dir, args.iteration, config, result, category, reason)
    print(json.dumps(evaluation, ensure_ascii=False, indent=2))
    return 0 if result == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
