#!/usr/bin/env bash
# Requirements.md canonical contract smoke.
# Asserts that a synthetic task with only Requirements.md (no Spec.md/Require.md)
# passes workflow-check artifact shape, and that the dual-mode detection picks
# the new protocol.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/.automind/tasks/__req_single_sandbox/logs/iter-1"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/single-file.log"
: > "$LOG"

SANDBOX="$(mktemp -d /tmp/automind_reqsingle_XXXX)"
trap 'rm -rf "$SANDBOX"' EXIT
export AUTOMIND_WORKSPACE_ROOT="$SANDBOX"

TASK="reqsingle_smoke"
TASK_DIR="$SANDBOX/.automind/tasks/$TASK"
mkdir -p "$TASK_DIR/logs/iter-1"

cat > "$TASK_DIR/Brainstorm.md" <<'MD'
# Brainstorm

## Idea expansion
Single-file Requirements.md should be detected by workflow-check.

## Approaches
- A: keep obsolete two-file form
- B: merge into Requirements.md (chosen)

## Recommendation
B.

## Pre-implementation review
auto_proceed (smoke).

## Assumptions / Questions
- Detector treats Requirements.md as authoritative when both Rxx and AC-xxx exist.
MD

cat > "$TASK_DIR/Requirements.md" <<'MD'
# Requirements (canonical contract)

## User Request
single-file smoke

## Non-goals
- not exercising real platform runners

## Requirements with inline Acceptance Criteria

### R01 — single-file detection
- **AC-001**: workflow-check picks Requirements.md when present
  - Verification method: workflow-check
  - Covered by: see TestCases.md (functional key path)
  - Timeout policy: Retry 3 times
MD

cat > "$TASK_DIR/TestCases.md" <<'MD'
# TestCases

## Design principles
- Functional Key Path covers single-file detection.

## Testcase list

| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeAutonomy command | Steps / verification method | Expected evidence/result | Dependency | Required? |
|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|
| TC-F01 | R01 / AC-001 | Functional / Key Path | unit | preflight: requirements.md created | command: automind workflow-check | run script-command and assert exit_code=0 | log shows result=pass, evidence path exists | none | yes |

## Quality
- not applicable for this smoke

## Next step
Evaluator runs script-command.
MD

cat > "$TASK_DIR/Plan.md" <<'MD'
# Plan

## Implementation Checklist

| ID | Description | Owner | Status | Source |
|----|-------------|-------|--------|--------|
| T01 | wire Requirements.md detector | generator | pending | R01 |

## Verification Checklist

| ID | Description | Owner | Status | Evidence |
|----|-------------|-------|--------|----------|
| TC-F01 | single-file detection | evaluator | pending | logs/iter-1/script-command.log |

## First functional batch
- TC-F01 (functional batch)

## Verification command
- automind workflow-check (command)

## Reuse considered
- Reuse.md not applicable; fresh smoke.
MD

cat > "$TASK_DIR/runtime-state.json" <<'MD'
{
  "task": "reqsingle_smoke",
  "userInput": "single-file smoke",
  "taskType": "script",
  "status": "planning",
  "currentOwner": "planner",
  "iteration": 1,
  "planner": {
    "preImplementationReview": {
      "decision": "auto_proceed",
      "decisionBundle": {
        "goal": "smoke single-file detection",
        "verificationTarget": "not_applicable",
        "confirmedAt": "2026-05-28T00:00:00",
        "confirmedBy": "smoke"
      }
    }
  }
}
MD

# Run workflow-check via the python orchestrator directly so we exercise the
# same code path the CLI uses. We do not require all warnings to clear — only
# that result=pass (no issues) for shape and IDs.
PY="$ROOT/.venv-tests/bin/python"
[[ -x "$PY" ]] || PY="python3"

REPORT_JSON="$TASK_DIR/workflow-report.json"
"$PY" -c "
import json, sys
sys.path.insert(0, '$ROOT')
from orchestrator.workflow import check_workflow_consistency, is_single_file_protocol
from pathlib import Path
ok, report = check_workflow_consistency('$TASK')
report['singleFileProtocol'] = is_single_file_protocol(Path('$TASK_DIR'))
Path('$REPORT_JSON').write_text(json.dumps(report, indent=2))
" >> "$LOG" 2>&1

if ! grep -q '"singleFileProtocol": true' "$REPORT_JSON"; then
    echo "FAIL: detector did not classify task as single-file" | tee -a "$LOG"
    cat "$REPORT_JSON" | tee -a "$LOG"
    exit 1
fi

if ! grep -q '"result": "pass"' "$REPORT_JSON"; then
    echo "FAIL: workflow-check did not pass for single-file Requirements.md" | tee -a "$LOG"
    cat "$REPORT_JSON" | tee -a "$LOG"
    exit 1
fi

echo "PASS: Requirements.md canonical contract workflow-check"
exit 0
