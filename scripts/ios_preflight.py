#!/usr/bin/env python3
"""iOS physical-device preflight evaluator for CodeMind."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import platform
import re
import shutil
import subprocess
import sys
from typing import Any

from automind_paths import RUNTIME_ROOT, TASKS_DIR, WORKSPACE_ROOT
from state_files import read_runtime_state, write_runtime_state

ROOT = RUNTIME_ROOT


def run(cmd: list[str], timeout: int = 45) -> tuple[int, str]:
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


def parse_xcodebuildmcp_device_list(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    m = re.search(r"UDID:\s*([^\n]+)", text)
    if m:
        data["coreDeviceId"] = m.group(1).strip()
    m = re.search(r"📱\s*([^\n]+)", text)
    if m:
        data["deviceName"] = m.group(1).strip()
    m = re.search(r"Model:\s*([^\n]+)", text)
    if m:
        data["model"] = m.group(1).strip()
    m = re.search(r"Platform:\s*([^\n]+)", text)
    if m:
        data["platform"] = m.group(1).strip()
    m = re.search(r"Connection:\s*([^\n]+)", text)
    if m:
        data["connection"] = m.group(1).strip()
    m = re.search(r"Developer Mode:\s*([^\n]+)", text)
    if m:
        data["developerMode"] = m.group(1).strip()
    return data


def parse_devicectl_json(path: pathlib.Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    devices = ((data.get("result") or {}).get("devices") or [])
    if not devices:
        return {}
    dev = devices[0]
    props = dev.get("deviceProperties") or {}
    hw = dev.get("hardwareProperties") or {}
    conn = dev.get("connectionProperties") or {}
    return {
        "coreDeviceId": dev.get("identifier", ""),
        "xcodebuildDeviceId": hw.get("udid", ""),
        "deviceName": props.get("name", ""),
        "model": hw.get("productType", ""),
        "osVersion": props.get("osVersionNumber", ""),
        "developerMode": props.get("developerModeStatus", ""),
        "transport": conn.get("transportType", ""),
        "pairingState": conn.get("pairingState", ""),
        "ddiServicesAvailable": str(props.get("ddiServicesAvailable", "")),
    }


def build_ask_user_question(failed: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any] | None:
    names = "\n".join(f.get("name", "") + " " + f.get("reason", "") for f in failed + warnings).lower()
    if "developer mode" in names:
        return {
            "question": "iOS physical-device verification requires Developer Mode. Has it been enabled on the iPhone and confirmed after restart?",
            "reason": "Physical-device build/install/launch/test/UI automation requires Developer Mode; the agent cannot enable it for the user.",
            "options": [
                {"id": "A", "label": "I will enable Developer Mode in iPhone Settings, then retry.", "impact": "Clear the physical-device verification permission blocker.", "requiresConfirmation": False},
                {"id": "B", "label": "Run simulator / dry-run only for now.", "impact": "Skip physical-device verification and continue validating configuration/scripts.", "requiresConfirmation": False},
                {"id": "C", "label": "Stop this iOS physical-device verification round.", "impact": "Keep the current blocked state.", "requiresConfirmation": False},
            ],
            "recommended": "A",
            "defaultAction": "retry",
        }
    if "no available physical ios device" in names or "physical device discovery" in names:
        return {
            "question": "No usable iOS physical device was found. What should happen next?",
            "reason": "XcodeBuildMCP/CoreDevice did not find a usable device, so physical-device verification cannot run.",
            "options": [
                {"id": "A", "label": "I will connect/unlock the iPhone and trust this Mac, then retry.", "impact": "Continue physical-device verification.", "requiresConfirmation": False},
                {"id": "B", "label": "Switch to simulator or dry-run.", "impact": "Does not require a physical device, but evidence will not represent a physical device.", "requiresConfirmation": False},
                {"id": "C", "label": "Stop and handle the device later.", "impact": "Keep the task blocked.", "requiresConfirmation": False},
            ],
            "recommended": "A",
            "defaultAction": "retry",
        }
    if "display backlight" in names or "screen" in names or "backlight" in names:
        return {
            "question": "The device screen may be off. Is the iPhone unlocked and screen-on?",
            "reason": "UI/evidence operations require the device to stay screen-on; screen-off can cause false launch/screenshot/UI automation failures.",
            "options": [
                {"id": "A", "label": "I will keep the device unlocked and screen-on, then retry.", "impact": "Clear the device-state blocker.", "requiresConfirmation": False},
                {"id": "B", "label": "Skip UI/evidence and run build/dry-run only.", "impact": "Reduces device dependency, but evidence is weaker.", "requiresConfirmation": False},
            ],
            "recommended": "A",
            "defaultAction": "retry",
        }
    return None


def parse_display(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    m = re.search(r"Main display backlight state:\s*([^\n]+)", text)
    if m:
        data["backlight"] = m.group(1).strip()
    m = re.search(r"Main display orientation:\s*([^\n]+)", text)
    if m:
        data["orientation"] = m.group(1).strip()
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Run iOS physical-device preflight")
    parser.add_argument("task_code")
    parser.add_argument("iteration", nargs="?", type=int, default=1)
    parser.add_argument("--device-id", help="CoreDevice id; optional, will use first device from list")
    parser.add_argument("--standalone-finish", action="store_true", help="Allow preflight pass to produce finish; default keeps product verification in progress.")
    args = parser.parse_args()

    task_dir = TASKS_DIR / args.task_code
    iter_dir = task_dir / "logs" / f"iter-{args.iteration}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)

    commands = {
        "xcodebuild-version": ["xcodebuild", "-version"],
        "xcodebuildmcp-device-list": ["xcodebuildmcp", "device", "list"],
        "devicectl-list": ["xcrun", "devicectl", "list", "devices", "--json-output", str(iter_dir / "devicectl-devices.json")],
        "idevice-id": ["idevice_id", "-l"],
    }
    outputs: dict[str, tuple[int, str]] = {}
    for name, cmd in commands.items():
        code, out = run(cmd, timeout=45)
        outputs[name] = (code, out)
        (iter_dir / f"{name}.log").write_text(out)
        (iter_dir / f"{name}.exit-code.txt").write_text(str(code) + "\n")

    xcb = parse_xcodebuildmcp_device_list(outputs["xcodebuildmcp-device-list"][1])
    devjson = parse_devicectl_json(iter_dir / "devicectl-devices.json")
    device = {**xcb, **{k: v for k, v in devjson.items() if v}}
    core_id = args.device_id or device.get("coreDeviceId")

    display_info: dict[str, str] = {}
    if core_id:
        code, out = run(["xcrun", "devicectl", "device", "info", "displays", "--device", core_id, "--json-output", str(iter_dir / "displays.json")], timeout=45)
        outputs["display-info"] = (code, out)
        (iter_dir / "display-info.log").write_text(out)
        (iter_dir / "display-info.exit-code.txt").write_text(str(code) + "\n")
        display_info = parse_display(out)

    failed = []
    warnings = []
    if not shutil.which("xcodebuild"):
        failed.append({"name": "xcodebuild", "category": "tool_missing", "reason": "xcodebuild not found"})
    if not shutil.which("xcodebuildmcp"):
        failed.append({"name": "xcodebuildmcp", "category": "tool_missing", "reason": "xcodebuildmcp not found"})
    if not device.get("coreDeviceId"):
        failed.append({"name": "physical device discovery", "category": "mobile_device_unavailable", "reason": "No available physical iOS device found by XcodeBuildMCP/CoreDevice"})
    if device.get("developerMode") and device.get("developerMode") != "enabled":
        failed.append({"name": "Developer Mode", "category": "permission_blocked", "reason": "Developer Mode is not enabled"})
    if device.get("connection") and device.get("connection") != "wired":
        warnings.append({"name": "connection", "category": "mobile_device_unavailable", "detail": "device_state", "reason": f"Connection is {device.get('connection')}, wired is recommended"})
    if display_info.get("backlight") and "off" in display_info["backlight"].lower():
        warnings.append({"name": "display backlight", "category": "mobile_device_unavailable", "reason": "Display backlight is off; UI/evidence operations need screen on"})

    result = "blocked" if failed else "pass"
    summary = "iOS physical-device preflight passed" if result == "pass" else "iOS physical-device preflight blocked"
    evidence = []
    for name in outputs:
        evidence.append({"type": "log", "path": f"logs/iter-{args.iteration}/{name}.log"})
    if (iter_dir / "devicectl-devices.json").exists():
        evidence.append({"type": "other", "note": "devicectl-devices", "path": f"logs/iter-{args.iteration}/devicectl-devices.json"})
    if (iter_dir / "displays.json").exists():
        evidence.append({"type": "other", "note": "displays", "path": f"logs/iter-{args.iteration}/displays.json"})

    env = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "cwd": str(WORKSPACE_ROOT),
        "os": platform.platform(),
        "tools": {name: shutil.which(name) or "missing" for name in ["xcodebuild", "xcrun", "xcodebuildmcp", "idevice_id", "ideviceinfo"]},
        "ios": {**device, "display": display_info},
    }
    write_json(iter_dir / "env.json", env)
    (iter_dir / "commands.md").write_text("# Commands\n\n" + "\n".join(f"```bash\n{' '.join(cmd)}\n```" for cmd in commands.values()) + "\n")
    (iter_dir / "evaluator.log").write_text("\n\n".join(f"## {name}\n{out}" for name, (_code, out) in outputs.items()))
    evidence.extend([
        {"type": "other", "note": "env", "path": f"logs/iter-{args.iteration}/env.json"},
        {"type": "command", "path": f"logs/iter-{args.iteration}/commands.md"},
    ])

    evaluation_result = "pass" if (result == "pass" and args.standalone_finish) else ("in_progress" if result == "pass" else result)
    evaluation_next_action = "finish" if (result == "pass" and args.standalone_finish) else ("retry_generator" if result == "pass" else "replan")
    evaluation_summary = (
        summary
        if result != "pass" or args.standalone_finish
        else "iOS physical-device preflight passed; product build/install/launch/test verification still required"
    )
    evaluation = {
        "iteration": args.iteration,
        "result": evaluation_result,
        "summary": evaluation_summary,
        "failedChecks": failed,
        "warnings": warnings,
        "evidence": evidence,
        "nextAction": evaluation_next_action,
    }
    ask = build_ask_user_question(failed, warnings)
    if ask and result != "pass":
        evaluation["askUserQuestion"] = ask
        evaluation["nextAction"] = "ask_user"
    write_json(task_dir / "evaluation.json", evaluation)
    state = {
        "taskId": args.task_code,
        "taskType": "ios",
        "status": "finished" if evaluation["nextAction"] == "finish" else ("human_input_pending" if evaluation["nextAction"] == "ask_user" else "retry_pending" if result == "pass" else "blocked"),
        "iteration": args.iteration,
        "nextAction": evaluation["nextAction"],
        "iosDevice": env["ios"],
        "updatedAt": dt.datetime.now().isoformat(timespec="seconds"),
    }
    old = read_runtime_state(task_dir)
    old.update(state)
    write_runtime_state(task_dir, old)

    validation = task_dir / "Validation.md"
    existing = validation.read_text() if validation.exists() else "# Validation\n"
    validation.write_text(existing.rstrip() + f"""

### Iteration {args.iteration} - iOS preflight
- Time: {dt.datetime.now().isoformat(timespec='seconds')}
- Environment: device=`{device.get('deviceName', '-')}`; coreDeviceId=`{device.get('coreDeviceId', '-')}`; xcodebuildDeviceId=`{device.get('xcodebuildDeviceId', '-')}`; Developer Mode=`{device.get('developerMode', '-')}`; backlight=`{display_info.get('backlight', '-')}`.
- Commands: see `logs/iter-{args.iteration}/commands.md`.
- Result: {result.upper()}.{summary}
- Failure category: {failed[0]['category'] if failed else 'none'}.
- Evidence: 
  - `logs/iter-{args.iteration}/xcodebuild-version.log`
  - `logs/iter-{args.iteration}/xcodebuildmcp-device-list.log`
  - `logs/iter-{args.iteration}/devicectl-list.log`
  - `logs/iter-{args.iteration}/display-info.log`
  - `logs/iter-{args.iteration}/env.json`
  - `logs/iter-{args.iteration}/commands.md`
- Reusable findings: Before iOS physical-device verification, confirm Xcode/XcodeBuildMCP, visible device, Developer Mode, legacy UDID/CoreDevice ID, and display backlight.
- Avoid repeating: Do not misclassify Developer Mode/UI Automation/lockscreen/screen-off/signing device or permission issues as product-code failures.
""")
    print(json.dumps(evaluation, ensure_ascii=False, indent=2))
    return 0 if result == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
