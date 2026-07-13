#!/usr/bin/env python3
"""Shared failure classification helpers for CodeAutonomy.

This module centralizes common log-signature -> category decisions so Android,
iOS, script-command, and future adapters don't each invent their own wording.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class Classification:
    category: str
    reason: str
    nextAction: str
    sameProblemKey: str
    retryableBy: str = "agent"
    askUserQuestion: dict[str, Any] | None = None
    specificErrors: list[str] | None = None
    recoveryAction: str | None = None
    triageSource: str = "code_fast_path"  # "code_fast_path" (deterministic pattern matched) | "model_triage" | "unclassified"
    needsModelReview: bool = False  # derived: True when triageSource is not a confident code_fast_path match

    def __post_init__(self) -> None:
        # Keep needsModelReview consistent with triageSource: only a confident
        # deterministic fast-path match is trusted without model review.
        self.needsModelReview = self.triageSource != "code_fast_path"


def _ask(question: str, reason: str, options: list[dict[str, Any]], recommended: str = "A", category: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "question": question,
        "reason": reason,
        "options": options,
        "recommended": recommended,
        "defaultAction": "retry",
    }
    if category:
        payload["category"] = category
    return payload


def classify(platform: str, phase: str, log: str, exit_code: int | None = None, context: dict[str, Any] | None = None) -> Classification:
    p = (platform or "generic").lower()
    ph = (phase or "unknown").lower()
    text = (log or "").lower()
    context = context or {}

    # iOS permission / readiness
    if "developer mode" in text and any(x in text for x in ["disabled", "not enabled", "enable developer mode"]):
        return Classification(
            "permission_blocked",
            "iOS Developer Mode is not enabled.",
            "ask_user",
            "ios.permission.developer_mode.disabled",
            "human",
            _ask(
                "iOS \u771f\u673a\u9a8c\u8bc1\u9700\u8981\u5f00\u542f Developer Mode，\u662f\u5426\u5df2\u5728 iPhone \u4e0a\u5f00\u542f\u5e76\u91cd\u542f\u786e\u8ba4？",
                "\u771f\u673a build/install/launch/test/UI automation \u9700\u8981 Developer Mode；Agent \u4e0d\u80fd\u4ee3\u66ff\u7528\u6237\u5f00\u542f。",
                [
                    {"id": "A", "label": "I will enable Developer Mode in iPhone Settings, then retry.", "impact": "Continue physical-device verification."},
                    {"id": "B", "label": "Switch to simulator / dry-run.", "impact": "Skip physical-device evidence."},
                    {"id": "C", "label": "Stop", "impact": "Keep the task blocked."},
                ],
            ),
        )

    if "timed out while enabling automation mode" in text or "failed to initialize for ui testing" in text:
        return Classification(
            "permission_blocked",
            "XCUITest timed out while enabling UI Automation mode.",
            "ask_user",
            "ios.permission.ui_automation.disabled_or_blocked",
            "human",
            _ask(
                "iOS \u771f\u673a XCUITest \u65e0\u6cd5\u542f\u7528 automation mode。\u662f\u5426\u5df2\u5f00\u542f iPhone \u8bbe\u7f6e -> \u5f00\u53d1\u8005 -> UI Automation？",
                "XCUITest runner \u5df2\u542f\u52a8，\u4f46\u8bbe\u5907\u4fa7 automation mode \u8d85\u65f6。",
                [
                    {"id": "A", "label": "I will enable UI Automation and keep the device unlocked/screen-on, then retry.", "impact": "Clear the UI automation permission blocker."},
                    {"id": "B", "label": "Switch to simulator XCUITest first.", "impact": "Skip physical-device UI automation."},
                    {"id": "C", "label": "Stop and manually inspect Xcode/device state", "impact": "Keep the task blocked."},
                ],
            ),
        )

    if "profile has not been explicitly trusted" in text or "invalid code signature" in text and "requestdenied" in text:
        return Classification(
            "permission_blocked",
            "Developer profile/code signature is not trusted on the iPhone.",
            "ask_user",
            "ios.permission.developer_profile.not_trusted",
            "human",
            _ask(
                "iPhone \u62d2\u7edd\u542f\u52a8\u5f00\u53d1\u8005\u7b7e\u540d App。\u662f\u5426\u5df2\u5728\u8bbe\u5907\u4e0a\u4fe1\u4efb\u5f00\u53d1\u8005 profile？",
                "Personal Team / Apple Development \u9996\u6b21\u5b89\u88c5\u540e\u9700\u8981 human \u5728 iPhone \u4e0a\u663e\u5f0f\u4fe1\u4efb。",
                [
                    {"id": "A", "label": "I will trust the developer profile on the iPhone, then retry.", "impact": "Continue physical-device launch/test."},
                    {"id": "B", "label": "Change signing team/profile.", "impact": "Requires re-signing."},
                    {"id": "C", "label": "Stop", "impact": "Keep the task blocked."},
                ],
            ),
        )

    if "requires a development team" in text or "no profiles for" in text or (
        "provisioning profile" in text and ("error" in text or "required" in text or "doesn't include" in text or "not found" in text or "code signing" in text)
    ) or "code signing is required" in text or "requires signing" in text:
        # First try to self-heal with signing material that already exists in the
        # project / on this machine (an existing DEVELOPMENT_TEAM, a codesigning
        # identity in the keychain, or a bundled `.mobileprovision`). Only escalate
        # to the user once that has been genuinely exhausted, which the caller
        # signals via context (signingRetryExhausted / no usable material found).
        exhausted = bool(
            context.get("signingRetryExhausted")
            or context.get("signingMaterialExhausted")
            or context.get("noUsableSigningMaterial")
        )
        if not exhausted:
            return Classification(
                "provisioning_profile_blocked",
                "iOS build failed on signing/provisioning. Before asking the user, reuse signing "
                "material that already exists in the project/machine: run "
                "`automind ios-signing-preflight <task> --discover --bundle-id <bundle> [--installed-team <team>] "
                "[--destination-type device|simulator]` and consume its `signingPlan` (the single source of "
                "truth). The plan ladder is: simulator -> CODE_SIGNING_ALLOWED=NO (no signing at all); "
                "manual_reuse -> a codesigning identity for the Team plus a local profile for that SAME Team "
                "(and bundle) exist, so sign offline with DEVELOPMENT_TEAM + CODE_SIGN_STYLE=Manual + "
                "PROVISIONING_PROFILE_SPECIFIER (no Apple ID login needed); automatic -> only when an Apple ID "
                "is signed in AND manages the build's Team (`signingPlan.targetTeamManagedByAppleId=true`), then "
                "rebuild with DEVELOPMENT_TEAM + CODE_SIGN_STYLE=Automatic + `-allowProvisioningUpdates` so Xcode "
                "generates the profile. Use `signingPlan.buildSettings`/`extraFlags`/`rebuildHint` as the command "
                "shape. Escalate to ask_user only when `signingPlan.strategy=blocked` (no identity for the Team, no "
                "usable profile incl. the <bundle>.xctrunner profile, not signed in, or signed in but not managing "
                "the Team).",
                "retry_generator",
                "ios.signing.team_or_profile.use_existing",
                "agent",
                recoveryAction="Reuse signing material in project/machine: run `automind ios-signing-preflight <task> --discover --bundle-id <bundle>` to obtain signingPlan, then rebuild.",
            )
        return Classification(
            "permission_blocked",
            "iOS signing/provisioning requires a development team/profile decision.",
            "ask_user",
            "ios.signing.team_or_profile.missing",
            "human",
            _ask(
                "\u8bf7\u9009\u62e9 iOS \u7b7e\u540d\u7b56\u7565。",
                "\u771f\u673a build/install \u9700\u8981\u6709\u6548 Development Team / provisioning profile\uff1b\u5de5\u7a0b\u5df2\u6709\u7684\u8bc1\u4e66/profile \u5df2\u5c1d\u8bd5\u4f46\u4ecd\u65e0\u6cd5\u89e3\u51b3。",
                [
                    {"id": "A", "label": "Use Personal Team + Automatic Signing.", "impact": "Suitable for demos with small impact.", "risk": "May access Apple Developer services to create/update profiles.", "requiresConfirmation": True},
                    {"id": "B", "label": "Use company Team + Automatic Signing.", "impact": "Closer to the real deployment environment.", "risk": "Requires corresponding permissions.", "requiresConfirmation": True},
                    {"id": "C", "label": "User manually configures in Xcode.", "impact": "Most reliable but requires manual work."},
                ],
                category="real_device_or_signing",
            ),
        )

    if "maximum number of installed apps using a free developer profile" in text:
        return Classification(
            "permission_blocked",
            "Personal Team free profile app limit reached.",
            "ask_user",
            "ios.signing.personal_team.app_limit",
            "human",
            _ask(
                "Personal Team \u514d\u8d39 profile \u5df2\u8fbe\u5230\u8bbe\u5907\u5b89\u88c5 App \u6570\u91cf\u9650\u5236，\u4e0b\u4e00\u6b65\u600e\u4e48\u5904\u7406？",
                "\u7ee7\u7eed\u5b89\u88c5\u65b0 bundle id \u4f1a\u5931\u8d25。",
                [
                    {"id": "A", "label": "Reuse an existing demo bundle id.", "impact": "Avoids adding a new app and minimizes quota usage."},
                    {"id": "B", "label": "Uninstall unneeded Personal Team demo apps.", "impact": "Frees quota.", "risk": "Deletes demo apps on the device.", "requiresConfirmation": True},
                    {"id": "C", "label": "Switch to company Team.", "impact": "Bypasses Personal Team limits.", "requiresConfirmation": True},
                ],
            ),
        )

    if "device was not, or could not be, unlocked" in text or "backlight is off" in text or "screen appears off" in text:
        return Classification(
            "mobile_device_unavailable",
            "Device is locked, not unlockable, or screen/backlight is off.",
            "ask_user",
            f"{p}.device.locked_or_screen_off",
            "human",
            _ask(
                "\u8bbe\u5907\u53ef\u80fd\u9501\u5c4f\u6216\u7184\u5c4f。\u662f\u5426\u5df2\u4fdd\u6301\u8bbe\u5907\u89e3\u9501\u4eae\u5c4f？",
                "UI/evidence \u64cd\u4f5c\u9700\u8981\u8bbe\u5907\u5904\u4e8e\u53ef\u4ea4\u4e92\u72b6\u6001。",
                [
                    {"id": "A", "label": "I will keep the device unlocked/screen-on, then retry.", "impact": "Continue real-device verification."},
                    {"id": "B", "label": "Switch to dry-run / non-UI verification.", "impact": "Skip device UI evidence."},
                ],
            ),
        )

    # iOS screenshot backend
    if "could not start screenshotr service" in text:
        return Classification(
            "tool_limitation",
            "Legacy idevicescreenshot/screenshotr backend is unavailable; use pymobiledevice3+tunneld for iOS 17+/18.",
            "replan",
            "ios.screenshot.legacy_screenshotr.unavailable",
            "tool",
        )

    if "unable to connect to tunneld" in text or "requires root privileges" in text and "tunneld" in text:
        return Classification(
            "permission_blocked",
            "pymobiledevice3 screenshot requires a running tunneld; starting it may require sudo/root.",
            "ask_user",
            "ios.screenshot.tunneld.not_running_or_needs_root",
            "human",
            _ask(
                "\u662f\u5426\u5141\u8bb8/\u662f\u5426\u5df2\u542f\u52a8 pymobiledevice3 tunneld \u6765\u7ee7\u7eed iOS \u771f\u673a\u622a\u56fe？",
                "iOS 17+/18 \u771f\u673a screenshot \u9700\u8981 RSD/tunneld。",
                [
                    {"id": "A", "label": "I have manually started tunneld; retry.", "impact": "Continue screenshot capture."},
                    {"id": "B", "label": "Allow temporarily starting tunneld with sudo.", "impact": "May enable automatic screenshots.", "risk": "Requires administrator privileges.", "requiresConfirmation": True},
                    {"id": "C", "label": "Skip screenshot.", "impact": "Main path uses XCUITest/log/display evidence."},
                ],
            ),
        )

    # Android readiness / install
    if "no android device" in text or "no device" in text and p == "android" or "adb state=device" in text:
        return Classification(
            "mobile_device_unavailable",
            "No Android device is available in adb state=device.",
            "ask_user",
            "android.device.no_adb_device",
            "human",
            _ask(
                "\u6ca1\u6709\u53d1\u73b0 adb state=device \u7684 Android \u8bbe\u5907。\u8bf7\u95ee\u4e0b\u4e00\u6b65\u600e\u4e48\u5904\u7406？",
                "Android probe-flow \u9700\u8981\u771f\u673a\u5904\u4e8e adb device \u72b6\u6001。",
                [
                    {"id": "A", "label": "I will connect an Android device, enable USB debugging, authorize it, then retry.", "impact": "Continue physical-device verification."},
                    {"id": "B", "label": "Run dry-run only to verify flow configuration.", "impact": "Does not produce real device evidence."},
                    {"id": "C", "label": "Stop", "impact": "Keep the task blocked."},
                ],
            ),
        )

    if "install_failed_update_incompatible" in text:
        return Classification(
            "permission_blocked",
            "Android install failed due to signature/package conflict with existing app.",
            "ask_user",
            "android.install.signature_conflict",
            "human",
            _ask(
                "Android \u8bbe\u5907\u5df2\u6709\u540c\u5305\u540d\u4f46\u7b7e\u540d\u4e0d\u4e00\u81f4\u7684 App，\u662f\u5426\u5141\u8bb8\u5378\u8f7d\u65e7\u5305\u540e\u7ee7\u7eed？",
                "\u5378\u8f7d\u65e7\u5305\u53ef\u80fd\u6e05\u9664\u8be5 App \u6570\u636e。",
                [
                    {"id": "A", "label": "Allow uninstalling the old package and continue.", "impact": "Resolves install conflict.", "risk": "Deletes this app's local data.", "requiresConfirmation": True},
                    {"id": "B", "label": "Use signing/install method consistent with the old package.", "impact": "Preserves data, but requires correct signing."},
                    {"id": "C", "label": "Stop; user handles it manually", "impact": "Keep the task blocked."},
                ],
            ),
        )

    if "systemui" in text or "appnodes=0" in text:
        return Classification(
            "validation_failure",
            "UI hierarchy does not expose target app nodes; may be SystemUI overlay or wait issue.",
            "retry_generator",
            f"{p}.ui_hierarchy.app_nodes_zero_or_systemui",
            "verifier",
        )

    # iOS XCUITest external-runner / root-install signatures (P0-2 / P0-7).
    # These are distinct from mobile_device_unavailable: the device is present
    # and reachable, but the XCUITest delivery mechanism is blocked.
    if "root install style" in text or "hasinternalosbuild" in text or (
        "root install" in text and "not supported" in text
    ):
        return Classification(
            "external_runner_root_install_unsupported",
            "xcodebuild test tried to install the XCUITest runner via Root install style, "
            "which retail (non-internal) physical devices do not support; this does "
            "not mean the external UI runner is invalid, because it supports running "
            "on iOS Simulator. For "
            "real-device runtime proof, switch to build-for-testing + devicectl install "
            "+ test-without-building, a project/native UI test target, or WDA/go-ios. "
            "Do not downgrade a real-device runtime-proof task to probe-flow dry-run; "
            "dry-run validates intent only.",
            "retry_generator",
            "ios.xcuitest.root_install_style.unsupported",
            "verifier",
        )

    if "cannot initiate shared session more than once" in text:
        return Classification(
            "external_runner_bootstrap_abort",
            "xcodebuild test-without-building driver aborted while bootstrapping the runner "
            "(shared session initiated more than once); regenerate the .xctestrun without a "
            "conflicting UITargetApp / host duplication before retrying.",
            "retry_generator",
            "ios.xcuitest.xctestrun.shared_session_double_init",
            "verifier",
        )

    if "exiting due to ide disconnection" in text or "channel canceled" in text or (
        "xctestmanager_ideinterface" in text and "refused" in text
    ):
        return Classification(
            "external_runner_capability_blocked",
            "The XCUITest runner started but no IDE-side helper implements "
            "XCTestManager_IDEInterface, so the runner disconnected. `pymobiledevice3 "
            "dvt xcuitest` cannot host an arbitrary IDE-dependent runner. Replan onto a "
            "runner whose commands do not need the Xcode IDE channel: a project UI test "
            "target via Xcode, or a WebDriverAgent-based runner (go-ios / pymobiledevice3 "
            "tunnel + WDA) whose taps ride WDA's own WebDriver server. Do not retry the "
            "same dvt-xcuitest-hosts-our-custom-runner path.",
            "replan",
            "ios.xcuitest.ide_interface.unimplemented",
            "tool",
        )

    # Once the XCUITest session has actually started ("Running tests..."), a
    # later DTX channel drop or exit code 74 is a test-runtime failure, not a
    # signing/provisioning problem. The runner was signed well enough to launch
    # and reach the test session, so do not misroute these into the signing
    # branch below (which keys only on log tokens). Treat them as a runtime
    # disconnect that the Generator should repair.
    tests_started = "running tests" in text or "test suite" in text and "started" in text
    dtx_or_code74 = (
        "dtxconnection" in text or "dtx" in text and "connect" in text
        or "lost connection to" in text or "the connection was lost" in text
        or (exit_code == 74)
    )
    if ph == "test" and tests_started and dtx_or_code74:
        return Classification(
            "test_failure",
            "The XCUITest session had already started (\"Running tests...\") and then the DTX "
            "connection dropped / the process exited with code 74. This is a test-runtime "
            "disconnect, not a signing/provisioning failure: the runner was signed and launched "
            "far enough to begin the session. Investigate the test process crash/hang, device "
            "stability, or test harness, and retry; do not re-sign.",
            "retry_generator",
            "ios.xcuitest.session.dtx_disconnect_or_code74",
            "agent",
        )

    # Device-side link instability *before* the test session started (no
    # "Running tests..." marker). These are transport/daemon drops between the
    # Mac and the iPhone, not code, signing, or device-absence problems: the
    # device is paired but the connection is flaky. Self-retry first (transient),
    # and only after retries are exhausted ask the user to physically recover the
    # link (restart the phone, restart Xcode/CoreDevice, or replug USB). This is
    # distinct from mobile_device_unavailable (no device at all) and from the
    # pymobiledevice3 IDE-interface capability dead end above.
    device_link_lost = (
        "lost connection to the device" in text
        or "connection to the device was lost" in text
        or "could not connect to the device" in text
        or "unable to connect to the device" in text
        or "the device was not connected" in text
        or "device is busy" in text
        or "device disconnected" in text
        or "could not connect to lockdown" in text
        or "failed to start the test runner" in text
    )
    if device_link_lost and not tests_started:
        link_exhausted = bool(
            context.get("deviceLinkRetryExhausted")
            or context.get("repeatedSameProblem")
        )
        if not link_exhausted:
            return Classification(
                "mobile_device_unavailable",
                "The connection between the Mac and the iPhone dropped before the test session "
                "started (device-side link instability, not signing/code). This is usually "
                "transient. Retry the same run first. If it keeps dropping, record "
                "deviceLinkRetryExhausted in the failure context so CodeAutonomy asks the user to "
                "physically recover the link.",
                "retry_generator",
                f"{p}.device.link_lost_before_session",
                "agent",
            )
        return Classification(
            "mobile_device_unavailable",
            "The Mac<->iPhone connection keeps dropping before the XCUITest session can start. "
            "This is a device/host link problem, not a code or signing failure. The user should "
            "physically recover the link before retrying.",
            "ask_user",
            f"{p}.device.link_lost_repeated",
            "human",
            _ask(
                "Mac \u4e0e iPhone \u7684\u8fde\u63a5\u53cd\u590d\u65ad\u5f00\uff0c\u6d4b\u8bd5\u4f1a\u8bdd\u65e0\u6cd5\u542f\u52a8\u3002\u8bf7\u5148\u6062\u590d\u8bbe\u5907\u8fde\u63a5\u540e\u91cd\u8bd5\uff1a",
                "\u591a\u6b21\u91cd\u8bd5\u540e\u8bbe\u5907\u4fa7\u8fde\u63a5\u4ecd\u4e0d\u7a33\u5b9a\uff08\u4e0d\u662f\u7b7e\u540d/\u4ee3\u7801\u95ee\u9898\uff09\u3002",
                [
                    {"id": "A", "label": "I unplugged and replugged the USB cable (try another port/cable), kept the iPhone unlocked, then retry.", "impact": "Re-establish a stable USB device link."},
                    {"id": "B", "label": "I restarted Xcode / CoreDevice (or ran `xcrun devicectl list devices` to re-pair), then retry.", "impact": "Reset the host-side device daemon."},
                    {"id": "C", "label": "I rebooted the iPhone, re-trusted this Mac, then retry.", "impact": "Clear device-side lockdown/connection state."},
                    {"id": "D", "label": "Switch to simulator for now.", "impact": "Skip physical-device link issues; real-device coverage stays unresolved."},
                ],
                category="system_or_external_dependency",
            ),
        )


    if ("code signing" in text or "code sign" in text or "signing" in text) and (
        "errsecinternalcomponent" in text or "no signing certificate" in text
        or "failed to code sign" in text or "no valid signing identities" in text
    ):
        signing_exhausted = bool(
            context.get("signingRetryExhausted")
            or context.get("signingMaterialExhausted")
            or context.get("noUsableSigningMaterial")
        )
        if not signing_exhausted:
            return Classification(
                "external_runner_signing_blocked",
                "XCUITest runner could not be code signed. Before asking the user, reuse signing "
                "material that already exists in the project/machine: run "
                "`automind ios-signing-preflight <task> --discover --bundle-id <bundle> [--installed-team <team>] "
                "[--destination-type device|simulator]` and consume its `signingPlan` (the single source of "
                "truth) for the runner too. The plan ladder is: simulator -> CODE_SIGNING_ALLOWED=NO (no "
                "signing); manual_reuse (preferred) -> a codesigning identity for the Team plus a local profile "
                "for that SAME Team (including the `<bundle>.xctrunner` profile for UI tests) exist, so re-sign "
                "offline with DEVELOPMENT_TEAM + CODE_SIGN_STYLE=Manual + PROVISIONING_PROFILE_SPECIFIER (no "
                "Apple ID login needed); automatic -> only when an Apple ID is signed in AND manages the build's "
                "Team (`signingPlan.targetTeamManagedByAppleId=true`), then rebuild with DEVELOPMENT_TEAM + "
                "CODE_SIGN_STYLE=Automatic + `-allowProvisioningUpdates`. You may also reuse a known-good "
                "already-signed runner bundle id. Escalate to ask_user only when `signingPlan.strategy=blocked`.",
                "retry_generator",
                "ios.xcuitest.runner.signing_use_existing",
                "agent",
            )
        return Classification(
            "external_runner_signing_blocked",
            "XCUITest runner could not be code signed (missing/invalid signing identity). "
            "Existing project/machine signing material was tried and is insufficient.",
            "ask_user",
            "ios.xcuitest.runner.signing_blocked",
            "human",
            _ask(
                "XCUITest runner \u7b7e\u540d\u5931\u8d25\uff08\u7f3a\u5c11\u6709\u6548\u7b7e\u540d\u8eab\u4efd\uff09\u3002\u8bf7\u9009\u62e9\u4e0b\u4e00\u6b65\u3002",
                "Runner \u9700\u8981\u6709\u6548\u7684 Apple Development \u7b7e\u540d\u8eab\u4efd / profile \u624d\u80fd\u88c5\u5230\u771f\u673a\uff1b\u5de5\u7a0b\u5df2\u6709\u7b7e\u540d\u6750\u6599\u5df2\u5c1d\u8bd5\u4f46\u4ecd\u4e0d\u8db3\u3002",
                [
                    {"id": "A", "label": "Reuse a known-good signed runner bundle id.", "impact": "Avoids re-provisioning a new bundle id."},
                    {"id": "B", "label": "I will configure a valid signing identity/profile, then retry.", "impact": "Unblocks runner signing.", "requiresConfirmation": True},
                    {"id": "C", "label": "Stop", "impact": "Keep the task blocked."},
                ],
                category="real_device_or_signing",
            ),
        )

    # Android/Kotlin: a large set of unresolved references that point at symbols
    # which still exist in a project module is far more often a stale incremental
    # build output / classpath cache than a real source defect. Steer the retry
    # toward an upstream/downstream module build-output reset instead of letting
    # Generator start editing source. The reason carries the diagnosis ladder so
    # the retry applies clean + --rerun-tasks before touching code.
    if "unresolved reference" in text and (
        "compilekotlin" in text or "kotlin" in text or p == "android"
    ):
        return Classification(
            "build_failure",
            "Kotlin unresolved-reference build failure. Before editing source, treat this "
            "as a likely stale module build output / classpath (Galaxy) cache, not a source "
            "defect: confirm the unresolved symbols' source still exists and is aligned with "
            "HEAD, build the upstream module on its own to prove it compiles, then clean the "
            "upstream and downstream module build/ outputs and rerun the downstream compile "
            "task with --rerun-tasks (small-knife scope first, not a full clean), and only "
            "after that still fails should source be edited. Do not patch imports / copy "
            "constants / move functions one by one to chase the unresolved symbols.",
            "retry_generator",
            "android.build.kotlin_unresolved_stale_cache",
        )

    # Gradle build *command* misconfiguration: a missing task/flavor is a
    # verification-command error, not a product-code defect. Do not send
    # Generator to edit source for it; the verifyCommand task path must be fixed.
    if "cannot locate tasks that match" in text or (
        "task" in text and "not found" in text and "gradle" in text
    ):
        return Classification(
            "verifier_command_misconfigured",
            "Gradle reports a task/flavor that does not exist (e.g. Cannot locate tasks that "
            "match ':app:assembleLiteDebug'). This is a verification-command / flavor "
            "configuration error, not a source-code failure. Fix the verifyCommand task path "
            "or flavor in the plan/runtime config; do not replan or edit product code for it.",
            "retry_generator",
            "android.build.gradle_task_not_found",
            "verifier",
        )

    # Explicitly unclassified: the code patterns above did not match. Return a
    # classification that tells the calling harness: "let the model read the
    # raw log and do the triage." The evaluator MUST then follow the Failure
    # Triage Protocol in evaluator_prompt.md to extract specificErrors, pick a
    # fine-grained category (dependency_missing / tooling_version_mismatch /
    # product_code_error / ...), propose a recoveryAction, and set a stable
    # sameProblemKey so the next round converges instead of looping.
    if ph == "build" or "build failed" in text:
        return Classification(
            "unknown",
            "Build failed but no deterministic code-pattern matched. "
            "The evaluator MUST follow the Failure Triage Protocol to read the "
            "raw build log, extract specificErrors, and classify into a "
            "fine-grained category (dependency_missing / tooling_version_mismatch / "
            "product_code_error / signing_or_provisioning / "
            "device_unavailable_or_untrusted / resource_exhausted_or_permissions / "
            "network_or_external_service / flaky_or_timeout). Do not stop here "
            "with a generic 'build_failure' — the sameProblemKey must be specific "
            "enough to break the loop.",
            "retry_generator",
            f"{p}.build.unclassified_triage_needed",
            "agent",
            specificErrors=[],
            recoveryAction="triage_needed",
            triageSource="unclassified",
        )
    if ph == "install":
        return Classification(
            "install_failure",
            "App installation failed; no code pattern matched. The evaluator MUST triage the raw install log following the Failure Triage Protocol.",
            "retry_generator",
            f"{p}.install.unclassified_triage_needed",
            "agent",
            specificErrors=[],
            recoveryAction="triage_needed",
            triageSource="unclassified",
        )
    if ph == "launch":
        return Classification(
            "launch_failure",
            "App launch failed; no code pattern matched. The evaluator MUST triage the raw launch log following the Failure Triage Protocol.",
            "retry_generator",
            f"{p}.launch.unclassified_triage_needed",
            "agent",
            specificErrors=[],
            recoveryAction="triage_needed",
            triageSource="unclassified",
        )
    if ph == "test" or "test failed" in text or "assert" in text:
        return Classification(
            "test_failure",
            "Test failure not matched by code patterns. The evaluator MUST triage following the Failure Triage Protocol (was it product_code_error, test_fixture_or_harness_bug, flaky_or_timeout?).",
            "retry_generator",
            f"{p}.test.unclassified_triage_needed",
            "agent",
            specificErrors=[],
            recoveryAction="triage_needed",
            triageSource="unclassified",
        )
    if exit_code and exit_code != 0:
        return Classification(
            "unknown",
            f"Command exited with code {exit_code} but no deterministic pattern matched — the evaluator MUST triage the raw output following the Failure Triage Protocol.",
            "retry_generator",
            f"{p}.{ph}.exit_{exit_code}_triage_needed",
            "agent",
            specificErrors=[],
            recoveryAction="triage_needed",
            triageSource="unclassified",
        )
    return Classification(
        "unknown",
        "Unable to classify failure from current evidence.",
        "replan",
        f"{p}.{ph}.unknown_triage_needed",
        "human",
        specificErrors=[],
        recoveryAction="triage_needed",
        triageSource="unclassified",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify CodeAutonomy failure logs")
    parser.add_argument("--platform", default="generic")
    parser.add_argument("--phase", default="unknown")
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--log-file")
    parser.add_argument("--log", default="")
    args = parser.parse_args()
    log = args.log
    if args.log_file:
        log += "\n" + open(args.log_file, errors="replace").read()
    c = classify(args.platform, args.phase, log, args.exit_code)
    print(json.dumps(asdict(c), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
