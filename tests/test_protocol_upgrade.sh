#!/usr/bin/env bash
# TC-R10: Requirements.md canonical contract.
# AC-020: scaffold 生成 Requirements.md（含 R01 + AC-001 行）。
# AC-021: 新 scaffold 不生成 Spec.md / Require.md。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/.automind/tasks/__tc_r10_sandbox/logs/iter-1"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/protocol-upgrade.log"
: > "$LOG"

# Sandbox under /tmp.
SANDBOX="$(mktemp -d /tmp/automind_t10_XXXX)"
trap 'rm -rf "$SANDBOX"' EXIT
export AUTOMIND_WORKSPACE_ROOT="$SANDBOX"

# Step 1: scaffold a fresh task and assert Requirements.md exists with R01 + AC-001.
"$ROOT/automind.sh" scaffold "test requirements canonical contract" >> "$LOG" 2>&1

# Find the scaffolded task dir (most recent).
NEW_TASK_DIR="$(find "$SANDBOX/.automind/tasks" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [[ -z "${NEW_TASK_DIR:-}" ]]; then
    echo "ERROR: scaffold did not create a task dir under $SANDBOX/.automind/tasks" | tee -a "$LOG"
    exit 1
fi

REQ_FILE="$NEW_TASK_DIR/Requirements.md"
if [[ ! -f "$REQ_FILE" ]]; then
    echo "ERROR: Requirements.md not generated at $REQ_FILE" | tee -a "$LOG"
    exit 1
fi
if ! grep -q "R01" "$REQ_FILE"; then
    echo "ERROR: Requirements.md missing R01 marker" | tee -a "$LOG"
    exit 1
fi
if ! grep -q "AC-001" "$REQ_FILE"; then
    echo "ERROR: Requirements.md missing AC-001 marker" | tee -a "$LOG"
    exit 1
fi
echo "[t10] AC-020 PASS: Requirements.md contains R01 + AC-001" >> "$LOG"

# Step 2: canonical-only scaffold — new tasks must not generate legacy files.
if [[ -e "$NEW_TASK_DIR/Spec.md" || -e "$NEW_TASK_DIR/Require.md" ]]; then
    echo "ERROR: scaffold generated legacy Spec.md/Require.md" | tee -a "$LOG"
    exit 1
fi
echo "[t10] AC-021 PASS: scaffold did not generate Spec.md/Require.md" >> "$LOG"

echo "PASS: TC-R10 Requirements.md canonical contract"
exit 0
