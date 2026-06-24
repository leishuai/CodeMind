#!/usr/bin/env python3
"""Minimal Android APK install/launch probe for AutoMind.

Scope is intentionally small: parse APK badging, optionally uninstall existing
package, install APK, launch activity, capture current app/screenshot/hierarchy,
and write reusable task records.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from automind_paths import ANDROID_TOOLS_PY, RUNTIME_ROOT, TASKS_DIR, WORKSPACE_ROOT, venv_requirements_current

ROOT = RUNTIME_ROOT
TASKS = TASKS_DIR


def run_android_tools_auto_setup(out: Path) -> dict:
    """Create/repair project-local Android helper venv when capture needs it."""
    cmd = [sys.executable, str(ROOT / "orchestrator" / "main.py"), "setup-automation-tools", "android"]
    result = run(cmd, timeout=runtime_timeout("AUTOMIND_AUTOMATION_SETUP_TIMEOUT"))
    (out / "android-tools-auto-setup.log").write_text((result.get("stdout") or "") + (result.get("stderr") or ""))
    (out / "android-tools-auto-setup.exit-code.txt").write_text(str(result.get("returncode")) + "\n")
    try:
        report = json.loads((result.get("stdout") or "") + (result.get("stderr") or ""))
    except Exception:
        report = {}
    return {
        "attempted": True,
        "exitCode": result.get("returncode"),
        "report": report,
        "log": str(out / "android-tools-auto-setup.log"),
    }


def android_capture_modules_ready(py: Path) -> tuple[bool, str]:
    code = run([
        str(py),
        "-c",
        "import adbutils, uiautomator2",
    ], timeout=30)
    if code.get("returncode") == 0:
        return True, "ready"
    return False, "missing Android helper modules: adbutils,uiautomator2"


def runtime_timeout(env_var: str = "AUTOMIND_CMD_TIMEOUT", default: int = 300) -> int:
    raw = os.environ.get(env_var) or os.environ.get("AUTOMIND_CMD_TIMEOUT") or str(default)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def run(cmd: list[str], *, timeout: int | None = None, check: bool = False) -> dict:
    timeout = timeout if timeout is not None else runtime_timeout("AUTOMIND_ANDROID_APK_PROBE_TIMEOUT")
    env = os.environ.copy()
    platform_tools = Path.home() / "Library" / "Android" / "sdk" / "platform-tools"
    env["PATH"] = f"{platform_tools}:{env.get('PATH', '')}"
    p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, env=env)
    result = {"cmd": cmd, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    if check and p.returncode != 0:
        raise RuntimeError(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def find_aapt() -> str:
    candidates = sorted((Path.home() / "Library" / "Android" / "sdk" / "build-tools").glob("*/aapt"))
    if candidates:
        return str(candidates[-1])
    found = shutil.which("aapt")
    if found:
        return found
    raise SystemExit("aapt not found")


def parse_badging(apk: Path) -> dict:
    aapt = find_aapt()
    r = run([aapt, "dump", "badging", str(apk)], timeout=120, check=True)
    text = r["stdout"]
    pkg = re.search(r"package: name='([^']+)'", text)
    launch = re.search(r"launchable-activity: name='([^']+)'", text)
    return {
        "aapt": aapt,
        "package": pkg.group(1) if pkg else "",
        "launchableActivity": launch.group(1) if launch else "",
        "raw": text,
    }


def capture_with_python(out: Path, expected_pkg: str, expected_act: str) -> dict:
    py = ANDROID_TOOLS_PY if ANDROID_TOOLS_PY.exists() else Path(sys.executable)
    ready, reason = android_capture_modules_ready(py)
    if ready and not venv_requirements_current("android"):
        ready, reason = False, "requirements changed since .venv-android-tools was built (stale stamp)"
    auto_setup: dict | None = None
    if not ready:
        auto_setup = run_android_tools_auto_setup(out)
        py = ANDROID_TOOLS_PY if ANDROID_TOOLS_PY.exists() else Path(sys.executable)
        ready, reason = android_capture_modules_ready(py)
    if not ready:
        return {
            "result": "blocked",
            "category": "tool_missing",
            "error": reason + "; AutoMind tried project-local setup from requirements/android-tools.txt but capture helpers are still unavailable.",
            "python": str(py),
            "autoSetup": auto_setup,
            "setupCommand": "automind setup-automation-tools android",
        }
    script = f"""
import json
from pathlib import Path
from adbutils import adb
import uiautomator2 as u2
out = Path({str(out)!r})
d = adb.device()
u = u2.connect(d.serial)
current = d.app_current()
xml = u.dump_hierarchy()
(out / 'hierarchy.xml').write_text(xml)
u.screenshot(str(out / 'screenshot.png'))
summary = {{
  'serial': d.serial,
  'expectedPackage': {expected_pkg!r},
  'expectedLaunchActivity': {expected_act!r},
  'current': {{'package': current.package, 'activity': current.activity, 'pid': getattr(current, 'pid', None)}},
  'hierarchyChars': len(xml),
  'screenshot': str(out / 'screenshot.png'),
  'hierarchy': str(out / 'hierarchy.xml'),
  'result': 'pass' if current.package == {expected_pkg!r} else 'fail'
}}
(out / 'apk-launch-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False, indent=2))
"""
    r = run([str(py), "-c", script], timeout=runtime_timeout("AUTOMIND_ANDROID_APK_PROBE_TIMEOUT"))
    if r["returncode"] != 0:
        return {"result": "fail", "error": r["stderr"] or r["stdout"], "python": str(py), "autoSetup": auto_setup}
    try:
        summary = json.loads(r["stdout"])
        if auto_setup:
            summary["autoSetup"] = auto_setup
        summary["python"] = str(py)
        return summary
    except Exception:
        return {"result": "fail", "error": r["stdout"], "python": str(py), "autoSetup": auto_setup}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("apk", help="Path to APK")
    ap.add_argument("task", nargs="?", default="android_apk_probe")
    ap.add_argument("--uninstall", action="store_true", help="Uninstall existing package before installing APK")
    ap.add_argument("--package", help="Override package name")
    ap.add_argument("--activity", help="Override launch activity")
    args = ap.parse_args()

    apk = Path(args.apk).expanduser().resolve()
    if not apk.exists():
        raise SystemExit(f"APK not found: {apk}")

    task_dir = TASKS / args.task
    out = task_dir / "logs" / "iter-1"
    out.mkdir(parents=True, exist_ok=True)

    badging = parse_badging(apk)
    package = args.package or badging["package"]
    activity = args.activity or badging["launchableActivity"]
    if not package or not activity:
        raise SystemExit("Cannot determine package/activity; pass --package and --activity")

    (out / "badging.txt").write_text(badging["raw"])
    log_lines: list[str] = []

    def record(title: str, r: dict):
        log_lines.append(f"--- {title} ---")
        log_lines.append("$ " + " ".join(r["cmd"]))
        if r.get("stdout"):
            log_lines.append(r["stdout"].rstrip())
        if r.get("stderr"):
            log_lines.append(r["stderr"].rstrip())
        log_lines.append(f"exit={r['returncode']}")

    record("adb devices", run(["adb", "devices", "-l"], timeout=60))
    record("pre package path", run(["adb", "shell", "pm", "path", package], timeout=60))
    if args.uninstall:
        record("uninstall existing package", run(["adb", "uninstall", package], timeout=runtime_timeout("AUTOMIND_ANDROID_APK_PROBE_TIMEOUT")))
    install = run(["adb", "install", "-r", "-t", str(apk)], timeout=runtime_timeout("AUTOMIND_ANDROID_APK_PROBE_TIMEOUT"))
    record("install apk", install)

    if install["returncode"] == 0:
        launch = run(["adb", "shell", "am", "start", "-S", "-n", f"{package}/{activity}"], timeout=runtime_timeout("AUTOMIND_ANDROID_APK_PROBE_TIMEOUT"))
        record("launch apk", launch)
        focus = run(["bash", "-lc", "adb shell dumpsys window | grep -E 'mCurrentFocus|mFocusedApp|topResumedActivity' || true"], timeout=60)
        record("current app", focus)
        capture = capture_with_python(out, package, activity)
    else:
        launch = {"returncode": -1, "stdout": "", "stderr": "install failed; launch skipped"}
        capture = {"result": "blocked", "error": install["stderr"] or install["stdout"]}

    (out / "apk-probe.log").write_text("\n".join(log_lines) + "\n")
    (out / "env.json").write_text(json.dumps({
        "cwd": str(WORKSPACE_ROOT),
        "apk": str(apk),
        "package": package,
        "launchableActivity": activity,
        "uninstallFirst": args.uninstall,
        "androidToolsPython": str(ANDROID_TOOLS_PY),
    }, ensure_ascii=False, indent=2))
    (out / "commands.md").write_text(f"""# Commands\n\n```bash\nadb {'uninstall ' + package if args.uninstall else '# uninstall skipped'}\nadb install -r -t {apk}\nadb shell am start -S -n {package}/{activity}\n```\n""")
    shutil.copyfile(out / "apk-probe.log", out / "evaluator.log")

    result = "pass" if capture.get("result") == "pass" else "blocked" if install["returncode"] != 0 or capture.get("category") == "tool_missing" else "fail"
    failure_category = "install_failure" if install["returncode"] != 0 else (capture.get("category") or "launch_failure")
    if result == "pass":
        next_action = "finish"
    elif failure_category in {"tool_missing", "mobile_device_unavailable", "permission_blocked"}:
        next_action = "ask_user"
    else:
        next_action = "retry_generator"
    evaluation = {
        "iteration": 1,
        "result": result,
        "summary": f"APK probe {result}: package={package}, activity={activity}",
        "passedChecks": [
            {"name": "parse_apk_badging", "result": "pass"},
            {"name": "install_apk", "result": "pass" if install["returncode"] == 0 else "fail"},
            {"name": "launch_and_capture", "result": capture.get("result", "fail")},
        ],
        "failedChecks": [] if result == "pass" else [
            {
                "name": "android_apk_probe",
                "reason": capture.get("error") or install.get("stderr") or install.get("stdout") or "APK probe failed",
                "category": failure_category,
            }
        ],
        "evidence": [
            {"type": "other", "note": "apk", "path": str(apk)},
            {"type": "other", "note": "badging", "path": str(out / "badging.txt")},
            {"type": "log", "path": str(out / "apk-probe.log")},
            {"type": "other", "note": "apk-launch-summary", "path": str(out / "apk-launch-summary.json")},
            {"type": "screenshot", "path": str(out / "screenshot.png")},
            {"type": "ui_hierarchy", "path": str(out / "hierarchy.xml")},
        ],
        "nextAction": next_action,
    }
    if capture.get("autoSetup"):
        evaluation.setdefault("warnings", []).append({
            "name": "android_apk_probe_capture_tools",
            "category": "tool_missing" if capture.get("category") == "tool_missing" else "info",
            "reason": "AutoMind attempted project-local Android helper setup for APK capture.",
            "setupCommand": "automind setup-automation-tools android",
            "setup": capture.get("autoSetup"),
        })
        evaluation["evidence"].append({"type": "log", "note": "android-tools-auto-setup", "path": str(out / "android-tools-auto-setup.log")})
    if capture.get("category") == "tool_missing":
        evaluation["askUserQuestion"] = {
            "question": "Android APK capture helper tools are still unavailable after AutoMind tried local setup. What should happen next?",
            "reason": capture.get("error", "adbutils/uiautomator2 unavailable"),
            "options": [
                {"id": "A", "label": "Fix Python/pip/network and retry.", "impact": "Keeps screenshot/UI hierarchy capture for APK verification.", "requiresConfirmation": False},
                {"id": "B", "label": "Use adb-only evidence.", "impact": "Lower confidence; can still use install/launch/current-focus logs.", "requiresConfirmation": False},
                {"id": "C", "label": "Replan verification", "impact": "Use another runnable Android verification path or defer UI capture.", "requiresConfirmation": False},
            ],
            "recommended": "A",
            "setupCommand": "automind setup-automation-tools android",
            "retryCommand": "automind android-apk-probe <apk-path> <task-code>",
            "defaultAction": "retry",
        }
        evaluation["nextAction"] = "ask_user"
    (task_dir / "evaluation.json").write_text(json.dumps(evaluation, ensure_ascii=False, indent=2))
    write_runtime_state(task_dir, {
        "taskId": args.task,
        "taskType": "android",
        "status": "finished" if result == "pass" else ("human_input_pending" if evaluation["nextAction"] == "ask_user" else "retry_pending"),
        "iteration": 1,
        "currentOwner": "supervisor",
        "nextAction": evaluation["nextAction"],
        "lastResult": result,
        "androidApp": {"apk": str(apk), "package": package, "activity": activity},
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
    })
    (task_dir / "Requirements.md").write_text(f"# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — Android APK probe\n- **AC-001**: Install and launch APK `{apk}`, then capture screenshot and UI hierarchy evidence.\n  - Verification method: android-apk-probe / TC-F01\n")
    (task_dir / "Plan.md").write_text("# Plan\n\n1. Parse APK badging.\n2. Optionally uninstall existing package.\n3. Install APK.\n4. Launch activity.\n5. Capture current app, screenshot, hierarchy.\n")
    (task_dir / "Validation.md").write_text(f"""# Validation

### Iteration 1 - APK probe
- Environment: cwd=`{WORKSPACE_ROOT}`; APK=`{apk}`; package=`{package}`; activity=`{activity}`; uninstallFirst=`{args.uninstall}`.
- Commands: `./automind.sh android-apk-probe {apk} {args.task} {'--uninstall' if args.uninstall else ''}`.Internally runs `aapt dump badging`, `adb install -r -t`, `adb shell am start -S -n {package}/{activity}`, uiautomator2 screenshot/hierarchy.
- Result: `{result}`.
- Evidence: 
  - `logs/iter-1/apk-probe.log`
  - `logs/iter-1/apk-launch-summary.json`
  - `logs/iter-1/screenshot.png`
  - `logs/iter-1/hierarchy.xml`
- Reusable findings: For similar Android APK verification, reuse `./automind.sh android-apk-probe <apk-path> <task-code> [--uninstall]`, it automatically parses package/activity, installs, launches, and collects UI evidence.
- Avoid repeating: When signature conflict occurs, do not uninstall the old package without approval; after approval use `--uninstall`. Do not misclassify install conflicts as product-code failures.
- Next step: If this probe passes, continue to more specific probe-flow acceptance; if it fails, classify install/launch/device first.
""")

    print(json.dumps({
        "task": args.task,
        "result": result,
        "package": package,
        "activity": activity,
        "capture": capture,
        "taskDir": str(task_dir),
    }, ensure_ascii=False, indent=2))
    return 0 if result == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
