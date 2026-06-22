# Delivery phase

## Goal
Record what Generator changed and how the change maps back to requirements, acceptance criteria, and testcases.

## Read on entry
- `docs/workflow.md`
- `docs/phase3-verification.md`
- `docs/references/state-actions.md`
- `workflow.json`
- `requirements.json`
- `plan.json`
- `testcases.json`
- latest `evaluation.json` when retrying
- `logs/iter-N/iteration-purpose.md` / `.json`
- `logs/iter-N/generator-context.md` / `.json`

## Hard inputs
- Product/runtime change target from `workflow.json` and `Plan.md`.
- `Requirements.md/json`, `TestCases.md/json`, `Plan.md/json`.
- Latest `evaluation.json` / `Validation.md` feedback when retrying.
- Iteration purpose and Generator context pack.

## Hard outputs
- Product/runtime code changes when required.
- `Delivery.md`.
- `delivery.json` with compact changed-file refs, implemented requirement/TC refs, commands/self-tests, risks, and `sourceRef` pointers.
- Plan implementation checklist update when applicable.
- `logs/iter-N/*` command/decision evidence.

`delivery.json` is not a long narrative copy of `Delivery.md`.

## Gate
- Generator cannot mark required TC pass or claim final finish.
- Generator cannot bypass unresolved `ask_user`.
- Generator cannot overwrite terminal `finished/finish` state.
- Delivery must be sufficient for an independent Evaluator to verify.
- If required runtime behavior exists but is not externally observable enough for verification, Generator may add scoped temporary diagnostic logs or test-only instrumentation. These must be minimal, non-secret, not change product semantics, use an identifiable marker such as `[AutoMind][Verify]`, and be removed or explicitly promoted before finish.

## Downstream contract
Evaluator consumes `Delivery.md/json`, changed files, required TC list, runtime target, and evidence/log refs. When temporary diagnostic logs are used, Delivery must name the tag/keyword, expected signal, touched files, and whether the log is temporary or intended to remain.

## Checker
- `delivery_contract`
- Delivery should name changed files, implemented requirements, touched testcases, commands run, and known risks.

## Blockers
- Changes cannot be mapped to required TC/AC.
- Implementation introduced risks that must be routed to replan or ask_user.

## Exit
Proceed to `evaluation` when delivery evidence is sufficient for an independent evaluator.
