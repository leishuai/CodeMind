# State and Actions Reference

This reference explains CodeMind runtime state, route actions, and iteration
boundaries. It is intentionally small: use it as a quick lookup when reading
`automind-workflow-state.json` for workflow routing plus local signals such as
`evaluation.json`, `workflow.json`, or `automind status`.

## 1. Three runtime-state fields

CodeMind runtime state is not a single enum. The main route tuple in `runtime-state.json` is:

```text
status + currentOwner + nextAction
```

| Field | Meaning | Example |
|---|---|---|
| `status` | Current task condition | `retry_pending` |
| `currentOwner` | Who owns the next work | `generator` |
| `nextAction` | Route action to take next | `retry_generator` |

Example:

```json
{
  "status": "retry_pending",
  "currentOwner": "generator",
  "nextAction": "retry_generator"
}
```

Meaning: the task is not finished, Generator owns the next work, and the loop
should continue with Generator repair/implementation. `retry_generator` is a
route action, not a separate phase name.

Another example:

```json
{
  "status": "human_input_pending",
  "currentOwner": "human",
  "nextAction": "ask_user"
}
```

Meaning: automation is paused, the human owns the next decision, and CodeMind
must ask/wait for the user before continuing.

## 2. Common route mapping

| Route signal | `runtime-state.status` | `currentOwner` | `runtime-state.nextAction` | Meaning |
|---|---|---|---|---|
| `retry_generator` | `retry_pending` | `generator` | `retry_generator` | Requirements and TestCases still hold; return to Generator to repair implementation, Delivery, or missing evidence. |
| `replan` | `replan_pending` | `planner` | `run_test_planner` | Requirements, TestCases, Plan, validation target, or strategy needs planning repair. Do not jump directly to Generator. |
| `ask_user` | `human_input_pending` | `human` | `ask_user` | A user decision/authorization is required. Autonomous loop must pause until an answer is recorded. |
| `finish` | `finished` | `automind` | `finish` | Terminal state after completion-check proves required TC/AC/evidence coverage. |
| `stop` | `stopped` | `automind` | `stop` | Stop the task due to explicit user stop or unrecoverable condition. |

## 3. Owner boundaries

- Planner owns `Brainstorm.md`, `Requirements.md`, `TestCases.md`, `Plan.md`,
  and pre-implementation review routing.
- Generator owns product/runtime implementation, `Delivery.md`, `delivery.json`,
  and implementation checklist progress.
- Evaluator owns independent verification, `Validation.md`, `evaluation.json`,
  evidence paths, and verification checklist progress.
- CodeMind owns deterministic gates such as `workflow-check`, `completion-check`,
  state reduction, terminal authority, summary/reuse handoff, and reports.
- Human owns only explicit `ask_user` decisions.

## 4. Evaluator route vs completion gate

Evaluator writes the primary repair route in `evaluation.json.nextAction`:

- `retry_generator`: implementation, Delivery, or evidence is incomplete, but
  Requirements and TestCases remain valid.
- `replan`: the requirement, testcase, validation target, or proof strategy is
  wrong or incoherent. It is a loop-control request. The current loop must run
  Planner, then run a fresh `workflow-check`. Only that same code path may
  consume the signal by rewriting `evaluation.json.nextAction` to
  `retry_generator` and adding `previousNextAction=replan` plus
  `replanResolution{...}`. Do not infer consumption from old planner/workflow
  fields.
- `ask_user`: a user/system decision is required.
- `finish`: Evaluator believes the task can finish.

Every final route write must be followed by `apply_evaluation_result()`, which
updates `runtime-state.json` as a runtime/cache projection and refreshes/seeds `automind-workflow-state.json` plus `stages/*-stage-state.json`. It does not write `runtime-state.json.stateSummary` by default.
`automind-workflow-state.json` is the only workflow control state; `stages/*-stage-state.json` carries stage-local status such as evaluation/completion projections; `runtime-state.json.stateSummary` is obsolete fallback only;
`workflow.json` is a contract/gate.

`completion-check` owns the final finish gate. It does not decide every normal
repair route. It validates claimed finish and may override false finish when
required TC/AC/evidence coverage is not proven.

## 5. Iteration contract

An CodeMind iteration is one Generator/Evaluator attempt unit, not one shell
command and not one testcase. Early planning/resume/ask-user bookkeeping may
advance counters, but evidence-bearing iteration work must have a clear purpose
and phase-owned outputs.

The iteration number lives in three places with one fixed meaning each
(`apply_evaluation_result` is the single source of truth):

- `evaluation.json.iteration` is the attempt that *just finished/failed*.
- `runtime-state.json.iteration` mirrors that finished attempt.
- `automind-workflow-state.json.iteration` is the *active/next* attempt being
  routed; on a retry route it advances to `evaluation.json.iteration + 1`.

### Start of an iteration

Required inputs:

- `automind-workflow-state.json` for workflow control truth;
- `runtime-state.json` for runtime/resume state;
- `workflow.json`;
- iteration number and phase owner;
- `logs/iter-N/iteration-purpose.md` and `logs/iter-N/iteration-purpose.json`;
- `generator-context.md/json` for Generator, or `evaluator-context.md/json` for Evaluator;
- latest `evaluation.json` when retrying.

Generator additionally consumes `Requirements.md/json`, `TestCases.md/json`,
`Plan.md/json`, and relevant previous `Validation.md` / `Delivery.md` excerpts
when retrying.

Evaluator additionally consumes `Delivery.md/json`, changed-file summary,
required `TC-*` list, runtime target, and probe-flow/script/build command.

### End of a Generator iteration

Generator must update:

- `Delivery.md`;
- `delivery.json`;
- implementation checklist in `Plan.md` when applicable;
- `logs/iter-N/*` evidence of commands/decisions;
- runtime-state route toward Evaluator, retry, replan, ask_user, or stop.

Generator must not mark required TC pass, claim final finish, or overwrite a
terminal `finished/finish` state.

### End of an Evaluator iteration

Evaluator must update:

- `Validation.md`;
- `evaluation.json`;
- `testResults[]`;
- evidence paths under `logs/iter-N/`;
- `failedChecks[]` and `nextAction`.

Evaluator must not repair product/runtime code unless it is explicitly recorded
as a temporary verification unblock change. Environment blockers, startup-only
checks, or preflight-only checks cannot satisfy required runtime TestCases.

### After Evaluator

`completion-check` may update:

- `completion-report.json`;
- `VerificationLedger.json`;
- derived terminal runtime state if and only if completion passes.

A passing final completion gate locks:

```json
{
  "status": "finished",
  "currentOwner": "automind",
  "nextAction": "finish"
}
```

## 6. Live-state JSON consolidation

CodeMind keeps one primary workflow control-state surface:

```text
automind-workflow-state.json
```

`stages/*-stage-state.json` carries stage-local control payloads such as
verification-loop evaluation and summary completion. `runtime-state.json.stateSummary`
is obsolete fallback only and should not be written by new flows. There is no
separate phase-summary JSON. `workflow.json`, `evaluation.json`, and
`completion-report.json` remain compatibility/domain artifacts, not live control
state authorities.

`plannedNextPhase` in `automind-workflow-state.json` is derived, not hand-coded.
It comes from `workflow_state.default_planned_next_phase`, which reads
`PHASE_REGISTRY[phase].next[0]` (or `CONTROL_PHASE_REGISTRY` for control phases)
and returns `None` for terminal/unknown phases. Adding or reordering a phase only
requires updating the registry `next` edges; the state reducer needs no change.

## 7. Authority rules

- `completion-report.json result=pass` plus valid ledger/evidence is terminal authority.
- `completion-check-current` is advisory; it cannot override a final pass.
- Weak/current/partial Evaluator or probe-flow artifacts cannot overwrite terminal `finished/finish` state.
- `replan` normalizes to `replan_pending / planner / run_test_planner`.
- `ask_user` is a hard pause until a user answer is recorded.
- After `finished/finish`, the loop must not dispatch Planner, Generator, or Evaluator unless a new explicit task epoch is started.

## 8. Observability artifacts: metrics and audit

CodeMind writes two categories of observability data alongside control state:
**metrics** (quantitative measurements) and **audit** (qualitative decision/action
trail). Both are written as standalone files so that `runtime-state.json` stays
focused on routing/resume state.

### 8.1 Metrics — `metrics.json`

`metrics.json` is the standalone metrics file. It is written by
`metrics.py:MetricsCollector.flush()` and referenced from `runtime-state.json`
via the `metricsRef` field (value: `"metrics.json"`).

**Backward compatibility**: older tasks may still have `runtime-state.json.metrics`
(embedded). `read_metrics()` in `orchestrator/metrics.py` reads `metrics.json`
first and falls back to the embedded `metrics` field for legacy tasks.

What metrics covers:

| Section | Contents |
|---|---|
| `taskDuration` | Total wall-clock task duration (seconds). |
| `iterations[]` | Per-iteration breakdown: generator/evaluator durations, sub-phase timings (build, install, ui_execution, preflight), platform, agent calls. |
| `aggregates` | Min/max/avg/sum/count for every recorded metric (phase durations, warm build, LLM tokens, resource usage, etc.). |
| `phases` | Top-level phase duration records (planning, generator, evaluator, summary, …). |
| `agentCalls` | All agent call records with duration, retries, exit code. |
| `llmTokens` | LLM prompt/completion/total token counts and model name. |
| `cache` | Warm-build and UI-path-cache hit/miss totals. |

Schema: [`schemas/metrics.schema.json`](../../schemas/metrics.schema.json).

### 8.2 Audit — `audit.jsonl` + `audit.json`

The audit trail records **key decisions, logic branches, actions, gate results,
policy evaluations, and recovery attempts** so you can trace *why* CodeMind did
something, not just *what* it did.

Two files are produced:

| File | Format | Purpose |
|---|---|---|
| `audit.jsonl` | Append-only JSON Lines, one entry per line | Raw event stream — the source of truth. Written incrementally during the task. |
| `audit.json` | Pretty-printed JSON summary | Aggregated report generated at task finish by `write_audit_report()`. Contains counts, high-risk entries, recent actions/gates/decisions. |

Audit event types:

| Event type | Meaning |
|---|---|
| `decision_made` | A key decision (retry, replan, ask_user, finish, recovery, fallback, skip, continue). |
| `branch_taken` | A logic branch was selected (condition, outcome, alternatives). |
| `action_executed` | An operation was performed (agent call, build, install, etc.). |
| `gate_result` | A gate check completed (workflow-check, completion-gate, phase-gate). |
| `policy_evaluation` | A policy was evaluated (retry policy, risk policy, completion policy). |
| `recovery_attempt` | A recovery or self-repair action was attempted. |

Each entry carries: `ts` (timestamp), `type`, `source`, plus optional
`iteration`, `phase`, `message`, `details`, `decisionType`, `reason`,
`action`, `riskLevel`.

**Risk levels** (`low`, `medium`, `high`, `critical`) are auto-inferred from
decision type and reason; callers can also override explicitly.

**Sensitive data** is redacted using the same `redact_sensitive_*` helpers as
`events.jsonl` — audit entries never contain raw credentials, tokens, or
secrets.

Schema: [`schemas/audit.schema.json`](../../schemas/audit.schema.json)
(describes `audit.json`; `audit.jsonl` is an unbounded stream of the same
entry objects).

### 8.3 `runtime-state.json` fields related to observability

| Field | Type | Meaning |
|---|---|---|
| `metricsRef` | string | Relative path to the standalone metrics file (always `"metrics.json"`). |
| `metrics` | object | **DEPRECATED** — embedded metrics for backward compatibility with tasks created before the split. New tasks write `metricsRef` instead. |
| `decisionLog` | array | Bounded recent decision log entries for compressed-session continuity. Mirrors a subset of audit events in a compact form. |
