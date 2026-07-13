#!/usr/bin/env python3
"""Suggest/apply minimal probe-flow repairs from CodeAutonomy evaluation artifacts.

Conservative by design:
- suggest mode never mutates files;
- apply mode backs up probe-flow.json first;
- only safe patches are applied automatically (wait/app hierarchy, optional flag).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from automind_paths import RUNTIME_ROOT, WORKSPACE_ROOT


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        return {"_error": str(exc), "_path": str(path)}


def latest_probe_summary(task_dir: Path) -> tuple[Path | None, dict[str, Any]]:
    logs = task_dir / "logs"
    if not logs.exists():
        return None, {}
    candidates = sorted(logs.glob("iter-*/probe-flow/probe-flow-summary.json"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        return None, {}
    path = candidates[-1]
    return path, load_json(path)


def load_flow(task_dir: Path) -> tuple[Path, dict[str, Any]]:
    # Prefer the canonical platform-suffixed name; fall back to the legacy
    # `probe-flow.json` only when the new file does not exist, and emit a
    # one-time stderr warning so users know to rename.
    new_path = task_dir / "probe-flow.android.json"
    legacy_path = task_dir / "probe-flow.json"
    if new_path.exists():
        return new_path, load_json(new_path)
    if legacy_path.exists():
        print(
            f"[CodeAutonomy] WARN: `{legacy_path.name}` is deprecated; rename to `probe-flow.android.json` (Android) or `probe-flow.ios.json` (iOS).",
            file=sys.stderr,
        )
        return legacy_path, load_json(legacy_path)
    raise SystemExit(
        f"probe-flow not found: tried {new_path} and {legacy_path}"
    )


def suggest(task_dir: Path) -> dict[str, Any]:
    evaluation = load_json(task_dir / "evaluation.json")
    summary_path, summary = latest_probe_summary(task_dir)
    suggestions: list[dict[str, Any]] = []

    failed: list[dict[str, Any]] = []
    for check in summary.get("checks", []):
        if check.get("ok") is False:
            failed.append(check)
    for check in evaluation.get("failedChecks", []):
        failed.append({
            "name": check.get("name", "failed_check"),
            "detail": check.get("reason", ""),
            "category": check.get("category", "unknown"),
            "evidence": check.get("evidence"),
        })

    for item in failed:
        name = item.get("name", "")
        detail = item.get("detail", "") or ""
        lower = detail.lower()
        evidence = item.get("evidence")

        compact_detail = detail.replace(" ", "").lower()
        if "com.android.systemui" in detail and ("appnodes\":0" in compact_detail or "appnodes':0" in compact_detail):
            suggestions.append({
                "type": "add_or_increase_wait",
                "safeToApply": True,
                "reason": "hierarchy only exposes SystemUI / appNodes=0; app UI may need more wait or an overlay handler",
                "patch": {
                    "after": "launch",
                    "insertOrUpdate": {
                        "type": "assert_app_hierarchy",
                        "name": "wait for app hierarchy",
                        "minNodes": 1,
                        "timeout": 10,
                        "interval": 1,
                    },
                },
                "evidence": evidence,
            })
        elif "not present" in lower or ("xpath" in lower and "timeout" in lower):
            suggestions.append({
                "type": "make_optional_or_use_tap_if_present",
                "safeToApply": True,
                "reason": "selector/text may represent an optional branch or unstable element",
                "patch": {"updateStep": name, "set": {"optional": True}},
                "evidence": evidence,
            })
        elif "assert text" in name.lower():
            suggestions.append({
                "type": "prefer_stable_selector",
                "safeToApply": False,
                "reason": "text assertion failed; inspect hierarchy and prefer resource-id/content-desc when available",
                "patch": {"updateStep": name, "consider": ["assert_selector", "resource_id", "content-desc"]},
                "evidence": evidence,
            })
        elif "install" in name.lower():
            suggestions.append({
                "type": "classify_install_failure",
                "safeToApply": False,
                "reason": "install failed; check signature conflict, device storage, USB install permission, or APK path",
                "patch": {"manualDecision": "Use --uninstall only with human authorization if signature conflict is confirmed"},
                "evidence": evidence,
            })

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for suggestion in suggestions:
        patch_key = json.dumps(suggestion.get("patch"), ensure_ascii=False, sort_keys=True)
        key = f"{suggestion.get('type')}::{patch_key}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(suggestion)
    suggestions = deduped

    summary_result = summary.get("result")
    if not suggestions and evaluation.get("result") == "pass" and summary_result in {None, "pass"}:
        suggestions.append({
            "type": "no_repair_needed",
            "safeToApply": False,
            "reason": "latest evaluation/probe-flow summary passed",
            "patch": None,
        })
    elif not suggestions:
        suggestions.append({
            "type": "manual_review_needed",
            "safeToApply": False,
            "reason": "no safe automatic repair suggestion matched; inspect evaluation and artifacts",
            "patch": None,
        })

    return {
        "task": task_dir.name,
        "evaluation": str(task_dir / "evaluation.json"),
        "probeFlowSummary": str(summary_path) if summary_path else None,
        "result": evaluation.get("result", "unknown"),
        "suggestions": suggestions,
    }


def find_step_index(steps: list[dict[str, Any]], *, step_type: str | None = None, name: str | None = None) -> int | None:
    for idx, step in enumerate(steps):
        if step_type and step.get("type") == step_type:
            return idx
        if name and step.get("name") == name:
            return idx
    return None


def apply_suggestions(task_dir: Path, suggestions: list[dict[str, Any]]) -> dict[str, Any]:
    flow_path, flow = load_flow(task_dir)
    steps = flow.setdefault("steps", [])
    if not isinstance(steps, list):
        raise SystemExit("probe-flow.json steps must be a list")

    backup: Path | None = None
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    def ensure_backup() -> Path:
        nonlocal backup
        if backup is None:
            backup = flow_path.with_suffix(f".json.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}")
            shutil.copyfile(flow_path, backup)
        return backup

    for suggestion in suggestions:
        if not suggestion.get("safeToApply"):
            skipped.append({"type": suggestion.get("type"), "reason": "not safeToApply"})
            continue
        patch = suggestion.get("patch") or {}
        stype = suggestion.get("type")

        if stype == "add_or_increase_wait":
            step_patch = patch.get("insertOrUpdate") or {}
            existing_idx = find_step_index(steps, step_type="assert_app_hierarchy")
            if existing_idx is not None:
                before = dict(steps[existing_idx])
                steps[existing_idx]["timeout"] = max(float(steps[existing_idx].get("timeout", 0)), float(step_patch.get("timeout", 10)))
                steps[existing_idx]["interval"] = step_patch.get("interval", steps[existing_idx].get("interval", 1))
                steps[existing_idx]["minNodes"] = max(int(steps[existing_idx].get("minNodes", 0)), int(step_patch.get("minNodes", 1)))
                if steps[existing_idx] != before:
                    ensure_backup()
                    applied.append({"type": stype, "action": "update", "index": existing_idx, "before": before, "after": steps[existing_idx]})
                else:
                    skipped.append({"type": stype, "reason": "existing assert_app_hierarchy already satisfies patch"})
            else:
                after_type = patch.get("after", "launch")
                insert_idx = find_step_index(steps, step_type=after_type)
                if insert_idx is None:
                    insert_idx = 0
                ensure_backup()
                steps.insert(insert_idx + 1, step_patch)
                applied.append({"type": stype, "action": "insert", "index": insert_idx + 1, "step": step_patch})

        elif stype == "make_optional_or_use_tap_if_present":
            target_name = patch.get("updateStep")
            idx = find_step_index(steps, name=target_name) if target_name else None
            if idx is None:
                skipped.append({"type": stype, "reason": f"target step not found: {target_name}"})
                continue
            before = dict(steps[idx])
            steps[idx].update(patch.get("set", {}))
            if steps[idx] != before:
                ensure_backup()
                applied.append({"type": stype, "action": "update", "index": idx, "before": before, "after": steps[idx]})
            else:
                skipped.append({"type": stype, "reason": f"target step already has requested values: {target_name}"})

        else:
            skipped.append({"type": stype, "reason": "unsupported patch type"})

    if applied:
        flow_path.write_text(json.dumps(flow, ensure_ascii=False, indent=2))
    return {
        "flow": str(flow_path),
        "backup": str(backup) if backup else None,
        "applied": applied,
        "skipped": skipped,
        "changed": bool(applied),
    }



def next_iteration(task_dir: Path) -> int:
    state = load_json(task_dir / "runtime-state.json")
    try:
        return int(state.get("iteration", 0) or 0) + 1
    except Exception:
        return 1


def repair_rerun(workspace_root: Path, runtime_root: Path, task_dir: Path, dry_run: bool = False) -> dict[str, Any]:
    result = suggest(task_dir)
    apply_result = apply_suggestions(task_dir, result.get("suggestions", []))
    result["applyResult"] = apply_result

    if not apply_result.get("applied"):
        result["rerun"] = {
            "skipped": True,
            "reason": "no safe patch was applied; rerun would not validate a changed flow",
        }
        result["exitCode"] = 2
        return result

    iteration = next_iteration(task_dir)
    cmd = [sys.executable, str(runtime_root / "orchestrator" / "main.py"), "android-probe-flow", task_dir.name, str(iteration)]
    if dry_run:
        cmd.append("--dry-run")
    env = dict(os.environ)
    env["AUTOMIND_WORKSPACE_ROOT"] = str(workspace_root)
    env["AUTOMIND_RUNTIME_ROOT"] = str(runtime_root)
    proc = subprocess.run(cmd, cwd=str(workspace_root), env=env, text=True, capture_output=True)
    result["rerun"] = {
        "skipped": False,
        "iteration": iteration,
        "dryRun": dry_run,
        "command": cmd,
        "returnCode": proc.returncode,
        "stdoutTail": proc.stdout[-4000:],
        "stderrTail": proc.stderr[-2000:],
        "evaluation": str(task_dir / "evaluation.json"),
    }
    result["exitCode"] = proc.returncode
    return result

def resolve_task(workspace_root: Path, task: str) -> Path:
    task_dir = Path(task)
    if not task_dir.exists():
        task_dir = workspace_root / ".automind" / "tasks" / task
    if not task_dir.exists():
        raise SystemExit(f"task not found: {task}")
    return task_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", help="Task code or path under .automind/tasks")
    ap.add_argument("--root", default=None, help="Target workspace root. Defaults to $AUTOMIND_WORKSPACE_ROOT or current directory.")
    ap.add_argument("--runtime-root", default=None, help="CodeAutonomy runtime checkout. Defaults to this script's checkout.")
    ap.add_argument("--apply", action="store_true", help="Apply safe suggestions to probe-flow.json after creating a backup")
    ap.add_argument("--rerun", action="store_true", help="Apply safe suggestions and rerun android-probe-flow if the flow changed")
    ap.add_argument("--dry-run", action="store_true", help="With --rerun, rerun android-probe-flow in dry-run mode")
    args = ap.parse_args()
    workspace_root = Path(args.root).expanduser().resolve() if args.root else WORKSPACE_ROOT
    runtime_root = Path(args.runtime_root).expanduser().resolve() if args.runtime_root else RUNTIME_ROOT
    task_dir = resolve_task(workspace_root, args.task)
    if args.rerun:
        result = repair_rerun(workspace_root, runtime_root, task_dir, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(result.get("exitCode", 1))

    result = suggest(task_dir)
    if args.apply:
        result["applyResult"] = apply_suggestions(task_dir, result.get("suggestions", []))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
