"""UI path caching and exploration for AutoMind tasks.

This module handles:
1. UI path exploration (finding successful action sequences for test goals)
2. Cache storage (persisting successful paths with validity fingerprints)
3. Validity checking (determining when cached paths are still usable)
4. Integration with Reuse mechanism
5. State persistence in runtime-state.json

The goal is to avoid repeated UI path exploration in the Evaluator phase.
When a successful path is found, it's cached and reused in subsequent runs.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from orchestrator.config import AUTOMIND_WORKSPACE_ROOT
from orchestrator.console import error, log, warn
from orchestrator.state import append_progress_log, ensure_dir, read_runtime_state, update_runtime_state
from orchestrator.metrics import get_metrics
from orchestrator.audit import record_action, record_branch

UI_PATH_CACHE_MAX_WAIT_SECONDS = 15
UI_PATH_CACHE_EXPIRY_DAYS = 7
UI_PATH_CACHE_DIR_NAME = "ui-path-cache"


def get_ui_path_cache_dir(task_dir: Path) -> Path:
    """Return the UI path cache directory path."""
    return task_dir / "logs" / UI_PATH_CACHE_DIR_NAME


def get_ui_path_cache_status(task_dir: Path) -> dict:
    """Read the current UI path cache status from runtime-state.json."""
    state = read_runtime_state(task_dir) or {}
    return state.get("uiPathCache") if isinstance(state.get("uiPathCache"), dict) else {}


def _update_ui_path_cache_state(task_dir: Path, **kwargs) -> None:
    """Update uiPathCache section in runtime-state.json."""
    state = read_runtime_state(task_dir) or {}
    ui_cache = state.get("uiPathCache") if isinstance(state.get("uiPathCache"), dict) else {}
    ui_cache.update(kwargs)
    ui_cache["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    update_runtime_state(task_dir, uiPathCache=ui_cache)


def get_ui_path_cache_file(task_dir: Path) -> Path:
    """Return the UI path cache JSON file path."""
    return get_ui_path_cache_dir(task_dir) / "cached-paths.json"


def read_ui_path_cache(task_dir: Path) -> dict:
    """Read the cached UI paths from the cache file."""
    cache_file = get_ui_path_cache_file(task_dir)
    if not cache_file.exists():
        return {}
    
    try:
        data = json.loads(cache_file.read_text())
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        warn(f"UI path cache file is invalid: {cache_file}")
        return {}


def write_ui_path_cache(task_dir: Path, cache: dict) -> None:
    """Write the UI path cache to the cache file."""
    cache_file = get_ui_path_cache_file(task_dir)
    ensure_dir(cache_file.parent)
    cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def compute_ui_fingerprint(task_dir: Path, screen_hierarchy: str = "") -> str:
    """Compute a fingerprint for UI hierarchy to detect changes.
    
    If screen_hierarchy is provided, it's used directly. Otherwise,
    we compute based on project structure and test goals.
    """
    if screen_hierarchy:
        return hashlib.sha256(screen_hierarchy.encode()).hexdigest()[:16]
    
    raw = os.environ.get("AUTOMIND_WORKSPACE_ROOT") or os.environ.get("AUTOMIND_PROJECT_ROOT")
    workspace_root = Path(raw).expanduser().resolve() if raw else AUTOMIND_WORKSPACE_ROOT
    test_cases_path = task_dir / "TestCases.md"
    
    content_parts = []
    
    if test_cases_path.exists():
        content_parts.append(test_cases_path.read_text(errors="ignore")[:5000])
    
    for ext in [".swift", ".m", ".h", ".kt", ".java", ".ts", ".tsx", ".jsx"]:
        for path in workspace_root.rglob(f"*{ext}"):
            if any(part in str(path) for part in {"Pods", "node_modules", ".git", "build", "dist"}):
                continue
            try:
                content_parts.append(str(path.relative_to(workspace_root)))
            except ValueError:
                continue
    
    return hashlib.sha256("\n".join(sorted(content_parts)).encode()).hexdigest()[:16]


def is_ui_path_cache_valid(task_dir: Path, tc_id: str, current_fingerprint: str) -> tuple[bool, str]:
    """Check if a cached UI path for a specific TC is valid.
    
    Returns: (is_valid, reason)
    """
    cache = read_ui_path_cache(task_dir)
    cached_entry = cache.get(tc_id)
    
    if not cached_entry:
        return False, "no cached path for this TC"

    if cached_entry.get("validity") == "expired":
        return False, "cache entry marked expired"

    if cached_entry.get("uiFingerprint") != current_fingerprint:
        return False, "UI fingerprint changed"
    
    timestamp_str = cached_entry.get("timestamp")
    if not timestamp_str:
        return False, "no timestamp in cache"
    
    try:
        cached_time = datetime.fromisoformat(timestamp_str)
    except ValueError:
        return False, "invalid timestamp format"
    
    if datetime.now() - cached_time > timedelta(days=UI_PATH_CACHE_EXPIRY_DAYS):
        return False, f"cache expired (older than {UI_PATH_CACHE_EXPIRY_DAYS} days)"
    
    return True, "valid"


def get_cached_ui_path(task_dir: Path, tc_id: str, current_fingerprint: str) -> Optional[dict]:
    """Get a valid cached UI path for a specific TC if available."""
    is_valid, reason = is_ui_path_cache_valid(task_dir, tc_id, current_fingerprint)
    if not is_valid:
        get_metrics(task_dir).record_cache_miss("ui_path", tc_id)
        record_branch(
            task_dir,
            phase="evaluator",
            condition=f"ui_path_cache:{tc_id}",
            outcome="miss",
            reason=reason,
        )
        return None

    cache = read_ui_path_cache(task_dir)
    get_metrics(task_dir).record_cache_hit("ui_path", tc_id)
    record_branch(
        task_dir,
        phase="evaluator",
        condition=f"ui_path_cache:{tc_id}",
        outcome="hit",
        alternatives=["regenerate_probe_flow"],
        reason="UI fingerprint matched",
    )
    return cache.get(tc_id)


def cache_ui_path(
    task_dir: Path,
    tc_id: str,
    goal: str,
    action_sequence: list[dict],
    ui_fingerprint: str,
) -> None:
    """Cache a successful UI path for a test case."""
    cache = read_ui_path_cache(task_dir)
    
    cache[tc_id] = {
        "tcId": tc_id,
        "goal": goal,
        "uiFingerprint": ui_fingerprint,
        "actionSequence": action_sequence,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "validity": "valid",
    }
    
    write_ui_path_cache(task_dir, cache)

    _update_ui_path_cache_state(task_dir, cachedPaths=list(cache.keys()))
    append_progress_log(task_dir, f"UI path cached for TC {tc_id}", owner="ui_path_cache", level="info")
    record_action(
        task_dir,
        phase="evaluator",
        action_type="ui_path_cache_write",
        target=tc_id,
        result="success",
        details={"goal": goal, "steps": len(action_sequence)},
    )


def mark_ui_path_expired(task_dir: Path, tc_id: str, reason: str = "manual") -> None:
    """Mark a cached UI path as expired."""
    cache = read_ui_path_cache(task_dir)
    if tc_id in cache:
        cache[tc_id]["validity"] = "expired"
        cache[tc_id]["expiredReason"] = reason
        cache[tc_id]["expiredAt"] = datetime.now().isoformat(timespec="seconds")
        write_ui_path_cache(task_dir, cache)
        append_progress_log(task_dir, f"UI path expired for TC {tc_id}: {reason}", owner="ui_path_cache", level="info")
        record_action(
            task_dir,
            phase="evaluator",
            action_type="ui_path_cache_expire",
            target=tc_id,
            result="expired",
            details={"reason": reason},
        )


def expire_cached_ui_paths(task_dir: Path, tc_ids: list[str], reason: str = "execution_failed") -> int:
    """Mark multiple cached UI paths as expired. Returns the number of entries expired."""
    expired = 0
    for tc_id in tc_ids:
        cache = read_ui_path_cache(task_dir)
        if tc_id in cache and cache[tc_id].get("validity") == "valid":
            mark_ui_path_expired(task_dir, tc_id, reason)
            expired += 1
    return expired


def _ui_exploration_worker(task_dir: Path, warm_build_status: dict) -> None:
    """Background worker that performs UI path exploration.
    
    This is a placeholder implementation. The actual exploration logic
    would be integrated with the probe-flow runner.
    """
    try:
        log("UI path exploration starting...")
        _update_ui_path_cache_state(task_dir, status="running")
        
        cache_dir = get_ui_path_cache_dir(task_dir)
        ensure_dir(cache_dir)
        
        platform = warm_build_status.get("platform")
        
        exploration_result = {
            "platform": platform,
            "exploredAt": datetime.now().isoformat(timespec="seconds"),
            "pathsFound": 0,
            "notes": "Placeholder - actual UI exploration would run probe-flow here",
        }
        
        exploration_path = cache_dir / "exploration-result.json"
        exploration_path.write_text(json.dumps(exploration_result, ensure_ascii=False, indent=2))
        
        _update_ui_path_cache_state(
            task_dir,
            status="completed",
            evidencePath=str(exploration_path),
            exploredPaths=exploration_result["pathsFound"],
            completedAt=datetime.now().isoformat(timespec="seconds"),
        )
        log("UI path exploration completed")
        append_progress_log(task_dir, "UI path exploration completed", owner="ui_path_cache", level="info")
    
    except Exception as exc:
        _update_ui_path_cache_state(
            task_dir,
            status="failed",
            reason=f"unexpected error: {type(exc).__name__}: {exc}",
            completedAt=datetime.now().isoformat(timespec="seconds"),
        )
        warn(f"UI path exploration exception: {exc}")


def start_ui_exploration(task_dir: Path, warm_build_status: dict) -> bool:
    """Start UI path exploration in the background.
    
    Returns True if exploration was started, False if skipped or already running.
    """
    status = get_ui_path_cache_status(task_dir)
    if status.get("status") == "running":
        return False
    
    if warm_build_status.get("status") != "completed":
        _update_ui_path_cache_state(task_dir, status="skipped", reason="warm build not completed")
        return False
    
    platform = warm_build_status.get("platform")
    if not platform:
        _update_ui_path_cache_state(task_dir, status="skipped", reason="no platform information")
        return False
    
    cache_dir = get_ui_path_cache_dir(task_dir)
    ensure_dir(cache_dir)
    
    _update_ui_path_cache_state(
        task_dir,
        status="pending",
        platform=platform,
        startedAt=datetime.now().isoformat(timespec="seconds"),
    )
    
    thread = threading.Thread(
        target=_ui_exploration_worker,
        args=(task_dir, warm_build_status),
        daemon=True,
        name=f"automind-ui-exploration-{task_dir.name}",
    )
    thread.start()
    
    return True


def wait_for_ui_exploration(task_dir: Path, max_wait: int = UI_PATH_CACHE_MAX_WAIT_SECONDS) -> dict:
    """Wait for UI path exploration to complete or timeout.
    
    Returns the final exploration status.
    """
    status = get_ui_path_cache_status(task_dir)
    current = status.get("status")
    if current in {"completed", "failed", "skipped", "timed_out"}:
        return status
    if current not in {"pending", "running"}:
        return status
    
    log(f"Waiting for UI path exploration (max {max_wait}s)...")
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        time.sleep(1)
        status = get_ui_path_cache_status(task_dir)
        if status.get("status") in {"completed", "failed", "skipped"}:
            break
    
    if status.get("status") in {"pending", "running"}:
        warn(f"UI path exploration still {status.get('status')} after {max_wait}s, proceeding with evaluation")
        _update_ui_path_cache_state(task_dir, status="timed_out", reason=f"timeout after {max_wait}s")
        status = get_ui_path_cache_status(task_dir)
    
    return status


def get_ui_fingerprint_for_current_state(task_dir: Path) -> str:
    """Get the UI fingerprint for the current workspace state."""
    return compute_ui_fingerprint(task_dir)


def _has_ui_testcases(task_dir: Path) -> bool:
    """Check if TestCases.md contains UI-related test cases."""
    test_cases_path = task_dir / "TestCases.md"
    if not test_cases_path.exists():
        return False
    
    content = test_cases_path.read_text(errors="ignore").lower()
    
    ui_keywords = ["click", "tap", "swipe", "scroll", "navigate", "button", "page", "screen"]
    return any(kw in content for kw in ui_keywords)
