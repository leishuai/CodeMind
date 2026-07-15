# Installation, Runtime Root, Workspace, Skills, and Commands

This reference defines where CodeMind is installed, how agents should resolve the runtime root, where task artifacts belong, and where the coding-agent skill/command packages are installed.

## Full installation

Recommended public install command:

```bash
curl -fsSL https://github.com/leishuai/CodeMind/raw/refs/heads/main/install-curl.sh | bash
```

Default paths:

| Item | Default path | Override |
|---|---|---|
| CodeMind git-free runtime copy | `~/.automind/automind` | `AUTOMIND_HOME=/custom/path` |
| CLI wrappers | `~/.local/bin/codemind` and `~/.local/bin/automind` | `AUTOMIND_BIN_DIR=/custom/bin` |
| Target project workspace | current shell cwd | `AUTOMIND_WORKSPACE_ROOT=/path/to/project` |
| Task artifacts | `<workspace>/.automind/tasks/<task-code>/` | controlled by `AUTOMIND_WORKSPACE_ROOT` or cwd |

The installer clones or updates the CodeMind repository in an installer cache, syncs a git-free runtime copy into `AUTOMIND_HOME`, runs initialization, creates the canonical `codemind` wrapper plus the legacy `automind` alias, and installs CodeMind agent integrations. The runtime install directory intentionally does not contain a `.git` directory or an `origin` remote.

The optional Lark/Feishu Bridge does not add Node.js to the base CodeMind
installation requirements. Its `node_modules` and compiled `dist/` output are
runtime caches: install/update invalidates them, and the first subsequent
`codemind channel start` installs the locked npm dependencies and builds the
Bridge. Later starts reuse that build until the Bridge source, package metadata,
lockfile, or TypeScript configuration changes.

The installer does **not** install Android Studio, Xcode, SDKs, device trust, signing material, browsers, OCR engines, Docker services, or arbitrary target-project dependencies.

## Updating

Preferred update command after CodeMind is installed:

```bash
codemind update
```

`codemind update` reruns the bundled `install-curl.sh` bootstrap. It updates the installer git cache (`git fetch` + reset to the target ref, default `origin/main`), re-syncs the git-free runtime into `AUTOMIND_HOME` with `rsync --delete`, refreshes the CLI wrapper, and reinstalls the CodeMind skill plus `/codemind` command for detected supported coding agents. In an installer-managed git-free runtime, direct `$AUTOMIND_HOME/automind.sh update` binds `AUTOMIND_HOME` to the current runtime path before invoking `install-curl.sh`, so custom install paths update themselves correctly. In a source Git checkout, `./automind.sh update` does not overwrite the checkout; it updates the normal installed runtime unless `AUTOMIND_HOME` is explicitly provided. The `--delete` sync drops stale runtime files from the previous version but preserves local data: it excludes `.automind/tasks/`, `.automind/summary/`, `dist/`, and `.venv-*/`.

If the installed runtime is too old to support `codemind update`, rerun the one-line installer:

```bash
curl -fsSL https://github.com/leishuai/CodeMind/raw/refs/heads/main/install-curl.sh | bash
```

Set `AUTOMIND_BRANCH=<ref>` to install or pin a specific version, and `AUTOMIND_UPDATE=0` to reuse the existing cache without fetching. A local source checkout can sync its current state instead with `AUTOMIND_HOME=~/.automind/automind ./install.sh` (uses the checkout's code rather than fetching the remote).

## Runtime root

The CodeMind runtime root is the directory containing the installed git-free CodeMind runtime copy, usually the directory containing `automind.sh`, `orchestrator/`, `scripts/`, `templates/`, `schemas/`, and `summaries/`.

Default full install:

```text
~/.automind/automind
```

Development checkout example:

```text
/path/to/automind
```

Inside CodeMind code, runtime-relative resources such as preloaded summaries are resolved from the checkout root, not from the target project. For example:

```text
<CodeMind runtime root>/summaries/preloaded/android-readiness.md
<CodeMind runtime root>/requirements/android-tools.txt
```

Do not hard-code a developer-machine absolute path from old logs such as:

```text
/Users/someone/projects/automind/...
```

Use the installed `codemind` wrapper, `$AUTOMIND_HOME/automind.sh`, or the current checkout's `./automind.sh`.

## Runtime vs workspace

Runtime and workspace are separate:

| Concept | Meaning | Example |
|---|---|---|
| Runtime root | Where the git-free CodeMind runtime copy is installed | `~/.automind/automind` |
| Workspace root | The target project being changed/verified | `/path/to/app` |
| Task dir | Per-task artifacts under workspace | `/path/to/app/.automind/tasks/<task-code>` |

Run CodeMind from the target project root:

```bash
cd /path/to/app
codemind scaffold "Fix login crash and verify it"
```

If the shell cannot change cwd, set the workspace explicitly:

```bash
AUTOMIND_WORKSPACE_ROOT=/path/to/app codemind scaffold "Fix login crash and verify it"
```

Never let the installed CodeMind runtime copy become the task workspace unless the task is actually about CodeMind itself.

## CLI discovery for agents

Coding agents should discover the CLI in this order while keeping cwd at the target project root:

1. `codemind help` on `PATH`.
2. `./automind.sh help` only if the current project vendors CodeMind or the task is CodeMind itself.
3. `$HOME/.automind/automind/automind.sh help`.
4. `$AUTOMIND_HOME/automind.sh help` when set.

Use the first candidate that succeeds as `<AUTOMIND_CLI>` for helper/gate commands. An absolute CLI path is only the executable location; task artifacts still belong under the workspace.

## Helper virtualenvs

Mobile/visual helper packages are lazy and local. Public install does not create them by default.

Explicit setup commands:

```bash
codemind setup-automation-tools android
codemind setup-automation-tools ios
codemind setup-automation-tools visual
```

These create project-local helper virtualenvs under the current workspace:

```text
<workspace>/.venv-android-tools
<workspace>/.venv-ios-tools
<workspace>/.venv-visual-tools
```

They install only low-risk Python helper packages from runtime `requirements/*.txt`, for example `adbutils`, `uiautomator2`, `pymobiledevice3`, `Pillow`, `numpy`, and `imagehash`.

When a required helper venv is missing or incomplete, CodeMind may automatically create/repair the project-local venv during preflight/evaluation. Network/DNS failures during helper package install are retried once with explicit logs such as `install-packages-retry1.log`; non-network failures are classified and routed to fallback or `ask_user`.

Helper resolution preference:

1. Ready project-local helper venv.
2. Ready runtime helper venv from the current CodeMind runtime root.
3. Project-local setup/repair.
4. Lower-capability fallback when safe, or `ask_user`.

Agents must not copy old absolute helper Python paths from logs. Use the current task's `logs/iter-N/env.json` field such as `androidToolsPython` when available, or resolve through the current CodeMind runtime/workspace.

## Skill and slash-command install targets

CodeMind exports two coding-agent packages:

- skill package: workflow docs, prompts, schemas, summaries, examples;
- slash command package: `/codemind` current-session command entrypoint.

Install commands:

```bash
codemind export-skill --install auto
codemind export-command --install auto
```

`all` is a compatibility alias that attempts all supported agents but does not create missing agent roots.

Default user-level targets:

| Agent | Skill target | Command target |
|---|---|---|
| Claude Code | `~/.claude/skills/codemind-skill` | `~/.claude/commands/codemind.md` |
| Codex | `~/.codex/skills/codemind-skill` | `~/.codex/commands/codemind.md` |
| Trae | `~/.trae/skills/codemind-skill` | `~/.trae/commands/codemind.md` |
| Trae-CN | `~/.trae-cn/skills/codemind-skill` | `~/.trae-cn/commands/codemind.md` |

The installer also writes `automind-skill` and `automind.md` compatibility aliases.

If a target agent root does not exist, CodeMind skips that target by default instead of creating a new agent root.

## Reuse.md preloaded path rule

`Reuse.md` is a compact navigation index. It should list preloaded packs as:

```md
### android-readiness — Android Readiness Summary
- Path: `summaries/preloaded/android-readiness.md`
- Summary: One-line description from the pack frontmatter `description`.
- Load: read this file on demand only when this capability is needed.
```

`Path` is relative to the CodeMind runtime root, not the target project and not `.automind/summary/`. The model should read the referenced file only when the current task needs that capability.
