#!/usr/bin/env python3
"""Read-only Android project probe for AutoMind.

Purpose: collect the minimum information needed before AutoMind attaches a
real Android app to the harness loop. It intentionally does not modify the
project and does not download dependencies by default.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

SENSITIVE_KEY_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|auth[_-]?token|access[_-]?token|private[_-]?key|secret[_-]?key)")
SENSITIVE_VALUE_RE = re.compile(r"(?i)(sk-[A-Za-z0-9_\-]{12,}|figd_[A-Za-z0-9_\-]{12,}|[A-Za-z0-9_\-]{24,}\.[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{12,})")


def redact_sensitive_text(text: str) -> str:
    out = []
    for line in str(text or "").splitlines():
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            if SENSITIVE_KEY_RE.search(key):
                out.append(f"{key}=<redacted>")
                continue
        out.append(SENSITIVE_VALUE_RE.sub("<redacted>", line))
    return "\n".join(out)


def tail(text: str, limit: int = 8000) -> str:
    return redact_sensitive_text((text or "")[-limit:])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", help="Android project root")
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument("--gradle-tasks", action="store_true", help="Run ./gradlew tasks --offline --all --console=plain")
    parser.add_argument("--build-command", default="", help="Optional build command to run as a gate, e.g. './gradlew :app:assembleDebug --offline'")
    parser.add_argument("--timeout", type=int, default=300)
    return parser.parse_args()


def read_text(path: Path, limit: int = 200_000) -> str:
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return ""
    return text[:limit]


def rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path)


def run_shell(command: str, cwd: Path, timeout: int, keep_stdout_for_parse: bool = False) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        result = {
            "command": command,
            "exitCode": proc.returncode,
            "stdoutTail": tail(proc.stdout),
            "stderrTail": tail(proc.stderr),
        }
        if keep_stdout_for_parse:
            result["_stdoutForParse"] = proc.stdout
            result["_stderrForParse"] = proc.stderr
        return result
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "exitCode": -1,
            "timeout": True,
            "stdoutTail": tail(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            "stderrTail": tail(exc.stderr or "") if isinstance(exc.stderr, str) else "",
        }


def classify_build(output: str, exit_code: int) -> dict[str, Any]:
    lower = output.lower()
    missing = []
    for pattern in [
        r"could not download ([^\s:]+:[^\s:]+:[^\s\)]+)",
        r"could not resolve ([^\s:]+:[^\s:]+:[^\s\)]+)",
        r"no cached version of ([^\s]+) available for offline mode",
    ]:
        for match in re.finditer(pattern, output, flags=re.IGNORECASE):
            dep = match.group(1).rstrip(".,")
            if dep not in missing:
                missing.append(dep)
    if exit_code == 0:
        return {"result": "pass", "category": "validation_failure", "detail": "ok", "summary": "build command passed"}
    if "offline mode" in lower and ("no cached version" in lower or "could not download" in lower):
        return {
            "result": "blocked",
            "category": "environment_blocked",
            "detail": "dependency_cache_missing",
            "summary": "Gradle offline build is blocked by missing cached dependencies",
            "missingDependencies": missing[:80],
        }
    if "task not found" in lower or "cannot locate tasks" in lower:
        return {"result": "blocked", "category": "needs_replan", "detail": "build_task_not_found", "summary": "requested Gradle task does not exist"}
    return {"result": "fail", "category": "build_failure", "summary": "build command failed"}


def parse_gradle_tasks(text: str) -> list[str]:
    tasks = []
    for line in text.splitlines():
        m = re.match(r"^(assemble|install|package|bundle)[A-Za-z0-9_]*\b", line.strip())
        if m:
            name = line.split()[0].rstrip(":")
            if name not in tasks:
                tasks.append(name)
    return tasks


def main() -> int:
    args = parse_args()
    root = Path(args.project).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"project does not exist: {root}")

    manifests = sorted(root.glob("**/src/main/AndroidManifest.xml"))[:120]
    build_files = sorted([
        *root.glob("settings.gradle*"),
        *root.glob("build.gradle*"),
        *root.glob("*/build.gradle*"),
        *root.glob("*/*/build.gradle*"),
    ])[:240]

    manifest_info = []
    package_candidates = []
    launcher_candidates = []
    for manifest in manifests:
        text = read_text(manifest)
        pkg_match = re.search(r'package\s*=\s*"([^"]+)"', text)
        pkg = pkg_match.group(1) if pkg_match else ""
        if pkg and pkg not in package_candidates:
            package_candidates.append(pkg)
        has_launcher = "android.intent.action.MAIN" in text and "android.intent.category.LAUNCHER" in text
        if has_launcher:
            launcher_candidates.append(rel(root, manifest))
        manifest_info.append({"path": rel(root, manifest), "package": pkg, "hasLauncherIntent": has_launcher})

    build_text = "\n".join(read_text(p, 50_000) for p in build_files)
    app_ids = []
    namespaces = []
    for m in re.finditer(r"applicationId\s+[\"']([^\"']+)[\"']", build_text):
        if m.group(1) not in app_ids:
            app_ids.append(m.group(1))
    for m in re.finditer(r"namespace\s+[\"']([^\"']+)[\"']", build_text):
        if m.group(1) not in namespaces:
            namespaces.append(m.group(1))

    gradlew = root / "gradlew"
    result: dict[str, Any] = {
        "project": str(root),
        "readOnly": True,
        "gradleWrapper": rel(root, gradlew) if gradlew.exists() else "missing",
        "buildFiles": [rel(root, p) for p in build_files],
        "manifestSummary": manifest_info,
        "packageCandidates": package_candidates,
        "applicationIdCandidates": app_ids,
        "namespaceCandidates": namespaces,
        "launcherManifestCandidates": launcher_candidates,
        "apkOutputs": [rel(root, p) for p in sorted(root.glob("**/build/outputs/**/*.apk"))[:120]],
        "gradleTasks": [],
        "buildGate": None,
        "recommendation": [],
    }

    if args.gradle_tasks and gradlew.exists():
        task_run = run_shell("./gradlew :app:tasks --offline --all --console=plain", root, args.timeout, keep_stdout_for_parse=True)
        result["gradleTasks"] = parse_gradle_tasks(task_run.get("_stdoutForParse", "") + "\n" + task_run.get("_stderrForParse", ""))
        task_run.pop("_stdoutForParse", None)
        task_run.pop("_stderrForParse", None)
        result["gradleTasksRun"] = task_run

    if args.build_command:
        gate = run_shell(args.build_command, root, args.timeout)
        gate["classification"] = classify_build(gate.get("stdoutTail", "") + "\n" + gate.get("stderrTail", ""), gate["exitCode"])
        result["buildGate"] = gate

    if result["apkOutputs"]:
        result["recommendation"].append("Existing APK outputs found; prefer install/launch probe before rebuilding if artifact is fresh enough.")
    elif result["buildGate"] and result["buildGate"]["classification"].get("detail") == "dependency_cache_missing":
        result["recommendation"].append("Build is blocked by missing offline Gradle dependency cache; do not classify as app code failure.")
        result["recommendation"].append("Ask before running online dependency resolution or changing project/local Gradle config.")
    else:
        result["recommendation"].append("Use the smallest app assemble task exposed by Gradle before attaching probe-flow runner.")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
