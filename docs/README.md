# CodeAutonomy Docs

CodeAutonomy is an automated mobile and general-project development assistant for coding agents. It gives agents a recoverable, evidence-driven loop for turning a user request into requirements, implementation, verification, feedback, and reusable learning. The product direction is loop-first rather than prompt-first: prompts guide model judgement, while the loop, gates, evidence, and recovery policy keep execution quality accountable.

---

## Canonical flow at a glance

The canonical loop (`User request -> Brainstorm -> Requirements -> TestCases -> Plan -> workflow.json -> workflow-check -> Generator -> Evaluator -> completion-check -> Report/summary/reuse`) is defined and diagrammed in [`workflow.md`](workflow.md); use it as the default skill/CLI mental model rather than repeating it here.

A few reminders that most affect readers of this index: new tasks use single-file `Requirements.md` (Rxx with inline AC-xxx) only. For UI/runtime tasks, discovery evidence helps the model find a path, but required TC pass requires proof actions plus satisfied postChecks/evidence. At completion, `Report.html` is the primary human review surface: `Test Results` should show a concise per-TC `Key Evidence` summary with screenshots, machine anchors/hardMetrics, and key proof files, while the final response tells the user what was generated and what to inspect first.

---

## Documentation map

This is the full index of CodeAutonomy docs and what each one owns. The authoritative
*mandatory startup read order* lives in the exported `SKILL.md` and in
[`workflow.md`](workflow.md) §4; this map is the on-demand lookup for the complete
document set, so agents can find the right deeper doc when a phase needs it.

1. [`workflow.md`](workflow.md) is the main entry point. It explains the CodeAutonomy file protocol, loop control, functional-first verification, quality-check policy, and record discipline.
2. [`references/state-actions.md`](references/state-actions.md) is the quick lookup for `automind-workflow-state.json`, `runtime-state.json` fields, route actions, Evaluator-to-Generator repair routing, completion authority, and iteration start/end contracts.
3. [`phase1-initialization.md`](phase1-initialization.md) through [`phase4-summary.md`](phase4-summary.md) provide macro phase-cluster rules: initialization → requirements/test design → verification loop → summary.
4. [`phases/`](phases/) contains concrete phase-node guides loaded when entering a node such as brainstorm, requirements, plan, testcases, delivery, evaluation, or completion.
5. [`references/`](references/) contains focused references for testcase runbooks, command/script selection, app-use verification, platform/visual/external-sink verification, probe-flow generation, log evidence/digest handling, dependency checks, and complete feature examples.
6. [`schemas/`](../schemas/) contains machine-readable contracts for artifacts such as `workflow.json`, `automind-workflow-state.json`, `runtime-state.json`, phase sidecars (`brainstorm.json`, `requirements.json`, `plan.json`, `testcases.json`, `pre-implementation-review.json`, `delivery.json`, `completion-report.json`), `evaluation.json`, and `probe-flow*.json`.
7. [`templates/`](../templates/) contains agent prompt contracts for the AI Phase 2 Refiner, Generator, Evaluator, and optional Quality Review layer.
8. [`agent-adapters.md`](agent-adapters.md) defines thin adapter boundaries and skill installation behavior for runtimes such as Codex, Claude Code, and Trae/Trae-CN.

Minimum required files for most implementation tasks are:
[`workflow.md`](workflow.md), [`phase2-requirement.md`](phase2-requirement.md),
[`../templates/phase2_planner_prompt.md`](../templates/phase2_planner_prompt.md),
[`phases/demand-definition.md`](phases/demand-definition.md),
[`phases/verification-execution-planning.md`](phases/verification-execution-planning.md),
[`phase3-verification.md`](phase3-verification.md),
[`../templates/evaluator_prompt.md`](../templates/evaluator_prompt.md), and
[`references/command-script-catalog.md`](references/command-script-catalog.md).
Use [`references/test-design-guide.md`](references/test-design-guide.md) when the Planner needs concrete testcase/runbook examples.
Use platform/reference/adapter docs before choosing platform commands or
external runtimes.

---

## Architecture and organization

- [`agent-adapters.md`](agent-adapters.md) defines the thin adapter boundary for Codex, Claude Code, Trae/Trae-CN, and similar runtimes.
- Public docs describe the runtime contract. Development-only notes are not part of the public skill contract.

---

## Current scope

CodeAutonomy currently focuses on an evidence-driven harness loop, not a complete mobile testing platform. Stable usage is documented in `workflow.md` and the phase docs.

---

## First-time installation

Users can install CodeAutonomy from the remote git repository with:

```bash
curl -fsSL https://raw.githubusercontent.com/leishuai/CodeAutonomy/main/install-curl.sh | bash
```

This is the single supported public install command. It clones CodeAutonomy to `~/.automind/automind` by default (`AUTOMIND_HOME=/custom/path` overrides it), creates `~/.local/bin/codeautonomy` plus the `automind` compatibility wrapper, runs initialization, and installs `/codeautonomy` plus legacy `/automind` integrations for Claude/Codex/Trae/Trae-CN by default. See [`references/installation-runtime.md`](references/installation-runtime.md) for exact runtime/workspace/skill/command path rules.

The installer does not install Android/iOS SDKs or manipulate devices. Mobile helper packages are installed lazily: when a chosen verification path needs low-risk Python helpers, preflight/evaluator may automatically create or repair local helper virtualenvs. Users may also pre-create them explicitly:

```bash
automind setup-automation-tools android
automind setup-automation-tools ios
automind setup-automation-tools visual
```

Those setup commands use version-bounded package specs in the CodeAutonomy runtime `requirements/*.txt`, create project-local Python virtualenvs under the target workspace (`.venv-android-tools` / `.venv-ios-tools` / `.venv-visual-tools`), and install only helper packages such as `adbutils`, `uiautomator2`, `pymobiledevice3`, `Pillow`, `numpy`, or `imagehash`. Transient network/DNS package-index failures are retried once with explicit retry logs; persistent failures are classified and routed to fallback or `ask_user`. They do not install Xcode, Android Studio, Android SDK/platform-tools, `adb`, OCR engines, browser drivers, certificates, signing profiles, keychains, trust settings, or privileged tunnel services.

Web/client/server project dependencies remain project-owned. Prefer project
docs, CI, lockfiles, and `Reuse.md`. When the dependency path is unclear, use:

```bash
automind dependency-check [task-code] [iteration]
```

as an optional read-only discovery aid when the dependency path is unclear. It
reports package managers, lockfiles, missing tools, and candidate project-native
commands. Then run the repository's lockfile or
documented setup only when required by the selected TestCases/Plan. CodeAutonomy
does not silently install system runtimes, Docker/database services, browser
drivers, private registry credentials, signing material, or device trust.

## Examples

For a user-facing first example, see [`../examples/README.md`](../examples/README.md) and [`../examples/offline-script-demo/`](../examples/offline-script-demo/). Run it with:

```bash
./automind.sh smoke offline-demo
```

In a full CodeAutonomy checkout, platform demo projects live under `demos/` and may require local Android/iOS tooling. Public skill exports may omit runnable demo projects and keep only public-safe examples.


## Recommended setup: full CodeAutonomy + skill

The recommended user setup is **Install full CodeAutonomy**. The full CodeAutonomy checkout provides the executable CLI/runtime (`automind`, `./automind.sh`, orchestrator, and documented scripts/adapters), while the exported skill gives Codex/Claude/Trae the workflow, prompts, schemas, and operating rules.

The exported skill package itself is a protocol/docs/templates/schemas/examples package and does not bundle executable runtime scripts. When the full runtime is installed, agents should prefer `automind` / `./automind.sh` commands and may use documented full-checkout scripts/adapters when the command catalog says direct script use is appropriate. If only the skill is available, the agent must first suggest the single full install command (`curl -fsSL https://raw.githubusercontent.com/leishuai/CodeAutonomy/main/install-curl.sh | bash`). If installation is not allowed, it can still follow `workflow.md` manually and use project-native build/test/device commands to produce `Validation.md`, `evaluation.json`, and evidence logs.

## Skill / command export / installation

- Export a public-safe skill package: `./automind.sh export-skill /tmp/codeautonomy-skill`.
- Install public-safe skills into detected local coding agents: `./automind.sh export-skill --install auto`.
- Install explicitly into a detected agent skill directory: `./automind.sh export-skill --install codex` (or `claude`, `trae`, `trae-cn`).
- Export a slash-command package: `./automind.sh export-command /tmp/codeautonomy-command`.
- Install `/codeautonomy` commands for Claude/Codex/Trae/Trae-CN: `./automind.sh export-command --install all`.
- Install `/codeautonomy` explicitly into a detected command directory: `./automind.sh export-command --install codex` (or `claude`, `trae`, `trae-cn`).
- Or choose a verified agent explicitly: `--install claude`, `--install codex`, `--install trae`, or `--install trae-cn`.
- Default user-level targets use `codeautonomy-skill` and `codeautonomy.md`; the installer also writes `automind-skill` and `automind.md` compatibility entries.
- Missing agent roots are skipped by default instead of created. See [agent-adapters.md](agent-adapters.md) and [`references/installation-runtime.md`](references/installation-runtime.md) for exact rules.

---

## Command/script reference

For the canonical mapping from user/agent needs to commands and backing scripts, read [`references/command-script-catalog.md`](references/command-script-catalog.md). Coding agents must prefer the CLI wrapper (`./automind.sh`, `$AUTOMIND_HOME/automind.sh`, or installed `automind`) and only call `scripts/*.py` directly when the catalog marks it as an adapter/debug path.

## Positioning

CodeAutonomy is designed as both a **Skill** and a command-line tool for coding agents such as **Codex / Claude Code / Trae**.

- **Skill mode**: the agent reads `docs/*.md` and follows the guides.
- **Slash-command mode**: the agent triggers `/codeautonomy`; by default this uses the current host-agent session as Planner/Generator and CodeAutonomy CLI helpers/gates such as `scaffold`, `workflow-check`, `phase-gate` (with `checklist[]`/`checkboxMarkdown[]` and deterministic refresh of missing/stale generator/evaluator phase reuse), `context-pack`, and `completion-check`.
  The checklist returned by `phase-gate` is the recommended lightweight TODO/checkbox plan for skill/slash-command current-session work; see [`references/skill-command-driver-checklist.md`](references/skill-command-driver-checklist.md).
- **Detached command mode**: the agent calls `./automind.sh ask/resume ...` through the terminal and lets CodeAutonomy own the loop through separate agent CLI invocations.

Key point: **the agent runs CodeAutonomy through terminal commands**, not through an API.

---

## Core capabilities

| Capability | Description |
|------|------|
| **Requirement/test planning** | Natural language → model-refined Brainstorm / Requirements / TestCases / Plan |
| **Automated development** | Generator writes code using requirements and verification feedback |
| **Automated verification** | Evaluator runs the selected functional batch first, then quality-check, and writes `Validation.md` / `evaluation.json` |
| **Loop iteration** | `evaluation.json.nextAction` decides `finish` / `retry_generator` / `replan` / `ask_user` / `stop` |
| **Workflow continuity** | `workflow-check` refreshes/validates the derived `workflow.json` contract and verifies `Rxx/AC-xxx -> TC-* -> Plan -> workflow.json -> evaluation` before phase handoff or finish |
| **Completion gate** | `completion-check` verifies required `TC-*` pass results, required `AC-xxx` coverage, and evidence before finish |
| **Complete records** | Commands, environment, evidence, `Report.html` with per-TC `Key Evidence`, and final/durable handoff summaries are traceable |
| **Metrics measurement** | Phase/sub-phase/iteration durations, agent call statistics, LLM token usage, warm-build and UI-cache stats, and resource usage — all in standalone `metrics.json` |
| **Build & UI caching** | Warm build pre-compilation and UI path cache significantly reduce repeated build/deploy time across iterations, with cache hit/miss stats tracked in metrics |
| **Audit trail** | Key decisions, logic branches, actions, gate results, policy evaluations, and recovery attempts are recorded in `audit.jsonl` (raw stream) and `audit.json` (summary report) for full traceability of *why* CodeAutonomy did something |
| **Local reuse memory** | Finished or durable paused handoffs generate `summary.md`, append `.automind/summary/*`, and seed the next task's `Reuse.md` |

---

## Quick start

### Option 1: Skill mode (recommended)

Ask the coding agent, for example:

```text
Please build a Fibonacci function generator.
Start with CodeAutonomy Phase 2 and use templates/phase2_planner_prompt.md to refine Demand Definition and Verification & Execution Planning before implementation. In Skill mode, use `workflow.json` and phase sidecars as the structured handoff between steps; Markdown explains the plan, JSON drives checker continuity.
```

The agent reads the docs and runs the full flow.

### Option 2: CLI / TUI mode

```bash
# Open the interactive CodeAutonomy shell from the target project
automind

# Or create a task directly
automind ask "Build a calculator that supports addition, subtraction, multiplication, and division"

# Inspect and interact with the current task
automind status <task-code>
automind tui <task-code> --interactive
automind trace <task-code>
automind process-check <task-code> --soft
automind summary <task-code>

# Current-session scaffold remains available for skill/slash-command flows
automind scaffold "Build a calculator that supports addition, subtraction, multiplication, and division"

# Run regression smoke tests
automind smoke android-self-repair
```

Inside bare `automind`, task commands such as `status`, `trace`, `process-check`,
`tui`, and `resume` use `.automind/current-task` when the task code is omitted.
Natural-language input uses session affinity: with a current task it is recorded
into that task/session and resumed into the coding-agent loop; before a task
exists, the shell creates a per-terminal TUI chat session so exploratory
questions reuse one coding-agent session, and a later `ask ...` in the same
shell can seed the new task from that session. Use `automind report <task-code>`
to generate the human HTML report; users should open it first at final handoff,
then inspect the `Key Evidence` entries and linked runtime proof files.

---

## Supported task types

| Type | Verification method |
|------|----------|
| script | Run script + compare output |
| ios | XcodeBuildMCP for iOS 16+ real devices, or `xcodebuild` + `simctl` for simulators |
| android | `adbutils` + `uiautomator2` for real devices first, with `adb` fallback |
| dual | Verify both iOS and Android paths |

Mobile helper setup is lazy/local: preflight commands such as `android-preflight` or screenshot/app-smoke evaluators can automatically run `automind setup-automation-tools <platform>` when required project-local Python helper packages are missing. If that local setup fails, CodeAutonomy may reuse a ready runtime helper venv; otherwise, or if a high-impact action is required, CodeAutonomy emits `nextAction=ask_user`.

---

## Workflow

See [workflow.md](workflow.md). Core rules:

- Each iteration runs the selected functional batch first; do not insert quality after every functional testcase.
- Functional dependency failures may fail fast; dependent cases should be marked `not_run` / `skipped_dependency`.
- After functional results are acceptable, run lightweight quality-check; use model-based quality review only when needed.
- Quality cases are grouped by category batch. If a quality fix changes runtime/product code, the next iteration starts with the selected/affected functional batch.
- The final control signal is a single merged `evaluation.json`; see [`../schemas/evaluation.schema.json`](../schemas/evaluation.schema.json).

## Probe-flow generation

- [references/test-design-guide.md](references/test-design-guide.md) gives concrete `TestCases.md` runbook and artifact examples.
- [references/command-script-catalog.md](references/command-script-catalog.md) is the canonical map from user/agent needs to `automind` commands and backing scripts.
- [references/probe-flow-generation.md](references/probe-flow-generation.md) explains the unified `Require/TestCases -> probe-flow -> runner -> evaluation` generation flow.
- [references/app-use-verification.md](references/app-use-verification.md) defines app-use path exploration, structured success/failure explanation, soft failures, and action-ladder evidence rules.
- [references/log-evidence-guide.md](references/log-evidence-guide.md) explains digest-first log reading, large-log guardrails, and evidence integrity.
- `examples/probe-flows/*.json` are starter examples, not mandatory templates.
