# AutoMind Design

AutoMind is designed around one product belief:

> Coding agents should not only write code. They should keep working through an evidence-driven engineering loop until the requested outcome is verified or a real blocker is exposed.

AutoMind is the harness layer for that loop.

---

## 1. Problem statement

Modern coding agents can generate useful code quickly, but real project completion is usually blocked by issues that are not solved by code generation alone:

- the original request is vague or changes during the conversation;
- acceptance criteria are implicit;
- tests are missing, stale, or not mapped to requirements;
- build, device, signing, account, or permission failures are confused with product-code failures;
- the agent fixes one issue and accidentally breaks another;
- verification evidence is scattered across chat messages and terminal output;
- an evaluator shares the generator's assumptions and misses failures;
- lessons from a completed task are lost after the session ends.

AutoMind treats these as system problems. The answer is not a bigger prompt; the answer is a recoverable loop with explicit artifacts, gates, evidence, and reuse memory. Prompts guide the agent's judgement, but the loop owns execution quality: keep moving, collect proof, recover safely, and refuse unsupported finish claims.

---

## 2. Product goal

AutoMind turns a user request into a controlled loop:

```text
understand -> plan -> implement -> verify -> diagnose -> repair/replan -> verify again -> summarize
```

The target behavior is end-to-end automation by default:

- continue automatically while the next safe action is clear;
- use model reasoning for planning, diagnosis, implementation, and test design;
- use deterministic commands and platform tools for evidence where possible;
- stop only when completion is proven, user input is required, progress is unsafe, or a hard blocker exists.

The loop should be usable in two ways:

1. **Skill/slash-command mode** — the current coding-agent session remains the Planner and Generator, while AutoMind provides the workflow, task files, CLI helper gates, and evaluator protocol.
2. **Detached CLI mode** — AutoMind starts separate non-interactive agent CLI invocations through adapters for background or scripted operation.

Both modes share the same task directory, session artifacts, trace/process-eval records, and evidence contract.

A third user-facing surface sits on top of those two modes: the interactive AutoMind shell/TUI (`automind`, `automind tui <task> --interactive`). It does not create a separate protocol. Commands, user answers, natural-language messages, traces, and process evals are persisted back into the same task/run session so CLI, TUI, skill mode, and adapter invocations see the same state.

---

## 3. Non-goals

AutoMind intentionally does not try to become:

- a replacement for Codex, Claude Code, Trae, or other coding agents;
- a replacement for project-native test frameworks;
- a complete mobile testing platform;
- a tool that silently changes signing, devices, privileged services, or user data;
- a system that guarantees correctness without evidence.

AutoMind's job is to orchestrate and discipline the engineering loop around existing agents and tools.

---

## 4. Core design principles

### 4.1 Evidence beats vibes

A task is not done because the model says it is done. It is done when required test cases and acceptance criteria are covered by concrete evidence. For UI/runtime tasks, startup/discovery evidence is only path-finding; proof requires the relevant action path plus postChecks that observe the intended state, event, log, sink, API response, or side effect.

Evidence may include:

- command output;
- build/test logs;
- screenshots, especially default runtime/UI TC screenshots when capturable;
- UI hierarchy or accessibility dumps;
- platform test reports;
- crash hints;
- structured quality summaries;
- generated `VerificationLedger.json` coverage.

### 4.2 The file protocol is the shared memory

AutoMind does not rely on hidden chat memory to connect roles, sessions, or processes. The shared state is the task directory:

```text
.automind/tasks/<task-code>/
  runtime-state.json
  workflow.json
  events.jsonl
  user-answers.json
  user-messages.json
  trace.json
  process-eval.json
  evaluation.json
  Validation.md
  Delivery.md
  VerificationLedger.json
  run-card.json
  logs/iter-N/*
```

A current host session, an isolated subagent, a deterministic verifier, or a detached CLI process should all exchange results through these files.

### 4.3 Requirements and tests are first-class artifacts

The loop starts by converting user intent into verifiable structure:

```text
Brainstorm.md -> Requirements.md -> TestCases.md -> Plan.md -> workflow.json
```

`workflow.json` is the machine-readable executable contract derived from the human-readable planning artifacts plus `runtime-state.json`. It preserves platform-neutral test intent, required/optional testcase policy, runtime level, executors, verification target, and runtime downgrade approval state so deterministic gates and platform adapters read the same semantics. This prevents the agent from changing the target silently during later repair rounds.

### 4.4 Scripts are adapters, not the brain

The model should do the reasoning-heavy work: clarify intent, design tests, diagnose failures, choose repair strategies, and decide when to replan.

Scripts should do bounded, repeatable work:

- create task containers;
- run preflight checks;
- provide optional read-only dependency/tooling discovery when project setup is
  unclear;
- execute build/test/device commands;
- collect evidence;
- write structured evaluator results;
- export skills/commands;
- enforce gates.

This boundary is deliberate. For example, a model can usually infer a project's
technology stack from README, CI, lockfiles, and source files. A helper such as
`dependency-check` exists only to produce a stable, reviewable fact snapshot
when that path is unclear or a tooling failure needs classification. It is not a
required gate and it does not install target project dependencies.

### 4.5 High automation with evidence-driven self-correction

AutoMind's default goal is not “run one command.” The default goal is to continue the harness loop and self-correct from evidence until one of these is true:

- required verification passes and `completion-check` passes;
- human confirmation is required;
- a human/system decision is required for environment/tool/device/signing state;
- repeated attempts show no progress and the loop needs replan or human strategy confirmation;
- the user explicitly requested a single-stage operation.

For Coding-Agent execution, AutoMind intentionally separates execution power from safety policy. Planner/Generator bypass is a task-level decision stored in `runtime-state.json.agentExecutionPolicy`, shared across Codex/Claude/Trae instead of being encoded as an agent-specific field. Missing policy falls back to bypass so non-new tasks, detached scripts, resume, helper commands, or flows that cannot ask still keep the high-automation default. The TUI asks for this decision only when creating a new task, and only once; detached scripts, resume, and helper commands do not create fresh bypass grants and must follow the recorded task policy or the missing-policy bypass fallback. If the recorded bypass state changes, AutoMind must not reuse an older primary Planner/Generator session created under the opposite execution mode. Model Evaluator is different: Evaluator is always fresh-isolated and bypassed for every supported Coding Agent (Codex dangerous bypass, Claude skip permissions, Trae/Coco YOLO). Evaluator runs the broadest set of runtime/device/build/browser commands and should not deadlock on agent approval prompts while collecting evidence. This does not authorize unsafe work: money movement, destructive changes, credential exposure, signing/keychain/device-trust changes, system/network/security configuration, uploads/exfiltration, and similar high-risk actions still route through AutoMind's `ask_user` contract with exact scope and risk.

Coding-agent runs also have an idle-output watchdog. If the subprocess produces no stdout/stderr for `AUTOMIND_AGENT_IDLE_TIMEOUT_SECONDS` seconds (default 1800), AutoMind kills the stale process and records `agent_stalled_no_output`. This is a recoverable agent/runtime interruption, not a product validation failure. Periodic AutoMind heartbeat events do not reset this watchdog; only real agent output does.

This is automation with brakes, not blind automation. Evaluator evidence should flow back to Generator as actionable repair input. The repair path is explicit:

```text
failed TC-* -> covered AC-* -> related Rxx -> evidence -> cause category -> next action
```

If the implementation is wrong, AutoMind retries Generator with the failure evidence. If the plan or test target is wrong, it replans. If the next step depends on a human/system choice, it asks the user. If the same failure repeats without progress, iteration/reflection budgets turn churn into a human strategy decision.

### 4.6 Observability, TUI, and human handoff

AutoMind should be inspectable while it runs and understandable when it hands work back to a human. The base records are:

- `events.jsonl`: append-only timeline for TUI/skill mode.
- `trace.json`: formal task/phase/event trace for debugging, evals, and summary.
- `process-eval.json`: checks whether the harness followed required gates and session handoffs.
- `Summary.md`, `run-card.json`, and `.automind/summary/run-cards.jsonl`: human-readable and structured learning for future tasks.

The interactive shell/TUI is the live control surface for long-running tasks. It should let a user inspect state, see events, continue work, answer pending questions, and understand progress without relying on hidden chat history.

`Report.html` is the human handoff surface. It should summarize requirements,
acceptance criteria, implementation artifacts, blockers, and summary/knowledge
deposition. Its Test Results table should make the primary reading path obvious:
each TC row gets a concise `Key Evidence` summary with screenshots when
available, machine anchors / hardMetrics, and the few proof files a human should
inspect first; complete artifact lists stay available for traceability but do
not dominate the row. Runtime/UI TCs should capture screenshots by default or
explicitly state why no screenshot is linked.

The final user-facing response is part of the product handoff, not just chat
polish. After completion passes, AutoMind should say in natural language what
was completed, what was generated, ask the user to open `Report.html` first,
and call out the key runtime proof / log / ledger files.

This keeps traces, evals, TUI observability, reports, and improve-across-runs inside the existing file protocol instead of creating a separate memory system.

### 4.7 Phase hooks make learning and policy attachable

AutoMind is phase-based, so lightweight deterministic hooks can run before and after phase nodes without changing the core loop semantics.

Current hook uses include:

- before a phase: prepare targeted `phase-reuse/<phase>.md` from matched knowledge;
- after a phase: write `logs/phase-learnings/<phase>.json` for later summary/reuse;
- during future extensions: attach policy checks, preflight hints, or project-specific reminders.

Hooks are intentionally bounded. They should enrich the phase with relevant facts or learning, not replace the Planner/Generator/Evaluator roles and not hide side effects from the task artifacts.

---

## 5. Canonical flow

```text
User request
  -> Brainstorm.md
  -> Requirements.md
  -> TestCases.md
  -> Plan.md
  -> workflow.json / workflow-contract
  -> workflow-check
  -> Generator
  -> Delivery.md
  -> Evaluator
  -> Validation.md + evaluation.json
  -> completion-check
  -> Retry / Replan / Ask Human / Finish
  -> trace / process-eval
  -> Summary / Run Card / Reuse
```

Each step has a clear owner and output:

| Step | Purpose | Output |
|---|---|---|
| Brainstorm | capture assumptions, alternatives, risks, decisions, and pre-implementation review state | `Brainstorm.md` |
| Requirements | freeze goal, scope, non-goals, requirement units, and inline acceptance criteria | `Requirements.md` |
| TestCases | map requirements/criteria to executable checks | `TestCases.md` |
| Plan | define implementation and verification strategy | `Plan.md` |
| workflow-contract | materialize/validate the executable contract for diagnostics and CI | `workflow.json` + report |
| workflow-check | verify artifact continuity plus pre-implementation hard gates before handoff | report and exit status |
| Generator | implement or repair | code/config changes, `Delivery.md` |
| Evaluator | verify independently with evidence | `Validation.md`, `evaluation.json`, logs |
| completion-check | prove required coverage before finish | `VerificationLedger.json`, `completion-report.json` |
| trace / process-eval | make the harness run inspectable and evaluate process correctness | `trace.json`, `process-eval.json` |
| Report | produce a human-readable task handoff | `Report.html` |
| Summary / Run Card / Reuse | preserve reusable lessons and structured cross-run learning | `summary.md`, `run-card.json`, `.automind/summary/*`, `Reuse.md` |

Before Generator changes code, `Brainstorm.md` and `runtime-state.json.planner.preImplementationReview` must explicitly decide one of:

- `auto_proceed`: the plan is clear and low-risk enough to continue automatically;
- `ask_user`: a human decision is required before implementation;
- `replan`: Phase 2 artifacts need more refinement.

This preserves AutoMind's automation goal while preventing the system from silently implementing a wrong plan when assumptions are material. `workflow-check` treats unresolved pre-implementation review, unresolved one-shot decision bundles, or missing required planning fields as hard failures rather than soft warnings.

Brainstorm is an active design phase, not a passive question list. The agent should explore project context, think of edge cases and constraints the user may not have mentioned, compare plausible approaches, recommend one, and usually ask the user to confirm that conclusion before non-trivial code changes. That confirmation should include not only the implementation direction but also must-pass acceptance criteria, required TestCases, and the evidence strategy; otherwise AutoMind might automate against the wrong test target.

---

## 6. How user intent becomes executable work

AutoMind has a demand-digestion phase. It does not treat the user's first sentence as the final implementation contract. The Planner expands the request, explores ambiguity, records decisions, and then distills the result into a verifiable requirement and test contract.

```text
Raw user request
  -> Brainstorm: assumptions, alternatives, risks, decisions, verification ideas
  -> Requirements: goal, scope, non-goals, Rxx units, objective AC-xxx criteria
  -> TestCases: executable evidence paths for those criteria
  -> Plan / workflow.json: implementation strategy plus machine-checkable contract
```

### 6.1 Brainstorm.md

`Brainstorm.md` is where the agent records:

- assumptions;
- unclear points;
- user decisions;
- constraints;
- risks;
- alternatives and trade-offs;
- recommendation and confirmation state;
- candidate verification paths.

It keeps uncertainty visible instead of burying it in the chat. Its job is to make the request understandable before it becomes executable.

### 6.2 Requirements.md

`Requirements.md` is the canonical single-file requirement contract for new tasks. It freezes the product intent and embeds objective acceptance criteria inline:

- goal;
- scope;
- non-goals;
- requirement units such as `R01`, `R02`;
- inline acceptance criteria such as `AC-001`, `AC-002`;
- observable behavior;
- constraints.

```text
R01 -> AC-001, AC-002
R02 -> AC-003
```

This is the anchor that prevents prompt drift while avoiding duplicated `Spec.md` / `Require.md` restatement.

### 6.3 TestCases.md

`TestCases.md` maps requirements and acceptance criteria to verifiable checks:

```text
TC-001 covers AC-001
TC-002 covers AC-002
TC-003 covers AC-003
```

Test cases may be automated tests, command checks, device checks,
manual-observable checks with evidence, or quality checks. For functional
behavior they should read like executable runbooks, not slogans:

```text
prepare/preflight -> build/install/deploy/start -> launch/open entry
-> perform actions -> assert visible/log/state/output/API result -> collect evidence
```

For App/UI work this means naming the entry page/screen/route/activity/state,
the action sequence, and the concrete assertions. If those cannot be known from
the request or repository, the planner should replan or ask the user rather than
letting the Evaluator pass a static-only check.

### 6.4 Plan.md

`Plan.md` explains how the Generator should work and how the Evaluator should verify:

- implementation order;
- first functional batch;
- preflight requirements;
- verification commands;
- Implementation Checklist for `T*` work items;
- Verification Checklist for `TC-*` progress;
- risks;
- fallback and replan triggers.

The plan is not a rigid script. It is a strategy that the model can refine when
evidence proves it wrong. Its checklists are the short-term progress layer:
Generator updates implementation rows, Evaluator updates testcase rows from
evidence, and status summarizes them so the agent does not rely on chat memory.

---

## 7. The AI Planner / Refiner role

AutoMind intentionally uses model capability for Phase 2 planning and refinement.

The deterministic scaffold only creates a starting structure. The model should improve it by:

- splitting broad requests into requirement units;
- identifying non-goals;
- making acceptance criteria testable;
- designing functional, edge, smoke, and quality checks;
- deciding which checks are required for completion;
- choosing the first verification batch;
- identifying cases that require user confirmation;
- making sure the resulting semantics can be materialized into a stable `workflow.json` contract.

This is important because a purely script-template planner cannot understand enough product context. AutoMind wants scripts to enforce the loop, while the model contributes judgment.

---

## 8. Generator context

Generator normally runs in the main coding-agent session. It may use broad context:

- user request;
- current repository files;
- `Brainstorm.md`, `Requirements.md`, `TestCases.md`, `Plan.md`, `workflow.json`;
- `Reuse.md` from previous local lessons;
- previous `Validation.md` and `evaluation.json` for retry rounds;
- relevant logs and evidence paths.

Generator must write `Delivery.md` after each iteration. `Delivery.md` should explain:

- what changed;
- why it changed;
- which `TC-*` checks it targets;
- what should be verified;
- risks or known limitations.

This makes the next Evaluator round focused and auditable.

---

## 9. Evaluator context isolation

Evaluator should not simply be the same conversation saying “now I am the evaluator.” That shares the Generator's assumptions and weakens verification.

Preferred evaluator order:

```text
1. Deterministic platform/script verifier
2. Native isolated subagent/session, if truly isolated and tool-capable
3. Fresh external agent CLI process with context pack only
4. Same-conversation role switch: not accepted as independent evaluation
```

Before a model Evaluator runs, AutoMind can generate:

```bash
automind context-pack <task-code> <iteration>
```

The context pack includes the information an Evaluator needs:

- requirements;
- acceptance criteria;
- test cases;
- plan;
- delivery notes;
- environment constraints;
- prior validation state;
- evidence paths.

It excludes raw Generator transcripts and hidden reasoning. This gives Evaluator enough context to verify the product without inheriting the Generator's private assumptions.

---

## 10. Deterministic verifiers and platform adapters

AutoMind favors deterministic evidence whenever possible.

Generic projects can use:

- project-native tests;
- build commands;
- lint/type checks;
- optional read-only dependency/tooling discovery when setup is unclear;
- `automind script-command` for a known verification command;
- `automind quality-check --merge` for lightweight quality evidence.

Android and iOS projects can use platform-aware checks when local tooling is available:

- preflight for tool/device readiness;
- build/install/launch evidence;
- reviewable UI automation or probe-flow validation;
- screenshots and logs;
- XCUITest evidence on iOS;
- structured failure classification.

UI interaction is a first-class verification capability when the runner exists.
Android probe-flow can encode tap/input/swipe/optional-popup/assertion steps.
iOS can use project/native XCUITest or materialize `probe-flow.ios.json` /
`action-plan.ios.json` into reviewable Swift test intent. AutoMind should not
fall back to “cannot click the app” by default; it should encode the action,
selector confidence, risk, authorization, post-action assertion, and evidence,
or route to `ask_user` / `replan` if the required runner, device, signing, or
selectors are unavailable.

iOS/Android device UI runs follow a delivery-mechanism priority ladder (native
test target, decoupled build + test-without-building, external WebDriverAgent/
go-ios runner, with the IDE-dependent custom-runner host as the known dead end);
the platform specifics live in `docs/references/verification-flow-ios.md` /
`verification-flow-android.md`, split out of the cross-platform
`verification-flow.md`. When every UI-automation tier is exhausted and a page
still cannot be reached through the normal flow, AutoMind may, as a low-fidelity
last resort, temporarily run route/navigation code to load the target page
directly. This is treated as a high-risk verification-unblock change (checkpoint,
record in `verificationUnblockChanges[]`, restore/promote), produces weaker
evidence because the page state is non-standard, and cannot satisfy a required
end-to-end navigation testcase.

Visual/image checks follow the same evidence principle: prefer measurable proof
from logs, DOM/UI hierarchy, bounds, screenshots, diffs, hashes, OCR, or
project-native snapshot/layout tests. AI visual review is a supplementary layer
when screenshots exist and the host model supports image understanding. If no
deterministic or AI-assisted proof can settle a required visual claim, AutoMind
should capture evidence and ask the user to confirm rather than silently pass.

Platform adapters must not invent product goals. They execute the verification intent described in `TestCases.md`, `Plan.md`, and optional probe-flow files.

---

## 11. Structured loop control

Evaluator writes `evaluation.json` as the machine-readable loop control signal. Platform tools differ, but the evidence consumption contract is shared across Android, iOS, Web, server/CLI and script-command adapters: required pass rows normalize into `testResults[]` with concrete evidence paths, `observedSignals`, and a positive `evidenceAssessment` whose `hardMetrics[].evidence` or `machineAnchor` points at a real artifact.

```json
{
  "result": "pass | fail | blocked | in_progress",
  "nextAction": "finish | retry_generator | replan | ask_user | stop",
  "failedChecks": [],
  "evidence": [],
  "testResults": [{
    "testCaseId": "TC-F01",
    "result": "pass",
    "observedSignals": ["expected state/event/log/API result observed"],
    "evidence": ["logs/iter-N/evidence-artifact"],
    "evidenceAssessment": {
      "verdict": "proved",
      "machineAnchor": "logs/iter-N/evidence-artifact",
      "hardMetrics": [{"name": "adapter_result", "passed": true, "evidence": "logs/iter-N/evidence-artifact"}]
    }
  }]
}
```

AutoMind uses this to decide the next step:

- `finish` enters `completion-check` before final success is accepted;
- `retry_generator` feeds evidence back into another Generator round;
- `replan` sends the task back to planning/refinement;
- `ask_user` pauses for a human decision;
- `stop` ends the loop with evidence explaining why.

The key point: the loop is controlled by structured evidence, not by a vague final message.

---

## 12. Workflow and completion gates

### workflow-check

`workflow-check` verifies that the planning artifacts and executable contract are coherent:

```text
Rxx/AC-xxx -> TC-* -> Plan -> workflow.json -> evaluation
```

It also enforces the pre-implementation hard gate: unresolved `ask_user`, pending `decisionBundle.confirmedAt/confirmedBy`, or missing required planning structure must fail before Generator edits product code. If the chain breaks, the model should refine the artifacts before coding further.

### completion-check

`completion-check` prevents false finish. It verifies:

- required `TC-*` cases passed;
- required `AC-*` criteria are covered by passed required cases;
- required evidence paths exist;
- required App/UI/runtime cases have hard evidence plus Evaluator/model `evidenceAssessment.verdict=proved`;
- proved required pass rows have `hardMetrics[].evidence` or `machineAnchor` pointing at an existing artifact, so screenshots/logs/traces are consumed as TC/AC proof rather than loose attachments;
- for `runtimeProofRequired=yes`, at least one required runtime/device testcase passed unless an approved `runtimeDowngradeApproval` exists;
- required clean-build/release/merge cases have attached build evidence plus `evidenceAssessment.verdict=proved`, not just blocker classification;
- `Validation.md` status is consistent with machine state before final reusable records pass.

It writes `VerificationLedger.json` so a reviewer can see exactly why the task is or is not complete.

### record-check

`record-check` verifies that task records are useful enough for future reuse. It helps ensure that completed work produces more than a chat transcript.

---

## 13. How AutoMind keeps looping safely

AutoMind combines multiple safeguards to keep the loop moving without becoming reckless.

```text
Explicit requirements
  + workflow-check
  + Generator delivery notes
  + isolated/deterministic Evaluator
  + structured evaluation.json
  + automatic retry/replan
  + completion-check
  + summary/reuse
```

When a test fails, the agent should diagnose the failure against the mapped requirement path:

```text
failed TC-* -> covered AC-* -> related Rxx -> evidence -> cause category -> next action
```

Typical classifications:

- implementation defect;
- test design mismatch;
- environment/tool/device blocker;
- missing user decision;
- unstable or insufficient evidence;
- quality regression;
- unknown/no-progress.

This lets the agent repair the affected area instead of rewriting the entire task or drifting away from the original request.

---

## 14. Current-session mode versus detached mode

### Current-session skill/slash-command mode

`/automind <request>` and `/automind ask <request>` mean:

- use the current coding agent as Planner and Generator;
- use AutoMind task artifacts and CLI helper gates;
- keep the loop running end-to-end;
- do not start another agent by default.

This mode best matches how users expect slash commands to behave inside coding agents.

### Detached CLI mode

Detached mode is explicit:

```text
/automind detached ask <request>
automind ask "<request>" <agent>
```

It starts non-interactive agent CLI invocations through adapters. For Codex, Claude, and Trae/Coco, AutoMind keeps one task-local primary Planner/Generator session when possible, so implementation and repair can reuse accumulated context. Evaluator remains a fresh isolated invocation and never resumes the Planner/Generator session.

Detached mode still does not reuse the active slash-command chat session. Both modes integrate through the same `.automind/tasks/<task-code>/` artifacts.

---

## 15. Safety and human decisions

AutoMind should continue automatically for low-risk actions, but it must ask before actions with meaningful side effects.

Examples that require user approval:

- destructive changes to app data;
- uninstalling or replacing an existing app when data loss is possible;
- signing/keychain/provisioning changes;
- device trust or security setting changes;
- privileged services or `sudo` operations;
- account, payment, privacy, or legal decisions;
- installing or upgrading large system tools.

Low-risk Python helper setup for Android/iOS/visual verification may be created
in local virtualenvs, but system SDKs and sensitive environment changes are not
silently installed.

For mobile/client behavior tasks, real-device verification is the default when feasible. If AutoMind cannot obtain required runtime/device evidence and still wants to finish, the user must explicitly approve a `runtimeDowngradeApproval` object with `approvedBy`, `approvedAt`, and `reason`. Code keeps backward compatibility for legacy `signedBy`/`signedAt`, but documentation and prompts should use the `approved*` fields consistently.

Target project dependencies are separate from AutoMind helper dependencies.
Web/client/server projects should use their own package manager, lockfiles, and
documented commands. AutoMind may suggest or record commands such as `npm ci`,
`pnpm install --frozen-lockfile`, `yarn install --immutable`, `uv sync --frozen`,
`poetry install --sync`, Gradle/Maven wrappers, or Docker config/build, but it
must not silently change package managers, rewrite lockfiles, install browser
drivers, start Docker/database services, or provide private registry
credentials.

---

## 16. Summary, reuse, and knowledge deposition

AutoMind should get better with use, but not by dumping old chat history into every new task. The goal of summary/reuse is to preserve evidence-backed lessons and route only the relevant ones into future phases.

At terminal or paused states, AutoMind preserves a task-level summary:

```text
.automind/tasks/<task-code>/summary.md
```

The summary should capture what was requested, what changed, what evidence proved or blocked completion, which commands/selectors/verification paths worked, which strategies failed, and what future tasks should reuse or avoid. It is a learning artifact, not a raw transcript.

Reusable knowledge is indexed and phase-scoped:

```text
.automind/tasks/<task-code>/Reuse.md
.automind/tasks/<task-code>/phase-reuse/<phase>.md
.automind/summary/index.jsonl
.automind/summary/raw/**
logs/phase-learnings/<phase>.json
```

`Reuse.md` is a compact task-level manifest. It should point to relevant knowledge-index entries, important reminders, and phase-specific reuse files. Long knowledge belongs in indexed raw files, not inline in every new task.

Phase hooks make reuse timely: before a phase, AutoMind can prepare `phase-reuse/<phase>.md` with matched successful paths, avoid paths, known environment traps, reliable selectors, stable verification commands, or evidence locations. After a phase, it can write phase-learning cards for summary refinement.

AI summary refinement and guarded knowledge actions may promote high-value lessons into the local knowledge index, but only when the lesson is concrete and evidence-backed. Generic advice, stale assumptions, or unproved claims should not become durable reuse.

Reuse is advisory, not absolute. Current `Requirements.md`, `TestCases.md`, `Plan.md`, user decisions, and fresh evidence always win. This is how AutoMind becomes more useful over time: it turns repeated project experience into local, reviewable, phase-targeted memory.

---

## 17. Why this is different

AutoMind is not just a prompt template and not just a script runner.

Its differentiators are the combination of:

1. model-driven requirement/test planning;
2. explicit artifact chain from user request to evidence;
3. deterministic helper gates;
4. context-isolated or deterministic evaluation;
5. structured loop control through `evaluation.json`;
6. completion gating through testcase/acceptance/evidence coverage;
7. safety rules for sensitive actions;
8. local summary memory for future tasks;
9. consistent behavior across Codex, Claude Code, Trae, and CLI usage.

The core product is the loop: a coding agent keeps working, with evidence, until it can prove the task is complete or explain why it cannot proceed.

---

## 18. Practical limitations

AutoMind improves agent reliability, but it does not remove all uncertainty.

Known boundaries:

- verification quality depends on the available project tests and platform tools;
- model planning can still miss requirements, so user review remains valuable for high-risk work;
- mobile/device verification requires correctly configured local platform environments;
- context isolation depends on the host agent or external runtime capabilities;
- summaries help future work but must be curated to avoid stale or overly specific lessons.

The design response is to make those limitations visible through artifacts, evidence, status guidance, and explicit stop conditions.

---

## 19. Mental model

A simple way to understand AutoMind:

```text
Coding agent = intelligence that can plan, code, and diagnose
AutoMind = harness that keeps that intelligence on track
Project tools = source of real evidence
Task artifacts = shared memory and contract
Completion gates = protection against false finish
Summaries = accumulated local learning
```

Together, they turn AI coding from “write code and hope” into a recoverable engineering loop.
