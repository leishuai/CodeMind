#!/usr/bin/env python3
"""Read-only iOS workspace command-surface probe for AutoMind.

This public runner discovers common iOS build/run command surfaces without
installing dependencies, generating projects, building, or mutating the target
repository. It intentionally avoids private/company-specific wrappers.
"""

from __future__ import annotations

import argparse
import re
import json
import os
import pathlib
import shutil
import subprocess
import time
from typing import Any


def run(cmd: list[str], cwd: pathlib.Path, timeout: int) -> dict[str, Any]:
    started = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=timeout)
        return {
            "command": cmd,
            "cwd": str(cwd),
            "exitCode": p.returncode,
            "durationMs": int((time.time() - started) * 1000),
            "stdoutTail": (p.stdout or "")[-16000:],
            "stderrTail": (p.stderr or "")[-16000:],
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "command": cmd,
            "cwd": str(cwd),
            "exitCode": 124,
            "timeout": True,
            "durationMs": int((time.time() - started) * 1000),
            "stdoutTail": stdout[-16000:],
            "stderrTail": stderr[-16000:],
        }


def rel(root: pathlib.Path, path: pathlib.Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path)


def discover_files(root: pathlib.Path) -> dict[str, Any]:
    names = {
        "Podfile",
        "Podfile.lock",
        "Cartfile",
        "Cartfile.resolved",
        "Package.swift",
        "Package.resolved",
        "project.yml",
        "Tuist.swift",
        "Gemfile",
        "Gemfile.lock",
        "Fastfile",
        "Makefile",
        "WORKSPACE",
        "MODULE.bazel",
        "BUILD",
        "BUILD.bazel",
        ".bazelrc",
    }
    skip_dirs = {"Pods", "DerivedData", ".git", ".build", "Carthage", "build"}
    found: list[str] = []
    xcodeproj: list[str] = []
    xcworkspace: list[str] = []
    scripts: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirpath_p = pathlib.Path(dirpath)
        if set(dirpath_p.parts) & skip_dirs or any(part.startswith("bazel-") for part in dirpath_p.parts):
            dirnames[:] = []
            continue
        depth = len(dirpath_p.relative_to(root).parts)
        if depth >= 4:
            dirnames[:] = []
        for dirname in list(dirnames):
            path = dirpath_p / dirname
            if dirname in skip_dirs or dirname.startswith("bazel-"):
                continue
            if dirname.endswith(".xcodeproj"):
                xcodeproj.append(rel(root, path))
            elif dirname.endswith(".xcworkspace"):
                xcworkspace.append(rel(root, path))
        for filename in filenames:
            path = dirpath_p / filename
            if filename in names:
                found.append(rel(root, path))
            if filename.endswith(".sh") and len(scripts) < 80:
                scripts.append(rel(root, path))
    return {
        "commandHintFiles": sorted(set(found))[:300],
        "xcodeproj": sorted(set(xcodeproj))[:80],
        "xcworkspace": sorted(set(xcworkspace))[:80],
        "scripts": sorted(set(scripts))[:120],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", help="Path to iOS workspace/repo root")
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--include-help", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    root = pathlib.Path(args.workspace).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"workspace path not found: {root}")

    result: dict[str, Any] = {
        "workspaceRoot": str(root),
        "readOnly": True,
        "mutatingCommandsSkipped": [
            "pod install",
            "bundle install",
            "carthage bootstrap/update",
            "swift package resolve/update",
            "tuist generate",
            "xcodebuild build/test/archive",
            "bazel build/test/run",
            "install/launch on physical device",
        ],
        "tools": {},
        "files": discover_files(root),
        "commands": {},
        "parsed": {},
        "issues": [],
        "warnings": [],
        "recommendation": [],
    }

    for tool in ["xcodebuild", "xcrun", "pod", "bundle", "ruby", "swift", "tuist", "bazel", "make"]:
        result["tools"][tool] = shutil.which(tool)

    if result["tools"].get("xcodebuild"):
        result["commands"]["xcodebuild_version"] = run(["xcodebuild", "-version"], root, args.timeout)
    else:
        result["issues"].append({"category": "tool_missing", "reason": "xcodebuild command not found on PATH."})

    if args.include_help:
        if result["tools"].get("pod"):
            result["commands"]["pod_version"] = run(["pod", "--version"], root, args.timeout)
        if result["tools"].get("swift"):
            result["commands"]["swift_version"] = run(["swift", "--version"], root, args.timeout)
        if result["tools"].get("bazel"):
            result["commands"]["bazel_version"] = run(["bazel", "--version"], root, args.timeout)

    hints = result["files"]["commandHintFiles"]
    if any(name.endswith("Podfile") or name.endswith("Podfile.lock") for name in hints):
        result["recommendation"].append("CocoaPods files detected; inspect Podfile/lockfile and prefer repo-documented install/build commands before raw pod changes.")
    if any(name.endswith("Package.swift") for name in hints):
        result["recommendation"].append("Swift Package manifest detected; inspect package targets and prefer project-documented resolve/build commands.")
    if any("BUILD" in name or name.endswith("WORKSPACE") or name.endswith("MODULE.bazel") for name in hints):
        result["recommendation"].append("Bazel files detected; use the repository-documented Bazel command path and avoid guessing build targets.")
    if result["files"].get("xcworkspace"):
        result["recommendation"].append("Xcode workspace detected; prefer workspace+scheme commands over raw project commands when appropriate.")
    if result["files"].get("xcodeproj"):
        result["recommendation"].append("Xcode project detected; run read-only scheme/build-setting discovery before build/test.")
    result["recommendation"].append("Do not run dependency install, project generation, build/test, or device install until the command path is chosen and checkpointed.")

    out = pathlib.Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
