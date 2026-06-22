---
name: ios-screenshot
description: "iOS physical-device screenshot playbook for using pymobiledevice3/RSD/tunneld developer services as evidence while respecting tunneld/sudo boundaries."
use_when:
  - "collecting screenshot evidence from a physical iOS device"
  - "legacy screenshotr/idevicescreenshot fails"
  - "deciding whether screenshot is optional or requires user action"
solves:
  - "documents the current preferred screenshot backend"
  - "records tunneld as an explicit precondition"
  - "avoids silently starting privileged services"
---
# iOS Physical Screenshot Summary

Current recommended backend for iOS 17+/18 physical screenshots:

```text
sudo pymobiledevice3 remote tunneld + pymobiledevice3 developer dvt screenshot
```

Treat legacy `idevicescreenshot` / screenshotr as deprecated for iOS 18+ real-device evidence on the current Xcode 26/CoreDevice stack; it previously failed with:

```text
Could not start screenshotr service!
```

## Precondition

`tunneld` must already be running. Starting it may require sudo/root, so AutoMind must not start it silently.

Known working command used by the human:

```bash
sudo .venv-ios-tools/bin/python -m pymobiledevice3 remote tunneld \
  --host 127.0.0.1 --port 49151 --protocol tcp --no-wifi
```

Then AutoMind can run:

```bash
./automind.sh ios-screenshot <task-code> [iteration] \
  --device-id <traditional-udid> \
  --output <output.png>
```

## Verified evidence

- Previous demo: `ios_screenshot_runner_demo_05061916`, PNG 828 x 1792.

## Policy

Screenshot is useful evidence but should not block P0 install/launch/alive validation. If tunneld is not running, classify as `permission_blocked` with an ask-user question, or skip screenshot and continue with launch/log/process evidence. Do not silently start `tunneld`, because it may require sudo/root.

Last updated: 2026-06-10
