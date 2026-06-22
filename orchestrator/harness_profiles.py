"""Task harness profile selection helpers."""
from __future__ import annotations


def get_harness_profile(task_type: str) -> dict:
    """Return recommended harness tools and hints for each task type."""
    if task_type == "android":
        return {
            "name": "android-v1",
            "primaryTools": ["adbutils", "uiautomator2"],
            "fallbackTools": ["adb"],
            "preflight": [
                "Android real device is connected and `adb devices -l` shows `device`",
                "Device is unlocked and screen stays on",
                "USB debugging is enabled and authorized",
                "Device is not on lock screen, notification shade, permission dialog, or SystemUI overlay",
            ],
            "recommendedActions": [
                "Prefer generating `probe-flow.android.json` and running `scripts/android_probe_flow_runner.py` for dynamic validation flow",
                "Use `scripts/android_app_harness_probe.py` only as fixed smoke helper / fallback, not as the main dynamic validation path",
                "Prefer content-desc/resource-id selectors; use text only as fallback",
                "Classify SystemUI/device-state issues as `mobile_device_unavailable` or `permission_blocked`",
            ],
        }
    if task_type == "ios":
        return {
            "name": "ios-v1",
            "primaryTools": ["XcodeBuildMCP"],
            "fallbackTools": ["xcodebuild", "xcrun", "Xcode IDE"],
            "preflight": [
                "iOS real device should preferably be iOS 16+",
                "Device is unlocked and screen stays on",
                "Mac is trusted and Developer Mode is enabled",
                "Xcode / xcodebuildmcp can discover the device",
            ],
            "recommendedActions": [
                "Use XcodeBuildMCP device build-and-run as the main real-device path",
                "Treat old-device or devicectl issues as device-stack blockers, not product-code failures",
            ],
        }
    return {"name": task_type, "primaryTools": [], "fallbackTools": [], "preflight": [], "recommendedActions": []}
