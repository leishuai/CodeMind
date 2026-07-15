# AGENTS.md - CodeMind

CodeMind is a recoverable, evidence-driven harness loop for coding agents.

Use CodeMind when a task needs more than a simple code edit:

- explicit requirements or acceptance criteria;
- preflight before blaming code;
- build/test/device verification evidence;
- retry, replan, ask-user, or finish decisions;
- reusable summaries after completion.


## Canonical CodeMind flow

```text
User request
  -> Brainstorm.md
  -> Spec.md
  -> Require.md
  -> TestCases.md
  -> Plan.md
  -> workflow-check
  -> Generator
  -> Delivery.md
  -> Evaluator
  -> Validation.md + evaluation.json
  -> completion-check
```

Minimal execution protocol:

```text
Prepare -> Plan -> Build -> Verify -> Finish
```

- Prepare: discover CodeMind CLI/runtime, run from the target project root, and verify `TASK_DIR` is under the target workspace.
- Plan: research only the implementation surface, verification surface, and risks needed to refine `Brainstorm.md`, `Spec.md`, `Require.md`, `TestCases.md`, and `Plan.md`.
- Build: Generator implements or repairs product/runtime code and writes `Delivery.md`.
- Verify: Evaluator or deterministic verifier writes `Validation.md`, `evaluation.json`, evidence logs, and verification checklist updates.
- Finish: `completion-check` must pass before completion; then prefer AI-refined summary/reuse (`summary --ai <agent>` or `summary-refine`) with deterministic fallback, and run `record-check`.

The three hard gates are `workflow-check` before Build, `Delivery.md` before final Verify, and `completion-check` before Finish. Do not replace these gates with chat confidence.

If `workflow-check` has issues, refine the current phase artifacts before moving on.
Plan-phase artifact boundaries (single-source-of-truth, enforced by `workflow-check`): `Brainstorm.md` owns idea expansion, option comparison, recommendation, DecisionBundle, assumptions/questions, and the pre-implementation review decision; it MUST NOT declare stable `Rxx` or `AC-xxx` IDs. `Spec.md` owns scope/non-goals and stable `Rxx` requirement units only; it MUST NOT declare `AC-xxx` (Require's responsibility) or `TC-*` testcases (TestCases' responsibility). `Require.md` owns the `AC-xxx` acceptance criteria mapped from `Rxx`. `TestCases.md` owns `TC-*` runbooks. Refiner only iterates the artifact whose responsibility actually changed. New tasks use single-file `Requirements.md` (Rxx with inline AC-xxx). Legacy `Spec.md + Require.md` is still accepted for older tasks; workflow-check auto-detects and validates either form.
Before editing product/runtime code, `Brainstorm.md` and `runtime-state.json.planner.preImplementationReview` must explicitly say whether CodeMind can `auto_proceed`, must `ask_user`, or should `replan`. This is a mandatory gate. Unless the user explicitly requested full-auto/no-confirmation mode (for example “一站到底”, “全自动模式”, “不用问用户”, “不用确认”, or “full auto/no confirmation”), non-trivial implementation/behavior-change tasks must ask the user once in pre-implementation. The one-shot ask_user bundle must confirm whether the requirement is clear, goal/scope/non-goals, key assumptions, recommended approach, known risks, verification direction, known must-pass `AC-*`, required `TC-*`, evidence expectations, rollback/replan boundaries, and authorization for non-low-risk operations such as overwrite install, uninstall/delete/reset, account login, signing/device-trust changes, privilege escalation, external upload, payment, or production-impacting actions. Bias hard toward asking everything in this single bundle so the loop is not interrupted again later: fold every foreseeable hard/preflight question into this one ask and prefer over-including a question here over re-asking mid-run. When verification will run on a real device, this bundle must also pre-warn the user that they may need to physically operate the phone during verification — typically trust the developer app/profile on the device (Settings -> General -> VPN & Device Management), keep it unlocked with the screen on, enable Developer Mode, and approve the USB debugging/trust prompt — so they prepare up front instead of being blocked mid-run; do not split these into separate later `ask_user`s. When the task requires reproducing a specified UI design (Figma / 设计稿 / 视觉还原 / pixel-level / UI consistency signals, or a Figma link already in the requirement) and no usable local design image exists yet, fold a design-reference request into the same bundle: ask the user for the Figma link or to export the design to a local image under `.automind/tasks/<task>/design/` so it can serve as the `visual-inspect --baseline` reference; if the user declines or none can be obtained, do not block — degrade the UI case to structure/text/element-order assertions plus final human screenshot confirmation and record the reason. Do not ask this for non-UI backend/script tasks. Only client/app development or verification tasks need the real-device vs simulator/emulator decision as one part of that bundle. For Android/iOS/mobile client tasks, follow the user's stated verification target as the source of truth — if the request already names real device, simulator/emulator, or both, CodeMind respects that choice and does not re-ask just for device choice. Auto-proceed is for verification-only, documentation-only, mechanical/low-risk edits, already-approved directions, or explicit full-auto/no-confirmation automation. After the gate is resolved, continue automatically through Generator implement/repair -> Evaluator verify/re-verify -> `completion-check` until finish is proven or a real stop condition occurs.
Generator owns product/runtime-code implementation and repair. Evaluator owns verification, failure classification, evidence, `Validation.md`, and `evaluation.json`; it routes product failures to `retry_generator` rather than repairing product code itself. Evaluator may only self-repair verifier/probe-flow/test-harness issues when the validation method is wrong. Every Evaluator-modified file must be declared in `evaluation.json.evaluatorChanges[]` with one of `verifier_self_repair`, `probe_flow_repair`, `test_harness_fix`, `evidence_only`. The completion gate (`apply_completion_gate`) checks the path/category whitelist (`orchestrator/completion.py:validate_evaluator_boundary`) and forces `nextAction=retry_generator` whenever an Evaluator change touches product/runtime code, even if `result=pass`. `nextAction=ask_user` is a hard interrupt of the autonomous full_auto loop and is only allowed when `askUserQuestion.category` is one of `unauthorized_destructive_or_sensitive`, `system_or_external_dependency`, `real_device_or_signing`, `manual_visual_confirmation`, or `repeated_same_failure`; safe no-loss runtime recovery such as `agent_unavailable` / `agent_timeout` -> `resume_after_recovery` must auto-retry/resume without `ask_user`. The completion gate (`validate_ask_user_category`) rewrites any other ask_user back to `retry_generator` so the loop continues without paying the human-interrupt cost. CodeMind biases toward automation: when the Evaluator declares `askUserQuestion.riskTier`, that self-assessment is the primary judge — `safe_self_service` (long build/compile, minimal reversible verification-unblock patch, in-app device driving, retryable env tweak) is trusted and rewritten back to `retry_generator`, while `sensitive_hard_gate` (irreversible/destructive, account/credential/keychain login, payment, or a real device/permission gate) is trusted to pause. Only a strong irreversible/account-security signal that contradicts a `safe_self_service` label re-engages the pause (anti self-contradiction safety net; `production` and signing/certificate/keychain-for-signing alone are deliberately NOT such signals — CodeMind may re-sign with the user's own certificates / automatic signing). When `riskTier` is absent, the gate falls back to conservative keyword heuristics so older Evaluator output stays safe. Whether to pause is ultimately the user's decision (拦不拦截以用户诉求为准), which authoritatively overrides the risk model: when the user explicitly chose full-auto/no-confirmation mode (`preImplementationReview.fullAuto=true`) the gate never pauses, and when the Evaluator sets `askUserQuestion.userAuthorized=true` for an action already covered by the pre-implementation `decisionBundle.destructiveActionsAllowList`, the gate trusts that consent and rewrites the ask_user back to `retry_generator`. This only releases what the user already authorized; it never auto-approves an un-authorized sensitive action.
If verification is blocked by unrelated build/test/workspace issues, CodeMind may create minimal reversible verification unblock changes only after checkpointing or recording a diff. These changes must be listed in `Delivery.md`/`Validation.md` and `evaluation.json.verificationUnblockChanges`, then restored or explicitly promoted before finish; active temporary unblock changes block completion.
Before claiming finish, `completion-check` must prove required `TC-*` cases passed with executed evidence, required `AC-xxx` criteria are covered, and evidence paths exist. Environment/device/tool blocker classification must never be treated as a passed required testcase. Required App/UI/client-facing runtime cases need hard evidence that the product actually launched/ran and the specified actions/assertions executed, plus `evidenceAssessment.verdict=proved`. Required clean-build/release/merge build cases need attached project-native build evidence plus `evidenceAssessment.verdict=proved`; a classified build blocker cannot satisfy that gate. Any required pass row that uses `evidenceAssessment.verdict=proved` must additionally be backed by a non-empty `hardMetrics` array (machine-checkable: exit_code, build_succeeded, tests_passed, log_keyword_matched, screenshot_hash_matched, etc.) or by an independent `secondaryAssessment` whose `assessor` differs from the primary `assessor`; rows lacking both anchors block finish even when the model claims pass. `manual_confirmed` rows backed by recorded `humanConfirmation` are exempt.
Use `Plan.md` checklists as the short-term progress tracker: Generator updates `Implementation Checklist` (`T*` rows), Evaluator updates `Verification Checklist` (`TC-*` rows), and `automind status` summarizes them. Do not rely on chat memory for progress.
At task start and before choosing build/test/verification commands, read `Reuse.md`. Prefer matching `Successful path:` entries and avoid same-condition `Avoid path:` entries; if a reusable path is ignored, state why in `Plan.md` or `TestCases.md`.
Reuse acknowledgement is a hard gate, not a verbal claim. Before each Generator/Evaluator turn (including every retry/re-verify iteration) the agent must read the matched `phase-reuse/<phase>.md` and high-confidence reuse, then record `automind reuse-ack <task-code> <phase> --read --applied "<paths used>" --ignored "<paths skipped and why>"`. `workflow-check` blocks entry to the gated phase until `runtime-state.json.reuseGate.<phase>.acknowledged=true` with `phaseReuseRead=true`. For repeated-failure / signing / device / build categories, the loop must first exhaust matched safe reuse paths (reuse signed app when business code unchanged, `devicectl install`/`launch`, avoid `idevicescreenshot`, avoid unnecessary full build, classify the issue) before `ask_user`; `workflow-check` fails an `ask_user` escalation that skipped available safe reuse paths. Only steps that genuinely need sensitive actions (login, keychain, certificate/profile change) may go to `ask_user`.

## How agents should use CodeMind

### If working in this repository

Use command mode:

```bash
./automind.sh help
./automind.sh smoke offline-demo
./automind.sh setup-automation-tools android   # optional; preflight can auto-run when required
./automind.sh setup-automation-tools ios       # optional; preflight can auto-run when required
./automind.sh setup-automation-tools visual    # optional; visual-inspect can auto-run when required
./automind.sh scaffold "<task>"                # current-session skill/slash-command mode
./automind.sh ask "<task>" <codex|claude|trae>
./automind.sh resume <task-code> <codex|claude|trae>
./automind.sh status <task-code>
./automind.sh plan <task-code> <codex|claude|trae>
./automind.sh workflow-check <task-code>
./automind.sh context-pack <task-code> [iteration]
./automind.sh completion-check <task-code>
./automind.sh summary <task-code> --ai codex   # prefer AI refinement when an agent is available; falls back to deterministic summary
./automind.sh summary <task-code>              # deterministic fallback
./automind.sh export-skill --install auto
./automind.sh export-command --install all
./automind.sh export-skill --install auto
./automind.sh export-command --install auto
./automind.sh export-skill --install claude
./automind.sh export-skill --install codex
./automind.sh export-skill --install trae
```

### If using CodeMind as a skill

Mandatory startup read protocol: this is not a reference-only list. At the start
of every CodeMind task, before planning/coding/validating, read and apply:

1. `SKILL.md` in the exported skill package, then `docs/workflow.md` (canonical
   loop, gate definitions, evaluation contract).
2. `docs/README.md` — single index for every other doc; load deeper material
   on demand by phase: Phase 2 (`docs/phase2-requirement.md` +
   `templates/phase2_planner_prompt.md`, with `docs/references/test-design-guide.md`
   for runbook examples), Phase 3 (`docs/phase3-verification.md` +
   `templates/evaluator_prompt.md`), command/script choice
   (`docs/references/command-script-catalog.md`), platform/device verification
   (`docs/references/verification-flow.md`), Generator work
   (`templates/generator_prompt.md`), and adapter integration
   (`docs/agent-adapters.md`).
3. The active task's `Reuse.md` (if present) for prior successful/avoid paths.

Do not preload all eight legacy entries up front; the Phase docs and index in
`docs/README.md` are the source of truth for which extra files apply to the
current step.

Slash-command note: `/codemind <request>` is the canonical current-session end-to-end flow for Codex/Claude/Trae/Trae-CN; `/codemind --detached <request>` (or the equivalent `./automind.sh ask ... <agent>`) is the detached-CLI variant that launches a separate agent CLI process/session. `/automind` remains a compatibility alias. Treat current-session forms as structured natural language that tells the current model to use the CodeMind skill/protocol in this conversation and continue Evaluator verify -> Generator repair -> Evaluator re-verify until `completion-check` passes, `ask_user` needs a human decision, or an explicit unsafe/non-recoverable stop condition occurs. They must use the current host agent as Planner/Generator and CLI helpers such as `scaffold`, `workflow-check`, `context-pack`, and `completion-check`. Use the detached form only when explicitly requested.

Result exchange note: current sessions, native isolated subagents, deterministic verifiers, and detached CLI processes must integrate through `.automind/tasks/<task>/` artifacts (`automind-workflow-state.json`, `automind-workflow-events.jsonl`, stage state files, `runtime-state.json`, `evaluation.json`, `Validation.md`, `Delivery.md`, `VerificationLedger.json`, and `logs/iter-N/*`), not hidden chat memory. When `evaluatorContext` is recorded, `workflow-check` validates that it has `inheritsGeneratorContext=false`, a valid context pack JSON, and passing context-pack validation.

Workspace note: CodeMind runtime and user workspace are separate. Run CodeMind CLI helpers from the target project root so `.automind/tasks` belongs to that project. If the shell cwd is not the target project root, set `AUTOMIND_WORKSPACE_ROOT=/path/to/project` before invoking `automind`; do not accidentally create task folders under the installed CodeMind checkout.

## Rules

- Evidence beats vibes: run the relevant verification before claiming completion.
- Required functional `TestCases.md` rows must be concrete runbooks. For App/UI work they must state preparation/preflight, build/install/deploy/start, launch/open, entry page/screen/route/activity/state, action sequence, assertions, and evidence; do not static-pass a vague "verify UI works" case. For release/merge confidence, include a required clean-build case and require attached build evidence plus positive Evaluator/model `evidenceAssessment`; do not count environment/build blocker classification as pass.
- CodeMind can operate real apps for verification when the right platform runner is available. For Android, use `android-preflight` + `android-probe-flow` with generated `probe-flow.android.json` for tap/click/input/swipe/optional popup handling/assertions. For iOS, use XCUITest directly or materialize `probe-flow.ios.json` / `action-plan.ios.json` into Swift and run it with `ios-xcuitest` or a project/native runner. For Web, use `web-probe-flow` with project-native E2E commands. Safe close/skip/later/dismiss overlays may be auto-unblocked with evidence; privacy/terms Agree/Allow/Continue, login/account, permission grants, payment, delete/reset, upload, signing/device trust, or ambiguous consent require explicit authorization or `ask_user`. Do not tell users CodeMind cannot click or navigate an app; instead encode the action, selector/confidence/risk, authorization, post-action assertion, and evidence, or route to `ask_user`/`replan` for missing device/signing/UI Automation/selectors or sensitive actions.
- Do not treat environment/device/signing failures as code failures.
- Do not silently perform destructive or sensitive actions.
- Low-risk Android/iOS Python helper setup may auto-run into project-local `.venv-*` folders from `requirements/*.txt` when required by verification. Do not silently install mobile SDKs, signing material, device trust settings, or privileged services.
- Low-risk visual helper setup may auto-run into project-local `.venv-visual-tools` from `requirements/visual-tools.txt` when deterministic screenshot/image inspection is required. Prefer measurable visual evidence first. Use visual helpers as the default fallback when the visual claim can be reduced to dimensions, crop/bbox, hash/diff, baseline comparison, OCR, or measurable geometry. Treat AI Visual Review as an add-on for semantic gaps that pure technical measurement cannot close. If measurable evidence and AI Visual Review still cannot prove correctness, capture screenshot(s) and ask the user to confirm as the final fallback. Do not pass semantic visual claims from screenshot paths alone; explicit user confirmation must be recorded before finish, otherwise use blocked/replan.
- For web/client/server target projects, use project-native dependency setup and lockfiles. When dependency setup is unclear, use `automind dependency-check [task-code] [iteration]` as an optional read-only discovery aid, then commands such as `npm ci`, `pnpm install --frozen-lockfile`, `yarn install --immutable`, `uv sync --frozen`, `poetry install --sync`, Gradle/Maven wrappers, or repo-documented commands. Do not silently install or change system SDKs, Docker/database services, browser drivers, private registry credentials, language runtimes, signing, or device trust.
- Keep local runtime data under `.automind/tasks/`; do not turn task logs into product docs.
- Summaries are local memory: terminal/paused tasks should have `summary.md`, `.automind/summary/lessons-learned.md`, and `.automind/summary/local-reuse-index.md`; new tasks read those into `Reuse.md`. Preserve known successful build/test/verification paths and known failed/deprecated paths with preconditions and evidence.
- Promote broadly reusable lessons into `summaries/` or `docs/`, then old task folders may be cleaned.
