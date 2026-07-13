# Phase 4: Summary / Critic-Refiner

## When to run

By default, generate `.automind/tasks/{task}/summary.md` only for final handoff:
terminal `finish`, explicit `stop`, max-iteration stop, or a durable paused
handoff where CodeAutonomy will not continue automatically without the user. Normal
Generator/Evaluator iterations must not refresh `summary.md` or `Report.html`.

Command mode generates the summary automatically at loop end. In skill/current-session mode, after `completion-check` passes, prefer AI-refined summary before final handoff; `completion-check` and `status` warn when completion is proven but summary/reuse memory is still missing. AI refinement is safe to prefer because CodeAutonomy first builds a deterministic seed and falls back to deterministic summary when the agent is unavailable or returns invalid JSON. It is also safe to regenerate manually:

```bash
./automind.sh summary <task-code> --ai codex      # preferred when an agent is available; falls back to deterministic summary
./automind.sh summary-refine <task-code> codex    # equivalent explicit AI refinement command
./automind.sh summary <task-code>                 # deterministic fallback / no agent required
./automind.sh record-check <task-code>
```

The summary is not just a final report. It is a Critic-Refiner pass: review the task history, identify causes, filter lessons, and convert useful experience into methods future agents can reuse. CodeAutonomy always starts with a deterministic script-filtered seed. Optional AI refinement may then classify and condense that seed, but validated scripts still control what is written to reuse memory.

When Phase 4 runs, surface the reports to the user. The user-facing handoff
should use natural language to say what is done, which reports were generated,
and what the user should inspect first. Tell the user to open `Report.html`
first, especially the `Test Results` rows where each TC has a concise `Key
Evidence` summary with screenshots, direct proof signals, and key files. The
complete artifact list may remain in the final column for traceability but
should not be the primary reading path. Include paths to the development report
(`Delivery.md` when present), the validation report (`Validation.md`), the
machine-readable evaluator result (`evaluation.json`), the completion ledger
(`VerificationLedger.json` when present), the final summary (`summary.md`), and
the latest evidence log directory (`logs/iter-N/`). For runtime/client tasks,
highlight the key machine proof files directly, such as `music-events.txt`,
full logcat/runtime logs, and `runtime-evidence.md`. If the task is paused or
failed at a durable handoff, show these same paths so the user can inspect the
current state and decide whether to resume, replan, or stop.

`Report.html` is a presentation artifact, not an authoritative state source. It
must not participate in routing and must not reopen Generator, Evaluator, or
Planner after terminal finish.

---

## Steps

### Step 1: Collect information

Collect from the task folder:

- `.automind/tasks/{task}/Requirements.md` — canonical requirement units and acceptance criteria
- `.automind/tasks/{task}/Validation.md` — final and historical verification results
- `.automind/tasks/{task}/Delivery.md` — latest delivery notes
- `.automind/tasks/{task}/evaluation.json` — latest structured evaluator result
- `.automind/tasks/{task}/VerificationLedger.json` — completion gate matrix for required `TC-*`, `AC-xxx`, and evidence coverage when available
- `.automind/tasks/{task}/logs/` — iteration logs and evidence

### Step 2: Generate Critic-Refiner summary

Write `.automind/tasks/{task}/summary.md`. Recommended structure:

```markdown
# Summary - {task name} - {date}

## 1. Final result
- **User request**: {original request}
- **Task type**: {script / ios / android / dual}
- **Iterations**: {N}
- **Final status**: {Finished / Failed / Blocked / Aborted}
- **Meets Requirements.md**: {yes / no / partial / blocked}

## 2. Key evidence
- Validation: `.automind/tasks/{task}/Validation.md`
- Evaluation: `.automind/tasks/{task}/evaluation.json`
- Completion ledger: `.automind/tasks/{task}/VerificationLedger.json`
- Logs: `.automind/tasks/{task}/logs/`
- Screenshots / UI hierarchy / logcat / console logs / command output: {paths}

## 3. Key failures and fix path

### {failure or blocker name}
- **Category**: {failure category}
- **Symptom**: {what happened}
- **Root cause**: {cause, not just symptom}
- **Attempts**:
  1. {attempt A} -> {result}
  2. {attempt B} -> {result}
- **Final resolution / current state**: {resolved / still blocked / needs human}

## 4. Reusable lessons
- **{lesson title}**: {specific method, preflight check, tool choice, selector strategy, or other reusable practice}

## 5. Lessons to downgrade or retract
- **Downgrade**: {no longer a default strategy, but usable under conditions}
- **Retract**: {proven unsuitable; do not reuse}
- **Condition**: {when this lesson applies}

## 6. Known successful verification/build paths
| Purpose | Command / method | Preconditions | Evidence | Scope | Reuse confidence |
|---------|------------------|---------------|----------|-------|------------------|
| {build/test/runtime verification goal} | `{exact command or method}` | {cwd/env/device/fixture} | `{log/report/screenshot path}` | {TC/AC/platform scope} | high/medium/low |

## 7. Known failed/deprecated paths
| Path | Failure category | Evidence | Do not retry unless |
|------|------------------|----------|---------------------|
| `{command or method}` | `{build_failure/tool_missing/...}` | `{evidence path}` | {condition changed} |

## 8. Follow-up actions
- [ ] {lesson to write into docs}
- [ ] {prompt/schema/orchestrator update}
- [ ] {next verification task}
```

### Summary anti-rot requirements

When generating the summary, filter experience deliberately:

- Do not treat every symptom as a lesson. Identify causes first, then preserve the lesson.
- Mark conflicting lessons with conditions or priority.
- Downgrade inefficient strategies so they are not used as defaults.
- Retract invalid strategies so future agents do not repeat them.
- Make every lesson concrete and actionable enough to guide the next task.

### Step 3: Append to the lesson base

This is the automatic extraction/refinement step. It happens inside `automind summary <task-code>` after the task is terminal or paused. The command reads `Validation.md`, `Delivery.md`, `evaluation.json`, `VerificationLedger.json`, and `logs/iter-N/commands.md`, then preserves only evidence-backed lessons and structured known successful/failed paths. It does not copy raw logs wholesale.

If `--ai <agent>` or `summary-refine <task-code> <agent>` is used, CodeAutonomy first writes a bounded deterministic seed under `logs/summary-refiner/`, asks the selected agent to produce strict JSON, then validates and normalizes that JSON before merging it into `summary.md` and `.automind/summary/*`. Invalid or unavailable AI output falls back to the deterministic summary.

Append only cause-analyzed and filtered lessons to the local machine lesson base:

```text
.automind/summary/lessons-learned.md
.automind/summary/local-reuse-index.md
```

Do not mechanically copy every log line or failure symptom.

```markdown
# Lessons Learned - General

## {date} - {task type}

### {lesson title}
{specific reusable lesson}

### {next improvement}
{how to handle a similar task better next time}
```

### Step 4: Reuse in the next task

When a new task is created, CodeAutonomy writes:

```text
.automind/tasks/{new-task}/Reuse.md
```

`Reuse.md` is now a compact task-level reuse manifest, not a full dump of historical lessons. It is generated from the knowledge indexes (`.automind/summary/index.jsonl` and `summaries/index.jsonl` when present), short legacy migration overviews (`local-reuse-index.md` / `lessons-learned.md`), and phase-specific reuse files.

The main files are:

```text
.automind/tasks/{task}/Reuse.md                 # task-level manifest / navigation
.automind/tasks/{task}/phase-reuse/{phase}.md   # phase-specific indexed hints
.automind/summary/index.jsonl                   # workspace knowledge routing index
.automind/summary/raw/**                        # single-responsibility raw knowledge files
summaries/index.jsonl                           # runtime/global routing index when present
summaries/raw/**                                # runtime/global raw knowledge files when present
```

`Reuse.md` should include only whole-task policy and relevant index pointers (entry IDs pointing to `phase-reuse/<phase>.md` / raw files). The phase-specific detail — matched values, successful/avoid paths, important reminders — lives in `phase-reuse/<phase>.md`, not inlined into the manifest. It must not inline long raw knowledge. The agent should read the relevant phase-reuse file before entering that phase and load raw files only when the matched entry is relevant. Treat all reuse as guidance only: the current task's `Requirements.md`, `TestCases.md`, `Plan.md`, and fresh evidence always win.

Before choosing commands in a new task, agents should inspect `Reuse.md` matched entries and the relevant `phase-reuse/<phase>.md`. Matching high-confidence successful paths should be copied into `TestCases.md` / `Plan.md` / `Delivery.md` / `Validation.md` with expected evidence, or explicitly rejected with a reason. `workflow-check` warns when `Reuse.md` has matched reuse/phase-reuse entries but planning artifacts do not say whether they were considered.

## Phase hooks and knowledge index

CodeAutonomy runs lightweight phase hooks around key phases. Hooks are generic and can later support checks beyond summary/reuse. The current MVP handlers are:

- `before:<phase>` -> write `phase-reuse/<phase>.md` from knowledge indexes.
- `after:<phase>` -> write a small `logs/phase-learnings/<phase>.json` card for later summary/refinement.

Summary generation may append a compact knowledge-index candidate when it sees evidence-backed successful/avoid paths. AI refinement is optional and should prefer `no_action` when it cannot extract high-value reusable knowledge. Do not produce generic filler: no evidence means no successful path; no retry condition means no avoid path; no concrete future value means no index/raw update.

The AI summary refiner supports guarded knowledge actions:

- `no_action`: deterministic summary is sufficient, or evidence is too thin.
- `upsert_raw`: write a new concise single-responsibility raw knowledge file plus index record.
- `merge_raw`: append a concise evidence-backed section to an existing raw knowledge area plus upsert index.
- `upsert_index`: update index routing when raw already exists or no raw content is needed.

Raw knowledge files should be single-responsibility: for example, keep iOS build knowledge in one raw file, iOS signing/install in another, Android build in another, and visual verification in another. Index records store the raw path, one-sentence value, applicable phases, surfaces, triggers, confidence, and evidence references. AI output is schema-filtered before any raw/index write, and raw paths are constrained to `.automind/summary/raw/**`. Run `automind knowledge evaluate` to check index/raw health: invalid JSON, duplicate ids, missing raw files, invalid phases/surfaces/confidence, oversized raw files, and orphan raw notes. Use `--strict` in CI-like checks when any error should fail the command.

---

## Example summary

```markdown
# Summary - fibonacci - 2026-04-28

## 1. Final result
- **User request**: Build a Fibonacci function that can calculate the value for any n.
- **Task type**: script
- **Iterations**: 3
- **Final status**: Finished
- **Meets Requirements.md**: yes

## 2. Key evidence
- Validation: `.automind/tasks/fibonacci/Validation.md`
- Evaluation: `.automind/tasks/fibonacci/evaluation.json`
- Logs: `.automind/tasks/fibonacci/logs/`

## 3. Key failures and fix path

### Boundary case returned the wrong value
- **Category**: `test_failure`
- **Symptom**: `fibonacci(0)` returned `1`; expected `0`.
- **Root cause**: base case handled `n <= 1` incorrectly for this implementation.
- **Attempts**:
  1. Added direct base cases for `0` and `1` -> pass.
- **Final resolution / current state**: resolved.

## 4. Reusable lessons
- **Handle base cases explicitly**: `fibonacci(0)` and `fibonacci(1)` should return directly before iterative or recursive logic.
- **Avoid naive recursion for larger n**: use iteration or matrix fast exponentiation when performance matters.

## 5. Lessons to downgrade or retract
- **Downgrade**: direct recursion is acceptable for tiny demos only; it should not be the default implementation strategy.
```

---

## Notes

1. Summarize `Finished`, `Failed`, and `Blocked` tasks; failures and blockers are valuable.
2. Analyze causes before preserving lessons; do not turn noise into rules.
3. Downgrade inefficient lessons and retract invalid strategies.
4. Keep artifact paths accurate so future agents can inspect evidence.
5. Make the summary reusable by both humans and agents.

---

## After completion

After the summary is generated, the task ends. CodeAutonomy returns to idle and waits for the next request.

## Run Card / Learning Card and improve suggestions

Phase 4 owns cross-run learning. In addition to `Summary.md`, `lessons-learned.md`,
and `local-reuse-index.md`, CodeAutonomy writes structured learning data:

- `trace.json` — formal task/phase/event trace.
- `run-card.json` — task-local Run Card / Learning Card with result, trace
  summary, successful paths, failed/deprecated paths, lessons, and avoid-repeat
  items.
- `.automind/summary/run-cards.jsonl` — append-only cross-run learning index.

Use:

```bash
automind improve-suggestions [--limit N]
```

The command analyzes recent run cards and proposes improvements such as stronger
process checks, prompt guards, project-local scripts to promote, or skill notes to
extract. It must not auto-edit prompts/docs without human review.
