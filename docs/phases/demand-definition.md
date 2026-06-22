# Phase 2A — Demand Definition

## Goal

Turn the user's request into a clear, reviewable demand contract before technical execution planning starts.

Demand Definition owns:

```text
Brainstorm.md   -> divergent understanding and bounded research
Requirements.md -> convergent Rxx / AC-xxx contract
```

It answers: what are we doing, why, for whom/what surface, what counts as success, what must not change, and what needs user approval?

## Inputs

- User request and follow-up decisions
- `.user_input.txt` when present
- `Reuse.md` when relevant
- Repository instructions/docs/scripts/runbooks discovered by the agent
- Existing `Brainstorm.md`, `Requirements.md`, `brainstorm.json`, `requirements.json` when replanning

## Brainstorm: divergent understanding

Brainstorm is demand digestion, not a passive question log. It must perform bounded research across five surfaces:

1. **Demand surface** — user/job goal, business/product outcome, success signal, scope/non-goals, affected user/system surface, and what must not change.
2. **Implementation surface** — likely files/modules/functions, domain concepts, architecture seams, existing patterns, and business flows.
3. **Verification surface** — project-native commands, runtime/app/device needs, fixtures, and evidence paths.
4. **Risk surface** — product, technical, verification, rollout/rollback, sensitive-action, blocker, and user-decision risks.
5. **Opportunity surface** — non-obvious business/product/UX/ops improvements, or an explicit note that no extra suggestion applies.

Brainstorm should propose 2-3 plausible approaches with trade-offs, recommend one, and record whether the direction needs user confirmation before code changes.

`brainstorm.json.demandAnalysis` is derived from Brainstorm sections and remains nested inside `brainstorm.json`; do not create a separate demand-analysis file and do not flatten it into top-level brainstorm fields.

## Requirements: convergent contract

Requirements converts the selected Brainstorm direction into stable requirement units:

- `R01`, `R02`, ... requirement statements
- inline `AC-001`, `AC-002`, ... acceptance criteria under each Rxx
- scope, non-goals, constraints, assumptions, and authorization requirements
- definition of done
- traceability from Brainstorm

Requirements is still part of demand understanding: it freezes the goal/scope/success contract. It should not redo open-ended research unless Brainstorm is missing or contradicted.

Every major Brainstorm output must be handled:

- selected approach -> requirement, AC, TestCase, or Plan item;
- major risk -> AC/test/plan mitigation or explicit accepted risk;
- business/product suggestion -> requirement/non-goal with rationale;
- workspace finding -> implementation/verification constraint or explicit non-goal;
- success signal -> acceptance criteria and evidence direction.

## Gate

Demand Definition is ready when:

- Brainstorm has concrete demand/context/risk/opportunity/approach/recommendation content;
- Requirements has stable Rxx and AC-xxx units;
- known assumptions and non-goals are written down;
- unresolved decisions are either low-risk assumptions or routed to `ask_user`;
- downstream TestCases can be derived without reinterpreting the user demand.

If the demand direction is ambiguous, high-risk, or approval-gated, stop after this subflow and ask the user with concise options before producing excessive downstream detail.

## Exit

Proceed to Phase 2B — Verification & Execution Planning when the demand contract is clear enough to design proof and execution order.
