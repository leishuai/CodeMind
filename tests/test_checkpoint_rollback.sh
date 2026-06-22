#!/usr/bin/env bash
# TC-R09: checkpoint create + rollback restore.
# AC-018: 创建 checkpoint 后 runtime-state.json 含快照副本。
# AC-019: 修改 task 内文件 → automind rollback → 文件还原。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/.automind/tasks/__tc_r09_sandbox/logs/iter-1"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/rollback.log"
: > "$LOG"

# Use a sandbox workspace under /tmp so we don't pollute the real workspace.
SANDBOX="$(mktemp -d /tmp/automind_t9_XXXX)"
trap 'rm -rf "$SANDBOX"' EXIT
export AUTOMIND_WORKSPACE_ROOT="$SANDBOX"
TASK_CODE="t9_smoke"
TASK_DIR="$SANDBOX/.automind/tasks/$TASK_CODE"
mkdir -p "$TASK_DIR"

# Original Plan.md content used as the snapshot baseline.
ORIGINAL="# Plan.md original content - $(date +%s)"
echo "$ORIGINAL" > "$TASK_DIR/Plan.md"
# runtime-state.json must exist for checkpoint create to succeed
echo '{"taskId":"t9_smoke","status":"ready"}' > "$TASK_DIR/runtime-state.json"

# Step 1: create checkpoint
"$ROOT/automind.sh" checkpoint create "$TASK_CODE" "before mutation" >> "$LOG" 2>&1

# Find the checkpoint id (cp-001)
CP_ID="cp-001"

# Step 2: mutate Plan.md
echo "# MUTATED $(date +%s)" > "$TASK_DIR/Plan.md"
MUTATED="$(cat "$TASK_DIR/Plan.md")"
if [[ "$MUTATED" == "$ORIGINAL" ]]; then
    echo "ERROR: mutation did not take effect" | tee -a "$LOG"
    exit 1
fi

# Step 3: rollback
"$ROOT/automind.sh" rollback "$TASK_CODE" "$CP_ID" >> "$LOG" 2>&1
echo "[t9] rollback executed: restored=Plan.md" >> "$LOG"

# Step 4: assert content restored
RESTORED="$(cat "$TASK_DIR/Plan.md")"
if [[ "$RESTORED" != "$ORIGINAL" ]]; then
    echo "ERROR: content not restored" | tee -a "$LOG"
    echo "expected: $ORIGINAL" >> "$LOG"
    echo "got:      $RESTORED" >> "$LOG"
    exit 1
fi

echo "[t9] PASS: file restored from checkpoint" >> "$LOG"
echo "PASS: TC-R09 checkpoint+rollback"
exit 0
