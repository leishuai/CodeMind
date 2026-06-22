#!/usr/bin/env bash
# Skill-mode automation smoke.
# Validates the four primitives that keep the skill/command-mode harness loop
# moving without an external orchestrator while-loop:
#   1) workflow-check report carries a nextActionPrompt field on pass and fail
#   2) completion-check report carries a nextActionPrompt field on fail
#   3) .automind/current-task marker is writable/readable/clearable and is
#      honored by `automind continue` (env override + file fallback)
#   4) tick-iteration increments iteration and exits non-zero when budget
#      is exhausted
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX="$(mktemp -d /tmp/automind_skillmode_XXXX)"
trap 'rm -rf "$SANDBOX"' EXIT
export AUTOMIND_WORKSPACE_ROOT="$SANDBOX"

LOG_DIR="$SANDBOX/.automind/tasks/__skillmode_sandbox/logs/iter-1"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/skill-mode.log"
: > "$LOG"

PY="$ROOT/.venv-tests/bin/python"
[[ -x "$PY" ]] || PY="python3"

# ----------------------------------------------------------------------------
# 1. Synthesize a minimal single-file-protocol task that workflow-check passes.
# ----------------------------------------------------------------------------
TASK="skillmode_smoke"
TASK_DIR="$SANDBOX/.automind/tasks/$TASK"
mkdir -p "$TASK_DIR/logs/iter-1"

cat > "$TASK_DIR/Brainstorm.md" <<'MD'
# Brainstorm

## Idea expansion
Skill-mode automation smoke for nextActionPrompt + current-task + tick.

## Approaches
- A: rely on chat memory (rejected)
- B: state-file driven (chosen)

## Recommendation
B.

## Pre-implementation review
auto_proceed (smoke).

## Assumptions / Questions
- workflow-check returns nextActionPrompt on pass and fail.
MD

cat > "$TASK_DIR/Requirements.md" <<'MD'
# Requirements (canonical contract)

## User Request
skill-mode smoke

## Non-goals
- not exercising real platform runners

## Requirements with inline Acceptance Criteria

### R01 — nextActionPrompt is emitted
- **AC-001**: workflow-check report carries nextActionPrompt
  - Verification method: workflow-check
  - Covered by: see TestCases.md (functional key path)
  - Timeout policy: Retry 3 times
MD

cat > "$TASK_DIR/TestCases.md" <<'MD'
# TestCases

## Design principles
- Functional Key Path covers nextActionPrompt emission.

## Testcase list

| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / AutoMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |
|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|
| TC-F01 | R01 / AC-001 | Functional / Key Path | unit | preflight: artifacts | command: automind workflow-check | run script-command and assert exit_code=0 | nextActionPrompt present | none | yes |

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
| T01 | wire skill-mode primitives | generator | pending | R01 |

## Verification Checklist

| ID | Description | Owner | Status | Evidence |
|----|-------------|-------|--------|----------|
| TC-F01 | nextActionPrompt emission | evaluator | pending | logs/iter-1/script-command.log |

## First functional batch
- TC-F01 (functional batch)

## Verification command
- automind workflow-check (command)

## Reuse considered
- Reuse.md not applicable; fresh smoke.
MD

cat > "$TASK_DIR/runtime-state.json" <<'JSON'
{
  "task": "skillmode_smoke",
  "userInput": "skill-mode smoke",
  "taskType": "script",
  "status": "planning",
  "currentOwner": "planner",
  "iteration": 0,
  "maxIterations": 3,
  "planner": {
    "preImplementationReview": {
      "decision": "auto_proceed",
      "decisionBundle": {
        "goal": "smoke skill-mode automation",
        "verificationTarget": "not_applicable",
        "confirmedAt": "2026-05-28T00:00:00",
        "confirmedBy": "smoke"
      }
    }
  }
}
JSON

# ----------------------------------------------------------------------------
# 2. workflow-check pass + fail must both carry nextActionPrompt.
# ----------------------------------------------------------------------------
REPORT_PASS="$TASK_DIR/workflow-pass.json"
"$PY" -c "
import json, sys
sys.path.insert(0, '$ROOT')
from orchestrator.workflow import check_workflow_consistency
ok, report = check_workflow_consistency('$TASK')
from pathlib import Path
Path('$REPORT_PASS').write_text(json.dumps(report, indent=2))
" >> "$LOG" 2>&1

if ! grep -q '"result": "pass"' "$REPORT_PASS"; then
    echo "FAIL: workflow-check did not pass for synthetic task" | tee -a "$LOG"
    cat "$REPORT_PASS" | tee -a "$LOG"; exit 1
fi
if ! grep -q '"nextActionPrompt"' "$REPORT_PASS"; then
    echo "FAIL: workflow-check pass report missing nextActionPrompt" | tee -a "$LOG"
    cat "$REPORT_PASS" | tee -a "$LOG"; exit 1
fi

# Force a failure by deleting Plan.md, then re-running.
mv "$TASK_DIR/Plan.md" "$TASK_DIR/Plan.md.bak"
REPORT_FAIL="$TASK_DIR/workflow-fail.json"
"$PY" -c "
import json, sys
sys.path.insert(0, '$ROOT')
from orchestrator.workflow import check_workflow_consistency
ok, report = check_workflow_consistency('$TASK')
from pathlib import Path
Path('$REPORT_FAIL').write_text(json.dumps(report, indent=2))
" >> "$LOG" 2>&1
mv "$TASK_DIR/Plan.md.bak" "$TASK_DIR/Plan.md"

if ! grep -q '"result": "fail"' "$REPORT_FAIL"; then
    echo "FAIL: workflow-check should have failed without Plan.md" | tee -a "$LOG"
    cat "$REPORT_FAIL" | tee -a "$LOG"; exit 1
fi
if ! grep -q '"nextActionPrompt"' "$REPORT_FAIL"; then
    echo "FAIL: workflow-check fail report missing nextActionPrompt" | tee -a "$LOG"
    cat "$REPORT_FAIL" | tee -a "$LOG"; exit 1
fi
if ! grep -q 'workflow-check FAILED' "$REPORT_FAIL"; then
    echo "FAIL: nextActionPrompt should mention FAILED in fail branch" | tee -a "$LOG"
    cat "$REPORT_FAIL" | tee -a "$LOG"; exit 1
fi

# ----------------------------------------------------------------------------
# 3. completion-check fail must carry nextActionPrompt with retry hints.
# ----------------------------------------------------------------------------
REPORT_COMP="$TASK_DIR/completion-fail.json"
"$PY" -c "
import json, sys
sys.path.insert(0, '$ROOT')
from orchestrator.completion import build_completion_report
from orchestrator.state import get_task_dir
from pathlib import Path
report = build_completion_report(get_task_dir('$TASK'))
Path('$REPORT_COMP').write_text(json.dumps(report, indent=2))
" >> "$LOG" 2>&1

if ! grep -q '"nextActionPrompt"' "$REPORT_COMP"; then
    echo "FAIL: completion-check report missing nextActionPrompt" | tee -a "$LOG"
    cat "$REPORT_COMP" | tee -a "$LOG"; exit 1
fi

# ----------------------------------------------------------------------------
# 4. current-task marker round-trip (write -> read file -> read env -> clear).
# ----------------------------------------------------------------------------
"$PY" -c "
import os, sys
sys.path.insert(0, '$ROOT')
from orchestrator.state import write_current_task, read_current_task, clear_current_task
write_current_task('$TASK')
assert read_current_task() == '$TASK', 'file fallback failed'
os.environ['AUTOMIND_CURRENT_TASK'] = 'env_override'
assert read_current_task() == 'env_override', 'env override failed'
del os.environ['AUTOMIND_CURRENT_TASK']
clear_current_task()
assert read_current_task() is None, 'clear failed'
print('current-task round-trip OK')
" >> "$LOG" 2>&1 || { echo "FAIL: current-task round-trip" | tee -a "$LOG"; tail -20 "$LOG"; exit 1; }

# ----------------------------------------------------------------------------
# 5. tick-iteration increments and exits non-zero on budget exhaustion.
# ----------------------------------------------------------------------------
# maxIterations=3 in runtime-state.json above; configured budget = max(MAX_ITERATIONS, 3)
# MAX_ITERATIONS default is 1000, so we override via env to make budget small.
export AUTOMIND_MAX_ITERATIONS=2

# Reset iteration counter to 0 so we can drive it deterministically.
"$PY" -c "
import json, sys
sys.path.insert(0, '$ROOT')
from pathlib import Path
p = Path('$TASK_DIR/runtime-state.json')
data = json.loads(p.read_text())
data['iteration'] = 0
data['maxIterations'] = 2
p.write_text(json.dumps(data, indent=2))
"

CLI="$ROOT/automind.sh"

# tick #1 -> iteration=1, budget=2, not exhausted, exit 0
out1="$("$CLI" tick-iteration "$TASK" generator 2>&1 || true)"
echo "$out1" >> "$LOG"
if ! echo "$out1" | grep -q '"iteration": 1'; then
    echo "FAIL: tick #1 did not produce iteration=1" | tee -a "$LOG"
    echo "$out1" | tee -a "$LOG"; exit 1
fi
if ! echo "$out1" | grep -q '"budgetExhausted": false'; then
    echo "FAIL: tick #1 should not be exhausted" | tee -a "$LOG"; exit 1
fi

# tick #2 -> iteration=2, budget=2, not exhausted (nxt > budget condition is strict).
"$CLI" tick-iteration "$TASK" generator >> "$LOG" 2>&1 || true

# tick #3 -> iteration=3, exhausted, exit 2.
set +e
"$CLI" tick-iteration "$TASK" generator > "$TASK_DIR/tick3.json" 2>> "$LOG"
rc=$?
set -e
if [[ "$rc" -ne 2 ]]; then
    echo "FAIL: tick on exhausted budget should exit 2, got $rc" | tee -a "$LOG"
    cat "$TASK_DIR/tick3.json" | tee -a "$LOG"; exit 1
fi
if ! grep -q '"budgetExhausted": true' "$TASK_DIR/tick3.json"; then
    echo "FAIL: tick3 missing budgetExhausted=true" | tee -a "$LOG"
    cat "$TASK_DIR/tick3.json" | tee -a "$LOG"; exit 1
fi
if ! grep -q '"nextActionPrompt"' "$TASK_DIR/tick3.json"; then
    echo "FAIL: tick3 missing nextActionPrompt" | tee -a "$LOG"
    cat "$TASK_DIR/tick3.json" | tee -a "$LOG"; exit 1
fi

unset AUTOMIND_MAX_ITERATIONS

# ----------------------------------------------------------------------------
# 6. `automind continue` must emit resume payload with nextActionPrompt.
# ----------------------------------------------------------------------------
"$PY" -c "
import sys
sys.path.insert(0, '$ROOT')
from orchestrator.state import write_current_task
write_current_task('$TASK')
"
out_cont="$("$CLI" continue 2>&1 || true)"
echo "$out_cont" >> "$LOG"
if ! echo "$out_cont" | grep -q '"task": "skillmode_smoke"'; then
    echo "FAIL: continue did not return active task code" | tee -a "$LOG"
    echo "$out_cont" | tee -a "$LOG"; exit 1
fi
if ! echo "$out_cont" | grep -q '"nextActionPrompt"'; then
    echo "FAIL: continue missing nextActionPrompt" | tee -a "$LOG"
    echo "$out_cont" | tee -a "$LOG"; exit 1
fi

echo "PASS: skill-mode automation smoke (workflow + completion + current-task + tick + continue)"
exit 0
