# Probe-flow Starter Examples

This folder contains small public-safe JSON examples that show the shape of
AutoMind probe-flow/action-intent files.

They are intentionally not complete app-specific tests. A real task should
generate or refine its own flow from:

- `Require.md` and `TestCases.md`;
- the target app package/bundle/config;
- available device/simulator state;
- discovered UI hierarchy or accessibility selectors;
- task-specific risk and authorization constraints.

## Files

- `android-basic.json` — Android flow shape for install, launch, optional popup
  handling / `uiUnblock`, hierarchy assertion, screenshot, and stop.
- `ios-intent-basic.json` — iOS XCUITest/action-intent shape for wait,
  optional non-destructive dialog handling, tap, assertion, and post-checks.

`uiUnblock` is a safe-overlay precondition helper. It may close/dismiss common
non-destructive blockers, but it does not prove the business testcase by itself.

## How to use

Use these files to understand the contract, not as copy-paste templates. For a
real app, replace placeholder fields, selectors, risk levels, and assertions with
values justified by the task artifacts and evidence.
