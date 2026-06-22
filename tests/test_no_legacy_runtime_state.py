from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_removed_runtime_state_legacy_artifact_names_do_not_return() -> None:
    """Guard the single runtime-state artifact contract.

    Keep the forbidden tokens assembled instead of writing them literally, so
    this test does not trip its own source scan.
    """
    forbidden = [
        "task" + "-state.json",
        "task" + "-state",
        "task" + "State",
        "Task" + " state",
    ]
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    offenders: list[str] = []
    for rel in tracked:
        path = ROOT / rel
        if not path.is_file():
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov", ".zip", ".gz", ".tar"}:
            continue
        text = path.read_text(errors="ignore")
        for token in forbidden:
            if token in text:
                offenders.append(f"{rel}: contains removed legacy token {token!r}")
    assert not offenders, "\n".join(offenders[:50])
