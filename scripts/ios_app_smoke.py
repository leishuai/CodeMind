#!/usr/bin/env python3
"""Minimal iOS physical app smoke runner.

Launches an already-installed app and verifies app-alive evidence via
``devicectl`` process listing. Optional screenshot, display, and crash-hint
artifacts can add evidence without installing, uninstalling, or modifying
signing state.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from automind_paths import IOS_TOOLS_PY, RUNTIME_ROOT, TASKS_DIR, WORKSPACE_ROOT, venv_requirements_current
from state_files import write_runtime_state

ROOT = RUNTIME_ROOT
TASKS = TASKS_DIR
IOS_PY = IOS_TOOLS_PY


def runtime_timeout(env_var: str = "AUTOMIND_CMD_TIMEOUT", default: int = 43200) -> int:
    raw = os.environ.get(env_var) or os.environ.get("AUTOMIND_CMD_TIMEOUT") or str(default)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def run(cmd: list[str], timeout: int | None = None) -> dict[str, Any]:
    timeout = timeout if timeout is not None else runtime_timeout("AUTOMIND_IOS_APP_SMOKE_TIMEOUT")
    started = datetime.now().isoformat(timespec="seconds")
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        return {"cmd": cmd, "exitCode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "startedAt": started}
    except Exception as exc:
        return {"cmd": cmd, "exitCode": 124, "stdout": "", "stderr": repr(exc), "startedAt": started}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def ios_python_ready() -> tuple[bool, str]:
    if not IOS_PY.exists():
        return False, ".venv-ios-tools python missing"
    res = run([str(IOS_PY), "-c", "import pymobiledevice3"], timeout=20)
    if res["exitCode"] != 0:
        return False, "pymobiledevice3 missing from .venv-ios-tools"
    return True, "ready"


def run_ios_tools_auto_setup(log_dir: Path) -> tuple[int, str, dict[str, Any]]:
    cmd = [sys.executable, str(ROOT / "orchestrator" / "main.py"), "setup-automation-tools", "ios"]
    res = run(cmd, timeout=runtime_timeout("AUTOMIND_AUTOMATION_SETUP_TIMEOUT"))
    out = res.get("stdout", "") + res.get("stderr", "")
    write(log_dir / "ios-tools-auto-setup.log", out)
    write(log_dir / "ios-tools-auto-setup.exit-code.txt", str(res.get("exitCode")) + "\n")
    try:
        report = json.loads(out)
    except Exception:
        report = {}
    return int(res.get("exitCode", 1)), out, report


def build_ios_tools_ask(reason: str) -> dict[str, Any]:
    return {
        "question": "Optional iOS screenshot helper tools are still unavailable after CodeMind tried local helper setup. What should happen next?",
        "reason": reason + " CodeMind can auto-create .venv-ios-tools for low-risk Python helper packages, but pymobiledevice3 is still unavailable. This may require fixing network/proxy/Python/pip, or continuing without screenshot evidence.",
        "options": [
            {"id": "A", "label": "I will fix Python/pip/network and retry setup.", "impact": "Keeps screenshot capability.", "requiresConfirmation": False},
            {"id": "B", "label": "Continue without screenshot.", "impact": "Uses devicectl process/display/log evidence only.", "requiresConfirmation": False},
            {"id": "C", "label": "Stop", "impact": "Keep the task blocked if screenshot evidence is required.", "requiresConfirmation": False},
        ],
        "recommended": "A",
        "setupCommand": "automind setup-automation-tools ios",
        "retryCommand": "automind ios-app-smoke <task-code> --bundle-id <bundle-id> --device-id <core-device-id> --screenshot",
        "defaultAction": "stop",
    }


def ensure_task(task_code: str) -> Path:
    task_dir = TASKS / task_code
    task_dir.mkdir(parents=True, exist_ok=True)
    if not (task_dir / "Requirements.md").exists():
        (task_dir / "Requirements.md").write_text("# Requirements - iOS App Smoke\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — iOS app smoke\n- **AC-001**: Launch an already-installed iOS app and collect minimal app-alive evidence.\n  - Verification method: ios-app-smoke / TC-F01\n")
    if not (task_dir / "Plan.md").exists():
        (task_dir / "Plan.md").write_text(
            "# Plan\n\n"
            "Use devicectl to verify installed app metadata, launch the app, wait briefly, "
            "inspect the process list, and collect optional display/screenshot/crash-hint evidence. "
            "Do not install/uninstall.\n"
        )
    if not (task_dir / "Validation.md").exists():
        (task_dir / "Validation.md").write_text("# Validation\n")
    return task_dir


def maybe_screenshot(args: argparse.Namespace, log_dir: Path) -> dict[str, Any]:
    if not args.screenshot:
        return {"enabled": False, "ok": False, "skipped": True, "reason": "disabled"}
    output = log_dir / "ios-app-smoke-screenshot.png"
    ready, reason = ios_python_ready()
    if ready and not venv_requirements_current("ios"):
        ready, reason = False, "requirements changed since .venv-ios-tools was built (stale stamp)"
    auto_setup_attempted = False
    if not ready:
        if not getattr(args, "auto_setup_tools", False):
            return {
                "enabled": True,
                "ok": False,
                "skipped": True,
                "reason": reason,
                "category": "tool_missing",
                "autoSetupAttempted": False,
                "setupCommand": "automind setup-automation-tools ios",
            }
        auto_setup_attempted = True
        _setup_code, _setup_out, setup_report = run_ios_tools_auto_setup(log_dir)
        ready, reason_after = ios_python_ready()
        if not ready:
            return {
                "enabled": True,
                "ok": False,
                "skipped": True,
                "reason": reason_after or reason,
                "category": "tool_missing",
                "autoSetupAttempted": True,
                "setupCommand": "automind setup-automation-tools ios",
                "setupReport": setup_report,
            }
    device_id = args.traditional_device_id or args.device_id
    cmd = [str(IOS_PY), "-m", "pymobiledevice3", "developer", "dvt", "screenshot", "--tunnel", device_id, str(output)]
    res = run(cmd, timeout=runtime_timeout("AUTOMIND_IOS_APP_SMOKE_TIMEOUT"))
    write(log_dir / "screenshot.log", res.get("stdout", "") + res.get("stderr", ""))
    ok = res["exitCode"] == 0 and output.exists() and output.stat().st_size > 0
    return {
        "enabled": True,
        "ok": ok,
        "skipped": False,
        "path": str(output),
        "exitCode": res["exitCode"],
        "autoSetupAttempted": auto_setup_attempted,
        "reason": "ok" if ok else "screenshot failed or tunneld unavailable",
    }


def crash_hints(bundle_id: str, executable: str) -> list[str]:
    root = Path.home() / "Library" / "Logs" / "CrashReporter" / "MobileDevice"
    if not root.exists():
        return []
    patterns = []
    if executable:
        patterns.append(f"*{executable}*")
    patterns.append(f"*{bundle_id}*")
    hits: list[Path] = []
    for pattern in patterns:
        hits.extend(root.glob(f"**/{pattern}"))
    unique = {str(path): path for path in hits if path.is_file()}
    items = sorted(unique.values(), key=lambda path: path.stat().st_mtime, reverse=True)[:20]
    return [str(path) for path in items]


def classify(
    args: argparse.Namespace,
    app_lookup: dict[str, Any],
    launch: dict[str, Any],
    processes: dict[str, Any],
    display: dict[str, Any],
) -> tuple[str, str, str, list[dict[str, str]], dict[str, bool]]:
    failed: list[dict[str, str]] = []
    app_text = app_lookup.get("stdout", "") + app_lookup.get("stderr", "")
    launch_text = launch.get("stdout", "") + launch.get("stderr", "")
    proc_text = processes.get("stdout", "") + processes.get("stderr", "")
    display_text = display.get("stdout", "") + display.get("stderr", "")
    process_markers = [marker for marker in [args.executable, args.process_hint, args.bundle_id] if marker]
    checks = {
        "installedAppFound": args.bundle_id in app_text,
        "launchSucceeded": launch.get("exitCode") == 0 and "Launched application" in launch_text,
        "processAliveAfterWait": any(marker in proc_text for marker in process_markers) if process_markers else processes.get("exitCode") == 0,
        "displayActive": "backlight is on and active" in display_text,
    }
    if not checks["installedAppFound"]:
        failed.append({"name": "app.installed", "category": "install_failure", "detail": "app_not_installed", "reason": f"{args.bundle_id} not found in devicectl app list", "evidence": "logs/iter-N/app-lookup.txt"})
    if not checks["launchSucceeded"]:
        failed.append({"name": "app.launch", "category": "launch_failure", "detail": "devicectl_launch_failed", "reason": "devicectl launch did not report success", "evidence": "logs/iter-N/launch.txt"})
    if not checks["processAliveAfterWait"]:
        failed.append({"name": "app.process_alive", "category": "validation_failure", "detail": "process_not_observed", "reason": f"None of process markers found: {process_markers}", "evidence": "logs/iter-N/processes.txt"})
    if args.require_display and not checks["displayActive"]:
        failed.append({"name": "device.display", "category": "device_state_blocked", "detail": "display_not_active", "reason": "display/backlight did not report active", "evidence": "logs/iter-N/display.txt"})

    if failed:
        summary = "iOS app smoke failed: " + "; ".join(item["reason"] for item in failed)
        return "fail", "validation_failure", summary, failed, checks
    display_note = "display active" if checks["displayActive"] else "display state not asserted"
    return "pass", "ok", f"iOS app smoke passed: runtime launch evidence captured; {args.bundle_id} launched, process evidence observed, {display_note}.", [], checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_code")
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--device-id", required=True, help="CoreDevice id/name for devicectl")
    parser.add_argument("--traditional-device-id", default="", help="Traditional UDID for pymobiledevice3 screenshot tunnel")
    parser.add_argument("--executable", default="", help="Expected executable/process path marker, e.g. DemoApp")
    parser.add_argument("--process-hint", default="", help="Additional process-list marker")
    parser.add_argument("--wait", type=float, default=5.0)
    parser.add_argument("--extended", action="store_true", help="Collect optional display, screenshot, and crash-hint evidence")
    parser.add_argument("--screenshot", action="store_true", help="Capture an optional screenshot via pymobiledevice3/tunneld")
    parser.add_argument("--require-display", action="store_true", help="Fail if devicectl display/backlight state is not active")
    args = parser.parse_args()

    task_dir = ensure_task(args.task_code)
    log_dir = task_dir / "logs" / f"iter-{args.iteration}"
    log_dir.mkdir(parents=True, exist_ok=True)

    app_lookup = run(["xcrun", "devicectl", "device", "info", "apps", "--device", args.device_id], timeout=runtime_timeout("AUTOMIND_IOS_APP_SMOKE_TIMEOUT"))
    launch = run(["xcrun", "devicectl", "device", "process", "launch", "--device", args.device_id, args.bundle_id], timeout=runtime_timeout("AUTOMIND_IOS_APP_SMOKE_TIMEOUT"))
    time.sleep(max(args.wait, 0))
    processes = run(["xcrun", "devicectl", "device", "info", "processes", "--device", args.device_id], timeout=runtime_timeout("AUTOMIND_IOS_APP_SMOKE_TIMEOUT"))
    display_enabled = args.extended or args.require_display
    screenshot_enabled = args.screenshot or args.extended
    crash_hints_enabled = args.extended
    display = run(["xcrun", "devicectl", "device", "info", "displays", "--device", args.device_id], timeout=runtime_timeout("AUTOMIND_IOS_APP_SMOKE_TIMEOUT")) if display_enabled else {"cmd": ["xcrun", "devicectl", "device", "info", "displays", "--device", args.device_id], "exitCode": 0, "stdout": "", "stderr": "", "startedAt": datetime.now().isoformat(timespec="seconds"), "skipped": True}
    screenshot = maybe_screenshot(argparse.Namespace(**{**vars(args), "screenshot": screenshot_enabled, "auto_setup_tools": bool(args.screenshot)}), log_dir)
    crashes = crash_hints(args.bundle_id, args.executable) if crash_hints_enabled else []

    write(log_dir / "app-lookup.txt", app_lookup.get("stdout", "") + app_lookup.get("stderr", ""))
    write(log_dir / "launch.txt", launch.get("stdout", "") + launch.get("stderr", ""))
    write(log_dir / "processes.txt", processes.get("stdout", "") + processes.get("stderr", ""))
    write(log_dir / "display.txt", ("skipped; use --extended or --require-display\n" if display.get("skipped") else "") + display.get("stdout", "") + display.get("stderr", ""))
    write(log_dir / "crash-hints.json", json.dumps(crashes, ensure_ascii=False, indent=2) + "\n")
    command_items = [app_lookup, launch, processes] + ([] if display.get("skipped") else [display])
    write(log_dir / "commands.md", "# Commands\n\n```bash\n" + "\n".join(" ".join(item["cmd"]) for item in command_items) + "\n```\n")
    write(log_dir / "env.json", json.dumps({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "taskCode": args.task_code,
        "iteration": args.iteration,
        "bundleId": args.bundle_id,
        "deviceId": args.device_id,
        "traditionalDeviceId": args.traditional_device_id,
        "executable": args.executable,
        "processHint": args.process_hint,
        "wait": args.wait,
        "extended": args.extended,
        "screenshot": screenshot_enabled,
        "requireDisplay": args.require_display,
        "cwd": str(Path.cwd()),
    }, ensure_ascii=False, indent=2) + "\n")

    result, category, summary, failed, checks = classify(args, app_lookup, launch, processes, display)
    for item in failed:
        item["evidence"] = item["evidence"].replace("iter-N", f"iter-{args.iteration}")
    checks["screenshotCaptured"] = bool(screenshot.get("ok"))
    checks["recentCrashHintsFound"] = bool(crashes)

    evidence = [
        {"type": "other", "note": "app-lookup", "path": f"logs/iter-{args.iteration}/app-lookup.txt"},
        {"type": "other", "note": "launch", "path": f"logs/iter-{args.iteration}/launch.txt"},
        {"type": "other", "note": "processes", "path": f"logs/iter-{args.iteration}/processes.txt"},
        {"type": "other", "note": "display", "path": f"logs/iter-{args.iteration}/display.txt"},
        {"type": "other", "note": "crash-hints", "path": f"logs/iter-{args.iteration}/crash-hints.json"},
    ]
    if screenshot.get("path"):
        shot_path = Path(str(screenshot["path"]))
        evidence.append({"type": "screenshot", "path": str(shot_path.relative_to(task_dir)) if str(shot_path).startswith(str(task_dir)) else str(shot_path)})
    if screenshot.get("autoSetupAttempted"):
        evidence.append({"type": "log", "note": "ios-tools-auto-setup", "path": f"logs/iter-{args.iteration}/ios-tools-auto-setup.log"})

    payload = {
        "iteration": args.iteration,
        "result": result,
        "category": category,
        "summary": summary,
        "bundleId": args.bundle_id,
        "deviceId": args.device_id,
        "launchExitCode": launch.get("exitCode"),
        "processMarkers": [marker for marker in [args.executable, args.process_hint, args.bundle_id] if marker],
        "checks": checks,
        "failedChecks": failed,
        "screenshot": screenshot,
        "crashHints": crashes[:20],
        "evidence": evidence,
    }
    write(log_dir / "ios-app-smoke-summary.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    write(log_dir / "evaluator.log", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    evaluation = {
        "iteration": args.iteration,
        "result": result,
        "nextAction": "finish" if result == "pass" else "replan",
        "summary": summary,
        "failedChecks": failed,
        "evidence": evidence + [{"type": "other", "note": "summary", "path": f"logs/iter-{args.iteration}/ios-app-smoke-summary.json"}],
    }
    if screenshot_enabled and screenshot.get("category") == "tool_missing":
        evaluation.setdefault("warnings", []).append({
            "name": "ios_app_smoke_screenshot_tools",
            "category": "tool_missing",
            "reason": "CodeMind tried to create/repair .venv-ios-tools from requirements/ios-tools.txt, but screenshot helper tools are still unavailable: " + screenshot.get("reason", "iOS screenshot helper tools missing"),
            "setupCommand": screenshot.get("setupCommand", "automind setup-automation-tools ios"),
            "autoSetupAttempted": bool(screenshot.get("autoSetupAttempted")),
        })
    write(task_dir / "evaluation.json", json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n")
    write_runtime_state(task_dir, {
        "taskId": args.task_code,
        "taskType": "ios",
        "status": "finished" if result == "pass" else "failed",
        "iteration": args.iteration,
        "nextAction": evaluation["nextAction"],
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
    })
    (task_dir / "Validation.md").open("a").write(
        f"\n## Iteration {args.iteration} - iOS app smoke\n\n"
        f"- Environment: bundleId={args.bundle_id}; deviceId={args.device_id}; cwd={Path.cwd()}\n"
        f"- Commands: see `logs/iter-{args.iteration}/commands.md`\n"
        f"- Result: {result.upper()}\n"
        f"- Category: `{category}`\n"
        f"- Summary: {summary}\n"
        f"- Evidence: `logs/iter-{args.iteration}/app-lookup.txt`, `launch.txt`, `processes.txt`, `display.txt`, `crash-hints.json`, `ios-app-smoke-summary.json`\n"
        f"- Reusable findings: iOS app smoke can use devicectl launch/process evidence, optional display state, optional screenshot, and local crash hints as app-alive evidence.\n"
        f"- Avoid repeating: This smoke does not prove target-screen readiness or a task-specific user journey; encode those as explicit probe-flow/XCUITest actions.\n"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
