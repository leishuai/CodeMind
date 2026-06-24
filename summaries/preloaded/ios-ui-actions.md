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

For iOS physical UI actions, prefer **XCUITest/accessibility** over raw
coordinate tapping.

- **Automation above all, in this fallback order:** if the pre-implementation
  Decision Bundle approved simulator coverage, start there directly; if
  real-device is approved, exhaust real-device UI automation -> reversible
  project edits that preserve automation -> direct-route/URL-scheme entry ->
  approved simulator automation -> human-assisted capture -> only then
  dry-run/static/build-only downgrade.
- **Action steps** (`probe-flow.ios.json` / `action-plan.ios.json`): `tap`,
  `tap_if_present`, `input`, `scroll`, `assert_exists`, `assert_text`, `wait`.
- **Selector order:** `accessibilityId` -> semantic text / button label ->
  NSPredicate over accessibility attributes -> coordinates. A coordinate tap must
  explain why accessibility/text/predicate failed and what screenshot/tree
  evidence bounds the target.
- Real tap/scroll execution should go through XCUITest / project test target /
  external runner until a stable direct physical-tap backend is validated.
- **AccessibilityAudit** (`pymobiledevice3.services.accessibilityaudit`) is an
  exploration fallback, not the primary proof backend: use it to inspect the live
  tree, discover candidate labels, and try low-risk `perform_press` when
  XCUITest/probe-flow selectors are missing or the project has no UI test target
  yet. Convert any stable discovery into probe-flow/XCUITest selectors before
  claiming runtime proof. It depends on the app's exposed accessibility data —
  if the tree shows only generic `Button`/ambiguous text, classify as
  selector/action-target risk and stop retrying unchanged. Keep its actions
  bounded/reversible; consent/login/payment/delete/upload/account actions need
  the same explicit authorization as XCUITest.
- **Visual evidence:** capture a screenshot before/after key page transitions
  when a backend is available, or record an explicit no-screenshot reason plus
  accessibility-tree/log evidence.
- **Device/host link drops** (Xcode/CoreDevice connection drops, automation
  session startup failures) on an otherwise-connected iPhone with **Enable UI
  Automation** on are NOT product bugs. Classify as device/host link recovery and
  ask for one physical recovery step: keep the phone unlocked/lit, replug USB,
  accept trust prompts, toggle Settings -> Developer -> Enable UI Automation off
  then on, retry the same command.
- Dry-run validates action intent and materializes Swift XCUITest drafts; it does
  NOT prove runtime behavior by itself.

## Entry points and evidence

```bash
./automind.sh ios-action-plan <task-code> <action-plan.ios.json> [--iteration N]
./automind.sh ios-probe-flow <task-code> [iteration] [--flow probe-flow.ios.json] [--dry-run]
```

- `ios-action-plan` validates a standalone declarative plan and generates a Swift
  XCUITest draft (`logs/iter-N/GeneratedActionPlanTests.swift`).
- `ios-probe-flow` validates richer task action intent inside
  `probe-flow.ios.json`: `testIntent.goal` / `.sources` / `.acceptanceCriteria` /
  `.authorization.scopes` / `.steps[]` / `.postChecks[]`. Dry-run writes
  `ios-action-intent-summary.json`, `action-plan.materialized.ios.json`,
  `ios-probe-flow-summary.json` under `logs/iter-N/`.

Generated Swift drafts attach screenshots after key UI actions
(`XCTAttachment(screenshot:)` + `.keepAlways`), so `.xcresult` carries visual
evidence. Coordinate fallback is still not the default.

### Example action plan

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

## Backend status (this machine)

- XcodeBuildMCP `ui-automation tap/snapshot-ui` requires `--simulator-id` →
  simulator-oriented.
- `pymobiledevice3 developer dvt xcuitest` requires an XCTest runner bundle id;
  not a direct tap API. `dvt` exposes screenshot/xcuitest/logs services but no
  stable direct tap/swipe API.
- `AccessibilityAudit` can inspect the tree and press exposed elements on some
  setups — exploratory backend only, not equivalent to XCUITest `.xcresult`.
- `idb` not available.
- So required real physical tap/scroll proof should go through XCUITest / project
  test target / external runner, using AccessibilityAudit only as bounded
  discovery/fallback until a task-specific direct backend is validated.

## Client-common repair rule

This follows the cross-client rule in `summaries/preloaded/client-ui-repair.md`:
generic repair heuristics are reusable, but concrete flow steps must come from
test intent. iOS mapping: represent safe repairs as XCUITest/action-plan steps
(`tap_if_present`, `waitForExistence`, accessibility-id/label/predicate
selectors, screenshot/XCUITest failure evidence).

Last updated: 2026-06-23
