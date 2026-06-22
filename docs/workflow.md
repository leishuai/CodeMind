# AutoMind Workflow

AutoMind is a recoverable, evidence-driven harness loop for coding agents. It
keeps requirements, implementation, verification, completion, and reusable
lessons connected through files under `.automind/tasks/<task>/`.

## 1. Canonical flow

```text
User request
  -> Brainstorm.md
  -> Requirements.md

  -> TestCases.md
  -> Plan.md
  -> workflow.json
  -> workflow-check
  -> Generator
  -> Delivery.md
  -> Evaluator
  -> Validation.md + evaluation.json
  -> completion-check
  -> Retry / Replan / Ask Human / Finish
  -> Summary / Reuse
  -> Report.html
```

Minimal execution protocol:

```text
Prepare -> Plan -> Build -> Verify -> Finish
```

This is a state machine, not a loose suggestion. Each phase has required
artifacts and a gate before the next phase.

New tasks use single-file `Requirements.md` (Rxx with inline AC-xxx) so the
Requirements.md is the single source of truth. New tasks must not generate `Spec.md` or `Require.md`; `workflow-check`
auto-detects either form and validates the requirement/AC continuity.

`workflow.json` is the derived executable contract, not an authoring surface.
Humans and agents edit `Brainstorm.md`, `Requirements.md`, `TestCases.md`,
`Plan.md`, and review live control state in `automind-workflow-state.json` and runtime/resume details in `runtime-state.json`; `workflow-check` materializes
and validates the machine projection consumed by deterministic gates and
platform adapters.

## 1.1 Per-TestCase reflection budget

An AutoMind `iteration` is one complete Generator -> Evaluator attempt, not one testcase and not one shell command. A single iteration may run a selected batch of multiple TestCases and then write `evaluation.json`, `Validation.md`, and `logs/iter-N/*`.

The total task loop is bounded by `AUTOMIND_MAX_ITERATIONS` (default `1000`). Individual TestCases are bounded by `AUTOMIND_MAX_REFLECTIONS_PER_TC` (default `10`): when a failing/blocked evaluation references a `TC-*` id in `testResults[]` or `failedChecks[]`, AutoMind increments that TC's reflection count once for that iteration. Attempt-level command retries inside one evaluator run do not count as new iterations or new TC reflections.

When a TC reaches the per-TC reflection limit, AutoMind stops normal `retry_generator`/`replan` churn and asks for a human strategy decision using `askUserQuestion.category=repeated_same_failure`. The model should still judge whether to retry, repair, replan, or ask earlier; the budget is only a hard safety backstop.


## 2.1 Workflow control state

AutoMind now separates **workflow control state** from artifact contracts.
The agent/runtime-facing control files are:

```text
.automind/tasks/<task>/automind-workflow-state.json
.automind/tasks/<task>/automind-workflow-events.jsonl
.automind/tasks/<task>/stages/initialization-stage-state.json
.automind/tasks/<task>/stages/requirement-stage-state.json
.automind/tasks/<task>/stages/verification-loop-stage-state.json
.automind/tasks/<task>/stages/summary-stage-state.json
```

`automind-workflow-state.json` is the live control state. It answers only:
current stage, current phase, current action, current owner, next action,
next phase, planned next phase, iteration, state health, and the last state
change event. It does not contain checklist items, outputs, evidence, reports,
or human-facing display summaries.

`automind-workflow-events.jsonl` is the append-only transition log. State changes
must be recorded as events first, then applied to the active stage state and the
workflow state. If the stage state and workflow state drift, AutoMind reconciles
from the latest valid workflow event and continues; state drift is not an
`ask_user` reason. When no usable event exists, reconciliation rebuilds from the
last known good phase (`workflow_state._last_known_good_phase`, which scans
workflow state -> runtime state -> events for the first non-`task_setup`
canonical phase) instead of falling back to `task_setup`; falling back to
`task_setup` would otherwise reset the iteration counter and lose loop progress.

Stages map to the existing AutoMind macro docs:

| stage | macro guide | phases |
|---|---|---|
| `initialization` | `docs/phase1-initialization.md` | `task_setup`, `context_load`, `environment_readiness` |
| `requirement` | `docs/phase2-requirement.md` | `brainstorm`, `requirements`, `testcases`, `plan`, `pre_implementation_review` |
| `verification_loop` | `docs/phase3-verification.md` | `delivery`, `evaluation` |
| `summary` | `docs/phase4-summary.md` | `completion` |

The `verification_loop` stage owns iteration details. In workflow control state,
`iteration` means the active or next Generator -> Evaluator attempt being routed.
`evaluation.json.iteration` may describe the attempt that just produced an
Evaluator result; for a retry route, `automind-workflow-state.json.iteration`
therefore advances to the next delivery attempt. `verification-loop-stage-state.json`
keeps the detailed iteration object for the current delivery/evaluation loop.

Skill-mode checklists remain in phase guides and the exported skill. They are
linked through `PHASE_REGISTRY[currentPhase].checklistRefs`; checklist text is
not copied into workflow state JSON.

Phase names have one internal vocabulary and one display vocabulary. Internally
AutoMind reasons in canonical phase names (`plan`, `completion`,
`pre_implementation_review`, ...). Every human/CLI/skill-facing label is
converted through the single helper `workflow_state.display_phase`, which maps
canonical names to the legacy display words (`planning`, `terminal`,
`human_input`, ...) via `DISPLAY_PHASE_MAP`. Do not hand-format phase labels in
output code; when a new phase is added, update `DISPLAY_PHASE_MAP` so the display
vocabulary stays single-sourced.

## 2. `workflow.json` as orchestration contract

AutoMind's main workflow is driven by `workflow.json`. Markdown remains the
human/agent authoring surface, but every phase also has a minimum JSON sidecar.
`workflow.json` indexes those sidecars and tells deterministic gates what each
phase consumes, produces, and whether it can advance.

Artifact model:

```text
Brainstorm.md     + brainstorm.json
Requirements.md   + requirements.json
Plan.md           + plan.json
TestCases.md      + testcases.json
Pre-impl review   + pre-implementation-review.json
Delivery.md       + delivery.json
Validation.md     + evaluation.json
completion-check  + completion-report.json
                  + workflow.json
```

`workflow.json` is derived, not hand-authored. If a phase changes, rerun
`workflow-check` to refresh sidecars and the workflow contract.

Each sidecar has:

- `version`;
- `phase`;
- `sourceRefs`;
- minimum structured fields for the phase output.

Schemas live in `schemas/`:

- `workflow.schema.json`;
- `brainstorm.schema.json`;
- `requirements.schema.json`;
- `plan.schema.json`;
- `testcases.schema.json`;
- `pre-implementation-review.schema.json`;
- `delivery.schema.json`;
- `completion-report.schema.json`;
- existing runtime schemas such as `evaluation.schema.json` and
  `probe-flow.schema.json`.

`evaluation.json` keeps its existing Evaluator-owned schema and is not
synthesized before the Evaluator runs.

### Skill-mode JSON handoff rule

In CLI-owned mode, AutoMind refreshes these sidecars automatically. In Skill or
slash-command current-session mode, the host agent must still use the same JSON
contracts as the handoff spine:

- read `workflow.json` before choosing the next action;
- read upstream phase sidecars before editing a downstream Markdown artifact;
- after a phase artifact changes, run `workflow-check` when available so the
  sidecar and derived `workflow.json` are refreshed and schema-checked;
- treat hard `workflow-check` / schema issues as blocking, not as advisory text;
- use `evaluation.json.nextAction` and `completion-report.json` as structured
  control signals instead of chat/prose confidence.

If the full CLI is unavailable, Skill-only agents should preserve the same file
shapes manually and record any missing schema/checker coverage as a limitation in
`Validation.md` or the final handoff.

### Skill-mode loop driver protocol

Skill mode lacks an orchestrator-owned while loop, so the host agent must act as
a lightweight loop driver:

1. Start or resume with `continue`/`status`/`workflow.json` to identify the next
   phase and any pending user answer/message.
2. Execute exactly one phase action: refine Plan, run Generator, run Evaluator,
   run `completion-check`, or ask the user only for an allowed blocker.
3. After every CLI helper/check/evaluator result, refresh/read
   `automind-workflow-state.json` as the workflow control state and
   immediately perform the next safe action. Local signals such as
   `evaluation.json.nextAction`, `workflow-check`, `completion-report.json`, and
   runtime-state projection feed the resolver; they are not competing
   macro truths.
4. Stop only on green `completion-check`, allowed `ask_user`, max-iteration
   escalation, explicit user pause/abort, or non-recoverable unsafe condition.
5. If a step fails because artifacts are inconsistent, go back to the phase that
   owns the broken artifact; do not ask the user to drive the loop manually.

`workflow.json` contains only the derived contract/gate surface:

- task identity and type;
- `phaseGraph` with start/final nodes, node list, and edges;
- `expectedNext` and `target` so deterministic gates know which contract node(s)
  are expected next and what final result must be reached;
- phase nodes with `guideRefs`, `artifactRefs`, `inputRefs`, `outputRefs`,
  dependency status, `checker`, `schema`, and gate result;
- testcase/runtime proof contract for platform adapters and completion gates.

Do not use `workflow.json` as the live task status card. Current/next phase, owner, next action, planned next phase, iteration, and state health belong in `automind-workflow-state.json`. `stages/*-stage-state.json` carries stage-local control payloads. `runtime-state.json.stateSummary` is obsolete fallback only, not a second source of truth.

Example phase node:

```json
{
  "id": "testcases",
  "cluster": "phase2-verification-execution-planning",
  "status": "ready",
  "guideRefs": {
    "workflow": "docs/workflow.md",
    "macro": "docs/phase2-requirement.md",
    "phase": "docs/phases/testcases.md"
  },
  "artifactRefs": {
    "markdown": "TestCases.md",
    "json": "testcases.json",
    "schema": "schemas/testcases.schema.json"
  },
  "inputRefs": ["Requirements.md", "requirements.json", "TestCases.md"],
  "outputRefs": ["testcases.json"],
  "dependencies": {
    "inputRefs": ["Requirements.md", "requirements.json", "TestCases.md"],
    "outputRefs": ["testcases.json"],
    "missingInputs": [],
    "missingOutputs": [],
    "ready": true
  },
  "blockedBy": [],
  "schema": "schemas/testcases.schema.json",
  "checker": {
    "name": "testcases_contract",
    "result": "pass",
    "issues": [],
    "warnings": []
  },
  "next": ["plan"],
  "gate": {
    "required": true,
    "result": "pass",
    "issues": [],
    "warnings": []
  }
}
```

### Phase hooks

AutoMind may run deterministic hooks before and after phase nodes. The hook mechanism is generic: it can prepare reuse context, write phase-learning cards, run future policy checks, or attach project preflight hints. The current MVP writes `phase-reuse/<phase>.md` before key phases and `logs/phase-learnings/<phase>.json` after phases.

`Reuse.md` remains as a compact task-level manifest: whole-task policy plus index pointers only. It points to relevant knowledge-index entries and `phase-reuse/<phase>.md` files; the actual phase-specific detail (matched values, successful/avoid paths, important reminders) lives in `phase-reuse/<phase>.md`, not duplicated here. Long knowledge lives in `index.jsonl + raw/**`; agents should not receive large historical dumps by default.

#### Reuse acknowledgement gate (read reuse before you act)

To prevent agents from verbally claiming "I considered reuse" without turning it into action, the before-phase hook for gated phases (`generator`, `evaluator`) computes a machine-checkable `runtime-state.json.reuseGate.<phase>` descriptor: `required`, matched `safePaths`/`avoidPaths`/`reminders`, a `repeatedFailure` classification (signing / device / build / repeated-same-failure), `acknowledged`, and the recorded `acknowledgement`. Entering a phase (including each retry/re-verify iteration) resets `acknowledged=false`, so reuse must be re-read every turn.

The agent records the acknowledgement with `automind reuse-ack <task-code> <phase> --read --applied "<paths used>" --ignored "<paths skipped and why>"`. `workflow-check` enforces this as a hard gate: it raises an issue (failing the check) when a gated phase is about to run with `acknowledged != true` / `phaseReuseRead != true`. For a detected repeated-failure / signing / device / build category, if the loop is escalating to `ask_user` while matched `safePaths` exist and none were applied, `workflow-check` also fails — the agent must exhaust safe reuse paths (reuse signed app when business code unchanged, `devicectl install`/`launch`, avoid `idevicescreenshot`, avoid unnecessary full builds, classify the issue) or record why each was insufficient before interrupting the human. `ask_user` is reserved for the remaining genuinely-sensitive steps (login, keychain, certificate/profile change).

### Document layers

The documentation is intentionally layered from broad to specific:

1. `docs/workflow.md` and `workflow.json` define the global workflow contract
   and gate surface; `automind-workflow-state.json` resolves live control state; `stages/*-stage-state.json` carries stage-local control payloads; `runtime-state.json.stateSummary` is obsolete fallback only.
2. `docs/phase1-initialization.md` through `docs/phase4-summary.md` define
   macro phase clusters. They group related concrete nodes by AutoMind's big
   process stages and should stay as macro guidance.
3. `docs/phases/*.md` files define concrete phase-node guides. When entering a
   node, the agent should read the macro guide plus that node's concrete guide.

This keeps AutoMind readable from top to bottom without forcing every phase rule
into the global workflow document.

### Progress and blockers

`automind-workflow-state.json` is the compact control state for long-running
automation. The workflow control state should answer:

- what concrete phase is current (`currentPhase`);
- who owns the work (`currentOwner`, for example planner/generator/evaluator/human);
- which phase/action/owner comes next (`nextPhase`, `nextAction`, `nextOwner`);
- why that route was chosen (`reason`, `basis`);
- what checklist/blocking work remains before handoff.

`workflow.json` stays as the derived contract/gate surface. It should not carry
root-level `currentPhase`, `current`, `overallStatus`, `progress`, `blockedBy`,
`pendingUserAction`, or runtime `execution` snapshots.

### Pre-implementation review node

`pre_implementation_review` sits between `plan` and `delivery`. It is the
explicit implementation gate and may output `auto_proceed`, `ask_user`, or
`replan`. There is no separate `ask_user` phase: `ask_user` is an action emitted
by this node or by later verification nodes when a human/system decision is
needed.

### State/action quick reference

AutoMind uses action values in different control layers. Keep them separate:

- Pre-implementation review decisions: `auto_proceed`, `ask_user`, `replan`.
- Evaluation/loop `nextAction` values: `finish`, `retry_generator`, `replan`,
  `ask_user`, `stop`, `stop_blocked`, `pause_for_external`.
- Runtime-state scheduler actions include internal resume targets such as
  `run_test_planner`, `run_generator`, `run_evaluator`, and `generate_summary`.

`automind-workflow-state.json` and `stages/*-stage-state.json` should be read first for workflow routing; fall back to `runtime-state.json.stateSummary` only for older tasks.
`runtime-state.json` should be read as runtime
projection fields (`status + currentOwner + nextAction`) only:
`status` describes the task condition, `currentOwner` says who owns the next
work, and `nextAction` is the route action. For the full enum table and common
route mappings, see [`references/state-actions.md`](references/state-actions.md).

`ask_user` is therefore an action, not a phase node. Ask-user routing is recorded
in `automind-workflow-state.json` and task-local answer artifacts;
`runtime-state.json.stateSummary` is obsolete fallback only. Do not encode
ask-user routing as root-level `workflow.json` status fields.

### Gate policy for the workflow contract

`workflow-check` refreshes and validates:

1. Phase sidecars for Brainstorm, Requirements, Plan, and TestCases.
2. `workflow.json` phase nodes and testcase/runtime policy.
3. Existing cross-artifact continuity: R/AC/TC/Plan/evaluation.
4. Pre-implementation hard gates.

Generator must not run while a required pre-generator phase gate fails.

#### Skill/command mode gate relationship

`workflow.md` remains the canonical workflow policy: phases, ownership, required artifacts, and route-back rules are defined here. `phase-gate` is not a replacement for the workflow; it is a lightweight script gate for skill/slash-command mode, where the host coding agent drives the loop and needs a deterministic handoff check.

Use `phase-gate` at phase handoff boundaries:

```bash
<AUTOMIND_CLI> phase-gate <task-code> auto
<AUTOMIND_CLI> phase-gate <task-code> build
<AUTOMIND_CLI> phase-gate <task-code> verify
<AUTOMIND_CLI> phase-gate <task-code> finish
```

`phase-gate` refreshes/seeds `automind-workflow-state.json`, reads `workflow.json` / `runtime-state.json` / evaluator and completion outputs as local signals, and returns a pass/fail JSON handoff decision. When the handoff is about to enter `delivery` or `evaluation`, it also performs a conservative deterministic refresh for missing/stale `phase-reuse/generator.md` or `phase-reuse/evaluator.md` and exposes the result in `phaseReuseRefresh`. It does not reset fresh acknowledged reuse. If `phase-gate` fails, follow its `requiredCommand` and keep using this workflow's owning phase to repair the blocked artifact.

CLI/TUI-owned loops call internal before/after phase hooks automatically. In skill/command mode, `phase-gate` covers the key execution-phase reuse refresh, and the host coding agent should use the returned checklist: copy `checklist[]` or `checkboxMarkdown[]` into its native TODO/checkbox plan, complete items one by one, then rerun `phase-gate` for the next handoff. This keeps phase discipline visible without adding a separate skill-only hook command. The detailed checklist guidance lives in [`references/skill-command-driver-checklist.md`](references/skill-command-driver-checklist.md).

Checklist items are ordered and deliberately simple. They should cover the critical phase flow — read inputs/reuse, do the phase work, write/update required artifacts and sidecars, capture evidence/logs when relevant, and rerun the gate — without becoming a second state machine.

`completion-check` remains the final finish gate. It uses the same testcase and
runtime policy carried by `workflow.json`/`testcases.json` and checks required
TCs, AC coverage, evidence paths, and runtime proof.

### Extension rule

New phases or platform adapters should add a small sidecar schema first, then add
a phase node to `workflow.json`. Do not expand `workflow.json` into a giant copy
of every artifact; keep it as the orchestration index and policy projection.

### Artifact redundancy policy

Markdown files are the authoring surface: they carry the full human/agent
reasoning, rationale, and reviewable contract. JSON sidecars are compact machine
contracts: ids, refs, gates, status, and schema-friendly fields. They must not
duplicate long Markdown prose; use `sourceRef`, path, section, hash, and compact
summaries instead.

`workflow.json` is the phase graph / refs / gates / runtime-test policy
projection, not a copy of every phase artifact and not a live status card.
`automind-workflow-state.json` is the workflow control truth for current/next
phase decisions and ask-user routing. Checklist text remains in phase guides/skill TODOs. The rest of
`runtime-state.json` is the mutable runtime projection/resume cache.
`events.jsonl`, `trace-spans.jsonl`, and raw logs are cold audit/debug artifacts
and should not enter default context packs.

`summary.md` and `Report.html` are final handoff artifacts by default. They must
not be generated or refreshed during normal Generator/Evaluator iterations, must
not drive routing, and must not reopen a finished task. Final handoff should
tell the user in natural language that the task is complete, which reports were
generated, to open `Report.html` first, and which runtime proof / log / ledger
artifacts matter most.

## 3. Phase chain and route-back contract

AutoMind is a repair loop, not a one-way pipeline:

```text
Plan / workflow-check
  -> Generator
  -> Evaluator
  -> completion-check
      -> finish
      -> retry_generator -> Generator
      -> replan -> Planner
      -> ask_user -> Human
      -> stop
```

Evaluator owns the primary repair route. When implementation, Delivery, or
evidence is incomplete but Requirements and TestCases remain valid, Evaluator
sets `evaluation.json.nextAction=retry_generator`, which maps back to Generator.
`completion-check` owns the final finish gate: it validates claimed finish and
may override a false finish when required TC/AC/evidence coverage is not proven.

| Phase | Owner | Hard inputs | Hard outputs | Gate / next route |
|---|---|---|---|---|
| Prepare | current agent / CLI | user request, workspace | task directory, `automind-workflow-state.json`, `runtime-state.json`, `Reuse.md` | task dir is under target workspace |
| Plan | Planner/Refiner | request, reuse, project discovery | `Brainstorm.md`, `Requirements.md`, `TestCases.md`, `Plan.md`, sidecars | pre-implementation review + `workflow-check` |
| Build | Generator | `workflow.json`, Plan/TestCases/Requirements, latest evaluation if retry | product/runtime changes, `Delivery.md`, `delivery.json`, implementation checklist | hand off to Evaluator |
| Verify | Evaluator / verifier | Delivery, required TC list, runtime target | `Validation.md`, `evaluation.json`, evidence logs, verification checklist | `finish`, `retry_generator`, `replan`, `ask_user`, or `stop` |
| Completion | AutoMind | `evaluation.json`, TestCases/Requirements, evidence | `completion-report.json`, `VerificationLedger.json` | pass -> `finished/finish`; fail -> route by gaps/evaluation |
| Finish | AutoMind / current agent | terminal or durable paused state | `summary.md`, reuse memory, `record-check`, `Report.html` | final handoff only |

## 4. Hard gates

AutoMind has three hard gates:

1. **`workflow-check` before Build**: requirements, AC, testcases, plan,
   the derived `workflow.json` executable contract, review decision, reuse,
   and evaluator context must be coherent. `workflow-check` refreshes
   `workflow.json` from the current Phase 2 source artifacts and validates it
   for drift before Generator edits product/runtime code.
2. **`Delivery.md` before final Verify**: Evaluator must see what Generator
   changed and which `TC-*` / `AC-*` are targeted.
3. **`completion-check` before Finish**: required testcases, acceptance criteria,
   evidence paths, runtime-evidence requirements, clean-build/release evidence,
   and temporary unblock changes must pass machine checks.

After completion passes, `summary` and `record-check` are mandatory before final
handoff. `record-check` also rejects stale human-readable report state, such as a
finished/pass machine state with `Validation.md` still marked `In Progress`. Do
not replace gates with chat confidence.

**Evidence ownership boundary:** Generator implements and may run sanity checks,
but required `TC-*` verdicts are owned by Evaluator. Evaluator should directly
collect or independently verify the necessary evidence and write structured
`evaluation.json.testResults[]`; `completion-check` consumes those structured
results rather than Generator narrative or loose logs. This evidence contract is
shared across Android, iOS, Web, server/CLI and script-command adapters: required
pass rows need concrete `evidence[]`, `observedSignals[]` when meaningful, and a
positive `evidenceAssessment` with `hardMetrics[].evidence` or `machineAnchor`
pointing at real artifacts.

### 4.1 Skill-mode continue-until-done protocol

Skill / `/automind` slash-command mode runs without an external orchestrator
loop, so the host agent itself must keep the harness moving. The protocol is:

1. After every gate or check (`workflow-check`, `completion-check`,
   `script-command`, Evaluator turn), parse the JSON `nextActionPrompt` field
   and obey it as a binding instruction. Do not paraphrase it into "let me
   know if you want me to continue".
2. The only legal stop conditions are: `completion-check result=pass`;
   `evaluation.json.nextAction=ask_user` with `askUserQuestion.category` in the
   5-item whitelist (`unauthorized_destructive_or_sensitive`,
   `system_or_external_dependency`, `real_device_or_signing`,
   `manual_visual_confirmation`, `repeated_same_failure`); or `tick-iteration`
   exited non-zero (budget exhausted).
3. Before each Generator/Evaluator turn, call
   `<AUTOMIND_CLI> tick-iteration <task-code> <phase>` to enforce
   `AUTOMIND_MAX_ITERATIONS`.
4. `scaffold` and `ask` write `.automind/current-task`; `completion-check`
   clears it on pass; `<AUTOMIND_CLI> continue` reads it and returns resume
   context with a `nextActionPrompt`. Use this marker (env override
   `AUTOMIND_CURRENT_TASK` first) instead of relying on chat memory for the
   active task code.
5. Any "task complete" / "all done" reply must be backed by a green
   `completion-check` in the same turn; otherwise treat the loop as still
   running.


## 5. Mandatory startup read order

At the start of every AutoMind task, read and apply:

1. `SKILL.md` in the exported skill package (if running as a skill), then
   `docs/workflow.md` itself for the canonical loop, gate definitions, and
   evaluation contract.
2. `docs/README.md` is the single index for every other document. Load deeper
   material on demand by phase: Phase 2 (`docs/phase2-requirement.md` +
   `docs/phases/demand-definition.md` +
   `docs/phases/verification-execution-planning.md` +
   `templates/phase2_planner_prompt.md`, with
   `docs/references/test-design-guide.md` for runbook examples), Phase 3
   (`docs/phase3-verification.md` + `templates/evaluator_prompt.md`),
   command/script choice (`docs/references/command-script-catalog.md`),
   platform/device/visual/external-sink verification
   (`docs/references/verification-flow.md`, plus
   `docs/references/verification-flow-ios.md` /
   `docs/references/verification-flow-android.md` on demand for that platform),
   Generator work
   (`templates/generator_prompt.md`), and adapter integration
   (`docs/agent-adapters.md`).
3. The active task's `Reuse.md` when present, for prior successful/avoid paths.

If context is constrained, read at minimum items 1 and 2's Phase 2/Phase 3 docs
plus the command-script catalog, and record which references must still be
consulted before choosing commands or verification strategy.

## 6. Workspace and runtime rule

AutoMind runtime and user workspace are separate.

- Run AutoMind helpers from the target project root.
- If shell cwd is not the target project root, set
  `AUTOMIND_WORKSPACE_ROOT=/path/to/project`.
- `TASK_DIR` must be under the target workspace:
  `.automind/tasks/<task>/`.
- `$AUTOMIND_HOME` or `$HOME/.automind/automind` is only the installed runtime
  location, not the task workspace.

At task start, discover the CLI in this order:

```text
automind help
./automind.sh help
$HOME/.automind/automind/automind.sh help
$AUTOMIND_HOME/automind.sh help
```

If no CLI exists, recommend full install; if installation is not allowed, follow
this workflow manually and use project-native verification.

During Plan/Verify, dependency setup is split by ownership:

- AutoMind may auto-create only its own low-risk helper venvs for
  Android/iOS/visual verification.
- Web/client/server project dependencies must use the target project's
  package manager, lockfiles, and documented scripts. When the dependency path
  is unclear, use `<automind> dependency-check [task-code] [iteration]` as an
  optional read-only discovery aid; it is not a workflow gate.
- System SDKs, signing, device trust, Docker/databases, browser drivers,
  private registry credentials, and privileged services require `ask_user`
  before installation or mutation.

## 7. Standard task files

Each non-trivial task should use:

```text
Brainstorm.md
Reuse.md
Requirements.md
TestCases.md
Plan.md
Delivery.md
Validation.md
evaluation.json
automind-workflow-state.json
automind-workflow-events.jsonl
stages/*-stage-state.json
runtime-state.json  # runtime/resume projection
VerificationLedger.json
summary.md
logs/iter-N/
```

Single-stage helper commands may use a smaller set, but must still write enough
records for status, evidence, and resume.

## 8. Plan phase rules

Phase 2 is model-refined planning. The Planner must:

- read `Reuse.md` and relevant summaries;
- proactively expand the user's request in `Brainstorm.md`;
- write `Requirements.md` as the canonical requirement contract with `Rxx` units and inline `AC-xxx` acceptance criteria;
- write executable `TC-*` runbooks in `TestCases.md`;
- create `Plan.md` with first functional batch, verification command/tool, and
  implementation/verification checklists;
- record pre-implementation review decision in `Brainstorm.md` and
  `runtime-state.json.planner.preImplementationReview`.

Before Generator edits product/runtime code, the review decision must be one of:

```text
auto_proceed | ask_user | replan
```

Unless the user explicitly requested full-auto/no-confirmation mode (for example
“one-stop/full auto”, “一站到底”, “全自动模式”, “不用问用户”, “不用确认”), non-trivial
implementation/behavior-change tasks must ask the user once in pre-implementation.
The one-shot decision bundle confirms whether the requirement is clear,
goal/scope/non-goals, assumptions, recommended approach, known risks,
verification direction, known must-pass AC/TC/evidence, rollback/replan
boundaries, and authorization for non-low-risk operations such as overwrite
install, uninstall/delete/reset, account login, signing/device trust changes,
privilege escalation, external upload, payment, or production-impacting actions.

Only client/app development or verification tasks need a real-device vs
simulator/emulator decision. For Android/iOS/mobile client tasks, follow the
user's stated verification target as the source of truth: if the request
already names real device, simulator/emulator, or both, AutoMind respects that
choice and does not re-ask, even when read-only physical-device discovery
detects a connected device. Only ask when the request leaves the verification
target unclear; in that case, perform read-only physical-device discovery and
include the verification target in the early decision: real physical device,
simulator/emulator, or both. If device(s) are connected, show the detected
device(s), recommend real-device verification, and ask only when multiple
connected real devices require target selection. If none are connected, say so
and explain that real-device verification requires connect/unlock/trust plus
Developer Mode/USB debugging and possible signing/permission prompts. Screenshot
capture is default allowed verification evidence and must not trigger a separate
ask_user. When exactly one connected real device is available, state in the
review bundle that AutoMind will use that device by default for development,
debugging, verification, and screenshots. When multiple connected real devices
are available, ask_user to choose the target device. When no authorized real
device is available or the real device is unavailable, AutoMind should try
simulator/emulator verification by default; ask_user only if no runnable
simulator/emulator path exists and the remaining fallback would be static-only
or otherwise sensitive.

For App/UI/client-facing tasks that require in-app behavior, AutoMind must treat
UI interaction as an available verification capability, not as impossible by
default. The Planner should encode app actions as reviewable TestCases and, when
needed, task-local `probe-flow.android.json` / `probe-flow.ios.json` (legacy
`probe-flow.json` is deprecated but still readable with a warning) or a
project-native
UI test. Supported actions include non-destructive dialog handling, tap/click,
input, scroll, navigation, launch/open, and assertions through Android
`adbutils`/`uiautomator2` probe-flow or iOS XCUITest/probe-flow materialization.
Do not random-click: every action must have an intent, selector or coordinate
justification, risk/authorization decision, post-action assertion, and evidence.
If selectors/UI hierarchy are unknown, first discover them with screenshot and
hierarchy evidence, then refine the probe-flow or ask/replan only when evidence
is insufficient or the action is sensitive/destructive. Startup/discovery flows
are path-finding evidence only; required App/UI/runtime TCs need proof actions
plus satisfied postChecks that observe the intended UI state, event, log, sink,
API response, or side effect. `completion-check` enforces hard evidence plus
`evidenceAssessment.verdict=proved` for required App/UI/runtime cases, so a
source-only or startup-only pass cannot finish.

For release/merge or explicit clean-build confidence, include a required build
case, attach the project-native build evidence, and make the Evaluator/model
record `evidenceAssessment.verdict=proved` only when that evidence truly proves
the clean-build TC. A build/device/tool blocker classification is routing
information; it cannot satisfy the clean-build gate.

Detailed Plan rules are in `docs/phase2-requirement.md`. Testcase examples are
in `docs/references/test-design-guide.md`.

## 9. Build phase rules

Generator owns product/runtime implementation and repair. It must:

- implement against `Plan.md`, `Requirements.md`, and `TestCases.md`;
- update the Plan Implementation Checklist (`T*` rows);
- write or update `Delivery.md` before final verification;
- record any temporary verification-unblock changes it created, such as reversible build/test/workspace fixes needed only to make verification runnable;
- repair implementation/product failures reported by Evaluator evidence, then hand the task back to Evaluator for re-verification.

Generator does not own final verification and must not claim finish without
Evaluator evidence and a passing completion gate.

### 9.1 Generator repair loop

When Evaluator/verifier reports `evaluation.json.nextAction=retry_generator`,
Generator becomes the next owner. Generator must read `Validation.md`,
`evaluation.json`, failed `TC-*` / `AC-*` coverage, and relevant logs/evidence;
repair the product/runtime code, config, docs, or task artifacts that caused the
failure; update `Delivery.md` and the Plan Implementation Checklist; then route
back to Evaluator/verifier. The normal loop is:

```text
Generator implement/repair
  -> Evaluator verify/re-verify with evidence
  -> evaluation.json.nextAction=retry_generator when implementation/product behavior fails
  -> Generator repairs from Evaluator evidence
  -> Evaluator re-verifies
  -> completion-check before finish
```

Generator should not re-label an Evaluator failure as finished. If evidence shows
the requirement, testcase, validation target, or approach is wrong, route to
`replan` instead of patching code blindly. If the failure is an environment,
device, signing, permission, sensitive-action, or external-dependency blocker,
route according to the table below rather than treating it as product code.

In detached Codex/Claude CLI mode, Planner/Generator/repair may reuse a
task-local primary implementation session so the implementation path and
Evaluator feedback remain connected. The session id is recorded under
`runtime-state.json.agentSessions.primary` when available. This reuse never
applies to Evaluator.

## 10. Verify phase rules

Evaluator owns verification, evidence, failure classification, `Validation.md`,
`evaluation.json`, and the Plan Verification Checklist (`TC-*` rows).

Preferred Evaluator order:

```text
1. Deterministic platform/script verifier
2. Native isolated subagent/session with context pack only
3. Fresh external agent CLI process with context pack only
4. Same-conversation role switch - not acceptable for independent evaluation
```

When a model Evaluator is used, create and validate a context pack:

```bash
<automind> context-pack <task-code> [iteration]
```

Evaluator must consume the context pack and must not read raw Generator logs or
hidden chat memory. `workflow-check` validates recorded evaluator context when
present.

Detailed verification rules are in `docs/phase3-verification.md`; platform,
visual, external sink, and unblock guidance is in
`docs/references/verification-flow.md`.

## 11. Verification unblock rule

If verification is blocked by unrelated build/test/workspace issues, AutoMind may
create minimal reversible verification-unblock changes only after checkpointing
or recording a diff. These are not product fixes; they are temporary changes
made only to make the verifier runnable, for example adjusting a local test
fixture, adding a missing test-only config file, or isolating an unrelated
workspace breakage so the selected `TC-*` can run. These changes must be listed
in `Delivery.md`, `Validation.md`, and
`evaluation.json.verificationUnblockChanges`, then restored or explicitly
promoted before finish. Active temporary unblock changes block completion.

## 12. Routing table

| Condition | Route |
|---|---|
| Planning artifacts fail `workflow-check` | refine Phase 2 or `replan` |
| Pre-implementation review needs user decision | `ask_user` |
| Generator completed but verification not run | run Evaluator/verifier |
| Product behavior fails and requirements/tests are valid | `retry_generator` |
| Validation target, requirements, or approach is wrong | `replan` |
| Device/signing/sensitive action/human visual confirmation is required | `ask_user` |
| Build/test/runtime fails but can plausibly be repaired or unblocked | keep trying via `retry_generator` or `replan`; do not accept it as pass |
| Environment/device/tool blocker prevents the selected required verification | `ask_user` only when human/system choice is needed; otherwise replan/repair and continue |
| External dependency (real device, signing material, third-party service) is unavailable and AutoMind cannot resolve it autonomously | `pause_for_external` (parks the task as `human_input_pending` so it can resume later when the blocker clears) |
| Destructive/policy/signing/non-recoverable failure that should NOT be retried | `stop_blocked` (legacy `stop` is treated as a synonym) |
| Max iterations are reached | `ask_user` for replan/fix/stop choice |
| Required TC/AC/evidence coverage passes | `finish` |
| Completion gate fails after `finish` signal | route back by gaps; do not finish |
| Runtime/agent interruption is recoverable | resume from persisted phase artifacts |
| Safe agent/runtime interruption (`agent_unavailable`, `agent_timeout`, `agent_stalled_no_output`, `agent_context_overflow`) | CLI/TUI-owned loops auto-resume from persisted artifacts up to `AUTOMIND_SAFE_AUTO_RESUME_MAX`; context overflow starts a fresh primary session |

`evaluation.json.nextAction` is the loop control signal. Valid values are:

```text
finish | retry_generator | replan | ask_user | stop | stop_blocked | pause_for_external
```

`stop` is treated as a legacy alias of `stop_blocked` (non-recoverable hard
stop). New evaluator output should prefer the explicit `stop_blocked` and
`pause_for_external` values so the orchestrator can route the task to the
right state (`status=failed` for hard stops, `status=human_input_pending` for
external pauses) without guessing.

## 13. Progress and resume

Use files, not chat memory, as the progress ledger:

- Generator updates `Plan.md` Implementation Checklist;
- Evaluator updates `Plan.md` Verification Checklist and evidence;
- `runtime-state.json` records owner, status, iteration, next action, and recovery
  metadata;
- `evaluation.json` records the latest verification decision;
- `automind status <task-code>` explains next action and gate status.

Interrupted tasks can resume from `runtime-state.json`, `evaluation.json`,
`Delivery.md`, `Validation.md`, and `logs/iter-N/*`. If interruption happened in
Evaluator after Generator output exists, resume Evaluator without rerunning
Generator unless new evidence requires repair.

## 14. Finish rules

Before claiming Finish:

```bash
<automind> completion-check <task-code>
```

The gate must prove:

- required `TC-*` cases passed;
- required `AC-xxx` criteria are covered;
- evidence paths exist;
- temporary unblock changes are restored or promoted;
- `Validation.md`, `evaluation.json`, and `VerificationLedger.json` are present.

After completion passes:

```bash
<automind> summary <task-code> --ai <agent>   # preferred when available
<automind> summary <task-code>                # deterministic fallback
<automind> record-check <task-code>
```

Surface the generated report paths to the user: `Delivery.md`, `Validation.md`,
`evaluation.json`, `VerificationLedger.json`, `summary.md`, `runtime-state.json`,
and latest `logs/iter-N/`.

## 15. Reference index

| Need | Read |
|---|---|
| Phase 2 planning/refinement | `phase2-requirement.md` |
| Testcase examples and runbook details | `references/test-design-guide.md` |
| Phase 3 verification/evaluator behavior | `phase3-verification.md` |
| Cross-platform (visual, external sink, probe-flow, unblock) verification details | `references/verification-flow.md` |
| iOS device/simulator/UI-runner ladder details | `references/verification-flow-ios.md` |
| Android device/adb verification details | `references/verification-flow-android.md` |
| Command/script selection | `references/command-script-catalog.md` |
| Dependency and preflight checks | `references/dependency-check.md` |
| Probe-flow generation | `references/probe-flow-generation.md` |
| Agent runtime / detached adapters | `agent-adapters.md` |
| Summary/reuse | `phase4-summary.md` |


## 16. TUI, session, trace, process eval, and learning references

The canonical workflow above stays intentionally compact. The interactive and
observability layers share the same task artifacts and are detailed in
[`tui-session-observability.md`](tui-session-observability.md).

Quick map:

- TUI/shell: `automind`, `automind tui <task> --interactive`, current-task defaults, and per-TUI-process fallback chat sessions for bare natural language before a task exists.
- Session messages: `user-answers.json`, `user-messages.json`, `automind answer`, `automind message`.
- Trace: `events.jsonl` -> `trace.json`, `automind trace <task>`.
- Process evals: `automind process-check <task>` -> `process-eval.json`.
- Improve across runs: `Summary.md`, `run-card.json`, `.automind/summary/run-cards.jsonl`, `automind improve-suggestions`.

Skill mode should still run automatically after pre-implementation review is
resolved. `automind continue [task-code]` is a recovery/checkpoint instruction
for host agents, not a command the human should manually trigger after every
step.


## Human-readable HTML report

AutoMind should generate `.automind/tasks/<task-code>/Report.html` only at final or durable handoff points, or when explicitly requested via `automind report <task-code>`. Normal Generator/Evaluator iterations must not refresh it. The report is for humans and uses the title `<task-code> Automind Report`. It summarizes the completed target requirements/AC, generated/changed artifacts, verification/test results, failed checks, and quality checks. In the Test Results table, the `Key Evidence` column should summarize the few signals/files humans should inspect first: screenshots, TC-level `evidenceAssessment.machineAnchor`, hardMetrics anchors, `music-events.txt`, full logcat, `runtime-evidence.md`, and `VerificationLedger.json` when relevant. The final `Evidence / Screenshots / Logs` column may keep the complete artifact list for traceability, preferably behind a collapsible details block so noisy files such as evaluator/env/summary-refiner logs do not dominate the row. Runtime/UI TC rows should include screenshot evidence by default or explicitly say why no screenshot is linked. The report also includes a Summary / Knowledge Deposition section for `summary.md`, workspace reuse files, knowledge index/raw promotion status, and phase-learning hook cards. A raw artifact appendix may remain for navigation, but the primary evidence should be visible from Test Results. `Report.html` is not an authoritative state source, does not participate in routing, and must not reopen Planner, Generator, or Evaluator after terminal finish.
