# Requirements phase

Part of Phase 2A — Demand Definition. Requirements is the convergent half: turn the selected Brainstorm direction into a stable Rxx / AC-xxx contract.

## Goal
Convert the clarified request into canonical `Requirements.md` with Rxx requirements and inline AC-xxx acceptance criteria. Requirements consumes Brainstorm; it should not redo open-ended research unless Brainstorm is missing or contradictory.

## Read on entry
- `docs/workflow.md`
- `docs/phase2-requirement.md`
- `docs/phases/brainstorm.md`
- `Brainstorm.md`
- `brainstorm.json`

## Hard inputs
- `Brainstorm.md` and `brainstorm.json` selected direction, scope, non-goals, risks, and project observations.
- User request and any recorded user answers.
- Known constraints, assumptions, and verification direction.

## Hard outputs
- `Requirements.md` with `Rxx` requirements and inline `AC-xxx` acceptance criteria.
- `requirements.json` with compact ids, required flags, verification methods, hashes, and `sourceRef` pointers.

`Requirements.md` must preserve traceability from Brainstorm: selected approach, major risks, business/product suggestions, workspace findings, and success signals become requirements/AC/tests/plan items or explicit non-goals with rationale.

`requirements.json` is not a copy of `Requirements.md`; do not duplicate long Markdown prose.

## Gate
- Every important user goal has an `Rxx`.
- Every required `Rxx` has testable `AC-xxx` coverage.
- Direction/scope contradictions route to refine/replan. Do not scatter `ask_user` here unless an immediate safety/system blocker cannot be deferred; the normal user-facing decision point is the bundled pre-implementation review.

## Downstream contract
TestCases consume the required AC list, verification methods, required flags, non-goals, and definition of done.

## Checker
- `requirements_contract`
- Every requirement must have an id and acceptance criteria.

## Blockers
- Missing or contradictory requirements.
- Acceptance criteria that cannot be verified or mapped to testcases.

## Exit
Proceed to `testcases` when requirements and acceptance criteria are coherent and reviewable.
