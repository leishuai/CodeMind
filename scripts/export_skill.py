#!/usr/bin/env python3
"""Export a generic AutoMind AgentSkill package.

The export is intentionally generic and agent-agnostic. It packages the stable
workflow docs, prompt templates, selected summaries, and a minimal artifact-shape
example so another coding agent can understand AutoMind as a harness-loop skill.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
from datetime import datetime
from typing import Iterable

from agent_targets import display_path, skill_target_entries, skill_targets

ROOT = pathlib.Path(__file__).resolve().parents[1]

DOCS = [
    "docs/README.md",
    "docs/workflow.md",
    "docs/tui-session-observability.md",
    "docs/agent-adapters.md",
    "docs/phase1-initialization.md",
    "docs/phase2-requirement.md",
    "docs/phase3-verification.md",
    "docs/phase4-summary.md",
    "docs/phases/demand-definition.md",
    "docs/phases/verification-execution-planning.md",
    "docs/phases/brainstorm.md",
    "docs/phases/requirements.md",
    "docs/phases/plan.md",
    "docs/phases/testcases.md",
    "docs/phases/pre-implementation-review.md",
    "docs/phases/delivery.md",
    "docs/phases/evaluation.md",
    "docs/phases/completion.md",
    "docs/references/installation-runtime.md",
    "docs/references/state-actions.md",
    "docs/references/skill-command-driver-checklist.md",
    "docs/references/command-script-catalog.md",
    "docs/references/log-evidence-guide.md",
    "docs/references/app-use-verification.md",
    "docs/references/verification-flow.md",
    "docs/references/verification-flow-ios.md",
    "docs/references/verification-flow-android.md",
    "docs/references/test-design-guide.md",
    "docs/references/probe-flow-generation.md",
    "docs/references/dependency-check.md",
    "docs/references/feature-sound-effect-loop-example.md",
    "schemas/workflow.schema.json",
    "schemas/runtime-state.schema.json",
    "schemas/automind-workflow-state.schema.json",
    "schemas/automind-stage-state.schema.json",
    "schemas/automind-workflow-event.schema.json",
    "schemas/brainstorm.schema.json",
    "schemas/requirements.schema.json",
    "schemas/plan.schema.json",
    "schemas/testcases.schema.json",
    "schemas/pre-implementation-review.schema.json",
    "schemas/delivery.schema.json",
    "schemas/completion-report.schema.json",
    "schemas/trace.schema.json",
    "schemas/run-card.schema.json",
    "schemas/process-eval.schema.json",
    "schemas/probe-flow.schema.json",
    "schemas/evaluation.schema.json",
]

REQUIREMENTS = [
    "requirements/android-tools.txt",
    "requirements/ios-tools.txt",
    "requirements/visual-tools.txt",
]

TEMPLATES = [
    "templates/phase2_planner_prompt.md",
    "templates/test_planner_prompt.md",  # deprecated alias; keep for external compatibility
    "templates/generator_prompt.md",
    "templates/evaluator_prompt.md",
    "templates/quality_review_prompt.md",
    "templates/visual_review_prompt.md",
    "templates/summary_refiner_prompt.md",
]

EXAMPLE_FILES = [
    "examples/README.md",
    "examples/probe-flows/README.md",
    "examples/probe-flows/android-basic.json",
    "examples/probe-flows/ios-intent-basic.json",
]

SUMMARY_FILES = [
    "summaries/README.md",
    "summaries/preloaded/README.md",
    "summaries/preloaded/android-bytecode-transform-cache.md",
    "summaries/accumulated/README.md",
    "summaries/preloaded/common-build-verification-playbook.md",
    "summaries/preloaded/client-ui-repair.md",
    "summaries/preloaded/android-readiness.md",
    "summaries/preloaded/ios-app-smoke.md",
    "summaries/preloaded/ios-external-xcuitest-runner.md",
    "summaries/preloaded/ios-readiness.md",
    "summaries/preloaded/ios-screenshot.md",
    "summaries/preloaded/ios-ui-actions.md",
    "summaries/preloaded/ios-signing-install.md",
]

INTERNAL_OPTIONAL = [
    "internal/rc-release-notes.md",
    "internal/product-story-notes.md",
    "internal/architecture-loop-notes.md",
    "internal/verification-platform-notes.md",
    "internal/summary-reuse-notes.md",
    "internal/maintenance-cleanup-notes.md",
]

PRIVATE_SUMMARY_PATTERNS = [
    "summaries/accumulated/business/**/*",
]


def copy_file(rel: str, out_dir: pathlib.Path) -> str | None:
    src = ROOT / rel
    if not src.exists():
        return None
    dst = out_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return rel


def copy_many(paths: Iterable[str], out_dir: pathlib.Path) -> list[str]:
    copied: list[str] = []
    for rel in paths:
        result = copy_file(rel, out_dir)
        if result:
            copied.append(result)
    return copied


def write_skill_md(out_dir: pathlib.Path) -> None:
    (out_dir / "SKILL.md").write_text(
        """---
name: automind-skill
description: AutoMind evidence-driven harness loop for coding agents. Use when a coding task needs explicit requirements, preflight, evaluator evidence, retry/replan decisions, mobile/script verification, or reusable summaries.
---

# AutoMind Skill

AutoMind is a harness-loop skill for coding agents. It does not replace the coding agent; it gives the agent a disciplined loop:

```text
User request
  -> Brainstorm.md
  -> Requirements.md
  -> TestCases.md
  -> Plan.md
  -> workflow-check
  -> Generator
  -> Delivery.md
  -> Evaluator
  -> Validation.md + evaluation.json
  -> completion-check
  -> Retry / Replan / Ask Human / Finish
  -> Summary / Reuse
```

Minimal execution protocol:

```text
Prepare -> Plan -> Build -> Verify -> Finish
```

- Prepare: discover `<AUTOMIND_CLI>`, run from the target project root, scaffold task artifacts, and verify `TASK_DIR` is under the target workspace.
- Plan: digest the user request against the project workspace, research implementation/verification/risk/opportunity surfaces, then refine `Brainstorm.md`, `Requirements.md`, `TestCases.md`, and `Plan.md`.
- Build: Generator implements or repairs product/runtime code, writes `Delivery.md`, and updates the Plan implementation checklist.
- Verify: deterministic verifier or context-isolated Evaluator writes `Validation.md`, `evaluation.json`, evidence logs, and verification checklist updates.
- Finish: run `completion-check`; then prefer AI-refined summary/reuse (`summary --ai <agent>` or `summary-refine`) with deterministic fallback, run `record-check`, and generate/inspect `Report.html` before final handoff. The final user-facing handoff must use natural language to say the task is complete, what was generated, ask the user to open `Report.html` first, and call out the key files to inspect. In the report, key runtime proof logs such as `music-events.txt`, full logcat, screenshots, and `VerificationLedger.json` should be summarized in each TC row's `Key Evidence` column; the complete artifact list can remain in the final column for traceability.

Hard gates: `workflow-check` before Build, `Delivery.md` before final Verify,
and `completion-check` before Finish. After completion is proven, `summary` and
`record-check` are mandatory final-handoff steps so reusable lessons seed the
next task's `Reuse.md`. In skill/current-session mode, prefer `summary --ai <agent>`
or `summary-refine`; AutoMind falls back to deterministic summary when the agent
is unavailable or returns invalid JSON. Do not replace these gates with chat confidence.

## Updating AutoMind itself

When the user asks to update, upgrade, refresh, reinstall, or sync AutoMind itself,
prefer the formal maintenance command:

```bash
<AUTOMIND_CLI> update
```

Examples of update intent:

- "更新 AutoMind"
- "升级一下 automind"
- "refresh AutoMind runtime"
- "同步最新 AutoMind skill/command"

This updates the AutoMind runtime, CLI wrapper, skill package, and `/automind`
slash-command integrations. Update-only intent is **not** an AutoMind task:
do not run `scaffold`, do not create `.automind/tasks/<task>/`, and do not enter
the harness loop. If `<AUTOMIND_CLI> update` is unavailable because the installed
runtime is too old, suggest the documented one-line installer from
`docs/references/installation-runtime.md`.

## Continue-until-done protocol (Skill mode automation)

Skill mode lacks the orchestrator while-loop, so the host agent itself must
not stop early. Follow this protocol on every turn:

1. After every `workflow-check` / `completion-check` / `script-command` /
   Evaluator turn, read the JSON `nextActionPrompt` field and obey it as a
   binding instruction. Do not paraphrase it into "I'll wait for further
   input" or "let me know if you want me to continue".
2. The only legal stop conditions are:
   - `completion-check` returned `result=pass`; or
   - `evaluation.json.nextAction=ask_user` AND
     `askUserQuestion.category` is one of the 5-item whitelist
     (`unauthorized_destructive_or_sensitive`, `system_or_external_dependency`,
     `real_device_or_signing`, `manual_visual_confirmation`,
     `repeated_same_failure`); or
   - `automind tick-iteration` exited non-zero (budget exhausted) and
     escalated to `ask_user`.
3. Before each Generator/Evaluator turn, call
   `<AUTOMIND_CLI> tick-iteration <task-code> <phase>` to enforce the
   `AUTOMIND_MAX_ITERATIONS` / `AUTOMIND_MAX_REFLECTIONS_PER_TC` budget client-side. A non-zero exit means halt
   and escalate, not silently continue.
4. After scaffolding (`scaffold` / `ask`) AutoMind writes
   `.automind/current-task` so Hooks and follow-up CLI calls can discover
   the active task without re-asking the user. `completion-check` clears it
   on pass. `<AUTOMIND_CLI> continue` reads it and returns the resume context
   (status, iteration, nextAction, latestUserAnswer/latestUserMessage, nextActionPrompt) when a session is
   reopened mid-task. Natural-language TUI/session messages are stored in `user-messages.json` and must be reconciled with Requirements/TestCases/Plan before coding.
5. If the host agent's reply contains "task complete", "all done", or any
   finish-style language, it must be backed by a green `completion-check`
   in this turn. Otherwise treat the reply as drift and re-run the loop.

## Skill-mode loop driver protocol

Skill mode lacks an orchestrator-owned while loop. The host agent must act as a
lightweight loop driver and keep moving without waiting for the user after every
substep:

1. Start or resume with `<AUTOMIND_CLI> continue [task-code]` when available, then run `<AUTOMIND_CLI> phase-gate <task-code> auto` before choosing the next phase. If the CLI is unavailable, read `automind-workflow-state.json` first, then `runtime-state.json.stateSummary` as migration fallback, `workflow.json`, `runtime-state.json`, and the latest structured result.
2. Execute exactly one next phase action: refine Plan, run Generator, run
   deterministic/model Evaluator, run `completion-check`, or ask the user only
   for an allowed blocker.
3. After every helper/check/evaluator result, parse structured control output:
   `nextActionPrompt`, `evaluation.json.nextAction`, `completion-report.json`,
   and runtime-state nextAction as resolver inputs. Immediately refresh/read `automind-workflow-state.json` and perform the next safe action; use `runtime-state.json.stateSummary` only as migration fallback/diagnostic.
   If `workflow-check` or `completion-check` is red, do **not** trust
   `runtime-state.json` finish-style markers such as `done` / `completed` /
   `finished`; treat that as a false-finish drift, repair the blocking artifact
   (often `evaluation.json` missing `nextAction`/`testResults`), and continue by
   the effective next phase instead of stopping.
4. Stop only on green `completion-check`, allowed `ask_user`, max-iteration
   escalation, explicit user pause/abort, or non-recoverable unsafe condition.
5. If artifacts are inconsistent, return to the phase that owns the broken
   artifact and rerun `workflow-check`; do not ask the user to manually steer the
   loop.

## JSON handoff protocol for Skill mode

Skill mode should not rely on Markdown and chat memory alone. Use the same
JSON input/output contracts as the CLI loop whenever the CLI/runtime is
available:

1. At each phase boundary, read `workflow.json` first to identify the current
   phase, upstream dependencies, expected sidecars, and gate status.
2. For each phase, consume the upstream JSON sidecars as structured inputs
   before editing the human-readable Markdown artifacts:
   - Brainstorm -> `brainstorm.json`;
   - Requirements -> `requirements.json`;
   - Plan -> `plan.json`;
   - TestCases -> `testcases.json`;
   - Pre-implementation review -> `pre-implementation-review.json`;
   - Build/Delivery -> `delivery.json`;
   - Verify -> `evaluation.json`;
   - Finish -> `completion-report.json`.
3. After updating a phase's Markdown artifact, run `<AUTOMIND_CLI> workflow-check
   <task-code>` when available. It refreshes/validates phase sidecars and the
   derived `workflow.json`; treat hard issues as blocking handoff.
4. Before Generator work, require green Plan-side JSON continuity: resolved
   pre-implementation review, `Rxx/AC-xxx -> TC-* -> Plan -> workflow.json`, and
   no hard `workflow-check` issues.
5. Before Evaluator work, require `delivery.json`/`Delivery.md` plus a context
   pack; Evaluator writes `evaluation.json` as the next structured control
   signal.
6. Before Finish, run `completion-check`; consume `completion-report.json`,
   `VerificationLedger.json`, and updated `evaluation.json` instead of trusting
   prose claims.
7. If the full CLI is unavailable, manually maintain the same JSON sidecars as
   well as possible, validate against `schemas/*.schema.json` when feasible, and
   explicitly state any missing checker coverage as a limitation.

## TestCase -> verifier operation protocol

During Verify, convert each required `TC-*` / `testcases.json.testcases[]` entry
into one concrete verifier operation:

1. Prefer structured `testcases.json` fields (`runtimeLevel`, `executor`,
   `runbook.command`, `runbook.steps`, `runbook.assertions`,
   `runbook.expectedEvidence`) and use `TestCases.md` for readable detail.
2. Choose the most concrete executor: project-native command first; then
   `script-command`; then Android probe-flow; then iOS XCUITest/probe-flow; then
   browser/project E2E; then external-sink proof; static/quality review only for
   quality/static cases or explicitly blocked functional cases.
3. For app/UI cases, map runbook actions to executable steps: build/start,
   launch/open, tap/click/input/swipe, optional guarded `tap_if_present`,
   assertions/postChecks, and evidence collection. Missing selectors trigger a
   discovery/refine/rerun cycle, not a pass.
4. Normalize every operation into `evaluation.json.testResults[]` with result,
   evidence paths, coverage, `evidenceAssessment`, and nextAction.
5. Let `completion-check` decide final finish; Evaluator `finish` is only a
   proposal until required TC/AC/evidence coverage is green.


## Recommended installation / runtime

Best user experience is **full AutoMind installation** with the single public install command: `curl -fsSL https://raw.githubusercontent.com/leishuai/Automind/main/install-curl.sh | bash`. It installs the AutoMind CLI/runtime, this skill, and the `/automind` command for supported coding agents. In that mode, the skill gives the agent the workflow and prompt protocol, while the full AutoMind checkout provides `automind` / `./automind.sh` plus orchestrator/scripts/adapters.

When a full AutoMind runtime is available, agents may use:

1. `automind` or `./automind.sh` helpers/gates first (`scaffold`, `status`, `workflow-check`, `context-pack`, `script-command`, `completion-check`, `trace`, `process-check`, `summary`, `summary-refine`);
2. documented scripts/adapters from the full checkout only when the command catalog says direct script use is appropriate.

Important workspace rule: AutoMind runtime and user workspace are separate. The public installer puts the runtime checkout at `$HOME/.automind/automind` by default (`AUTOMIND_HOME` may override it) and creates a wrapper at `$HOME/.local/bin/automind` by default (`AUTOMIND_BIN_DIR` may override it). Run AutoMind CLI commands from the target project/workspace root. Task artifacts are written to `$AUTOMIND_WORKSPACE_ROOT/.automind/tasks` when set, otherwise to the current working directory's `.automind/tasks`. If the agent is not currently in the target project root, set `AUTOMIND_WORKSPACE_ROOT=/path/to/project` before running `automind ...`; do not let an installed AutoMind checkout become the task workspace. Do not copy developer-machine absolute paths from old logs; resolve runtime resources relative to the current runtime root. See `docs/references/installation-runtime.md`.

## Runtime discovery required at task start

This skill does **not** automatically install or load the AutoMind CLI every time
the skill is invoked. Skills are static instructions. At the start of every
AutoMind task, the agent must actively discover whether the full AutoMind runtime
is available:

1. Try `automind help` from the target project/workspace root.
2. Try `./automind.sh help` only if the current project vendors AutoMind.
3. Try the default install path: `$HOME/.automind/automind/automind.sh help`.
4. Try `$AUTOMIND_HOME/automind.sh help` when `AUTOMIND_HOME` is set.

After selecting `<AUTOMIND_CLI>`, keep invoking it from the target project/workspace root, or prefix commands with `AUTOMIND_WORKSPACE_ROOT=/path/to/project` if the shell cwd cannot be changed.
If `<AUTOMIND_CLI>` is an absolute/runtime path such as `$HOME/.automind/automind/automind.sh`, that path is only the executable location; it is not the task workspace.

Use the first candidate that succeeds as `<AUTOMIND_CLI>` for helper/gate
commands.

If none of the four probes succeeds, treat AutoMind as not installed and run
this guided-install loop instead of silently giving up or silently installing:

1. Tell the user the skill is available but the full AutoMind CLI/runtime was not
   detected on this machine, and that full installation gives the best
   experience (CLI/runtime + `/automind` command + helpers/gates).
2. Ask the user once, explicitly, whether AutoMind should install it now with the
   single public install command:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/leishuai/Automind/main/install-curl.sh | bash
   ```

3. If the user agrees (or the user already requested full-auto/no-confirmation
   mode, or the environment policy explicitly allows tool installation), run that
   command for the user, then re-run the four discovery probes to pick up the
   newly installed `<AUTOMIND_CLI>` (typically `$HOME/.automind/automind/automind.sh`
   or the `automind` wrapper on `PATH`). Verify with `<AUTOMIND_CLI> help`. If the
   wrapper is not yet on `PATH` in the current shell, fall back to the absolute
   path `$HOME/.automind/automind/automind.sh` (or `$AUTOMIND_HOME/automind.sh`).
   After a verified install, continue the task in full runtime mode.
4. If the install command fails (network/DNS/permission), report the concrete
   error, do not retry blindly more than once, and fall back to skill-only mode.
5. If the user declines, do not install. Continue in skill-only mode using
   project-native verification while writing the same AutoMind artifacts.

Hard rule: never install the CLI silently. Installation may proceed only after
explicit user consent, an explicit full-auto/no-confirmation request, or an
environment policy that explicitly allows tool installation. Asking once and
acting on the answer is the intended behavior; installing without any of those
green lights is not.

In skill/slash-command mode, `/automind <request>` is the canonical current-session end-to-end flow for Codex, Claude, Trae, and Trae-CN: the host coding agent remains the Planner/Generator, AutoMind CLI provides scaffolding/gates/evidence helpers, and the agent must keep looping through verification and repair until required evidence passes, `ask_user` needs a human decision, or an explicit unsafe/non-recoverable stop condition is reached. `/automind --detached <request>` (or the equivalent `automind ask "<request>" <agent>`) is the detached-CLI variant; use it only for explicit detached/background requests. `/automind ask <request>` is kept as an alias for `/automind <request>` and behaves identically in the current session. In detached Codex/Claude/Trae/Trae-CN mode, Planner/Generator may reuse one task-local primary CLI session; Evaluator must still be a fresh isolated invocation.

Think of `/automind <request>` as structured natural language for the current model: "use the AutoMind skill/protocol in this conversation; call AutoMind CLI helpers when useful; run the harness loop end-to-end; do not start a detached agent unless explicitly requested."

Result exchange across current sessions, native isolated subagents, deterministic verifiers, and detached CLI processes happens through `.automind/tasks/<task>/` artifacts rather than hidden chat memory. `automind-workflow-state.json`, `automind-workflow-events.jsonl`, stage state files, `runtime-state.json`, `evaluation.json`, `Validation.md`, `Delivery.md`, `VerificationLedger.json`, and `logs/iter-N/*` are the shared contract for control state, evidence, resume, and integration.

When only this skill package is available, first suggest the single full install command (`curl -fsSL https://raw.githubusercontent.com/leishuai/Automind/main/install-curl.sh | bash`). If installation is not allowed, follow the same workflow manually and use project-native build/test/device commands for evidence.

Mobile/visual automation helper packages are not installed by the public installer and are not bundled as executable code in this skill. In full runtime mode, required low-risk Python helpers may be auto-created/repaired during preflight/evaluation with `automind setup-automation-tools android`, `automind setup-automation-tools ios`, or `automind setup-automation-tools visual`; those commands use the full runtime `requirements/android-tools.txt`, `requirements/ios-tools.txt`, and `requirements/visual-tools.txt`. They create project-local Python virtualenvs in the target workspace only and do not install system SDKs, signing material, device trust settings, OCR engines, browser drivers, or privileged services. Transient network/DNS package-index failures may be retried once with explicit logs; persistent failures must be classified and routed to runtime-helper fallback, lower-capability fallback, or `ask_user`. Human approval is still required for high-impact actions such as installing Xcode/Android SDKs, changing signing/keychains, trusting devices, or starting `tunneld`/sudo services.

For web/client/server target-project dependencies, use the target project's own package manager, lockfiles, and documented scripts. In full runtime mode, `automind dependency-check [task-code] [iteration]` is an optional read-only discovery helper for unclear dependency paths; it recommends commands such as `npm ci`, `pnpm install --frozen-lockfile`, `yarn install --immutable`, `uv sync --frozen`, `poetry install --sync`, Gradle/Maven wrappers, Docker config/build, or repo-specific setup; it does not install those dependencies. Ask the user before system SDK/runtime installs, Docker/database service startup, browser-driver installs, private registry credentials, signing, or device-trust changes.

## When to use

Use AutoMind when work needs any of these:

- testable requirements instead of drifting prompts;
- real build/test/device verification;
- Android or iOS app harness validation;
- structured `evaluation.json` feedback;
- `workflow-check` continuity checks across `Rxx -> AC-xxx -> TC-* -> Plan -> evaluation`;
- `completion-check` final gate for required `TC-*`, required `AC-xxx`, and evidence coverage. Required `TC-*` cases must pass with executed evidence; environment/device/tool blocker classification is routing information, not a pass. Required App/UI/runtime cases need hard product-launch/action/assertion evidence plus `evidenceAssessment.verdict=proved`. Required clean-build/release/merge cases need attached project-native build evidence plus `evidenceAssessment.verdict=proved`;
- self-explaining `status` output with next recommended action and suggested commands;
- repeated Generator/Evaluator loops;
- human approval for sensitive or destructive actions;
- reusable task summaries and lessons.
- local reuse memory: `summary.md` plus `.automind/summary/*` seed the next task's `Reuse.md` in full runtime mode.
- AI summary refinement preferred at Finish: `summary` first builds a deterministic seed; `summary --ai <agent>` / `summary-refine` asks an agent to classify and condense it, then deterministic validation filters the result before reuse memory is updated. If AI fails, deterministic summary remains the fallback.
- Prefer measurable visual evidence first. AI visual review may supplement UI verification when screenshot/image evidence exists, the host model supports images, and pure technical measurement cannot fully settle the visual claim. Use `templates/visual_review_prompt.md` after deterministic app/UI evidence is captured; it can catch wrong screen, overlays, clipping, overlap, unreadable text, and visual mismatch, but it does not replace required UI execution, measurable bounds/frame checks, screenshot diff, deterministic visual inspection, OCR, or project-native tests. If a required visual assertion needs semantic image understanding and no vision-capable model is available, first try deterministic proof such as `automind setup-automation-tools visual` + `automind visual-inspect ...`, screenshot diff, OCR, bounds/hierarchy, or project-native snapshot/layout tests. If measurable evidence and AI Visual Review still cannot prove correctness, capture screenshot(s) and ask the user to confirm as the final fallback. Do not pass from screenshot paths alone; explicit user confirmation must be recorded before finish. If no screenshot/evidence path exists, route to `blocked`/`ask_user`/`replan`.
- known successful/avoid paths: before choosing build/test/verification commands, inspect `Reuse.md` for `Successful path:` and `Avoid path:` entries. Prefer a matching known-successful path, and record why if it is ignored.

## Mandatory startup read protocol

This is not a reference-only list. At the start of every AutoMind task, before
planning, coding, or validating, the agent must read these files in this order
and use them as active instructions:

1. `docs/workflow.md` — canonical end-to-end loop, pre-implementation review, checklist ownership, and completion gates.
2. `docs/tui-session-observability.md` — CLI/TUI shell, shared session artifacts, trace, process-check, and run-card learning.
3. `docs/phase2-requirement.md`, `docs/phases/demand-definition.md`, `docs/phases/verification-execution-planning.md`, and `templates/phase2_planner_prompt.md` — required Phase 2A Demand Definition plus Phase 2B Verification & Execution Planning before implementation. Use `docs/references/test-design-guide.md` for concrete testcase runbook examples when needed.
4. `docs/phase3-verification.md` and `templates/evaluator_prompt.md` — mandatory verification/evaluator behavior, evidence, `evaluation.json`, and nextAction rules. Read `templates/visual_review_prompt.md` when screenshot/image evidence and a vision-capable host model are available for UI visual review.
5. `docs/references/command-script-catalog.md` — choose AutoMind CLI helpers or direct scripts safely.
6. `docs/references/skill-command-driver-checklist.md` — script-gated phase handoff for skill/command mode.
7. `docs/references/verification-flow.md` — cross-platform verification flow details when device/app/runtime checks are needed; load `docs/references/verification-flow-ios.md` or `docs/references/verification-flow-android.md` on demand for that platform's device prerequisites, flows, and UI-runner ladder.
8. `docs/agent-adapters.md` — required when invoking Codex, Claude Code, Trae/Trae-CN, or another runtime.
9. `templates/generator_prompt.md` before Generator work.
10. Relevant public-safe `summaries/` entries before real mobile work.

Do not skip this startup read because SKILL.md already summarizes the flow. If
time/context is constrained, read at minimum `docs/workflow.md`,
`docs/phase2-requirement.md`, `docs/phases/demand-definition.md`, `docs/phases/verification-execution-planning.md`, `templates/phase2_planner_prompt.md`,
`docs/phase3-verification.md`, and `templates/evaluator_prompt.md`, then record
that the remaining docs still need to be consulted before choosing commands,
adapters, or platform-specific verification.

## Core task files

Each task must use these artifacts unless the user explicitly requested a single-stage helper operation:

- `Brainstorm.md`
- `Reuse.md`
- `Requirements.md`
- `TestCases.md`
- `Plan.md`
- `Delivery.md`
- `Validation.md`
- `evaluation.json`
- `automind-workflow-state.json`
- `automind-workflow-events.jsonl`
- `stages/*-stage-state.json`
- `runtime-state.json.stateSummary` (migration fallback)
- `runtime-state.json`
- `phase-gate` output for each skill/command handoff
- `summary.md`
- `logs/iter-N/`
- phase sidecars when available: `brainstorm.json`, `requirements.json`, `testcases.json`, `plan.json`, `pre-implementation-review.json`, `delivery.json`, `completion-report.json`
- observability/learning artifacts when available: `events.jsonl`, `user-answers.json`, `user-messages.json`, `trace.json`, `process-eval.json`, `run-card.json`

## Non-negotiable rules

- Execute the Mandatory startup read protocol at the beginning of each AutoMind task; do not treat the doc list as optional background reading.
- Use the AI Phase 2 Refiner before implementation: the deterministic scaffold is only a starting point/fallback.
- Follow the minimal execution protocol: Prepare -> Plan -> Build -> Verify -> Finish. Keep it simple, but do not skip the three hard gates.
- At the start of each AutoMind task, discover `<AUTOMIND_CLI>` with `automind help`, project-local `./automind.sh help`, `$HOME/.automind/automind/automind.sh help`, or `$AUTOMIND_HOME/automind.sh help`. Run the chosen CLI from the target project/workspace root, or set `AUTOMIND_WORKSPACE_ROOT=/path/to/project`; if no CLI is available, recommend full install before falling back to skill-only mode.
- Before Generator edits product/runtime code, explicitly decide and record the pre-implementation user review result in `Brainstorm.md` and `runtime-state.json.planner.preImplementationReview`: `auto_proceed`, `ask_user`, or `replan`. This is a mandatory gate, not advice. Brainstorm is the demand digestion phase. Unless the user explicitly requested full-auto/no-confirmation mode (for example “一站到底”, “全自动模式”, “不用问用户”, “不用确认”, or “full auto/no confirmation”), non-trivial implementation/behavior-change tasks must ask the user once in pre-implementation. The one-shot ask_user bundle must first prompt the user to review the key planning artifacts — above all `Requirements.md` and `TestCases.md`, plus `Brainstorm.md`/`Plan.md` — because a wrong requirement or test design sends the whole route off course and wastes all later development and verification. It must then confirm whether the requirement is clear, goal/scope/non-goals, key assumptions, recommended approach, known risks, verification direction, known must-pass `AC-*`, required `TC-*`, evidence expectations, rollback/replan boundaries, and authorization for non-low-risk operations such as overwrite install, uninstall/delete/reset, account login, signing/device-trust changes, privilege escalation, external upload, payment, or production-impacting actions. The bundle must also remind the user that the host coding agent's command-execution permissions may interrupt the run — for example commands outside the agent sandbox or high-risk commands may prompt the user for approval — so the user can stay available to approve them promptly or grant the needed permission mode up front. When the task must reproduce a specified UI design (Figma / 设计稿 / 视觉还原 / pixel-level / UI consistency signals, or a Figma link in the requirement) and no usable local design image exists, fold a design-reference request into the same bundle: ask for the Figma link or a local export under `.automind/tasks/<task>/design/` as the `visual-inspect --baseline` reference; if the user declines or none can be obtained, do not block — degrade the UI case to structure/text/element-order assertions plus final human screenshot confirmation and record the reason. Do not ask this for non-UI backend/script tasks. Only client/app development or verification tasks need the real-device vs simulator/emulator decision as one part of that bundle. Auto-proceed only for verification-only, documentation-only, mechanical/low-risk edits, already-approved directions, explicit full-auto/no-confirmation automation, and safe no-loss runtime recovery (`agent_unavailable`/`agent_timeout` -> `resume_after_recovery`). After the gate is resolved, continue automatically through Generator implement/repair -> Evaluator verify/re-verify -> `completion-check` until finish is proven or a real stop condition occurs.
- Run `<AUTOMIND_CLI> phase-gate <task-code> auto` before each skill/command phase handoff. Use `phase-gate <task-code> build|verify|finish` for explicit gates. It reads/seeds `automind-workflow-state.json` as the central workflow control state and blocks unsafe transitions. When the handoff is about to enter `delivery` or `evaluation`, `phase-gate` deterministically refreshes missing/stale `phase-reuse/generator.md` or `phase-reuse/evaluator.md` without resetting fresh acknowledged reuse.
- In skill/command mode, use the `checklist[]` / `checkboxMarkdown[]` returned by `automind-workflow-state.json` / `phase-gate` as the native TODO/checkbox plan. Complete required checklist items one by one with the coding agent's normal ability to read, edit, test, and write artifacts; run a CLI command only when that checklist item explicitly names an AutoMind helper/gate. Review `Reuse.md` and relevant `phase-reuse/<phase>.md` when the checklist asks for them, as guidance only. Then rerun `phase-gate`.
- Run `<AUTOMIND_CLI> workflow-check <task-code>` before Build when the CLI is available; if it has hard issues, refine Plan artifacts instead of coding.
- Generator must write/update `Delivery.md` before final verification. Evaluator must not infer delivery only from chat.
- Run `<AUTOMIND_CLI> completion-check <task-code>` before claiming Finish; if it fails, route back by `evaluation.json.nextAction` or replan. If it passes, prefer `<AUTOMIND_CLI> summary <task-code> --ai <agent>` or `<AUTOMIND_CLI> summary-refine <task-code> <agent>`, fall back to `<AUTOMIND_CLI> summary <task-code>` when no agent is available, then run `<AUTOMIND_CLI> record-check <task-code>` and `<AUTOMIND_CLI> report <task-code>` before final handoff. The HTML report should show `<task> Automind Report`, Test Results with per-TC evidence/screenshots/logs, and Summary / Knowledge Deposition.
- Do not mark a required TestCase as passed merely because a build/test/device/tool issue was classified as `environment_blocked`, `mobile_device_unavailable`, `permission_blocked`, or `tool_missing`. Keep trying with Generator repair, safe reversible verification-unblock, `replan`, or `ask_user` for human/system decisions. For clean-build/release/merge cases, only attached build evidence plus `evidenceAssessment.verdict=proved` can pass.
- Required or strongly recommended verification actions default to automatic execution: build/compile/install/test/runtime smoke/project-native verifiers should run when needed for required TC/AC/evidence closure. Do not ask the user merely because the step is long-running or expensive; ask only for real sensitive/destructive/external decisions such as delete/uninstall/reset, account/login, external upload, payment, sudo/system configuration, keychain/signing material/device trust changes, production impact, or runtime/static downgrade.
- Required functional TestCases must be executable runbooks. For App/UI/client-facing work, specify preparation/preflight, build/install/deploy/start, launch/open, exact entry page/screen/route/activity/state, action sequence, assertions, and evidence; do not accept vague static-only rows such as "verify UI works". `completion-check` enforces attached hard evidence plus `evidenceAssessment.verdict=proved` for required App/UI/runtime cases, so source-only proof cannot finish.
- AutoMind can operate real apps for verification when the right platform runner is available. For Android, use `android-preflight` + `android-probe-flow` with generated `probe-flow.android.json` for tap/click/input/swipe/optional popup handling/assertions. For iOS, use XCUITest directly or materialize `probe-flow.ios.json` / `action-plan.ios.json` into Swift and run it with `ios-xcuitest` or a project/native runner; direct `pymobiledevice3 AccessibilityAudit` probing is an exploration fallback for reading the live accessibility tree and trying low-risk reversible presses when XCUITest selectors/runner setup are not yet available, not a replacement for `.xcresult` proof. For iOS project-native UI test targets, repeated Xcode/CoreDevice connection drops or automation-session startup failures while the iPhone is otherwise visible and Enable UI Automation is already on are device/host link recovery blockers, not product bugs: ask the user to keep the phone unlocked/lit, replug USB, accept trust prompts, and toggle Settings -> Developer -> Enable UI Automation off/on before retrying the same command. For Web, use `web-probe-flow` with `probe-flow.web.json` and project-native E2E commands (Playwright/Cypress/npm scripts) when available; do not silently install browsers/drivers. Safe close/skip/later/dismiss overlays may be auto-unblocked with evidence. Privacy/terms Agree/Allow/Continue, reject/deny, login/account grants, payment, delete/reset/uninstall, external upload, signing/device trust, or ambiguous/irreversible consent require explicit authorization or `ask_user`. Do not tell users AutoMind cannot click or navigate an app; instead encode the action, selector/confidence/risk, authorization, post-action assertion, and evidence, or route to `ask_user`/`replan` for missing device/signing/UI Automation/selectors or sensitive actions.
- Use `Plan.md` checklists as the short-term progress tracker: Generator updates `Implementation Checklist` (`T*` rows), Evaluator updates `Verification Checklist` (`TC-*` rows), and `status` summarizes them. Do not rely on chat memory for progress.
- At task start and before choosing build/test/verification commands, read `Reuse.md`. Prefer matching `Successful path:` entries and avoid same-condition `Avoid path:` entries; if a reusable path is ignored, state why in `Plan.md`, `TestCases.md`, or `Validation.md`.
- Generator may run in the current/main coding-agent session with full task context.
- Generator must review every failure, not just ones marked `needsModelReview`. The context pack's "All Failures Overview" lists all failures — treat each entry's `category` / `recoveryAction` as a starting point, not the final answer. Verify against raw evidence; if the prior assessment was wrong (e.g. it suggested "skip" but the real fix is `pod install`), override it. Document corrected entries in `Delivery.md` under "Re-triage of failures" with a `rootCause` object: `summary`, `confidence` (high/medium/low), `evidence`, `correctedCategory`, `recommendedAction`, and `whyPreviousApproachFailed`. Then proceed with the corrected fix.
- When a failure entry has `triageSource: "model_reviewed"` and a `rootCause` object, read the root cause analysis first — it contains a prior model's diagnosis and confidence. Use it to inform your repair approach, but still verify against current evidence.
- If a full AutoMind CLI is available in skill/slash-command mode, prefer `automind scaffold "<request>"` to create the task artifacts without launching another agent process. Before running it, ensure the shell cwd is the target project root or set `AUTOMIND_WORKSPACE_ROOT=/path/to/project`; verify the printed `TASK_DIR` is under that target project.
- Evaluator must be context-isolated from Generator. In skill mode, do not merely “switch roles” inside the same conversation and call it isolated.
- When `evaluatorContext` is recorded in `runtime-state.json`, `workflow-check` validates `inheritsGeneratorContext=false`, context pack paths, and context-pack validation status.
- Evaluator execution preference: deterministic platform/script verifier first; then a native isolated subagent/session if the host provides one and it can consume only the context pack; then a fresh external agent CLI process when native isolation is unavailable or detached/background operation is requested. If none is available, mark the task blocked/replan instead of pretending validation is independent.
- Before a model Evaluator in current-session mode, use `automind context-pack <task-code> [iteration]` when available. Evaluator must consume `logs/iter-N/evaluator-context.md/json` as the only orchestrator-provided task context and must not read raw Generator logs/transcripts.
- Evaluator context must be complete, audited, and non-redundant: include requirements, test cases, acceptance criteria, delivery artifacts, environment/device constraints, and prior validation state, but exclude Generator reasoning/code-authoring process/raw transcripts. Evaluator capability must be strong: independently run real app/product verification when available, including Android/iOS preflight, probe-flow, XCUITest, script-command, tests, logs, screenshots, UI hierarchy evidence, and `ui-evidence-check` for auditable evidence completeness.
- Generator owns product/runtime-code implementation and repair. Evaluator owns
  verification, failure classification, evidence, `Validation.md`, and
  `evaluation.json`; it routes product failures to `retry_generator` rather
  than repairing product code itself. Evaluator may only self-repair
  verifier/probe-flow/test-harness issues when the validation method is wrong.
  For failed/partial/blocked runtime paths, write generic `runtimePath`,
  `failureClass`, `observedSignals`, optional `shouldRetry`, and `retryAdvice`
  into `evaluation.json.testResults[]` or `failedChecks[]`; use `unknown` when
  evidence is ambiguous rather than inventing project-specific classes.
- Every failure gets model review — not just ones the code couldn't classify.
  The context pack's "Model-Review Attention Signals" and "All Failures Overview"
  are your starting point. This covers code-classifier misclassification, repeated
  failed retries (same `sameProblemKey`), and gate failures (completion/workflow
  check stuck across iterations). For each failed/blocked entry:
  1. Review the raw evidence (logs, screenshots, stack traces, exit codes).
  2. Evaluate whether the current `category` and `recoveryAction` are correct.
  3. If you agree: keep `triageSource: "code_deterministic"` as-is, or upgrade to `"model_reviewed"` if you added significant analysis.
  4. If you disagree: replace with `triageSource: "model_reviewed"`, `needsModelReview: false`, corrected `category` and `recoveryAction`, and add a `rootCause` object with `summary`, `confidence` (high/medium/low), `evidence` (list of evidence paths), `correctedCategory` (if different), `recommendedAction`, and `whyPreviousApproachFailed` (why the prior classification/recovery didn't work — applies to code classifier misclassification, repeated failed retries, or stuck gates alike).
  5. Confidence guide: `high` = direct proof (clear error message, reproducible stack); `medium` = strong inference from multiple indirect signals; `low` = speculation, other explanations also plausible.
- If verification is blocked by unrelated build/test/workspace issues, AutoMind may create minimal reversible verification unblock changes only after checkpointing or recording a diff. Record them in `Delivery.md`/`Validation.md` and `evaluation.json.verificationUnblockChanges`, then restore or explicitly promote them before finish. Active temporary unblock changes block completion.
- Do not treat environment/device/signing failures as code failures.
- Do not invent a new validation target without updating requirements/test cases/probe-flow.
- Do not silently run destructive or sensitive actions.
- Low-risk Android/iOS Python helper package setup may run automatically when it is required by the selected verifier; it must use the full checkout's requirements files and project-local `.venv-*` folders only. Do not silently install system SDKs, signing material, device trust settings, or privileged services.
- For web/client/server target dependencies, prefer project docs, CI, lockfiles, and `Reuse.md`; use `automind dependency-check` only as optional read-only discovery when the path is unclear, then project-native lockfile/documented commands. Do not silently install system runtimes, Docker/database services, browser drivers, private registry credentials, signing, or device trust.
- Prefer platform-native verification: project-native tests or script commands for generic projects, XCUITest for iOS, and adb/uiautomator-style evidence for Android. In skill-only mode, first suggest full install (`curl -fsSL https://raw.githubusercontent.com/leishuai/Automind/main/install-curl.sh | bash`); if installation is not allowed, run those tools directly and write the same AutoMind artifacts. In full CLI mode, use `automind` / `./automind.sh`.
- Evidence beats vibes. Always write structured results.
- Functional verification runs first: complete the selected functional batch before formal quality-check; do not interleave quality after every functional testcase.
- Crash/timeout quality failures require stack/page context before they are hard product failures. Record crash stack/backtrace, process/bundle, occurred page/screen/scene, reproduction path, and stability. Stable product-attributable crashes/timeouts should route to Generator self-repair; verifier timeouts, raw network/syslog timeouts, historical crash text, and control-plane/log-digest text should not fail completion by keyword alone.
- For App/UI/runtime TC pass claims, capture or link screenshot/visual evidence for the executed TC or distinct page/state by default. Screenshots are not sufficient proof alone; pair them with logs/state/assertions/hardMetrics. If no screenshot is possible, record `noScreenshotReason` and attach `.xcresult`, UI hierarchy/accessibility, trace, or runner summary instead.
- Functional dependency failures may fail fast with dependent cases marked `not_run` / `skipped_dependency`; independent checks may continue to collect evidence.
- Quality cases are grouped by category batch; after quality-driven runtime/product code changes, the next iteration starts from the selected/affected functional batch again, not necessarily the entire product suite.
- Probe-flow actions should be reviewable. A click is a decision: confidence, risk, fallback, and evidence all matter.
- App UI action verification is first-class: prefer Android probe-flow, iOS XCUITest/probe-flow/action-plan, and Web probe-flow/project E2E before falling back to manual confirmation.

## Demo

See `examples/offline-script-demo/` for the expected artifact shape, and `docs/references/feature-sound-effect-loop-example.md` for a concrete functional-batch + quality-category loop example. The export intentionally does not include local `.automind/tasks/` runtime folders.
""",
        encoding="utf-8",
    )


def write_readme(out_dir: pathlib.Path, copied: dict[str, list[str]], include_internal: bool = False, include_private: bool = False) -> None:
    internal_layout = "internal/                        # optional product/showcase notes\n" if include_internal else ""
    internal_note = "- Internal notes: {}\n".format(len(copied.get('internal', []))) if include_internal else ""
    private_note = "- Private summaries: {}\n".format(len(copied.get('private_summaries', []))) if include_private else ""
    if include_internal or include_private:
        public_note = "\nThis export includes private/internal material because an explicit include flag was requested. Do not publish this package without review.\n"
    else:
        public_note = "\nThis is a public-safe export: private business/project summaries, internal product drafts, and development notes are not included by default. Use explicit include flags only for private/internal review.\n"
    (out_dir / "README.md").write_text(
        f"""# AutoMind Skill Export

Generated at: {datetime.now().isoformat(timespec='seconds')}

This package is a generic AutoMind skill bundle for coding agents. It packages the stable workflow docs, prompt templates, schemas, selected summary knowledge, and a minimal artifact-shape example. The recommended setup is full AutoMind installation via `curl -fsSL https://raw.githubusercontent.com/leishuai/Automind/main/install-curl.sh | bash`: the CLI/runtime provides `automind` commands and scripts/adapters, while this skill teaches agents how to use them correctly. The public skill package itself does not package the executable AutoMind runtime.{public_note}

## What AutoMind gives a coding agent

- testable requirements;
- preflight before blaming code;
- platform-native evaluator evidence;
- structured `evaluation.json` next actions;
- durable artifact exchange through `automind-workflow-state.json`, workflow events/stage state, and `runtime-state.json`;
- checkpoint/recovery thinking;
- confidence/risk-aware probe-flow actions;
- functional-first loop semantics with CLI/project-native quality checks;
- reusable summaries.
- local reuse memory through task `summary.md`, `run-card.json`, `.automind/summary/*`, `.automind/summary/run-cards.jsonl`, and next-task `Reuse.md` in full runtime mode. Full runtime `Reuse.md` may also include local/private `summaries/accumulated/business/` lessons that exist on the machine; public exports exclude those lessons by default.

## Directory layout

```text
SKILL.md                         # agent-facing entry point
docs/                            # stable workflow/reference docs
templates/                       # Phase 2 Orchestrator / Generator / Evaluator prompt templates
requirements/                    # version-bounded optional mobile helper package specs
schemas/                         # workflow control-state, runtime-state, phase sidecar, trace/process/run-card, evaluation, probe-flow contracts
summaries/                       # selected reusable knowledge packs
examples/offline-script-demo/    # artifact-shape example, not local runtime tasks
{internal_layout}manifest.json                    # export manifest
```

## Quick start for an agent

1. Read `SKILL.md`.
2. Execute the Mandatory startup read protocol from `SKILL.md`: read `docs/workflow.md`, `docs/phase2-requirement.md`, `docs/phases/demand-definition.md`, `docs/phases/verification-execution-planning.md`, `templates/phase2_planner_prompt.md`, `docs/phase3-verification.md`, and `templates/evaluator_prompt.md` before planning/coding/validating.
3. Discover `<AUTOMIND_CLI>` by trying `automind help` from the target project root, project-local `./automind.sh help`, `$HOME/.automind/automind/automind.sh help`, then `$AUTOMIND_HOME/automind.sh help`. If none works, recommend the full install command before falling back to skill-only mode. Treat the chosen CLI path as runtime/executable location only; task artifacts still belong under the target workspace.
4. Read `docs/README.md` for the doc map.
5. Use `docs/references/test-design-guide.md` for detailed testcase/runbook examples, `docs/references/command-script-catalog.md` before choosing which AutoMind command or script adapter to run, and `docs/references/verification-flow.md` before platform/device verification.
6. For a new current-session task, use `<AUTOMIND_CLI> scaffold "<request>"` when the CLI is available; refine the generated artifacts with model judgment before implementation.
7. Follow the minimal execution protocol: Prepare -> Plan -> Build -> Verify -> Finish.
8. Before implementation, make the pre-implementation review decision explicit in `Brainstorm.md` and `runtime-state.json.planner.preImplementationReview`: `auto_proceed`, `ask_user`, or `replan`. This gate must be resolved before product/runtime code edits. Unless the user explicitly requested full-auto/no-confirmation mode (for example “一站到底”, “全自动模式”, “不用问用户”, “不用确认”, or “full auto/no confirmation”), non-trivial implementation/behavior-change tasks must ask the user once in pre-implementation. The ask must first prompt the user to review the key planning artifacts — above all `Requirements.md` and `TestCases.md`, plus `Brainstorm.md`/`Plan.md` — because a wrong requirement or test design derails the whole route and wastes all downstream development and verification. Then ask one concise decision bundle covering: requirement clarity, goal/scope/non-goals, recommended approach, key assumptions, risks, verification direction, known must-pass AC/TC/evidence, rollback/replan boundaries, and authorization for non-low-risk operations such as overwrite install, uninstall/delete/reset, account login, signing/device trust changes, privilege escalation, external upload, payment, or production-impacting actions. Also remind the user that the host coding agent's command-execution permissions may interrupt the run — for example commands outside the agent sandbox or high-risk commands may prompt the user for approval — so they can stay available to approve promptly. Include the real-device/simulator/emulator choice only when relevant.
9. Use `<AUTOMIND_CLI> workflow-check <task-code>` after planning/refining when the CLI is available. Do not Build while it has hard issues.
10. Use `templates/generator_prompt.md` for Build; Generator must write `Delivery.md`.
11. Use `<AUTOMIND_CLI> context-pack <task-code> [iteration]` before launching a model Evaluator from current-session mode.
12. Use `templates/evaluator_prompt.md` for Verify; Evaluator must write `Validation.md`, `evaluation.json`, evidence, and `nextAction`. Prefer measurable visual evidence first. When screenshots/images exist and the host model supports image understanding, use `templates/visual_review_prompt.md` as a supplementary semantic review and save the result under `logs/iter-N/ai-visual-review.json`. When the host cannot inspect images, prefer deterministic fallback (`automind visual-inspect`, screenshot diff, OCR, bounds/hierarchy, project-native snapshot/layout tests) before asking the user. If none can prove a visual claim, show captured screenshot(s) to the user with `nextAction=ask_user`; explicit user confirmation is the final fallback evidence.
13. Use `<AUTOMIND_CLI> completion-check <task-code>` before claiming Finish; detached command mode runs it automatically when `evaluation.json.nextAction=finish`.
14. Before each phase handoff, run `<AUTOMIND_CLI> phase-gate <task-code> auto`; if scripts are unavailable, follow `docs/references/skill-command-driver-checklist.md` and validate `automind-workflow-state.json`, stage state, `runtime-state.json`, `workflow.json`, and current phase sidecars.
15. Validate `workflow.json`, `automind-workflow-state.json`, stage state, `runtime-state.json`, phase sidecars (`brainstorm.json`, `requirements.json`, `testcases.json`, `plan.json`, `pre-implementation-review.json`, `delivery.json`, `completion-report.json`), observability artifacts (`trace.json`, `process-eval.json`, `run-card.json`), `evaluation.json`, and `probe-flow*.json` against `schemas/` when those artifacts are present.
16. If invoking a concrete agent runtime, follow `docs/agent-adapters.md`.
17. Record evidence and summary/reuse before claiming completion.

## Included counts

- Docs: {len(copied.get('docs', []))}
- Templates: {len(copied.get('templates', []))}
- Requirements: {len(copied.get('requirements', []))}
- Summaries: {len(copied.get('summaries', []))}
{private_note}
{internal_note}
## Note about command/script references

The catalog in `docs/references/command-script-catalog.md` names the commands and scripts behind AutoMind capabilities. The recommended user setup is full AutoMind installation, where this skill may call `automind` / `./automind.sh` and, when documented, full-checkout scripts/adapters. This public skill export intentionally does **not** bundle those executable runtime scripts or platform adapters; it is a protocol/docs/templates/schemas/examples package. If no full AutoMind CLI checkout or installed `automind` wrapper is available, first suggest the single full install command (`curl -fsSL https://raw.githubusercontent.com/leishuai/Automind/main/install-curl.sh | bash`). If installation is not allowed, follow `docs/workflow.md` manually and use the target project's own build/test/device tools to collect equivalent evidence.

Mobile/visual automation helper packages are lazy/local. The installer and this skill export do not install Android/iOS/visual helper kits by default. In full runtime mode, required low-risk Python helpers may be auto-created/repaired during preflight/evaluation via `automind setup-automation-tools android`, `automind setup-automation-tools ios`, or `automind setup-automation-tools visual`. These commands use the AutoMind runtime `requirements/*.txt`, create local Python virtualenvs in the target workspace, retry transient network/DNS package-index failures once with explicit logs, and must not install system SDKs, signing material, OCR engines, browser drivers, device trust settings, or privileged services.

Web/client/server project dependency setup remains project-native. Use `automind dependency-check [task-code] [iteration]` only as an optional read-only planner/preflight report when dependency setup is unclear, then run the repository's lockfile/documented commands when required by TestCases/Plan and record evidence.

## Product idea in one line

AutoMind turns AI coding from "write code and hope" into a recoverable, evidence-driven engineering loop.
""",
        encoding="utf-8",
    )


def write_demo(out_dir: pathlib.Path) -> list[str]:
    """Write a tiny artifact-shape example without exporting local runtime tasks."""
    demo = out_dir / "examples" / "offline-script-demo"
    demo.mkdir(parents=True, exist_ok=True)
    (demo / "README.md").write_text(
        """# Offline Script Demo Artifact Shape

This exported example shows the expected AutoMind task artifact shape without bundling any local `.automind/tasks/` runtime folders.

In a real AutoMind checkout, create fresh local runtime evidence with:

```bash
./automind.sh smoke offline-demo
```

That command writes a task under `.automind/tasks/offline_demo_smoke/` in the user's own workspace. Those generated task folders are local evidence and are intentionally not part of the public skill export.

## Expected task files

```text
.automind/tasks/<task>/
  Requirements.md
  Plan.md
  Delivery.md
  Validation.md
  evaluation.json
  automind-workflow-state.json
  automind-workflow-events.jsonl
  stages/*-stage-state.json
  runtime-state.json.stateSummary  # migration fallback
  runtime-state.json
  summary.md
  Report.html
  logs/iter-1/
    commands.md
    evaluator.log
```

## Minimal `evaluation.json` shape

```json
{
  "iteration": 1,
  "result": "pass",
  "summary": "Verification passed with evidence",
  "failedChecks": [],
  "evidence": [
    {"type": "command", "path": "logs/iter-1/commands.md"},
    {"type": "log", "path": "logs/iter-1/evaluator.log"}
  ],
  "nextAction": "finish"
}
```
""",
        encoding="utf-8",
    )
    (demo / "artifact-shape.md").write_text(
        """# AutoMind Artifact Shape

AutoMind task runtime folders are created locally while the user works. The public skill export only documents the shape:

- `Requirements.md` — requirement units `Rxx` with inline acceptance criteria `AC-xxx` .
- `Plan.md` — current plan.
- `Delivery.md` — what changed.
- `Validation.md` — human-readable validation history.
- `evaluation.json` — latest structured evaluator result.
- `automind-workflow-state.json` — current workflow control state; `runtime-state.json` remains runtime/resume state and `stateSummary` is migration fallback.
- `summary.md` — reusable task conclusion.
- `Report.html` — human handoff report with per-TC evidence/screenshots/logs and summary/knowledge deposition.
- `logs/iter-N/` — command/output evidence.

Do not copy another user's `.automind/tasks/` into a new project as product material. Generate fresh task evidence in the target workspace.
""",
        encoding="utf-8",
    )
    return [str(p.relative_to(out_dir)) for p in demo.rglob("*") if p.is_file()]


def find_project_root(start: pathlib.Path) -> pathlib.Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if any((candidate / marker).exists() for marker in (".git", "AGENTS.md", "package.json", "pyproject.toml", ".claude")):
            return candidate
    return current


def copy_tree_clean(src: pathlib.Path, dst: pathlib.Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def looks_like_legacy_automind_skill(path: pathlib.Path) -> bool:
    """Return true only for old AutoMind-generated skill folders safe to remove.

    This avoids deleting a user's unrelated folder named `automind`.
    """
    skill_md = path / "SKILL.md"
    if not skill_md.exists():
        return False
    try:
        text = skill_md.read_text(errors="ignore")
    except Exception:
        return False
    return "name: automind" in text and "AutoMind is a harness-loop skill" in text


def legacy_skill_target_for(target: pathlib.Path) -> pathlib.Path | None:
    if target.name == "automind":
        return None
    return target.parent / "automind"


def agent_skill_targets(agent: str, install_name: str) -> list[tuple[str, pathlib.Path]]:
    return skill_targets(agent, install_name)


def agent_skill_target_entries(agent: str, install_name: str) -> list[dict[str, object]]:
    return skill_target_entries(agent, install_name)


def install_skill(out_dir: pathlib.Path, agent: str, install_name: str) -> dict:
    if agent == "none":
        return {"agent": "none", "installed": False}

    entries = agent_skill_target_entries(agent, install_name)
    targets = [(str(entry["kind"]), entry["path"]) for entry in entries if entry.get("available")]
    skipped = [
        {"kind": str(entry["kind"]), "path": display_path(entry["path"]), "reason": str(entry.get("reason") or "not detected")}
        for entry in entries
        if not entry.get("available")
    ]

    if not targets:
        return {
            "agent": agent,
            "installed": False,
            "reason": "No supported user-level agent skill folder detected. Export only.",
            "skipped": skipped,
        }

    installed = []
    removed_legacy = []
    selected = targets
    for kind, target in selected:
        target.parent.mkdir(parents=True, exist_ok=True)
        legacy_target = legacy_skill_target_for(target)
        if legacy_target and legacy_target.exists() and looks_like_legacy_automind_skill(legacy_target):
            shutil.rmtree(legacy_target)
            removed_legacy.append({"kind": kind, "path": display_path(legacy_target)})
        copy_tree_clean(out_dir, target)
        installed.append({"kind": kind, "path": display_path(target)})

    result = {"agent": agent, "installed": True, "targets": installed}
    if skipped:
        result["skipped"] = skipped
    if removed_legacy:
        result["removedLegacyTargets"] = removed_legacy
    return result


def write_json(path: pathlib.Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", nargs="?", default=str(pathlib.Path.home() / "Downloads" / "automind-skill"))
    parser.add_argument("--clean", action="store_true", default=True, help="Remove existing output directory first (default true)")
    parser.add_argument("--include-internal", action="store_true", help="Include internal product/showcase notes. Off by default for public-safe exports.")
    parser.add_argument("--include-private-summaries", action="store_true", help="Include private/project-specific summary packs. Off by default; do not use for public exports.")
    parser.add_argument("--no-internal", action="store_true", help="Deprecated compatibility flag; internal notes are excluded by default.")
    parser.add_argument("--install", choices=["none", "all", "auto", "claude", "codex", "trae", "trae-cn"], default="none", help="Optionally install the exported skill to detected user-level agent skill folders. Recommended: auto. all is kept as a compatibility alias and does not create missing agent roots. Default: none.")
    parser.add_argument("--install-name", default="automind-skill", help="Folder name to use when installing into an agent skill directory.")
    args = parser.parse_args()

    out_dir = pathlib.Path(args.out_dir).expanduser().resolve()
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    write_skill_md(out_dir)
    copied_docs = copy_many(DOCS, out_dir)
    copied_requirements = copy_many(REQUIREMENTS, out_dir)
    copied_templates = copy_many(TEMPLATES, out_dir)
    copied_scripts: list[str] = []
    copied_examples = copy_many(EXAMPLE_FILES, out_dir)
    copied_summaries = copy_many(SUMMARY_FILES, out_dir)
    include_private = bool(args.include_private_summaries)
    private_summary_files = []
    if include_private:
        for pattern in PRIVATE_SUMMARY_PATTERNS:
            private_summary_files.extend(str(path.relative_to(ROOT)) for path in ROOT.glob(pattern) if path.is_file())
    copied_private_summaries = copy_many(sorted(set(private_summary_files)), out_dir) if include_private else []
    include_internal = bool(args.include_internal and not args.no_internal)
    copied_internal = copy_many(INTERNAL_OPTIONAL, out_dir) if include_internal else []
    demo_files = write_demo(out_dir)

    copied = {
        "docs": copied_docs,
        "templates": copied_templates,
        "requirements": copied_requirements,
        "scripts": copied_scripts,
        "summaries": copied_summaries,
        "private_summaries": copied_private_summaries,
        "internal": copied_internal,
        "examples": copied_examples + demo_files,
    }
    write_readme(out_dir, copied, include_internal=include_internal, include_private=include_private)
    manifest = {
        "name": "automind-skill",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "copied": copied,
        "includeInternal": include_internal,
        "includePrivateSummaries": include_private,
        "entrypoints": ["SKILL.md", "docs/workflow.md", "docs/references/command-script-catalog.md", "docs/agent-adapters.md"],
    }

    install_result = install_skill(out_dir, args.install, args.install_name)
    manifest["install"] = install_result
    write_json(out_dir / "manifest.json", manifest)
    print(json.dumps({
        "result": "pass",
        "outDir": str(out_dir),
        "files": sum(len(v) for v in copied.values()) + 3,
        "install": install_result,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
