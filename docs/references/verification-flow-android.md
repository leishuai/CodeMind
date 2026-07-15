# Android Verification Flow Reference

Android-specific verification commands and flows for CodeMind. This is loaded on
demand for Android tasks; the cross-platform skeleton (generic script validation,
visual, external-sink, verification-unblock policy, probe-flow, evidence,
`Validation.md`/`evaluation.json`, retry semantics) lives in
[`verification-flow.md`](verification-flow.md). The iOS equivalent is
[`verification-flow-ios.md`](verification-flow-ios.md).

CodeMind should classify environment/device/tool blockers separately from
product-code failures. Evidence beats guesses.

## Operational priority quick reference

Use this order when an Android task needs real app/device verification:

1. **Use a real device by default when one is connected and authorized.** If
   `adb state=device` and the screen is usable, do not switch to Emulator unless
   the user/TestCase explicitly allowed or selected Emulator coverage.
2. **Prepare adb/device facts first.** Resolve the adb binary, confirm
   `adb state=device`, unlock/authorize the device, and capture foreground
   package/SystemUI state before choosing an action path.
3. **Use `android-preflight` before deeper runtime verification.** It records adb
   path, helper Python, device metadata, and evidence paths.
4. **Operate the app through `android-probe-flow` when UI behavior matters.**
   Generated actions should launch/install, handle safe overlays, tap/input/swipe,
   assert postconditions, and collect screenshot/hierarchy/logcat evidence.
5. **Do not ask the user to perform ordinary UI actions** while adb/uiautomator
   can execute them on an authorized connected device.
6. **Use direct Activity/route/deep-link launch only after normal UI automation is
   exhausted.** It is lower fidelity and cannot prove an end-to-end navigation
   testcase.
7. **Use Emulator only when explicitly allowed.** Emulator validation is not the
   default Android fallback when a real device is available; it is a selected or
   approved modality, and can be higher-cost than real-device adb/uiautomator.
8. **Use human-assisted evidence capture only after automated fallbacks** or for
   sensitive/system actions automation must not perform. Manual action alone is
   not proof.
9. **Ask the user only for genuine human/system gates** such as no device,
   unauthorized/offline state, lock screen, USB debugging/trust, or missing
   signing/device policy. A blocker never passes a required `TC-*`.

---

## Android real-device preparation

Real-device validation depends on both tools and the current device state. Ask
the human to confirm:

- device is unlocked and screen stays on;
- Developer Options are enabled;
- USB debugging is enabled;
- the computer has been authorized for USB debugging;
- USB mode is not charge-only when file-transfer/MTP is required;
- the device is not on lock screen, notification shade, permission dialog, system settings overlay, or other SystemUI screen;
- vendor-specific debug permissions are enabled when required.

Automatic checks:

```bash
adb devices -l
adb shell dumpsys window | head -80
```

If hierarchy/screenshot mainly comes from `com.android.systemui`, treat it as device-state blocked, not product-code failure.

---

## Android prerequisites

### Tool checks

```bash
command -v adb
adb version
adb devices -l
```

If `adb` is not in `PATH`, common macOS location:

```text
$HOME/Library/Android/sdk/platform-tools/adb
```

Recommended project-local Python toolchain:

```bash
automind setup-automation-tools android
```

Preflight/evaluator may run this automatically when Android probe-flow requires the helper and it is missing; users can also run it up front. It creates `.venv-android-tools` in the target workspace and installs packages from the CodeMind runtime `requirements/android-tools.txt`; it does not install Android Studio, Android SDK/platform-tools, `adb`, or change device settings. CodeMind should prefer a project-local `.venv-android-tools/bin/python` only when it imports the required helper modules; if it exists but is broken and the CodeMind runtime `.venv-android-tools` is ready, reuse the runtime helper venv and record the actual interpreter in `logs/iter-N/env.json`.

### Android real-device preflight

```bash
./automind.sh android-preflight <task-code> [iteration]
```

If no device is in `adb state=device`, classify as `mobile_device_unavailable`, not product-code failure.

---

## Android real-device flow

Install APK:

```bash
# Use a bounded wrapper/timeout; do not leave raw adb install running indefinitely.
adb install -r path/to/app.apk
```

Launch app:

```bash
adb shell monkey -p <package> -c android.intent.category.LAUNCHER 1
```

Stop app:

```bash
adb shell am force-stop <package>
```

Current foreground app/activity:

```bash
adb shell dumpsys activity activities | grep -E 'ResumedActivity|topResumedActivity' | tail -5
```

Screenshot:

```bash
adb exec-out screencap -p > logs/iter-N/screenshot.png
```

Logcat:

```bash
adb logcat -d > logs/iter-N/logcat.txt
```

UI hierarchy:

```bash
adb shell uiautomator dump /sdcard/window.xml
adb pull /sdcard/window.xml logs/iter-N/window.xml
```

For richer actions and assertions, prefer the Android probe-flow runner with
`adbutils` / `uiautomator2`. Android probe-flow can install/launch, close
optional blockers with `tap_if_present`, tap/click, input text, swipe, send
keyevents, assert text/selector/app hierarchy, and capture screenshot/UI
hierarchy/logcat evidence. Actions must be generated from TestCases/Require and
include selector intent plus post-action assertions; do not random-click.

For generic popups that hide the target screen, generate top-level `uiUnblock`
when safe overlay handling is needed. Android `android-probe-flow` now scans UI
hierarchy after launch and before configured actions, taps only safe dismiss
controls, and records `probe-flow/overlay-unblock.json` plus before/after
screenshot/hierarchy evidence and an `overlay_unblock` trace row. It must not
auto-click login, payment, delete/reset,
upload, signing/device trust, or ambiguous consent.

When the device is connected and authorized (`adb state=device`), driving these
in-app actions — play, skip/next, trigger an error, interrupt/pause, navigate —
and then capturing logcat is CodeMind's own verification work. Encode the steps
as probe-flow actions and run them; do not stop and ask the user "I cannot
operate your physical device, please confirm how to verify". That phrasing is a
non-whitelisted soft pause. Only escalate to `ask_user(real_device_or_signing)`
for a genuine human/system gate: no device in `state=device`, an unresolved
unlock / Developer-Mode / USB-debugging / trust prompt, or missing signing
material — and then say exactly what CodeMind detected and the single physical
action needed.

Tier-1 hard evidence here is logcat with matched keywords
(`log_keyword_matched`), test/instrumentation exit codes, and post-action
assertions; screenshots and UI-hierarchy dumps are Tier-2 supporting evidence —
strongly recommended when capturable and surfaced in `report.html`, but they do
not replace Tier-1. See the evidence-tier ordering in
[`verification-flow.md`](verification-flow.md#evidence-tiers-which-evidence-to-prefer).

If UI automation cannot reach the target page through the normal flow even after
exhausting the probe-flow runner, the cross-platform low-fidelity last resort is
to temporarily launch the target Activity/route directly (e.g.
`adb shell am start -n <pkg>/<activity>` or a debug deep-link) — see
[Direct-route page load](verification-flow.md#direct-route-page-load-low-fidelity-last-resort)
in the main flow. It is weaker evidence (non-standard entry) and cannot satisfy a
required end-to-end navigation testcase.

---

## Android adb environment and daemon diagnostics

Android real-device preflight must distinguish adb path/device problems instead
of collapsing every `adb devices` failure into "no device". Use these categories:

1. `adb_binary_missing` — no adb executable found in PATH, Android SDK env vars,
   common SDK locations, or the running adb server executable.
2. `adb_daemon_unavailable` — adb executable exists, but the server cannot start
   or cannot be reached, for example `ADB server didn't ACK`, `could not install
   *smartsocket* listener`, or `cannot connect to daemon`.
3. `mobile_device_unavailable` — adb server is reachable, but no devices are
   listed.
4. `mobile_device_unauthorized_or_offline` — device is listed but state is not
   `device` (`unauthorized`, `offline`, etc.).
5. `android_install_or_launch_failed` — device is `state=device`, but APK
   install/start/probe-flow fails.

For macOS developer machines, CodeMind should prefer explicit SDK adb paths when
agent shells have incomplete PATHs, especially:

```text
$ANDROID_HOME/platform-tools/adb
$ANDROID_SDK_ROOT/platform-tools/adb
$HOME/Library/Android/sdk/platform-tools/adb
```

If a normal host shell can see a device with the same adb binary, but the coding
agent / CodeMind preflight cannot, classify it as an adb daemon or process
environment issue, not as a physical device absence. Record the adb path,
`ANDROID_HOME`, `ANDROID_SDK_ROOT`, `PATH`, exit code, raw `adb devices -l`
output, and adb startup log path before asking the user to reconnect the device.

Repeated occurrences of the same adb daemon failure should move toward
self-repair or replan; do not repeatedly ask the same `No Android device`
question when the previous answer was retry and the failure category has not
changed.

---

## Android Gradle / Kotlin build-failure triage

Android build failures must be triaged before any source edit. Two patterns are
mistakes the loop keeps making; classify them first.

### Build-command error vs source-compile error

`Cannot locate tasks that match ':app:assembleLiteDebug'` (or any
`task '...' not found`) is **not** a code problem — the requested task/flavor
does not exist. Treat it as a verification-command / flavor configuration error
(`verifier_command_misconfigured`, `sameProblemKey=android.build.gradle_task_not_found`):
fix the `verifyCommand` task path or flavor, do not replan or send Generator to
edit product code.

### Large unrelated unresolved references → suspect stale cache, not source

When many `unresolved reference` errors appear at once (e.g. `compileKotlin*`
failing on `BEHAVIOR_DOWNLOAD`, `BEHAVIOR_COLLECT`, `applyButtonCornerStyleForLite`,
`takeAsArgs`, `MUSIC_VM_PRELOAD_STRATEGY_NONE`), and they are spread across many
stable old files all referencing the same upstream module, **do not** start
patching imports / copying constants / moving functions. When a large amount of
historically stable code suddenly cannot see the same upstream module's
top-level symbols, suspect a stale classpath / build output / Galaxy incremental
cache first. The classifier routes this to
`sameProblemKey=android.build.kotlin_unresolved_stale_cache`.

### Standard triage ladder

1. `git status` — converge the working tree first (see below).
2. Confirm the current tracked diff actually touches the failing files.
3. `grep` that the unresolved symbols' definitions still exist in source.
4. Compare the upstream module against `HEAD` (file list / diff aligned?).
5. Build the upstream module on its own.
6. Build the downstream module on its own.
7. Clean the affected module build outputs + `--rerun-tasks`.
8. Only then run the app-level assemble.

A worked example that pinned the root cause to a stale Kotlin/Galaxy classpath
snapshot: `music_api` source existed and was aligned with `HEAD`; `music_api`
compiled alone; `music_impl` still could not see `music_api` symbols; cleaning
`music_api/build` + `music_impl/build` fixed it; `app:assembleDebug` then passed.

### Small-knife cache reset (preferred over a full clean)

Clean only the affected upstream/downstream module `build/` directories and
rerun the downstream compile with `--rerun-tasks`. Do not start by deleting the
whole `.gradle` or running a full-project `clean`; widen scope only if the
narrow reset still fails.

```bash
rm -rf business/music/music_api/build
rm -rf business/music/music_impl/build
./gradlew :business:music:music_impl:compileFmDebugKotlinAndroid --offline --rerun-tasks
./gradlew :app:assembleDebug --offline --quiet
```

This is faster and introduces fewer extra variables than a global clean.

### Converge the working tree before triage

Before triaging a compile failure, converge the working tree so unrelated diff
is not mistaken for the cause: separate task changes, historical noise,
generated build outputs, and untracked directories. In particular, move aside
unrelated `.automind/tasks/*` noise so the tracked diff shows only the current
task's files.

```bash
git status --porcelain
git diff --name-only
```

Keep the current task files, back up a patch if needed, and isolate unrelated
`.automind` task noise. A clean working tree makes it obvious whether the build
failure is caused by the current task's changes or by stale outputs.
