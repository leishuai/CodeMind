# App-use Verification

App-use verification is AutoMind's capability for proving client/UI behavior by operating the real app or browser, observing runtime state, and recording structured success or failure explanations. It is not a screenshot-only review and it is not a fixed coordinate script.

This reference is for AutoMind Planner/Generator/Evaluator prompts, platform adapters, skill users, and maintainers. Platform-specific runners such as Android probe-flow, iOS XCUITest/probe-flow, and Web/browser probe-flow should follow this contract when a TestCase requires real UI/client behavior.

## Operating rules (read first)

- App-use is active verification: operate the app/browser and assert the
  postcondition. It is not screenshot-only review.
- Do not ask the user to perform automatable actions while a platform runner can
  launch, tap/click, input, scroll, or assert.
- Treat source UI maps and user-provided paths as hypotheses. Runtime hierarchy,
  logs, screenshots, DOM/accessibility, and post-action assertions decide.
- Honor the pre-implementation one-shot modality decision. If simulator/emulator
  coverage was already approved, choose that automation branch directly rather
  than first exhausting physical-device runners.
- Android default is real device when a device is connected and authorized; do
  not switch to Emulator unless the user/TestCase explicitly selected or approved
  Emulator coverage.
- Climb the operation ladder before lower-fidelity fallbacks. With real-device
  approval, the preferred order is real-device automation -> reversible project
  edit to preserve automation -> real-device direct-route/deep-link -> approved
  simulator/emulator automation -> human-assisted evidence capture -> explicit
  reduced-scope downgrade.
- Manual action or screenshot confirmation alone is not enough for strict
  runtime proof; attach machine-checkable post-condition evidence.

## Goals

App-use verification should:

1. Execute the smallest safe UI path that can prove the required TestCase.
2. Combine source-derived UI hints with runtime hierarchy/screenshot observations.
3. Record every meaningful attempt as structured evidence.
4. Explain both success and failure in a machine-readable way.
5. Avoid false positives from startup-only, preflight-only, or screenshot-only evidence.
6. Avoid false negatives from flaky launch APIs, stale app state, optional popups, or selector drift.

## When to use app-use

Use app-use verification when a required TestCase depends on client-visible or runtime UI behavior, for example:

- opening a screen/page/route;
- tapping a button/card/tab/menu;
- playing, pausing, stopping, or seeking media;
- triggering a modal/dialog/toast/snackbar;
- verifying visible text, layout state, selected tab, or navigation;
- verifying runtime logs/events that require real UI actions;
- reproducing a user journey before collecting deeper evidence.

Do not use app-use as a substitute for stronger project-native proof when a native unit/integration/E2E test can prove the behavior more directly. Prefer combining both when required: app-use for real runtime path, native logs/tests for internal signal proof.

## Modes

### `user_path`

Use `user_path` when the user or TestCase gives an explicit operation path.

Example:

```text
Launch app -> tap 听书 -> enter first card -> open 目录 -> try Overview -> scroll -> read tags
```

The Evaluator should materialize this path into executable actions, but still validate each assumption against runtime hierarchy/screenshot. A user-provided path is a high-priority hypothesis, not a guaranteed fact.

### `goal_directed`

Use `goal_directed` when the TestCase gives a target outcome but no exact path.

Example:

```text
Prove audio playback can start and a stop_reason is reported when playback stops.
```

The Evaluator should first build or request a source-derived UI map, then explore the runtime UI safely until it finds a path that can prove the target signal.

### `hybrid`

Use `hybrid` when a user path exists but must be adapted using source/runtime evidence.

Example: the user says to switch to `Overview` inside `目录`, but runtime hierarchy shows the current app version exposes `Overview` on the detail overview and the catalog page has no `Overview` tab. The Evaluator should record the catalog-tab hypothesis as ruled out and use the detail overview intro section if that still proves the goal.

## Source UI map

For non-trivial app-use, Generator or Evaluator should collect source-derived hints before or during execution.

A task-local `source-ui-map.json` may include:

```json
{
  "goal": "read intro tags from an audio detail page",
  "platform": "android",
  "candidateEntryPoints": [
    {
      "name": "audio detail page",
      "activity": "com.example.app.DetailActivity",
      "signals": ["目录", "Overview", "开始Play"]
    }
  ],
  "candidateSelectors": [
    {"text": "听书"},
    {"text": "目录"},
    {"text": "Overview"},
    {"resource_id": "com.example.app:id/audioDetailFeed"}
  ],
  "runtimeSignals": [
    "foreground package is target app",
    "hierarchy contains target package nodes",
    "detail page exposes intro/catalog/play controls"
  ],
  "knownPopups": [
    {"name": "privacy", "acceptSelector": {"resource_id": "com.example.app:id/confirm_btn"}}
  ]
}
```

The source UI map is guidance. Runtime evidence is authoritative.

## Action ladder + postcondition verification

External UI operations should use an action ladder when a single API is known to be flaky. Each rung must be followed by a postcondition check and evidence recording.

### Generic operation ladder

Use this ordering for any client app/browser operation:

1. Project-native proof when available: native E2E/UI test, browser E2E, platform
   test report, or deterministic project script.
2. Platform app-use runner: Android probe-flow, iOS XCUITest/probe-flow/WDA/go-ios,
   or web probe-flow with project E2E.
3. Alternate launch/control API inside the same fidelity level, followed by the
   same postcondition check.
4. Safe overlay unblock / selector repair, then retry the same intended action.
5. Minimal reversible project edit when it preserves automation and no-edit
   runners cannot execute.
6. Direct-route/page-load/deep-link only after the normal UI path is genuinely
   blocked.
7. Simulator/emulator automation only when that modality is allowed for the TC.
8. Human-assisted evidence capture only after automated paths are unavailable or
   inappropriate, and only with machine-checkable postcondition evidence.
9. Reduced-scope downgrade such as dry-run/static/build-only proof only after an
   explicit user decision.

### Android launch ladder

Android app launch should not trust `app_start` alone. Use a ladder such as:

1. Optional cold start: `forceStop` / `stopBeforeLaunch` when the flow requires a fresh entry state.
2. `adbutils` / `uiautomator2` `app_start(package, activity)`.
3. Fallback: explicit `adb shell am start -W -n package/activity`.
4. Fallback: launcher intent via `adb shell monkey -p package -c android.intent.category.LAUNCHER 1`.
5. After each attempt, check the foreground package/activity via `app_current()` or equivalent.
6. Record attempts in `launch-attempts.json`.
7. Only pass launch when the foreground package matches the target package and the expected hierarchy/page readiness checks pass.

This prevents:

- false failure when one launch API is flaky but another works;
- false success when the command returns but the device stays on launcher/SystemUI/old page;
- unreproducible failures with no launch evidence.

The same pattern should be used for other external systems: build/test command ladders, adb path discovery, browser start/open ladders, iOS launch ladders, and local service readiness checks.

## Auto-unblock overlay before target actions

Before judging a target control as missing, UI runners should capture the current
screen/page, detect safe overlays, and dismiss only low-risk blockers. This is a
precondition step:

```text
launch/open -> capture UI state -> auto-unblock safe overlay -> capture UI state
again -> execute the TC action -> assert postcondition
```

Safe dismiss examples are close/skip/later/not-now/cancel/×/知道了/跳过/稍后.
Sensitive examples such as login, allow/permission, agree/terms, payment, delete,
reset, upload, signing, or device trust must not be auto-clicked without explicit
authorization. A dismissed overlay is evidence that the path was unblocked; it
does not satisfy the business `TC-*` until the requested action and assertion
also pass.

## Fresh launch vs resume launch

Flows must distinguish fresh-entry tests from continuation tests.

Use fresh launch when verifying a fixed user path from app entry:

```json
{"type": "launch", "forceStop": true, "wait": 3}
```

Use resume launch when testing recovery from an existing state or continuing a previously established flow.

Stale app state is a common source of false failures. If a flow expects home but launch resumes into a detail page, the runner should either perform a fresh launch or record the mismatch as a structured state diagnosis.

## Attempt recording

Each meaningful UI action should be represented in `evaluation.json.testResults[].uiExploration` and/or platform summary artifacts.

Recommended shape:

```json
{
  "mode": "hybrid",
  "goal": "read intro tags from audio detail",
  "sourceUiMap": "source-ui-map.json",
  "runtimeState": {
    "foregroundPackage": "com.example.app",
    "activity": "com.example.app.DetailActivity"
  },
  "attempts": [
    {
      "progressKind": "navigation",
      "hypothesis": "The 听书 tab leads to audio cards.",
      "actionTried": "tap text=听书",
      "expectedSignal": "audio tab/category content appears",
      "outcome": "pass",
      "evidence": ["action-04-after.png", "action-04-after-hierarchy.xml"]
    },
    {
      "progressKind": "control_discovery",
      "hypothesis": "The catalog page exposes a Overview tab.",
      "actionTried": "tap text=Overview after opening 目录",
      "expectedSignal": "intro tab becomes active",
      "outcome": "soft_fail",
      "ruledOut": ["catalog surface does not expose Overview tab on this app version"],
      "remainingHypotheses": ["use detail overview intro section instead"],
      "evidence": ["action-12-after.png", "action-12-after-hierarchy.xml"]
    }
  ],
  "extracted": {
    "introTags": ["现代言情", "家庭"]
  },
  "stopReason": "goal_proved_with_adapted_path"
}
```

## Success explanation

A pass result must explain why the TestCase is proved.

Required elements for runtime/client-facing required TestCases:

- `testResults[].result = "pass"`;
- `observedSignals[]` includes the actual runtime signals observed;
- `evidenceAssessment.verdict = "proved"`;
- hard evidence references include screenshots, hierarchy XML/JSON, logs, media/session state, or project-native test output;
- if app-use was involved, `uiExploration` or linked action trace explains the path taken.

Do not pass a required runtime TestCase using only:

- successful app launch;
- preflight-only device readiness;
- startup screenshot;
- static code review;
- generic command success unrelated to the required behavior.

## Failure and soft-failure explanation

Failures must narrow the search space. Do not repeat the same action blindly.

Use hard failure when:

- the required postcondition is absent;
- the app crashes;
- the target package/page cannot be reached after launch ladder;
- required runtime evidence cannot be collected;
- a product behavior required by the TestCase is wrong.

Use soft failure when an exploratory branch fails but the flow can continue with another hypothesis.

Soft failure should include:

```json
{
  "softFailure": true,
  "ruledOut": ["catalog surface does not expose Overview tab on this app version"],
  "remainingHypotheses": ["use detail overview intro section instead"],
  "evidence": "action-12-after.png"
}
```

`continueOnFail` must not mean "ignore the failure". It means:

1. record the failed branch;
2. explain what was learned;
3. continue only if another valid route/hypothesis remains;
4. let strong postconditions decide whether the overall TestCase is proved.

## Ask-user policy

Do not ask the human when AutoMind can determine the fact from artifacts.

Examples AutoMind should diagnose directly:

- foreground package is launcher, not target app;
- screen is off from dumpsys power;
- lockscreen/keyguard is focused;
- adb binary missing from PATH but exists at SDK path;
- Gradle failed due local socket permission;
- page hierarchy lacks the requested tab;
- app-use branch failed because selector text is absent.

Ask the human only when a real external action or decision remains necessary, such as:

- unlock or connect a physical device;
- approve a security-sensitive command;
- choose between lowering proof scope vs providing external evidence;
- provide credentials or a test account;
- approve destructive/system-changing/payment/upload actions.

When asking, phrase it as a diagnosis plus requested action, not a guess:

```text
AutoMind detected the Android lockscreen is focused from dumpsys window. Please unlock the device, then retry.
```

Avoid:

```text
The device may be locked. Is it unlocked?
```

## Completion gate

Completion should remain fail/blocked unless required runtime TestCases have proved evidence. App-use exploration artifacts are useful, but they do not override required acceptance criteria.

A valid completion should answer:

1. What user/client behavior was operated?
2. What signal proved the behavior?
3. Where is the machine-verifiable evidence?
4. What failed branches were ruled out?
5. What, if anything, remains unproved?

Final handoff must also point humans to the shortest review path:

1. Open `Report.html` first.
2. Review the report's `Test Results` section, where each `TC-*` row should
   show a concise `Key Evidence` summary: screenshot(s), TC-level
   `evidenceAssessment.machineAnchor`, hardMetrics anchors, key files such as
   `music-events.txt`, full runtime logs, and ledger links when relevant.
   Keep the complete noisy artifact list in the final column for traceability,
   not as the primary reading path.
3. For every executed runtime/UI TC, include screenshot evidence in the report
   when available. If a screenshot cannot be captured, the TC row should say so
   explicitly and still attach machine-checkable logs/state evidence.

## Platform notes

### Android

Use `android-preflight` + `android-probe-flow` for device readiness, launch, hierarchy, screenshots, taps, swipes, extraction, and app-use action traces.

### iOS

Prefer project-native XCUITest when available. Use iOS probe-flow/action-plan runners when a generic runner can safely operate the app. Record UI hierarchy/screenshot and XCUITest logs as evidence.

### Web/browser

Prefer project-native Playwright/Cypress E2E when present. Use browser probe-flow/snapshots for runtime exploration. Do not silently install browsers/drivers.
