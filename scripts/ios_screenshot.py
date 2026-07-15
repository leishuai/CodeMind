#!/usr/bin/env python3
"""iOS physical screenshot evaluator for CodeMind.

Primary backend: pymobiledevice3 developer dvt screenshot over an already running
RSD/tunneld. Starting tunneld may require sudo, so this evaluator does not start
it automatically; if unavailable, it emits askUserQuestion.
"""

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

from automind_paths import IOS_TOOLS_PY, RUNTIME_ROOT, TASKS_DIR, WORKSPACE_ROOT, venv_requirements_current

ROOT = RUNTIME_ROOT
IOS_PY = IOS_TOOLS_PY


def run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
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


def load_task_state(task_dir: pathlib.Path) -> dict[str, Any]:
    path = task_dir / "runtime-state.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def resolve_device_id(task_dir: pathlib.Path, cli_device_id: str | None) -> str:
    if cli_device_id:
        return cli_device_id
    state = load_task_state(task_dir)
    ios = state.get("iosApp") or state.get("iosDevice") or {}
    candidates = [
        ios.get("traditionalDeviceId") if isinstance(ios, dict) else None,
        ios.get("xcodebuildDeviceId") if isinstance(ios, dict) else None,
        ios.get("deviceId") if isinstance(ios, dict) else None,
        state.get("traditionalDeviceId"),
        state.get("xcodebuildDeviceId"),
        state.get("deviceId"),
    ]
    for value in candidates:
        if value:
            return str(value)
    return ""


def ios_python_ready() -> tuple[bool, str]:
    if not IOS_PY.exists():
        return False, f"Missing iOS tools venv Python: {IOS_PY}"
    code, out = run([str(IOS_PY), "-c", "import pymobiledevice3"], timeout=20)
    if code != 0:
        return False, "pymobiledevice3 is not available in .venv-ios-tools"
    return True, "ready"


def run_ios_tools_auto_setup(iter_dir: pathlib.Path) -> tuple[int, str, dict[str, Any]]:
    cmd = [sys.executable, str(ROOT / "orchestrator" / "main.py"), "setup-automation-tools", "ios"]
    code, out = run(cmd, timeout=900)
    (iter_dir / "ios-tools-auto-setup.log").write_text(out)
    (iter_dir / "ios-tools-auto-setup.exit-code.txt").write_text(str(code) + "\n")
    try:
        report = json.loads(out)
    except Exception:
        report = {}
    return code, out, report


def build_ios_tools_ask(reason: str) -> dict[str, Any]:
    return {
        "question": "iOS screenshot helper tools are still unavailable after CodeMind tried local helper setup. What should happen next?",
        "reason": reason + " CodeMind can auto-create .venv-ios-tools for low-risk Python helper packages, but pymobiledevice3 is still unavailable. This may require fixing network/proxy/Python/pip, or skipping physical screenshot evidence.",
        "options": [
            {"id": "A", "label": "I will fix Python/pip/network and retry setup.", "impact": "Keeps physical screenshot capability.", "requiresConfirmation": False},
            {"id": "B", "label": "Skip physical-device screenshot.", "impact": "Continue with XCUITest/devicectl/log evidence if sufficient.", "requiresConfirmation": False},
            {"id": "C", "label": "Stop", "impact": "Keep the task blocked.", "requiresConfirmation": False},
        ],
        "recommended": "A",
        "setupCommand": "automind setup-automation-tools ios",
        "retryCommand": "automind ios-screenshot <task-code> [iteration] --device-id <udid>",
        "defaultAction": "stop",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture iOS physical screenshot")
    parser.add_argument("task_code")
    parser.add_argument("iteration", nargs="?", type=int, default=1)
    parser.add_argument("--device-id", default=None, help="xcodebuild/traditional UDID for pymobiledevice3 --tunnel. If omitted, CodeMind reads runtime-state iosApp/iosDevice fields.")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    task_dir = TASKS_DIR / args.task_code
    iter_dir = task_dir / "logs" / f"iter-{args.iteration}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    output = pathlib.Path(args.output) if args.output else iter_dir / "ios-physical-screenshot.png"
    if not output.is_absolute():
        output = WORKSPACE_ROOT / output

    device_id = resolve_device_id(task_dir, args.device_id)
    if not device_id:
        cmd = [str(IOS_PY), "-m", "pymobiledevice3", "developer", "dvt", "screenshot", "--tunnel", "<device-id>", str(output)]
        code = 2
        out = "Missing iOS device id. Pass --device-id or set runtime-state iosApp.traditionalDeviceId / iosApp.xcodebuildDeviceId."
    else:
        cmd = [str(IOS_PY), "-m", "pymobiledevice3", "developer", "dvt", "screenshot", "--tunnel", device_id, str(output)]
    (iter_dir / "commands.md").write_text("# Commands\n\n```bash\n" + " ".join(cmd) + "\n```\n")

    tools_ok, tools_reason = ios_python_ready() if device_id else (False, "")
    if device_id and tools_ok and not venv_requirements_current("ios"):
        tools_ok, tools_reason = False, "requirements changed since .venv-ios-tools was built (stale stamp)"
    auto_setup_attempted = False
    auto_setup_report: dict[str, Any] | None = None
    if not device_id:
        pass
    elif not tools_ok:
        auto_setup_attempted = True
        _setup_code, _setup_out, auto_setup_report = run_ios_tools_auto_setup(iter_dir)
        tools_ok, tools_reason_after = ios_python_ready()
        if tools_ok:
            code, out = run(cmd, timeout=90)
        else:
            code, out = 127, tools_reason_after or tools_reason
    else:
        code, out = run(cmd, timeout=90)
    (iter_dir / "ios-screenshot.log").write_text(out)
    (iter_dir / "evaluator.log").write_text(out)
    (iter_dir / "exit-code.txt").write_text(str(code) + "\n")

    ok = code == 0 and output.exists() and output.stat().st_size > 0
    failed = []
    ask = None
    if ok:
        result = "pass"
        next_action = "finish"
        summary = f"iOS physical screenshot captured: {output}"
    else:
        result = "blocked"
        next_action = "replan"
        lower = out.lower()
        if not device_id:
            reason = "Missing iOS device id. Pass --device-id or configure runtime-state iosApp.traditionalDeviceId / iosApp.xcodebuildDeviceId."
            category = "mobile_device_unavailable"
            next_action = "ask_user"
            ask = {
                "question": "Which iOS device id should CodeMind use for screenshot capture?",
                "reason": "The screenshot runner needs a traditional/xcodebuild UDID for pymobiledevice3 --tunnel, and no device id was provided or found in runtime-state.json.",
                "options": [
                    {"id": "A", "label": "I will provide --device-id and retry.", "impact": "Continue screenshot capture with explicit device selection.", "requiresConfirmation": False},
                    {"id": "B", "label": "Skip physical screenshot.", "impact": "Keep other launch/log/test evidence without screenshot.", "requiresConfirmation": False},
                ],
                "recommended": "A",
                "defaultAction": "stop",
            }
        elif "unable to connect to tunneld" in lower or "tunneld" in lower or "requires root" in lower:
            reason = "pymobiledevice3 screenshot requires a running tunneld; starting tunneld may require sudo/root privileges."
            category = "permission_blocked"
            next_action = "ask_user"
            ask = {
                "question": "Has pymobiledevice3 tunneld been started, or may CodeMind temporarily start it with sudo?",
                "reason": "iOS 17+/18 physical-device screenshots require RSD/tunneld. Without it, the developer screenshot service cannot be reached.",
                "options": [
                    {"id": "A", "label": "I have manually started tunneld; retry screenshot.", "impact": "Continue automatic screenshot capture.", "requiresConfirmation": False},
                    {"id": "B", "label": "Allow temporarily starting tunneld with sudo.", "impact": "May enable the automatic screenshot backend.", "risk": "Requires administrator permission and starts a local tunnel service.", "requiresConfirmation": True},
                    {"id": "C", "label": "Skip physical-device screenshot and keep XCUITest/log/display evidence.", "impact": "Does not block the main path.", "requiresConfirmation": False},
                ],
                "recommended": "A",
                "defaultAction": "stop",
            }
        elif "missing ios tools venv python" in lower or "pymobiledevice3 is not available" in lower:
            reason = out.strip() or "pymobiledevice3 is not available in the local iOS tools environment."
            category = "tool_missing"
            next_action = "ask_user"
            ask = build_ios_tools_ask(reason)
        else:
            reason = "iOS physical screenshot command failed; see log."
            category = "tool_limitation"
        summary = reason
        failed.append({"name": "ios physical screenshot", "category": category, "reason": reason, "exitCode": code, "evidence": [f"logs/iter-{args.iteration}/ios-screenshot.log"]})

    env = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "cwd": str(WORKSPACE_ROOT),
        "os": platform.platform(),
        "tools": {"pymobiledevice3": str(IOS_PY), "pymobiledevice3Ready": tools_ok, "python": sys.executable},
        "ios": {"xcodebuildDeviceId": device_id or None},
        "screenshot": {"path": str(output), "bytes": output.stat().st_size if output.exists() else 0},
        "autoSetup": {
            "attempted": auto_setup_attempted,
            "target": "ios" if auto_setup_attempted else None,
            "requirements": "requirements/ios-tools.txt" if auto_setup_attempted else None,
            "report": auto_setup_report,
        },
    }
    write_json(iter_dir / "env.json", env)
    evidence_items = [
        {"type": "log", "path": f"logs/iter-{args.iteration}/ios-screenshot.log"},
        {"type": "screenshot", "path": str(output)},
        {"type": "log", "path": f"logs/iter-{args.iteration}/evaluator.log"},
        {"type": "other", "note": "env", "path": f"logs/iter-{args.iteration}/env.json"},
        {"type": "command", "path": f"logs/iter-{args.iteration}/commands.md"},
    ]
    if auto_setup_attempted:
        evidence_items.append({"type": "log", "note": "ios-tools-auto-setup", "path": f"logs/iter-{args.iteration}/ios-tools-auto-setup.log"})
    evaluation = {
        "iteration": args.iteration,
        "result": result,
        "summary": summary,
        "failedChecks": failed,
        "evidence": evidence_items,
        "nextAction": next_action,
    }
    if ask:
        evaluation["askUserQuestion"] = ask
    write_json(task_dir / "evaluation.json", evaluation)
    state_path = task_dir / "runtime-state.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except Exception:
            pass
    state.update({"taskId": args.task_code, "taskType": "ios", "status": "finished" if ok else ("human_input_pending" if next_action == "ask_user" else "blocked"), "iteration": args.iteration, "nextAction": next_action, "updatedAt": dt.datetime.now().isoformat(timespec="seconds")})
    write_json(state_path, state)

    validation = task_dir / "Validation.md"
    existing = validation.read_text() if validation.exists() else "# Validation\n"
    validation.write_text(existing.rstrip() + f"""

### Iteration {args.iteration} - iOS physical screenshot
- Time: {dt.datetime.now().isoformat(timespec='seconds')}
- Environment: backend=`pymobiledevice3 developer dvt screenshot --tunnel`; device id=`{device_id or 'not configured'}`.
- Commands: see `logs/iter-{args.iteration}/commands.md`.
- Result: {result.upper()}.{summary}
- Failure category: {failed[0]['category'] if failed else 'none'}.
- Evidence: 
  - `logs/iter-{args.iteration}/ios-screenshot.log`
  - `{output}`
  - `logs/iter-{args.iteration}/env.json`
  - `logs/iter-{args.iteration}/commands.md`
- Reusable findings: For iOS 17+/18 screenshots, use pymobiledevice3 with running RSD/tunneld; starting tunneld may require sudo and must be explicitly confirmed by a human.
- Avoid repeating: Do not use non-tunnel `idevicescreenshot` as the main path for iOS 18.
""")
    print(json.dumps(evaluation, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
