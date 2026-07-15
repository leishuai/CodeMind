# Validation Flow Reference

This document is the **cross-platform** validation command reference for CodeMind
(generic script/project validation, visual/image, external-sink, the
verification-unblock policy, probe-flow, evidence, `Validation.md`/`evaluation.json`,
retry semantics, and the agent execution/command guards). Platform-specific
command flows are split out and loaded on demand:

- iOS: [`verification-flow-ios.md`](verification-flow-ios.md) — iOS prerequisites,
  simulator, real-device, signing/build-unblock, and the UI-runner priority ladder.
- Android: [`verification-flow-android.md`](verification-flow-android.md) — Android
  prerequisites, real-device flow, and adb daemon diagnostics.

Use it together with:

- [`../workflow.md`](../workflow.md) for the loop protocol;
- [`../phase3-verification.md`](../phase3-verification.md) for validation policy;
- [`probe-flow-generation.md`](probe-flow-generation.md) for `Require/TestCases -> probe-flow -> runner -> evaluation` generation;
- [`app-use-verification.md`](app-use-verification.md) for real app/browser operation, source UI maps, action ladders, and structured success/failure explanations;
- [`command-script-catalog.md`](command-script-catalog.md) for the canonical command-to-script map.

CodeMind should classify environment/device/tool blockers separately from product-code failures. Evidence beats guesses.

---

## Quick navigation

| Need | Section |
|------|---------|
| Command/script selection | [Command and script catalog](command-script-catalog.md) |
| iOS preparation / prerequisites / device flows | [`verification-flow-ios.md`](verification-flow-ios.md) |
| Android preparation / prerequisites / device flows | [`verification-flow-android.md`](verification-flow-android.md) |
| App/UI operation fallback order | [Operation fallback order](#operation-fallback-order) |
| Generic script/project validation | [Generic script/project validation](#generic-scriptproject-validation) |
| Probe-flow | [Probe-flow validation](#probe-flow-validation) |
| Visual/image verification | [Visual/image verification](#visualimage-verification) |
| External sink / side-effect validation | [External sink / side-effect validation](#external-sink--side-effect-validation) |
| Temporary verification unblock changes | [Verification unblock policy](#verification-unblock-policy) |
| Direct-route page load (last resort, all platforms) | [Direct-route page load](#direct-route-page-load-low-fidelity-last-resort) |
| Logs/screenshots/evidence | [Evidence collection](#evidence-collection) |
| Evaluation records | [Validation.md and evaluation.json](#validationmd-and-evaluationjson) |

## Operation fallback order

When a required testcase needs real app/UI/runtime behavior, use this order
before asking the user or accepting weaker evidence:

Pre-step: **honor pre-implementation modality approval.** If the one-shot
Decision Bundle explicitly allows simulator/emulator/browser-equivalent coverage,
start with that selected automation mode instead of treating it as a late
fallback. Android has a stricter default: when an authorized real device is
available, use the real-device path unless Emulator coverage was explicitly
selected/approved.

1. **Project-native proof first** when it directly proves the claim: unit,
   integration, platform UI test, framework report, or deterministic script.
2. **Platform UI runner / probe-flow next** for client behavior: Android
   `android-probe-flow`, iOS XCUITest/probe-flow/WDA/go-ios, or web project E2E.
3. **Safe precondition repair** before declaring a target missing: handle
   low-risk overlays through `uiUnblock` / `tap_if_present` with evidence.
4. **Temporary project edits before switching modality** when they preserve the
   requested automation level and are minimal/reversible. Example: add a UI test
   target or verification hook after no-edit runners fail, then restore/promote
   via `verificationUnblockChanges[]`.
5. **Direct-route/page-load after normal UI automation is exhausted.** It is
   lower fidelity than the UI-runner ladder, but still a CodeMind-controlled
   automation path.
6. **Simulator/emulator/browser-equivalent automation only when allowed** for a
   real-device testcase. Automation is still preferred, but the modality downgrade
   must be explicit when runtime proof required a physical device.
7. **Human-assisted evidence capture after automated paths.** The user may
   perform one narrow physical action only when automation is unavailable,
   inappropriate, or unsafe; CodeMind must still collect machine-checkable
   post-condition evidence.
8. **Ask for reduced-scope downgrade last** when no usable evidence path remains:
   dry-run, static proof, compile/build-only proof, or another scoped fallback.
   A blocker classification never passes a required `TC-*`.

---

## Human/device preparation

Real-device validation depends on both tools and the current device state. The
device-readiness checklist and automatic checks are platform-specific:

- iOS device preparation -> [`verification-flow-ios.md`](verification-flow-ios.md#ios-real-device-preparation)
- Android device preparation -> [`verification-flow-android.md`](verification-flow-android.md#android-real-device-preparation)

In all cases, classify a device/environment blocker separately from product-code
failure: do not send Generator to change product code when the real cause is an
unprepared device.

---

## Generic script/project validation

Use generic script validation when the project already has a concrete build/test/verification command and no mobile device runner is needed. The command should be recorded in runtime state or the Phase 2 artifacts before evaluation.

```bash
./automind.sh script-command <task-code> [iteration]
```

After the selected functional batch passes, run lightweight quality checks when appropriate:

```bash
./automind.sh quality-check <task-code> [iteration] --merge
```

For the complete command-to-script map, see [`command-script-catalog.md`](command-script-catalog.md).

---

## Visual/image verification

Use visual/image verification only when the requirement cannot be proven from
logs, API/data state, DOM/accessibility hierarchy, selector text, frame/bounds,
or project-native assertions. Prefer measurable evidence first; image
understanding is a supplementary semantic review for gaps that pure technical
measurement cannot close.

Routing:

1. Prefer structured evidence: logs/API/data state, DOM/accessibility
   hierarchy, frame/bounds, XCUITest frames, Android hierarchy bounds, or
   project-native UI/layout tests.
2. If pixels are the measurable target, use deterministic proof:
   screenshot diff, snapshot/golden comparison, OCR, bbox/crop checks, or:

   ```bash
   automind setup-automation-tools visual
   automind visual-inspect <task-code> --image logs/iter-1/screenshot.png
   automind visual-inspect <task-code> --image logs/iter-1/screenshot.png --baseline references/baseline.png --max-rms 8
   ```

   For "restore a specified UI design" tasks, the baseline is the user-provided
   design image (Figma export under `.automind/tasks/<task>/design/`, requested
   once in the pre-implementation bundle). `visual-inspect` resizes the baseline
   to the screenshot resolution by default so design mockups can be compared;
   pass `--strict-size` to require identical pixel dimensions instead.

3. If measurable methods cannot fully settle semantic visual correctness and
   screenshots/images exist, run AI Visual Review as an add-on only when the
   host model/runtime can inspect images.
4. If none of those paths can prove the required visual claim but screenshots can
   be captured, use the final semantic fallback: show the screenshot(s) to the
   user with the exact expected claim and set `nextAction=ask_user`. A screenshot
   path alone is never a visual pass. User confirmation can prove only the
   allowed visual/semantic claim; strict runtime/device proof still needs
   machine-checkable post-condition evidence.
5. If screenshots/evidence cannot be captured, write `blocked`/`fail` with
   `nextAction=replan` or `ask_user` for the missing environment/capability.

`visual-inspect` writes `logs/iter-N/visual-inspection.json` and auto-uses the
project-local `.venv-visual-tools` when present. If Pillow is missing, the CLI
wrapper may create/repair that venv from `requirements/visual-tools.txt`.

---

## External sink / side-effect validation

For analytics/event reporting, telemetry, metrics, logging, notifications,
network dispatch, payment callbacks, and similar external side effects, choose
the validation layer by changed scope:

| Changed scope | Required evidence | Optional stronger evidence |
|---|---|---|
| Only added/changed call into an existing sink | Runtime trigger path + sink method/API call + event key/payload assertion | Packet capture, backend/server log |
| Changed event schema/contract | Payload assertion plus contract/schema compatibility evidence | Server acceptance/backend log |
| Changed transport/retry/queue/network code | Transport-level test or end-to-end network/server proof | Packet capture plus backend log |
| Cannot run app/device but can run unit/integration test | Mock/spying sink or local test hook proving payload | Later device/manual confirmation |

Good local evidence examples:

- project-native unit/integration test with a mock/spying report manager;
- debug/test log that prints event key and sanitized payload at the sink boundary;
- runtime log assertion captured in `logs/iter-N/evaluator.log`;
- breakpoint/test hook/local file emitted only in test/debug mode;
- simulator/device app run that triggers the path and captures the sink-call log.

Avoid these mistakes:

- Do not require Charles/proxy/backend logs for every analytics addition when
  the transport layer was not changed.
- Do not static-pass a required functional case merely because network capture
  is difficult.
- Do not add permanent noisy production logs just to satisfy verification; use
  test/debug hooks or existing logging when possible.

---

## Verification unblock policy

Use a temporary verification unblock only when the selected verification is
blocked by an unrelated build/test/workspace/harness issue and the unblock is
minimal, reversible, and does not weaken the required `TC-*` assertion.

Required sequence:

1. Classify the blocker before editing: product failure, environment/device,
   verifier/harness, unrelated workspace/build, or requirement/test mismatch.
2. Checkpoint or record `git status` and `git diff` under `logs/iter-N/`.
3. Make the smallest local change that allows the selected verifier to run.
4. Run the verification and collect normal evidence.
5. Restore the temporary change or explicitly promote it as real delivery.
6. Record every change in `Delivery.md`, `Validation.md`, and
   `evaluation.json.verificationUnblockChanges`.

Example record:

```json
{
  "verificationUnblockChanges": [
    {
      "id": "VUC-001",
      "reason": "Temporary fixture needed to run unrelated test target.",
      "files": ["Tests/Fixtures/MockConfig.swift"],
      "status": "restored",
      "diff": "logs/iter-2/verification-unblock.diff",
      "restorePlan": "Remove fixture after verification unless promoted."
    }
  ]
}
```

Completion rule: `active` unblock changes block finish. Each item must be
`restored` or `promoted` before `nextAction=finish`. If the unblock would require
sensitive/destructive/system changes, use `ask_user` instead.

Allowed examples:

- temporary local fixture or mock needed by a test target;
- temporary debug/test log or test hook proving a runtime path;
- excluding/stubbing an unrelated broken target from a temporary validation
  workspace;
- repairing a probe-flow/test-harness bug.

Disallowed examples:

- changing product behavior to hide a requirement failure;
- permanently disabling required validation;
- modifying signing/keychains/device trust/system SDKs without user approval;
- broad unrelated refactors just to make the workspace build.

### Auto-unblock overlay policy

Auto-unblock overlay is a low-risk UI precondition helper for iOS, Android, and
web. It removes common popups that hide the target page before the real testcase
action runs. It is not business proof by itself.

Probe-flow may declare:

```json
{
  "uiUnblock": {
    "enabled": true,
    "policy": "safe_non_destructive_only",
    "maxAttempts": 3,
    "afterLaunch": true,
    "beforeActions": true,
    "rules": [
      {
        "category": "safe_dismiss",
        "decision": "allow",
        "terms": ["<task/project-specific safe dismiss label>"],
        "reason": "Task-local safe overlay close action."
      }
    ]
  }
}
```

The built-in rule set only covers generic safe dismiss/close patterns and
generic sensitive patterns. It is a fallback, not a product-specific oracle.
Planner/Evaluator should extend `uiUnblock.rules[]` from project context,
runtime hierarchy, accessibility labels, and TestCases when a task has a
project-specific safe overlay. Rules are data, not runner code, so no platform
adapter should grow app/business hard-coding.

App-internal privacy/terms Agree/Allow/Continue and OS/app permission Allow controls may auto-unblock with evidence so verification can proceed.
Do not auto-click login/account,
payment/purchase, delete/reset/uninstall, external upload, signing/device trust,
reject/deny, or ambiguous consent unless the task already has explicit authorization for that
exact scope. Record `overlay-unblock.json`, before/after screenshot/hierarchy or
DOM/trace references, the applied rule/category/decision, and an
`overlay_unblock` row in `action-trace.jsonl`.

Platform status:

- Android `android-probe-flow` executes this policy with UI hierarchy scanning
  and safe dismiss taps.
- Web `web-probe-flow` records the same policy as a project-E2E contract; the
  Playwright/Cypress/project command must perform the actual close/dismiss and
  leave trace/screenshots/DOM evidence.
- iOS follows the same policy at the probe-flow/XCUITest intent level; execution
  stays with project-native XCUITest/runner support.

### Direct-route page load (low-fidelity last resort)

This applies to all UI platforms — iOS, Android, and web. It is **not** a tier in
any UI-runner ladder, because the ladder tiers only differ in how UI commands are
delivered (xcodebuild XCUITest, WebDriverAgent, Espresso/UiAutomator, browser
driver, etc.) while all of them still drive the real navigation flow. Directly
running route/navigation code to jump straight to a specific page (e.g. invoking
a router/deep-link/`navigate()` API, an internal debug route, or a test-only
entry point) instead trades fidelity for reachability, so it sits **below** the
whole ladder.

Use it only when every normal UI-automation tier has been tried, including
minimal reversible project edits when they preserve the requested modality, and
the page genuinely cannot be reached through the normal flow (the automation is
blocked and not bypassable). It is a high-risk verification-unblock change and
must follow the unblock sequence above (classify -> checkpoint -> minimal change
-> run -> restore/promote -> record in `verificationUnblockChanges[]`), plus
these extra constraints:

- The page reached this way may not be in a realistic state — preconditions,
  upstream data, and side effects of the normal entry path are skipped, so the
  rendered result is not guaranteed to match production. Treat the result as
  weaker evidence.
- Narrow the assertions accordingly. It can validate that the target page/route
  *renders/loads* and that page-local logic works, but it must **not** be
  reported as "the full UI flow passes" or used to satisfy a required testcase
  that asserts the end-to-end navigation path.
- Record it explicitly with `reason` stating "direct-route load, non-standard
  entry, low fidelity" and keep the entry hook out of shipped product behavior
  (restore, or promote only as a guarded debug/test entry, never as a silent
  product change).
- If the requirement specifically needs the real entry flow validated, this
  path does not satisfy it — route to `replan` or `ask_user` instead of claiming
  pass.

### Human-assisted evidence capture (after automated fallbacks)

This applies only after the UI-runner ladder, reversible automation-preserving
project edits, direct-route/page-load fallback, and any explicitly allowed
simulator/emulator automation have been considered. It is lower priority than
those paths because they remain CodeMind-controlled automation, while this mode
asks the user to perform one physical action on the device.

Use it when the remaining blocker is genuinely human-owned or device-local, and
CodeMind can still collect machine evidence around the user's action. Examples:
the user taps a sensitive authorization/login/permission control, performs a
physical-device-only gesture, or clears a system prompt that automation must not
click directly.

Constraints:

- Do not use this mode to outsource ordinary automatable actions (play, pause,
  skip, navigate, close a safe dialog) while a real UI runner is available.
- Ask for one narrow action with an explicit time window and reason; record the
  request as `ask_user`, not as an automatic pass.
- CodeMind must collect before/during/after evidence where available: screenshot
  or screen recording, device/runtime logs, console/syslog markers, DB/API/event
  cache diffs, external sink events, and post-action UI assertions.
- The user's manual action alone is not proof. A pass still needs
  machine-checkable post-condition evidence in `evaluation.json.evidence[]` and
  `Validation.md`.
- If only a screenshot or user statement exists, treat it as weak/manual visual
  confirmation and do not use it to satisfy strict runtime proof unless the
  testcase explicitly allows that evidence type.

---

## Probe-flow validation

Probe-flow is generated from `Requirements.md` and `TestCases.md`; it must not invent product goals.

```text
Requirements.md / TestCases.md
  -> probe-flow*.json
  -> platform runner
  -> artifacts + evaluation.json
```

Schema:

```text
schemas/probe-flow.schema.json
```

Starter examples:

```text
examples/probe-flows/android-basic.json
examples/probe-flows/ios-intent-basic.json
```

Run Android probe-flow:

```bash
./automind.sh android-probe-flow <task-code> [iteration] [--dry-run]
```

Run iOS probe-flow:

```bash
./automind.sh ios-probe-flow <task-code> [iteration] [--flow probe-flow.ios.json] [--dry-run]
```

Materialize iOS probe-flow to Swift XCUITest draft:

```bash
./automind.sh ios-probe-flow-materialize <task-code> [iteration] --target-bundle-id <bundle-id>
```

Optional or weak steps such as `tap_if_present` are allowed for popups or non-critical UI. Strong task-specific assertions should still fail the round when unmet.

### Page / State Signature MVP

Use `assert_page` when a UI flow must prove it reached a target page/state. Keep the contract small:

```json
{
  "type": "assert_page",
  "name": "Home page",
  "pageSignature": {
    "name": "home",
    "required": [{"resource_id": "com.example:id/search_bar"}],
    "anyOf": [{"text": "Home"}, {"text": "Media"}],
    "minAnyOf": 1,
    "forbidden": [{"text": "Privacy notice"}]
  }
}
```

Semantics: all `required` conditions must match, at least `minAnyOf` `anyOf` conditions must match, and no `forbidden` condition may match. Android evaluates this directly against UI hierarchy XML. iOS/Web accept the same intent contract; real proof should come from XCUITest or project-native E2E evidence until those runners emit structured accessibility/DOM snapshots.

### UI action trace and critical-action screenshots

Android and iOS are both client UI targets and should share the same evidence contract at the CodeMind layer: action trace, Client UI action evidence, screenshot/accessibility/hierarchy references, and compact `Validation.md` `Client UI action evidence` reporting. Platform-specific details stay inside each runner/adapter; web can reuse the same contract with simpler DOM/screenshot evidence. For app-use path exploration, source UI maps, launch/action ladders, `softFailure`, `ruledOut`, `remainingHypotheses`, and structured pass/fail explanations, follow [`app-use-verification.md`](app-use-verification.md).

For Android probe-flow, critical UI actions (`launch`, `tap`, `tap_if_present`, `input`, `swipe`, `keyevent`, or any step with `critical: true`) should produce auditable action evidence:

```text
logs/iter-N/probe-flow/action-trace.jsonl
logs/iter-N/probe-flow/action-XX-before.png
logs/iter-N/probe-flow/action-XX-before-hierarchy.xml
logs/iter-N/probe-flow/action-XX-after.png
logs/iter-N/probe-flow/action-XX-after-hierarchy.xml
```

The action trace records action intent, selector, resolved node/bounds/center when available, tap point, before/after screenshot and hierarchy paths, and post-action detail. Reports should cite these artifacts for key UI actions instead of saying only that a button was clicked. Screenshot evidence supports UI understanding, but pass/fail still requires structured post-action checks where possible.

For iOS probe-flow, `action-trace.jsonl` records the reviewable XCUITest action intent/selector in the same shape. Real execution evidence remains XCUITest logs/xcresult and project attachments; do not invent per-step screenshots when the XCUITest backend did not produce them.

For iOS XCUITest screenshot attachments, generated Swift drafts from `ios-action-plan` and `ios-probe-flow-materialize` should call `XCTAttachment(screenshot: XCUIScreen.main.screenshot())` after key UI actions, with `lifetime = .keepAlways`, so `.xcresult` contains visual evidence without relying on the physical-device `pymobiledevice3` screenshot backend.

For Web probe-flow, `action-trace.jsonl` records URL/route, role/text/css/test-id targets, and project E2E command evidence in the same Client UI contract. Prefer project-native Playwright/Cypress/test scripts when available; do not silently install browsers or drivers.

---

## Evidence collection

Every validation attempt should record enough evidence for another agent or human to understand the result.

Recommended files:

```text
logs/iter-N/commands.md
logs/iter-N/env.json
logs/iter-N/evaluator.log
logs/iter-N/probe-flow-summary.json
logs/iter-N/quality-summary.json
logs/iter-N/screenshot.png
logs/iter-N/window.xml
logs/iter-N/logcat.txt
logs/iter-N/result.xcresult
logs/iter-N/ai-visual-review.json
```

Evidence should answer:

- What command ran?
- In which cwd and environment?
- Which device/app was targeted?
- What passed, failed, or blocked?
- Is the failure product code, platform/flow, tool missing, permission, or device state?
- If screenshots/images were reviewed by a vision-capable model, what image path,
  region, expected visual state, actual visual state, and confidence/result were
  recorded?

### Evidence tiers (which evidence to prefer)

Not all evidence is equal. Prefer Tier-1 hard evidence; treat Tier-2 as
supporting. This ordering is the single source of truth — the iOS and Android
flow docs point back here.

- **Tier-1 hard evidence (machine-checkable, can populate `hardMetrics`)** — this
  is what proves a required pass:
  - runtime logs / device logs with matched keywords (`log_keyword_matched`):
    crash stacks, lifecycle, business/network markers. Logs are first-tier hard
    evidence, not just supporting decoration.
  - test result bundles: `.xcresult` (iOS) / JUnit / framework reports, yielding
    `tests_passed`, `exit_code`, `build_succeeded`.
  - post-action UI assertions (`assert_exists` / `assert_text`): program-judged
    proof that the screen reached the expected state after an action, recorded in
    the result bundle.
- **Tier-2 supporting evidence** — corroborates but cannot by itself prove a
  semantic claim:
  - screenshots and accessibility/UI-hierarchy dumps. Back a semantic visual
    claim with a measurable check (hash/diff/OCR/geometry), AI Visual Review, or
    recorded human confirmation before treating it as proof.

**Screenshots are strongly recommended whenever they can be captured**, even
though they are Tier-2. Capture before/after screenshots for key UI actions and
attach them, and **surface them in `report.html`** so the user can visually see
that CodeMind actually ran the verification — this makes the run clearer and more
trustworthy. Screenshots supplement, never replace, Tier-1 hard evidence.

---

## Validation.md and evaluation.json

`Validation.md` is cumulative human/agent-readable history. Append key verification rounds and evidence paths.

`evaluation.json` is the latest structured control signal. The orchestrator/agent must read it before guessing from text.

Schema:

```text
schemas/evaluation.schema.json
```

Typical result shape:

```json
{
  "iteration": 1,
  "result": "fail",
  "summary": "Effect panel cannot be opened.",
  "failedChecks": [
    {
      "name": "TC-F02 open effect panel",
      "reason": "Button was not found in the current UI hierarchy.",
      "category": "validation_failure",
      "evidence": "logs/iter-1/window.xml"
    }
  ],
  "skippedChecks": [
    {
      "name": "TC-F03 select Reverb",
      "testCaseId": "TC-F03",
      "reason": "Skipped because TC-F02 failed.",
      "category": "skipped_dependency",
      "dependsOn": "TC-F02"
    }
  ],
  "evidence": [
    {"type": "ui_hierarchy", "path": "logs/iter-1/window.xml"}
  ],
  "nextAction": "retry_generator"
}
```

Functional dependency failures may fail fast. Formal quality-check should run only after the selected functional batch is acceptable.

---

## Retry semantics for UI automation

Keep retry semantics small and explicit:

- **Attempt retry**: same command, same flow, same iteration. Use for transient runner/server/device timing issues. Web probe-flow supports `--retries N` and preserves `web-probe-flow-attempt-*.log` evidence.
- **Reflection retry**: a new Generator/Evaluator loop attempts to fix the same TestCase based on evidence (selector, wait, optional popup, probe-flow/action-plan adjustment). This counts toward `AUTOMIND_MAX_REFLECTIONS_PER_TC` for referenced `TC-*` ids.
- **Strategy retry**: replan or choose another verifier/backend when the current route is unsuitable (for example physical device blocker -> simulator, external runner -> native UI test target).

Android and iOS should share the same high-level UI automation contract: intent/action trace, key action evidence, page/state assertions, `evaluation.json`, and `Validation.md`. Platform-specific details stay inside adapters: Android uses screenshot/hierarchy/UIAutomator evidence; iOS uses XCUITest logs, `.xcresult`, and `XCTAttachment(screenshot:)`.

---

## Temporary verification logs in target projects

When CodeMind needs to add temporary logs to the target iOS/Android/Web/Server project to prove a testcase, every such log line should use a stable prefix:

```text
[CodeMind][Verify] <component> <event> key=value ...
```

Examples:

```text
[CodeMind][Verify] iOS PlayerViewModel playbackState=playing trackId=123
[CodeMind][Verify] Android LoginRepository requestFinished status=200 userId=fixture
[CodeMind][Verify] Web CheckoutPage submitClicked orderId=fixture-001
[CodeMind][Verify] Server PaymentWebhook received eventId=evt_fixture result=accepted
```

Use temporary verification logs only when existing hard evidence is insufficient or too indirect. Prefer existing deterministic evidence first: test assertions, exit codes, screenshots, UI hierarchy/accessibility, DOM snapshots, network mocks, database/API assertions, and platform test reports. If temporary logs are added, they must be minimal, non-secret, removable or explicitly promoted, and recorded in `Delivery.md` / `Validation.md` / `evaluation.json` as verification evidence. Do not log tokens, passwords, PII, payment data, private payloads, or full request/response bodies.

### Unsafe execution guard for no-sandbox/bypass modes

CodeMind supports no-sandbox / permission-bypass coding-agent modes for highly
automated host-only verification. Coding-Agent Evaluator invocations must always
be fresh-isolated and bypassed for all supported agents: Codex uses dangerous
no-sandbox, Claude uses dangerous skip permissions, and Trae/Trae-CN uses YOLO
mode. This is required because Evaluator runs the most runtime/device/build
evidence commands. Planner/Generator approval bypass is controlled by the task-level
`runtime-state.json.agentExecutionPolicy`, shared across Codex/Claude/Trae. Missing
policy falls back to bypass so non-new tasks, detached scripts, resume/recover,
helper commands, or flows that cannot ask still keep high automation. TUI-owned
sessions ask only when a new task is created and only once; resume/recover,
detached scripts, and helper commands do not create new bypass grants and must
follow the existing task policy or the missing-policy bypass fallback. The task-level policy does not apply to Evaluator. If bypass state
changes, CodeMind must not reuse a primary Planner/Generator session created
under the opposite execution mode. These modes are never a blanket approval for
high-risk actions. When an agent runs with bypassed approvals/no sandbox,
the workflow must still route sensitive/destructive/system-changing operations
through `ask_user` before execution. The `ask_user` question must include the
exact command or action, purpose, scope, affected paths/packages/devices/accounts,
and risk. Examples that require a gate include deletion/uninstall/reset, downgrade
install, signing or keychain changes, device trust/developer-mode changes,
credential exposure, privilege escalation, system/network/security configuration
changes, money movement, or uploading/exfiltrating files/logs/data.

### Coding-agent restricted external command ladder

When a Coding Agent needs to interact with external devices, local daemons,
privileged services, or host-only tooling (Android `adb`, iOS device tooling,
Docker, browser drivers, local service ports, etc.), do not collapse a failed
agent shell command into "target unavailable". First distinguish where the
failure happened:

1. **Agent native command** — try the normal command when it is expected to be
   available in the agent environment.
2. **Explicit path discovery** — if the command is missing from PATH, discover
   and retry the explicit tool path from environment variables, project config,
   or common locations (for Android, `$AUTOMIND_ADB`, `$ANDROID_HOME`,
   `$ANDROID_SDK_ROOT`, `local.properties sdk.dir`, then common SDK paths).
3. **Sandbox/permission restriction** — if the explicit tool exists but fails
   with sandbox, daemon, USB, socket, or permission errors, classify it as
   `agent_sandbox_restricted` / `permission_blocked` /
   `system_or_external_dependency`, not as physical target absence.
4. **Agent approval path** — if the current agent session supports command
   approval (for example Codex `--ask-for-approval on-request`), ask for the
   exact command with purpose, cwd, scope, expected evidence, and risk; after
   approval, let the agent execute that exact command and record the result.
5. **No-approval sessions** — if the current agent session cannot request
   approval (for example `approval_policy=never`), do not retry the same
   restricted command blindly. Ask the user to choose one of: restart/use an
   agent session that can request exact-command approval, provide external
   evidence/artifacts, use a CodeMind host-runner fallback after repeated
   agent failure, or pause/replan/downgrade as appropriate.
6. **Host-runner fallback** — CodeMind host runner is a fallback after repeated
   agent-side failures or when the agent cannot safely/legitimately access the
   needed external resource. Prefer agent-native execution first, but collect
   host-runner evidence when it is the only reliable way to execute the selected
   verification path.

Always record the exact command, execution environment, whether approval was
available/requested/granted, and the raw failure output. If a host shell can
reach the resource but the coding-agent shell cannot, say so explicitly and
route by agent/host environment mismatch rather than asking the user to reconnect
a device or reinstall an already-present tool.

Android-specific adb path/daemon/device diagnostic categories live in
[`verification-flow-android.md`](verification-flow-android.md#android-adb-environment-and-daemon-diagnostics).
