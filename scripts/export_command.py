#!/usr/bin/env python3
"""Export/install CodeMind slash-command entrypoints for coding agents.

This complements script / CLI / skill distribution with a command-style entry:
users can type `/codemind ...` in tools that support slash commands. The legacy
`/automind` name is installed as a compatibility alias. The
command is intentionally a current-session natural-language protocol entrypoint
by default, not a shell alias for `automind ask ... <agent>`.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
from datetime import datetime

from agent_targets import command_targets as resolve_command_targets, display_path

ROOT = pathlib.Path(__file__).resolve().parents[1]


def command_body(command_name: str = "codemind") -> str:
    slash = f"/{command_name}"
    return f"""---
description: "CodeMind evidence-driven harness loop command. Defaults to high automation: keep looping implement -> verify -> repair without pausing, unless a real sensitive/destructive decision or a hard gate needs the user. Use for testable requirements, current-session Generator work, isolated Evaluator evidence, and structured loop decisions."
argument-hint: [ask|resume|status|summary|verify|detached|cli-ask|update|help] [task or request]
---

# {slash} - CodeMind Command

You are executing the CodeMind command entrypoint.

CodeMind turns coding work into an evidence-driven harness loop:

```text
Intent / Require / TestCases
  -> Preflight
  -> Generator
  -> Evaluator
  -> Evidence
  -> Retry / Replan / Ask Human / Finish
  -> Summary / Reuse
```

Minimal current-session protocol:

```text
Prepare -> Plan -> Build -> Verify -> Finish
```

- Prepare: discover `<AUTOMIND_CLI>`, verify workspace root, scaffold task.
- Plan: digest user intent against the project workspace, refine Brainstorm/Requirements/TestCases/Plan, and resolve pre-implementation review.
- Build: implement or repair as Generator and write `Delivery.md`.
- Verify: run deterministic verifier or context-isolated Evaluator; write `Validation.md` and `evaluation.json`.
- Finish: run `completion-check`; only then summarize, generate/inspect `Report.html`, and claim completion.

Hard gates: `workflow-check` before Build, `Delivery.md` before final Verify,
and `completion-check` before Finish.

**High automation is the default.** Once the pre-implementation review gate is
resolved (or the user requested full-auto/no-confirmation mode), keep the loop
running Generator -> Evaluator -> `completion-check` autonomously. Do not stop
just because a step is long, expensive, or routine. Pause only for an allowed
`ask_user` category: unauthorized destructive/sensitive action, system/external
dependency, real device/signing, manual visual confirmation, or repeated same
failure.

## Command input

User arguments, if supplied by the host tool:

```text
$ARGUMENTS
```

If the host does not support `$ARGUMENTS`, infer the request from the user's message after `{slash}`.

## Update intent

If the user invokes `{slash} update` or asks to update, upgrade, refresh,
reinstall, or sync CodeMind itself, run:

```bash
<AUTOMIND_CLI> update
```

This is a maintenance command for CodeMind itself: it updates the CodeMind
runtime, CLI wrapper, skill package, and `{slash}` slash-command integrations.
Do not scaffold a task, do not create `.automind/tasks/<task>/`, and do not
enter the harness loop for update-only intent. If `<AUTOMIND_CLI> update` is
unavailable because the installed runtime is too old, suggest the documented
one-line installer from `docs/references/installation-runtime.md`.

## Non-negotiable execution rules

- Default slash-command mode uses the **current/main coding-agent session** as Planner and Generator. Do not start another agent process unless the user explicitly asks for detached/background CLI mode.
- Follow the minimal protocol: Prepare -> Plan -> Build -> Verify -> Finish. Keep it simple; do not skip the hard gates.
- Generator may use the current/main coding-agent session and full task context.
- Evaluator must be context-isolated from Generator.
- Evaluator context must be complete, audited, and non-redundant: include requirements, test cases, acceptance criteria, delivery artifacts, environment/device constraints, and prior validation state; exclude Generator reasoning/code-authoring process/raw transcripts.
- Evaluator must have full independent verification capability: run Android/iOS preflight, probe-flow, XCUITest, script-command, tests, logs, screenshots, UI hierarchy, `ui-evidence-check`, or other deterministic checks when available.
- For App/UI work, CodeMind verification is action-capable when platform runners are available: Android probe-flow can tap/click/input/swipe/assert; iOS XCUITest/probe-flow/action-plan can run or materialize tap/input/scroll/assert flows; iOS `pymobiledevice3 AccessibilityAudit` may be used as a low-risk exploration fallback to inspect the live accessibility tree and try reversible presses when XCUITest selectors/runner setup are not ready; Web probe-flow can record Client UI action evidence and delegate to project-native E2E commands. Safe close/skip/later/dismiss overlays may be auto-unblocked with evidence. Privacy/terms Agree/Allow/Continue, reject/deny, login/account grants, payment, delete/reset/uninstall, external upload, signing/device trust, or ambiguous/irreversible consent require explicit authorization or `ask_user`. Do not claim CodeMind cannot operate the app; encode reviewable actions and evidence, or route to ask_user/replan for missing runners/devices/selectors/authorization.
- Generator owns product/runtime-code implementation and repair. Evaluator owns
  verification, failure classification, evidence, `Validation.md`, and
  `evaluation.json`; it routes product failures to `retry_generator` rather
  than repairing product code itself. Evaluator may only self-repair
  verifier/probe-flow/test-harness issues when the validation method is wrong.
  For failed/partial/blocked runtime paths, write generic `runtimePath`,
  `failureClass`, `observedSignals`, optional `shouldRetry`, and `retryAdvice`
  into `evaluation.json.testResults[]` or `failedChecks[]`; use `unknown` when
  evidence is ambiguous rather than inventing project-specific classes.
- If verification is blocked by unrelated build/test/workspace issues, CodeMind may create minimal reversible verification unblock changes only after checkpointing or recording a diff. Record them in `Delivery.md`/`Validation.md` and `evaluation.json.verificationUnblockChanges`, then restore or explicitly promote them before finish. Active temporary unblock changes block completion.
- Required or strongly recommended verification actions default to automatic execution: build/compile/install/test/runtime smoke/project-native verifiers should run when needed for required TC/AC/evidence closure. Do not ask the user merely because the step is long-running or expensive; ask only for real sensitive/destructive/external decisions such as delete/uninstall/reset, account/login, external upload, payment, sudo/system configuration, keychain/signing material/device trust changes, production impact, or runtime/static downgrade.
- Do not claim completion without `evaluation.json`, executed evidence, and required `TC-*`/`AC-xxx` coverage. Run `completion-check` when the CodeMind CLI is available; command mode runs it automatically before accepting `finish`. Environment/device/tool blocker classification is routing information, not a passed required testcase. Required App/UI/runtime cases need hard product-launch/action/assertion evidence plus positive `evidenceAssessment`; required clean-build/release/merge cases need attached build evidence plus positive `evidenceAssessment`, not blocker classification.
- Crash/timeout quality failures require stack/page context before they are hard product failures. Record crash stack/backtrace, process/bundle, occurred page/screen/scene, reproduction path, and stability. Stable product-attributable crashes/timeouts should route to Generator self-repair; verifier timeouts, raw network/syslog timeouts, historical crash text, and control-plane/log-digest text should not fail completion by keyword alone.
- For App/UI/runtime TC pass claims, capture or link screenshot/visual evidence for the executed TC or distinct page/state by default. Screenshots are not sufficient proof alone; pair them with logs/state/assertions/hardMetrics. If no screenshot is possible, record `noScreenshotReason` and attach `.xcresult`, UI hierarchy/accessibility, trace, or runner summary instead.
- Do not edit product/runtime code while `workflow-check` has hard issues.
- Do not enter final verification without `Delivery.md`.
- Do not treat environment/device/signing failures as product code failures.
- Do not run destructive or sensitive actions without explicit human confirmation.
- Low-risk Android/iOS/visual Python helper setup may auto-run when required by the selected verifier via `codemind setup-automation-tools android`, `codemind setup-automation-tools ios`, or `codemind setup-automation-tools visual`; these commands use version-bounded runtime `requirements/*.txt`, only create project-local Python virtualenvs, and may retry transient network/DNS package-index failures once with explicit logs. Do not silently install system SDKs, signing material, device trust settings, or privileged services.
- For web/client/server target dependencies, prefer project docs, CI, lockfiles, and `Reuse.md`; use `codemind dependency-check [task-code] [iteration]` as optional read-only discovery only when needed, then project-native lockfile/documented commands such as `npm ci`, `pnpm install --frozen-lockfile`, `yarn install --immutable`, `uv sync --frozen`, `poetry install --sync`, Gradle/Maven wrappers, or repo-specific setup. Do not silently install system runtimes, Docker/database services, browser drivers, private registry credentials, signing, or device trust.

## Session boundary

`<AUTOMIND_CLI> ask "<request>" <codex|claude|trae|trae-cn>` starts a separate CodeMind-owned end-to-end loop through the adapter. For Codex/Claude/Trae/Trae-CN detached mode, Planner/Generator may reuse one task-local primary CLI session; Evaluator remains a fresh isolated invocation. It does **not** reuse the current slash-command conversation/session in Codex, Claude, Trae, or Trae-CN.

Therefore, inside this slash command:

- `{slash} <request>` is the current-session form and means: use CodeMind in the current session to drive the task end-to-end until completion, `ask_user`, blocked environment/permission, max-iteration guard, or an explicitly requested single-stage stop. The installed `/automind` command remains a compatibility alias.
- Treat the command as structured natural language: "use the CodeMind skill/protocol in this conversation, call CodeMind CLI helpers/gates as needed, and keep looping through Evaluator verify -> Generator repair -> Evaluator re-verify until the required evidence passes, `ask_user` needs a human decision, or an explicit unsafe/non-recoverable stop condition is reached." The host agent is the loop driver: after each helper/check/evaluator result, run `<AUTOMIND_CLI> phase-gate <task-code> auto` when available. That gate refreshes/reads `automind-workflow-state.json` first; when handing off into `delivery` or `evaluation`, it also deterministically refreshes missing/stale `phase-reuse/generator.md` or `phase-reuse/evaluator.md` without resetting fresh acknowledged reuse. It then uses local signals such as `evaluation.json.nextAction`, `completion-report.json`, runtime-state fields, and migration `stateSummary` only as resolver inputs before taking the next safe step.
- `{slash} --detached <request>` (or the equivalent `<AUTOMIND_CLI> ask "<request>" <agent>`) is the detached-CLI variant; use it only when the user explicitly asks for background/detached execution.

## Result exchange contract

Current-session work, native isolated subagents, deterministic verifiers, and detached CLI processes communicate through CodeMind task artifacts, not hidden chat memory:

```text
.automind/tasks/<task>/
  automind-workflow-state.json  # workflow control state: current/next phase, action, owner, iteration
  automind-workflow-events.jsonl # workflow control-state transition log
  runtime-state.json             # runtime/resume projection: status, owner, iteration, session/resume state
  evaluation.json                # structured evaluator result and nextAction
  Validation.md            # human-readable verification record
  Delivery.md              # Generator delivery notes
  VerificationLedger.json  # completion-check coverage ledger
  Report.html              # human handoff report with per-TC evidence/screenshots/logs
  logs/iter-N/*            # commands, env, evaluator logs, screenshots, context packs
```

Use `automind-workflow-state.json` as the workflow control source of truth, with `evaluation.json`, `runtime-state.json`, migration `stateSummary`, and evidence files as local signals when resuming or integrating detached/subagent results. In skill/command mode, prefer `<AUTOMIND_CLI> phase-gate <task-code> auto` as the script gate for every phase handoff.

Workspace rule: runtime and workspace are separate. The CodeMind runtime may be installed under `~/.automind/automind` by default, `$AUTOMIND_HOME`, or another checkout, but task artifacts must belong to the target project. Run `<AUTOMIND_CLI>` from the target project/workspace root. If the shell is not in that root, set `AUTOMIND_WORKSPACE_ROOT=/path/to/project` on every CodeMind command. Do not scaffold tasks from inside the CodeMind installation checkout unless that checkout itself is the project being worked on. Do not copy developer-machine absolute paths from old logs; resolve resources through the current runtime/workspace or task `logs/iter-N/env.json`.

## Find the CodeMind CLI for helper/gate commands

Find the CodeMind CLI in this order, while keeping the shell cwd at the target project root:

1. `codemind` on `PATH`, if the user installed the wrapper.
2. Legacy `automind` on `PATH`.
3. Current project checkout/vendor path: `./automind.sh` only when the target project contains CodeMind.
4. Default install path: `$HOME/.automind/automind/automind.sh`.
5. Environment override: `$AUTOMIND_HOME/automind.sh`.

Use the first candidate whose `help` command succeeds. Prefer CLI helpers/gates because they keep task artifacts, coverage checks, and reusable summaries consistent; keep Planner/Generator in the current session unless detached mode is explicitly requested. The selected CLI path is executable/runtime location only; it is not the task workspace.

Example candidate commands:

```bash
codemind help
automind help
./automind.sh help
$HOME/.automind/automind/automind.sh help
$AUTOMIND_HOME/automind.sh help
```

If the selected CLI is an absolute/runtime path such as
`$HOME/.automind/automind/automind.sh`, still execute it from the target project
root or prefix `AUTOMIND_WORKSPACE_ROOT=/path/to/project`; the CLI path is not
the task workspace.

Use the selected CLI path consistently for deterministic helpers and gates, for example:

```bash
<AUTOMIND_CLI> scaffold "<user request>"
<AUTOMIND_CLI> status <task-code>
<AUTOMIND_CLI> phase-gate <task-code> auto
<AUTOMIND_CLI> workflow-check <task-code>
<AUTOMIND_CLI> completion-check <task-code>
<AUTOMIND_CLI> summary <task-code>
```

Do not use `<AUTOMIND_CLI> ask ... <agent>` or `<AUTOMIND_CLI> resume ... <agent>` for the default slash-command path because those commands own the loop and may launch a new external agent process.

## Default current-session end-to-end flow

For `{slash} <request>` and `{slash} ask <request>`:

Use this as the required state check before each major action; in skill/command mode copy `checklist[]` / `checkboxMarkdown[]` from `phase-gate` into the native TODO/checkbox plan, complete required items with the host agent's normal read/edit/test/write abilities; run a CLI command only when that item explicitly provides one. Treat `phaseReuseRefresh` as evidence that the key generator/evaluator reuse file was refreshed or already fresh. Then rerun `phase-gate` when the CLI is available:

```text
CodeMind State Check
- stage: Prepare | Plan | Build | Verify | Finish
- last gate: pass | fail | not_run
- missing artifact: ...
- next required action: ...
```

1. Resolve `<AUTOMIND_CLI>`.
2. Before scaffolding, confirm the shell's current working directory is the target project root. If not, either `cd` there or set `AUTOMIND_WORKSPACE_ROOT=/path/to/project`.
3. If available, run from the target project/workspace root:

   ```bash
   <AUTOMIND_CLI> scaffold "<user request>"
   ```

   If the current shell cannot stay in the project root, run `AUTOMIND_WORKSPACE_ROOT=/path/to/project <AUTOMIND_CLI> scaffold "<user request>"`.

   Capture `TASK_CODE` / `TASK_DIR` from stdout and verify `TASK_DIR` is under the target project. If it points under the CodeMind runtime checkout, stop and rerun with the correct cwd or `AUTOMIND_WORKSPACE_ROOT`.
4. Execute the CodeMind mandatory startup read protocol. This is not optional reference reading. Before planning, coding, or validating, read and apply:
   - `SKILL.md`
   - `docs/workflow.md`
   - `docs/phase2-requirement.md`
   - `docs/phases/demand-definition.md`
   - `docs/phases/verification-execution-planning.md`
   - `templates/phase2_planner_prompt.md`
   - `docs/references/test-design-guide.md` when designing concrete TestCases/runbooks
   - `docs/phase3-verification.md`
   - `templates/evaluator_prompt.md`
   - `docs/references/command-script-catalog.md`
   - `docs/references/skill-command-driver-checklist.md`
   - `docs/references/verification-flow.md` when platform/device/runtime verification may be needed
   - `docs/agent-adapters.md` when invoking Codex/Claude/Trae/Trae-CN or another runtime
5. In the current agent session, refine `Brainstorm.md`, `Requirements.md`, `TestCases.md`, and `Plan.md` using model judgment. The scaffold is only a starting point. Brainstorm must first digest the user's real goal against project/workspace context, domain/business flows, risks/opportunities, and better product suggestions before requirements are frozen.
   `Plan.md` must include Implementation and Verification checklists. Generator updates `T*` implementation rows; Evaluator updates `TC-*` verification rows from evidence.
   Use JSON sidecars as the structured handoff protocol, not just Markdown/chat memory: read `workflow.json` and existing phase sidecars before changing downstream artifacts; after edits, run `workflow-check` so `brainstorm.json`, `requirements.json`, `testcases.json`, `plan.json`, `pre-implementation-review.json`, and the derived `workflow.json` are refreshed/schema-checked.
6. Before implementation, make the pre-implementation review decision explicit:
   - `auto_proceed`: continue without interrupting the user because scope, assumptions, and verification are clear enough.
   - `ask_user`: stop and ask the user to confirm/correct the approach before code changes.
   - `replan`: refine Phase 2 artifacts again.
   Record this in `Brainstorm.md` and `runtime-state.json.planner.preImplementationReview`.
   This is a mandatory gate, not advice. Unless the user explicitly requested full-auto/no-confirmation mode (for example “一站到底”, “全自动模式”, “不用问用户”, “不用确认”, or “full auto/no confirmation”), non-trivial implementation/behavior-change tasks must ask the user once in pre-implementation. The confirmation should first prompt the user to review the key planning artifacts — above all `Requirements.md` and `TestCases.md`, plus `Brainstorm.md`/`Plan.md` — because a wrong requirement or test design sends the whole route off course and wastes all later development and verification. It should then be one concise decision bundle covering: whether the requirement is clear, goal/scope/non-goals, approach, key assumptions, known risks, verification direction, known must-pass `AC-*`, required `TC-*`, evidence expectations, rollback/replan boundaries, and authorization for non-low-risk operations such as overwrite install, uninstall/delete/reset, account login, signing/device trust changes, privilege escalation, external upload, payment, or production-impacting actions. The bundle should also remind the user that the host coding agent's command-execution permissions may interrupt the run — for example commands outside the agent sandbox or high-risk commands may prompt the user for approval — so the user can stay available to approve them promptly. Only client/app development or verification tasks need the real-device vs simulator/emulator decision as one part of the bundle. Auto-proceed is for verification-only, documentation-only, mechanical/low-risk edits, already-approved directions, explicit full-auto/no-confirmation automation, or safe no-loss runtime recovery (`agent_unavailable`/`agent_timeout` -> `resume_after_recovery`). After the gate is resolved, continue automatically through Generator implement/repair -> Evaluator verify/re-verify -> `completion-check` until finish is proven or a real stop condition occurs.
7. Run:

   ```bash
   <AUTOMIND_CLI> workflow-check <task-code>
   ```

   Fix artifact continuity before implementation. This is the Plan gate.
8. Implement as Generator in the current session only after the review decision is `auto_proceed` or user confirmation has resolved `ask_user`. Write/update `Delivery.md`.
9. Before final verification, confirm `Delivery.md` exists and maps changed files to concrete `TC-*` targets.
10. Evaluate with an isolated Evaluator or deterministic verifier:
   - Prefer deterministic verification first: project-native tests, `script-command`, Android/iOS probe-flow, XCUITest, screenshots/logs, or another deterministic adapter.
   - If model judgment is required and the host provides a reliable native isolated subagent/session, use it with the context pack only.
   - Otherwise use a fresh external agent CLI process only for Evaluator or explicit detached mode.
   - Before any model Evaluator, run `<AUTOMIND_CLI> context-pack <task-code> [iteration]` and pass only the generated `logs/iter-N/evaluator-context.md/json`; do not reuse Generator context.
   - If `runtime-state.json` already has `evaluatorContext`, `workflow-check` validates its isolation fields and context-pack files.
11. Convert each required `TC-*` / `testcases.json.testcases[]` entry into a concrete verifier operation: project-native command, `script-command`, Android probe-flow, iOS XCUITest/probe-flow/action-plan, browser/project E2E, external-sink proof, or static/quality review only when appropriate. Write `Validation.md`, `evaluation.json`, evidence logs, testcase/AC coverage, and for any non-pass runtime path add `runtimePath` + generic `failureClass` + observed/missing signals + retry advice. When present, consume `delivery.json`, `testcases.json`, and `workflow.json` for structured coverage mapping.
12. Refresh/read `automind-workflow-state.json` for workflow routing. Local evaluator actions still map as resolver inputs: `retry_generator` -> Build, `replan` -> Plan, `ask_user` -> user, `finish` -> completion-check, `stop` -> stop.
13. Run:

   ```bash
   <AUTOMIND_CLI> completion-check <task-code>
   ```

   If it fails, loop back through diagnosis, repair, and verification.
14. Run `summary <task-code>` / `record-check <task-code>` / `report <task-code>` when the task is terminal or ready for reuse. Consume `completion-report.json`, `VerificationLedger.json`, `run-card.json`, and `Report.html` when present before claiming final handoff. `Report.html` should use `<task> CodeMind Report`, show Test Results with per-TC evidence/screenshots/logs, and summarize Summary / Knowledge Deposition.

Default `{slash}` should not stop after scaffolding or one verification round. Continue the harness loop until `completion-check` passes, the task needs human confirmation (`ask_user`), a human/system environment/tool/permission decision is required, the max-iteration guard asks for direction, or the user explicitly requested only one phase such as `verify`.

If build/test/device/tool verification is blocked, keep trying through Generator repair, safe reversible verification-unblock, safe overlay auto-unblock, or replan when plausible. Use `ask_user` for human/system decisions such as device trust/unlock/Developer Mode, signing material, credentials, privileged services, reject/deny, login/account grants, payment, delete/reset/uninstall, external upload, signing/device trust, ambiguous/irreversible consent, or real-device vs simulator/emulator target. Do not convert `environment_blocked`, `mobile_device_unavailable`, `permission_blocked`, or `tool_missing` into a pass for required `TC-*`.

If command helpers are not available, use the installed CodeMind skill/docs as the protocol:

1. Read `SKILL.md` if present, otherwise locate the CodeMind skill package.
2. Execute the same mandatory startup read protocol: `docs/workflow.md`, `docs/phase2-requirement.md`, `docs/phases/demand-definition.md`, `docs/phases/verification-execution-planning.md`, `templates/phase2_planner_prompt.md`, `docs/phase3-verification.md`, `templates/evaluator_prompt.md`, `docs/references/command-script-catalog.md`, and platform/adapter docs as applicable. Use `docs/references/test-design-guide.md` when concrete TestCases/runbooks need examples.
3. Create/update `.automind/tasks/<task>/` artifacts manually following the workflow.
4. For Generator, write/update `Delivery.md`.
5. Before Evaluator, create/consume `logs/iter-N/evaluator-context.md/json` with complete audited non-redundant context.
6. Launch Evaluator via a fresh runtime/native isolated subagent/deterministic platform evaluator. Do not merely switch roles in the same conversation.
7. Write `Validation.md`, `evaluation.json`, evidence logs, and next action.

## Detached CLI mode

Only when the user explicitly requests detached/background CLI ownership:

```bash
<AUTOMIND_CLI> ask "<user request>" <codex|claude|trae>
<AUTOMIND_CLI> resume <task-code> <codex|claude|trae>
```

This mode is useful for background loops and demos. Codex/Claude/Trae Planner/Generator may reuse a task-local primary CLI session, but detached mode is still not the same as continuing the current slash-command session; Evaluator remains fresh-isolated.

## Subcommands

Interpret the first argument as a soft subcommand:

- `ask <request>`: current-session CodeMind flow. Equivalent to omitting `ask`.
- `<request>` with no subcommand: same as `ask <request>`.
- `resume <task-code>`: inspect an existing CodeMind task and continue in the current session when possible; do not call detached CLI resume unless requested.
- `status <task-code>`: inspect task status and follow its "Next recommended action" / suggested commands to keep the current-session loop moving.
- `summary <task-code>`: generate or inspect task summary.
- `verify <task-code>`: run/describe evaluator-only verification for the task.
- `detached ask <request>` / `cli-ask <request>`: start CodeMind-owned detached CLI loop through the selected agent adapter.
- `detached resume <task-code>` / `cli-resume <task-code>`: resume CodeMind-owned detached CLI loop through the selected agent adapter.
- `help`: explain CodeMind usage in this repository.

If no subcommand is obvious, treat the whole input as `ask <request>`.
"""


def write_command_package(
    out_dir: pathlib.Path,
    command_name: str,
    aliases: list[str] | None = None,
) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    names = [command_name, *(aliases or [])]
    for name in names:
        command_path = out_dir / "commands" / f"{name}.md"
        command_path.parent.mkdir(parents=True, exist_ok=True)
        command_path.write_text(command_body(name), encoding="utf-8")
        files.append(str(command_path.relative_to(out_dir)))

    readme = out_dir / "README.md"
    readme.write_text(f"""# CodeMind Command Export

Generated at: {datetime.now().isoformat(timespec='seconds')}

This package provides a slash-command style entrypoint for coding agents that support commands.

## Command

- `/{command_name}` -> `commands/{command_name}.md` (canonical)
{chr(10).join(f"- `/{alias}` -> `commands/{alias}.md` (compatibility alias)" for alias in aliases or [])}

The command defaults to current-session CodeMind mode: it uses the host agent session as Planner/Generator and uses the CodeMind CLI for deterministic scaffolding and gates such as `scaffold`, `workflow-check`, `context-pack`, `completion-check`, and `summary`. Detached CLI loops are available only when the user explicitly asks for `detached` / `cli-ask` mode.

## Install examples

```bash
./automind.sh export-command --install all
./automind.sh export-command --install claude
./automind.sh export-command --install trae
./automind.sh export-command --install trae-cn
```

Installed user-level paths:

- Claude: `~/.claude/commands/{command_name}.md`
- Codex: `~/.codex/commands/{command_name}.md`
- Trae: `~/.trae/commands/{command_name}.md`
- Trae-CN: `~/.trae-cn/commands/{command_name}.md`
""", encoding="utf-8")
    files.append(str(readme.relative_to(out_dir)))
    return files


def command_targets(agent: str, command_name: str) -> list[tuple[str, pathlib.Path]]:
    return resolve_command_targets(agent, command_name)



def install_command(
    out_dir: pathlib.Path,
    agent: str,
    command_name: str,
    aliases: list[str] | None = None,
) -> dict:
    if agent == "none":
        return {"agent": "none", "installed": False}
    names = [command_name, *(aliases or [])]
    targets = [
        (name, kind, target)
        for name in names
        for kind, target in command_targets(agent, name)
    ]
    if not targets:
        return {"agent": agent, "installed": False, "reason": "No supported user-level command folder detected. Export only."}
    if agent == "auto" and targets:
        first_kind = targets[0][1]
        first_agent = first_kind.split(":", 1)[0]
        selected = [item for item in targets if item[1].startswith(f"{first_agent}:")]
    else:
        selected = targets
    installed = []
    for name, kind, target in selected:
        target.parent.mkdir(parents=True, exist_ok=True)
        source = out_dir / "commands" / f"{name}.md"
        shutil.copy2(source, target)
        installed.append({"kind": kind, "command": f"/{name}", "path": display_path(target)})
    return {"agent": agent, "installed": True, "targets": installed}


def write_json(path: pathlib.Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", nargs="?", default=str(ROOT / "dist" / "codemind-command"))
    parser.add_argument("--clean", action="store_true", default=True, help="Remove existing output directory first (default true)")
    parser.add_argument("--install", choices=["none", "all", "auto", "claude", "codex", "trae", "trae-cn"], default="none", help="Optionally install to user-level slash-command folders. all=claude+codex+trae+trae-cn. Default: none.")
    parser.add_argument("--command-name", default="codemind", help="Slash command name without leading slash. Default: codemind")
    parser.add_argument("--no-legacy-alias", action="store_true", help="Do not export/install the legacy /automind alias.")
    args = parser.parse_args()

    command_name = args.command_name.strip().lstrip("/")
    if not command_name:
        raise SystemExit("--command-name must not be empty")

    out_dir = pathlib.Path(args.out_dir).expanduser().resolve()
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    aliases = (
        ["automind"]
        if not args.no_legacy_alias and command_name == "codemind"
        else []
    )
    files = write_command_package(out_dir, command_name, aliases)
    install_result = install_command(out_dir, args.install, command_name, aliases)
    manifest = {
        "name": "codemind-command",
        "commandName": command_name,
        "slashCommand": f"/{command_name}",
        "compatibilityAliases": [f"/{name}" for name in aliases],
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "files": files,
        "install": install_result,
    }
    write_json(out_dir / "manifest.json", manifest)
    print(json.dumps({
        "result": "pass",
        "outDir": str(out_dir),
        "command": f"/{command_name}",
        "files": len(files) + 1,
        "install": install_result,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
