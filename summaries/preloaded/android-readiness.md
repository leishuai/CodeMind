---
name: android-readiness
description: "Android real-device readiness and helper-venv reuse playbook for adb, adbutils/uiautomator2, and preflight evidence."
use_when:
  - "Android real-device or probe-flow verification is required"
  - "android-preflight reports adb/device/helper package blockers"
  - "project-local .venv-android-tools setup fails because of Python/pip/network"
solves:
  - "prevents bad project-local helper venvs from hiding a working runtime venv"
  - "separates adb/device blockers from Python helper installation blockers"
  - "records reusable Android preflight evidence before runtime probe-flow"
---
# Android Readiness / Helper Venv Playbook

Use this pack when an Android task needs real-device verification, `android-preflight`, or `android-probe-flow`.

## Operation rules (read first)

- Resolve adb/device readiness before blaming product code. ADB startup failure,
  no device, unauthorized/offline device, and SystemUI/lock-screen blockers are
  verifier/device states, not source defects.
- Android default is real device when available: if `adb state=device` and the
  device is unlocked/authorized, use the real-device `android-preflight` /
  `android-probe-flow` path. Do not switch to Emulator just because it appears
  more isolated or standard.
- When a device is `state=device`, do not ask the user to perform ordinary UI
  actions. Use `android-probe-flow` for launch, safe overlay unblock, tap/input,
  swipe, assertions, screenshot, hierarchy, and logcat evidence.
- Use the resolved adb path and one bounded safe retry for adb daemon startup
  failures before escalating. Only ask the user when adb is healthy but the
  device is genuinely absent, unauthorized/offline, locked, or requires a
  physical/system action.
- Direct Activity/deep-link launch is a lower-fidelity fallback after normal UI
  automation is exhausted; it cannot prove an end-to-end navigation testcase.
- Emulator validation is a selected/approved modality, not the default Android
  fallback when real hardware is available. Use it only when the testcase allows
  it, the user selected it in one-shot planning, or a runtime downgrade is
  explicitly approved.
- Manual action alone is not proof. If human-assisted evidence capture is used,
  CodeAutonomy must still collect machine-checkable postcondition evidence.

## Recommended path

1. Run Android readiness before deeper probe-flow:

   ```bash
   <AUTOMIND_CLI> android-preflight <task-code> <iteration>
   ```

2. Treat a passing preflight as reusable evidence. Record:
   - exact adb path;
   - Android tools Python path;
   - `adbutils` / `uiautomator2` availability;
   - device serial/model/sdk/release when safe for local task artifacts;
   - evidence paths under `logs/iter-N/`.

3. Prefer the first ready helper Python in this order:
   - project-local `.venv-android-tools` when it exists **and** imports `adbutils` + `uiautomator2`;
   - CodeAutonomy runtime `.venv-android-tools` when it is already ready;
   - otherwise project-local setup via `setup-automation-tools android`.

A project-local venv that exists but cannot import required modules is not ready. Do not let it hide a known-good runtime helper venv. Do not copy a developer-machine absolute runtime helper path such as `/Users/.../projects/automind/.venv-android-tools/bin/python` from old logs. Resolve the helper from the current installation/runtime root or from `logs/iter-N/env.json` (`androidToolsPython`) for the current task.

## Common blockers and classification

### ADB / device blocker

Symptoms:

- `adb devices` exits non-zero;
- no row with state `device`;
- device is locked, unauthorized, offline, or under SystemUI/notification overlay;
- macOS ADB daemon errors such as smartsocket/listener/USB interface failures.

Classify as `mobile_device_unavailable`, `permission_blocked`, or `tool_missing` depending on the evidence. Do not mark product code failed.

Separate adb startup failure from real device absence:

- If output contains `daemon not running; starting now`, `ADB server didn't ACK`, `could not install *smartsocket* listener`, `Operation not permitted`, `cannot connect to daemon`, or repeated `Unable to create an interface plug-in`, treat the first failure as an adb server startup/USB interface problem, not as proof that no device exists.
- Use the resolved adb path from env evidence and do one bounded safe retry when current retry policy permits: start/reuse the adb server, then run `adb devices -l` again. If the second attempt sees a `device` row, continue verification and record both attempts.
- If retry still fails, classify as `permission_blocked` / adb server startup failure with evidence. Do not collapse it into plain `No Android device` unless adb server is healthy and the device list is genuinely empty/unauthorized/offline.
- Do not run global disruptive recovery such as `adb kill-server` unless the current task/user policy allows affecting other adb clients.

Safe next steps:

- use the project/resolved adb path recorded in env evidence;
- run one bounded safe retry for adb daemon startup/link issues when policy
  permits;
- only ask the user to connect/unlock/authorize the phone after adb is healthy
  and evidence still shows absent/unauthorized/offline/locked device state;
- rerun `android-preflight` after the device or adb server state changes.

### Python helper setup blocker

Symptoms:

- `adbutils` / `uiautomator2` missing;
- `pip install -r requirements/android-tools.txt` fails;
- DNS/proxy/private network failure, e.g. cannot resolve `pypi.org`.

Classify as `tool_missing` / external environment. Do not keep reinstalling blindly.

Safe next steps:

- reuse a ready CodeAutonomy runtime `.venv-android-tools` if available;
- otherwise ask the user to fix Python/pip/network/proxy or provide an approved package mirror/wheelhouse;
- if verification can proceed with lower capability, ask/record an adb-only fallback decision.

## Avoid paths

- Do not install Android Studio, Android SDK/platform-tools, adb, USB drivers, OS packages, or change device trust settings silently.
- Do not delete or recreate project-local venvs unless explicitly chosen; first prove whether they are missing vs. broken.
- Do not treat PyPI DNS/proxy failure as product/runtime failure.
- Do not replace a working runtime helper venv with a newly guessed Python interpreter just because a project-local venv exists.

## Evidence expectations

A useful Android readiness record includes:

- `logs/iter-N/adb-devices.log`
- `logs/iter-N/python-packages.log`
- `logs/iter-N/android-device-info.json`
- `logs/iter-N/env.json`
- `logs/iter-N/commands.md`

For probe-flow tasks, follow preflight with `android-probe-flow` and collect screenshot/UI hierarchy/logcat/probe-flow summary when applicable.

Last updated: 2026-06-13
