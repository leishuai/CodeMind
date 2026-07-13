# Probe-flow Generation

Probe-flow is the executable validation plan derived from requirements and test cases. It should not be a fixed script template that limits the model. It is a generated, reviewable artifact.

## Key idea

```text
User intent / Requirements.md / TestCases.md
  -> testIntent
  -> probe-flow steps
  -> schema validation
  -> platform runner
  -> probe-flow summary + evaluation.json
```

The task-local file is an instance:

```text
.automind/tasks/<task>/probe-flow.android.json
.automind/tasks/<task>/probe-flow.ios.json
```

(Legacy `.automind/tasks/<task>/probe-flow.json` is still readable but emits a
one-time deprecation warning; rename to the platform-suffixed name for new
tasks.)

The format guidance comes from:

```text
schemas/probe-flow.schema.json
examples/probe-flows/android-basic.json
examples/probe-flows/ios-intent-basic.json
docs/references/probe-flow-generation.md
```

## Are example files mandatory?

No.

`examples/probe-flows/*.json` are starter examples, not hard templates. They help humans and agents understand the shape, but the generated flow should be driven by the task's `Requirements.md`, `TestCases.md`, app config, platform constraints, and relevant summaries.

Do not force every generated flow to follow an example exactly.

## Unified generation process

Android and iOS should align at the process level:

```text
1. Read task context
   - Requirements.md
   - TestCases.md if present
   - Plan.md
   - runtime-state.json
   - existing probe-flow if present
   - relevant summaries

2. Build testIntent
   - goal
   - sources
   - preconditions
   - authorization scopes
   - functional acceptance criteria
   - quality acceptance criteria when relevant: performance duration, smoothness, stability, architecture boundaries, class/function relationship sanity

3. Generate steps
   - setup steps
   - optional blocker handling
   - task verification actions
   - assertions
   - quality evidence sampling, when relevant
   - post-action evidence checks

4. Add confidence/risk/guards only when useful
   - ambiguous target
   - visual-only control
   - risky action
   - state-dependent step
   - fallback needed

5. Validate against schema
   - schemas/probe-flow.schema.json

6. Execute with platform adapter
   - Android: android_probe_flow_runner.py
   - iOS: ios_probe_flow_runner.py / XCUITest / materializer

7. Write evidence and merge evaluation
   - probe-flow-summary.json or equivalent
   - quality-summary.json / architecture-review.md when Quality cases apply
   - one merged evaluation.json
   - Validation.md
   - logs/iter-N/
```

## What should align across Android and iOS

Aligned:

- `testIntent` concept
- step semantics
- confidence/risk/guards/postChecks shape
- schema validation
- evidence and one merged `evaluation.json` after functional + quality checks
- no product path invented by runner
- no sensitive action without authorization
- functional and quality test intent preserved from TestCases/Requirements

Platform-specific:

- selector fields
- preflight details
- runner implementation
- screenshots/OCR backend
- native test technology

## Android current state

Android currently has the more complete automatic generation path:

```text
orchestrator/main.py::generate_probe_flow_json()
  -> .automind/tasks/<task>/probe-flow.android.json
  -> scripts/android_probe_flow_runner.py
  -> probe-flow-summary.json
  -> evaluation.json
```

The generator reads:

- existing `probe-flow.android.json` first (legacy `probe-flow.json` falls back with a one-time warning);
- `runtime-state.json` android app config;
- app config extracted from `Requirements.md` / `Plan.md` / `Delivery.md`;
- rule-based hints from requirements.

This is useful, but it is still a rule-based V1 generator. It should evolve toward the unified testIntent process above.

## iOS current state

iOS currently has a stronger explicit intent shape but less automatic generation:

```text
.automind/tasks/<task>/probe-flow.ios.json
  -> scripts/ios_probe_flow_runner.py
  -> dry-run intent validation
  -> scripts/ios_probe_flow_materialize.py
  -> Swift XCUITest draft
  -> XCUITest execution path
```

This is better for reviewability because it carries:

- `testIntent.goal`
- `testIntent.sources`
- `authorization`
- `acceptanceCriteria`
- `steps`
- `postChecks`

Android should move toward this explicit intent shape. iOS should gain a more automatic generation path when enough task context exists.

## Confidence-aware action contract

Use confidence-aware fields when they add real value, not mechanically.

Recommended minimal shape:

```json
{
  "type": "tap",
  "name": "tap target element",
  "intentMode": "verify",
  "selector": {"predicate": "label CONTAINS '<target>'"},
  "confidence": {
    "required": 0.8,
    "actual": 0.86,
    "source": "nearby_text"
  },
  "risk": {
    "level": "medium",
    "reversibility": "recoverable",
    "requiresApproval": false
  },
  "guards": {
    "preconditions": ["target_page_ready", "no_modal_blocker"],
    "fallback": {
      "onLowConfidence": "capture_more_evidence",
      "onAmbiguousMatch": "ask_user",
      "onMissingElement": "scroll_and_retry"
    }
  },
  "postChecks": [
    {"type": "ocr_contains_any", "values": ["<expected>"], "strength": "strong"}
  ]
}
```

Use it for:

- cover images;
- visual cards;
- icon-only controls;
- volatile recommendation content;
- repeated/ambiguous text;
- optional popups;
- actions with non-trivial risk.

Do not use it mechanically for every stable low-risk selector.

## UI action capability contract

CodeAutonomy probe-flow is the mechanism that lets coding agents operate real apps
for verification. It is not limited to screenshots or prose evaluation.

Supported reviewable actions include:

- setup: install, launch, wait, optional blocker handling;
- interaction: `tap`, `tap_if_present`, `input`, `scroll`/`swipe`, navigation or
  key events where supported;
- assertions: text/selector/app-hierarchy existence, accessibility state, logs,
  screenshots, UI hierarchy, and post-action checks.

Action quality rules:

1. Every action must map to `Requirements.md` / `TestCases.md` intent.
2. Prefer stable selectors: accessibility id, text, label, predicate, resource id,
   or UI hierarchy path.
3. Use top-level `uiUnblock` for common safe overlays and `tap_if_present` for
   known optional non-destructive popups/coach marks; do not make optional
   blockers required task failures. Treat built-in labels as a generic fallback,
   not as product logic. When a project has its own safe dismiss wording, add
   task-local `uiUnblock.rules[]` from source/runtime evidence instead of
   hard-coding it in runners. Login, permission grants, privacy/terms
   agree/allow, payment, delete/reset/uninstall, external upload,
   signing/device trust, or ambiguous consent require explicit authorization or
   `ask_user`.
4. Use coordinate taps only as a documented fallback with screenshot/bounds
   evidence and a post-action assertion.
5. Sensitive/destructive actions such as reject/deny, payment, account/login
   grants, deletion/reset/uninstall, purchases, credentials, external upload, or
   ambiguous/irreversible consent require explicit authorization scope or
   `ask_user`.
6. If selectors are unknown, add a discovery step: capture screenshot and UI
   hierarchy/accessibility, infer candidates, dry-run/validate the flow, then
   execute. Discovery may navigate toward the goal when safe: if the current
   screen lacks the target control, prefer goal-relevant candidates such as
   cards, covers, detail entries, tabs, or list items and verify the page changed
   before continuing. Record what the attempt ruled out so the next round can
   shrink the remaining search space instead of repeating the same click.
7. A successful click is not enough. Each flow needs postChecks/assertions that
   prove the intended app state or side effect happened. Startup/discovery flows
   (launch/current app/hierarchy/screenshot) are path-finding evidence only; they
   cannot pass a required functional/runtime TC. Strong postChecks are the TC's
   final proof contract: if expected signals are not observed and recorded, the
   flow result is partial/fail and must be refined or rerun.

Per-TC screenshot default: each runtime/UI testcase referenced by a probe-flow is
normalized to capture at least one screenshot by default (one representative step
per TC group is marked `screenshotAfter: true` with `screenshotReason: "per_tc_default"`)
so `Report.html` can show a per-TC screenshot. This is best-effort and not a hard
gate — a TC that already marks a screenshot is left untouched, untagged steps are
ignored, and no warning is raised when a screenshot ends up missing.

Android executes task-local `probe-flow.android.json` with `android-probe-flow` and
`adbutils`/`uiautomator2`. iOS executes through XCUITest: `ios-probe-flow`
validates intent, `ios-probe-flow-materialize`/`ios-action-plan` generates
reviewable Swift, and `ios-xcuitest` runs it when a runner/test target is
available.

## Generation rules

A probe-flow generator should:

- preserve explicit user/TestCases intent, including non-functional quality checks when requested or obviously relevant;
- prefer existing stable selectors over visual/coordinate fallbacks;
- include optional blockers as `tap_if_present` rather than hard failures;
- classify high-risk or destructive actions as requiring approval;
- include postChecks for runtime state/side effects, not only action success; convert strong postChecks into observable signals whenever practical;
- keep task-local paths and secrets out of reusable examples;
- avoid changing validation target without updating requirements/test cases.

## Example files

Current examples:

```text
examples/probe-flows/android-basic.json
examples/probe-flows/ios-intent-basic.json
```

They are intentionally small. They show structure, not a universal flow.

Last updated: 2026-05-08

## `workflow.json` as the cross-platform bridge

New tasks should treat `workflow.json` as the bridge between human TestCases and
platform execution artifacts:

```text
TestCases.md
  -> workflow.json testIntent / executable steps
  -> platform adapter materialization
  -> runner evidence / evaluation.json
```

This keeps iOS and Android aligned in logic without forcing them to share one
low-level runner. The common layer describes intent and evidence:

- prepare / preflight;
- build/install/deploy/start when needed;
- launch/open the target app entry;
- perform reviewable actions such as tap, tap_if_present, input, scroll/swipe,
  navigation, API/background trigger;
- assert text/selector/state/log/event/output;
- capture evidence such as screenshot, UI hierarchy/accessibility, device/app
  logs, test reports, `.xcresult`, or probe-flow summary.

Adapters then choose the native implementation:

- Android: `workflow.json` -> `probe-flow.android.json` ->
  `android-probe-flow` / adb / uiautomator / logcat.
- iOS: `workflow.json` -> `probe-flow.ios.json` or `action-plan.ios.json` ->
  `ios-probe-flow`, `ios-probe-flow-materialize`, XCUITest, or project-native
  `xcodebuild test`.

`workflow.json` also carries runtime skip policy. If a mobile/client task sets
`runtimeProofRequired=true`, a runtime/device testcase cannot be silently marked
optional and ignored; an approved `runtimeDowngradeApproval` (`approvedBy` + `approvedAt` + `reason`) is required before
completion can pass without runtime/device evidence.
