# Phase 2B — Verification & Execution Planning

## Goal

Derive technical proof and implementation order from an already-understood demand contract.

Verification & Execution Planning owns:

```text
TestCases.md -> AC -> executable/inspectable proof design
Plan.md      -> implementation order, verification order, fallback, handoff
```

It answers: how will we prove the requirements, what evidence is required, what should Generator change first, and how will Evaluator decide pass/fail?

## Inputs

- `Brainstorm.md` / `brainstorm.json`
- `Requirements.md` / `requirements.json`
- `Reuse.md` successful/avoid paths when relevant
- Project-native command/runbook discovery
- Existing `TestCases.md`, `Plan.md`, `testcases.json`, `plan.json` when replanning

## TestCases: proof design

TestCases maps Requirements acceptance criteria to concrete verification rows. Each required functional/key-path case must include:

- `TC-*` id;
- mapped Rxx / AC-xxx refs;
- runtime level: `unit`, `integration`, `runtime`, `device`, `static`, or `manual`;
- preconditions/tools/fixtures;
- command, CodeAutonomy command, or action sequence;
- assertions and expected result;
- concrete evidence path/type;
- dependency and required flag;
- fallback/blocker behavior when the tool/device/environment is missing.

Prefer dynamic/runtime evidence whenever a runnable path exists. Static inspection can supplement, but must not be the only required evidence for code or behavior changes unless dynamic execution is impossible/unsafe and the testcase is marked blocked/manual/ask_user with the reason.

For App/UI/client-facing work, TestCases must explicitly settle:

- build/package;
- install/deploy/start server;
- launch/open;
- UI flow;
- entry target/page/screen/route/activity/state;
- action sequence;
- assertions;
- evidence;
- tool/command.

Every UI action needs a post-action assertion. Do not claim CodeAutonomy cannot operate an app just because the flow needs taps/clicks/input/navigation; use Android probe-flow, iOS XCUITest/probe-flow/action-plan, browser automation, project-native tests, logs, screenshots, and UI hierarchy where applicable.

For layout/visual requirements, prefer measurable evidence first: frame/bounds/bounding box, screenshot diff with tolerance, snapshot/golden comparison, DOMRect/Playwright bounding box, XCUITest accessibility frame, Android UI hierarchy bounds, OCR, or project-native layout assertion. AI Visual Review is supplementary semantic review or fallback when deterministic proof cannot settle the visual claim.

## Plan: execution design

Plan consumes Requirements and TestCases. It should not rediscover demand. If Plan reveals missing AC/TC coverage, return to the owning artifact instead of hiding assumptions in Plan.

Plan must include:

- implementation approach and likely files/modules when known;
- project script/runbook discovery result for command choice;
- selected/ignored `Reuse.md` successful/avoid paths with reasons;
- first functional batch (`TC-F*` ids);
- implementation order;
- verification order;
- preflight and environment needs;
- quality checks and skipped categories with reasons;
- fallback/rollback strategy;
- verification unblock policy for build/test/device blockers;
- risks;
- `Implementation Checklist` with `T*` rows;
- `Verification Checklist` covering every declared `TC-*`.

## Gate

Verification & Execution Planning is ready when:

- every required AC is covered by at least one required or intentionally blocked TC;
- required functional/key-path TCs have concrete command/action/assertion/evidence;
- app/client/runtime tasks include runtime proof decisions;
- Plan references concrete TC ids and first functional batch;
- checklists are complete enough for Generator and Evaluator handoff;
- pre-implementation review can make a final `auto_proceed`, `ask_user`, or `replan` decision.

## Exit

Proceed to pre-implementation review and `workflow-check`. Build/Generator may start only after the review gate is resolved and `workflow-check` passes.
