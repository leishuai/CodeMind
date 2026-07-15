#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DEMO="$ROOT/demos/android-minimal-demo"
WORKSPACE_ROOT="${AUTOMIND_WORKSPACE_ROOT:-$(pwd)}"
WORK_DEMO="$WORKSPACE_ROOT/.automind/tmp/android-probe-flow-self-repair-smoke"
TASK_DIR="${TASK_DIR:-$WORKSPACE_ROOT/.automind/tasks/android_probe_flow_self_repair_smoke}"
PY="${ANDROID_TOOLS_PYTHON:-$WORKSPACE_ROOT/.venv-android-tools/bin/python}"
APK_PATH="$WORK_DEMO/build/AutoMindAndroidDemo-debug.apk"
PKG="ai.openclaw.automind.demo"
ACTIVITY=".MainActivity"

export PATH="$HOME/Library/Android/sdk/platform-tools:$PATH"

if [[ ! -x "$PY" ]]; then
  echo "[CodeMind] Android tools python not found: $PY" >&2
  echo "[CodeMind] Expected project venv: $WORKSPACE_ROOT/.venv-android-tools/" >&2
  exit 2
fi

"$PY" - <<'PY'
import importlib.util, sys
missing = [m for m in ["adbutils", "uiautomator2"] if importlib.util.find_spec(m) is None]
if missing:
    print("[CodeMind] Missing Android python modules: " + ", ".join(missing), file=sys.stderr)
    sys.exit(2)
PY

rm -rf "$WORK_DEMO"
mkdir -p "$(dirname "$WORK_DEMO")"
cp -R "$SRC_DEMO" "$WORK_DEMO"
mkdir -p "$TASK_DIR/logs/iter-1/probe-flow" "$TASK_DIR/logs/iter-2/probe-flow"

MAIN="$WORK_DEMO/app/src/main/java/ai/openclaw/automind/demo/MainActivity.java"
FLOW="$TASK_DIR/probe-flow.android.json"

cat > "$TASK_DIR/runtime-state.json" <<JSON
{
  "taskId": "android_probe_flow_self_repair_smoke",
  "userInput": "Android dynamic probe-flow self-repair smoke: iter-1 broken, iter-2 completed",
  "taskType": "android",
  "harnessProfile": {"name": "android-v1"},
  "androidApp": {
    "apk": "$APK_PATH",
    "package": "$PKG",
    "activity": "$ACTIVITY",
    "buildCommand": "$WORK_DEMO/build_apk.sh"
  },
  "status": "created",
  "iteration": 0
}
JSON

cat > "$TASK_DIR/Requirements.md" <<'MD'
# Requirements - Android Dynamic Probe Flow Self Repair Smoke

## Requirements with inline Acceptance Criteria

### R01 — Android probe-flow self-repair
- **AC-001**: Launch demo APK package `ai.openclaw.automind.demo` activity `.MainActivity`.
  - Verification method: android-probe-flow / TC-F01
- **AC-002**: Assert `CodeMind Android Harness Demo` and `Probe state: Idle`, tap `probe_button` / `Run Probe`, then assert `Probe state: Completed`.
  - Verification method: android-probe-flow / TC-F01
MD

cat > "$FLOW" <<JSON
{
  "platform": "android",
  "name": "Android dynamic probe-flow self-repair smoke",
  "app": {
    "apk": "$APK_PATH",
    "package": "$PKG",
    "activity": "$ACTIVITY"
  },
  "steps": [
    {"type": "install", "name": "install apk"},
    {"type": "launch", "name": "launch app"},
    {"type": "assert_text", "name": "assert title", "text": "CodeMind Android Harness Demo"},
    {"type": "assert_text", "name": "assert idle", "text": "Probe state: Idle"},
    {"type": "tap", "name": "tap probe button", "selector": {"desc": "probe_button", "text": "Run Probe"}},
    {"type": "assert_text", "name": "assert completed", "text": "Probe state: Completed"},
    {"type": "screenshot", "name": "final screenshot", "output": "final-screenshot"},
    {"type": "stop", "name": "stop app"}
  ]
}
JSON

python3 - <<PY
from pathlib import Path
p = Path("$MAIN")
s = p.read_text()
s = s.replace('result.setText("Probe state: Completed");', 'result.setText("Probe state: Broken");')
p.write_text(s)
PY

"$WORK_DEMO/build_apk.sh" > "$TASK_DIR/logs/iter-1/android-build.log" 2>&1
set +e
"$PY" "$ROOT/scripts/android_probe_flow_runner.py" \
  --flow "$FLOW" \
  --out "$TASK_DIR/logs/iter-1/probe-flow" \
  > "$TASK_DIR/logs/iter-1/evaluator.log" 2>&1
ITER1_CODE=$?
set -e
if [[ "$ITER1_CODE" -eq 0 ]]; then
  echo "[CodeMind] Expected iter-1 dynamic probe-flow to fail, but it passed" >&2
  exit 1
fi

python3 - <<PY
from pathlib import Path
p = Path("$MAIN")
s = p.read_text()
s = s.replace('result.setText("Probe state: Broken");', 'result.setText("Probe state: Completed");')
p.write_text(s)
PY

"$WORK_DEMO/build_apk.sh" > "$TASK_DIR/logs/iter-2/android-build.log" 2>&1
"$PY" "$ROOT/scripts/android_probe_flow_runner.py" \
  --flow "$FLOW" \
  --out "$TASK_DIR/logs/iter-2/probe-flow" \
  > "$TASK_DIR/logs/iter-2/evaluator.log" 2>&1

python3 - <<PY
import json
from pathlib import Path
root = Path("$TASK_DIR")
iter1 = json.loads((root / "logs/iter-1/probe-flow/probe-flow-summary.json").read_text())
iter2 = json.loads((root / "logs/iter-2/probe-flow/probe-flow-summary.json").read_text())
summary = {
    "result": "pass" if iter1.get("result") == "fail" and iter2.get("result") == "pass" else "fail",
    "iteration1": iter1.get("result"),
    "iteration2": iter2.get("result"),
    "expected": "iter-1 fail, iter-2 pass via dynamic probe-flow runner",
    "artifacts": [
        str(root / "logs/iter-1/probe-flow/probe-flow-summary.json"),
        str(root / "logs/iter-2/probe-flow/probe-flow-summary.json"),
    ],
}
(root / "dynamic-probe-flow-self-repair-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False, indent=2))
raise SystemExit(0 if summary["result"] == "pass" else 1)
PY
