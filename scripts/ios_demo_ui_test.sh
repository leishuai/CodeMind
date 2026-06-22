#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO_DIR="$ROOT/demos/ios-simulator-demo"
SCHEME="AutoMindIOSDemo"
PROJECT="$DEMO_DIR/AutoMindIOSDemo.xcodeproj"
DESTINATION="platform=iOS Simulator,OS=18.0,name=iPhone 16 Pro"
LOG_PATH="${1:-$DEMO_DIR/artifacts/ui_test.log}"
mkdir -p "$(dirname "$LOG_PATH")"

xcodebuild \
  -project "$PROJECT" \
  -scheme "$SCHEME" \
  -destination "$DESTINATION" \
  test > "$LOG_PATH" 2>&1

echo "UI_TEST_RESULT=PASS"
echo "UI_TEST_LOG=$LOG_PATH"
LAST_XCRESULT=$(find ~/Library/Developer/Xcode/DerivedData/AutoMindIOSDemo-*/Logs/Test -name 'Test-AutoMindIOSDemo-*.xcresult' | sort | tail -1)
if [[ -n "${LAST_XCRESULT:-}" ]]; then
  echo "UI_TEST_XCRESULT=$LAST_XCRESULT"
fi
