#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO_DIR="$ROOT/demos/ios-simulator-demo"
SCHEME="AutoMindIOSDemo"
PROJECT="$DEMO_DIR/AutoMindIOSDemo.xcodeproj"
DESTINATION="platform=iOS Simulator,OS=18.0,name=iPhone 16 Pro"
BUNDLE_ID="ai.openclaw.automind.demo"
ARTIFACT_DIR="$DEMO_DIR/artifacts"
SCREENSHOT="$ARTIFACT_DIR/verification.png"

mkdir -p "$ARTIFACT_DIR"

xcodebuild -project "$PROJECT" -scheme "$SCHEME" -destination "$DESTINATION" build >/tmp/automind_ios_demo_build.log
APP_PATH=$(find ~/Library/Developer/Xcode/DerivedData/AutoMindIOSDemo-*/Build/Products/Debug-iphonesimulator -maxdepth 1 -name 'AutoMindIOSDemo.app' | head -1)

if [[ -z "$APP_PATH" ]]; then
  echo "VERIFY_RESULT=FAIL"
  echo "REASON=app_not_found"
  exit 1
fi

xcrun simctl boot "iPhone 16 Pro" >/dev/null 2>&1 || true
xcrun simctl install booted "$APP_PATH" >/tmp/automind_ios_demo_install.log
xcrun simctl launch booted "$BUNDLE_ID" >/tmp/automind_ios_demo_launch.log
xcrun simctl io booted screenshot "$SCREENSHOT" >/dev/null

echo "VERIFY_RESULT=PASS"
echo "APP_PATH=$APP_PATH"
echo "SCREENSHOT=$SCREENSHOT"
echo "BUILD_LOG=/tmp/automind_ios_demo_build.log"
echo "INSTALL_LOG=/tmp/automind_ios_demo_install.log"
echo "LAUNCH_LOG=/tmp/automind_ios_demo_launch.log"
