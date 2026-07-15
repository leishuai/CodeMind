"""Markdown artifact, testcase, evidence, and ID helpers for CodeMind.

These utilities are intentionally side-effect-light and are shared by workflow,
completion, status, and summary code. Keeping them outside ``main.py`` makes the
CLI entrypoint smaller without changing command behavior.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional

from orchestrator.config import AUTOMIND_ROOT, AUTOMIND_WORKSPACE_ROOT


def normalize_artifact_id(value: str) -> str:
    """Normalize cross-file IDs such as AC-001 / AC_001 for comparison."""
    return re.sub(r"[-_\s]", "", value or "").upper()


def extract_artifact_ids(text: str, kind: Literal["R", "AC", "TC"]) -> dict[str, str]:
    """Extract stable workflow IDs from markdown/prose.

    Returned mapping is normalized-id -> first display value.
    """
    if kind == "R":
        pattern = r"\bR[-_]?\d{2,3}\b"
    elif kind == "AC":
        pattern = r"\bAC[-_]?\d{2,3}\b"
    else:
        pattern = r"\bTC(?:[-_][A-Z]{1,4})?[-_]?\d{2,3}\b"

    found: dict[str, str] = {}
    for match in re.finditer(pattern, text or "", flags=re.IGNORECASE):
        display = match.group(0).upper().replace("_", "-")
        found.setdefault(normalize_artifact_id(display), display)
    return found


def format_missing_ids(source: dict[str, str], missing_norms: set[str]) -> str:
    return ", ".join(source[norm] for norm in sorted(missing_norms, key=lambda item: source[item]))


def evidence_path_exists(task_dir: Path, raw_path: str) -> bool:
    """Best-effort evidence path existence check for task-relative or absolute paths."""
    if not raw_path:
        return False
    path = Path(raw_path)
    candidates = [path] if path.is_absolute() else [task_dir / path, AUTOMIND_WORKSPACE_ROOT / path, AUTOMIND_ROOT / path]
    return any(candidate.exists() for candidate in candidates)


def resolve_task_artifact_path(task_dir: Path, raw_path: str) -> Optional[Path]:
    """Resolve a task/workspace/runtime-relative artifact path when it exists."""
    if not raw_path:
        return None
    path = Path(str(raw_path)).expanduser()
    candidates = [path] if path.is_absolute() else [task_dir / path, AUTOMIND_WORKSPACE_ROOT / path, AUTOMIND_ROOT / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None




def task_uses_single_file_requirements(task_dir: Path) -> bool:
    """Return True when Requirements.md exists and is the task requirement contract."""
    path = task_dir / "Requirements.md"
    return path.exists() and bool(path.read_text(errors="ignore").strip())


def requirement_contract_paths(task_dir: Path) -> list[Path]:
    """Return the authoritative requirement contract file for every task."""
    return [task_dir / "Requirements.md"]


def primary_requirements_path(task_dir: Path) -> Path:
    """Return the human-readable requirements artifact path."""
    return task_dir / "Requirements.md"


def read_requirements_contract_text(task_dir: Path) -> str:
    """Read the authoritative Requirements.md contract text."""
    path = task_dir / "Requirements.md"
    return path.read_text(errors="ignore") if path.exists() else ""

def extract_markdown_section(text: str, heading: str) -> str:
    """Extract a markdown section by exact heading text."""
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", flags=re.MULTILINE)
    match = pattern.search(text or "")
    if not match:
        return ""
    start = match.end()
    next_match = re.search(r"^##\s+", text[start:], flags=re.MULTILINE)
    end = start + next_match.start() if next_match else len(text)
    return text[start:end].strip()


def extract_plan_checklist_rows(plan_text: str, heading: str) -> list[dict]:
    """Extract rows from Plan.md Implementation/Verification checklist tables."""
    section = extract_markdown_section(plan_text, heading)
    rows: list[dict] = []
    headers: list[str] | None = None
    for line in section.splitlines():
        cells = split_markdown_table_row(line)
        if not cells:
            continue
        if is_markdown_separator_row(cells):
            continue
        lower = [cell.lower() for cell in cells]
        if "id" in lower and ("status" in lower or "状态" in lower):
            headers = cells
            continue
        if not headers or len(cells) < 2:
            continue
        normalized_headers = [
            re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", header.lower())
            for header in headers
        ]

        def cell_for(names: list[str], default_idx: int | None = None) -> str:
            for idx, header in enumerate(normalized_headers):
                if any(name in header for name in names):
                    return cells[idx] if idx < len(cells) else ""
            if default_idx is not None and default_idx < len(cells):
                return cells[default_idx]
            return ""

        row_id = cell_for(["id"], 0).strip()
        if not row_id:
            continue
        rows.append({
            "id": row_id,
            "status": cell_for(["status", "状态"], 3).strip(),
            "owner": cell_for(["owner", "负责人"], None).strip(),
            "source": cell_for(["source", "来源"], None).strip(),
            "evidence": cell_for(["evidence", "证据"], None).strip(),
            "raw": " | ".join(cells),
        })
    return rows


def summarize_plan_checklists(task_dir: Path) -> dict:
    """Return lightweight checklist status summary from Plan.md."""
    plan_path = task_dir / "Plan.md"
    if not plan_path.exists():
        return {"implementation": {}, "verification": {}, "implementationRows": [], "verificationRows": []}
    plan_text = plan_path.read_text(errors="ignore")
    implementation_rows = extract_plan_checklist_rows(plan_text, "Implementation Checklist")
    verification_rows = extract_plan_checklist_rows(plan_text, "Verification Checklist")

    def counts(rows: list[dict]) -> dict:
        result: dict[str, int] = {}
        for row in rows:
            status = (row.get("status") or "unknown").strip().lower()
            status = re.sub(r"`|\*|_", "", status)
            result[status] = result.get(status, 0) + 1
        return result

    return {
        "implementation": counts(implementation_rows),
        "verification": counts(verification_rows),
        "implementationRows": implementation_rows,
        "verificationRows": verification_rows,
    }


def merge_verification_status_from_completion(task_dir: Path, checklist_summary: dict, completion_report: dict | None) -> dict:
    """Overlay machine verification results onto Plan checklist counts for status output.

    Plan.md remains the human/AI progress layer, but deterministic evaluators may
    not edit Plan.md directly. `status` therefore shows a merged view where
    VerificationLedger/evaluation results win over stale Plan checklist statuses.
    """
    verification_rows = [dict(row) for row in checklist_summary.get("verificationRows", [])]
    if not verification_rows or not completion_report:
        return checklist_summary
    result_by_norm = {
        normalize_artifact_id(item.get("testCaseId", "")): normalize_test_result_value(item.get("result", "not_run"))
        for item in completion_report.get("testResults", [])
        if isinstance(item, dict)
    }
    if not result_by_norm:
        return checklist_summary

    for row in verification_rows:
        norm = normalize_artifact_id(row.get("id", ""))
        if norm in result_by_norm:
            row["status"] = result_by_norm[norm]

    result_counts: dict[str, int] = {}
    for row in verification_rows:
        status = (row.get("status") or "unknown").strip().lower()
        status = re.sub(r"`|\*|_", "", status)
        result_counts[status] = result_counts.get(status, 0) + 1

    merged = dict(checklist_summary)
    merged["verification"] = result_counts
    merged["verificationRows"] = verification_rows
    merged["verificationMergedFromCompletion"] = True
    return merged


def split_markdown_table_row(line: str) -> list[str]:
    """Split a simple markdown table row into trimmed cells."""
    stripped = (line or "").strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def is_markdown_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells if cell)


def parse_required_flag(raw: str, testcase_id: str, testcase_type: str = "") -> bool:
    """Return whether a testcase should be part of the final completion gate."""
    value = (raw or "").strip().lower()
    if value:
        # Check negative phrases first because "not required" contains the word
        # "required" and would otherwise be misclassified as mandatory.
        if any(token in value for token in ["not required", "no", "false", "optional", "not_applicable", "n/a", "否", "可选"]):
            return False
        if any(token in value for token in ["yes", "true", "required", "must", "p0", "是", "必需", "必须"]):
            return True

    # Missing Required? column fallback: functional/smoke cases are required by
    # default; quality cases are optional unless explicitly marked required.
    upper_id = testcase_id.upper()
    lower_type = (testcase_type or "").lower()
    if upper_id.startswith("TC-Q") or "quality" in lower_type:
        return False
    return True


def extract_declared_testcases(task_dir: Path) -> list[dict]:
    """Extract declared TestCases.md rows for completion gating.

    This parser intentionally stays lightweight and markdown-oriented. It reads
    the canonical CodeMind table shape but also falls back to discovered `TC-*`
    identifiers when the table is incomplete.
    """
    path = task_dir / "TestCases.md"
    if not path.exists():
        return []
    text = path.read_text(errors="ignore")
    rows: list[dict] = []
    seen: set[str] = set()
    headers: list[str] | None = None

    def header_index(names: list[str]) -> int | None:
        if not headers:
            return None
        for idx, header in enumerate(headers):
            normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", header.lower())
            for name in names:
                if name in normalized:
                    return idx
        return None

    for line in text.splitlines():
        cells = split_markdown_table_row(line)
        if not cells:
            continue
        lower_cells = [cell.lower() for cell in cells]
        if "id" in lower_cells and any("required" in cell or "必" in cell for cell in lower_cells):
            headers = cells
            continue
        if is_markdown_separator_row(cells):
            continue

        row_text = " | ".join(cells)
        tc_ids = extract_artifact_ids(row_text, "TC")
        if not tc_ids:
            continue
        testcase_id = next(iter(tc_ids.values()))
        normalized_id = normalize_artifact_id(testcase_id)
        if normalized_id in seen:
            continue
        seen.add(normalized_id)

        id_idx = header_index(["id"]) if headers else 0
        req_idx = header_index(["requirement", "ac", "验收", "需求"]) if headers else 1
        type_idx = header_index(["type", "类型"]) if headers else 2
        runtime_idx = header_index(["runtimelevel", "runtime", "运行", "执行"]) if headers else 3
        preconditions_idx = header_index(["preconditions", "precondition", "tools", "tool", "前置", "工具"]) if headers else None
        command_idx = header_index(["command", "automindcommand", "命令"]) if headers else None
        steps_idx = header_index(["stepsverificationmethod", "steps", "verificationmethod", "method", "步骤", "方法"]) if headers else None
        evidence_idx = header_index(["expectedevidenceresult", "expectedevidence", "expectedresult", "evidence", "预期", "证据"]) if headers else None
        dependency_idx = header_index(["dependency", "depends", "依赖"]) if headers else None
        required_idx = header_index(["required", "必须", "必需"]) if headers else None

        def cell_at(idx: int | None) -> str:
            if idx is None or idx >= len(cells):
                return ""
            return cells[idx]

        testcase_type = cell_at(type_idx)
        required_raw = cell_at(required_idx)
        source = row_text
        rows.append({
            "id": testcase_id,
            "normalizedId": normalized_id,
            "required": parse_required_flag(required_raw, testcase_id, testcase_type),
            "type": testcase_type,
            "runtimeLevel": cell_at(runtime_idx),
            "quality": testcase_id.upper().startswith("TC-Q") or "quality" in testcase_type.lower(),
            "requirements": list(extract_artifact_ids(cell_at(req_idx) or source, "R").values()),
            "acceptanceCriteria": list(extract_artifact_ids(cell_at(req_idx) or source, "AC").values()),
            "preconditions": cell_at(preconditions_idx),
            "command": cell_at(command_idx),
            "steps": cell_at(steps_idx),
            "expectedEvidence": cell_at(evidence_idx),
            "dependency": cell_at(dependency_idx),
            "source": source,
        })

    if rows:
        return rows

    # Fallback for non-table TestCases.md. Treat non-quality discovered cases as
    # required so incomplete formatting does not hide functional checks.
    for testcase_id in extract_artifact_ids(text, "TC").values():
        normalized_id = normalize_artifact_id(testcase_id)
        if normalized_id in seen:
            continue
        seen.add(normalized_id)
        rows.append({
            "id": testcase_id,
            "normalizedId": normalized_id,
            "required": parse_required_flag("", testcase_id, ""),
            "type": "",
            "runtimeLevel": "",
            "quality": testcase_id.upper().startswith("TC-Q"),
            "requirements": [],
            "acceptanceCriteria": [],
            "expectedEvidence": "",
            "dependency": "",
            "source": testcase_id,
        })
    return rows


def normalize_evidence_refs(value) -> list[str]:
    """Normalize evidence fields from evaluation/testResults into path strings."""
    refs: list[str] = []

    def add(item):
        if item is None:
            return
        if isinstance(item, str):
            if item.strip():
                refs.append(item.strip())
            return
        if isinstance(item, dict):
            path = item.get("path") or item.get("evidence") or item.get("file")
            if path:
                refs.append(str(path))
            return
        if isinstance(item, list):
            for child in item:
                add(child)

    add(value)
    return refs


def extract_fenced_code_blocks(text: str, languages: tuple[str, ...] = ("bash", "sh", "shell", "zsh", "")) -> list[str]:
    """Extract fenced command/code blocks from markdown text."""
    blocks: list[str] = []
    allowed = {lang.lower() for lang in languages}
    for match in re.finditer(r"```([^\n`]*)\n(.*?)```", text or "", flags=re.DOTALL):
        lang = match.group(1).strip().lower()
        if lang in allowed:
            body = match.group(2).strip()
            if body:
                blocks.append(body)
    return blocks


def extract_first_command_block(text: str) -> str:
    """Return a compact first runnable-looking command block from markdown."""
    for block in extract_fenced_code_blocks(text):
        lines = [line.strip() for line in block.splitlines() if line.strip() and not line.strip().startswith("#")]
        if lines:
            return " && ".join(lines[:4])
    # Fallback for simple inline list entries such as `pytest` or `xcodebuild ...`.
    inline_matches = re.findall(r"`([^`\n]{3,240})`", text or "")
    command_starters = (
        "automind", "./automind.sh", "python", "python3", "pytest", "npm", "pnpm", "yarn",
        "xcodebuild", "./gradlew", "gradle", "adb", "curl", "bash", "sh",
    )
    for item in inline_matches:
        stripped = item.strip()
        if stripped.startswith(command_starters):
            return stripped
    return ""


def normalize_test_result_value(raw: str) -> str:
    value = str(raw or "").strip().lower()
    aliases = {
        "passed": "pass",
        "ok": "pass",
        "success": "pass",
        "failed": "fail",
        "failure": "fail",
        "blocked": "blocked",
        "skip": "skipped",
        "skipped": "skipped",
        "notrun": "not_run",
        "not_run": "not_run",
        "not-run": "not_run",
        "warn": "warn",
        "warning": "warn",
    }
    return aliases.get(value, value if value in {"pass", "fail", "partial", "blocked", "skipped", "not_run", "warn"} else "not_run")


def test_result_is_acceptable(testcase: dict, result: str) -> bool:
    normalized = normalize_test_result_value(result)
    if normalized == "pass":
        return True
    # Quality warnings are allowed as non-blocking when the testcase is a
    # quality check. Functional required checks still need pass.
    if normalized == "warn" and testcase.get("quality"):
        return True
    return False
