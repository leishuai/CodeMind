# Test Design Guide

This reference supports `docs/phase2-requirement.md`. The Phase 2 document is
the hard process; this guide gives concrete artifact shapes and examples so the
Planner can produce testable work instead of generic checklists.

## 1. Design goal

CodeMind test design converts a user request into evidence-producing checks:

```text
User intent -> Rxx requirements -> AC-xxx acceptance criteria -> TC-* runbooks -> Plan checklist
```

A testcase is useful only when an Evaluator can execute or audit it. Avoid vague
rows such as "verify UI works" or "check implementation". Required functional
cases must specify what to prepare, what to run, what to assert, and which
evidence proves the result.

## 2. Artifact templates

### `Requirements.md`

New tasks use `Requirements.md` as the canonical requirement contract: stable
`Rxx` requirement units plus inline `AC-xxx` acceptance criteria.

```markdown
# Requirements

## Goal
<one paragraph>

## Scope
- In scope: ...
- Out of scope: ...

## Requirements with inline Acceptance Criteria

### R01 — <requirement title>
- Behavior: ...
- **AC-001**: ...
  - Verification method: runtime command / UI flow / unit test
  - Covered by: TC-F01
  - Required: yes

## Assumptions / Risks
- ...
```

New tasks use `Requirements.md` only. planning should not duplicate the same content across all three files.

### `TestCases.md`

Use this shape for required functional cases:

```markdown
# Test Cases

## Functional cases
| ID | Requirement/AC | Runtime level | Preconditions / tools | Command / CodeMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |
|----|----------------|---------------|-----------------------|----------------------------|-----------------------------|--------------------------|------------|-----------|
| TC-F01 | R01 / AC-001 | runtime | ... | ... | ... | ... | - | yes |

## Quality cases
| ID | Category | Check | Evidence | Required? |
|----|----------|-------|----------|-----------|
| TC-Q01 | regression | ... | ... | no |
```

### `Plan.md`

```markdown
# Plan

## First functional batch
- TC-F01

## Verification command
- `<command>`

## Implementation Checklist
| ID | Source | Status | Owner | Evidence | Notes |
|----|--------|--------|-------|----------|-------|
| T01 | R01 / AC-001 / TC-F01 | todo | generator | - | ... |

## Verification Checklist
| ID | Required | Status | Owner | Evidence | Notes |
|----|----------|--------|-------|----------|-------|
| TC-F01 | yes | todo | evaluator | - | ... |
```

## 3. Required testcase qualities

Every required functional testcase must contain these parts:

1. **Preparation / preflight**: cwd, tools, fixtures, device/runtime state, data,
   credentials or explicit blocker.
2. **Execution**: command, CodeMind helper, UI action sequence, API call, or
   project-native test.
3. **Assertion**: expected output, state, log, UI hierarchy, screenshot/bounds,
   database/file/network/mock call, or other measurable result.
4. **Evidence path**: logs, reports, screenshots, JSON, test output, or explicit
   user confirmation when automation cannot prove a visual/semantic claim.
5. **Dependency**: whether the case depends on another case passing.

Static inspection alone is not enough for a required functional case unless the
case explicitly states that dynamic execution is impossible/unsafe and records
the blocker or required human confirmation.

## 4. App / UI runbooks

For client-facing work, required functional cases must state:

- preparation/preflight: build tools, device/simulator/browser/server state;
- build/install/deploy/start command;
- launch/open target: exact app, screen, route, page, activity, view, or state;
- action sequence: tap/click/input/scroll/navigation/API trigger;
- assertions: visible text, state, log, event, API/data result, UI hierarchy,
  bounds/geometry, screenshot diff, OCR, or human confirmation;
- evidence: test report, log, screenshot, hierarchy JSON/XML, UI `action-trace.jsonl`, trace, or confirmation.

Mobile App/UI automation capability is first-class in CodeMind:

- Android: prefer `android-preflight` -> `android-probe-flow`; generated
  `probe-flow.android.json` can launch/install, close optional blockers with
  `tap_if_present`, tap/click, input, swipe, assert text/selector/app hierarchy,
  and collect screenshot/UI hierarchy/logcat evidence.
- iOS: prefer XCUITest when a test target/runner exists. When task intent is
  known but code is not yet in a test target, write `probe-flow.ios.json` and use
  `ios-probe-flow` / `ios-probe-flow-materialize` / `ios-action-plan` to validate
  and materialize tap/input/scroll/assert steps into reviewable Swift XCUITest.
- Optional popups and coach marks should be explicit `tap_if_present` setup steps
  or top-level `uiUnblock` when they are safe dismiss/close actions. Permission
  prompts, privacy/terms Agree/Allow/Continue, destructive/account/payment/
  reject-or-deny/ambiguous consent actions require explicit authorization or
  `ask_user`.
- If selectors are unknown, do not give up or static-pass. Add a discovery pass:
  launch the app, capture screenshot + UI hierarchy/accessibility, infer stable
  selectors, then refine the probe-flow. Use coordinate taps only as a documented
  fallback with screenshot/bounds justification and post-action assertions.
- Fallback ordering must reference `verification-flow.md`: platform UI runner
  first; minimal reversible project edit before switching modality; then
  direct-route/page-load/deep-link only after UI automation is exhausted; then
  approved simulator/emulator automation when allowed; then human-assisted
  evidence capture; finally explicit reduced-scope downgrade. Do not design a
  required runtime testcase whose default verifier is `ask_user`, screenshot
  confirmation, dry-run, static-only proof, or manual action without
  machine-checkable postconditions.

Example:

```markdown
| TC-F01 | R01 / AC-001 | device runtime | iOS simulator booted; app buildable | xcodebuild test ... | Install app -> open Login screen -> enter invalid password -> tap Login | XCUITest report shows error label visible; screenshot and logs saved under logs/iter-1/ | - | yes |
```

## 5. TestCase -> verifier operation mapping

When CodeMind enters Verify, the Evaluator converts each required `TC-*` row (or
`testcases.json.testcases[]` entry) into one concrete verifier operation. This
conversion should be mechanical enough for Skill mode to keep looping without
asking the user for every step.

### Normalized input

For each testcase, prefer structured fields from `testcases.json` and use
`TestCases.md` for readable detail:

- `id`, `required`, `runtimeLevel`, `executor`;
- `requirementRefs` / `acceptanceCriteriaRefs`;
- `runbook.preconditions` / preparation;
- `runbook.command` or CodeMind command;
- `runbook.steps` / action sequence;
- `runbook.assertions`;
- `runbook.expectedEvidence`;
- `dependency` and `skipPolicy`.

If the JSON sidecar and Markdown disagree, run `workflow-check` or replan before
verification; do not silently pick the easier version.

### Executor selection priority

Choose the most concrete executor that matches the testcase intent:

1. **Project-native command** — use when the TC names an existing command, package
   script, test target, CI command, Fastlane lane, Gradle task, Make target, or
   documented runbook. Record stdout/stderr/test report under `logs/iter-N/`.
2. **`script-command`** — use only when the task explicitly declares
   `scriptCommand` / `verifyCommand`; it wraps a known project-local script or
   shell runbook, not a fallback for missing platform verification.
3. **Android app/UI** — use `android-preflight` then generate/refine
   `probe-flow.android.json` from the TC action sequence; run `android-probe-flow`
   and collect screenshot, UI hierarchy/uiautomator, logcat, and summary JSON.
4. **iOS app/UI** — prefer project XCUITest when available; otherwise generate
   `probe-flow.ios.json` / `action-plan.ios.json`, validate/materialize it, and
   run through `ios-xcuitest` or the project/native runner.
5. **Browser/web UI** — prefer project-native E2E (Playwright/Cypress/etc.) or a
   documented script. Evidence should include report/trace/screenshot/logs.
6. **External sink / side effect** — use a project-native test, mocked sink, debug
   log, captured request, local debug file, or backend receipt depending on what
   the AC requires.
7. **Static/quality review** — use only for quality or static cases, or when a
   required functional TC explicitly records why runtime execution is impossible
   or unsafe.

### UI action mapping

Map testcase runbook language to probe/XCUITest/browser actions. For page/state transitions, prefer an `assert_page` signature with `required` / `anyOf` / `forbidden` conditions rather than a vague screenshot-only check:

| TestCase runbook phrase | Verifier action | Required post-check | Evidence |
|---|---|---|---|
| build/package app | build command / native test setup | build succeeded | build log/report |
| install/deploy/start | install/start/server command | process/app reachable | logs/device output |
| launch/open route/screen | launch/open URL/activity/view | target state visible | screenshot + hierarchy/log |
| tap/click button | `tap`/click action with selector | expected UI/state/log change | action trace + after screenshot/hierarchy |
| input text | `input`/fill action | value accepted or result visible | action log + screenshot/hierarchy |
| handle optional popup | `tap_if_present` guarded action | no destructive/unauthorized action | guarded action log |
| assert visible text/state | assertion/postCheck | assertion passed | test/probe summary |
| trigger external event | project test/API/UI path | sink called with expected payload | mock/log/request/backend evidence |

If selectors are missing, the first verifier operation should be discovery:
launch/open, capture screenshot + UI hierarchy/accessibility tree, infer stable
selectors, refine the probe/test, then rerun. A missing selector is not a pass;
it should become an explicit attempt record. The Evaluator should use exclusion:
record the hypothesis, attempted action, expected signal, observed outcome, what
was ruled out, and remaining candidate paths. For UI navigation, examples of
valid intermediate progress include entering a detail page from a cover/card,
discovering a playback/control selector, or narrowing to a different tab/list
path. Use `replan`, `retry_generator` for test harness repair, or `ask_user` only
if a human/system decision is genuinely needed.

### Output normalization

Every verifier operation must normalize its result into `evaluation.json`:

- one `testResults[]` entry per executed/blocked/skipped `TC-*`;
- `result`: `pass`, `fail`, `partial`, `blocked`, `skipped_dependency`, or `not_run`;
- optional progress fields for attempted UI/runtime cases: `progressKind`
  (`navigation`, `control_discovery`, `proof`, `evidence`, `blocked`, or
  `unknown`), `hypothesis`, `actionTried`, `expectedSignal`, `outcome`,
  `ruledOut`, and `remainingHypotheses`;
- evidence paths that exist;
- `evidenceAssessment.verdict` for model/evaluator judgment;
- `hardMetrics[]` or independent secondary assessment for proved passes;
- `nextAction`: `finish`, `retry_generator`, `replan`, `ask_user`, or `stop`.

`completion-check` is the final arbiter: a verifier operation can propose
`finish`, but required `TC-*` / `AC-*` / evidence coverage must still pass the
completion gate.

## 6. Visual assertions

Prefer measurable evidence first:

- UI hierarchy text/existence;
- bounds/frame/coordinate/size checks;
- screenshot diff or baseline comparison;
- OCR for text-in-image;
- `visual-inspect` for deterministic image checks when available.

AI Visual Review is supplementary. Use it for semantic visual claims, ambiguous
screenshots, or UX issues that pure measurements cannot settle. If deterministic
evidence and AI Visual Review still cannot prove the claim, ask the user to
confirm the exact screenshot claim. A screenshot path alone is not a pass.

## 7. External sink / side-effect cases

For analytics, telemetry, logging, notifications, database writes, message
queues, or network side effects, verify the layer that changed.

If the implementation only adds a call into an existing sink, acceptable evidence
may be local runtime proof that the trigger path ran and the expected sink method
received the correct key/payload. Server receipt, proxy capture, or backend logs
are high-confidence evidence but are not mandatory unless the requirement or
transport/schema changed.

Example evidence:

- project-native test with mocked sink;
- runtime log showing event key and payload;
- spy/assertion on sink method;
- local debug file or captured request;
- backend receipt if required by AC.

## 8. Functional batches and quality checks

Plan the first functional batch before quality checks. The first batch should
prove the core behavior or unblock the most important dependency. Quality checks
run after functional evidence exists.

Dependency policy:

- If a required prerequisite case fails, dependent cases may be marked
  `skipped_dependency` / `not_run` with evidence.
- Independent cases should continue when safe so Evaluator collects more signal.
- After quality-driven runtime/product changes, rerun the affected functional
  batch before claiming finish.

## 9. Good and bad examples

Bad:

```markdown
| TC-F01 | Verify login works | manual | Check page | Looks good | yes |
```

Good:

```markdown
| TC-F01 | R01 / AC-001 | browser runtime | dev server started; test user exists | npm run test:e2e -- login.spec.ts | Open /login -> enter valid credentials -> submit | Playwright report passes; screenshot shows dashboard; trace saved | - | yes |
```

Bad:

```markdown
| TC-F02 | Verify analytics | static | inspect code | event added | yes |
```

Good:

```markdown
| TC-F02 | R02 / AC-002 | runtime unit/integration | analytics sink can be mocked | npm test -- analytics-stop-reason.test.ts | Trigger stop reason path with mocked sink | Test asserts sink called once with event key and reason payload; junit/log evidence saved | - | yes |
```

## 9. Preflight hints

Use `docs/references/dependency-check.md` and `docs/references/verification-flow.md`
for platform-specific checks. Low-risk helper venv setup may use CodeMind helper
commands when the full runtime is installed, but system SDKs, signing material,
keychains, trust settings, and privileged services require explicit user action.


## Temporary verification logging

For iOS/Android/Web/Server testcases, prefer assertions and platform evidence over code instrumentation. If a testcase cannot be proven from available evidence, CodeMind may add temporary logs with `[CodeMind][Verify]` to expose a hidden state transition, callback, request, queue/event, or async completion. Keep these logs scoped to the testcase and remove them after verification unless the project owner decides they are useful production diagnostics.
