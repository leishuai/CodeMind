#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DEMO="$ROOT/demos/android-minimal-demo"
WORKSPACE_ROOT="${AUTOMIND_WORKSPACE_ROOT:-$(pwd)}"
WORK_DEMO="$WORKSPACE_ROOT/.automind/tmp/android-self-repair-smoke"
TASK_DIR="${TASK_DIR:-$WORKSPACE_ROOT/.automind/tasks/android_self_repair_smoke}"
ANDROID_VENV="${ANDROID_VENV:-$WORKSPACE_ROOT/.venv-android-tools}"
APK_PATH="$WORK_DEMO/build/AutoMindAndroidDemo-debug.apk"
PKG="ai.openclaw.automind.demo"
ACTIVITY=".MainActivity"

if [[ ! -d "$ANDROID_VENV" ]]; then
  echo "[CodeAutonomy] Android tool venv not found: $ANDROID_VENV" >&2
  echo "[CodeAutonomy] Create it with: python3 -m venv $ANDROID_VENV && source $ANDROID_VENV/bin/activate && pip install adbutils uiautomator2" >&2
  exit 2
fi

export PATH="$HOME/Library/Android/sdk/platform-tools:$PATH"
source "$ANDROID_VENV/bin/activate"

rm -rf "$WORK_DEMO"
mkdir -p "$(dirname "$WORK_DEMO")"
cp -R "$SRC_DEMO" "$WORK_DEMO"
mkdir -p "$TASK_DIR/logs/iter-1" "$TASK_DIR/logs/iter-2"

MAIN="$WORK_DEMO/app/src/main/java/ai/openclaw/automind/demo/MainActivity.java"

# Iteration 1: inject expected validation failure.
python3 - <<PY
from pathlib import Path
p = Path("$MAIN")
s = p.read_text()
s = s.replace('result.setText("Probe state: Completed");', 'result.setText("Probe state: Broken");')
p.write_text(s)
PY

"$WORK_DEMO/build_apk.sh" >/tmp/automind-android-self-repair-smoke-build1.txt
set +e
python3 "$ROOT/scripts/android_app_harness_probe.py" \
  --apk "$APK_PATH" \
  --package "$PKG" \
  --activity "$ACTIVITY" \
  --out "$TASK_DIR/logs/iter-1" \
  --initial-text 'CodeAutonomy Android Harness Demo' \
  --initial-text 'Probe state: Idle' \
  --tap-desc probe_button \
  --tap-text 'Run Probe' \
  --expected-text 'Probe state: Completed'
ITER1_CODE=$?
set -e

if [[ "$ITER1_CODE" -eq 0 ]]; then
  echo "[CodeAutonomy] Expected iter-1 to fail, but it passed" >&2
  exit 1
fi

# Iteration 2: repair.
python3 - <<PY
from pathlib import Path
p = Path("$MAIN")
s = p.read_text()
s = s.replace('result.setText("Probe state: Broken");', 'result.setText("Probe state: Completed");')
p.write_text(s)
PY

"$WORK_DEMO/build_apk.sh" >/tmp/automind-android-self-repair-smoke-build2.txt
python3 "$ROOT/scripts/android_app_harness_probe.py" \
  --apk "$APK_PATH" \
  --package "$PKG" \
  --activity "$ACTIVITY" \
  --out "$TASK_DIR/logs/iter-2" \
  --initial-text 'CodeAutonomy Android Harness Demo' \
  --initial-text 'Probe state: Idle' \
  --tap-desc probe_button \
  --tap-text 'Run Probe' \
  --expected-text 'Probe state: Completed'

python3 - <<PY
import json
from pathlib import Path
root = Path("$TASK_DIR")
iter1 = json.loads((root / "logs/iter-1/android-app-harness-summary.json").read_text())
iter2 = json.loads((root / "logs/iter-2/android-app-harness-summary.json").read_text())
summary = {
    "result": "pass" if iter1.get("result") == "fail" and iter2.get("result") == "pass" else "fail",
    "iteration1": iter1.get("result"),
    "iteration2": iter2.get("result"),
    "expected": "iter-1 fail, iter-2 pass",
    "artifacts": [
        str(root / "logs/iter-1/android-app-harness-summary.json"),
        str(root / "logs/iter-2/android-app-harness-summary.json"),
    ],
}
(root / "self-repair-smoke-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False, indent=2))
raise SystemExit(0 if summary["result"] == "pass" else 1)
PY
