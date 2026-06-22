# AutoMind TUI, Session, Trace, and Process Evals

This reference keeps the interactive/session/observability details out of the
canonical workflow guide.

## Shared session core

AutoMind has one shared workflow core and two user-facing shells:

- **CLI/TUI mode**: AutoMind owns the command-line session, calls Codex/Claude/Trae
  CLIs as subprocesses, streams output into a timeline, and receives user answers
  inside the terminal.
- **Skill mode**: a host coding agent follows AutoMind instructions, but uses the
  same artifacts, gates, answers, messages, trace, and next-instruction commands.

Shared task/session artifacts:

- `events.jsonl`: durable timeline for TUI and skill mode. Replaceable status rows use `replaceKey`.
- `user-answers.json`: durable answers for `ask_user` actions.
- `user-messages.json`: natural-language user messages from TUI/shell.
- `trace.json`: formal task/phase/event trace.
- `process-eval.json`: process correctness report.
- `runtime-state.json.latestUserAnswer` / `latestUserMessage`: quick resume summaries.

Shared commands:

```bash
automind ask "..." [agent] [--tui|--detached]
automind resume <task-code> [agent] [--tui|--detached]
automind continue [task-code]
automind answer <task-code> --text "..."
automind message <task-code> --text "..." [--resume agent]
automind trace <task-code> [--json|--write]
automind process-check <task-code> [--json|--soft|--no-write]
automind tui <task-code> [--watch|--interactive]
```

## TUI-owned ask/resume

`automind ask ...` and `automind resume ...` run through the TUI session wrapper
by default when stdin/stdout are interactive terminals. Non-interactive scripts,
CI, and piped invocations keep detached/plain behavior. Use `--tui` or
`--detached` to force either mode.

The wrapper does not replace the workflow engine. It displays status/timeline,
prompts for pending `ask_user` answers, persists those answers, and invokes the
existing loop again so the next agent invocation receives durable context.

While the owned loop is blocked inside a long Planner/Generator/Evaluator call,
the wrapper publishes a heartbeat status update once per minute and redraws the
snapshot. Heartbeat status events use the stable `replaceKey` value
`heartbeat:status`, so the TUI updates one visible row instead of appending a new
timeline line for every check.

Agent CLI stdout/stderr is streamed into `events.jsonl` as `agent_output` events
while preserving the captured-output contract used for planner/session parsing.
If an agent process stays quiet, AutoMind emits a replaceable
`agent_still_running` event once per minute with elapsed/quiet duration. The TUI
may show visible agent progress, tool summaries, runtime banners, and errors,
but it must not depend on or expose hidden model chain-of-thought.

## Natural-language TUI/session messages

Inside bare `automind` or `automind tui <task> --interactive`, command-shaped
input runs AutoMind commands. Non-command natural language uses session affinity:

- when `.automind/current-task` exists, it is recorded as a pending task-local user message and can resume the current task;
- when no current task exists, bare `automind` creates a per-TUI-process hidden chat task (`AUTOMIND_TUI_CHAT_TASK=__tui_chat_<pid>_<id>`) so exploratory questions such as `where am i?` reuse one coding-agent session within that terminal window;
- if that same TUI process later runs `ask ...`, the new task seeds its primary Planner/Generator session from the hidden chat task when possible. A different terminal window gets a different hidden chat task/session.

Example current-task message:

```text
automind> 这个任务优先复用项目里的验证脚本，不要自己乱造命令
```

Equivalent command when a current task exists:

```bash
automind message <current-task> --text "这个任务优先复用项目里的验证脚本，不要自己乱造命令" --resume auto
```

Equivalent command before a task exists inside one bare-`automind` shell process:

```bash
AUTOMIND_TUI_CHAT_TASK=__tui_chat_<pid>_<id> automind message "$AUTOMIND_TUI_CHAT_TASK" --text "where am i?" --resume auto
```

Pending messages are injected into the next Planner/Generator prompt and then
marked `delivered`. They are user intent/clarification, not permission to bypass
Requirements/TestCases/Plan/workflow gates. Scope or risk changes must route
through `ask_user` or `replan`.

## Current-task defaults

Inside the interactive shell, task-oriented commands can omit the task code when
`.automind/current-task` exists:

```text
automind> status
automind> trace --json
automind> process-check --soft
automind> tui --interactive
automind> resume
```

Explicit task codes still win.

## Formal traces

AutoMind projects task-local events/state into an OpenTelemetry-friendly trace
shape without requiring a backend:

- `traceId` / `runId`: identify the task run.
- `spanId` / `parentSpanId`: describe task -> phase -> action hierarchy.
- `phase`, `actor`, `action`, `status`, `durationMs`, `artifactRefs`, and
  `evidenceRefs`: make each step queryable.

`events.jsonl` remains the append-only timeline; `trace.json` is the formal
projection used by CLI trace, TUI summaries, process evals, and summaries.

## Process evals

`workflow-check` validates artifact continuity. `process-check` evaluates whether
the harness process followed required gates:

- workflow contract has no blocking issues;
- `pre_implementation_review` exists and has a valid decision;
- workflow current/expectedNext state is present;
- latest user answer/message delivery state is sane;
- formal trace spans exist and trace errors are visible;
- Brainstorm repository context records docs/scripts/agent-instruction discovery;
- finished tasks have passing completion report and Summary artifact.

## Improve across runs

Cross-run learning stays in Summary/Reuse, not a separate memory island. After
completion, `automind summary <task-code>` writes:

- task-local `Summary.md`;
- task-local `trace.json`;
- task-local `run-card.json`;
- global `.automind/summary/run-cards.jsonl`;
- existing `.automind/summary/local-reuse-index.md` and `lessons-learned.md`.

`automind improve-suggestions` reads run cards and proposes prompt, workflow,
skill, or project-local reuse improvements. Suggestions are advisory and require
review before applying.

## Version source

The runtime display version is centralized in `orchestrator/version.py`:

```python
AUTOMIND_VERSION = "0.1.0"
```

TUI and shell render it through `automind_version_label()`. Use
`automind version` to print the current runtime version. Installer examples may
still mention git tags such as `v0.1.0`; those are repository refs, not separate
TUI version constants.

## User interrupt / pause

In a TUI-owned loop, `Ctrl+C` is treated as a recoverable user pause instead of a
hard task abort. AutoMind records:

```json
{
  "status": "paused_by_user",
  "interruptedByUser": true,
  "nextAction": "resume_after_user_interrupt"
}
```

and writes a `tui_user_interrupt` event. The task can be resumed later with:

```bash
automind resume <task-code> [agent]
```

This is different from `aborted`: `paused_by_user` means the user interrupted the
running loop but the task artifacts remain valid and resume is allowed.

### Human-facing chat output

For the hidden per-process TUI chat task, AutoMind formats the coding-agent reply
as a compact chat turn instead of printing raw CLI output tuples. Runtime banners,
hook traces, token counters, and Codex session metadata are stripped when possible:

```text
automind> where am i
[AutoMind] User message recorded: user-message-003
[AutoMind] coding-agent chat (codex)...

codex> You are in:
`/path/to/project`
```

After the first turn, `--resume auto` uses the cached primary agent from the
hidden chat task when available. This avoids repeated auto-agent discovery and
keeps follow-up chat turns faster and less noisy.
