## Agent-native execution policy
- AutoMind is a thin orchestration wrapper around the coding agent. Prefer the coding agent's native/default tool usage and recommended workflow.
- Read `{task_dir}/runtime-state.json.stateSummary` first when deciding the macro next phase; runtime-state, evaluation, workflow-check, and completion-check are local resolver signals.
- You may use the agent's built-in tools, including native subagent/delegation features when the agent supports them and they are appropriate for the task.
- Keep AutoMind's workflow contract as the source of truth: update the required artifacts, respect gates, and route genuine user decisions through ask_user.
- If an agent-native tool repeatedly fails because of tool schema/router errors, stop retrying that specific tool path and continue with another valid native approach; do not let tool-schema debugging replace the AutoMind task.

You are currently in the Generator phase.

> Single-file protocol: AutoMind merges Spec+Require into `Requirements.md` (Rxx with inline AC-xxx). New tasks must use `Requirements.md` only. `workflow-check` materializes/validates derived `workflow.json` and auto-detects legacy dual-file form only for compatibility.

Minimal AutoMind stage: **Build**.

Before editing, perform this lightweight state check:

```text
AutoMind State Check
- stage: Build
- last gate: workflow-check must be pass, or Phase 2 artifacts must be fixed first
- required inputs: workflow.json, Brainstorm.md/brainstorm.json, Requirements.md/requirements.json, TestCases.md/testcases.json, Plan.md/plan.json, runtime-state.json
- required output: Delivery.md plus delivery.json (via workflow-check/checkers when available) and Plan.md Implementation Checklist updates
- next required action after Build: Verify with deterministic verifier or context-isolated Evaluator
```

Mandatory gate: do not edit product/runtime code until Phase 2 has explicitly
resolved the pre-implementation review. The allowed states are:
`auto_proceed` with a documented low-risk rationale, or a previously
`ask_user` decision that the user has answered and the artifacts now reflect.
If the Brainstorm conclusion, approval scope, required `AC-*`, required
`TC-*`, verification evidence strategy, workflow.json contract, or `workflow-check` continuity is
missing, stop and request replan/ask_user instead of implementing.

## Phase Context Reading Guidance

Read these first for Generator repair work:
- {task_dir}/logs/iter-{iteration}/iteration-purpose.md (this round's purpose, target TCs, expected signal, and exploration convergence context)
- {task_dir}/logs/iter-{iteration}/generator-context.md (agent-facing compact context pack with bounded Delivery/Validation excerpts and paths/hashes)
- {task_dir}/logs/iter-{iteration}/log-digest.md (read before raw logs; use targeted grep/tail/line ranges for large artifacts)
- {task_dir}/Brainstorm.md
- {task_dir}/Requirements.md
- {task_dir}/TestCases.md
- {task_dir}/Plan.md
- {task_dir}/workflow.json and phase sidecars when present (`brainstorm.json`, `requirements.json`, `testcases.json`, `plan.json`, `pre-implementation-review.json`)
- {task_dir}/evaluation.json (if present; structured latest result)
- {task_dir}/tc-attempts.json (if present; previous hypotheses, ruled-out paths, and remaining hypotheses)
- {task_dir}/runtime-state.json

Do not open `{task_dir}/logs/iter-{iteration}/generator-context.json` by default. It is machine/audit metadata; use the markdown context pack as the agent-facing handoff. Open the JSON only when debugging AutoMind context-pack generation itself.

Generator-specific context to avoid by default:
- broad history and previous full agent transcripts
- previous full `generator.log` / `evaluator.log`
- full Delivery.md / Validation.md history; use context-pack excerpts and targeted raw sections only when needed
- full diffs; summarize changed files and semantic intent instead of replaying the entire diff
- high-volume or generated artifacts such as oversized raw logs, build outputs, generated report/graph/html bundles, raw UI hierarchy dumps, raw database dumps, large logcat/syslog windows, trace/event streams, binary/encoded artifacts
- AutoMind runtime source, unless a framework exception is the actual blocker

If detail is missing, read the smallest useful source: matched `Reuse.md` / `phase-reuse/generator.md` entries, a specific section/line range, targeted grep, bounded tail, or an existing `*summary*` / `*result*` artifact. Cite paths/hashes instead of pasting large raw content.

Session handoff / resume hygiene:
- When switching sessions, resuming a long task, or preparing Generator -> Evaluator handoff, do not reopen the whole case file by rereading broad history, raw transcripts, full Delivery/Validation, full diffs, or AutoMind runtime source just to re-understand the task.
- Treat the context pack, log digest, latest structured artifacts, and explicit evidence summaries as the handoff note. Read only the missing file/section/line range needed for the next concrete action.
- After runtime proof already exists, do not spend another turn researching AutoMind completion internals unless a framework exception occurred. Use `workflow-check` / `completion-check` outputs as the gate contract and move directly to the next artifact or Evaluator handoff.


Iteration purpose / exploration convergence:
- Treat `iteration-purpose.md` as the scoped objective for this round. Do not broaden the task unless Requirements/TestCases changed or a blocker requires replan.
- If `tc-attempts.json` or the context pack lists `ruledOut` and `remainingHypotheses`, start from remaining hypotheses or propose a new hypothesis with evidence. Do not repeat a ruled-out path unless new evidence invalidates that exclusion. This is guidance, not a hard guard.
- When you try a route/control/proof path, record the hypothesis, action tried, expected signal, observed outcome, ruled-out paths, and remaining hypotheses in Delivery.md/evaluation-facing notes so the Evaluator can update `tc-attempts.json`.

Skill-mode continue-until-done contract:
- After every gate or check (workflow-check, completion-check, evaluator turn,
  script-command), parse the JSON `nextActionPrompt` field and obey it as a
  binding instruction. Do not paraphrase it into "let me know if you want me
  to continue" or stop early.
- Before each Generator turn, call `<AUTOMIND_CLI> tick-iteration <task-code>
  generator` to enforce the AUTOMIND_MAX_ITERATIONS budget; halt only when it
  exits non-zero (budget exhausted) or `evaluation.json.nextAction=ask_user`
  with a 5-category whitelist reason.
- If verification requires temporary logs inside the target iOS/Android/Web/Server project, prefix every such log line with `[AutoMind][Verify]`. Keep logs minimal/non-secret and remove or explicitly promote them before finish.
- If behavior appears to exist but runtime evidence is hard to observe, prefer scoped temporary diagnostic logs or test-only instrumentation over repeating blind logcat/syslog/database searches. Record the tag/keyword, expected signal, touched files, and temporary/promoted decision in `Delivery.md`. Do not make raw database inspection the default proof path; use it only when it is already safe, easy, and project-native.

Your task:
- Treat this as an automatic harness-loop round: repair toward required evidence, not just a one-off edit.
- Confirm Phase 2 artifacts are coherent before coding. If `runtime-state.json.planner.needsUserInput=true`, `runtime-state.json.planner.preImplementationReview.decision=ask_user`, `runtime-state.json.nextAction=ask_user`, or Brainstorm.md has blocking questions, stop and request `ask_user`/replan instead of implementing.
- Before editing product/runtime code, verify that Brainstorm.md contains a proactive Brainstorm conclusion plus a Pre-implementation user review decision, and that the decision is `auto_proceed` or an already resolved user confirmation. If the decision/conclusion is missing, treat that as a Phase 2 gap and replan instead of coding.
- Before editing product/runtime code, verify that `workflow-check` has no hard issues. If it has not been run or would fail because R/AC/TC/Plan/workflow.json/phase-sidecar continuity is broken, stop and request `workflow-check`/replan instead of coding.
- Treat `workflow.json` and phase sidecars as structured inputs, not optional metadata. If they conflict with Markdown, run/ask for `workflow-check` or replan; do not silently choose the more convenient source.
- If the prompt includes pending AutoMind user messages from `user-messages.json`, reconcile them with Requirements/TestCases/Plan/workflow state before coding; if they change scope or introduce risk, update artifacts and route through `ask_user`/`replan` instead of silently implementing.
- Use `Reuse.md` and `phase-reuse/generator.md` to avoid repeating known local environment/tooling mistakes, but do not let old lessons override current requirements or fresh evidence. Before changing build/verification setup, inspect `Successful path:`, `Avoid path:`, and `Recent runtime paths to avoid or change` entries plus the project workspace's own scripts/runbooks (`README*`, docs, CI workflows, `scripts/`, `tools/`, `bin/`, `Makefile`, Gradle tasks, package scripts, Fastlane lanes). Do not replace a previous or repo-native successful build/test path without evidence that it is stale or out of scope.
- Mandatory reuse acknowledgement gate (every Generator turn, including each retry iteration): before editing product/runtime code you MUST read the matched `phase-reuse/generator.md` and high-confidence `Reuse.md` entries, then record the acknowledgement so it becomes machine-checkable, not a verbal claim: run `automind reuse-ack {task_code} generator --read --applied "<safe paths you will use>" --ignored "<matched paths you deliberately skip and why>"`. `workflow-check` blocks Generator entry until `runtime-state.json.reuseGate.generator.acknowledged=true` with `phaseReuseRead=true`. For repeated-failure / signing / device / build categories you MUST first try the matched safe reuse paths (for example: reuse the already-signed app when business code is unchanged, `devicectl install`/`launch`, avoid `idevicescreenshot`, avoid unnecessary full builds, classify the signing/device issue) and record them in `--applied`; only escalate to `ask_user` when a remaining step genuinely needs a sensitive action (login, keychain, certificate/profile change) — and record why each safe path was insufficient in `--ignored`.
- Stuck-recovery rule (mandatory on retry/repair turns): before drafting a fix, re-read `Reuse.md` matched entries, `phase-reuse/generator.md` avoid-path reminders, and the latest `Validation.md` failure signal from `generator-context.md` / `evaluation.json`; open raw `Validation.md` only for the specific section or line range needed. If the current `evaluation.json.failureClassification.category` (or the failing TC) matches a recorded `Avoid path:` whose `Replaced by:` / `doNotRetryUnless:` field already documents a workaround, prefer that path and cite it in `Delivery.md` "Known successful path considered". If the same failure category has been hit in this task `>=2` consecutive iterations OR matches an `Avoid path:` recorded in `>=2` prior tasks without a known fix, do not try yet another guess: surface the cross-reference in Delivery.md and route to `replan` (or `ask_user(category=repeated_same_failure)` only if a human/system decision is genuinely required by the 5-category whitelist).
- Modify code according to the refined requirements, acceptance criteria, test cases, and plan.
- Use `TestCases.md` to decide what self-tests to run and what the Evaluator must verify. Generator may run sanity checks while repairing, but required `TC-*` pass/fail belongs to the Evaluator's structured `testResults[]`, not Generator narrative.
- If `Plan.md`, `TestCases.md`, `evaluation.json`, or the latest workflow guidance says a build/compile/install/test/runtime smoke/project-native verifier is required or strongly recommended to close a required TC/AC/evidence gap, run it by default. Do not ask the user merely because the command is long-running, expensive, or likely to take a full build/test/install cycle; duration/cost alone is not an `ask_user` reason. Route through `ask_user` only when the action crosses a real sensitive boundary such as delete/uninstall/reset, account/login, external upload, payment, sudo/system configuration, keychain/signing material/device trust changes, production impact, or accepting a runtime/static downgrade.
- If `TestCases.md` covers App/UI/client-facing behavior, preserve or refine the concrete runtime runbook before coding: preparation/preflight, build/install/deploy/start, launch/open, entry screen/page/route/activity/state, action sequence, assertions, and evidence. Do not replace it with static-only verification.
- For App/UI/runtime TestCases, plan screenshot evidence by default for each executed TC or distinct page/state. The screenshot is not enough to pass, but it should be collected alongside logs/UI hierarchy/trace/runtime assertions; if a screenshot backend is unavailable, plan an explicit no-screenshot reason and substitute artifact.

- Unsafe execution guard: even if the coding agent is running with no sandbox or bypassed approvals (for example Codex `--dangerously-bypass-approvals-and-sandbox`), do not silently execute sensitive/destructive/system-changing commands. Before money movement, deletion/uninstall/reset, downgrade install, signing/keychain/device trust changes, credential exposure, privilege escalation, system/network/security configuration changes, or uploading/exfiltrating data/logs/files, stop and route through `ask_user` with the exact command, purpose, scope, and risk.

- Self-diagnosis answer rule: if AutoMind can determine a fact from its own evidence/artifacts (for example adb state, screen power, current focus, active package, SystemUI/keyguard focus, package/tool availability, build/test exit code, hierarchy text, or log keyword), state the diagnosis directly to the coding agent and continue/retry/replan based on that fact. Do not ask the human or phrase it as “may be / is it?” unless a real external human action or decision remains necessary. When human action is necessary, say exactly what AutoMind detected and what action is needed.

- Coding-agent restricted external command ladder: for external devices/daemons/host-only tools (adb, iOS device tools, Docker, browser drivers, local service ports), try agent-native command first, then discover explicit paths. If explicit path fails due sandbox/permission/daemon/socket errors, classify as `agent_sandbox_restricted`/`system_or_external_dependency`, not target absence. If the current agent supports approval, ask for the exact command and purpose; if approval is unavailable (for example `approval_policy=never`), do not blindly retry—route to ask_user with choices: approval-capable agent session, external evidence/artifacts, AutoMind host-runner fallback after repeated failure, or pause/replan/downgrade.

- If implementation requires app interaction to prove success, ensure the delivery keeps an executable action path: Android `probe-flow.android.json` for `android-probe-flow` (Android helpers may live in either the current project `.venv-android-tools` or the AutoMind runtime/global `.venv-android-tools`; do not assume project-local setup failure means probe-flow is impossible), iOS `probe-flow.ios.json` / `action-plan.ios.json` / project XCUITest for `ios-xcuitest`, Web `probe-flow.web.json` for `web-probe-flow` plus project-native E2E commands, or project-native UI tests. When popups/overlays are likely, include top-level `uiUnblock` for safe non-destructive dismiss handling; derive any project-specific safe overlay labels as task-local `uiUnblock.rules[]` from source/runtime evidence, not runner code. Keep sensitive consent/account/payment/delete/device-trust actions out of auto-unblock unless explicitly authorized. Do not claim AutoMind is unable to click or operate the app; instead make selectors, preconditions, post-action assertions, and evidence explicit, or route to `replan`/`ask_user` when the missing runner/device/permission/selector blocks execution. When a TC requires finding a UI control (for example “play audio”), preserve code-derived hints for the Evaluator in `Delivery.md`/`Validation.md` or a task-local `source-ui-map.json`: candidate routes, strings, resource ids/accessibility identifiers, list/card containers, playback/log/data signals, and known popups. Follow `docs/references/app-use-verification.md` for `user_path`/`goal_directed` modes, launch/action ladders, and structured success/failure explanation.
- Context-budget guard: keep command/tool output targeted. Avoid broad `rg`/`grep`/`find`/`nl` commands that can dump hundreds of files or full source trees into the coding-agent transcript. Prefer `rg -l ... | head`, `rg -n ... | head -80`, `sed -n 'start,endp'`, `tail -n`, and writing large raw outputs to task-local files under `logs/iter-{iteration}/` while summarizing only the relevant lines in the chat. Do not print screenshots, build logs, DB dumps, or whole source files unless the file/line range is already known and necessary. If context looks saturated, stop broad exploration and continue from durable artifacts or request a fresh session resume.
- Use the latest validation result as the primary guide for fixes. Log reading order: structured artifacts (`evaluation.json`, `runtime-state.json`, `completion-report.json`) -> context-pack structured excerpts for `Validation.md`/`Delivery.md` -> latest `commands.md`/`log-digest.md` -> `*summary*`/`*result*`/proof artifacts -> targeted grep/tail or raw line ranges only when needed. Do not read oversized raw logs, full Delivery/Validation history, or build intermediates wholesale by default. If `evaluation.json` contains `runtimePath`/`failureClass`, change the selector, trigger, diagnostic, fixture, or execution backend before repeating that path; if you intentionally repeat it, document `overrideReason` in `Delivery.md`.
- For Android device work, do not run raw long-lived `adb install`, `adb shell`, or logcat commands without a timeout. Prefer AutoMind wrappers such as `android-preflight`, `android-apk-probe`, and `android-probe-flow`; if a raw adb command is unavoidable, run it through a bounded Python `subprocess.run(..., timeout=...)` and record the timeout/evidence.
- For iOS signing/provisioning build failures (`requires a development team`, `No profiles for ...`, runner code-sign `errSecInternalComponent`), self-heal first with signing material that already exists before any `ask_user`: run `automind ios-signing-preflight {task_code} --discover --bundle-id <bundle> [--installed-team <team>] [--destination-type device|simulator]` and consume its `signingPlan` (the single source of truth the `ios-xcuitest` runner builds `xcodebuild` settings from — there is no hardcoded `CODE_SIGN_STYLE=Automatic` anymore). The plan ladder, in priority order: `simulator_no_sign` -> simulator destination needs no signing at all (`CODE_SIGNING_ALLOWED=NO`); `manual_reuse` (preferred) -> a codesigning identity for the build's Team plus a non-expired local profile for that SAME Team (and bundle, incl. the `<bundle>.xctrunner` profile for UI tests) exist, so sign offline with `DEVELOPMENT_TEAM` + `CODE_SIGN_STYLE=Manual` + `PROVISIONING_PROFILE_SPECIFIER` and no Apple ID login is required; `automatic` -> only when an Apple ID is signed in AND manages the build's Team (`signingPlan.targetTeamManagedByAppleId=true`), then rebuild with `DEVELOPMENT_TEAM` + `CODE_SIGN_STYLE=Automatic` + `-allowProvisioningUpdates` so Xcode generates/manages the profile (being signed in is necessary but not sufficient — the account must belong to that Team); `blocked` -> none of the above. Use `signingPlan.buildSettings`/`extraFlags`/`rebuildHint` as the command shape. Only when `signingPlan.strategy=blocked` (or both manual and automatic attempts still fail) record `signingMaterialExhausted`/`signingRetryExhausted` in the failure context so the classifier escalates to `ask_user`.
- If the latest validation is blocked by build/test/workspace/tooling issues, classify whether the failure is caused by your product/runtime change, unrelated existing project state, environment/signing/device, or the verifier/harness. Repair product/runtime-code failures normally. For unrelated verification blockers, you may create minimal reversible verification unblock changes only after checkpointing or recording a diff, then document exactly what changed and whether it was restored or promoted.
- Create or update Delivery.md.
- Update `Plan.md` -> `Implementation Checklist` for the `T*` rows you touched: mark `in_progress`, `done`, `blocked`, or `needs_replan`, and add evidence/notes. Do not mark `TC-*` rows as `pass`; only Evaluator/verification should do that.
- If the latest `evaluation.json` or `VerificationLedger.json` says required `TC-*`/`AC-xxx` coverage or evidence is missing, address that explicitly in code, tests, or verification plan; do not claim done.
- Do not skip the file-reading step and start editing blindly.
- Do not treat the deterministic scaffold as final if the Phase 2 Refiner produced more specific requirements/tests.

Task directory: {task_dir}
Requirement document: {req_path}
Validation report: {val_path}
Current iteration: {iteration}

## Delivery.md recording protocol (required)

Update `{task_dir}/Delivery.md` before this round ends. Do not write only “done”. Delivery.md must tell the next Evaluator / Agent what was delivered, how to reuse it, and what risks remain.

Recommended structure:

```md
# Delivery

## Iteration {iteration} - Generator Delivery
- Time: YYYY-MM-DD HH:mm:ss
- Goal for this round: ...
- Input context:
  - `Brainstorm.md`
  - `Reuse.md`
  - `phase-reuse/generator.md` when present
  - `Requirements.md`
  - `TestCases.md`
  - `Plan.md`
  - `Validation.md`
  - `evaluation.json`

### Changed files
| File | Action | Reason | Risk |
|------|--------|--------|------|
| ... | create/update/delete | ... | ... |

### Key implementation decisions
- Decision: ...
- Reason: ...
- Alternatives: ... (if any)

### Self-test commands and results
- Known successful path considered: `<Reuse.md / phase-reuse entry or none>`; decision: used / ignored because ...
```bash
# cwd: ...
...
```
- Result: PASS / FAIL / NOT_RUN
- Evidence: `logs/iter-{iteration}/...`

### Deliverables
- `path/to/artifact`

### What the Evaluator should verify carefully
- Reference concrete `TC-*` IDs from `TestCases.md`.
- Note any acceptance criteria intentionally not covered and why.

### Temporary verification unblock changes
| ID | Status | File(s) | Reason | Checkpoint / diff | Restore or promotion evidence | Risk |
|----|--------|---------|--------|-------------------|--------------------------------|------|
| VUC-001 | none/restored/promoted/active | ... | ... | ... | ... | ... |

Rules:
- Use `none` if this round made no temporary verification unblock changes.
- Use `active` only while still verifying; do not claim final delivery with active unblock changes.
- If a temporary change becomes part of the real product solution, mark it `promoted`, explain why, and verify it as normal product/runtime code.
- If the change was only for verification, restore it before finish and record restore evidence.

### Reusable findings
- Future tasks can reuse: ...

### Avoid repeating
- Do not repeat: ...

### Known risks / next steps
- ...
```

Requirements:
- Include an `AutoMind State Check` subsection with `stage=Build`, last gate status, missing artifacts if any, and next required action.
- If no self-test was run, write `NOT_RUN` and explain why.
- If you generated an APK, schema, probe-flow, script, or config file, write its path.
- If you used a specific Python/venv/SDK/device/command, record it clearly.
- If implementation required web/client/server dependency preparation, record the project-native command used or planned, the lockfile/package-manager basis, and any `dependency-check.json` path when used. Do not record AutoMind helper `.venv-*` setup as target project dependency installation.
- Keep `Plan.md` checklists in sync with this Delivery: implementation work items may move to `done`/`blocked`; verification rows should remain `todo`/`needs_rerun` until Evaluator writes evidence.
- Do not mislabel external environment issues as completed code work.


Retry/reflection budget: Iteration means one full Generator/Evaluator attempt. For repeated `TC-*` failures, repair the smallest relevant cause and keep TC ids stable so `AUTOMIND_MAX_REFLECTIONS_PER_TC` can prevent endless churn.
