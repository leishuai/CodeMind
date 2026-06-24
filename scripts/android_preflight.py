#!/usr/bin/env python3
"""Android device readiness preflight for AutoMind."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import platform
import shutil
import subprocess
import sys
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from failure_classifier import classify
from automind_paths import ANDROID_TOOLS_PY, RUNTIME_ANDROID_TOOLS_PY, RUNTIME_ROOT, TASKS_DIR, WORKSPACE_ROOT, venv_requirements_current
from state_files import read_runtime_state, write_runtime_state

ROOT = RUNTIME_ROOT
REQUIRED_ANDROID_MODULES = ["adbutils", "uiautomator2"]


def python_has_android_modules(python_exec: pathlib.Path) -> bool:
    if not python_exec.exists():
        return False
    code, out = run([str(python_exec), "-c", "import adbutils, uiautomator2"])
    return code == 0


def android_python() -> str:
    candidates = [ANDROID_TOOLS_PY, RUNTIME_ANDROID_TOOLS_PY]
    for candidate in candidates:
        if python_has_android_modules(candidate):
            return str(candidate)
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(WORKSPACE_ROOT), text=True, capture_output=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return 124, out + f"\n[TIMEOUT after {timeout}s]"


def write_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def parse_package_status(text: str) -> dict[str, bool]:
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return {name: bool(data.get(name)) for name in REQUIRED_ANDROID_MODULES}


def missing_required_modules(status: dict[str, bool]) -> list[str]:
    return [name for name in REQUIRED_ANDROID_MODULES if status.get(name) is not True]


def _focus_summary(focus_text: str) -> dict[str, str]:
    """Extract deterministic foreground focus lines from dumpsys window output.

    Do not classify the mere presence of StatusBar/NavigationBar windows as an
    overlay: those windows are always listed on many Android builds. Only focus
    summary lines such as mCurrentFocus / mFocusedApp / mInputMethodTarget are
    useful for deciding whether UI automation is blocked.
    """
    rows = {}
    for raw in (focus_text or "").splitlines():
        line = raw.strip()
        lower = line.lower()
        if lower.startswith("mcurrentfocus") or " mcurrentfocus" in lower:
            rows["currentFocus"] = line
        elif lower.startswith("mfocusedapp") or " mfocusedapp" in lower:
            rows["focusedApp"] = line
        elif lower.startswith("minputmethodtarget") or " minputmethodtarget" in lower:
            rows["inputMethodTarget"] = line
    return rows


def diagnose_device_interactivity(power_text: str, window_policy_text: str, focus_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return deterministic Android device interactivity blockers/warnings.

    Block only on strong signals. Weak signals are returned as diagnostics so the
    coding agent can act on facts without asking the human unnecessarily.
    """
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    power = (power_text or "").lower()
    policy = (window_policy_text or "").lower()
    focus = focus_text or ""
    focus_lower = focus.lower()
    focus_summary = _focus_summary(focus)

    screen_off = any(marker in power for marker in ["display power: state=off", "display power: state=doze", "display power: state=unknown"]) or "mscreenon=false" in policy
    screen_on = any(marker in power for marker in ["display power: state=on", "mwakefulness=awake"]) or "mscreenonfully=true" in policy
    if screen_off:
        blockers.append({
            "name": "screen state",
            "category": "mobile_device_unavailable",
            "reason": "AutoMind detected the Android screen is off/not interactive from dumpsys power/window policy; turn the screen on and retry.",
        })
    elif not screen_on:
        warnings.append({
            "name": "screen state",
            "category": "mobile_device_state_unknown",
            "reason": "AutoMind could not prove the Android screen is on from dumpsys output; continuing unless UI execution proves otherwise.",
        })

    focused_lines = "\n".join(focus_summary.values()).lower()
    keyguard_markers = ["keyguard", "lockscreen", "keyguardservice"]
    notification_markers = ["notificationshade", "notificationshadewindow", "statusbarwindow"]
    if any(marker in focused_lines for marker in keyguard_markers):
        blockers.append({
            "name": "lockscreen focus",
            "category": "mobile_device_unavailable",
            "reason": "AutoMind detected lockscreen/keyguard as the focused UI from dumpsys window; unlock the device and retry.",
            "focus": focus_summary,
        })
    elif any(marker in focused_lines for marker in notification_markers):
        warnings.append({
            "name": "system overlay focus",
            "category": "mobile_device_overlay",
            "reason": "AutoMind detected a focused SystemUI/notification surface; it can usually be cleared/retried automatically before app launch.",
            "focus": focus_summary,
        })
    elif "statusbar" in focus_lower or "navigation_bar" in focus_lower:
        warnings.append({
            "name": "system ui present",
            "category": "diagnostic_only",
            "reason": "SystemUI windows are present in dumpsys output, but they are not focused; this is diagnostic only and should not block app launch.",
            "focus": focus_summary,
        })

    diagnostics = {
        "screenOn": screen_on and not screen_off,
        "screenOff": screen_off,
        "focus": focus_summary,
    }
    return blockers, warnings, diagnostics


def run_android_tools_auto_setup(iter_dir: pathlib.Path) -> tuple[int, str, dict[str, Any]]:
    cmd = [sys.executable, str(ROOT / "orchestrator" / "main.py"), "setup-automation-tools", "android"]
    code, out = run(cmd, timeout=900)
    (iter_dir / "android-tools-auto-setup.log").write_text(out)
    (iter_dir / "android-tools-auto-setup.exit-code.txt").write_text(str(code) + "\n")
    try:
        report = json.loads(out)
    except Exception:
        report = {}
    return code, out, report


def build_ask_user_question(failed: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any] | None:
    text = "\n".join(f.get("name", "") + " " + f.get("reason", "") for f in failed + warnings).lower()
    if "adbutils" in text or "uiautomator2" in text or "tool" in text:
        return {
            "question": "Android verification tools are still unavailable after AutoMind tried local helper setup. What should happen next?",
            "reason": "AutoMind can auto-create .venv-android-tools for low-risk Python helper packages, but the required adbutils/uiautomator2 modules are still unavailable. This may require fixing network/proxy/Python/pip or using a lower-capability adb-only fallback.",
            "options": [
                {"id": "A", "label": "I will fix Python/pip/network and retry setup.", "impact": "Keeps full Android probe-flow capability.", "requiresConfirmation": False},
                {"id": "B", "label": "Use adb fallback.", "impact": "Lower capability, but installs no new packages.", "requiresConfirmation": False},
                {"id": "C", "label": "Stop", "impact": "Keep the task blocked.", "requiresConfirmation": False},
            ],
            "recommended": "A",
            "setupCommand": "automind setup-automation-tools android",
            "retryCommand": "automind android-preflight <task-code> [iteration]",
            "defaultAction": "retry",
        }
    if "no android device" in text or "device discovery" in text:
        return {
            "question": "No Android device with adb state=device was found. What should happen next?",
            "reason": "Android probe-flow requires a physical device in adb device state; no usable device is currently available.",
            "options": [
                {"id": "A", "label": "I will connect an Android device, enable USB debugging, authorize it, then retry.", "impact": "Continue physical-device verification.", "requiresConfirmation": False},
                {"id": "B", "label": "Run dry-run only to verify flow configuration.", "impact": "Does not require a physical device, but will not produce real device evidence.", "requiresConfirmation": False},
                {"id": "C", "label": "Stop and handle the device later.", "impact": "Keep the task blocked.", "requiresConfirmation": False},
            ],
            "recommended": "A",
            "defaultAction": "retry",
        }
    if "screen is off" in text or "screen off" in text or "keyguard" in text or "lockscreen" in text or "locked" in text:
        return {
            "question": "AutoMind detected the Android device is not interactive for UI automation. Please unlock/turn on the device, then retry.",
            "reason": "The diagnosis came from adb/dumpsys evidence, not a guess. UI hierarchy/tap/assert requires an unlocked, screen-on foreground device.",
            "options": [
                {"id": "A", "label": "I fixed the device state; retry now.", "impact": "Clear the device-state blocker and run the real UI proof.", "requiresConfirmation": False},
                {"id": "B", "label": "Run dry-run only.", "impact": "Validates flow configuration but does not produce real device UI evidence.", "requiresConfirmation": False},
            ],
            "recommended": "A",
            "defaultAction": "retry",
        }
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Android preflight")
    parser.add_argument("task_code")
    parser.add_argument("iteration", nargs="?", type=int, default=1)
    parser.add_argument("--serial")
    args = parser.parse_args()

    task_dir = TASKS_DIR / args.task_code
    iter_dir = task_dir / "logs" / f"iter-{args.iteration}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)

    adb = shutil.which("adb") or str(pathlib.Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / "adb")
    if not pathlib.Path(adb).exists() and not shutil.which("adb"):
        adb = "adb"
    py = android_python()

    commands: dict[str, list[str]] = {
        "adb-devices": [adb, "devices", "-l"],
        "python-packages": [py, "-c", "import json; out={}\nfor n in ['adbutils','uiautomator2']:\n    try:\n        __import__(n); out[n]=True\n    except Exception:\n        out[n]=False\nprint(json.dumps(out))"],
    }
    serial = args.serial
    outputs: dict[str, tuple[int, str]] = {}
    for name, cmd in commands.items():
        code, out = run(cmd)
        outputs[name] = (code, out)
        (iter_dir / f"{name}.log").write_text(out)
        (iter_dir / f"{name}.exit-code.txt").write_text(str(code) + "\n")

    adb_text = outputs.get("adb-devices", (0, ""))[1].lower()
    adb_env_blocked = (
        outputs.get("adb-devices", (0, ""))[0] != 0
        and (
            "could not install *smartsocket* listener" in adb_text
            or "operation not permitted" in adb_text
            or "cannot connect to daemon" in adb_text
        )
    )
    if adb_env_blocked:
        # Retry once after an explicit start-server attempt. On macOS a host/user
        # adb server may become available between attempts; if it does, continue
        # without asking the user. If it still fails, classify as auto-retryable
        # environment blockage rather than a missing-device user decision.
        start_code, start_out = run([adb, "start-server"])
        outputs["adb-start-server-retry"] = (start_code, start_out)
        (iter_dir / "adb-start-server-retry.log").write_text(start_out)
        (iter_dir / "adb-start-server-retry.exit-code.txt").write_text(str(start_code) + "\n")
        retry_code, retry_out = run([adb, "devices", "-l"])
        outputs["adb-devices-retry"] = (retry_code, retry_out)
        (iter_dir / "adb-devices-retry.log").write_text(retry_out)
        (iter_dir / "adb-devices-retry.exit-code.txt").write_text(str(retry_code) + "\n")
        if retry_code == 0:
            outputs["adb-devices"] = (retry_code, retry_out)
            (iter_dir / "adb-devices.log").write_text(retry_out)
            (iter_dir / "adb-devices.exit-code.txt").write_text("0\n")
            adb_env_blocked = False

    # Pick first available device from adb devices if serial not supplied.
    if not serial:
        for line in outputs.get("adb-devices", (1, ""))[1].splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                break

    device_outputs = {}
    if serial:
        device_cmds = {
            "get-state": [adb, "-s", serial, "get-state"],
            "model": [adb, "-s", serial, "shell", "getprop", "ro.product.model"],
            "brand": [adb, "-s", serial, "shell", "getprop", "ro.product.brand"],
            "sdk": [adb, "-s", serial, "shell", "getprop", "ro.build.version.sdk"],
            "release": [adb, "-s", serial, "shell", "getprop", "ro.build.version.release"],
            "screen-power": [adb, "-s", serial, "shell", "dumpsys", "power"],
            "window-policy": [adb, "-s", serial, "shell", "dumpsys", "window", "policy"],
            "current-focus": [adb, "-s", serial, "shell", "dumpsys", "window", "windows"],
            "current-app": [adb, "-s", serial, "shell", "dumpsys", "activity", "activities"],
            "settings-install-non-market": [adb, "-s", serial, "shell", "settings", "get", "secure", "install_non_market_apps"],
        }
        for name, cmd in device_cmds.items():
            code, out = run(cmd, timeout=20)
            outputs[name] = (code, out)
            device_outputs[name] = out
            (iter_dir / f"{name}.log").write_text(out)
            (iter_dir / f"{name}.exit-code.txt").write_text(str(code) + "\n")

    failed = []
    warnings = []
    packages = outputs.get("python-packages", (1, "{}"))[1]
    pkg = parse_package_status(packages)
    auto_setup_report: dict[str, Any] | None = None
    auto_setup_attempted = False
    reqs_stale = not venv_requirements_current("android")
    if missing_required_modules(pkg) or reqs_stale:
        auto_setup_attempted = True
        setup_cmd = [sys.executable, str(ROOT / "orchestrator" / "main.py"), "setup-automation-tools", "android"]
        commands["android-tools-auto-setup"] = setup_cmd
        setup_code, setup_out, auto_setup_report = run_android_tools_auto_setup(iter_dir)
        outputs["android-tools-auto-setup"] = (setup_code, setup_out)
        py = android_python()
        package_cmd = [py, "-c", "import json; out={}\nfor n in ['adbutils','uiautomator2']:\n    try:\n        __import__(n); out[n]=True\n    except Exception:\n        out[n]=False\nprint(json.dumps(out))"]
        commands["python-packages-after-setup"] = package_cmd
        code, out = run(package_cmd)
        outputs["python-packages-after-setup"] = (code, out)
        (iter_dir / "python-packages-after-setup.log").write_text(out)
        (iter_dir / "python-packages-after-setup.exit-code.txt").write_text(str(code) + "\n")
        pkg = parse_package_status(out)
    if outputs.get("adb-devices", (1, ""))[0] != 0:
        if adb_env_blocked:
            failed.append({
                "name": "adb server",
                "category": "permission_blocked",
                "reason": "adb daemon/smartsocket could not start in the current runner environment; retry from an approval-capable/bypassed runner or after host adb server recovery.",
                "sameProblemKey": "android.adb.server.environment_blocked",
                "evidence": [
                    f"logs/iter-{args.iteration}/adb-devices.log",
                    f"logs/iter-{args.iteration}/adb-start-server-retry.log",
                    f"logs/iter-{args.iteration}/adb-devices-retry.log",
                ],
                "autoRetryable": True,
            })
        else:
            failed.append({"name": "adb devices", "category": "tool_missing", "reason": "adb devices failed"})
    if not serial and not adb_env_blocked:
        c = classify("android", "preflight", "No Android device in adb state=device")
        failed.append({"name": "android device discovery", "category": c.category, "reason": c.reason, "sameProblemKey": c.sameProblemKey})
    for mod in REQUIRED_ANDROID_MODULES:
        if pkg.get(mod) is False:
            warnings.append({"name": mod, "category": "tool_missing", "reason": f"{mod} not available in selected Android Python"})
    missing_after_setup = missing_required_modules(pkg)
    if auto_setup_attempted and missing_after_setup:
        failed.append({
            "name": "android tools auto setup",
            "category": "tool_missing",
            "reason": "AutoMind tried to create/repair .venv-android-tools from requirements/android-tools.txt, but required modules are still missing: " + ", ".join(missing_after_setup),
            "evidence": [
                f"logs/iter-{args.iteration}/android-tools-auto-setup.log",
                f"logs/iter-{args.iteration}/python-packages-after-setup.log",
            ],
        })

    device_diagnostics: dict[str, Any] = {}
    if serial:
        interactivity_blockers, interactivity_warnings, device_diagnostics = diagnose_device_interactivity(
            device_outputs.get("screen-power", ""),
            device_outputs.get("window-policy", ""),
            device_outputs.get("current-focus", ""),
        )
        failed.extend(interactivity_blockers)
        warnings.extend(interactivity_warnings)

    device_info = {
        "serial": serial or "",
        "model": device_outputs.get("model", "").strip(),
        "brand": device_outputs.get("brand", "").strip(),
        "sdk": device_outputs.get("sdk", "").strip(),
        "release": device_outputs.get("release", "").strip(),
    }
    write_json(iter_dir / "android-device-info.json", device_info)

    if not failed and any(item.get("category") == "tool_missing" for item in warnings):
        preflight_result = "blocked"
    else:
        preflight_result = "blocked" if failed else "pass"
    # Preflight only proves device/tool readiness. For app/runtime tasks it must
    # not finish the AutoMind task or mark required TC proof as complete.
    result = "blocked" if preflight_result == "blocked" else "partial"
    summary = (
        "Android device preflight passed; proof flow or script-command testResults are still required"
        if preflight_result == "pass"
        else "Android device preflight blocked"
    )
    evidence = []
    for name in outputs:
        evidence.append({"type": "log", "path": f"logs/iter-{args.iteration}/{name}.log"})
    evidence.extend([
        {"type": "other", "note": "android-device-info", "path": f"logs/iter-{args.iteration}/android-device-info.json"},
        {"type": "other", "note": "env", "path": f"logs/iter-{args.iteration}/env.json"},
        {"type": "command", "path": f"logs/iter-{args.iteration}/commands.md"},
    ])

    env = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "cwd": str(WORKSPACE_ROOT),
        "os": platform.platform(),
        "python": py,
        "tools": {"adb": adb, "adbutils": pkg.get("adbutils", "unknown"), "uiautomator2": pkg.get("uiautomator2", "unknown")},
        "android": device_info,
        "deviceDiagnostics": device_diagnostics,
        "autoSetup": {
            "attempted": auto_setup_attempted,
            "target": "android" if auto_setup_attempted else None,
            "requirements": "requirements/android-tools.txt" if auto_setup_attempted else None,
            "report": auto_setup_report,
        },
    }
    write_json(iter_dir / "env.json", env)
    (iter_dir / "commands.md").write_text("# Commands\n\n" + "\n".join(f"```bash\n{' '.join(cmd if isinstance(cmd, list) else [cmd])}\n```" for cmd in list(commands.values())) + "\n")
    (iter_dir / "evaluator.log").write_text("\n\n".join(f"## {name}\n{out}" for name, (_code, out) in outputs.items()))

    failed_checks = list(failed)
    if preflight_result == "pass":
        failed_checks.append({
            "name": "android_preflight_only",
            "category": "validation_incomplete",
            "reason": "Android preflight proves device readiness only; required runtime TC proof/testResults still need android-probe-flow or script-command evidence.",
            "evidence": f"logs/iter-{args.iteration}/android-device-info.json",
        })
    evaluation = {
        "iteration": args.iteration,
        "result": result,
        "preflightResult": preflight_result,
        "summary": summary,
        "failedChecks": failed_checks,
        "warnings": warnings,
        "evidence": evidence,
        "nextAction": "retry_generator" if preflight_result == "pass" else "replan",
        "preflightOnly": preflight_result == "pass",
        "proofRequired": preflight_result == "pass",
    }
    auto_retryable_env_block = any(isinstance(item, dict) and item.get("autoRetryable") for item in failed)
    if auto_retryable_env_block:
        evaluation["nextAction"] = "retry_generator"
        evaluation["autoRecovery"] = {
            "selected": "resume_after_recovery",
            "reason": "adb_server_environment_blocked_auto_retryable",
            "details": "Do not ask the user for a known runner/adb smartsocket environment blockage; retry after host/runner recovery or with evaluator bypass capability.",
        }
    ask = None if auto_retryable_env_block else build_ask_user_question(failed, warnings)
    if ask and result != "pass":
        evaluation["askUserQuestion"] = ask
        evaluation["nextAction"] = "ask_user"
    write_json(task_dir / "evaluation.json", evaluation)
    state = read_runtime_state(task_dir)
    state.update({
        "taskId": args.task_code,
        "taskType": "android",
        "status": "retry_pending" if preflight_result == "pass" else ("human_input_pending" if evaluation["nextAction"] == "ask_user" else "blocked"),
        "iteration": args.iteration,
        "nextAction": evaluation["nextAction"],
        "androidDevice": device_info,
        "updatedAt": dt.datetime.now().isoformat(timespec="seconds"),
    })
    write_runtime_state(task_dir, state)

    validation = task_dir / "Validation.md"
    existing = validation.read_text() if validation.exists() else "# Validation\n"
    validation.write_text(existing.rstrip() + f"""

### Iteration {args.iteration} - Android preflight
- Time: {dt.datetime.now().isoformat(timespec='seconds')}
- Environment: serial=`{device_info.get('serial', '-')}`; model=`{device_info.get('model', '-')}`; Android=`{device_info.get('release', '-')}`; sdk=`{device_info.get('sdk', '-')}`.
- Commands: see `logs/iter-{args.iteration}/commands.md`.
- Result: {result.upper()}. {summary}
- Preflight result: {preflight_result.upper()}.
- Failure category: {failed_checks[0]['category'] if failed_checks else 'none'}.
- Evidence: 
  - `logs/iter-{args.iteration}/adb-devices.log`
  - `logs/iter-{args.iteration}/screen-power.log`
  - `logs/iter-{args.iteration}/window-policy.log`
  - `logs/iter-{args.iteration}/current-focus.log`
  - `logs/iter-{args.iteration}/current-app.log`
  - `logs/iter-{args.iteration}/env.json`
  - `logs/iter-{args.iteration}/commands.md`
- Reusable findings: Before Android UI/evidence collection, confirm adb state=device, tools are available, the device is unlocked/screen-on, and no SystemUI/notification/lockscreen overlay is present.
- Avoid repeating: Do not misclassify lockscreen/SystemUI/authorization/missing-tool issues as product-code failures.
""")
    print(json.dumps(evaluation, ensure_ascii=False, indent=2))
    return 0 if preflight_result == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
