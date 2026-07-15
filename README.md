# CodeMind

[中文](README.zh-CN.md) | English

**CodeMind is an evidence-driven execution loop for coding agents. It helps an
agent plan, implement, verify, repair, and report real engineering work instead
of stopping at a plausible answer.**

It works with Codex, Claude Code, Trae, and other coding agents. CodeMind does
not replace them; it gives them a disciplined way to keep working until the
result is supported by build, test, device, UI, or other concrete evidence.

> Give coding agents a harness, not just a prompt.

## Why CodeMind

Coding agents are fast, but real tasks often fail at the edges: unclear
requirements, missing tests, broken environments, UI flows that were never
actually exercised, and “done” claims without proof.

CodeMind adds the engineering loop around the agent:

- **Plan before changing code** — clarify the goal, scope, risks, and what must
  pass.
- **Verify the real result** — run project builds, tests, apps, devices, and UI
  flows when the task requires them.
- **Repair instead of stopping** — use failure evidence to fix the product or
  verification path, then verify again.
- **Pause only for real decisions** — ask for help when user intent, permission,
  signing, a device, or a sensitive action genuinely requires it.
- **Produce a reviewable handoff** — deliver code changes, evidence, a readable
  report, and reusable lessons for future work.

## How a task completes

```text
Your request
  -> clarify and plan
  -> implement
  -> build and verify
  -> diagnose failures
  -> repair and verify again
  -> finish only when the result is proven
  -> report and reuse what worked
```

CodeMind keeps the task recoverable on disk. If an agent process or verification
step is interrupted, the next run can continue from the recorded task state
instead of relying on chat memory.

For the complete workflow and evidence contract, see
[docs/workflow.md](docs/workflow.md).

## Quick start

Install CodeMind:

```bash
curl -fsSL https://github.com/leishuai/CodeMind/raw/refs/heads/main/install-curl.sh | bash
```

Run the no-device smoke test:

```bash
codemind smoke offline-demo
```

Update later with:

```bash
codemind update
```

Install paths and environment requirements are documented in
[installation-runtime.md](docs/references/installation-runtime.md).

## Use CodeMind

### In Codex / Claude Code / Trae

After installation, restart or reload your coding agent, then run:

```text
/codemind Fix the login crash and verify it
```

CodeMind uses the current coding-agent session to plan and implement, then keeps
the verification and repair loop moving until the result passes, needs a real
decision, or reaches a proven blocker.

### In a terminal

Run from the project you want CodeMind to work on:

```bash
cd /path/to/your-project
codemind ask "Fix the login crash and verify it"
```

Useful commands:

```bash
codemind                         # open the interactive shell
codemind ask "..."               # start a task
codemind status <task-code>      # inspect progress and the next action
codemind resume <task-code>      # continue a saved task
codemind report <task-code>      # generate the human-readable report
codemind update                  # update CodeMind
```

Run `codemind help` for the full command list. Existing `automind` and
`/automind` entrypoints remain supported as compatibility aliases.

Existing installations and task history remain compatible. CodeMind continues
to use the `.automind/` data directory and `AUTOMIND_*` environment variables,
so no task migration is required.

### In Lark / Feishu

CodeMind can connect to a Lark/Feishu bot so you can chat naturally, confirm a
development task, follow progress, answer pending questions, and receive the
final result in Lark.

```bash
codemind channel start [botId]
codemind channel dashboard
```

- Omit `botId` to connect all registered bots.
- Pass `botId` to configure or start one specific bot.

See [Lark Bridge](lark-bridge/README.md) for setup and usage.

## Full-auto mode

By default, a non-trivial implementation task may pause once before coding so
you can confirm the direction, scope, risks, and expected verification.

To let CodeMind continue end to end without that planning confirmation, include
`full auto` or `no confirmation` in the request:

```text
/codemind Fix the login crash and verify it, full auto
```

```bash
codemind ask "Fix the login crash and verify it, full auto"
```

Full-auto mode still does not silently approve account access, payment,
destructive operations, production-impacting actions, or genuine device,
signing, and permission gates.

## What you get

Every completed task produces a durable workspace under:

```text
.automind/tasks/<task-code>/
```

The main results are:

- the implementation or repair;
- the requirements and verification plan used for the task;
- build, test, device, UI, and log evidence when applicable;
- a record of failures, recovery, and remaining blockers;
- `Report.html`, the first place to review the result;
- a reusable summary of successful and failed approaches.

Open a task with:

```bash
codemind status <task-code>
codemind report <task-code>
```

## Safety boundaries

CodeMind is designed for high automation, but it does not treat every failure or
instruction as permission to mutate the machine.

- It does not silently install system SDKs, signing material, device trust,
  privileged services, or private credentials.
- Sensitive or irreversible actions require explicit authorization.
- Environment, device, signing, and permission failures are reported as
  blockers instead of being misrepresented as product-code failures.
- Runtime and UI claims require runtime evidence when a runnable path exists.
- Completion is checked against evidence, not only the agent's final message.

## Troubleshooting

### `codemind: command not found`

Add the wrapper directory to your shell profile, usually:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Restart the shell and run `codemind help`.

### `/codemind` is not visible

Restart or reload the coding agent after installation.

### A task is stuck or keeps failing

Start with:

```bash
codemind status <task-code>
codemind resume <task-code>
```

The status output explains the current blocker and recommended next action.
Detailed diagnostic and verification commands are listed in the
[command catalog](docs/references/command-script-catalog.md).

### Mobile or UI tooling is missing

Install the required platform tooling explicitly, then resume the task. CodeMind
can prepare its own low-risk helper packages, but it does not install Xcode,
Android Studio, signing assets, or device trust settings for you.

## Learn more

- [Product design](automind_design.md) — why CodeMind is loop-first and
  evidence-driven.
- [Complete workflow](docs/workflow.md) — phases, recovery, and evidence rules.
- [Installation and runtime](docs/references/installation-runtime.md) — install
  paths, project workspaces, and prerequisites.
- [Lark / Feishu usage](lark-bridge/README.md) — connect and use a bot.
- [Documentation map](docs/README.md) — all advanced and platform-specific
  references.

## What CodeMind is not

CodeMind is not another coding agent, a replacement for project-native tests or
platform SDKs, or a guarantee that every task can be solved automatically.

It is the engineering loop around coding agents: plan, implement, verify,
recover, report, and reuse.
