#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
BUILD_TOOLS="${BUILD_TOOLS_VERSION:-35.0.0}"
PLATFORM="${ANDROID_PLATFORM:-android-29}"
BT="$SDK/build-tools/$BUILD_TOOLS"
ANDROID_JAR="$SDK/platforms/$PLATFORM/android.jar"
OUT="$ROOT/build"
SRC="$ROOT/app/src/main"
PKG="ai.openclaw.automind.demo"

rm -rf "$OUT"
mkdir -p "$OUT/compiled" "$OUT/classes" "$OUT/dex" "$OUT/apk"

"$BT/aapt2" compile --dir "$SRC/res" -o "$OUT/compiled/resources.zip"
"$BT/aapt2" link \
  -o "$OUT/apk/unsigned.apk" \
  -I "$ANDROID_JAR" \
  --manifest "$SRC/AndroidManifest.xml" \
  --min-sdk-version 23 \
  --target-sdk-version 29 \
  "$OUT/compiled/resources.zip" \
  --java "$OUT/generated"

find "$SRC/java" "$OUT/generated" -name '*.java' > "$OUT/sources.list"
javac -source 1.8 -target 1.8 \
  -classpath "$ANDROID_JAR" \
  -d "$OUT/classes" \
  @"$OUT/sources.list"

"$BT/d8" \
  --lib "$ANDROID_JAR" \
  --output "$OUT/dex" \
  $(find "$OUT/classes" -name '*.class')

cp "$OUT/apk/unsigned.apk" "$OUT/apk/with-dex.apk"
(cd "$OUT/dex" && zip -q "$OUT/apk/with-dex.apk" classes.dex)

KEYSTORE="$OUT/debug.keystore"
keytool -genkeypair \
  -keystore "$KEYSTORE" \
  -storepass android \
  -keypass android \
  -alias androiddebugkey \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000 \
  -dname "CN=Android Debug,O=Android,C=US" >/dev/null 2>&1

"$BT/zipalign" -f 4 "$OUT/apk/with-dex.apk" "$OUT/apk/aligned.apk"
"$BT/apksigner" sign \
  --ks "$KEYSTORE" \
  --ks-pass pass:android \
  --key-pass pass:android \
  --out "$OUT/AutoMindAndroidDemo-debug.apk" \
  "$OUT/apk/aligned.apk"

"$BT/apksigner" verify "$OUT/AutoMindAndroidDemo-debug.apk"
echo "$OUT/AutoMindAndroidDemo-debug.apk"
