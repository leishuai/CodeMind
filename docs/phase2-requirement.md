# Phase 2: Requirements and Test Design

Phase 2 turns the user's request into a small set of connected, testable task
artifacts. It is model-driven: the deterministic scaffold is only a starting
point, and the Planning Orchestrator / Refiner must use project context and
reasoning to improve it before implementation.

Phase 2 is split by flow type:

```text
Phase 2A — Demand Definition
  Brainstorm.md   -> divergent demand digestion and bounded research
  Requirements.md -> convergent Rxx / AC-xxx contract

Phase 2B — Verification & Execution Planning
  TestCases.md -> AC -> executable/inspectable proof design
  Plan.md      -> implementation order, verification order, fallback, handoff
```

Brainstorm and Requirements are both demand-understanding work: one explores
the request in project context, the other freezes it into a reviewable contract.
TestCases and Plan are technical derivations after the demand is clear. They
should not reinterpret the user goal except to route back to Phase 2A when the
contract is incomplete.

## 1. When to run

Run Phase 2 after Prepare/scaffold and before product/runtime code changes.
Re-run it whenever requirements, acceptance criteria, validation targets, or
verification feasibility change.

Inputs:

- user request and any follow-up decisions;
- `Reuse.md` and relevant local summaries;
- implementation surface, verification surface, and known risks;
- existing scaffold files under `.automind/tasks/<task>/`.

Outputs:

```text
Brainstorm.md -> Requirements.md -> TestCases.md -> Plan.md -> workflow.json
```

`workflow-check` must pass before Build. It refreshes/validates the derived
`workflow.json` contract from Phase 2 artifacts. If it fails, fix Phase 2
artifacts; do not start Generator against incoherent requirements, tests, or
workflow contract.

New tasks use `Requirements.md` as the authoritative contract (Rxx with inline AC-xxx). Do not generate `Spec.md` or `Require.md`. This removes 1:1 restatement between requirement units and acceptance criteria.

## 2. Phase 2 state machine

```text
Discover context
  -> Phase 2A Demand Definition
      -> Brainstorm/refine direction
      -> Requirements (Rxx + inline AC-xxx)
  -> Phase 2B Verification & Execution Planning
      -> TestCases executable runbooks
      -> Plan implementation/verification order
  -> workflow.json derived executable contract
  -> Pre-implementation review gate
  -> workflow-check
  -> Build or ask_user/replan
```

Before each transition, verify the current artifact exists and contains the
required IDs/decisions for the next artifact. Do not rely on chat memory.

## 3. Required artifacts and gates

| Artifact | Purpose | Must contain | Gate before next step |
|---|---|---|---|
| `Brainstorm.md` | Phase 2A divergent demand digestion | goal, scope/non-goals, options, recommended direction, open questions, pre-implementation decision | unresolved questions route to `ask_user` or `replan` |
| `Requirements.md` | Phase 2A convergent demand contract | `R01`, `R02`, ... with concise behavior statements; each Rxx followed by `AC-xxx` rows with verification method and required flag  | every important user goal has an `Rxx`, and every required `Rxx` has testable AC coverage |
| `TestCases.md` | Phase 2B evidence-producing proof design | `TC-*` rows with runtime level, preconditions, command/action, assertion, evidence, dependency, required flag | required functional cases are executable or explicitly blocked |
| `Plan.md` | Phase 2B execution and tracking contract | first functional batch, verification command/tool, implementation checklist, verification checklist | every required `TC-*` is tracked |
| `runtime-state.json` | Runtime/resume projection | `planner.preImplementationReview.decision` | `workflow-check` validates this field |

Subflow guides live in [`phases/demand-definition.md`](phases/demand-definition.md)
and [`phases/verification-execution-planning.md`](phases/verification-execution-planning.md).
Detailed artifact templates and good/bad testcase examples live in
[`references/test-design-guide.md`](references/test-design-guide.md).

## 4. Brainstorm policy

Brainstorm is not a passive restatement. It must proactively help the user think
through the task before implementation:

- restate the user goal in one sentence;
- identify scope and non-goals;
- inspect only the implementation and verification surfaces needed for the task;
- list key assumptions and risks;
- compare reasonable approaches when there is more than one;
- recommend one direction and explain why;
- propose the verification direction and must-pass evidence when known;
- decide whether to continue automatically, ask the user, or replan.

For most non-trivial implementation or behavior-change tasks, ask the user early
to confirm the Brainstorm/Spec direction before editing product/runtime code.
The user should not have to review every generated artifact; ask for directional
confirmation with concise options and a recommended option.

### 4.1 Self-discovery before ask_user

Brainstorm must attempt static project discovery before recording any
"information missing" assumption or drafting an `ask_user` question. Asking the
user for facts that the project files already contain is a protocol violation
that breaks the One-Shot pre-implementation review.

Discovery scope, proportional to the task surface:

- Project shape via `Glob`/`LS`: `*.xcodeproj`, `*.xcworkspace`, `Podfile`,
  `Package.swift`, `BUILD.bazel`, `WORKSPACE`, `custom_build_wrapper.yml`, `custom_workspace_wrapper.yml`,
  Gradle/Android manifests, `package.json` + lockfiles, `pyproject.toml`,
  `Cargo.toml`, `go.mod`, `Makefile`, CI configs.
- Identifiers via `Grep`: `PRODUCT_BUNDLE_IDENTIFIER`,
  `ios_application(bundle_id`, `CFBundleIdentifier`, `applicationId`, scheme/
  target names. Use `xcodebuild -list` / `bazel query` / `./gradlew tasks`
  when CLI is available.
- Build/test commands via README/CI/lockfile inspection, then framework/SDK
  keywords via `Grep` to locate code anchors and existing patterns.
- `Reuse.md` `Successful path:` / `Avoid path:` entries.

Record findings in `Brainstorm.md` "Project/context observations" with file
paths and exact identifiers. Only `ask_user` after discovery genuinely fails
or the question is a business/product decision, multi-candidate disambiguation
(present discovered options + recommendation, not open-ended), or one of the
5-category hard-interrupt whitelist.

See `templates/phase2_planner_prompt.md` "Self-discovery before ask_user" for
the full anti-pattern list.

## 5. Pre-implementation review gate

Before Build, both `Brainstorm.md` and `runtime-state.json` must record one of:

```text
auto_proceed | ask_user | replan
```

Use `auto_proceed` only when the task is low-risk, mechanical, documentation-only,
verification-only, already approved, or explicitly requested as no-confirmation
automation.

Use `ask_user` when user intent, scope, product behavior, validation target,
ambiguous/irreversible privacy/security impact, destructive action, or
non-obvious tradeoff needs a human decision. Privacy/terms Agree/Allow/Continue
is sensitive by default. Use ask_user unless the exact consent action was
already authorized in the pre-implementation bundle; auto-unblock without
ask_user is limited to safe close/skip/later/dismiss overlays. Ask with options,
put the recommended option first, and state the impact of each option.

Only client/app development or verification tasks need the mobile verification
target gate. For Android/iOS/mobile client tasks, the pre-implementation review
must explicitly settle the target when it is not already clear: real physical
device, simulator/emulator, or both. Ask before implementation because this
choice changes setup, evidence strength, runtime cost, and failure
classification. First run read-only physical-device discovery. If device(s) are
connected, present the detected device(s), recommend real-device verification,
and ask whether CodeAutonomy may use them, use both real device and
simulator/emulator, or intentionally keep simulator/emulator only.
If no physical device is connected, say so, explain the connect/unlock/trust,
Developer Mode/USB debugging, signing, and permission-prompt requirements.
Screenshot capture is default allowed verification evidence and must not trigger a separate ask_user.
When exactly one connected real device is available, state in the review bundle that CodeAutonomy will use that device by default for development, debugging, verification, and screenshots.
When multiple connected real devices are available, ask_user to choose the target device.
When no authorized real device is available, CodeAutonomy should try simulator/emulator verification by default; ask_user only if no runnable simulator/emulator path exists and the remaining fallback would be static-only or otherwise sensitive.

### Real-device-default verification policy

For client/app behavior tasks CodeAutonomy defaults to real-device verification.
iOS/Android screenshot capture is normal verification evidence and is always default-allowed; it must not by itself trigger `ask_user`.
If one real device is connected, CodeAutonomy should announce that it will use that device by default. If multiple real devices are connected, CodeAutonomy must ask the user to choose the device. If no real device is connected or the real device is unavailable, CodeAutonomy should try simulator/emulator verification by default; ask_user only if no runnable simulator/emulator path exists and the remaining fallback would be static-only or otherwise sensitive.
The Brainstorm decision bundle MUST set
`decisionBundle.runtimeProofRequired = "yes"` and `decisionBundle.taskType` to
the matching client platform (`ios`, `android`, or `dual`). The
pre-implementation `ask_user` is required for the normal one-shot implementation decision bundle, for multi-device target selection, for static-only/no-runnable-target fallback, or when separate sensitive actions need authorization. In those cases it MUST surface, in one bundle:

0. An explicit prompt for the user to review the key planning artifacts before
   approving — above all `Requirements.md` and `TestCases.md`, plus
   `Brainstorm.md` and `Plan.md`. State plainly that a wrong requirement or test
   design sends the whole route off course and wastes all later development and
   verification, so this review is the cheapest place to catch a misaligned
   direction;
1. Requirements summary (goal / scope / non-goals);
2. Required `TC-*` IDs and their runtime levels;
3. Optional or skipped `TC-*` IDs and the reason for each (especially any
   runtime/device-level TC that was demoted to optional);
4. Why real-device verification is or is not used. If proposing to skip
   real-device verification, name the concrete blocker (no device connected,
   signing not available, user opted out, etc.) and the fallback evidence
   path;
5. Environment authorization items the user must grant (device trust,
   Developer Mode, USB debugging, signing identity, network/VPN, sensitive
   permissions). If none, say "none required";
6. A heads-up that the host coding agent's command-execution policy may
   interrupt the run: commands outside the agent sandbox or high-risk commands
   can prompt the user for approval. Ask the user to stay available to approve
   them promptly, or to grant the needed permission/approval mode up front, so
   the loop is not blocked midway.

Only `decisionBundle.runtimeDowngradeApproval` with both `approvedBy` and
`approvedAt` (plus a `reason`) lets a `runtimeProofRequired=yes` task finish
without a runtime/device-level required TC pass. `workflow-check` rejects an
unapproved downgrade as an issue (not a warning), and `completion-check` rejects
a finish without runtime/device-level evidence and without an approved downgrade.

### Real-device unavailable phrasing interpretation

When the user literally says `真机不可用`, `没真机`, `无可用真机`, "real device
unavailable", "no real device", or equivalent, treat it as a real-device
unavailable signal, not as a task hard-stop by itself. CodeAutonomy should try
simulator/emulator verification by default and record the fallback reason in the
pre-implementation review / decision bundle. Do not mark runtime-required cases
as optional or `not_run` and still claim pass. Route to `ask_user` only when no
runnable simulator/emulator path exists and the remaining fallback would be
static-only/manual, or when separate sensitive actions are required.

Use `replan` when the current artifacts cannot be made coherent without changing
requirements, verification strategy, or task scope. During Brainstorm,
Requirements, TestCases, and Plan refinement, prefer self-discovery and replan /
refine over scattered `ask_user` interruptions. The normal user-facing decision
point is one bundled pre-implementation review question; ask earlier only for an
immediate safety/system/external-dependency blocker that cannot be deferred.

Gate validation:

- `workflow-check` fails if the decision is missing;
- `ask_user` must set runtime state to `human_input_pending` / `nextAction=ask_user`;
- Generator must not edit product/runtime code while the gate is unresolved.

## 6. Requirements and acceptance criteria rules

`Requirements.md` is the single-file source of truth (preferred for new tasks).
It defines requirement units `Rxx` and, immediately after each Rxx, the
acceptance criteria `AC-xxx` for that requirement. The
single `Requirements.md` contract — the
workflow accepts either form.

Keep each `Rxx` small enough to test and map it to the user's goal. Each
required AC must be:

- specific enough to assert;
- linked to one or more `Rxx` IDs (in the single-file form this link is
  positional — the AC sits under its parent Rxx);
- mapped to one or more `TC-*` cases;
- clear about whether server/device/human evidence is required;
- updated before changing a validation target.

The AI Phase 2 Refiner must actively decompose the user request into multiple
fine-grained `Rxx` (and their `AC-xxx`), not restate the request as a single
coarse requirement. The deterministic scaffold only splits the request weakly
and often leaves it almost verbatim; a near-verbatim single `Rxx` produces too
few TestCases and weakens verification depth. `workflow-check` blocks a
non-trivial implementation task that is still on the unrefined scaffold
(`planner.mode=deterministic_scaffold` without `artifactsRefined=true`).

Do not let hidden assumptions live only in chat. Put them in `Brainstorm.md`
or `Requirements.md`.

`requirements.json` is a compact machine sidecar, not a copy of
`Requirements.md`. Store ids, required flags, verification methods, hashes, and
`sourceRef` pointers; do not duplicate long Markdown prose.

## 7. TestCases rules

Required functional cases must be concrete runbooks. Each required functional
row must specify:

1. preparation/preflight/preconditions;
2. command, CodeAutonomy helper, or action sequence;
3. expected assertion/result;
4. evidence path or evidence type;
5. dependency and required flag.

For web/client/server projects, include dependency preparation explicitly only
when the selected runbook needs it. Before selecting compile/build/install/test
commands, inspect the project workspace for existing runbooks and scripts:
`README*`, project docs, CI workflows, `scripts/`, `tools/`, `bin/`, `Makefile`,
Gradle wrapper/tasks, package-manager scripts, Fastlane lanes, and other
repo-local helpers. Prefer project-native scripts when they satisfy the selected
TestCase; if an existing script is ignored, record the reason in `TestCases.md`
or `Plan.md`. Prefer the model's reading of project docs, CI, lockfiles, and
`Reuse.md`. If the dependency path is unclear, use `automind dependency-check
[task-code] [iteration]` as an optional read-only aid to discover package
managers, lockfiles, and candidate commands. TestCases should prefer
lockfile-first setup such as `npm ci`, `pnpm install --frozen-lockfile`, `yarn
install --immutable`, `uv sync --frozen`, `poetry install --sync`, Gradle/Maven
wrapper commands, or the repo's documented command. Do not turn target project
dependencies into CodeAutonomy helper venvs; only Android/iOS/visual helper packages
use `setup-automation-tools`.

For App/UI/client-facing work, include build/install/deploy/start, launch/open,
entry page/screen/route/activity/state, action sequence, assertions, and
evidence. Static-only checks are not sufficient for required functional behavior
unless dynamic execution is explicitly impossible/unsafe and the blocker or
human-confirmation route is recorded. `completion-check` enforces that required App/UI/runtime cases have attached
hard evidence and a positive Evaluator/model `evidenceAssessment`; do not design
a required App/UI case that can pass from source inspection alone.

For client/app behavior changes, the baseline runtime verification obligations
must be expressed as required `TC-*` rows, not only in prose. The required TC set
should cover, either as separate rows or an explicitly combined end-to-end row:

- project-native build/package evidence;
- install/deploy/start evidence when the app must run on a device, emulator,
  simulator, browser, or desktop runtime;
- launch/open evidence for the target entry page/screen/route/activity/state;
- action-flow evidence for the changed user/runtime behavior;
- assertion evidence proving the target UI text/selector/state/log/event/output;
- runtime artifacts such as screenshot, UI hierarchy/accessibility dump, logcat
  or device/app logs, probe-flow summary, test report, `.xcresult`, or equivalent.

If one baseline item is intentionally not required, the relevant TestCase or
Plan entry must say why. If it is required but blocked, the TestCase result must
route to `ask_user`/`replan`/`blocked`; it must not be marked pass from source
inspection or an environment blocker.

For release/merge or explicit build-confidence work, include a required
clean-build testcase with attached project-native build evidence and a positive
Evaluator/model `evidenceAssessment`. Build blocker classification may route to
retry/replan/ask_user, but it must not satisfy the required clean-build testcase.

For mobile App/UI tasks, do not say CodeAutonomy cannot operate the app merely
because a click, input, scroll, page transition, or popup close is needed. Plan a
reviewable automation path first: Android `android-probe-flow` for
`tap`/`tap_if_present`/`input`/`swipe`/assertions; iOS XCUITest or
`ios-probe-flow`/`ios-probe-flow-materialize` for `tap`/`tap_if_present`/`input`/
`scroll`/assertions. If the app UI hierarchy or selector is unknown, add a
discovery step using screenshots, UI hierarchy/accessibility, logs, or a dry-run
probe-flow, then refine the action plan. Use `ask_user` only for sensitive or
destructive actions, ambiguous low-confidence actions, missing device/signing
permission, or when no executable UI automation path can be created.

Use [`references/test-design-guide.md`](references/test-design-guide.md) for
concrete runbook examples, visual assertion guidance, external sink/side-effect
examples, and functional/quality batch policy.

`testcases.json` is the compact proof-design contract. It should store TC ids,
AC refs, runtime level, required flag, concise action/assertion/evidence fields,
and `sourceRef` pointers; it must not duplicate long `TestCases.md` runbook
prose.

## 8. Plan rules

`Plan.md` is the short-term progress tracker. It must contain:

- first functional batch;
- project script/runbook discovery result for compile/build/verification command
  choice, including any matching script that was selected or intentionally not
  used;
- concrete verification command/tool path or explicit blocked route;
- implementation checklist with `T*` rows owned by Generator;
- verification checklist with `TC-*` rows owned by Evaluator;
- fallback/replan notes for known blockers;
- reuse decision when `Reuse.md` contains matching `Successful path:` or
  `Avoid path:` entries.

Generator updates the Implementation Checklist. Evaluator updates the
Verification Checklist or completion evidence. `automind status` summarizes both;
do not use chat memory as the progress ledger.

`plan.json` is a compact checklist/status/ref contract. It must not duplicate
long `Plan.md` prose; downstream phases should use ids and refs for context.

## 9. Reuse rules

At task start and before choosing verification commands, read `Reuse.md`.
Prefer a matching `Successful path:` when scope and preconditions match. Avoid a
matching `Avoid path:` unless current evidence proves conditions changed. If the
Planner ignores relevant reuse memory, record why in `Plan.md` or `TestCases.md`.

## 10. `workflow.json` executable contract

`workflow.json` is the machine-readable orchestration contract that carries
Phase 2 decisions through the rest of the harness loop. Markdown remains the
human review surface. Phase JSON sidecars (`brainstorm.json`,
`requirements.json`, `plan.json`, `testcases.json`, and later `delivery.json` /
`completion-report.json`) capture each phase's minimum input/output schema, while
`workflow.json` indexes those sidecars, phase gates, testcase policy, and runtime
policy so deterministic gates and future platform adapters can progress without
re-parsing prose.

Creation and ownership:

- It is materialized after `Requirements.md`, `TestCases.md`, and `Plan.md` are
  generated/refined, before Generator edits product/runtime code.
- `workflow-check` may refresh it from the current Phase 2 artifacts and then
  validate it for drift.
- If its contract fields need to change (required TC, runtime level,
  verification target, skip policy), the task should go back to Phase 2 replan
  or pre-implementation user review, not silently mutate during Verify.

Minimum responsibilities:

- preserve phase graph input/output refs, schemas, and gate status;
- preserve `Rxx` / `AC-xxx` / `TC-*` mappings;
- encode each testcase's `runtimeLevel`, `required` flag, executor, command, and
  platform-neutral `intent` actions/assertions;
- encode `runtimeProofRequired`, `verificationTarget`, and
  `runtimeDowngradeApproval`;
- encode skip policy for runtime/device testcases. When
  `runtimeProofRequired=true`, runtime/device skip requires explicit user approval via `approvedBy` + `approvedAt` + `reason`.

Platform unification rule:

- iOS and Android are unified at the `workflow.json` / testIntent level:
  launch/open, tap/click/input/scroll, assert UI/log/event/state, collect
  screenshot/hierarchy/log/report evidence.
- Platform-specific differences stay in adapters: Android materializes to
  `probe-flow.android.json` / `android-probe-flow`; iOS materializes to
  `probe-flow.ios.json`, `action-plan.ios.json`, XCUITest, or project-native
  `xcodebuild test`.

## 11. Workflow-check handoff

Run:

```bash
<automind> workflow-check <task-code>
```

before Build when the CLI is available. The gate checks:

- required artifacts exist and are non-empty;
- `Rxx/AC-xxx -> TC-* -> Plan -> workflow.json` continuity;
- required functional testcases have concrete runbook parts;
- App/UI tasks include runtime/UI-flow verification decisions;
- visual tasks include measurable proof or fallback;
- pre-implementation review state is valid;
- a non-trivial implementation task is not still on the deterministic scaffold
  (`planner.mode=deterministic_scaffold` without `artifactsRefined=true`); the AI
  Phase 2 Refiner must run first so the user request is decomposed into
  fine-grained `Rxx`/`AC-xxx` instead of a near-verbatim copy that yields too few
  TestCases;
- existing evaluator context, if present, is isolated and valid;
- temporary verification unblock records, if present, are structured.

If it fails, refine Phase 2 artifacts and run it again. If the task cannot be
made coherent, set `evaluation.json.nextAction=replan` or `ask_user` with a
specific question.

## 12. Handoff to Build

Build may start only when:

- `Brainstorm.md`, `Requirements.md`,
  `TestCases.md`, `Plan.md`, and derived `workflow.json` are coherent;
- pre-implementation review is `auto_proceed` or resolved by the user;
- `workflow-check` has no hard issues;
- required verification path is runnable, or blocked with `ask_user`/`replan`
  and therefore not ready to be treated as a passed required testcase.

Then Generator implements or repairs product/runtime code against `Plan.md` and
must write `Delivery.md` before final Verify.
