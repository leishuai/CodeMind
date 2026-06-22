---
name: client-ui-repair
description: "Cross-client UI automation repair playbook for Android/iOS flows, blocker classification, and test-intent-grounded action generation."
use_when:
  - "a client UI assertion or tap/input/scroll flow fails"
  - "a popup, login wall, permission prompt, keyboard, loading state, or overlay blocks the flow"
  - "generating or repairing probe-flow/action-plan steps"
solves:
  - "prevents blind retry tapping"
  - "keeps concrete product flows grounded in requirements/testcases"
  - "maps generic UI repair heuristics to Android and iOS automation"
---
# Client UI Repair / Test Intent Summary

This is the common client-side rule for Android, iOS, and future mobile/client adapters.

## Core principle

Generic repair heuristics are reusable; concrete probe-flow / action-plan steps must be generated from test intent.

Operation order:

Pre-step: honor pre-implementation modality approval. If simulator/emulator
coverage was already allowed, start with that automation branch directly.

1. Confirm app/device/readiness and capture current UI evidence.
2. Repair selectors, waits, keyboard/loading state, and safe overlays inside the
   platform runner when the action is automatable.
3. Use minimal reversible project edits when they preserve automation and no-edit
   runners cannot execute.
4. Use direct-route/page-load/deep-link only after the normal UI path is genuinely
   blocked.
5. Use simulator/emulator automation only when allowed for the testcase.
6. Use human-assisted evidence capture only after automated fallbacks or for
   sensitive/system/account actions that automation must not perform.
7. Ask for dry-run/static/build-only downgrade only after all evidence paths fail.
8. Never pass from a manual action or screenshot path alone; require
   machine-checkable postcondition evidence.

In other words:

- UI primitives are generic: `tap`, `tap_if_present`, `input`, `scroll`, `assert`, `wait`.
- UI diagnostics are generic: blocked by popup, permission alert, login wall, keyboard, loading, system overlay, or stale page state.
- Concrete product flows are not generic: named navigation targets, content playback, account flows, purchase, profile edit, etc. must come from test intent.

## Test intent sources

Concrete flow steps must be grounded in at least one of:

- user request
- `Require.md`
- `TestCases.md`
- private project/domain summary
- explicit acceptance criteria
- existing product-specific test plan

A runner must not invent product goals just because it has tap/scroll primitives.

## Generic repair heuristic

When an expected view is missing or not interactable:

1. Confirm app/device readiness:
   - app installed
   - app launched / foreground / process alive
   - device unlocked / screen active
2. Capture evidence if possible:
   - screenshot
   - UI hierarchy / accessibility tree
   - XCUITest failure output
   - logcat / device log / devicectl output
3. Classify likely blocker:
   - privacy/terms popup
   - system permission alert
   - login/account wall
   - upgrade/activity/modal dialog
   - keyboard covering target
   - loading/skeleton/spinner
   - system overlay / notification shade / lock screen
4. If blocker is optional, low-risk, and known, add `tap_if_present` /
   `uiUnblock` before the original step and record evidence.
5. If blocker needs human/account/permission/system authorization, classify as one of:
   - `permission_blocked`
   - `account_state_blocked`
   - `business_state_blocked` / `project_state_blocked`
   - `device_state_blocked`
6. Retry only after the blocker is resolved, safely auto-unblocked, or explicitly skipped.

## Android mapping

Android probe-flow may materialize the heuristic as:

- `tap_if_present`
- `assert_app_hierarchy`
- wait/retry
- selector fallback: resource-id/content-desc/text
- SystemUI overlay classification

## iOS mapping

iOS action-plan / XCUITest may materialize the heuristic as:

- `tap_if_present`
- `waitForExistence`
- accessibility id / label / NSPredicate selectors
- screenshot + XCUITest failure output
- permission/login/privacy blocker classification

## Anti-patterns

Do not:

- auto-generate a product journey without test intent
- keep retrying a tap without checking blockers
- treat permission/login/device overlays as product code failures
- use coordinate taps as the default when accessibility selectors exist
- silently dismiss destructive/security-sensitive dialogs
- ask the user to perform ordinary app actions while a platform runner can do so
- treat human-assisted action or visual confirmation as runtime proof without
  machine-checkable postcondition evidence

Last updated: 2026-06-13

## Reusable user authorization for common dialogs

If the user explicitly authorizes a class of common test-flow dialogs, the authorization can become a reusable testing convention.

Example:

- privacy / terms consent
- allow-style permission prompts that are necessary for the test path

Rules:

- Record the authorization and its scope.
- Apply it only to similar non-destructive test-flow dialogs.
- Still record evidence when the dialog is handled.
- Do not extend the authorization to destructive, payment, credential, privacy-export, or account-changing actions.
- Do not pretend the action was executed unless a stable backend actually performed it.

For any product, user authorization to handle privacy/terms prompts must be scoped to the current test flow, and physical execution still requires a stable backend such as XCUITest, WDA, or project UI test integration.
