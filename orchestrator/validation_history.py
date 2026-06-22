"""Validation.md history append helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_latest_evaluation(task_dir: Path) -> dict[str, Any]:
    path = task_dir / "evaluation.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _evidence_index_lines(evaluation: dict[str, Any], *, limit: int = 12) -> list[str]:
    rows: list[str] = []
    entries = evaluation.get("evidenceIndex") if isinstance(evaluation.get("evidenceIndex"), list) else []
    for item in entries:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        tc = item.get("tc")
        if isinstance(tc, list):
            tc_text = ", ".join(str(x) for x in tc if str(x).strip()) or "-"
        else:
            tc_text = str(tc or "-").strip() or "-"
        rows.append(
            f"- `{path}` — type=`{item.get('type') or 'hint'}`, "
            f"signal=`{item.get('signal') or '-'}`, tc=`{tc_text}`"
        )
        if len(rows) >= limit:
            break
    if rows and len(entries) > len(rows):
        remaining = len(entries) - len(rows)
        rows.append(f"- ... {remaining} more evidenceIndex entr{'y' if remaining == 1 else 'ies'} in `evaluation.json`")
    return rows


def append_validation_history(task_dir: Path, iteration: int, result: str, summary: str, next_action: str):
    val_path = task_dir / "Validation.md"
    content = val_path.read_text() if val_path.exists() else "# Validation\n"
    evidence_index = _evidence_index_lines(_read_latest_evaluation(task_dir))
    evidence_block = ""
    if evidence_index:
        evidence_block = "- Lightweight evidence index:\n" + "\n".join(f"  {line}" for line in evidence_index) + "\n"
    history = (
        f"\n\n## Iteration {iteration} - Evaluator\n"
        f"- Result: {result}\n"
        f"- Summary: {summary}\n"
        f"- Next action: {next_action}\n"
        f"{evidence_block}"
    )
    val_path.write_text(content.rstrip() + history + "\n")
