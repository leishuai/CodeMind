"""Mobile/probe-flow CLI command handlers."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from typing import Optional

from orchestrator.config import AUTOMIND_ROOT, AUTOMIND_WORKSPACE_ROOT, TASKS_DIR
from orchestrator.evaluation_result import apply_evaluation_result
from orchestrator.harness_profiles import get_harness_profile
from orchestrator.probe_records import init_task_artifacts, write_json_file, write_probe_record
from orchestrator.validation_history import append_validation_history
from orchestrator.console import error, log
from orchestrator.state import ensure_dir, get_task_dir, read_evaluation_json, read_runtime_state, write_evaluation_json, write_runtime_state

def _run_android_probe_flow_evaluator(*args, **kwargs):
    from orchestrator.main import run_android_probe_flow_evaluator
    return run_android_probe_flow_evaluator(*args, **kwargs)


def cmd_ios_project_probe(project: str, task_code: str = "ios_project_probe", extra_args: Optional[list[str]] = None):
    """Run read-only iOS project probe and write standard AutoMind task artifacts."""
    extra_args = extra_args or []
    task_dir = get_task_dir(task_code)
    iter_dir = task_dir / "logs" / "iter-1"
    init_task_artifacts(
        task_dir,
        "iOS Project Probe",
        f"Read-only probe for iOS project: `{project}`.",
        "Run read-only iOS project discovery and classify readiness for AutoMind attachment.",
    )
    out = iter_dir / "ios-project-probe.json"
    script = AUTOMIND_ROOT / "scripts" / "ios_project_probe.py"
    command = [sys.executable, str(script), project, "--out", str(out), *extra_args]
    proc = subprocess.run(command, cwd=str(AUTOMIND_WORKSPACE_ROOT), text=True, capture_output=True)
    output = proc.stdout + proc.stderr
    (iter_dir / "evaluator.log").write_text(output)
    if proc.returncode != 0:
        error("iOS project probe failed")
        print(output[-4000:])
        sys.exit(proc.returncode)
    probe = json.loads(out.read_text())
    issues = probe.get("issues") or []
    result = "blocked" if issues else "pass"
    failed = [
        {
            "name": "ios_project_probe",
            "category": issue.get("category", "needs_replan"),
            "reason": issue.get("reason", "issue"),
            "evidence": "logs/iter-1/ios-project-probe.json",
        }
        for issue in issues
    ]
    summary = "iOS project probe blocked" if issues else "iOS project probe passed"
    category = failed[0]["category"] if failed else "none"
    evaluation = {
        "iteration": 1,
        "result": result,
        "summary": summary,
        "failedChecks": failed,
        "evidence": [
            {"type": "other", "note": "ios-project-probe", "path": "logs/iter-1/ios-project-probe.json"},
            {"type": "log", "path": "logs/iter-1/evaluator.log"},
            {"type": "other", "note": "env", "path": "logs/iter-1/env.json"},
            {"type": "command", "path": "logs/iter-1/commands.md"},
        ],
        "nextAction": "replan" if issues else "finish",
    }
    write_evaluation_json(task_dir, evaluation)
    write_runtime_state(task_dir, {
        "taskId": task_code,
        "taskType": "ios",
        "status": "blocked" if issues else "finished",
        "iteration": 1,
        "nextAction": evaluation["nextAction"],
        "iosProjectProbe": probe,
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
    })
    write_json_file(iter_dir / "env.json", {"taskCode": task_code, "projectPath": project, "cwd": str(AUTOMIND_WORKSPACE_ROOT)})
    write_probe_record(
        task_dir,
        1,
        "iOS project probe",
        f"project=`{project}`; cwd=`{AUTOMIND_WORKSPACE_ROOT}`; runtime=`{AUTOMIND_ROOT}`",
        ["./automind.sh", "ios-project-probe", project, task_code, *extra_args],
        "logs/iter-1/ios-project-probe.json",
        "logs/iter-1/evaluator.log",
        result,
        summary,
        category,
        "Run read-only project/workspace/scheme/build-settings probe before attaching a real iOS project.",
        "Do not run Generator or modify the project before scheme/signing/test target are clear.",
    )
    print(json.dumps({"task": task_code, "result": result, "nextAction": evaluation["nextAction"], "probe": str(out), "issues": issues}, ensure_ascii=False, indent=2))

def cmd_ios_command_probe(workspace: str, task_code: str = "ios_command_probe", extra_args: Optional[list[str]] = None):
    """Run read-only iOS command-surface probe and write standard AutoMind task artifacts."""
    extra_args = extra_args or []
    task_dir = get_task_dir(task_code)
    iter_dir = task_dir / "logs" / "iter-1"
    init_task_artifacts(
        task_dir,
        "iOS Command Probe",
        f"Read-only command-surface probe for iOS workspace: `{workspace}`.",
        "Probe custom workspace wrapper/Bazel/CocoaPods command surface with read-only commands only.",
    )
    out = iter_dir / "ios-command-probe.json"
    script = AUTOMIND_ROOT / "scripts" / "ios_command_probe.py"
    command = [sys.executable, str(script), workspace, "--out", str(out), *extra_args]
    proc = subprocess.run(command, cwd=str(AUTOMIND_WORKSPACE_ROOT), text=True, capture_output=True)
    output = proc.stdout + proc.stderr
    (iter_dir / "evaluator.log").write_text(output)
    if proc.returncode != 0:
        error("iOS command probe failed")
        print(output[-4000:])
        sys.exit(proc.returncode)
    probe = json.loads(out.read_text())
    issues = probe.get("issues") or []
    result = "blocked" if issues else "pass"
    failed = [
        {
            "name": "ios_command_probe",
            "category": issue.get("category", "needs_replan"),
            "reason": issue.get("reason", "issue"),
            "evidence": "logs/iter-1/ios-command-probe.json",
        }
        for issue in issues
    ]
    summary = "iOS command probe blocked" if issues else "iOS command probe passed"
    category = failed[0]["category"] if failed else "none"
    evaluation = {
        "iteration": 1,
        "result": result,
        "summary": summary,
        "failedChecks": failed,
        "evidence": [
            {"type": "other", "note": "ios-command-probe", "path": "logs/iter-1/ios-command-probe.json"},
            {"type": "log", "path": "logs/iter-1/evaluator.log"},
            {"type": "other", "note": "env", "path": "logs/iter-1/env.json"},
            {"type": "command", "path": "logs/iter-1/commands.md"},
        ],
        "nextAction": "replan" if issues else "finish",
    }
    write_evaluation_json(task_dir, evaluation)
    write_runtime_state(task_dir, {
        "taskId": task_code,
        "taskType": "ios",
        "status": "blocked" if issues else "finished",
        "iteration": 1,
        "nextAction": evaluation["nextAction"],
        "iosCommandProbe": probe,
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
    })
    write_json_file(iter_dir / "env.json", {"taskCode": task_code, "workspacePath": workspace, "cwd": str(AUTOMIND_WORKSPACE_ROOT)})
    write_probe_record(
        task_dir,
        1,
        "iOS command probe",
        f"workspace=`{workspace}`; cwd=`{AUTOMIND_WORKSPACE_ROOT}`; runtime=`{AUTOMIND_ROOT}`",
        ["./automind.sh", "ios-command-probe", workspace, task_code, *extra_args],
        "logs/iter-1/ios-command-probe.json",
        "logs/iter-1/evaluator.log",
        result,
        summary,
        category,
        "Probe command surface before install/build/generate in large custom workspace wrapper/Bazel/CocoaPods iOS workspaces.",
        "Prefer safe status/help/file clues; do not run pod install, project generation, build/test, or device install until command paths are clear.",
    )
    print(json.dumps({"task": task_code, "result": result, "nextAction": evaluation["nextAction"], "probe": str(out), "issues": issues}, ensure_ascii=False, indent=2))

def cmd_android_project_probe(project: str, task_code: str = "android_project_probe"):
    """Run a read-only Android project preflight probe and store the result as an AutoMind task artifact."""
    task_dir = TASKS_DIR / task_code
    ensure_dir(task_dir)
    out = task_dir / "android-project-probe.json"
    script = AUTOMIND_ROOT / "scripts" / "android_project_probe.py"
    if not script.exists():
        error(f"Android project probe script not found: {script}")
        sys.exit(1)

    command = [
        sys.executable,
        str(script),
        project,
        "--out",
        str(out),
        "--gradle-tasks",
        "--build-command",
        "./gradlew :app:assembleDebug --offline --console=plain -Pandroid.aapt2FromMavenOverride=$HOME/Library/Android/sdk/build-tools/35.0.0/aapt2",
        "--timeout",
        "420",
    ]
    log("Android project read-only probe: " + project)
    proc = subprocess.run(command, cwd=str(AUTOMIND_WORKSPACE_ROOT), text=True, capture_output=True)
    (task_dir / "android-project-probe.log").write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        error("Android project probe failed")
        print((proc.stdout + proc.stderr)[-4000:])
        sys.exit(proc.returncode)

    probe = json.loads(out.read_text())
    build_gate = probe.get("buildGate") or {}
    classification = build_gate.get("classification") or {}
    raw_result = classification.get("result", "unknown")
    next_action = "finish" if raw_result == "pass" else ("retry_generator" if raw_result in {"fail", "blocked"} else "replan")
    raw_category = classification.get("category")
    failed_category = raw_category if raw_category in {
        "agent_unavailable",
        "agent_timeout",
        "invalid_evaluation_output",
        "environment_blocked",
        "build_failure",
        "install_failure",
        "launch_failure",
        "test_failure",
        "validation_failure",
        "mobile_device_unavailable",
        "tool_missing",
        "tool_limitation",
        "permission_blocked",
        "old_team_signing_available",
        "signing_material_blocked",
        "provisioning_profile_blocked",
        "needs_replan",
        "unknown",
        "no_progress",
    } else "unknown"
    write_runtime_state(task_dir, {
        "taskId": task_code,
        "userInput": f"Read-only Android project probe: {project}",
        "taskType": "android",
        "harnessProfile": get_harness_profile("android"),
        "status": "finished" if raw_result == "pass" else ("retry_pending" if next_action == "retry_generator" else "replan_pending"),
        "iteration": 1,
        "currentOwner": "supervisor",
        "nextAction": "finish" if raw_result == "pass" else next_action,
        "lastResult": raw_result,
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "androidProjectProbe": str(out),
    })
    evaluation = {
        "iteration": 1,
        "result": raw_result,
        "summary": classification.get("summary", "Android project probe completed"),
        "failedChecks": [] if raw_result == "pass" else [{
            "name": "android_project_build_gate",
            "reason": classification.get("summary", "build gate did not pass"),
            "category": failed_category,
            "detail": classification.get("detail") or raw_category,
            "evidence": str(out),
        }],
        "evidence": [{"type": "other", "note": "android-project-probe", "path": str(out)}],
        "nextAction": next_action,
    }
    write_evaluation_json(task_dir, evaluation)
    print(json.dumps({
        "task": task_code,
        "probe": str(out),
        "classification": classification,
        "recommendation": probe.get("recommendation", []),
    }, ensure_ascii=False, indent=2))

def cmd_android_probe_flow(task_code: str, iteration: Optional[int] = None, dry_run: bool = False, retries: int = 0):
    """Run Android dynamic probe-flow evaluator for an existing AutoMind task."""
    task_dir = TASKS_DIR / task_code
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)

    state = read_runtime_state(task_dir) or {}
    if iteration is None:
        iteration = int(state.get("iteration", 0) or 0) + 1
    iter_log_dir = task_dir / "logs" / f"iter-{iteration}"

    old_dry_run = os.environ.get("AUTOMIND_ANDROID_PROBE_DRY_RUN")
    if dry_run:
        os.environ["AUTOMIND_ANDROID_PROBE_DRY_RUN"] = "1"
    try:
        code, output = _run_android_probe_flow_evaluator(task_dir, iteration, iter_log_dir)
    finally:
        if dry_run:
            if old_dry_run is None:
                os.environ.pop("AUTOMIND_ANDROID_PROBE_DRY_RUN", None)
            else:
                os.environ["AUTOMIND_ANDROID_PROBE_DRY_RUN"] = old_dry_run

    evaluation = read_evaluation_json(task_dir) or {}
    if evaluation:
        append_validation_history(
            task_dir,
            iteration,
            evaluation.get("result", "unknown"),
            evaluation.get("summary", "Android probe-flow evaluator completed"),
            evaluation.get("nextAction", "stop"),
        )
        apply_evaluation_result(task_dir, evaluation)

    print(json.dumps({
        "task": task_code,
        "iteration": iteration,
        "dryRun": dry_run,
        "retries": retries,
        "result": evaluation.get("result", "unknown"),
        "nextAction": evaluation.get("nextAction", "unknown"),
        "evaluation": str(task_dir / "evaluation.json"),
        "logDir": str(iter_log_dir),
        "runnerExit": code,
        "outputTail": output[-2000:],
    }, ensure_ascii=False, indent=2))
    if code != 0:
        sys.exit(code)
