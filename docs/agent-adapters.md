# Agent-specific Adapters

CodeAutonomy is agent-agnostic at its core. Codex, Claude Code, Trae/Trae-CN, Cursor, or future agents should all consume the same CodeAutonomy task artifacts:

```text
Requirements.md
Plan.md
Delivery.md
Validation.md
evaluation.json
automind-workflow-state.json
stages/*-stage-state.json        # stage-local control payloads
runtime-state.json.stateSummary  # obsolete fallback for older tasks only
runtime-state.json  # runtime/resume projection
summary.md
logs/iter-N/
```

An agent-specific adapter is only the thin boundary that translates CodeAutonomy's generic invocation contract into a concrete agent runtime command. Installation paths, runtime root, workspace root, helper virtualenvs, and user-level skill/command target folders are defined in [`references/installation-runtime.md`](references/installation-runtime.md); adapters must not hard-code developer-machine absolute paths from old logs.

```text
CodeAutonomy loop
  -> AgentAdapter.prepare(prompt, task_dir, role)
  -> concrete CLI/runtime
  -> AgentResult(exit_code, output, metadata)
  -> CodeAutonomy evaluation / runtime-state
```

## What belongs in an adapter

Adapters should handle only runtime-specific differences:

1. **Discovery / preflight**
   - Is the binary installed?
   - Does `--version` or equivalent work?
   - Is the current runtime likely usable before starting a real task?

2. **Command construction**
   - Codex command shape.
   - Claude Code command shape.
   - Trae/Trae-CN command shape.
   - Future Cursor/Gemini/OpenCode command shape.

3. **Working directory policy**
   - Whether the agent needs a git repo.
   - Whether it supports an explicit cwd flag.
   - CodeAutonomy must run agent/project subprocesses with the target workspace root as cwd. `AUTOMIND_ROOT` is only the installed runtime/scripts/templates root.

4. **Permission / sandbox flags**
   - Claude Code: `--print --permission-mode bypassPermissions`.
   - Codex: `exec --sandbox workspace-write --skip-git-repo-check -C <root>`.
   - Trae/Trae-CN: allowed tools and JSON mode.

5. **Output normalization**
   - Combine stdout/stderr.
   - Preserve raw output under task logs when needed.
   - Return a uniform `{exitCode, output, adapter, commandPreview}` shape.

## What does not belong in an adapter

Adapters should not own CodeAutonomy's product logic:

- runtime state machine;
- `evaluation.json` schema;
- failure classification;
- Android/iOS/script evaluator logic;
- probe-flow schema;
- requirement or summary format;
- sensitive-action policy.

Those stay in CodeAutonomy core so every agent follows the same loop.

## Recommended interface

A minimal Python-side shape is enough for V2:

```python
@dataclass(frozen=True)
class AgentSpec:
    name: str
    binary: str
    probe: list[str]
    description: str

class AgentAdapter(Protocol):
    spec: AgentSpec

    def preflight(self) -> tuple[bool, dict]: ...
    def build_command(self, prompt: str, task_dir: Path) -> list[str]: ...
    def run(self, prompt: str, task_dir: Path) -> tuple[int, str]: ...
```

For the current codebase, a dictionary-based adapter registry is sufficient and keeps the project simple.

## Current adapter registry

CodeAutonomy currently supports these thin CLI adapters:

| Agent | Binary | Current command shape | Notes |
|---|---|---|---|
| `codex` | `codex` | Evaluator always: fresh `codex --dangerously-bypass-approvals-and-sandbox exec --skip-git-repo-check -C <AUTOMIND_WORKSPACE_ROOT> <prompt>`; Planner/Generator uses the task-level `agentExecutionPolicy`: bypass => dangerous bypass, normal => `codex --ask-for-approval on-request exec --sandbox workspace-write ...`. Primary Codex sessions are reused only when their execution mode matches the current task policy. | Evaluator is always fresh-isolated and bypassed for evidence collection, across all Coding Agents. Planner/Generator also default to bypass for supported coding-agent CLIs; new TUI tasks record this as `default_bypass` without prompting. |
| `claude` | `claude` | Planner/Generator uses task-level `agentExecutionPolicy`: bypass => `claude --print --dangerously-skip-permissions --permission-mode bypassPermissions --session-id <uuid> <prompt>`, normal => `claude --print --session-id <uuid> <prompt>`; Evaluator: fresh `claude --print --dangerously-skip-permissions ... <prompt>` | Planner/Generator follows the shared task policy; Evaluator is always bypassed. |
| `trae` / `trae-cn` | `traecli` | Planner/Generator: `traecli -p <prompt> --session-id/--resume <uuid> --allowed-tool ... --yolo --json`; Evaluator: fresh `traecli -p ... --yolo --json` | Both adapter names map to the same Trae CLI runtime. `trae-cn` is accepted as a detached CLI alias and also matches the Trae-CN skill/command install target. Detached/non-TTY command mode defaults to YOLO/tool auto-approval for high automation while CodeAutonomy prompt-level unsafe-action guards still require `ask_user`. |

`auto` is the default detached CLI selection mode for `ask`, `plan`, and `resume`. It tries `codex`, then `claude`, then `trae`/`trae-cn` (`traecli`); the first adapter whose binary and `--version` probe pass is used for the actual run. If none pass, CodeAutonomy returns a full preflight diagnostic and recommends installing/configuring a supported CLI or using current-session `scaffold`/`/codeautonomy` mode.

## Evaluator isolation across coding agents

CodeAutonomy should not assume every tool has a native "subagent" primitive. The portable guarantee is stricter and simpler:

1. **Primary implementation session**: Planner, Generator, and Generator repair may reuse one persistent primary agent session for the same task when the CLI supports it. This preserves the implementation narrative and lets repair prompts benefit from prior planning/build context.
2. **Fresh bypassed Evaluator invocation**: Evaluator is never resumed from the Planner/Generator primary session. It must start as a fresh isolated process/session or a deterministic verifier, and Coding-Agent Evaluator invocations must always use the high-automation bypass mode for that agent (Codex dangerous bypass, Claude skip permissions, Trae/Trae-CN YOLO). The task-level Planner/Generator policy does not affect Evaluator.
3. **Task-level Planner/Generator execution policy**: `runtime-state.json.agentExecutionPolicy` records Planner/Generator bypass across Codex/Claude/Trae/Trae-CN. Missing policy falls back to bypass so non-new tasks, detached scripts, resume/helper commands, or flows that cannot ask still keep high automation. New TUI tasks no longer ask about this default; they record `default_bypass` for audit. Detached scripts, resume, and helper commands do not prompt. If bypass state changes in older tasks, do not reuse a primary session created under the opposite execution mode. The legacy `codexDangerousBypass` field is compatibility-only.
3. **Context-pack only for Evaluator**: Evaluator receives `logs/iter-N/evaluator-context.md/json` as the only orchestrator-provided task context.
4. **Policy validation gate**: before launching an agent Evaluator, CodeAutonomy validates the pack:
   - required files exist and are non-empty;
   - optional files are included only if present;
   - forbidden raw Generator context is not embedded.
5. **Independent verification allowed**: Evaluator may inspect source/product files and run commands, but must not read raw Generator logs/transcripts or inherit the primary implementation session.

The intended evaluator shape is **complete, audited, non-redundant context + no Generator reasoning pollution + full independent verification capability**. Context isolation must not weaken the Evaluator into a prose reviewer. When platform config exists, the Evaluator must use the same real app verification capabilities CodeAutonomy already provides: Android preflight/probe-flow, iOS preflight/probe-flow/XCUITest, script-command, unit tests, build/install/launch/log/screenshot evidence.

Native subagents are optional optimizations, not the baseline contract. If a future adapter supports native isolated sessions, it may use them, but it must still consume the same context pack and pass the same validation gate.

Result exchange is file/artifact based. A current host session, a native isolated subagent, a deterministic verifier, and a detached external CLI process must all communicate through the same task directory:

```text
.automind/tasks/<task>/
  automind-workflow-state.json
  stages/*-stage-state.json        # stage-local control payloads
runtime-state.json.stateSummary  # obsolete fallback for older tasks only
  runtime-state.json  # runtime/resume projection
  evaluation.json
  Validation.md
  Delivery.md
  VerificationLedger.json
  logs/iter-N/*
```

Do not depend on private chat memory to transfer results between Evaluator, deterministic verifiers, detached loops, or resumed work. Planner/Generator may reuse a primary implementation session, but the durable integration contract remains `automind-workflow-state.json` for workflow routing plus `evaluation.json`, `runtime-state.json`, `Delivery.md`, `Validation.md`, and evidence paths. Evaluator results must be written to those artifacts and then fed back to the primary Generator session as ordinary file/prompt context.

### Context budget and artifact handoff

CodeAutonomy must keep raw evidence on disk, not in the coding-agent conversation. Context packs use a tiered policy: core contract files such as `Requirements.md`, `TestCases.md`, `Plan.md`, and `Brainstorm.md` get much larger/full excerpts so task intent is not lost; history/evidence files such as `Delivery.md` and `Validation.md` use structured excerpts (headings, key result/evidence lines, latest sections) when large; build logs, probe logs, screenshots, DB dumps, and raw agent transcripts remain authoritative artifacts under the task directory and are reached through `log-digest.md` plus targeted `grep`/`tail`/line-range reads. If an agent hits `agent_context_overflow`, CodeAutonomy should clear the saturated primary session and resume through a fresh session using durable task artifacts as the handoff.

Current adapter interpretation:

| Agent | Isolation mechanism | Subagent required? | Notes |
|---|---|---:|---|
| `codex` | Planner/Generator use one primary `codex exec` session, then `codex exec resume <session-id>`; Evaluator uses a separate fresh `codex exec ... <evaluator_prompt>` with context-pack-only prompt | No | Primary session reuse is for implementation continuity only. Evaluator must not resume that session. |
| `claude` | Planner/Generator use one primary `claude --print --session-id <uuid>` session; Evaluator uses a separate fresh `claude --print ... <evaluator_prompt>` | No | `--session-id` is for Planner/Generator continuity only. Evaluator must not reuse it. |
| `trae`/`trae-cn` | Planner/Generator use one primary `traecli -p ... --session-id/--resume <uuid>` session; Evaluator uses a separate fresh `traecli -p ... <evaluator_prompt>` | No | `trae` and `trae-cn` are aliases for the same Trae CLI adapter. Primary session reuse is for implementation continuity only. Evaluator must not reuse it. |
| Future OpenClaw/native | `sessions_spawn(context=isolated)` or equivalent | Optional/preferred | If available, use isolated subagent/session, but still feed only the context pack. |

A tool that can only continue the same conversation/session for Evaluator is **not acceptable** for context-isolated Evaluator mode. It should be marked unsupported for agent Evaluator and fall back to script/platform evaluator or blocked/replan.

### Skill mode vs command mode

In **detached command mode** (`./automind.sh ask/resume ...`), CodeAutonomy owns the loop. For Codex, Claude, and Trae/Trae-CN, CodeAutonomy keeps a task-local primary Planner/Generator session when possible, recorded in `runtime-state.json.agentSessions.primary`. Evaluator is always a fresh isolated and bypassed invocation that receives only the validated evaluator context pack. Detached mode still does not reuse the current slash-command conversation.

In **skill/slash-command current-session mode** (the CodeAutonomy skill or `/codeautonomy` command is installed inside Claude Code, Trae/Trae-CN, Codex, etc.), the host coding agent is usually the Planner/Generator/main session. That main session may keep full project and conversation context. The CLI may still be used for deterministic helpers such as `scaffold`, `workflow-check` (refresh/validate `workflow.json`), `context-pack`, `script-command`, `completion-check`, `summary`, and `record-check`. But Evaluator isolation is not automatic. The host agent must choose one of these patterns:

1. **Default for slash commands: current session for Planner/Generator + CLI gates**
   - Run `./automind.sh scaffold "<request>"` (or installed `automind scaffold "<request>"`) to create the task container without launching another agent.
   - Refine `Brainstorm.md`, `Requirements.md`, `TestCases.md`, and `Plan.md` in the current session, then run `workflow-check` to refresh/validate derived `workflow.json`.
   - Use JSON sidecars as the handoff spine, not just Markdown/chat memory: read `automind-workflow-state.json`, `workflow.json`, and upstream sidecars before choosing the next macro action; after phase edits, use `workflow-check`, `phase-gate`, and the returned `checklist[]`/`checkboxMarkdown[]` to refresh/schema-check sidecars and drive the native TODO plan; use `evaluation.json.nextAction` and `completion-report.json` as local resolver signals rather than prose confidence.
   - Before code changes, record the pre-implementation review decision in `Brainstorm.md` and `runtime-state.json.planner.preImplementationReview`. This is a mandatory gate. Brainstorm must proactively expand the request, compare approaches, recommend one, and normally ask the user to confirm the conclusion before non-trivial implementation. Continue only for `auto_proceed` or after a resolved user confirmation; pause for `ask_user` or `replan`. After the gate is resolved, keep the harness loop moving through Generator implement/repair -> Evaluator verify/re-verify -> completion gate until finish is proven or a real stop condition occurs.
   - Before a model Evaluator, run `context-pack <task-code> [iteration]` and pass only that pack to an isolated evaluator.

2. **Native isolated session/subagent, if the host supports it**
   - Launch a new isolated session/subagent and pass only `logs/iter-N/evaluator-context.md/json` plus the evaluator prompt.
   - Do not fork/copy the Generator conversation.

3. **Fresh external CLI invocation**
   - From the main session, run a separate non-interactive command such as `claude --print`, `codex exec`, or `traecli -p` with the evaluator prompt and context pack path.
   - The subprocess output is saved as Evaluator output; the parent session must not summarize hidden Generator context into the prompt.

4. **Deterministic platform/script evaluator**
   - Prefer this when available: Android/iOS probe-flow runners, XCUITest, script-command, unit tests, etc.
   - For App/UI work, deterministic platform evaluators are action-capable: Android probe-flow can tap/input/swipe/assert; iOS XCUITest/probe-flow can materialize and run tap/input/scroll/assert flows. Do not weaken the Evaluator into a passive screenshot/prose reviewer when these paths are available. Follow `docs/references/app-use-verification.md` for source UI maps, action ladders, soft failures, and structured `uiExploration` evidence.
   - This avoids model-context contamination entirely.

5. **Explicit detached CLI ownership**
   - Run `./automind.sh ask ...` or `./automind.sh resume ...` only when the user wants CodeAutonomy to own a background/detached loop through an adapter.
   - In Codex/Claude/Trae/Trae-CN this starts a new agent CLI process/session rather than reusing the slash-command conversation.

If none of these is possible, the skill must not claim independent evaluation. It should write `evaluation.json` as `blocked`/`replan` with category `invalid_evaluation_output` or `needs_replan`.

Recommended Evaluator preference order:

```text
1. Deterministic platform/script verifier
2. Native isolated subagent/session, if truly isolated and tool-capable
3. Fresh external agent CLI process with context pack only
4. Same conversation "role switch" evaluator — not acceptable for independent evaluation
```

## Adapter-specific prompt differences

Prefer one common prompt template. Only add adapter-specific prompt text when it solves a real observed issue.

Good adapter-specific additions:

- "You are running under Claude Code print mode; write final status clearly to stdout."
- "You are running under JSON-output Trae/Trae-CN mode; do not rely on interactive prompts."
- "Codex may require a trusted git workspace; CodeAutonomy is passing `--skip-git-repo-check`."

Bad additions:

- Duplicating the whole Generator/Evaluator prompt per agent.
- Changing the CodeAutonomy task file protocol per agent.
- Letting one adapter silently skip evidence or structured evaluation.

## Failure mapping

All adapter failures should normalize into CodeAutonomy categories:

| Adapter symptom | CodeAutonomy category | Typical nextAction |
|---|---|---|
| binary missing | `agent_unavailable` | safe auto-recovery: record `autoRecovery.selected=resume_after_recovery`, keep artifacts, and retry/resume without `ask_user`; ask only if the chosen fix installs software, changes system config, switches agent/account, or expands access |
| `--version` fails | `agent_unavailable` | safe auto-recovery: retry/resume after auth/runtime recovers without `ask_user`; ask only for credentials, provider/account changes, installs, or system configuration changes |
| auth/provider/model error | `agent_unavailable` | safe auto-recovery for transient provider/model/runtime outages; `ask_user` for credentials, account switching, model/provider policy decisions, or access expansion |
| CLI process exceeds CodeAutonomy timeout | `agent_timeout` | safe auto-recovery: keep artifacts and retry/resume without `ask_user`; increasing timeout is also safe when bounded and local. Default detached agent timeout is 43200 seconds. |
| CLI process produces no stdout/stderr for the idle watchdog window | `agent_stalled_no_output` | safe auto-recovery: keep artifacts and retry/resume without `ask_user`; this means agent progress is unobservable, not that product validation failed. Default idle-output timeout is 1800 seconds (`AUTOMIND_AGENT_IDLE_TIMEOUT_SECONDS`). |
| CLI exits non-zero before code change | `agent_unavailable` or `unknown` | safe auto-recovery for known runtime categories; otherwise `retry_generator`, deterministic verifier, or `ask_user` only for unsafe/non-local decisions |
| agent output lacks expected artifacts | `invalid_evaluation_output` | `retry_generator` or `replan` |

CLI/TUI-owned loops also wrap `run_harness_loop` with a bounded safe auto-resume supervisor. When Generator/Evaluator writes `evaluation.autoRecovery` for `agent_unavailable`, `agent_timeout`, `agent_stalled_no_output`, or `agent_context_overflow`, CodeAutonomy re-enters the same task automatically instead of asking the user to run `resume`. For `agent_context_overflow`, it clears the saturated primary Planner/Generator session and retries from durable task artifacts in a fresh primary session. The default outer auto-resume limit is 3 (`AUTOMIND_SAFE_AUTO_RESUME_MAX`) to avoid infinite loops.

## Implementation plan

### P0: Keep it thin

- Replace hardcoded `if agent == ...` blocks with an `AGENT_ADAPTERS` registry.
- Keep existing command behavior unchanged.
- Add `agent-adapters.md` as the design contract.
- Run syntax gates.

### P1: Make adapter config overridable

Add optional local config, for example:

```json
{
  "agents": {
    "codex": {
      "binary": "codex",
      "extraArgs": ["--sandbox", "workspace-write"]
    },
    "trae": {
      "binary": "traecli"
    }
  }
}
```

Do not require this for the default path.

### P2: Export agent-specific skill packages

Keep the core export generic, then optionally add thin overlays:

```text
exports/automind-generic/
exports/automind-claude/
exports/automind-codex/
exports/automind-trae/
```

Each overlay should mostly differ in entry instructions and invocation examples, not in core workflow docs.

## Practical rule

If an adapter needs more than ~100 lines or starts changing workflow semantics, it is probably not an adapter anymore. Move shared behavior back into CodeAutonomy core.

## Using CodeAutonomy as a skill or command in coding agents

There are three integration modes. Prefer the skill package for agents that support reusable skills. Use slash-command export for agents that expose `/command` style entrypoints. Use repo-local command mode when the agent is working directly inside the CodeAutonomy repository or a project that vendors CodeAutonomy.

```text
Mode A: Skill package
  CodeAutonomy repo -> ./automind.sh export-skill <skill-dir> -> install/import into agent skill system

Mode B: Slash-command package
  CodeAutonomy repo -> ./automind.sh export-command <command-dir> -> install/import /codeautonomy command

Mode C: Command/project tool
  Agent works in a repo that has CodeAutonomy -> run ./automind.sh commands directly
```

### Mode A: exported skill package

Generate a public-safe skill bundle:

```bash
cd /path/to/automind
./automind.sh export-skill /tmp/codeautonomy-skill
```

Export only is the default. The installer installs public-safe skills for Claude/Codex/Trae/Trae-CN by default; to install manually:

```bash

# Install for Claude, Codex, Trae, and Trae-CN user-level skill folders
./automind.sh export-skill --install auto

# Detect a supported agent skill folder and install into the first match
./automind.sh export-skill --install auto

# Same, but keep/export a copy at a specific path too
./automind.sh export-skill /tmp/codeautonomy-skill --install auto

# Install for Claude user-level skill folder
./automind.sh export-skill --install claude

# Same, but keep/export a copy at a specific path too
./automind.sh export-skill /tmp/codeautonomy-skill --install claude

# Install for Codex, verified with Codex CLI 0.125.0
./automind.sh export-skill --install codex

# Install for Trae/Trae-CN, path convention cross-checked against graphify
./automind.sh export-skill --install trae
./automind.sh export-skill --install trae-cn
```

Install target is intentionally simple: `--install` always writes to the user-level skill folder for the chosen agent, matching graphify's default install model.

- `--install`: `all | auto | claude | codex | trae | trae-cn | none`

Current verified user-level install targets:

| Agent | User-level skill path | Status |
|---|---|---|
| Claude | `~/.claude/skills/<name>` | verified on this machine |
| Codex | `~/.codex/skills/<name>` | verified with Codex CLI 0.125.0 |
| Trae | `~/.trae/skills/<name>` | path convention cross-checked against graphify |
| Trae-CN | `~/.trae-cn/skills/<name>` | path convention cross-checked against graphify |

CodeAutonomy does not silently modify agent configuration without `--install`. Trae does not support PreToolUse hooks; use the skill plus project `AGENTS.md` rules for always-on behavior.

The exported package contains stable user-facing material:

```text
SKILL.md
docs/
templates/
summaries/
examples/
manifest.json
```

It intentionally does **not** include local runtime tasks from `.automind/tasks/`. Those task folders are evidence from this developer machine, not something another user needs for their own project.

Install/import the exported folder according to the target agent's skill mechanism.

#### Claude Code

Use the exported package as a project/user skill if Claude Code's skill mechanism is available in the target environment. The important part is that Claude executes the mandatory startup read protocol before acting, not merely treats the docs as references.

Suggested first prompt after installing the skill:

```text
Use the CodeAutonomy skill. For this task, first execute the mandatory startup read
protocol: read SKILL.md, docs/workflow.md, docs/phase2-requirement.md,
docs/phases/demand-definition.md, docs/phases/verification-execution-planning.md,
templates/phase2_planner_prompt.md, docs/phase3-verification.md,
templates/evaluator_prompt.md, docs/references/command-script-catalog.md, docs/references/app-use-verification.md, and
docs/agent-adapters.md. Then create testable Require/TestCases/Plan and run
verification before claiming completion.
```

If the skill package is not formally installed, attach or copy the exported folder into the project and tell Claude Code where it is.

#### Codex

Codex may not have the same formal skill system in every environment. Use one of these options:

1. Put the exported CodeAutonomy skill folder inside the working project, for example `.agent-skills/codeautonomy-skill/`.
2. Add a short project instruction telling Codex to read `.agent-skills/codeautonomy-skill/SKILL.md` before long-running tasks.
3. If CodeAutonomy itself is available as a command, tell Codex to use command mode below.

Suggested prompt:

```text
Use .agent-skills/codeautonomy-skill as the CodeAutonomy skill. Read SKILL.md and follow the
CodeAutonomy harness loop: requirements, preflight, generator/evaluator evidence,
evaluation.json, and summary.
```

#### Trae / Trae-CN

Trae/Trae-CN skill installation follows the path convention used by graphify:

```bash
./automind.sh export-skill --install trae
# -> ~/.trae/skills/codeautonomy-skill

./automind.sh export-skill --install trae-cn
# -> ~/.trae-cn/skills/codeautonomy-skill
```

Trae does not support PreToolUse hooks. Use the exported skill plus project `AGENTS.md` rules as the always-on mechanism. Repo-local command mode is still useful when you want the agent to run CodeAutonomy directly from a checkout.

Suggested prompt for repo-local use:

```text
Use the CodeAutonomy skill in .agent-skills/codeautonomy-skill. Do not rely on interactive
questions unless CodeAutonomy asks for a human decision. Produce structured evidence
and evaluation output.
```

### Mode B: exported slash-command package

Some coding agents expose slash commands such as `/graphify`. CodeAutonomy supports the same style through `export-command`: `/codeautonomy` is a thin entrypoint that makes the host agent follow the CodeAutonomy skill/workflow protocol. When a CodeAutonomy CLI is available, the command uses it for deterministic helpers and gates; it does not start a detached agent loop unless the user explicitly asks for detached mode.

```bash
cd /path/to/automind
./automind.sh export-command
./automind.sh export-command --install all
./automind.sh export-command --install auto
./automind.sh export-command --install claude
./automind.sh export-command --install codex
./automind.sh export-command --install trae
./automind.sh export-command --install trae-cn
```

Current user-level command targets:

| Agent | User-level command path | Notes |
|---|---|---|
| Claude | `~/.claude/commands/codeautonomy.md` | Claude-style markdown slash command. |
| Codex | `~/.codex/commands/codeautonomy.md` | Codex-style markdown command entrypoint; pairs with CodeAutonomy skill at `~/.codex/skills/codeautonomy-skill`. |
| Trae | `~/.trae/commands/codeautonomy.md` | Follows the same user-level convention as graphify-style Trae installs, using `commands/` for slash commands. |
| Trae-CN | `~/.trae-cn/commands/codeautonomy.md` | Domestic Trae/Trae-CN variant. |

The command file is intentionally small. It should not duplicate all CodeAutonomy docs; it routes the agent to the CodeAutonomy CLI, `SKILL.md`, and the workflow docs so the CLI, skill, and slash command keep one source of truth. The generated command resolves the CLI in this order: project-local `./automind.sh`, `$AUTOMIND_HOME/automind.sh`, then `automind.sh`/`automind` on `PATH`.

Default slash-command semantics:

- `/codeautonomy ask <request>` is equivalent to `/codeautonomy <request>`.
- Both mean: use the current host-agent session as Planner/Generator and use CodeAutonomy CLI helpers/gates such as `scaffold`, `workflow-check` (refresh/validate `workflow.json`), `phase-gate` (refresh/read `automind-workflow-state.json`, checklist, and missing/stale generator/evaluator phase reuse at delivery/evaluation handoff), `context-pack`, and `completion-check`.
- They do **not** call `automind ask "<request>" [auto|codex|claude|trae]` by default, because that command starts a CodeAutonomy-owned detached loop and launches a separate agent CLI process/session.
- Use `/codeautonomy detached ask <request>` or `/codeautonomy cli-ask <request>` only when a separate background CLI loop is intended.

Example usage after install:

```text
/codeautonomy ask Add login smoke validation for this app
/codeautonomy Add login smoke validation for this app
/codeautonomy resume login_smoke_05111530
/codeautonomy status login_smoke_05111530
/codeautonomy verify login_smoke_05111530
/codeautonomy detached ask Add login smoke validation for this app
```

### Mode C: command/project tool

When the agent is operating inside the CodeAutonomy repo, or inside a project that has CodeAutonomy installed, use `./automind.sh` directly.

Useful commands:

```bash
./automind.sh help
./automind.sh smoke offline-demo
./automind.sh scaffold "<task>"
./automind.sh ask "<task>"        # auto-selects first available: codex -> claude -> trae
./automind.sh ask "<task>" codex
./automind.sh ask "<task>" claude
./automind.sh ask "<task>" trae
./automind.sh context-pack <task-code> [iteration]
./automind.sh resume <task-code> claude
./automind.sh status <task-code>
./automind.sh summary <task-code>
```

Repo-local command mode is useful for local validation and demos. Skill export and slash-command export are the cleaner distribution units for other coding agents.

## Should projects mention CodeAutonomy in AGENTS.md?

Yes. If CodeAutonomy is installed or vendored into a project, add a short `AGENTS.md` section so any coding agent knows when and how to use it.

Recommended snippet:

````markdown
## CodeAutonomy

Use CodeAutonomy for non-trivial coding tasks that need explicit requirements,
preflight, verification evidence, retry/replan decisions, or reusable summaries.

Before claiming completion:

1. Make the requirement/test target explicit.
2. Run the relevant verification command or platform harness.
3. Record evidence and next action.
4. Do not silently perform destructive or sensitive actions.

If CodeAutonomy is available as a command, use:

```bash
./automind.sh help
./automind.sh smoke offline-demo
./automind.sh ask "<task>" [auto|codex|claude|trae]
./automind.sh status <task-code>
```

If CodeAutonomy is installed as a skill, execute its mandatory startup read protocol
before starting the task: `SKILL.md`, `docs/workflow.md`, phase2/test-planner
docs, phase3/evaluator docs, command catalog, and adapter docs when applicable.
````

Keep the project `AGENTS.md` short. It should point agents to CodeAutonomy, not duplicate all CodeAutonomy docs.


## Shared session, TUI, and observability

Adapters should treat session/TUI/trace/process-eval behavior as a shared
CodeAutonomy protocol, not adapter-specific state. See
[`tui-session-observability.md`](tui-session-observability.md) for the durable
artifacts (`events.jsonl`, `user-answers.json`, `user-messages.json`,
`trace.json`, `process-eval.json`), TUI behavior, natural-language session
messages, and optional agent I/O streaming.

Adapter-specific rule: stdout/stderr streaming, stdin passthrough, and primary
session resume are optional capabilities. Evaluator remains isolated and must not
reuse the Planner/Generator primary session.
