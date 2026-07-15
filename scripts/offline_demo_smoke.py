#!/usr/bin/env python3
"""Run an offline CodeMind demo smoke without mobile devices.

The smoke creates a task under .automind/tasks/offline_demo_smoke, writes the
standard task files, runs a script-command evaluator, generates a summary, and
checks that the core artifacts exist and are valid.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any

from automind_paths import RUNTIME_ROOT, TASKS_DIR, WORKSPACE_ROOT

ROOT = RUNTIME_ROOT
TASK_CODE = "offline_demo_smoke"
TASK_DIR = TASKS_DIR / TASK_CODE


def write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(WORKSPACE_ROOT), text=True, capture_output=True, timeout=120)


def main() -> int:
    if TASK_DIR.exists():
        shutil.rmtree(TASK_DIR)
    TASK_DIR.mkdir(parents=True)
    now = datetime.now().isoformat(timespec="seconds")
    script_command = "python3 - <<'PY'\nprint('CodeMind offline demo: evidence loop OK')\nPY"

    write(TASK_DIR / "Brainstorm.md", """# Brainstorm

## Original user input

Run the offline no-device CodeMind smoke demo.

## Questions and assumptions

- No blocking questions.
- Assumption: the target environment has `python3` and can run a shell heredoc command.
- Assumption: this smoke demonstrates CodeMind artifact/evidence flow, not mobile platform capability.

## Decision

Proceed with a generic script-command evaluator.

## Pre-implementation user review

- Decision: `auto_proceed`
- Needs user input before code changes: `false`
- Reason: This smoke has a fixed local script-command verifier, no product code changes, and no external/device side effects.
- Policy: Auto-proceed is allowed for this no-device smoke.
""")
    write(TASK_DIR / "Requirements.md", f"""# Requirements - Offline Demo Smoke

## Goal

Prove CodeMind's core harness-loop artifact shape without requiring iOS/Android devices.

## Requirements with inline Acceptance Criteria

### R01 — Standard task artifacts
- **AC-001**: Standard task artifacts exist for the offline demo.
  - Verification method: file existence checks

### R02 — Script-command evaluator
- **AC-002**: `script-command` evaluator runs successfully.
  - Verification method: run configured `verifyCommand`

### R03 — Valid pass evaluation and reusable records
- **AC-003**: `evaluation.json` is valid JSON and has `result=pass,nextAction=finish`.
  - Verification method: parse and inspect `evaluation.json`
- **AC-004**: `Validation.md`, `summary.md`, command logs, and environment logs are generated.
  - Verification method: file existence and record-check

verifyCommand: `{script_command}`
""")

    write(TASK_DIR / "TestCases.md", """# TestCases

| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |
|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|
| TC-F01 | R01 / AC-001 | Functional | static/runtime | Task directory created | file existence check during smoke script | Prepare clean task directory -> create standard artifacts -> check required artifact files exist. | Brainstorm/Requirements/TestCases/Plan/Validation/runtime-state exist. | - | yes |
| TC-F02 | R02 / AC-002 | Functional | runtime | `python3` available; TC-F01 artifacts exist | `<AUTOMIND_CLI> script-command offline_demo_smoke 1` | Prepare configured `verifyCommand` -> run `script-command` evaluator -> assert exit code 0 and expected stdout. | `logs/iter-1/commands.md`, `evaluator.log`, `env.json`; exit code 0. | TC-F01 | yes |
| TC-F03 | R03 / AC-003 | Functional | runtime | TC-F02 passed; evaluation file generated | parse `.automind/tasks/offline_demo_smoke/evaluation.json` | Prepare evaluation path -> parse `evaluation.json` -> assert `result=pass` and `nextAction=finish`. | `result=pass`, `nextAction=finish`. | TC-F02 | yes |
| TC-F04 | R03 / AC-004 | Smoke | runtime | TC-F03 passed; logs directory exists | `<AUTOMIND_CLI> summary offline_demo_smoke` and `<AUTOMIND_CLI> record-check offline_demo_smoke` | Prepare passed evaluation -> generate summary -> run record-check -> assert summary and reusable record evidence exist. | `summary.md` exists and record-check passes. | TC-F03 | yes |
| TC-QA01 | R01..R03 | Quality: Artifact continuity | static/runtime | TC-F01 passed | `<AUTOMIND_CLI> workflow-check offline_demo_smoke` | Run workflow/record consistency checks when available. | No hard continuity issues; warnings are acceptable for smoke-only metadata. | TC-F01 | no |

## First functional batch

TC-F01, TC-F02, TC-F03, TC-F04.
""")
    write(TASK_DIR / "Plan.md", """# Plan

## First functional batch

TC-F01, TC-F02, TC-F03, TC-F04.

## Concrete verification command/tool path

- Primary command/tool path: `<AUTOMIND_CLI> script-command offline_demo_smoke 1`.
- Runtime evidence is required for TC-F02/TC-F03/TC-F04; static file checks alone are not sufficient for finish.

## Steps

1. Use the generic `script-command` adapter for TC-F02.
2. Run a tiny Python command that prints deterministic output.
3. Treat exit code 0 as pass and check TC-F03.
4. Generate summary and run record-check for TC-F04.

## Implementation Checklist

| ID | Work item | Source | Status | Owner | Evidence | Notes |
|----|-----------|--------|--------|-------|----------|-------|
| T01 | Create offline smoke task artifacts | R01 / AC-001 / TC-F01 | done | generator | Brainstorm/Requirements/TestCases/Plan/Validation/runtime-state | Smoke setup writes artifacts directly. |
| T02 | Configure deterministic script-command verifier | R02 / AC-002 / TC-F02 | done | generator | runtime-state.json verifyCommand | No product code changes. |

## Verification Checklist

| ID | TestCase | Source | Required | Status | Owner | Evidence | Notes |
|----|----------|--------|----------|--------|-------|----------|-------|
| TC-F01 | Artifact existence check | R01 / AC-001 | yes | todo | evaluator | - | Checked by smoke script. |
| TC-F02 | script-command evaluator | R02 / AC-002 | yes | todo | evaluator | - | Filled after script-command. |
| TC-F03 | evaluation.json result check | R03 / AC-003 | yes | todo | evaluator | - | Filled after script-command. |
| TC-F04 | summary and record-check | R03 / AC-004 | yes | todo | evaluator | - | Filled after summary/record-check. |
| TC-QA01 | Artifact continuity | R01..R03 | no | todo | evaluator | - | Optional quality continuity check. |

This demo intentionally avoids mobile devices. It proves the harness loop shape, not Android/iOS capability.
""")
    write(TASK_DIR / "Delivery.md", """# Delivery

## Iteration 1 - Offline demo setup

- Created task files for a no-device smoke demo.
- No application code changes.
- Covered testcases: TC-F01, TC-F02, TC-F03, TC-F04.
- Evaluator should run the configured `scriptCommand`.
""")
    write(TASK_DIR / "Validation.md", "# Validation\n")
    write_json(TASK_DIR / "runtime-state.json", {
        "taskId": TASK_CODE,
        "taskType": "script",
        "status": "ready",
        "iteration": 0,
        "currentOwner": "evaluator",
        "nextAction": "run_script_command",
        "scriptCommand": script_command,
        "planner": {
            "mode": "offline_demo_scaffold",
            "artifactsRefined": True,
            "needsUserInput": False,
            "preImplementationReview": {
                "required": True,
                "decision": "auto_proceed",
                "needsUserInput": False,
                "confidence": "high",
                "reason": "Fixed local script-command verifier; no product code changes or external/device side effects.",
                "questions": [],
                "checkedAt": now,
            },
            "notes": "Offline smoke can proceed without human confirmation.",
        },
        "createdAt": now,
        "updatedAt": now,
    })

    proc = run([str(ROOT / "automind.sh"), "script-command", TASK_CODE, "1"])
    (TASK_DIR / "logs" / "offline-demo-script-command-wrapper.log").parent.mkdir(parents=True, exist_ok=True)
    write(TASK_DIR / "logs" / "offline-demo-script-command-wrapper.log", proc.stdout + proc.stderr)
    if proc.returncode != 0:
        print(proc.stdout + proc.stderr)
        return proc.returncode

    completion_proc = run([str(ROOT / "automind.sh"), "completion-check", TASK_CODE])
    write(TASK_DIR / "logs" / "offline-demo-completion-check-wrapper.log", completion_proc.stdout + completion_proc.stderr)
    if completion_proc.returncode != 0:
        print(completion_proc.stdout + completion_proc.stderr)
        return completion_proc.returncode

    summary_proc = run([str(ROOT / "automind.sh"), "summary", TASK_CODE])
    write(TASK_DIR / "logs" / "offline-demo-summary-wrapper.log", summary_proc.stdout + summary_proc.stderr)
    if summary_proc.returncode != 0:
        print(summary_proc.stdout + summary_proc.stderr)
        return summary_proc.returncode

    record_proc = run([str(ROOT / "automind.sh"), "record-check", TASK_CODE])
    write(TASK_DIR / "logs" / "offline-demo-record-check-wrapper.log", record_proc.stdout + record_proc.stderr)
    if record_proc.returncode != 0:
        print(record_proc.stdout + record_proc.stderr)
        return record_proc.returncode

    workflow_proc = run([str(ROOT / "automind.sh"), "workflow-check", TASK_CODE])
    write(TASK_DIR / "logs" / "offline-demo-workflow-check-wrapper.log", workflow_proc.stdout + workflow_proc.stderr)
    if workflow_proc.returncode != 0:
        print(workflow_proc.stdout + workflow_proc.stderr)
        return workflow_proc.returncode

    evaluation_path = TASK_DIR / "evaluation.json"
    evaluation = json.loads(evaluation_path.read_text())
    required = [
        TASK_DIR / "Requirements.md",
        TASK_DIR / "Plan.md",
        TASK_DIR / "Delivery.md",
        TASK_DIR / "Validation.md",
        TASK_DIR / "evaluation.json",
        TASK_DIR / "VerificationLedger.json",
        TASK_DIR / "runtime-state.json",
        TASK_DIR / "summary.md",
        TASK_DIR / "logs" / "iter-1" / "commands.md",
        TASK_DIR / "logs" / "iter-1" / "evaluator.log",
        TASK_DIR / "logs" / "iter-1" / "env.json",
    ]
    missing = [str(p.relative_to(WORKSPACE_ROOT)) if str(p).startswith(str(WORKSPACE_ROOT)) else str(p) for p in required if not p.exists()]
    checks = {
        "evaluation_result": evaluation.get("result") == "pass",
        "evaluation_nextAction": evaluation.get("nextAction") == "finish",
        "required_files_present": not missing,
        "record_check_passed": ("PASS" in record_proc.stdout or "Record check passed" in record_proc.stdout or "\u8bb0\u5f55\u68c0\u67e5\u901a\u8fc7" in record_proc.stdout),
        "workflow_check_passed": ("Workflow check passed" in workflow_proc.stdout),
        "completion_check_passed": ("Completion check passed" in completion_proc.stdout),
        "completion_coverage_passed": evaluation.get("coverage", {}).get("completionCheck") == "pass",
    }
    ok = all(checks.values())
    smoke_summary = {
        "result": "pass" if ok else "fail",
        "task": TASK_CODE,
        "taskDir": str(TASK_DIR.relative_to(WORKSPACE_ROOT)),
        "checks": checks,
        "missing": missing,
        "evaluation": str(evaluation_path.relative_to(WORKSPACE_ROOT)),
        "summary": str((TASK_DIR / "summary.md").relative_to(WORKSPACE_ROOT)),
    }
    write_json(TASK_DIR / "offline-demo-smoke-summary.json", smoke_summary)
    print(json.dumps(smoke_summary, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
