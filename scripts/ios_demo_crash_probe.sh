#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO_DIR="$ROOT/demos/ios-simulator-demo"
SCHEME="AutoMindIOSDemo"
PROJECT="$DEMO_DIR/AutoMindIOSDemo.xcodeproj"
DESTINATION="platform=iOS Simulator,OS=18.0,name=iPhone 16 Pro"
BUNDLE_ID="ai.openclaw.automind.demo"
ARTIFACT_DIR="$DEMO_DIR/artifacts"
LOG_PATH="$ARTIFACT_DIR/crash_probe.log"

mkdir -p "$ARTIFACT_DIR"
: > "$LOG_PATH"

xcodebuild -project "$PROJECT" -scheme "$SCHEME" -destination "$DESTINATION" build >/tmp/automind_ios_demo_build.log
APP_PATH=$(find ~/Library/Developer/Xcode/DerivedData/AutoMindIOSDemo-*/Build/Products/Debug-iphonesimulator -maxdepth 1 -name 'AutoMindIOSDemo.app' | head -1)
if [[ -z "$APP_PATH" ]]; then
  echo "CRASH_PROBE=FAIL"
  echo "REASON=app_not_found"
  exit 1
fi

xcrun simctl boot "iPhone 16 Pro" >/dev/null 2>&1 || true
xcrun simctl install booted "$APP_PATH" >/dev/null
xcrun simctl spawn booted log stream --style compact --level debug --predicate 'process == "AutoMindIOSDemo"' > "$LOG_PATH" 2>&1 &
LOG_PID=$!
trap 'kill $LOG_PID >/dev/null 2>&1 || true' EXIT

set +e
xcrun simctl launch booted "$BUNDLE_ID" --args --crash-on-launch >/tmp/automind_ios_demo_crash_launch.log 2>&1
LAUNCH_CODE=$?
set -e
sleep 3
kill $LOG_PID >/dev/null 2>&1 || true
wait $LOG_PID 2>/dev/null || true

if grep -q 'AutoMindIOSDemo crash probe' "$LOG_PATH" || grep -q 'AutoMindIOSDemo crash probe' /tmp/automind_ios_demo_crash_launch.log; then
  echo "CRASH_PROBE=PASS"
  echo "CRASH_LOG=$LOG_PATH"
  echo "LAUNCH_CODE=$LAUNCH_CODE"
else
  echo "CRASH_PROBE=UNKNOWN"
  echo "CRASH_LOG=$LOG_PATH"
  echo "LAUNCH_CODE=$LAUNCH_CODE"
  exit 1
fi
