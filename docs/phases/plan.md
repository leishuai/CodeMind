# Plan phase

Part of Phase 2B — Verification & Execution Planning. Plan is the execution-design half: order implementation and verification after TestCases define proof.

## Goal
Create an implementation and verification plan that maps requirements and TestCases to concrete work. Plan consumes Demand Definition and TestCases; if it discovers missing demand or proof coverage, return to the owning artifact rather than hiding assumptions in Plan.

## Read on entry
- `docs/workflow.md`
- `docs/phase2-requirement.md`
- `docs/references/command-script-catalog.md`
- `Requirements.md`, `requirements.json`
- `TestCases.md`, `testcases.json`
- project README/docs/scripts/runbooks when choosing commands

## Hard inputs
- `Requirements.md` / `requirements.json`.
- `TestCases.md` / `testcases.json`.
- Required TC list, runtime/device TC list, known commands/tooling, `Reuse.md` when present.

## Hard outputs
- `Plan.md` with implementation checklist, verification checklist, first functional batch, command/tool choice, fallback/replan policy.
- `plan.json` with compact checklist/status/refs.
- Derived `workflow.json` after `workflow-check` refreshes/materializes the contract.

`plan.json` is not a copy of `Plan.md`; it stores machine-readable checklist state and refs only.

## Gate
- Plan covers every required TC.
- Pre-implementation review is resolved before Generator edits product/runtime code.
- `workflow-check` passes before Generator.
- `replan_pending / planner / run_test_planner` must not jump directly to Generator.

## Downstream contract
Generator consumes `workflow.json`, Plan implementation checklist, required TC target set, verification plan, and latest `evaluation.json` when retrying.

## Checker
- `plan_contract`
- Must include implementation checklist, verification checklist, first functional batch, and project script discovery rationale.

## Blockers
- No safe command or verification path for required behavior.
- Missing project setup information that prevents credible implementation or verification.

## Exit
Proceed to `pre_implementation_review` when the plan maps requirements and TestCases to implementation and verification work, including first functional batch and complete checklists.
