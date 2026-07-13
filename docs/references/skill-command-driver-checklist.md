## Workflow control state migration

For new/updated tasks, read `automind-workflow-state.json` first. It is the
agent-facing control state for current stage, current phase, next action,
next phase, planned next phase, owner, iteration, and state health.

Use `PHASE_REGISTRY[currentPhase].checklistRefs` to find the phase checklist.
Keep checklist items in the native agent TODO/checklist plan; do not copy them
into `automind-workflow-state.json`.

`runtime-state.json.stateSummary` may still exist as a compatibility resolver
projection. Treat it as fallback/diagnostic during migration, not as a competing
state source. If the workflow state is missing, run `phase-gate`/`status` to seed
or reconcile it.

# Skill / Command Phase Handoff Checklist

Use this checklist when CodeAutonomy is running as a skill or slash command, where the host coding agent drives the loop instead of the CLI/TUI orchestrator.

## Preferred path: script-gated handoff

Before every phase handoff, run:

```bash
<AUTOMIND_CLI> phase-gate <task-code> auto
```

For an explicit handoff, use:

```bash
<AUTOMIND_CLI> phase-gate <task-code> plan
<AUTOMIND_CLI> phase-gate <task-code> build
<AUTOMIND_CLI> phase-gate <task-code> verify
<AUTOMIND_CLI> phase-gate <task-code> finish
```

`phase-gate` refreshes/seeds `automind-workflow-state.json`, checks local gate
signals, and returns JSON with:

- `result`
- `canProceed`
- `centralJson`
- `workflowControlState`
- `stageState`
- `phaseSummary` / `stateSummary` response field (in-memory CLI guidance; runtime-state mirror is obsolete fallback)
- `effectiveNext`
- `requiredCommand`
- `nextActionPrompt`
- `phaseReuseRefresh`

If `result != pass`, do not continue to the requested phase. Run `requiredCommand` or fix the owning artifact first.

For skill/command stability, `phase-gate` also performs a deterministic
phase-reuse refresh when it is about to hand off into the key execution phases:
`delivery -> phase-reuse/generator.md` and
`evaluation -> phase-reuse/evaluator.md`. This refresh is intentionally
conservative: it runs only when the phase-reuse file or reuse gate is missing,
or when key owner artifacts such as `Requirements.md`, `TestCases.md`,
`Plan.md`, `Delivery.md`, `Validation.md`, or `evaluation.json` are newer than
the existing phase-reuse file. Fresh acknowledged reuse is not reset.

## Checklist / checkbox plan in skill/command mode

CLI/TUI-owned loops call phase hooks automatically. Skill/command mode is different: the host coding agent edits artifacts and product code directly, so phase discipline is expressed as a checklist instead of a separate hook command. `phase-gate` still refreshes missing/stale generator/evaluator phase reuse at the key execution handoffs.

`automind-workflow-state.json` / `phase-gate` include:

- `checklist[]` — machine-readable ordered phase todo items with `id`, `text`, `done`, `required`, and optional `command`.
- `checkboxMarkdown[]` — human/agent-facing `- [ ]` / `- [x]` rendering.

The host agent should copy the active checklist into its native TODO/checkbox mechanism, complete items one by one, then rerun `phase-gate` for the next handoff. Do not skip an unchecked required item just because chat context sounds confident.

Reuse guidance is part of the ordered checklist. For every phase, the agent should review `Reuse.md` and the relevant `phase-reuse/<phase>.md` when present, but treat them as guidance only; current Requirements/TestCases/Plan/Delivery/Validation and fresh evidence always win. CLI/TUI-owned loops generate phase-reuse through existing hooks; skill/command mode also gets a deterministic `phase-gate` refresh for `delivery -> generator` and `evaluation -> evaluator`, then consumes the files through this checklist.

Coverage expectation: keep the ordered checklist broad enough to cover phase-internal flow, not only handoff gates. In practice each phase should mention: read current routing/reuse inputs, read required phase artifacts, do the phase work, write/update the phase output artifact and compact JSON sidecar when applicable, capture evidence/logs when applicable, update Plan progress when applicable, then rerun the relevant gate/summary command.

UI verification ordering (when a Verify-phase checklist item drives App/UI on a
real device):

1. Climb the platform UI-runner ladder first: `verification-flow-ios.md` /
   `verification-flow-android.md`.
2. If no-edit real-device runners fail, use minimal reversible project edits
   before switching to simulator/emulator or non-automated operation.
3. Use direct-route/page-load/deep-link only after normal UI automation cannot
   reach the target page. Treat it as high-risk verification-unblock: checkpoint,
   record in `verificationUnblockChanges[]`, restore/promote, mark evidence as
   low fidelity, and never use it to satisfy an end-to-end navigation testcase.
4. Use simulator/emulator automation only when that modality is explicitly
   allowed for the testcase or approved as a runtime downgrade.
5. Use human-assisted evidence capture only after automated paths, because
   automation is preferred over user operation.
6. Never pass a testcase from manual action alone; require machine-checkable
   post-condition evidence such as logs, screenshots with assertions, DB/event
   cache diffs, or external sink events.
7. If all evidence paths fail, ask the user for a reduced-scope decision such as
   dry-run, static proof, or compile/build-only proof.

Rules of thumb:

- `command` is optional. Use it only when the item needs a CodeAutonomy CLI helper/gate such as `workflow-check`, `phase-gate`, `context-pack`, `completion-check`, `summary`, or `report`.
- For normal coding-agent work — reading files, editing code, refining Markdown, writing Delivery/Validation, or reasoning about implementation — rely on the host agent's native abilities and TODO/checkbox tool.
- `done` is a best-effort projection from artifacts/gates; hard authority remains `workflow-check`, `phase-gate`, `evaluation.json`, and `completion-check`.

## Central JSON rule

Use `automind-workflow-state.json` as the central workflow control state:

```text
automind-workflow-state.json = current/next workflow control truth
runtime-state.json            = runtime/resume projection
workflow.json                 = phase-sidecar continuity signal
```

Do not route from `runtime-state.json.nextAction` alone. Treat `evaluation.json`, `completion-report.json`, and `runtime-state.json` as resolver inputs that feed the phase summary.

## Hard handoff gates

- Before Build: `phase-gate <task> build` must pass, which implies no hard `workflow-check` blockers.
- Before Verify: `phase-gate <task> verify` must pass, and `Delivery.md` or `delivery.json` must exist.
- Before Finish: `phase-gate <task> finish` must pass, which requires `completion-report.json.result=pass` from `completion-check`.
- Before replying “done”: run `completion-check`, `phase-gate <task> finish`,
  summary/reuse (`summary --ai <agent>` or deterministic fallback),
  `record-check`, and `report <task>`. The final response must be a natural
  handoff: say what was completed and generated, tell the user to open
  `Report.html` first, and call out the key `Test Results` / `Key Evidence`
  proof files such as screenshots, runtime logs, event payloads, and
  `VerificationLedger.json`.

## Fallback when scripts are unavailable

If `<AUTOMIND_CLI>` is unavailable, use schema validation plus the same central JSON rule:

```bash
python -m json.tool .automind/tasks/<task>/runtime-state.json >/dev/null
python -m json.tool .automind/tasks/<task>/workflow.json >/dev/null
python -m json.tool .automind/tasks/<task>/evaluation.json >/dev/null
```

When a JSON Schema validator is available, validate against:

```text
schemas/runtime-state.schema.json
schemas/workflow.schema.json
schemas/evaluation.schema.json
schemas/completion-report.schema.json
```

Without scripts, be conservative:

1. Read `automind-workflow-state.json` and `stages/*-stage-state.json` first; use `runtime-state.json.stateSummary` only as read-only fallback for older tasks.
2. If it says `nextPhase=planning`, do not Build or Verify.
3. If it says `nextPhase=delivery`, only then Build.
4. If it says `nextPhase=evaluation`, require `Delivery.md` or `delivery.json` before Verify.
5. If it says `nextPhase=terminal`, require `completion-report.json.result=pass` before saying done.
6. If artifacts disagree, return to the owner of the broken artifact and avoid asking the user to manually steer the loop.
