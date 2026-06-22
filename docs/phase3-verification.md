# Phase 3: Verification Loop

Phase 3 proves whether the task is done. It evaluates `Delivery.md` and runtime
evidence against `Plan.md` plus the derived `workflow.json` contract, producing
`Validation.md`, `evaluation.json`, and eventually a passing `completion-check`.

## 1. When to run

Run Phase 3 after Phase 2 passes `workflow-check` with a valid derived
`workflow.json` contract and either:

- Generator has implemented or repaired product/runtime code and written
  `Delivery.md`; or
- the task is evaluator-only and `Plan.md` already contains a concrete verifier.

Re-run Phase 3 after every Generator repair, validation target change, or
quality-driven code change.

## 2. Phase 3 state machine

```text
Preflight
  -> Generator implement/repair (if needed)
  -> Delivery.md gate
  -> Evaluator verification
  -> Validation.md + evaluation.json
  -> completion-check
      -> finish
      -> retry_generator -> Generator
      -> replan -> Planner
      -> ask_user -> Human
      -> stop
```

Each transition has a file/gate. Evaluator owns the primary repair route in
`evaluation.json.nextAction`; completion-check owns the final finish gate. Do not
replace these with chat confidence.

## 3. Role boundaries

| Role | Owns | Must not do |
|---|---|---|
| Generator | product/runtime implementation and repair, `Delivery.md`, implementation checklist | claim final verification without Evaluator evidence |
| Evaluator | preflight, verification, evidence, failure classification, `Validation.md`, `evaluation.json`, verification checklist | repair product/runtime code for product failures |
| Completion gate | required TC/AC/evidence coverage, `VerificationLedger.json` | infer pass from prose only |

Evaluator may self-repair verifier/probe/test-harness logic only when the
validation method is wrong. Product failures route to `retry_generator`: this
means Requirements and TestCases still hold, but Generator must repair
implementation, Delivery, or missing evidence.

Evaluator may directly call the selected project/platform tool; no extra proof
middle layer is required. Android, iOS, Web, server/CLI, and `script-command`
adapters use different execution tools but share the same result contract:
required `pass` rows in `evaluation.json.testResults[]` must include evidence
paths, meaningful `observedSignals[]` when available, and a positive
`evidenceAssessment` whose `hardMetrics[].evidence` or `machineAnchor` points at
an existing screenshot/log/trace/report artifact.

### Lightweight evidence index contract

CLI and Skill-mode tasks use the same artifact protocol. Do not add a
Skill-only finalizer or a separate evidence-manifest file. When a runner,
deterministic verifier, or Evaluator already writes an iteration summary/result
JSON, it may include a lightweight `evidenceIndex[]` field to help later steps
map raw artifacts to TestCases:

```json
{
  "evidenceIndex": [
    {
      "path": "logs/iter-4/probe-flow/action-03-after.png",
      "type": "screenshot",
      "tc": "TC-F01",
      "signal": "screen_after_action"
    }
  ]
}
```

Only `path` is required. `type`, `tc`, and `signal` are optional lightweight
hints. `tc` and `signal` may be strings or arrays. Avoid heavy per-artifact
schemas such as mandatory ids, SQL details, line ranges, AC copies, or long
summaries; detailed proof belongs in the referenced artifact itself. Runner and
script outputs should produce `evidenceIndex[]` automatically when they know the
artifacts they created. Coding agents should only add entries for special
project-native evidence that AutoMind cannot infer.

`signal` may use `missing:<name>` for negative evidence, for example a captured
logcat window that did not contain an expected keyword. This records "collected
but not observed" instead of losing the attempt. Raw database dumps are not a
primary verification path; treat database/file/state inspection as optional,
project-specific evidence when it is already easy and safe to collect.

Default evidence paths are: project-native test/report output; AutoMind
runner/probe-flow artifacts such as summaries, screenshots, hierarchy, traces,
and command logs; runtime logs or scoped diagnostic logs; and mock/test sink or
captured request/receipt evidence when the task is about side effects. Use
raw database/file/state inspection only as a fallback or supplemental
project-specific path when it is already easy and safe.

## 4. Preflight and blocker classification

Before blaming code, classify failures:

```text
product_failure
verifier_or_harness_failure
environment/device/signing/tooling blocker
unrelated workspace/build blocker
requirement_or_testcase_mismatch
```

Do not treat environment/device/signing failures as product failures. Low-risk
project-local helper setup may run when required by the selected verifier:

```bash
automind setup-automation-tools android
automind setup-automation-tools ios
automind setup-automation-tools visual
```

These commands create project-local `.venv-*` folders from the AutoMind runtime `requirements/*.txt`. They must not silently install system SDKs, signing material, keychains, trust settings, OCR engines, browser drivers, or privileged services. If package installation fails for a transient network/DNS reason, setup may retry once with explicit retry logs; if it still fails, classify the blocker and route to runtime-helper fallback, lower-capability fallback, or `ask_user`. See [`references/installation-runtime.md`](references/installation-runtime.md) for runtime/workspace/helper path rules.

For web/client/server target-project dependencies, use project-native setup and
verification commands from `TestCases.md` / `Plan.md`. If those commands are
unclear, or if a previous install/build failure may be an environment issue,
you may run the optional read-only discovery aid:

```bash
automind dependency-check <task-code> <iteration>
```

This command reports lockfiles, package managers, missing tools, and candidate
install/test commands; it does not install dependencies and is not a required
gate. Missing Node/pnpm/yarn,
Python/Poetry/uv, Docker daemon, Gradle/Maven/JDK, private registry auth,
database services, SDKs, or signing/device trust are environment/tool blockers
or `ask_user` situations, not product-code failures.

Verification flow and platform preflight details are in
[`references/verification-flow.md`](references/verification-flow.md) (cross-platform),
with [`references/verification-flow-ios.md`](references/verification-flow-ios.md) and
[`references/verification-flow-android.md`](references/verification-flow-android.md)
for platform-specific device flows.

## 5. Verification unblock rule

If verification is blocked by an unrelated build/test/workspace issue, AutoMind
may use a minimal reversible verification unblock change only when it is safe and
needed to run the selected verifier.

Hard requirements:

- checkpoint or record a diff before the temporary change;
- keep the change minimal and scoped to verification;
- record it in `Delivery.md` / `Validation.md` and
  `evaluation.json.verificationUnblockChanges`;
- restore it or explicitly promote it before finish;
- active temporary unblock changes block `nextAction=finish`.

The detailed policy and examples live in
[`references/verification-flow.md`](references/verification-flow.md#verification-unblock-policy).

## 6. Delivery.md gate

Before final Verify, Generator must write or update `Delivery.md` with:

- files changed and why;
- mapping to `Rxx`, `AC-xxx`, and `TC-*` where applicable;
- self-tests or local checks already run;
- risks, fallback, and temporary unblock changes if any.

Evaluator must not infer delivery from chat alone. Missing `Delivery.md` after
Generator runs is a gate failure.

## 7. Evaluator isolation

A model Evaluator must be context-isolated from Generator. Isolation means:

- Evaluator receives an audited context pack, not raw Generator transcript;
- `runtime-state.json.evaluatorContext.inheritsGeneratorContext=false`;
- context pack validation passes before model Evaluator runs;
- result exchange happens through task files, not hidden chat memory.

Preferred Evaluator order:

```text
1. Deterministic platform/script verifier
2. Native isolated subagent/session with context pack only
3. Fresh external agent CLI process with context pack only
4. Same conversation role switch - not acceptable for independent evaluation
```

When the AutoMind CLI is available, create the pack with:

```bash
<automind> context-pack <task-code> [iteration]
```

`workflow-check` validates recorded evaluator context when present and keeps
the derived `workflow.json` contract aligned with Phase 2 artifacts.

## 8. Functional verification first

Run the selected required functional batch before formal quality checks.

Before executing compile, build, install, launch, or verification commands, first
inspect the project workspace for existing scripts/runbooks that may already
encode the right path: `README*`, project docs, CI workflows, `scripts/`,
`tools/`, `bin/`, `Makefile`, Gradle wrapper/tasks, package-manager scripts,
Fastlane lanes, and repo-local helpers. Prefer the command named by the required
`TC-*`/`Plan.md`; if that command was not selected from an existing script, record
why before inventing a new command.

Rules:

- required functional `TC-*` cases need dynamic/runtime evidence when a runnable
  path exists;
- static inspection alone cannot prove required behavior unless dynamic execution
  is explicitly impossible/unsafe and recorded;
- dependency failures may mark dependent cases `not_run` or `skipped_dependency`;
- independent cases should continue when safe to collect more signal;
- after quality-driven runtime/product changes, rerun affected functional cases.

For App/UI/client-facing tasks, verify the product actually ran when required by
`TestCases.md`/`Plan.md`: prepare, build/install/deploy/start, launch/open the
specified entry page/screen/route/activity/state, perform actions, assert result,
and save logs/reports/screenshots/hierarchy/traces. Required App/UI/runtime cases need hard evidence in the ledger and a positive
`evidenceAssessment.verdict=proved` from the Evaluator/model; source-only or
blocker-assessed evidence is a completion failure.

Treat the required TestCase set as the verification contract. For client/app
behavior changes, do not collapse build, install/deploy/start, launch/open,
action-flow assertion, and evidence collection into an informal checklist unless
`TestCases.md` explicitly combines them into one required end-to-end `TC-*`.
Each baseline obligation must have a required TC result and evidence path. If any
required baseline TC is blocked by device/signing/tooling/environment state,
record the blocker, refresh/read `automind-workflow-state.json` for workflow
routing, and treat `evaluation.json.nextAction` as the local Evaluator signal.
Use `stages/verification-loop-stage-state.json` for verification-loop control payloads. Treat `runtime-state.json.stateSummary` as obsolete fallback only; do not mark the
product behavior complete.

For release/merge or explicit clean-build cases, attach the actual project-native
build evidence and let the Evaluator/model judge it in `evidenceAssessment`. A
classified `environment_blocked` / device / signing / tool blocker is not a
clean-build pass; continue with safe unblock/retry/replan/ask_user.

## 9. TestCase to verification operation

Evaluator should not treat `TestCases.md` as prose instructions only. For each
required `TC-*`, build one verifier operation from `testcases.json` /
`TestCases.md`:

```text
TC-* runbook
  -> executor selection
  -> concrete command/probe/test action
  -> evidence capture
  -> evaluation.json.testResults[]
  -> completion-check coverage
```

Executor selection order:

1. project-native command or test target named by the testcase;
2. `script-command` for project-local scripts/runbooks;
3. Android preflight + `probe-flow.android.json` + `android-probe-flow`;
4. iOS project XCUITest or `probe-flow.ios.json` / `action-plan.ios.json`;
5. browser/project E2E runner;
6. external-sink proof (mock, log, captured request, backend receipt when
   required);
7. static/quality review only for quality cases or explicitly blocked runtime
   cases.

If selectors/action details are missing for UI work, run discovery first
(screenshot + UI hierarchy/accessibility tree), refine the probe/test, and rerun.


### Runtime path failure classification

When a runtime/device/browser/UI path fails, is partial, or is blocked, the
Evaluator should add a compact, generic classification to the existing
artifacts instead of creating a heavyweight new ledger. Prefer fields directly
on `evaluation.json.testResults[]`, `failedChecks[]`, or the corresponding
`Validation.md` section:

```json
{
  "testCaseId": "TC-F01",
  "result": "partial",
  "runtimePath": "platform.entry_or_flow_name",
  "failureClass": "action_target_not_found",
  "observedSignals": {"event_a": 1, "required_event": 0},
  "shouldRetry": false,
  "retryAdvice": "Do not repeat the same path unless selector or trigger changes."
}
```

Use these `failureClass` values:

- `unknown` — inconclusive or not yet classified; classify before retrying the
  same path.
- `entry_invalid` — route/URL/fixture/start state did not reach the intended
  target.
- `entered_but_no_actionable_state` — target surface opened but the expected
  ready/actionable state was absent or ambiguous.
- `action_target_not_found` — intended control/selector/API/action target was
  not found.
- `wrong_surface_or_target` — automation acted on the wrong page/control/surface
  because selectors were too generic or state drifted.
- `action_failed` — action was attempted but no expected state transition or
  side effect appeared.
- `automation_timeout` — driver/script timed out before a decisive signal.
- `signal_missing` — journey ran but the expected log/event/output was missing.
- `proof_mismatch` — evidence proves a related behavior, not the required
  behavior or AC.
- `environment_blocked` — device/browser/network/account/permission/signing or
  service state blocked execution.
- `authorization_blocked` — consent/login/payment/delete/external upload or
  another sensitive boundary requires explicit user authorization.
- `diagnostic_needed` — black-box evidence cannot distinguish selector issue,
  product bug, or missing instrumentation; propose bounded diagnostics or
  scoped manual action.

`phase-reuse/<phase>.md` may surface recent failed runtime paths from
`evaluation.json` so the next Generator/Evaluator can avoid repeating them
unchanged. `workflow-check` should warn, not hard-fail, when Plan/Delivery looks
likely to repeat a low-value path without documenting a changed selector,
trigger, diagnostic, manual action, or `overrideReason`.

For stability signals, stable crash/timeout evidence must include stack/page context before it becomes a hard product failure. If a crash-like signal appears, record crash stack/backtrace, process/bundle, occurred page/screen/scene, reproduction path, and whether it is stable. If stable and attributable to product/runtime code, route to `retry_generator` for self-repair. If the signal is only verifier timeout, raw network/syslog timeout, historical crash text, or log digest/prompt text, classify it as warning / `automation_timeout` / `diagnostic_needed`, not a hard quality failure.
Do not mark a required functional `TC-*` as pass from static inspection or
missing-selector prose.

## 10. Special verification guidance

Keep Phase 3 focused, but use the reference guide for details:

- external sink / side-effect validation: analytics, telemetry, logging,
  notifications, message queues, network dispatch; database/file/state
  inspection is optional project-specific evidence, not a default requirement;
- UI/visual validation: measurable evidence first, AI Visual Review as
  supplementary, screenshot-based human confirmation as final fallback;
- mobile validation: Android/iOS preflight, build/install/launch, probe-flow,
  XCUITest, logs, screenshots, and UI hierarchy;
- generic project validation: project-native tests, script-command, unit tests,
  integration tests, build/lint/typecheck when relevant.

See [`references/verification-flow.md`](references/verification-flow.md).

## 11. Required outputs

### `Validation.md`

Must record:

- environment/preflight result;
- commands run and cwd;
- functional testcase results;
- quality results if selected;
- evidence paths;
- failure classification;
- verification unblock changes if any;
- reusable findings and avoid-repeat lessons;
- next action.

### `evaluation.json`

Must contain at minimum:

```json
{
  "iteration": 1,
  "result": "pass|fail|blocked",
  "summary": "short evaluator conclusion",
  "failedChecks": [],
  "evidence": [],
  "evidenceIndex": [],
  "testResults": [],
  "nextAction": "finish|retry_generator|replan|ask_user|stop"
}
```

Use `askUserQuestion` when `nextAction=ask_user`. Use
`verificationUnblockChanges[]` for temporary unblock work. Use
`humanConfirmation` only when explicit user confirmation is the evidence. Use
`evidenceIndex[]` as a lightweight artifact index when existing runner/summary
JSONs expose it; `testResults[].evidence[]` may still cite paths directly.

## 12. Decision rules

| Condition | `result` | `nextAction` |
|---|---|---|
| Required TC/AC/evidence can pass completion-check | `pass` | `finish` |
| Product/runtime behavior failed and requirements/tests are still valid | `fail` | `retry_generator` |
| Requirements, testcase, validation target, or approach is wrong | `blocked` / `fail` | `replan` |
| User decision, sensitive action, missing device/signing, or semantic visual confirmation is required | `blocked` | `ask_user` |
| The max-iteration guard is reached | `blocked` | `ask_user` |
| Non-recoverable agent/runtime failure or max-iteration stop | `fail` / `blocked` | `stop` |

`result=pass` requires `nextAction=finish`. `nextAction=finish` requires
`result=pass`.

Do not convert `environment_blocked`, `mobile_device_unavailable`,
`permission_blocked`, `tool_missing`, or similar blocker classifications into a
passed required testcase. Those categories are useful for routing; they are not
evidence that the product requirement passed. Keep trying through
`retry_generator`/`replan` when the blocker can plausibly be repaired or
unblocked. Use `ask_user` only when a human/system choice is required, such as
device authorization, signing material, privileged service startup, or selecting
real device vs simulator/emulator.

When a real device is already connected and authorized, operating the app for
verification (play, skip/next, trigger error, interrupt/pause, navigate, then
capture logcat/log) is AutoMind's own job, not a question for the user. Drive the
device through probe-flow/XCUITest/instrumentation with selectors and post-action
assertions. Do not emit `ask_user` like "I cannot operate your physical device,
please confirm the verification approach" — that is a non-whitelisted soft pause
and the completion gate rewrites it back to `retry_generator`. Escalate to
`ask_user(real_device_or_signing)` only for a real human/system gate: no device
in `state=device`, an unresolved unlock/Developer-Mode/USB-debugging/trust
prompt, missing signing/provisioning material, or denied UI Automation
permission — and then state exactly what AutoMind detected and which single
physical action is needed.

## 13. Completion artifacts and exit condition

`completion-report.json` and `VerificationLedger.json` have different jobs:

- `completion-report.json` is the compact final gate verdict: result, short
  summary, top issues/warnings, generatedAt, refs, and terminal authority.
- `VerificationLedger.json` is the detailed TC/AC/evidence coverage ledger:
  required TC status, required AC coverage, evidence refs, missing-proof reasons,
  issues, and warnings.

Keep both compact by reference. `completion-report.json` must not duplicate the
full coverage matrix, and `VerificationLedger.json` must not copy long
Requirements/TestCases/Validation prose. Use ids and `sourceRef` / evidence refs.

## 14. Completion gate and exit condition

Before claiming Finish, run:

```bash
<automind> completion-check <task-code>
```

Completion requires:

- every required `TC-*` passed with executed evidence; blocked/skipped/not-run
  required cases cannot finish unless the user explicitly changes scope or
  records human confirmation as the required evidence;
- every required `AC-xxx` is covered by passed required testcase evidence;
- evidence paths exist;
- active temporary unblock changes are restored or promoted;
- `Validation.md`, `evaluation.json`, and `VerificationLedger.json` are present.

If completion-check fails, route by the latest `evaluation.json.nextAction` or
replan the validation target. If Evaluator already set `retry_generator`, the
loop returns to Generator. If Evaluator claimed `finish` but completion-check
finds missing required TC/AC/evidence coverage, completion-check must block the
finish and route back to `retry_generator`, `replan`, or `ask_user` according to
the missing proof. Do not finish from chat confidence.

After completion passes, Phase 4 must generate summary/reuse and run
`record-check`, generate/inspect `Report.html`, and then perform the final
natural-language handoff. The handoff should tell the user what was completed
and generated, ask them to open `Report.html` first, and call out the key
proof/log/ledger files. In the report, each `Test Results` row should expose a
concise `Key Evidence` summary with screenshots when available, machine anchors
or hardMetrics, and the few runtime proof files humans should inspect first; the
complete artifact list can remain in the final column for traceability.
