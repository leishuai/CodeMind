#!/usr/bin/env python3
"""Lightweight CodeAutonomy checkpoint manager.

P0 supports create/list/plan-restore. It does not mutate or restore files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shutil
import subprocess
import sys
from typing import Any

from automind_paths import RUNTIME_ROOT, TASKS_DIR, CHECKPOINTS_DIR, WORKSPACE_ROOT

ROOT = RUNTIME_ROOT

KEY_TASK_FILES = [
    "Brainstorm.md",
    "Requirements.md",
    "TestCases.md",
    "Plan.md",
    "Delivery.md",
    "Validation.md",
    "evaluation.json",
    "automind-workflow-state.json",
    "runtime-state.json",
    "runtime-state.json",
    "probe-flow.json",
    "probe-flow.android.json",
    "probe-flow.ios.json",
]


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=str(WORKSPACE_ROOT), text=True, capture_output=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def write_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def next_checkpoint_id(task_cp_dir: pathlib.Path) -> str:
    existing = sorted(p.name for p in task_cp_dir.glob("cp-*") if p.is_dir())
    if not existing:
        return "cp-001"
    nums = []
    for name in existing:
        try:
            nums.append(int(name.split("-", 1)[1]))
        except Exception:
            pass
    return f"cp-{(max(nums) if nums else 0) + 1:03d}"


def git_snapshot(cp_dir: pathlib.Path) -> dict[str, Any]:
    code, root = run(["git", "rev-parse", "--show-toplevel"])
    if code != 0:
        return {"isGitRepo": False, "reason": root.strip()}
    code, branch = run(["git", "branch", "--show-current"])
    _, head = run(["git", "rev-parse", "HEAD"])
    _, status = run(["git", "status", "--short"])
    _, diff = run(["git", "diff", "--binary"])
    _, staged = run(["git", "diff", "--cached", "--binary"])
    _, untracked = run(["git", "ls-files", "--others", "--exclude-standard"])
    (cp_dir / "git-status.txt").write_text(status)
    (cp_dir / "git-diff.patch").write_text(diff)
    (cp_dir / "git-staged.patch").write_text(staged)
    (cp_dir / "git-untracked.txt").write_text(untracked)
    return {
        "isGitRepo": True,
        "repoRoot": root.strip(),
        "branch": branch.strip(),
        "head": head.strip(),
        "dirty": bool(status.strip()),
        "statusPath": "git-status.txt",
        "diffPath": "git-diff.patch",
        "stagedDiffPath": "git-staged.patch",
        "untrackedPath": "git-untracked.txt",
    }


def create(args: argparse.Namespace) -> int:
    task_dir = TASKS_DIR / args.task_code
    if not task_dir.exists():
        print(f"Task not found: {args.task_code}", file=sys.stderr)
        return 2
    task_cp_dir = CHECKPOINTS_DIR / args.task_code
    task_cp_dir.mkdir(parents=True, exist_ok=True)
    cp_id = next_checkpoint_id(task_cp_dir)
    cp_dir = task_cp_dir / cp_id
    cp_dir.mkdir()
    files_dir = cp_dir / "task-files"
    files_dir.mkdir()

    copied = []
    for name in KEY_TASK_FILES:
        src = task_dir / name
        if src.exists():
            shutil.copy2(src, files_dir / name)
            copied.append(name)

    manifest = {
        "checkpointId": cp_id,
        "taskCode": args.task_code,
        "reason": args.reason,
        "createdAt": dt.datetime.now().isoformat(timespec="seconds"),
        "taskDir": str(task_dir),
        "copiedTaskFiles": copied,
        "git": git_snapshot(cp_dir),
        "restorePolicy": {
            "default": "task-files-only",
            "gitWorkingTreeRestoreRequiresConfirmation": True,
            "destructiveCommandsRequireAskUserQuestion": True,
        },
    }
    write_json(cp_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def list_checkpoints(args: argparse.Namespace) -> int:
    task_cp_dir = CHECKPOINTS_DIR / args.task_code
    rows = []
    for manifest in sorted(task_cp_dir.glob("cp-*/manifest.json")):
        try:
            data = json.loads(manifest.read_text())
        except Exception:
            continue
        rows.append({
            "checkpointId": data.get("checkpointId"),
            "createdAt": data.get("createdAt"),
            "reason": data.get("reason"),
            "dirty": (data.get("git") or {}).get("dirty"),
            "head": (data.get("git") or {}).get("head", "")[:12],
        })
    print(json.dumps({"taskCode": args.task_code, "checkpoints": rows}, ensure_ascii=False, indent=2))
    return 0


def plan_restore(args: argparse.Namespace) -> int:
    cp_dir = CHECKPOINTS_DIR / args.task_code / args.checkpoint_id
    manifest_path = cp_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"Checkpoint not found: {args.task_code}/{args.checkpoint_id}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text())
    ask = {
        "question": f"Restore checkpoint {args.checkpoint_id}?",
        "reason": manifest.get("reason", "Restoring this checkpoint allows retrying from before the old strategy started."),
        "options": [
            {
                "id": "A",
                "label": "Restore CodeAutonomy task state files only.",
                "impact": "Restores runtime-state/evaluation/Plan/Validation/probe-flow records without touching product code.",
                "risk": "Low; still overwrites current task record files.",
                "requiresConfirmation": True,
            },
            {
                "id": "B",
                "label": "Restore task state + git working tree.",
                "impact": "Return to the checkpoint git HEAD/patch state.",
                "risk": "May discard current unsaved changes; this is destructive.",
                "requiresConfirmation": True,
            },
            {"id": "C", "label": "Cancel", "impact": "Do not restore.", "requiresConfirmation": False},
        ],
        "recommended": "A",
        "defaultAction": "stop",
    }
    plan = {
        "checkpoint": manifest,
        "restorePlan": {
            "taskFilesAvailable": manifest.get("copiedTaskFiles", []),
            "git": manifest.get("git", {}),
            "askUserQuestion": ask,
        },
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def restore(args: argparse.Namespace) -> int:
    """Restore task-files from a checkpoint (R09/AC-019).

    Default policy is task-files-only restore: copies the snapshotted
    Brainstorm/Requirements/TestCases/Plan/Delivery/Validation/evaluation/
    runtime-state/probe-flow files back into the task directory. Does NOT touch
    git working tree unless --git is passed (reserved, currently a no-op
    placeholder for safety).
    """
    cp_dir = CHECKPOINTS_DIR / args.task_code / args.checkpoint_id
    manifest_path = cp_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"Checkpoint not found: {args.task_code}/{args.checkpoint_id}", file=sys.stderr)
        return 2
    files_dir = cp_dir / "task-files"
    if not files_dir.is_dir():
        print(f"Checkpoint task-files dir missing: {files_dir}", file=sys.stderr)
        return 2

    task_dir = TASKS_DIR / args.task_code
    if not task_dir.exists():
        print(f"Task not found: {args.task_code}", file=sys.stderr)
        return 2

    restored: list[str] = []
    for snap in sorted(files_dir.iterdir()):
        if not snap.is_file():
            continue
        dst = task_dir / snap.name
        shutil.copy2(snap, dst)
        restored.append(snap.name)

    result = {
        "taskCode": args.task_code,
        "checkpointId": args.checkpoint_id,
        "restored": restored,
        "restoredAt": dt.datetime.now().isoformat(timespec="seconds"),
        "policy": "task-files-only",
        "status": "restored",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="CodeAutonomy checkpoint manager")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_create = sub.add_parser("create")
    p_create.add_argument("task_code")
    p_create.add_argument("reason")
    p_create.set_defaults(func=create)
    p_list = sub.add_parser("list")
    p_list.add_argument("task_code")
    p_list.set_defaults(func=list_checkpoints)
    p_plan = sub.add_parser("plan-restore")
    p_plan.add_argument("task_code")
    p_plan.add_argument("checkpoint_id")
    p_plan.set_defaults(func=plan_restore)
    p_restore = sub.add_parser("restore", help="Restore task-files from a checkpoint")
    p_restore.add_argument("task_code")
    p_restore.add_argument("checkpoint_id")
    p_restore.set_defaults(func=restore)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
