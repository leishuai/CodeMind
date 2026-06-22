#!/usr/bin/env bash
# TC-R05: cmd_doctor interactive prompt + --auto-resume.
# AC-010: doctor 扫描后对每个 recoverable 任务交互 prompt y/n/all/skip。
# AC-011: --auto-resume flag 静默对全部 recoverable 调 resume。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/.automind/tasks/__tc_r05_sandbox/logs/iter-1"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/doctor-interactive.log"
: > "$LOG"

# Sandbox workspace under /tmp.
SANDBOX="$(mktemp -d /tmp/automind_t5_XXXX)"
trap 'rm -rf "$SANDBOX"' EXIT
export AUTOMIND_WORKSPACE_ROOT="$SANDBOX"

# Helper: create a stalled task in sandbox.
make_stalled() {
    local code="$1"
    local task_dir="$SANDBOX/.automind/tasks/$code"
    mkdir -p "$task_dir"
    # heartbeat.lastBeatAt very old (1970) → stalled
    cat > "$task_dir/runtime-state.json" <<EOF
{"taskId":"$code","status":"generating","heartbeat":{"lastBeatAt":"1970-01-01T00:00:00","owner":"generator","note":"-"}}
EOF
}

# Helper: create a healthy active task in sandbox.
make_active() {
    local code="$1"
    local task_dir="$SANDBOX/.automind/tasks/$code"
    mkdir -p "$task_dir"
    local now
    now="$(date +%Y-%m-%dT%H:%M:%S)"
    cat > "$task_dir/runtime-state.json" <<EOF
{"taskId":"$code","status":"generating","heartbeat":{"lastBeatAt":"$now","owner":"generator","note":"-"}}
EOF
}

# Scenario 1: 1 stalled + echo y → resume called: 1
make_stalled "t5_a"
make_active  "t5_b"
echo "--- scenario 1: echo y → resume 1 ---" >> "$LOG"
echo "y" | "$ROOT/automind.sh" doctor --dry-run >> "$LOG" 2>&1
rm -rf "$SANDBOX/.automind"

# Scenario 2: 1 stalled + echo n → skipped: 1
make_stalled "t5_c"
echo "--- scenario 2: echo n → skipped 1 ---" >> "$LOG"
echo "n" | "$ROOT/automind.sh" doctor --dry-run >> "$LOG" 2>&1
rm -rf "$SANDBOX/.automind"

# Scenario 3: 2 stalled + --auto-resume < /dev/null → resume 2
make_stalled "t5_d"
make_stalled "t5_e"
echo "--- scenario 3: --auto-resume < /dev/null → resume 2 ---" >> "$LOG"
"$ROOT/automind.sh" doctor --auto-resume --dry-run < /dev/null >> "$LOG" 2>&1

# Assertions
if ! grep -q "resume called: 1" "$LOG"; then
    echo "ERROR: 'resume called: 1' not found in log" | tee -a "$LOG"
    exit 1
fi
if ! grep -q "skipped: 1" "$LOG"; then
    echo "ERROR: 'skipped: 1' not found in log" | tee -a "$LOG"
    exit 1
fi
if ! grep -q "resume called: 2" "$LOG"; then
    echo "ERROR: 'resume called: 2' not found in log (auto-resume scenario)" | tee -a "$LOG"
    exit 1
fi

echo "[t5] PASS: doctor interactive + --auto-resume"
echo "PASS: TC-R05 doctor interactive"
exit 0
