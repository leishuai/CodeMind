"""Warm build and incremental compilation cache for CodeAutonomy tasks.

This module handles:
1. Warm build trigger conditions (native apps requiring runtime verification)
2. Background async warm build execution
3. Cache hit detection and incremental build validation
4. State persistence in runtime-state.json
5. Failure fallback to full compilation

Warm build runs during the planning phase (before Generator starts) to:
- Pre-resolve dependencies (pod install, gradle sync)
- Pre-compile third-party libraries
- Prime build caches (derived data, gradle cache)

The main loop can then use incremental compilation in the Evaluator phase.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from orchestrator.config import AUTOMIND_WORKSPACE_ROOT
from orchestrator.console import error, log, warn
from orchestrator.state import append_progress_log, ensure_dir, read_runtime_state, update_runtime_state
from orchestrator.ui_path_cache import start_ui_exploration
from orchestrator.metrics import get_metrics

WARM_BUILD_MAX_WAIT_SECONDS = 30
WARM_BUILD_LOG_DIR_NAME = "warm-build"

IOS_STRUCTURAL_FILES = {"Podfile", "Podfile.lock", "*.podspec", ".xcconfig", "*.xcodeproj", "*.xcworkspace"}
ANDROID_STRUCTURAL_FILES = {"build.gradle", "settings.gradle", "gradle.properties", "*.gradle", "buildSrc"}
WEB_STRUCTURAL_FILES = {"package.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json", "bower.json"}

PLATFORM_STRUCTURAL_FILES = {
    "ios": IOS_STRUCTURAL_FILES,
    "android": ANDROID_STRUCTURAL_FILES,
    "web": WEB_STRUCTURAL_FILES,
}

BUILD_SYSTEM_COMMANDS = {
    "ios": ["xcodebuild", "xcrun"],
    "android": ["./gradlew", "gradle", "gradlew"],
    "web": ["npm", "yarn", "pnpm"],
}


def get_warm_build_log_dir(task_dir: Path) -> Path:
    """Return the warm build log directory path."""
    return task_dir / "logs" / WARM_BUILD_LOG_DIR_NAME


def _workspace_root() -> Path:
    """Resolve the workspace root at call time.

    Reads the environment first so tests and runtime overrides via
    AUTOMIND_WORKSPACE_ROOT / AUTOMIND_PROJECT_ROOT take effect, falling back
    to the module-level default resolved at import time.
    """
    raw = os.environ.get("AUTOMIND_WORKSPACE_ROOT") or os.environ.get("AUTOMIND_PROJECT_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return AUTOMIND_WORKSPACE_ROOT


def get_warm_build_status(task_dir: Path) -> dict:
    """Read the current warm build status from runtime-state.json."""
    state = read_runtime_state(task_dir) or {}
    return state.get("warmBuild") if isinstance(state.get("warmBuild"), dict) else {}


def _update_warm_build_state(task_dir: Path, **kwargs) -> None:
    """Update warmBuild section in runtime-state.json."""
    state = read_runtime_state(task_dir) or {}
    warm_build = state.get("warmBuild") if isinstance(state.get("warmBuild"), dict) else {}
    warm_build.update(kwargs)
    warm_build["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    update_runtime_state(task_dir, warmBuild=warm_build)


def detect_platform(task_dir: Path) -> Optional[str]:
    """Detect the project platform from workspace structure.
    
    Returns: "ios", "android", "web", or None if ambiguous.
    """
    raw = os.environ.get("AUTOMIND_WORKSPACE_ROOT") or os.environ.get("AUTOMIND_PROJECT_ROOT")
    if raw:
        workspace_root = Path(raw).expanduser().resolve()
    else:
        workspace_root = AUTOMIND_WORKSPACE_ROOT
    candidates = []
    for root, dirs, files in os.walk(str(workspace_root), topdown=True):
        dirs[:] = [d for d in dirs if d not in {".git", ".automind", "node_modules", "Pods", "build", "dist", "DerivedData", ".venv*"}]
        
        for f in files:
            if f.endswith(".xcodeproj") or f.endswith(".xcworkspace"):
                candidates.append("ios")
            elif f.endswith(".gradle") or f == "settings.gradle":
                candidates.append("android")
            elif f == "package.json":
                candidates.append("web")
            elif f.endswith(".swift") or f.endswith(".m") or f.endswith(".h"):
                candidates.append("ios")
            elif f.endswith(".kt") or f.endswith(".java"):
                candidates.append("android")
            elif f.endswith(".ts") or f.endswith(".tsx") or f.endswith(".jsx"):
                candidates.append("web")
        
        if candidates:
            break
    
    if not candidates:
        return None
    
    platform_counts = {"ios": 0, "android": 0, "web": 0}
    for c in candidates:
        platform_counts[c] += 1
    
    max_count = max(platform_counts.values())
    platforms_with_max = [p for p, cnt in platform_counts.items() if cnt == max_count]
    
    return platforms_with_max[0] if len(platforms_with_max) == 1 else None


def _has_runtime_testcases(task_dir: Path) -> bool:
    """Check if TestCases.md contains required runtime-level verification."""
    test_cases_path = task_dir / "TestCases.md"
    if not test_cases_path.exists():
        return False
    
    content = test_cases_path.read_text(errors="ignore")
    lines = content.splitlines()
    
    runtime_level_idx = None
    required_idx = None
    
    for line in lines:
        if "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 6:
            continue
        
        if runtime_level_idx is None or required_idx is None:
            for i, part in enumerate(parts):
                header = part.strip().lower()
                if "runtime" in header and "level" in header:
                    runtime_level_idx = i
                elif header in {"required?", "required"}:
                    required_idx = i
            continue
        
        runtime_level = parts[runtime_level_idx].strip().lower()
        required = parts[required_idx].strip().lower()
        
        if "runtime" in runtime_level and required == "yes":
            return True
    
    return False


def _build_system_available(platform: str) -> bool:
    """Check if the build system commands are available."""
    commands = BUILD_SYSTEM_COMMANDS.get(platform, [])
    for cmd in commands:
        if cmd.startswith("./"):
            cmd_name = cmd[2:]
            search_paths = [_workspace_root()]
        else:
            cmd_name = cmd
            search_paths = None
        
        if search_paths:
            for path in search_paths:
                full_path = Path(path) / cmd_name
                if full_path.exists() and os.access(str(full_path), os.X_OK):
                    return True
        else:
            if _command_exists(cmd_name):
                return True
    
    return False


def _command_exists(cmd: str) -> bool:
    """Check if a command exists in PATH."""
    try:
        subprocess.run([cmd, "--version"], capture_output=True, check=False)
        return True
    except FileNotFoundError:
        return False


def should_trigger_warm_build(task_dir: Path) -> tuple[bool, str]:
    """Determine if warm build should be triggered.
    
    Returns: (should_trigger, reason)
    """
    if os.environ.get("AUTOMIND_WARM_BUILD_ENABLED", "").strip().lower() == "false":
        return False, "disabled by AUTOMIND_WARM_BUILD_ENABLED=false"
    
    if not _has_runtime_testcases(task_dir):
        return False, "no required runtime-level test cases"
    
    platform = detect_platform(task_dir)
    if not platform:
        return False, "cannot detect platform"
    
    if platform not in BUILD_SYSTEM_COMMANDS:
        return False, f"unsupported platform: {platform}"
    
    if not _build_system_available(platform):
        return False, f"build system not available for {platform}"
    
    return True, f"platform={platform}, runtime tests present, build system available"


def _find_podfile(workspace_root: Path) -> Optional[Path]:
    """Find Podfile in the workspace, including subdirectories.
    
    Returns the first Podfile found, preferring root directory.
    """
    if (workspace_root / "Podfile").exists():
        return workspace_root / "Podfile"
    for path in workspace_root.rglob("Podfile"):
        if "Pods" in path.parts:
            continue
        return path
    return None


def _resolve_build_command(platform: str) -> tuple[str, list[str], Path]:
    """Resolve the build command for a platform.
    
    Returns: (command, arguments, working_directory)
    """
    workspace_root = _workspace_root()
    
    if platform == "ios":
        if (workspace_root / "custom_build_wrapper.sh").exists() and _command_exists("custom_workspace_wrapper"):
            return "bash", [str(workspace_root / "custom_build_wrapper.sh"), "--install"], workspace_root
        
        podfile = _find_podfile(workspace_root)
        if podfile:
            return "pod", ["install", "--quiet"], podfile.parent
        
        return "xcodebuild", ["build", "-quiet"], workspace_root
    
    if platform == "android":
        gradle_path = workspace_root / "gradlew"
        if gradle_path.exists() and os.access(str(gradle_path), os.X_OK):
            return str(gradle_path), ["assembleDebug", "--no-daemon", "--configuration-cache", "-q"], workspace_root
        return "gradle", ["assembleDebug", "--no-daemon", "-q"], workspace_root
    
    if platform == "web":
        pkg_file = workspace_root / "package.json"
        if pkg_file.exists():
            for cmd in ["pnpm", "yarn", "npm"]:
                if _command_exists(cmd):
                    return cmd, ["install"], workspace_root
        return "npm", ["install"], workspace_root
    
    return "", [], workspace_root


def _run_build_command(command: str, args: list[str], log_path: Path, cwd: Path) -> tuple[int, str]:
    """Run a build command and capture output."""
    ensure_dir(log_path.parent)
    
    try:
        proc = subprocess.run(
            [command] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=600,
            env={**os.environ, "CI": "true", "BUILD_NUMBER": "warm-build"},
        )
        output = proc.stdout + "\n" + proc.stderr
        log_path.write_text(output, errors="ignore")
        return proc.returncode, output
    except subprocess.TimeoutExpired as exc:
        output = f"Command timed out after {exc.timeout}s"
        log_path.write_text(output)
        return -1, output
    except Exception as exc:
        output = f"Command failed: {type(exc).__name__}: {exc}"
        log_path.write_text(output)
        return -1, output


def _warm_build_worker(task_dir: Path, platform: str, log_dir: Path) -> None:
    """Background worker that performs the warm build."""
    start_time = time.time()
    try:
        command, args, cwd = _resolve_build_command(platform)
        if not command:
            _update_warm_build_state(task_dir, status="failed", reason="no build command resolved")
            get_metrics(task_dir).record_warm_build(time.time() - start_time, "failed", platform)
            return
        
        log(f"Warm build starting: {command} {' '.join(args)} (cwd={cwd})")
        _update_warm_build_state(task_dir, status="running", buildCommand=f"{command} {' '.join(args)}")
        
        output_path = log_dir / "output.log"
        returncode, output = _run_build_command(command, args, output_path, cwd)
        
        duration = time.time() - start_time
        if returncode == 0:
            warm_build_status = {
                "status": "completed",
                "platform": platform,
                "returnCode": returncode,
                "evidencePath": str(output_path),
                "completedAt": datetime.now().isoformat(timespec="seconds"),
            }
            _update_warm_build_state(task_dir, **warm_build_status)
            log("Warm build completed successfully")
            append_progress_log(task_dir, "warm build completed", owner="warm_build", level="info")
            get_metrics(task_dir).record_warm_build(duration, "completed", platform)
            
            if start_ui_exploration(task_dir, warm_build_status):
                log("UI path exploration started in background")
        else:
            _update_warm_build_state(
                task_dir,
                status="failed",
                returnCode=returncode,
                evidencePath=str(output_path),
                reason=f"build command failed with exit code {returncode}",
                completedAt=datetime.now().isoformat(timespec="seconds"),
            )
            warn(f"Warm build failed: {output[:500]}")
            append_progress_log(task_dir, f"warm build failed: {output[:200]}", owner="warm_build", level="warn")
            get_metrics(task_dir).record_warm_build(duration, "failed", platform)
    
    except Exception as exc:
        _update_warm_build_state(
            task_dir,
            status="failed",
            reason=f"unexpected error: {type(exc).__name__}: {exc}",
            completedAt=datetime.now().isoformat(timespec="seconds"),
        )
        warn(f"Warm build exception: {exc}")
        get_metrics(task_dir).record_warm_build(time.time() - start_time, "failed", platform)


def _warm_build_thread_name(task_dir: Path) -> str:
    return f"automind-warm-build-{task_dir.name}"


def _warm_build_thread_alive(task_dir: Path) -> bool:
    """Return True if this process still has a live warm-build worker thread."""
    name = _warm_build_thread_name(task_dir)
    return any(t.name == name and t.is_alive() for t in threading.enumerate())


def start_warm_build(task_dir: Path) -> bool:
    """Start a warm build in the background.
    
    Returns True if warm build was started, False if skipped or already running.
    """
    status = get_warm_build_status(task_dir)
    # A live worker thread in this process means a warm build is genuinely in
    # flight; do not start a duplicate. A stale pending/running left by a killed
    # process (no live thread, e.g. on resume) is not in flight and may restart.
    if status.get("status") in {"pending", "running"} and _warm_build_thread_alive(task_dir):
        return False
    
    should_trigger, reason = should_trigger_warm_build(task_dir)
    if not should_trigger:
        _update_warm_build_state(task_dir, status="skipped", reason=reason)
        return False
    
    platform = detect_platform(task_dir)
    if not platform:
        _update_warm_build_state(task_dir, status="skipped", reason="cannot detect platform")
        return False
    
    log_dir = get_warm_build_log_dir(task_dir)
    ensure_dir(log_dir)
    
    _update_warm_build_state(
        task_dir,
        status="pending",
        platform=platform,
        startedAt=datetime.now().isoformat(timespec="seconds"),
    )
    
    thread = threading.Thread(
        target=_warm_build_worker,
        args=(task_dir, platform, log_dir),
        daemon=True,
        name=_warm_build_thread_name(task_dir),
    )
    thread.start()
    
    return True


def wait_for_warm_build(task_dir: Path, max_wait: int = WARM_BUILD_MAX_WAIT_SECONDS) -> dict:
    """Wait for warm build to complete or timeout.
    
    Returns the final warm build status.
    """
    status = get_warm_build_status(task_dir)
    current = status.get("status")
    # Terminal states return immediately.
    if current in {"completed", "failed", "skipped", "timed_out"}:
        return status
    # Only "pending" (just scheduled) or "running" (worker started) are waitable;
    # anything else (e.g. missing) means no warm build is in flight.
    if current not in {"pending", "running"}:
        return status
    
    log(f"Waiting for warm build (max {max_wait}s)...")
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        time.sleep(1)
        status = get_warm_build_status(task_dir)
        if status.get("status") in {"completed", "failed", "skipped"}:
            break
    
    if status.get("status") in {"pending", "running"}:
        warn(f"Warm build still {status.get('status')} after {max_wait}s, proceeding with evaluation")
        _update_warm_build_state(task_dir, status="timed_out", reason=f"timeout after {max_wait}s")
        status = get_warm_build_status(task_dir)
    
    return status


def is_incremental_build_possible(task_dir: Path) -> tuple[bool, str]:
    """Check if incremental build is possible.

    Informational/diagnostic only. The actual platform build runs inside the
    external runner scripts (e.g. ios_xcuitest_runner.py), which transparently
    reuse the primed disk caches (DerivedData / Gradle / Pods). This function
    reports whether structural dependency files changed after the warm build so
    callers can log the incremental-vs-full expectation; it does not itself
    inject build flags or alter the runner's command.

    Returns: (is_possible, reason)
    """
    status = get_warm_build_status(task_dir)
    
    if status.get("status") != "completed":
        return False, f"warm build not completed (status={status.get('status')})"
    
    platform = status.get("platform")
    if not platform:
        return False, "no platform information"
    
    structural_files = PLATFORM_STRUCTURAL_FILES.get(platform, set())
    if not structural_files:
        return False, f"no structural files defined for platform {platform}"
    
    if _structural_files_modified(task_dir, platform):
        return False, "STRUCTURAL_CHANGES: dependency config modified after warm build; full rebuild required"
    
    return True, "FULL_INCREMENTAL: no structural changes; incremental build reuses warm cache"


def _structural_files_modified(task_dir: Path, platform: str) -> bool:
    """Check if any structural files (dependency configs) were modified.
    
    This is a lightweight check - we look for these files in the workspace
    and compare against the warm build start time.
    """
    status = get_warm_build_status(task_dir)
    started_at = status.get("startedAt")
    if not started_at:
        return False
    
    try:
        warm_start = datetime.fromisoformat(started_at)
    except ValueError:
        return False
    
    structural_files = PLATFORM_STRUCTURAL_FILES.get(platform, set())
    workspace_root = _workspace_root()
    
    for pattern in structural_files:
        for path in workspace_root.glob(pattern):
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime)
                if mtime > warm_start:
                    return True
            except OSError:
                continue
    
    return False


def get_incremental_build_args(task_dir: Path, base_command: str, base_args: list[str]) -> list[str]:
    """Add incremental build arguments based on platform and warm build status.
    
    Only augments the command when an incremental build is actually possible
    (warm build completed and no structural/dependency files changed). Returns
    the original args unchanged otherwise so callers always get a valid command.
    """
    incremental_possible, _reason = is_incremental_build_possible(task_dir)
    if not incremental_possible:
        return base_args
    
    status = get_warm_build_status(task_dir)
    platform = status.get("platform")
    incremental_args: list[str] = []
    
    if platform == "ios":
        # xcodebuild has no `-incremental` flag; reusing the warm DerivedData
        # path is what actually enables incremental compilation.
        derived_data = _workspace_root() / "DerivedData"
        if derived_data.exists() and "-derivedDataPath" not in base_args:
            incremental_args.extend(["-derivedDataPath", str(derived_data)])
    
    elif platform == "android":
        for flag in ("--build-cache", "--configuration-cache"):
            if flag not in base_args:
                incremental_args.append(flag)
    
    return base_args + incremental_args
