# Brainstorm phase

Part of Phase 2A — Demand Definition. Brainstorm is the divergent half: understand the request in project context before Requirements freezes the contract.

## Goal
Digest the user request against the real project/workspace context, surface risks and opportunities, and produce a reviewable direction before Requirements are frozen. It performs bounded research; downstream TestCases/Plan consume its conclusions rather than rediscovering demand.

## Read on entry
- `docs/workflow.md`
- `docs/phase2-requirement.md`
- `.user_input.txt` when present
- existing `Reuse.md` when present
- repository instructions when present: `AGENTS.md`, `.agents.md`, `.cursor/rules`, `.github/copilot-instructions.md`
- project docs/runbooks: `README*`, `docs/`, setup guides, architecture notes, CI workflows
- project scripts/tools: `scripts/`, `tools/`, `bin/`, `Makefile`, package-manager scripts, Gradle/Maven wrappers, Fastlane lanes, repo-local helpers

## Inputs
- User request / `.user_input.txt`
- Prior reusable lessons when relevant

## Outputs
- `Brainstorm.md`
- `brainstorm.json`

`Brainstorm.md` must include five bounded research surfaces and the resulting decision:

- Demand surface — user/job goal, business/product outcome, success signal, scope/non-goals, affected surfaces, and what must not change.
- Implementation surface — likely files/modules/functions, domain concepts, architecture seams, existing patterns, and business flows.
- Verification surface — project-native commands, runtime/app/device needs, fixtures, and evidence paths.
- Risk surface — product, technical, verification, rollout/rollback, sensitive-action, blocker, and user-decision risks.
- Opportunity surface — non-obvious business/product/UX/ops improvements or an explicit note that no extra suggestion applies.

`Brainstorm.md` must include:

- `User intent digest` — user/job goal, business/product outcome, scope/non-goals, success signal, affected surfaces, and what should not change.
- `Project/context observations` — repository instructions, project docs, scripts/tools, CI/runbooks, constraints, existing domain concepts/business flows, and verification entry points.
- `Business/product suggestions` — non-obvious improvements, UX/ops implications, or an explicit note that no extra suggestion applies.
- `Risk and opportunity register` — product, technical, verification, rollout/rollback risks plus opportunity/quality improvements.
- `Approach options` and `Recommendation` — 2-3 plausible approaches with trade-offs and a recommended direction.

`brainstorm.json.repositoryContext` and the nested `brainstorm.json.demandAnalysis` object are derived from these sections so later phases can depend on structured project/context and demand-understanding signals. Do not create a separate demand-analysis file and do not flatten `demandAnalysis` into top-level `brainstorm.json` fields.

## Checker
- `brainstorm_contract`
- Must capture summary, assumptions, clarification decisions, pre-implementation review state, repository context observations, user intent digest, risks/opportunities, business/product suggestions, approach options, and recommendation.

## Blockers
- Material ambiguity that changes product behavior or verification scope.
- Sensitive/destructive/external dependency decisions that require user approval.

## Exit
Proceed to `requirements` when the request is clear enough to express Rxx/AC-xxx requirements.
