# CodeAutonomy

[中文](README.zh-CN.md) | English

**CodeAutonomy, formerly AutoMind, is an autonomous coding harness with evidence-driven self-verification, real UI operation, structured recovery, and self-evolving project knowledge.**

It does not replace Codex, Claude Code, Trae, or other agents. It gives them a disciplined execution loop for turning a request into requirements, code changes, real build/test/device/UI evidence, retry/replan decisions, a human-readable report, and reusable project knowledge.

> Give coding agents a harness, not just a prompt.

CodeAutonomy highlights:

- **Highly automated loop** — drives Planner → Generator → Evaluator → repair/re-verify cycles until evidence passes, user input is needed, or a real blocker is proven.
- **Loop over prompt** — uses prompts to guide agents, but relies on the harness loop, gates, evidence, and recovery policy to improve task completion quality.
- **Real UI and device operation** — supports app/UI interaction as a first-class verification path, including Android `adb`/`uiautomator2`, iOS XCUITest/probe-flow, web probe-flow, screenshots, hierarchy, logs, and post-action assertions. Startup/discovery evidence helps find a path; required runtime TCs need proof actions plus satisfied postChecks. See [`docs/references/app-use-verification.md`](docs/references/app-use-verification.md) for app-use path exploration, soft-failure explanation, and action-ladder evidence rules.
- **File-protocol continuity** — keeps phases aligned through Markdown artifacts plus machine-readable JSON contracts such as `workflow.json`, phase sidecars, and `evaluation.json`, reducing model drift between planning, coding, and verification.
- **Structured recovery** — uses `evaluation.json` to decide whether to retry, repair, replan, ask the user, or stop.
- **Human-readable handoff** — generates `Report.html` with per-TC `Key Evidence`,
  screenshots when available, concise runtime proof links, and a natural-language
  final summary that tells users what was done and what to inspect first.
- **Self-evolving knowledge** — writes summaries, reuse indexes, successful paths, and avoid paths so future tasks can reuse proven commands, environments, and project-specific lessons.
- **Evidence over vibes** — prefers deterministic build/test/device/UI evidence over model self-confidence and prevents false finish with `completion-check`.
- **Continuously optimized for speed** — warm-build pre-compilation and a UI path cache cut repeated build/deploy and UI-navigation time across iterations, with cache hit/miss stats tracked in `metrics.json`.
- **Measured and auditable** — records phase/iteration durations, agent-call and LLM token usage in `metrics.json`, and logs every key decision, gate result, and recovery attempt to `audit.jsonl` / `audit.json` so you can trace *why* CodeAutonomy acted — part of an ongoing push to make the loop faster and more transparent.

## How it works

CodeAutonomy runs each task as a self-repairing harness loop:

```text
Request
  -> Brainstorm: clarify intent, context, risks, options, and recommendation
  -> Requirements + TestCases + Plan
  -> workflow.json + phase sidecars preserve the contract between phases
  -> Pre-implementation review: auto-proceed or ask_user for direction/risks/device choice
  -> workflow-check gates coding readiness and catches drift
  -> Generator implements or repairs
  -> Evaluator verifies with build/test/device/UI evidence
  -> if verification fails: evaluation.json routes back to Generator for repair
  -> Generator repairs, then Evaluator re-verifies
  -> repeat until evidence passes, user input is needed, or a real blocker is proven
  -> completion-check gates done
  -> Report.html + natural-language handoff + summaries become reusable project knowledge
```

The key behavior is the repair loop: the Evaluator does not just say "failed". It records evidence and a structured next action, then CodeAutonomy sends the task back to the Generator to fix and re-evaluate. The model is still trusted to understand UI state, choose paths, and diagnose failures; the loop decides when evidence is strong enough. For UI/runtime tasks, proof means executing the relevant action path and satisfying postChecks, not merely launching the app or taking a screenshot. The key continuity mechanism is the file protocol: human-readable Markdown explains intent, while JSON contracts carry phase state, coverage, next actions, and gates so the model cannot silently drift to a different requirement or test target. Before implementation, CodeAutonomy also makes the plan review explicit: low-risk tasks can auto-proceed, while unclear direction, risk, authorization, signing/device choice, or other human decisions become an `ask_user` gate.

---

## Why CodeAutonomy

Modern coding agents are fast, but real engineering work often fails at the edges: vague requirements, missing acceptance criteria, stale tests, environment blockers, weak verification, UI flows that require real interaction, and “done” claims without evidence.

CodeAutonomy helps an agent:

- turn a user request into explicit `Requirements.md`, `TestCases.md`, and `Plan.md`;
- keep implementation and verification connected through task artifacts;
- operate real apps and UI flows when the task requires runtime evidence;
- prefer deterministic build/test/device/UI evidence over model vibes;
- retry, repair, replan, or ask the user based on structured `evaluation.json` results;
- prevent false finish with `completion-check`;
- produce a reviewable `Report.html`, a natural-language handoff pointing to the
  key evidence, plus reusable summaries and local knowledge for future work.

For the design rationale, read [`automind_design.md`](automind_design.md).

---

## Quick start

Install with the bootstrap command:

```bash
curl -fsSL https://raw.githubusercontent.com/leishuai/CodeAutonomy/main/install-curl.sh | bash
```

The installer:

- installs the CodeAutonomy runtime under `~/.automind/automind` by default (`AUTOMIND_HOME=/custom/path` overrides it);
- creates the primary CLI wrapper `~/.local/bin/codeautonomy` and the `automind` compatibility wrapper (`AUTOMIND_BIN_DIR=/custom/bin` overrides the directory);
- runs initialization;
- installs the CodeAutonomy skill and `/codeautonomy` command, plus legacy `automind` aliases, for Claude Code, Codex, Trae, and Trae-CN user folders when available.

Runtime and workspace are separate: CodeAutonomy itself lives under `~/.automind/automind`, while task artifacts are written under the target project workspace (`<workspace>/.automind/tasks/<task-code>/`). The `automind` CLI and `.automind/` workspace directory are retained for compatibility. See [`docs/references/installation-runtime.md`](docs/references/installation-runtime.md) for install paths, runtime-root rules, helper venvs, and coding-agent skill/command targets.

Compatibility policy: `codeautonomy` and `/codeautonomy` are the primary new entrypoints. Existing `automind`, `/automind`, `automind-skill`, `.automind/`, `AUTOMIND_*`, and machine-readable `automind-*` artifact/schema names remain supported so existing tasks and integrations continue to work.

If `~/.local/bin` is not on `PATH`, the installer prints the line to add.

Verify the install:

```bash
automind smoke offline-demo
```

This no-device smoke test creates `.automind/tasks/offline_demo_smoke/` and verifies the basic loop: command evidence, `evaluation.json`, completion check, summary, and record check.

### Updating

To update to the latest release, the recommended way is:

```bash
automind update
```

Alternatively, rerun the same one-line install command:

```bash
curl -fsSL https://raw.githubusercontent.com/leishuai/CodeAutonomy/main/install-curl.sh | bash
```

Install and update are the same flow: CodeAutonomy fetches the latest version, refreshes the runtime under `~/.automind/automind`, and reinstalls the agent skill/command files. Your local data is preserved — task artifacts and reuse memory under `.automind/tasks/` and `.automind/summary/` are never removed. Pin a specific version with `AUTOMIND_BRANCH`:

```bash
curl -fsSL https://raw.githubusercontent.com/leishuai/CodeAutonomy/main/install-curl.sh | AUTOMIND_BRANCH=v0.2.0 bash
```

---

## Usage

### In Codex / Claude Code / Trae

After installing, restart or reload your coding agent, then run:

```text
/codeautonomy Fix the login crash and verify it
```

Equivalent current-session form:

```text
/codeautonomy ask Fix the login crash and verify it
```

In slash-command mode, CodeAutonomy keeps the current coding-agent session as Planner/Generator, creates task artifacts under the target project's `.automind/tasks/<task-code>/`, runs helper gates, and keeps looping Evaluator verify -> Generator repair -> Evaluator re-verify until evidence passes, user input is needed, or a real blocker/max-iteration guard occurs.

It does **not** start a separate agent session by default.

### In a terminal

Run commands from the target project root so `.automind/tasks` is created in that project:

```bash
cd /path/to/your-project
automind
```

Bare `automind` opens the interactive shell. If you chat before a current task exists, that terminal gets its own hidden TUI chat session, and a later `ask ...` in the same shell can reuse that coding-agent session.

To let CodeAutonomy own a separate CLI-driven loop:

```bash
automind ask "Fix the login crash and verify it"
```

When no agent is specified, `ask`, `plan`, and `resume` use `auto` selection: CodeAutonomy tries `codex`, then `claude`, then Trae/Trae-CN and runs the first CLI that passes preflight; if none are available, it reports the checked adapters and suggests current-session mode. Planner/Generator approval-bypass follows the task-level `agentExecutionPolicy` in `runtime-state.json` (missing policy falls back to bypass), so automation stays high while sensitive/destructive/system-changing actions still route through CodeAutonomy's `ask_user` guard. Model Evaluator runs are always fresh-isolated and bypassed so they can collect evidence without approval deadlocks.

If an agent shell is not in the target project root, set `AUTOMIND_WORKSPACE_ROOT=/path/to/project` first. Runtime and workspace are separate: the runtime stays under `~/.automind/automind` (or `$AUTOMIND_HOME`), while task artifacts live in the project where CodeAutonomy runs. Resolve helper paths from the current runtime/workspace or task `logs/iter-N/env.json`, not from absolute paths copied out of old logs.

---

## Choose a mode

| Need | Use |
|---|---|
| You are already inside Codex / Claude Code / Trae | `/codeautonomy <request>` |
| You want an interactive terminal shell | `automind` |
| You want CodeAutonomy to own a separate CLI-driven loop | `automind ask "..."` |
| You want detached mode from a slash command | `/codeautonomy detached ask <request>` |
| You only need task artifacts for current-session work | `automind scaffold "..."` |
| You need to continue a previous task | `automind resume <task-code>` or `automind continue [task-code]` |

Advanced helper/gate commands such as `scaffold`, `workflow-contract`, `phase-gate`, `context-pack`, `completion-check`, and `record-check` are mainly for the CodeAutonomy skill, slash-command current-session flow, CI/regression fixtures, and debugging. New users normally do not need to run them manually.

---

## Full-auto mode (run to completion without interruption)

By default, non-trivial implementation tasks pause once at the pre-implementation review to confirm scope, approach, risks, and authorization. If you want CodeAutonomy to run end-to-end autonomously without that interruption, opt into **full-auto mode**:

- **Declare it in the original request** — include a full-auto phrase such as `full auto` or `no confirmation`, e.g.:

  ```text
  /codeautonomy Fix the login crash and verify it, full auto
  ```
  ```bash
  automind ask "Fix the login crash and verify it, full auto"
  ```

- **Or choose it when prompted** — at the pre-implementation `ask_user` gate, select the `Full auto mode` option, or simply reply `full auto`.

Once enabled, CodeAutonomy records your stated scope/goals/authorization in `runtime-state.json` (`preImplementationReview.fullAuto=true`) and auto-proceeds at every subsequent completion gate without asking again. This is the user-intent override: it authoritatively skips the ask_user pauses the risk model would otherwise raise.

**What full-auto does not bypass:** truly sensitive/irreversible actions you did not pre-authorize — account/credential login, payment, destructive delete/reset/force-push, or a real device/signing/permission gate — still surface for a decision, and the host coding agent's own command-approval prompts may still appear. To pre-authorize specific destructive actions, list them in the pre-implementation decision bundle's `destructiveActionsAllowList`.

---

## Common commands

### Start and resume

```bash
automind                         # interactive shell
automind ask "Fix login crash"    # start a CLI-owned loop
automind list                    # list tasks
automind resume <task-code>      # resume from persisted state
automind continue [task-code]    # print the shared next-step instruction
automind answer <task-code> --text "..."  # answer a pending ask_user decision
```

### Inspect and report

```bash
automind status <task-code>        # state, next action, suggested commands, gate summaries
automind tui <task-code>           # TUI snapshot/watch/interactive view
automind notifications <task-code> # tail long-running task notifications
automind doctor <task-code>        # diagnose stale or long-running tasks
automind report <task-code>        # generate Report.html
automind logs [task-code]          # show logs
```

`report` generates `.automind/tasks/<task-code>/Report.html`, a human-readable handoff page with requirements, blockers, and summary/knowledge deposition. In `Test Results`, each TC row should show a concise `Key Evidence` column first: screenshots, machine anchors / hardMetrics, and the few runtime proof files users should inspect. The full `Evidence / Screenshots / Logs` artifact list remains available for traceability without becoming the primary reading path.

### Gates and helpers

The loop is kept honest by gates (`workflow-check` before coding,
`completion-check` before finish) and by verification/summary helpers
(`script-command`, `quality-check`, `setup-automation-tools`, `visual-inspect`,
`summary`, `reuse`, `checkpoint`, and more). Most users do not run these
directly. Run `automind help` for the full command list, and see
[automind_design.md](automind_design.md) for the contracts behind the gates.

Safe runtime interruptions (agent timeout/network/process hiccups,
context-window overflow) are retried and resumed automatically from the durable
task artifacts.

---

## How a task flows

```text
User request
  -> Brainstorm.md
  -> Requirements.md
  -> TestCases.md
  -> Plan.md
  -> workflow.json + phase JSON sidecars
  -> Pre-implementation review / ask_user when needed
  -> workflow-check
  -> Generator -> Delivery.md
  -> Evaluator -> Validation.md + evaluation.json
  -> Retry Generator / Replan / Ask Human / Stop / Finish
  -> completion-check
  -> Report.html + natural-language handoff + Summary / Reuse
```

The important idea is continuity:

1. **Brainstorm first** — clarify user intent, project context, assumptions, risks, options, recommendation, and verification strategy before freezing requirements.
2. **Define the contract** — turn the chosen direction into `Requirements.md`, acceptance criteria, `TestCases.md`, and `Plan.md`.
3. **Preserve continuity with file protocol** — `workflow.json` and phase JSON sidecars carry requirements, AC/TC coverage, gate state, and handoff state between phases so the next model turn does not reinterpret the task from scratch.
4. **Review before coding** — resolve the pre-implementation gate: auto-proceed for clear/low-risk work, or `ask_user` for unclear direction, risk, authorization, signing/device choice, or other human decisions.
5. **Check before coding** — `workflow-check` catches gaps and model drift before implementation.
6. **Generate or repair** — the current agent or detached adapter changes code/config/docs and writes `Delivery.md`.
7. **Evaluate with evidence** — prefer project tests, build commands, device/UI probes, logs, screenshots, and other concrete evidence. Model evaluation should be isolated from Generator context when used.
8. **Self-repair from `evaluation.json`** — failed evidence routes back to Generator for repair, then Evaluator re-verifies; `finish`, `retry_generator`, `replan`, `ask_user`, or `stop` drives the next step.
9. **Gate completion** — `completion-check` prevents false finish.
10. **Report and reuse** — `Report.html`, the final natural-language handoff,
    `summary.md`, and reuse records make the result reviewable and useful later.

For the full workflow contract, see [`docs/workflow.md`](docs/workflow.md).

---

## What CodeAutonomy produces

Each task gets a workspace under:

```text
.automind/tasks/<task-code>/
```

The files most users care about are:

- **Planning:** `Brainstorm.md`, `Requirements.md`, `TestCases.md`, `Plan.md`
- **Implementation handoff:** `Delivery.md`
- **Verification:** `Validation.md`, `evaluation.json`, `logs/iter-N/*`
- **Completion proof:** `VerificationLedger.json`, `completion-report.json`
- **Human review:** `Report.html` with per-TC `Key Evidence`, screenshots for runtime/UI TCs when available, and concise links to the proof files users should inspect first.
- **Reuse:** `summary.md`, `Reuse.md`

Most users should inspect tasks with:

```bash
automind status <task-code>
automind report <task-code>
automind summary <task-code>
```

CodeAutonomy also maintains machine-readable state, sidecars, traces, process evals, and knowledge indexes for gates, adapters, TUI/status, and future reuse. The full file protocol is documented in [`docs/workflow.md`](docs/workflow.md).

Repository layout:

```text
.
├── automind.sh        # CLI entry point used by the wrapper
├── install.sh         # one-command installer
├── orchestrator/      # loop engine, runtime state, gates, context packs
├── scripts/           # execution/evidence adapters and export helpers
├── requirements/      # optional mobile helper constraints
├── docs/              # workflow and references
├── schemas/           # machine-readable contracts
├── templates/         # planner/generator/evaluator prompts
├── examples/          # public-safe starter examples
├── summaries/         # curated reusable technical lessons
└── .automind/         # generated local runtime data
```

---

## Examples

Start with:

- [`examples/README.md`](examples/README.md)
- [`examples/offline-script-demo/`](examples/offline-script-demo/)

Best first run:

```bash
automind smoke offline-demo
automind status offline_demo_smoke
automind summary offline_demo_smoke
automind record-check offline_demo_smoke
```

Platform demos may require local Android/iOS tooling, simulators, or devices.

---

## Dependencies and safety policy

Basic install requires `bash` (or a compatible shell), `git`, and `python3`.

The public installer does **not** install or modify system SDKs (Xcode, Android
Studio, Android SDK/platform-tools, `adb`), iOS signing/keychains/profiles, or
device trust settings. When mobile/visual verification needs low-risk Python
helpers, CodeAutonomy may create local virtualenvs (`.venv-android-tools/`,
`.venv-ios-tools/`, `.venv-visual-tools/`) in the target workspace via
`automind setup-automation-tools [android|ios|visual]`.

For web/client/server dependencies, CodeAutonomy uses the target project's native
commands and lockfiles rather than installing arbitrary packages;
`automind dependency-check` is an optional read-only aid when the path is
unclear. Sensitive or system-changing actions — signing, trust changes, sudo
services, browser drivers, destructive app actions, registry credentials,
Docker/database startup, account/payment/privacy/legal decisions — require
explicit user approval. Environment, device, signing, and permission failures
are blockers, not product-code failures.

---

## Troubleshooting

### `automind: command not found`

Add the wrapper directory to your shell profile, usually:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Then restart the shell and run `automind help`.

### `/codeautonomy` is not visible

Restart or reload the coding agent. Confirm the relevant user-level files exist, for example `~/.codex/commands/codeautonomy.md` and `~/.codex/skills/codeautonomy-skill` for Codex. Legacy `/automind` remains supported.

### Mobile tooling is missing

Install the required platform tooling manually and rerun preflight. CodeAutonomy can create Python helper virtualenvs, but it does not install Xcode, Android Studio, SDKs, signing assets, or device trust settings.

### The loop keeps failing

Run:

```bash
automind status <task-code>
automind workflow-check <task-code>
automind completion-check <task-code>
automind doctor <task-code>
```

If the same failure repeats, CodeAutonomy may keep giving the model repair attempts; use `replan` when evidence shows the strategy or validation target is wrong.

### The agent says done but completion fails

Trust `completion-check`. Add missing evidence, fix failed `TC-*`, cover missing `AC-*`, or repair the implementation before claiming completion.

---

## Important docs

- [`automind_design.md`](automind_design.md) — product idea, design principles, and reliability mechanisms.
- [`docs/workflow.md`](docs/workflow.md) — canonical file protocol and loop semantics.
- [`docs/tui-session-observability.md`](docs/tui-session-observability.md) — TUI, shared sessions, traces, process evals, and status/report observability.
- [`docs/README.md`](docs/README.md) — documentation map.
- [`docs/references/installation-runtime.md`](docs/references/installation-runtime.md) — install paths, runtime root, workspace root, helper venvs, and coding-agent skill/command targets.
- [`docs/references/command-script-catalog.md`](docs/references/command-script-catalog.md) — command/script selection guide.
- [`docs/references/app-use-verification.md`](docs/references/app-use-verification.md) — app-use verification contract for UI path exploration, structured success/failure explanation, and launch/action ladders.
- [`docs/references/verification-flow.md`](docs/references/verification-flow.md) — cross-platform verification command flow, with [`verification-flow-ios.md`](docs/references/verification-flow-ios.md) and [`verification-flow-android.md`](docs/references/verification-flow-android.md) for per-platform device flows and the UI-runner ladder.
- [`docs/agent-adapters.md`](docs/agent-adapters.md) — detached adapters and Evaluator isolation.
- [`docs/phase1-initialization.md`](docs/phase1-initialization.md) — environment/task workspace setup.
- [`docs/phase2-requirement.md`](docs/phase2-requirement.md) — model-driven planning/refinement.
- [`docs/references/test-design-guide.md`](docs/references/test-design-guide.md) — concrete `TestCases.md` runbook and artifact examples.
- [`docs/phase3-verification.md`](docs/phase3-verification.md) — verification and Evaluator behavior.
- [`docs/phase4-summary.md`](docs/phase4-summary.md) — summary, reuse, and knowledge deposition.
- [`schemas/runtime-state.schema.json`](schemas/runtime-state.schema.json), [`schemas/evaluation.schema.json`](schemas/evaluation.schema.json), [`schemas/probe-flow.schema.json`](schemas/probe-flow.schema.json) — machine-readable contracts.

---

## What CodeAutonomy is not

CodeAutonomy is not another coding agent, a replacement for project-native tests or platform SDKs, a tool that silently bypasses signing/permissions, or a guarantee of correctness.

It is the engineering loop around coding agents: preflight, plan, generate, verify, recover, summarize, report, and reuse.
