# Pre-implementation review phase

## Goal
Stop before Generator edits product/runtime code and make the implementation gate explicit: proceed, ask the user, or replan.

This is not an `ask_user` phase. `ask_user` is one possible decision/action from this review phase.

## Read on entry
- `docs/workflow.md`
- `docs/phase2-requirement.md`
- `docs/phases/brainstorm.md`
- `docs/phases/requirements.md`
- `docs/phases/plan.md`
- `docs/phases/testcases.md`
- `Brainstorm.md`, `brainstorm.json`
- `Requirements.md`, `requirements.json`
- `Plan.md`, `plan.json`
- `TestCases.md`, `testcases.json`

## Inputs
- Phase 2 artifacts: Brainstorm, Requirements, Plan, TestCases
- Their JSON sidecars
- `runtime-state.json.planner.preImplementationReview`
- Runtime/client verification decision bundle when relevant

## Outputs
- `pre-implementation-review.json`
- `runtime-state.json` update:
  - `auto_proceed` -> `status=planned`, `currentOwner=generator`, `nextAction=run_generator` after `workflow-check` passes
  - `ask_user` -> `status=human_input_pending`, `currentOwner=human`, `nextAction=ask_user`
  - `replan` -> `status=replan_pending`, `currentOwner=planner`, `nextAction=replan` or `run_test_planner`

## Decisions

### `auto_proceed`
Use only when the artifacts are coherent and no material user/system choice is needed before implementation.

### `ask_user`
Use when user intent, product behavior, validation target, runtime/device/signing, destructive action, ambiguous/irreversible privacy/security impact, or non-obvious tradeoff needs a human decision. Privacy/terms Agree/Allow/Continue is sensitive by default: use ask_user unless the exact consent action was already authorized in the pre-implementation bundle. Safe close/skip/later/dismiss overlays may auto-unblock with evidence.

The pre-implementation `ask_user` prompt is a one-shot decision bundle generated after Phase 2 Refiner has inspected the requirement and relevant project context. It should merge model-derived soft questions (implementation direction, requirement boundaries, key assumptions, risks/divergences, acceptance/evidence expectations) with deterministic hard questions (device/signing/permission/environment/safety). Device choice is one necessary part for mobile/client tasks, but it must not replace the broader implementation-quality review. When the task requires reproducing a specified UI design (Figma / 设计稿 / 视觉还原 / pixel-level / UI consistency signals, or a Figma link in the requirement) and no usable local design image exists, fold a design-reference request into the same bundle: ask for the Figma link or a local design export under `.automind/tasks/<task>/design/` to serve as the `visual-inspect --baseline` reference. Do not ask this for non-UI tasks, and if the user declines, degrade the UI case to structure/text/element-order assertions plus final human screenshot confirmation instead of blocking.

### `replan`
Use when Requirements, Plan, TestCases, or verification strategy are internally inconsistent and should be corrected before implementation.

## Checker
- `pre_implementation_review_contract`
- Decision must be one of `auto_proceed`, `ask_user`, or `replan`.
- Reviewed refs must include Requirements, Plan, and TestCases.
- `ask_user` requires a question/options and `nextAction=ask_user`.
- `replan` requires `nextAction=replan` and should explain issues.
- `auto_proceed` requires `nextAction=delivery` and should record confirmation metadata when available.

## Blockers
- Missing review decision.
- Unresolved user decision.
- Missing approval for runtime/device downgrade.
- Required TC/AC/evidence strategy is not coherent.

## Exit
- `auto_proceed` exits to `delivery` after Generator runs.
- `ask_user` exits to human input pending and later resumes this review.
- `replan` exits back to Phase 2 refinement.
