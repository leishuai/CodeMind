---
name: ios-ui-actions
description: "iOS UI action automation playbook for preferring XCUITest/accessibility-driven action plans over coordinate tapping and materializing probe-flow intent into evidence."
use_when:
  - "building or reviewing iOS probe-flow/action-plan steps"
  - "choosing selectors and assertions for physical-device UI automation"
  - "deciding whether coordinates are an acceptable fallback"
solves:
  - "standardizes tap/input/scroll/assert/wait actions"
  - "prioritizes accessibility identifiers, labels, and predicates"
  - "keeps coordinate actions as reviewed fallbacks only"
---
# iOS UI Actions Summary

## Decision rules (read first)

For iOS physical UI actions, AutoMind should prefer **XCUITest/accessibility**
over raw coordinate tapping.

- Overall posture: automation above all. If the pre-implementation one-shot
  Decision Bundle already approved simulator coverage, start directly with
  simulator automation. If real-device verification is approved, exhaust
  real-device UI automation, then reversible project edits to preserve
  real-device automation, then real-device direct-route/URL-scheme entry, then
  approved simulator automation, then human-assisted capture, and only then
  dry-run/static/build-only downgrade.
- Use reviewable `probe-flow.ios.json` / `action-plan.ios.json` steps for
  `tap`, `tap_if_present`, `input`, `scroll`, `assert_exists`, `assert_text`, and
  `wait`.
- Prefer accessibility selectors over visual/coordinate guesses. Selector order:
  `accessibilityId` / accessibility identifier -> semantic text or button label
  -> NSPredicate over accessibility attributes -> coordinates.
- Keep coordinates as a reviewed fallback only. A coordinate tap must explain why
  accessibility/text/predicate failed and what screenshot/accessibility evidence
  bounds the target.
- Real physical tap/scroll execution should go through XCUITest/project test
  target/external runner integration until a stable direct physical-device tap
  backend is validated.
- Direct `pymobiledevice3.services.accessibilityaudit.AccessibilityAudit`
  probing is a useful **exploration fallback**, not the primary proof backend:
  use it to inspect the live accessibility tree, discover candidate labels, and
  try low-risk `perform_press` actions when XCUITest/probe-flow selectors are
  missing, a project has no usable UI test target yet, or the task needs quick
  path discovery on a real device. Convert any stable discovery into
  `probe-flow.ios.json` / `action-plan.ios.json` / XCUITest selectors before
  claiming required runtime proof.
- AccessibilityAudit probing depends on the app's exposed accessibility data.
  UIKit controls may expose usable labels by default, but custom controls often
  need stable `accessibilityIdentifier` / label / `isAccessibilityElement` to be
  reliable. If the tree shows only generic `Button`/ambiguous text, classify the
  path as selector/action-target risk and do not keep retrying unchanged.
- Keep AccessibilityAudit actions bounded and reversible. It may tap visible UI
  but must not perform consent/login/payment/delete/external-upload/account
  actions without the same explicit authorization required for XCUITest or
  probe-flow.
- When AccessibilityAudit is used to drive an iOS UI path, collect a screenshot
  before/after key page transitions when a screenshot backend is available, or
  record an explicit no-screenshot reason and attach accessibility tree/log
  evidence. The Report is more trustworthy when each UI/runtime TC has a visual
  artifact in addition to logs or payload proof.
- For project-native UI test targets, do not treat repeated Xcode/CoreDevice
  connection drops or automation-session startup failures as product bugs when
  the iPhone is otherwise connected and **Enable UI Automation** is already on.
  Classify as device/host link recovery and ask the user for one physical
  recovery step: keep the phone unlocked/lit, replug USB, accept any trust
  prompt, and toggle **Settings -> Developer -> Enable UI Automation** off then
  on before retrying the same UI test command.
- Dry-run validates action intent and materializes Swift XCUITest drafts; it does
  not prove runtime behavior and must not satisfy runtime proof by itself.

## Supported action layer

Reliable iOS action-plan/probe-flow step types:

- `tap`
- `tap_if_present`
- `input`
- `scroll`
- `assert_exists`
- `assert_text`
- `wait`

Selectors should be chosen in this order:

1. `accessibilityId` / accessibility identifier
2. semantic text / button label
3. NSPredicate over accessibility attributes
4. coordinates only as a reviewed fallback

## Entry points and evidence

AutoMind has two complementary iOS action-intent entry points:

```bash
./automind.sh ios-action-plan <task-code> <action-plan.ios.json> [--iteration N]
./automind.sh ios-probe-flow <task-code> [iteration] [--flow probe-flow.ios.json] [--dry-run]
```

`ios-action-plan` validates a standalone declarative plan and generates a Swift XCUITest draft:

```text
logs/iter-N/GeneratedActionPlanTests.swift
```

`ios-probe-flow` now validates richer task action intent inside `probe-flow.ios.json`:

- `testIntent.goal`
- `testIntent.sources`
- `testIntent.acceptanceCriteria`
- `testIntent.authorization.scopes`
- `testIntent.steps[]`
- `testIntent.postChecks[]`

Dry-run writes:

```text
logs/iter-N/ios-action-intent-summary.json
logs/iter-N/action-plan.materialized.ios.json
logs/iter-N/ios-probe-flow-summary.json
```

Generated Swift drafts attach screenshots after key UI actions with
`XCTAttachment(screenshot:)` and `.keepAlways`, so `.xcresult` can carry visual
evidence for action steps. Coordinate fallback is still not the default.

## Example

```json
{
  "name": "Example smoke action plan draft",
  "bundleId": "com.example.app",
  "steps": [
    {"type": "wait", "seconds": 3},
    {
      "type": "tap_if_present",
      "name": "optional privacy accept button",
      "selector": {"predicate": "label CONTAINS 'Agree' OR label CONTAINS 'Accept' OR label CONTAINS 'Continue'"},
      "timeout": 2
    },
    {"type": "scroll", "direction": "up", "count": 1},
    {"type": "assert_exists", "selector": {"predicate": "label.length > 0"}, "timeout": 5}
  ]
}
```

## Example result shape

A dry-run should validate the declared action intent, materialize a Swift XCUITest draft, and keep coordinate actions as reviewed fallbacks only.

## Backend status and coordinate fallback

For physical iOS devices on this machine:

- XcodeBuildMCP `ui-automation tap/snapshot-ui` currently requires
  `--simulator-id`, so it is simulator-oriented.
- `pymobiledevice3 developer dvt xcuitest` requires an XCTest runner bundle id;
  it is not a direct tap API.
- `pymobiledevice3 developer dvt` exposes screenshot / xcuitest / logs-style
  developer services, but not a clearly stable direct tap/swipe API.
- `pymobiledevice3.services.accessibilityaudit.AccessibilityAudit` can inspect
  the live accessibility tree and perform presses on exposed elements on some
  real-device setups. Treat this as an exploratory direct backend for selector
  discovery and low-risk fallback taps; do not treat raw probing as equivalent
  to XCUITest `.xcresult` evidence.
- `idb` is not available on this machine.
- Therefore, real physical tap/scroll execution should go through
  XCUITest/project test target/external runner integration for required proof,
  using AccessibilityAudit only as bounded discovery/fallback unless and until a
  task-specific direct backend is selected, validated, and documented.

## Client-common repair rule

iOS-specific mapping: represent safe repairs as XCUITest/action-plan steps such as `tap_if_present`, `waitForExistence`, accessibility-id/label/predicate selectors, and screenshot/XCUITest failure evidence.

This iOS-specific document follows the cross-client rule in:

```text
summaries/preloaded/client-ui-repair.md
```

The common rule applies to Android and iOS: generic repair heuristics are
reusable, but concrete flow steps must come from test intent.

Last updated: 2026-06-14
