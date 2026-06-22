---
name: ios-app-smoke
description: "iOS real-device app-alive smoke playbook for verifying an already-installed app can launch and remain observable as a running process."
use_when:
  - "checking whether a physical-device iOS app can launch"
  - "separating install/launch/process-alive evidence from task-specific UI validation"
  - "collecting minimal app-alive evidence before deeper XCUITest/probe-flow"
solves:
  - "proves installed app lookup and process launch"
  - "records process/display/screenshot/crash-hint evidence"
  - "avoids overstating app-alive as full UI validation"
---
# iOS App Smoke Summary

Minimal iOS real-device app smoke verifies an already-installed app can launch and remain observable as a process.

## Read-only physical-device discovery

Before app-smoke or launch verification on a physical iPhone, first confirm the
device is visible through both the traditional libimobiledevice path and the
Xcode/CoreDevice path. This prevents false "device not found" conclusions
caused by mixing CoreDevice IDs and traditional UDIDs.

```bash
idevice_id -l
ideviceinfo -u <traditional-udid>
xcrun devicectl list devices
xcrun xctrace list devices
```

Use the discovered IDs according to the tool:

- `idevice_id` / `ideviceinfo` use the traditional device UDID.
- `devicectl` often reports a CoreDevice identifier and also includes the
  traditional UDID in JSON/device details.
- Record both IDs in `logs/iter-N/env.json` and command logs so later phases can
  reuse them instead of asking the user again.

## Command

```bash
./automind.sh ios-app-smoke <task-code> \
  --bundle-id <bundle-id> \
  --device-id <core-device-id> \
  --executable <process-marker> \
  --process-hint <process-path-marker> \
  --extended                 # optional display/screenshot/crash-hint evidence
```

## What it proves

- The app is installed and visible in `devicectl device info apps`.
- `devicectl device process launch` reports launch success.
- After a short wait, `devicectl device info processes` contains an expected process marker.
- With `--extended`, it also records display state, optional screenshot evidence, and local crash hints.

## What it does not prove

- It does not prove any target screen is usable.
- It does not handle privacy/login/permission popups.
- It does not assert task-specific UI state.
- It does not replace XCUITest/probe-flow.

Use it as a P0 app-alive check after install/launch, then layer explicit probe-flow/XCUITest actions on top.

## Evidence

The runner writes:

```text
logs/iter-N/app-lookup.txt
logs/iter-N/launch.txt
logs/iter-N/processes.txt
logs/iter-N/display.txt
logs/iter-N/crash-hints.json
logs/iter-N/ios-app-smoke-screenshot.png   # optional
logs/iter-N/ios-app-smoke-summary.json
evaluation.json
Validation.md
```

Last updated: 2026-05-11
