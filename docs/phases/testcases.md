# TestCases phase

Part of Phase 2B — Verification & Execution Planning. TestCases are the proof-design half: derive evidence-producing checks from accepted Requirements.

## Goal
Turn requirements into executable or inspectable `TC-*` rows that drive Generator, Evaluator, Plan, and completion gates. TestCases consume Demand Definition; they should not reinterpret demand except to route back to Brainstorm/Requirements when coverage is impossible.

## Read on entry
- `docs/workflow.md`
- `docs/phase2-requirement.md`
- `docs/references/test-design-guide.md`
- `docs/references/verification-flow.md` when runtime/device/platform verification is relevant
- `Requirements.md`, `requirements.json`
- `Brainstorm.md`, `brainstorm.json` when traceability or risk context is needed

## Hard inputs
- `Requirements.md` and `requirements.json`.
- Required `AC-xxx` list, verification methods, and runtime proof requirements.

## Hard outputs
- `TestCases.md` with `TC-*` proof rows.
- `testcases.json` with compact TC ids, AC refs, runtime level, required flag, concise action/assertion/evidence fields, and `sourceRef` pointers.

`testcases.json` is not a long runbook copy; keep long reasoning and examples in `TestCases.md`.

## Gate
- Every required AC maps to at least one required TC.
- Runtime/device TCs include preconditions, command/action, assertion, evidence expectation, and postCheck.
- Runtime/device TCs cannot be silently downgraded to static-only. Startup/preflight-only evidence cannot satisfy required runtime behavior.

## Downstream contract
Plan consumes required TC list, runtime/device TC list, verification target, dependencies, blockers, and evidence expectations.

## Checker
- `testcases_contract`
- Required testcases must map to acceptance criteria and include command or steps plus expected evidence.

## Blockers
- Required runtime/client behavior with no safe executable or inspectable verification path.
- Testcases that demote required runtime proof without explicit approval.

## Exit
Proceed to `plan` when required ACs are covered by concrete or explicitly blocked/manual TestCases. Build/delivery only after Plan, pre-implementation review, and `workflow-check` pass.
