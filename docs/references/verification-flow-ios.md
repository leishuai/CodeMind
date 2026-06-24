# iOS Verification Flow Reference

iOS-specific verification commands and flows for AutoMind. This is loaded on
demand for iOS tasks; the cross-platform skeleton (generic script validation,
visual, external-sink, verification-unblock policy, probe-flow, evidence,
`Validation.md`/`evaluation.json`, retry semantics) lives in
[`verification-flow.md`](verification-flow.md). The Android equivalent is
[`verification-flow-android.md`](verification-flow-android.md).

AutoMind should classify environment/device/tool blockers separately from
product-code failures. Evidence beats guesses.

## Operational priority quick reference

Use this order when an iOS task needs real app/device verification:

1. **Honor the pre-implementation one-shot modality decision.** If the user or
   Decision Bundle already approved simulator coverage, directly choose the
   simulator automation ladder; do not first exhaust real-device work.
2. **Prepare the selected device/project facts first.** For real device, confirm
   device visibility, Developer Mode/UI Automation, signing material, bundle id,
   scheme, and device id namespace. For simulator, confirm simulator device,
   scheme, build destination, and simulator-compatible app/test target.
3. **Automation is the default verification posture.** When the user approved
   real-device verification, exhaust real-device automation before simulator,
   human-assisted operation, or dry-run/static/build-only downgrade.
4. **Climb the real-device UI-runner ladder first when real device is selected.** Existing project UI test
   target -> decoupled native XCUITest -> external UI runner with a complete
   control channel.
5. **If those real-device tiers cannot work, temporary project edits are allowed
   before simulator.** Add or enable a UI test target / `build-for-testing` only
   as a reversible verification-unblock change with checkpoint/restore evidence.
6. **If project-edit automation still cannot reach the page, try automated direct
   entry on the real device.** Use direct-route, deep link, URL scheme, or debug
   route only as low-fidelity automation; it cannot prove an end-to-end
   navigation testcase.
7. **Use simulator automation when allowed.** If simulator was approved during
   pre-implementation one-shot, start here directly. If real device was selected
   first, reach this step only after simulator coverage or runtime downgrade is
   approved.
8. **Use human-assisted evidence capture after automated paths.** The user's
   physical action is not proof unless AutoMind captures machine-checkable
   post-condition evidence.
9. **Last resort is an explicit downgrade decision.** If everything above fails,
   ask the user whether to accept dry-run, static proof, compile/build-only proof,
   or another reduced-scope outcome. A blocker never satisfies a required `TC-*`
   by itself.

---

## iOS real-device preparation

Real-device validation depends on both tools and the current device state. Ask
the human to confirm:

- the iPhone has trusted this Mac;
- iOS 16+ Developer Mode is enabled;
- for Personal Team / Apple Development first install, the developer profile is trusted on the device;
- Xcode can see the device;
- signing team / provisioning can run on the target device;
- device is unlocked and not covered by permission/system dialogs.

Useful checks:

```bash
xcrun xctrace list devices
xcrun devicectl list devices
xcodebuild -showdestinations -scheme <scheme>
```

Developer Mode is required for build/install/launch/test/UI automation. If the device is visible but Developer Mode is disabled, classify as `permission_blocked` or `mobile_device_unavailable` and ask the human; do not send Generator to change product code.

For project-native UI test targets, a visible iPhone with **Enable UI
Automation** turned on can still suffer from Xcode/CoreDevice link instability.
If `xcodebuild test`, XcodeBuildMCP, or a native UI test target reports repeated
pre-session connection failures, automation-session startup failures, or device
unavailable errors while the device is otherwise connected, classify it as a
device/host link recovery blocker rather than product code or signing. Ask for a
single physical recovery action: keep the phone unlocked and lit, replug the USB
cable, acknowledge any trust prompt, then turn **Settings -> Developer ->
Enable UI Automation** off and back on before retrying the same command.

---

## iOS prerequisites

### Read-only project probe

Before modifying a real iOS project, inspect it read-only:

```bash
./automind.sh ios-project-probe <project-path> [task-code] [--scheme <scheme>]
```

For large customized workspaces, run the command-surface probe first:

```bash
./automind.sh ios-command-probe <workspace-path> [task-code] [--include-help]
```

These probes should detect scheme, bundle id, build settings, test target availability, custom workspace wrapper/Bazel/CocoaPods/Bundler surfaces, and recommended evaluator path. Use `ios-command-probe` before project generation/build/test in large customized workspaces.

### Tool checks

```bash
command -v xcodebuild
command -v xcrun
command -v idevice_id || true
command -v ios || true
command -v xcodebuildmcp || true
```

Recommended iOS 16+ real-device path is XCUITest / XcodeBuildMCP when available. `libimobiledevice`, `go-ios`, or `ios-deploy` may help with compatibility/debugging but should not silently replace the primary path.

Optional project-local Python helper for physical screenshots:

```bash
automind setup-automation-tools ios
```

Preflight/evaluator may run this automatically when screenshot/app-smoke requires the helper and it is missing; users can also run it up front. It creates `.venv-ios-tools` in the target workspace and installs packages from the AutoMind runtime `requirements/ios-tools.txt`; it does not install Xcode, change signing, start `tunneld`, or manipulate devices. `tunneld`/sudo remains a separate human-confirmed step when needed.

### iOS real-device preflight

```bash
./automind.sh ios-preflight <task-code> [iteration]
```

Expected records:

- `evaluation.json`
- `Validation.md`
- `logs/iter-N/env.json`
- `logs/iter-N/commands.md`

---

## iOS real-device flow

### Device IDs

Do not confuse:

- CoreDevice identifier from `devicectl` / XcodeBuildMCP;
- traditional UDID from `xcodebuild -showdestinations`.

If destination matching fails, run:

```bash
xcodebuild -scheme <scheme> -showdestinations
```

and select the `platform:iOS` real-device destination id.

### Build / test

```bash
xcodebuild \
  -workspace <workspace>.xcworkspace \
  -scheme <scheme> \
  -destination 'id=<device-udid>' \
  build
```

For XCUITest:

```bash
xcodebuild \
  -workspace <workspace>.xcworkspace \
  -scheme <scheme> \
  -destination 'id=<device-udid>' \
  test \
  -resultBundlePath logs/iter-N/result.xcresult
```

### Install / launch

Prefer XcodeBuildMCP or `devicectl` when available. Record the exact command and device id used.

```bash
xcrun devicectl device install app --device <coredevice-id> path/to/App.app
xcrun devicectl device process launch --device <coredevice-id> com.example.bundleid
```

A simulator build with `CODE_SIGNING_ALLOWED=NO` cannot be installed on a real device. If install/launch fails because the developer profile is not trusted, ask the human to trust it on the device instead of modifying app code.

### Real-device UI automation

AutoMind can operate iOS apps through XCUITest/probe-flow. Do not describe iOS
capability as "screenshot/evaluation only" when a required testcase needs app
interaction. The safe path is:

1. use `ios-preflight` / project probe to confirm Xcode, device, signing, and UI
   Automation readiness;
2. encode required actions in `probe-flow.ios.json` or `action-plan.ios.json`:
   `tap`, `tap_if_present`, `input`, `scroll`, `assert_exists`, `assert_text`,
   `wait`;
3. materialize/review the Swift XCUITest with
   `ios-probe-flow-materialize` or `ios-action-plan`;
4. run the project/native/external XCUITest through `ios-xcuitest`;
5. collect `.xcresult`, logs, screenshots, accessibility/UI evidence, and
   post-action assertions. Tier-1 hard evidence here is `.xcresult`
   (`tests_passed`/`exit_code`), runtime logs with matched keywords, and
   post-action assertions; screenshots are Tier-2 supporting evidence — strongly
   recommended when capturable and surfaced in `report.html`, but they do not
   replace Tier-1. See the evidence-tier ordering in
   [`verification-flow.md`](verification-flow.md#evidence-tiers-which-evidence-to-prefer).

When the device is connected and authorized, driving the required in-app actions
— play, skip/next, trigger an error, interrupt/pause, navigate — and capturing
`.xcresult`/logs is AutoMind's own verification work. Encode and run them through
XCUITest/probe-flow; do not stop and ask the user "I cannot operate your physical
device, please confirm how to verify". That phrasing is a non-whitelisted soft
pause. Only escalate to `ask_user(real_device_or_signing)` for a genuine
human/system gate: no usable device, an unresolved unlock / trust prompt, missing
signing/provisioning material, or denied UI Automation permission — and then say
exactly what AutoMind detected and the single physical action needed.

Selector strategy, the reliable action set (`tap`/`tap_if_present`/`input`/
`scroll`/`assert_*`/`wait`), and the coordinate-fallback rule are owned by
[`summaries/preloaded/ios-ui-actions.md`](../../summaries/preloaded/ios-ui-actions.md);
prefer accessibility identifiers/labels/predicates over coordinates. iOS uses
the same `uiUnblock` intent policy as Android/Web, but runner execution should
stay inside project-native XCUITest/materialized action plans: safe dismiss
controls and app-internal privacy/terms agree/allow and OS/app permission allow may be closed with evidence; reject/deny,
payment, account/login grants, delete/reset, external upload, signing/device
trust changes, or ambiguous/irreversible consent actions require explicit
authorization.

For iOS layout/frame proof, a stable selector is part of the evidence path. If the target view/container has no reliable selector, Generator may add a minimal `accessibilityIdentifier` / label as a testability anchor, as long as it does not change layout or behavior. Prefer this over coordinate-only or nth-element proof, and record the anchor plus screenshot/hierarchy/frame evidence.

### iOS UI-runner priority ladder and external-runner downgrade

Real-device iOS UI automation is first-class verification work. Prefer the
highest-fidelity runner that can actually execute the required actions on the
connected device, and downgrade only when fresh evidence/classifier output proves
the current runner is unsuitable.

Decision rules:

- **Automation above all.** With real-device approval, the ordering is
  real-device automation (including reversible project edits and direct-route /
  URL-scheme automation) -> simulator automation when approved -> non-automated
  human-assisted operation -> explicit reduced-scope downgrade.
- **Pre-approved simulator is a selected automation mode, not a late fallback.**
  If the pre-implementation one-shot Decision Bundle allows simulator coverage,
  start with simulator automation and record that authorization in the evidence.
- **Do not ask the user to perform ordinary UI actions** while a viable runner can
  tap/input/scroll/assert on the device.
- **Native-first means existing-native-first**: use an existing project UI test
  target immediately, but do not edit a large `.pbxproj` before trying the
  reversible no-edit external runner.
- **Temporary project edits are allowed before simulator** after the no-edit
  real-device tiers fail, because a reversible project edit can still preserve
  real-device automation.
- **Simulator is not real-device proof** unless the testcase explicitly allows
  simulator coverage or a runtime downgrade is approved.
- **Dry-run is never a runtime-proof substitute**; it validates intent and
  selector/config shape only.

Real-device automation tiers, from highest to lowest:

1. **Project/native UI test target via Xcode.** Run the project's own UI test
   target/scheme with `xcodebuild test`. This is the highest-fidelity path:
   Xcode hosts the XCTest session, `.xcresult` captures the result, and
   `tap`/`input`/`scroll`/`assert_*` are supported through the normal XCUITest
   channel. Use this whenever the target already exists and is usable.
2. **Decoupled native XCUITest.** Split the same native/Xcode channel into
   `build-for-testing -> devicectl install -> test-without-building`. Use this
   when the full project command is brittle but the app/test bundle can be built
   and installed separately. This does not weaken UI capability; it only changes
   how the app/test bundle is delivered.
3. **External UI runner with a complete control channel.** Use when the project
   has no usable UI test target. The runner drives the already-installed target
   app by bundle id without modifying the original project. Valid control
   channels include an Xcode-hosted external runner, WebDriverAgent, go-ios, or
   Appium/WDA-style flows where taps/inputs go through WDA's WebDriver/HTTP
   server. `pymobiledevice3` can be a transport/launcher in this tier when paired
   with WDA/XCUITestService; the UI commands are serviced by WDA, not by a missing
   IDE interface.
4. **Temporary project edit to obtain a real-device UI runner.** If tiers 1-3
   cannot run and the blocker is fixable, AutoMind may add/enable a UI test
   target or `build-for-testing` support as a reversible verification-unblock
   change. Checkpoint first, record `verificationUnblockChanges[]`, and restore
   or explicitly promote the edit before finish.

Avoid path:

- **Do not retry `pymobiledevice3 dvt xcuitest` as a host for an arbitrary
  IDE-dependent custom XCTest runner.** That specific path lacks
  `XCTestManager_IDEInterface`, so a runner expecting Xcode on the other end can
  start and then disconnect. This is a capability dead end for that host shape,
  not proof that pymobiledevice3/WDA cannot drive UI. Replan to tier 1, 2, or 3.

After real-device runner tiers:

1. **Direct-route / URL scheme / debug route on the real device.** If the target
   page still cannot be reached through the normal flow, temporarily run
   route/navigation code, a deep link, URL scheme, or test-only entry point to
   load the page directly — see
   [Direct-route page load](verification-flow.md#direct-route-page-load-low-fidelity-last-resort).
   This is still automated, but weaker evidence; it cannot satisfy a required
   end-to-end navigation testcase.
2. **Approved simulator automation.** If the user or testcase allows simulator
   coverage during pre-implementation one-shot, this can be the first execution
   branch instead of a late fallback. If real-device proof was selected first,
   use simulator-capable automation only after the approval/downgrade decision.
   Valid options include an external UI runner on iOS Simulator, an existing
   project native UI test target against a simulator destination, or a temporary
   simulator UI test target when the project has none.
3. **Human-assisted real-device evidence capture.** Only after automated
   real-device and allowed simulator paths are unavailable, inappropriate, or
   insufficient should the user perform one narrow physical action on the iPhone
   while AutoMind captures screenshots/screen recording, syslog/console/runtime
   logs, DB or event-cache diffs, external sink events, and post-action
   assertions. Manual action itself is not proof.
4. **Explicit reduced-scope downgrade.** If all automation and human-assisted
   evidence paths fail, ask the user what reduced proof is acceptable: dry-run,
   static review, compile/build-only success, or another scoped fallback. Record
   this as a downgrade/ask_user decision, not as runtime proof.

The detailed classifier signals, `sameProblemKey`s, and downgrade/route actions
(root-install, bootstrap-abort, capability-blocked, signing-blocked, device-link
instability, post-session DTX/code 74) are the single source of truth in
[`summaries/preloaded/ios-external-xcuitest-runner.md`](../../summaries/preloaded/ios-external-xcuitest-runner.md)
and [`summaries/preloaded/ios-signing-install.md`](../../summaries/preloaded/ios-signing-install.md);
do not re-document them here. A repeated `external_runner_capability_blocked` on
the same runner/root cause is a known avoid-path: the reuse gate forces `replan`
instead of re-asking the user.

### Build unblock policy

See also [Verification unblock policy](verification-flow.md#verification-unblock-policy) for the generic rule that applies across iOS, Android, web, and script validation.

When `xcodebuild` fails, do not immediately stop unless the failure is a true
environment/signing/device blocker that needs user action. Inspect the first compile errors and classify them:

- errors in files changed by the task -> route to Generator repair;
- missing Xcode/device/signing/trust/system SDK -> `permission_blocked`,
  `mobile_device_unavailable`, `tool_missing`, or `environment_blocked`;
- unrelated extension/widget/generated target/dependency failure -> consider a
  temporary verification unblock if it does not weaken the selected `TC-*`;
- broken test harness or probe flow -> self-repair the harness and rerun.

Classification is not acceptance. A build/test/device blocker may explain why a
run could not complete, but it must not make a required `TC-*` pass by itself.
Prefer continued safe attempts: repair product compile errors, try a reversible
verification-unblock for unrelated targets, switch to the connected real device
when appropriate, rerun preflight, or replan the verification target. Use
`ask_user` when the next step requires device trust/unlock/Developer Mode,
signing credentials, privileged services, or another human/system decision.

Safe iOS unblock examples include creating a temporary validation workspace,
excluding or stubbing an unrelated broken target, adding a local debug/test log
hook, or adjusting local build flags for the validation run. Before editing,
create `automind checkpoint create <task-code> "before verification unblock
changes"` or save `git status` and `git diff` under `logs/iter-N/`. After the
run, restore the temporary files or explicitly promote them, then record
`verificationUnblockChanges[]` in `evaluation.json` plus restore evidence in
`Validation.md`.

---

## iOS simulator flow

Use simulator verification only when the testcase allows simulator coverage or a
runtime downgrade is approved. Simulator evidence is not real-device proof.

List simulators:

```bash
xcrun simctl list devices available
```

Boot simulator:

```bash
xcrun simctl boot <device-udid>
```

Install app:

```bash
xcrun simctl install <device-udid> path/to/App.app
```

Launch app:

```bash
xcrun simctl launch <device-udid> com.example.bundleid
```

Screenshot:

```bash
xcrun simctl io <device-udid> screenshot logs/iter-N/screenshot.png
```

Logs:

```bash
xcrun simctl spawn <device-udid> log stream --style compact --predicate 'process == "AppName"'
```
