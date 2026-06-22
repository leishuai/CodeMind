# AutoMind Command and Script Catalog

This is the canonical map from **agent/user intent** to AutoMind commands and the scripts behind them.

Use this file when you need to answer:

- Which AutoMind command should I run for this task?
- Which `scripts/*.py` file implements the command?
- Is a script meant to be called directly, or only through `./automind.sh` / `automind`?
- Which phase writes the evidence and `evaluation.json`?

---

## 0. Invocation rule for coding agents

Prefer the AutoMind CLI wrapper. Direct script invocation is an implementation detail unless this catalog explicitly says it is a direct adapter.

Use this placeholder in instructions:

```text
<AUTOMIND_CLI>
```

Resolve it in this order:

1. Installed wrapper on `PATH`: `automind`.
2. Project-local/vendored AutoMind checkout: `./automind.sh` only when the target project contains AutoMind.
3. Default full install path: `$HOME/.automind/automind/automind.sh`.
4. Explicit home override: `$AUTOMIND_HOME/automind.sh`.
5. Last resort in repo internals: `python3 orchestrator/main.py ...` or `python3 scripts/<adapter>.py ...`

Examples in this repository usually show `./automind.sh`. Installed users can replace it with `automind`. Always run AutoMind commands from the target project root; otherwise set `AUTOMIND_WORKSPACE_ROOT=/path/to/project` so `.automind/tasks` is created in the project, not in the AutoMind runtime checkout.

If you are reading this inside an exported skill-only package, executable runtime scripts and platform adapters are not bundled. The recommended setup is full AutoMind installation; then the skill may use the installed CLI and documented full-checkout scripts/adapters. Prefer the CLI wrapper first. Direct script use is allowed only when this catalog marks it as an adapter/debug/direct path. If no CLI is available, first suggest the single full install command (`curl -fsSL https://raw.githubusercontent.com/leishuai/Automind/main/install-curl.sh | bash`). If installation is not allowed, follow `docs/workflow.md` manually and use the target project's own build/test tools for evidence.

For slash-command integrations, treat `/automind <request>` as current-session natural language that invokes the AutoMind skill/protocol. It should normally map to helper/gate commands such as `scaffold`, `workflow-check`, `context-pack`, and `completion-check`, not to detached `ask`. In Skill/slash-command mode, JSON outputs are part of the command contract: `workflow-check` refreshes sidecars and `workflow.json`; `evaluation.json.nextAction`, `completion-report.json`, and `VerificationLedger.json` drive the next step.

Detached commands and current-session commands exchange results through `.automind/tasks/<task>/` artifacts. `automind-workflow-state.json`, `automind-workflow-events.jsonl`, stage state files, `evaluation.json`, `Validation.md`, `Delivery.md`, `VerificationLedger.json`, and `logs/iter-N/*` are the common contract.

---

## 1. How to choose a command

| User / agent need | Recommended command | Behind the command | Phase | Main evidence/output | Notes |
|---|---|---|---|---|---|
| Check local AutoMind basics | `<AUTOMIND_CLI> init` | `automind.sh` | Setup | terminal output, `.automind/` dirs | Does not install mobile SDKs. It only checks optional tools and creates local AutoMind directories. |
| Create task for current-session skill/slash-command flow | `<AUTOMIND_CLI> scaffold "<request>"` | `orchestrator/main.py` | Phase 1 → 2 | `.automind/tasks/<task>/` | Creates deterministic starter artifacts only; the current host agent refines Brainstorm/Requirements/TestCases/Plan and acts as Generator. Does not launch another agent process. |
| Create task and start AutoMind-owned CLI/TUI loop | `<AUTOMIND_CLI> ask "<request>" [auto\|codex\|claude\|trae] [--tui\|--detached]` | `orchestrator/main.py` | Phase 1 → 3 | `.automind/tasks/<task>/` | Starts AutoMind-owned loop. In an interactive terminal it uses the TUI-owned wrapper by default; new TUI tasks record `agentExecutionPolicy=default_bypass` without asking. Non-interactive scripts keep detached/plain behavior; if the task has no policy, Planner/Generator fall back to bypass. Planner/Generator reuse a task-local primary CLI session when the execution mode matches the task policy/fallback; model Evaluator still launches fresh isolated context and always uses the selected agent's bypass/high-automation mode. |
| Refine planning only | `<AUTOMIND_CLI> plan <task-code> [agent]` | `orchestrator/main.py`, `templates/phase2_planner_prompt.md` | Phase 2 | `Brainstorm.md`, `Requirements.md`, `TestCases.md`, `Plan.md` | Use when requirements/tests need model reasoning before implementation. |
| Resume existing loop | `<AUTOMIND_CLI> resume <task-code> [agent] [--tui\|--detached]` | `orchestrator/main.py` | Phase 3 | next iteration artifacts; `runtime-state.json.resumeRecovery` when an interruption is detected | Reads `automind-workflow-state.json`, `stages/*-stage-state.json`, `evaluation.json` compatibility artifact, obsolete `runtime-state.json.stateSummary` fallback, and `logs/iter-N/*` to recover at a safe phase boundary. In a TTY it uses TUI-owned resume by default; scripts keep plain behavior. It can rerun interrupted Generator/Evaluator iterations and can resume `agent_unavailable` / `agent_timeout` / `agent_stalled_no_output` failures after the external runtime issue is fixed. Resume follows the existing task-level `agentExecutionPolicy` and does not ask for a new bypass grant. Model Evaluator reruns remain fresh-isolated and bypassed. It does not auto-resume unsafe blocked states. |
| Inspect task and get next step | `<AUTOMIND_CLI> status <task-code>` / `list` / `logs [task-code]` | `orchestrator/main.py` | Any | task status/log output + next recommended action | `status` is self-explaining for current-session agents; it prints suggested commands, files to inspect, and gate summaries. `logs` tails the latest generator log when present. |
| Gate current-session phase handoff | `<AUTOMIND_CLI> phase-gate <task-code> [auto\|plan\|build\|verify\|finish]` | `orchestrator/commands/session.py`, `orchestrator/phase_transition.py` | Any / skill mode | JSON handoff decision with `checklist[]`, `checkboxMarkdown[]`, `phaseReuseRefresh`, and `requiredCommand` when blocked | Preferred skill/slash-command state check before major actions. It refreshes/seeds `automind-workflow-state.json`; when entering `delivery` or `evaluation`, it deterministically refreshes missing/stale `phase-reuse/generator.md` or `phase-reuse/evaluator.md` without resetting fresh acknowledged reuse. |
| Create isolated Evaluator context pack | `<AUTOMIND_CLI> context-pack <task-code> [iteration]` | `orchestrator/main.py` | Before model Evaluator | `logs/iter-N/evaluator-context.md/json` | Use in current-session mode before launching a fresh isolated model Evaluator. Deterministic evaluators may not need this. |
| Generate final reusable summary | `<AUTOMIND_CLI> summary <task-code> --ai <agent>` preferred; `<AUTOMIND_CLI> summary <task-code>` fallback | `orchestrator/main.py` | Phase 4 | `summary.md`, `.automind/summary/lessons-learned.md`, `.automind/summary/local-reuse-index.md` | AI mode first builds a deterministic seed and falls back if agent output is invalid/unavailable. Command mode may auto-generate deterministic summary at terminal/paused loop states. |
| Run explicit AI Summary Refiner | `<AUTOMIND_CLI> summary-refine <task-code> <agent>` | `orchestrator/main.py`, `templates/summary_refiner_prompt.md` | Phase 4 | `logs/summary-refiner/*`, `summary.md`, `.automind/summary/*` | Equivalent to `summary --ai <agent>`; use before final `record-check` when an agent is available. |
| Generate human-readable HTML report | `<AUTOMIND_CLI> report <task-code>` | `orchestrator/reports.py` | `.automind/tasks/<task-code>/Report.html` | One-page report for human review across success/failure/pause: title `<task> Automind Report`, requirements/AC, generated artifacts, Test Results with a per-TC `Key Evidence` summary (screenshots, machine anchors, hardMetrics, key files) plus a complete artifact details column, screenshot gallery, failed checks, quality checks, summary/knowledge deposition, and a raw artifact appendix. Also refreshed when report manifests are printed at handoff points. |
| Check UI automation evidence completeness | `<AUTOMIND_CLI> ui-evidence-check <task-code> [iteration] [--json]` | `scripts/ui_evidence_check.py` | `logs/iter-N/ui-evidence-check.json`, `Validation.md` | Lightweight gate that checks `evaluation.json`, `Validation.md`, action trace, Android screenshots/hierarchy, iOS `.xcresult`/xcodebuild logs, and Web E2E evidence references. It does not decide product correctness; it checks whether evidence is auditable. |
| Run AI Visual Review from screenshots | host-agent image understanding with `templates/visual_review_prompt.md` | `templates/visual_review_prompt.md` | Phase 3 optional visual/UX review | `logs/iter-N/ai-visual-review.json`, `Validation.md`, optional `evaluation.json.qualityChecks[]` | Use after real screenshot/image evidence is captured. It supplements deterministic UI tests, hierarchy/bounds assertions, and screenshot diff; it does not replace them when required. |
| Check workflow continuity | `<AUTOMIND_CLI> workflow-check <task-code>` | `orchestrator/main.py` | Phase 2/3 gate | refreshed `workflow.json` + JSON report + PASS/FAIL exit code | Materializes/validates the derived `workflow.json` executable contract and checks `Rxx/AC-xxx -> TC-* -> Plan -> workflow.json -> evaluation` continuity; use after planning/refining and before Build/final verification. |
| Check final completion coverage | `<AUTOMIND_CLI> completion-check <task-code>` | `orchestrator/main.py` | Final gate | `VerificationLedger.json`, enriched `evaluation.json`, PASS/FAIL exit code | Checks required `TC-*` pass results, required `AC-xxx` coverage, and evidence paths. Command mode runs this automatically before accepting `finish`. |
| Check record completeness | `<AUTOMIND_CLI> record-check <task-code>` | `orchestrator/main.py` | Phase 4 / release | PASS/FAIL exit code | Fails non-zero when required reusable records/logs are incomplete. |
| Show reusable local lessons | `<AUTOMIND_CLI> reuse [limit]` | `orchestrator/main.py` | Any | reuse index output | New tasks also receive `.automind/tasks/<task>/Reuse.md` generated from this index. |
| Create/list restoration checkpoints | `<AUTOMIND_CLI> checkpoint ...` | `scripts/checkpoint.py` | Any | checkpoint metadata | Use before verification unblock changes or risky strategy shifts. Planning aid only; no silent destructive restore. |
| Generic project verification command | `<AUTOMIND_CLI> script-command <task-code> [iteration]` | `orchestrator/main.py` | Phase 3 Evaluator | `Validation.md`, `evaluation.json`, `logs/iter-N/commands.md` | Use only when an explicit `scriptCommand` / `verifyCommand` exists in runtime state or Phase 2 artifacts. It wraps a known project-local test/build/runbook command; platform/runtime UI proof should use the platform Evaluator instead. |
| Read-only project dependency plan | `<AUTOMIND_CLI> dependency-check [task-code] [iteration]` | `orchestrator/main.py` | Plan / Phase 3 preflight | `logs/iter-N/dependency-check.json` or `.automind/setup/dependency-check/*/dependency-check.json` | Optional read-only aid when project docs/CI/lockfiles/Reuse.md do not make dependency setup clear, or when dependency/tooling failure needs classification. It does not install target project dependencies and is not a gate. |
| Lightweight quality pass | `<AUTOMIND_CLI> quality-check <task-code> [iteration] --merge` | `scripts/quality_evaluator.py` | Phase 3 after functional pass | `logs/iter-N/quality-summary.json`, merged `evaluation.json` | Rule/script based; use semantic quality review only for ambiguous/high-risk cases. |
| Install/repair project-local helper packages | `<AUTOMIND_CLI> setup-automation-tools [android\|ios\|visual\|all]` | `orchestrator/main.py`, `requirements/*.txt` | Lazy setup / preflight auto-repair | `.automind/setup/automation-tools/*/setup-report.json`, `.venv-android-tools` / `.venv-ios-tools` / `.venv-visual-tools` | Low-risk Python helper setup may auto-run when required. `setup-automation-tools ... --help` is help-only and must not create venvs or install packages. Visual tools provide deterministic screenshot size/hash/diff fallback. Does not install Android/iOS SDKs, adb, Xcode, OCR engines, signing material, device trust settings, or privileged services. |
| Deterministic visual inspection fallback | `<AUTOMIND_CLI> visual-inspect <task-code> --image PATH [--baseline PATH] [--bbox x,y,w,h] [--strict-size]` | `scripts/visual_inspector.py`, `requirements/visual-tools.txt` | Phase 3 visual fallback | `logs/iter-N/visual-inspection.json` | Use when no vision-capable model is available or when a measurable screenshot/baseline check is preferred. The wrapper auto-uses `.venv-visual-tools/bin/python` when present and may create that project-local venv if Pillow is missing. It can inspect dimensions/crops/hash and compare to a baseline; it does not semantically understand image content. For design-restore tasks the baseline is the user-provided design image (e.g. Figma export under `.automind/tasks/<task>/design/`); by default the baseline is resized to the screenshot resolution before comparison, and `--strict-size` requires identical pixel dimensions instead. |
| Offline no-device release smoke | `<AUTOMIND_CLI> smoke offline-demo` | `orchestrator/main.py`, `scripts/offline_demo_smoke.py` | Release/dev | `.automind/tasks/offline_demo_smoke/` | Best first demo for users without devices. |
| Planner prompt smoke | `<AUTOMIND_CLI> smoke planner-refiner` | `orchestrator/main.py` | Release/dev | `.automind/tasks/planner_refiner_smoke/` | Offline check for Phase 2 scaffold and prompt rendering. |
| Evaluator context isolation smoke | `<AUTOMIND_CLI> smoke context-isolation` | `orchestrator/main.py` | Release/dev | `.automind/tasks/context_isolation_smoke/` | Verifies Generator logs are not leaked into Evaluator context pack. |
| Verification unblock gate smoke | `<AUTOMIND_CLI> smoke unblock-gate` | `orchestrator/main.py` | Release/dev | `.automind/tasks/verification_unblock_gate_smoke/` | Verifies workflow/completion/record gates reject missing or active temporary verification unblock records and accept restored/promoted records. |
| Android self-repair demo smoke | `<AUTOMIND_CLI> smoke android-self-repair` | `scripts/android_self_repair_smoke.sh` | Dev/demo | demo task artifacts | Requires Android tooling/demo context; not a generic user first step. |
| Android probe-flow self-repair smoke | `<AUTOMIND_CLI> smoke android-probe-flow-self-repair` | `scripts/android_probe_flow_self_repair_smoke.sh` | Dev/demo | demo task artifacts | Dev/release smoke for flow repair behavior. |
| Export public-safe skill | `<AUTOMIND_CLI> export-skill [dir] [--install auto]` | `scripts/export_skill.py`, `scripts/agent_targets.py` | Distribution | skill package, `manifest.json` | Default export excludes private/internal material. |
| Export slash command | `<AUTOMIND_CLI> export-command [dir] [--install target]` | `scripts/export_command.py`, `scripts/agent_targets.py` | Distribution | command package, `manifest.json` | Slash command delegates back to CLI/skill protocol. |

---

## 2. Android command map

Android helper package setup is lazy/local. The public installer does not install it. If `android-preflight` or `android-probe-flow` needs `adbutils` / `uiautomator2` and they are missing, AutoMind may auto-run:

```bash
<AUTOMIND_CLI> setup-automation-tools android
```

This only creates `.venv-android-tools` in the target workspace and installs packages from the AutoMind runtime `requirements/android-tools.txt`. It does not install Android Studio, Android SDK/platform-tools, `adb`, or change the device. If setup fails, first reuse a ready AutoMind runtime helper venv when available; otherwise ask the user whether to fix Python/pip/network or package mirror/wheelhouse access, use adb-only fallback, or stop.

`<AUTOMIND_CLI> setup-automation-tools android --help` is help-only and must not create the venv, run pip, or write setup reports.

| Need | Recommended command | Script(s) behind it | Evidence/output | Notes/safety |
|---|---|---|---|---|
| Check device/tool readiness | `<AUTOMIND_CLI> android-preflight <task-code> [iteration] [--serial SERIAL]` | `scripts/android_preflight.py`, `scripts/failure_classifier.py` | `logs/iter-N/env.json`, `commands.md`, `evaluation.json` | Device unavailable/SystemUI/permission issues are blockers, not product-code failures. |
| Inspect Android project read-only | `<AUTOMIND_CLI> android-project-probe <project-path> [task-code]` | `orchestrator/main.py`, `scripts/android_project_probe.py` | `android-project-probe.json`, `evaluation.json` | Read-only discovery/build gate before modifying a real project. |
| Install/launch an APK | `<AUTOMIND_CLI> android-apk-probe <apk-path> [task-code] [--uninstall]` | `scripts/android_apk_probe.py` | task artifacts + install/launch evidence | Use only when installing the APK is intended. `--uninstall` is destructive to the target app install. |
| Run generated Android probe-flow | `<AUTOMIND_CLI> android-probe-flow <task-code> [iteration] [--dry-run] [--retries N]` | `orchestrator/main.py`, `scripts/android_probe_flow_runner.py` | `logs/iter-N/probe-flow-summary.json`, `logs/iter-N/probe-flow/action-trace.jsonl`, screenshot/hierarchy/logcat, `evaluation.json` | Preferred dynamic Android evaluator for real App UI actions: install/launch, optional popup close, tap/click, input, swipe, assert text/selector/app hierarchy, screenshot/log evidence. Use `--dry-run` to validate flow shape without a device. |
| Run generated Web probe-flow | `<AUTOMIND_CLI> web-probe-flow <task-code> [iteration] [--flow probe-flow.web.json] [--dry-run] [--retries N]` | `scripts/web_probe_flow_runner.py` | `logs/iter-N/web-probe-flow-summary.json`, `logs/iter-N/action-trace.jsonl`, `Validation.md`, `evaluation.json` | Validates reviewable Web UI action intent, records Client UI action evidence, and optionally delegates real execution to a project-native E2E command such as Playwright/Cypress. It does not install browsers or drivers. |
| Suggest/apply safe probe-flow repair | `<AUTOMIND_CLI> probe-flow-repair-suggest <task-code> [--apply]` | `scripts/probe_flow_repair_suggest.py` | suggestions and optional patched `probe-flow.android.json` backup | Only applies conservative repairs. Review intent changes. |
| Repair then rerun probe-flow | `<AUTOMIND_CLI> probe-flow-repair-rerun <task-code> [--dry-run]` | `scripts/probe_flow_repair_suggest.py`, Android probe-flow command | updated evidence | Use after a flow failure that looks like selector/flow drift, not product failure. |

### Android direct adapters

These scripts are lower-level adapters and are normally called by commands or demos:

| Script | Role | Direct use? |
|---|---|---|
| `scripts/android_probe_flow_runner.py` | Executes `probe-flow.android.json` (or legacy `probe-flow.json`) using Android device tools. | Only for adapter debugging; prefer `android-probe-flow`. |
| `scripts/android_project_probe.py` | Read-only Gradle/project probe. | Prefer `android-project-probe`. |
| `scripts/android_apk_probe.py` | Minimal APK install/launch probe. | Prefer `android-apk-probe`. |
| `scripts/android_app_harness_probe.py` | Legacy/fallback app harness probe with explicit selectors. | Direct only for demos or adapter debugging. Prefer generated probe-flow for user tasks. |
| `scripts/android_device_probe.py` | Low-level device diagnostic. | Direct diagnostic only; prefer `android-preflight`. |
| `scripts/android_self_repair_smoke.sh` | Demo smoke. | Dev/release smoke only. |
| `scripts/android_probe_flow_self_repair_smoke.sh` | Probe-flow repair smoke. | Dev/release smoke only. |

---

## 3. iOS command map

iOS helper package setup is lazy/local. The public installer does not install it. If screenshot/app-smoke evaluation needs `pymobiledevice3` and it is missing, AutoMind may auto-run:

```bash
<AUTOMIND_CLI> setup-automation-tools ios
```

This only creates `.venv-ios-tools` in the target workspace and installs packages from the AutoMind runtime `requirements/ios-tools.txt`. It does not install Xcode, change signing/keychains/profiles, trust devices, start `tunneld`, or use sudo. If setup fails, ask the user whether to fix Python/pip/network, continue without screenshot, or stop.

`<AUTOMIND_CLI> setup-automation-tools ios --help` is help-only and must not create the venv, run pip, or write setup reports.

| Need | Recommended command | Script(s) behind it | Evidence/output | Notes/safety |
|---|---|---|---|---|
| Inspect iOS project read-only | `<AUTOMIND_CLI> ios-project-probe <project-path> [task-code] [--scheme SCHEME] [--device-id UDID]` | `orchestrator/main.py`, `scripts/ios_project_probe.py` | `logs/iter-1/ios-project-probe.json`, `evaluation.json` | Run before modifying a real iOS project. |
| Inspect command surfaces in customized workspace | `<AUTOMIND_CLI> ios-command-probe <workspace-path> [task-code] [--include-help]` | `orchestrator/main.py`, `scripts/ios_command_probe.py` | `logs/iter-1/ios-command-probe.json`, `evaluation.json` | Read-only probe for custom workspace wrapper/Bazel/CocoaPods/Bundler surfaces. Does not build/install. |
| Check real-device readiness | `<AUTOMIND_CLI> ios-preflight <task-code> [iteration] [--device-id CORE_DEVICE_ID]` | `scripts/ios_preflight.py`, `scripts/failure_classifier.py` | `logs/iter-N/env.json`, `commands.md`, `evaluation.json` | Developer Mode/trust/signing/device lock issues are blockers, not product-code failures. |
| Analyze screenshot for readiness blockers | `<AUTOMIND_CLI> ios-readiness-analyze <task-code> --image PATH [--bundle-id BID]` | `scripts/ios_readiness_analyzer.py` | readiness classification in task artifacts | OCR/classification only; it does not click. |
| Validate/materialize iOS UI action plan | `<AUTOMIND_CLI> ios-action-plan <task-code> <action-plan.ios.json> [--iteration N]` | `scripts/ios_action_plan.py` | generated XCUITest draft and validation evidence | Converts tap/input/scroll/assert intent into reviewable XCUITest Swift with `XCTAttachment(screenshot:)` after key UI actions. It does not directly poke the app by itself; pair with `ios-xcuitest` or a project/native runner to execute real UI interaction and keep screenshots in `.xcresult`. |
| Launch already-installed iOS app | `<AUTOMIND_CLI> ios-app-smoke <task-code> --bundle-id BID --device-id CORE_DEVICE_ID [--extended] [--screenshot]` | `scripts/ios_app_smoke.py` | process/display/screenshot/crash-hint evidence, `evaluation.json` | Does not install/uninstall. Use for app-alive evidence. |
| Check signing preservation risk | `<AUTOMIND_CLI> ios-signing-preflight <task-code> --bundle-id BID --installed-team TEAM [--new-team TEAM] [--device-id UDID]` | `scripts/ios_signing_preflight.py` | signing classification, `ask_user` when needed | Read-only. Human decision required for signing/team changes. |
| Discover existing signing material (self-heal signing build failures) | `<AUTOMIND_CLI> ios-signing-preflight <task-code> --discover [--bundle-id BID] [--installed-team TEAM] [--destination-type device\|simulator] [--device-id UDID] [--profile-root DIR]` | `scripts/ios_signing_preflight.py` | identities + profiles + whether an Apple ID is signed in and which Teams it manages (`xcodeAccounts`) + project `DEVELOPMENT_TEAM`/`CODE_SIGN_STYLE`/specifiers + an executable `signingPlan` (the single source of truth the `ios-xcuitest` runner consumes) + legacy `recommendation` | Read-only. Use before `ask_user` on signing/provisioning build failures: consume `signingPlan` (ladder: `simulator_no_sign` -> `manual_reuse` -> `automatic` (needs `targetTeamManagedByAppleId=true`) -> `blocked`); escalate only when `signingPlan.strategy=blocked`. |
| Capture physical device screenshot | `<AUTOMIND_CLI> ios-screenshot <task-code> [iteration] [--device-id UDID] [--output PATH]` | `scripts/ios_screenshot.py` | screenshot + `evaluation.json` | Device id can come from runtime state; no hardcoded device default. |
| Run XCUITest evaluator | `<AUTOMIND_CLI> ios-xcuitest <task-code> [iteration] --project-path PATH --scheme SCHEME --device-id UDID ...` | `scripts/ios_xcuitest_runner.py` | `.xcresult`, logs, `evaluation.json` | Preferred iOS UI automation path for real app tap/input/scroll/assert flows when a test target/runner is available. |
| Run generated iOS probe-flow | `<AUTOMIND_CLI> ios-probe-flow <task-code> [iteration] [--flow probe-flow.ios.json] [--dry-run] [--retries N]` | `scripts/ios_probe_flow_runner.py` | probe-flow summary, `logs/iter-N/action-trace.jsonl`, `Validation.md`, `evaluation.json` | Validates reviewable UI action intent, records Client UI action evidence, and delegates real execution to XCUITest when configured. Use for tap/input/scroll/assert plans, not just passive evaluation. |
| Materialize iOS probe-flow to Swift | `<AUTOMIND_CLI> ios-probe-flow-materialize <task-code> [iteration] [--flow probe-flow.ios.json] --target-bundle-id BID` | `scripts/ios_probe_flow_materialize.py` | Swift XCUITest draft in `logs/iter-N/` | Generates executable XCUITest tap/input/scroll/assert code with `XCTAttachment(screenshot:)` after key UI actions for review/copy into a runner project; does not edit target app. |

### iOS direct adapters

| Script | Role | Direct use? |
|---|---|---|
| `scripts/ios_project_probe.py` | Read-only project/scheme/build-settings probe. | Prefer `ios-project-probe`. |
| `scripts/ios_command_probe.py` | Read-only command-surface probe. | Prefer `ios-command-probe`. |
| `scripts/ios_preflight.py` | Physical-device readiness evaluator. | Prefer `ios-preflight`. |
| `scripts/ios_app_smoke.py` | Launch already-installed app and collect app-alive evidence. | Prefer `ios-app-smoke`. |
| `scripts/ios_screenshot.py` | Capture physical screenshot. | Prefer `ios-screenshot`. |
| `scripts/ios_readiness_analyzer.py` | OCR readiness classification. | Prefer `ios-readiness-analyze`. |
| `scripts/ios_action_plan.py` | Validate/materialize iOS action plans. | Prefer `ios-action-plan`. |
| `scripts/ios_probe_flow_runner.py` | Probe-flow evaluator/materializer bridge. | Prefer `ios-probe-flow`. |
| `scripts/ios_probe_flow_materialize.py` | Swift XCUITest draft generator. | Prefer `ios-probe-flow-materialize`. |
| `scripts/ios_xcuitest_runner.py` | XCUITest evaluator. | Prefer `ios-xcuitest`. |
| `scripts/ios_signing_preflight.py` | Read-only signing/provisioning classifier. | Prefer `ios-signing-preflight`. |
| `scripts/resign_ios_app.py` | Manual app re-signing helper. | Sensitive direct tool only; not part of default public workflow. Requires explicit human approval. |
| `scripts/ios_demo_*.sh` | Demo project verification helpers. | Demo/dev only. |

---

## 4. Distribution / agent integration scripts

| Need | Recommended command | Script(s) behind it | Notes |
|---|---|---|---|
| Export/install skill package | `<AUTOMIND_CLI> export-skill [dir] --install none\|auto` | `scripts/export_skill.py`, `scripts/agent_targets.py` | Public-safe by default. Use private flags only for internal/private review. |
| Export/install slash command | `<AUTOMIND_CLI> export-command [dir] --install none\|all\|auto\|claude\|codex\|trae\|trae-cn` | `scripts/export_command.py`, `scripts/agent_targets.py` | Command text locates CLI via project-local script, `$AUTOMIND_HOME`, or PATH. |
| Resolve agent target paths | internal helper | `scripts/agent_targets.py` | Import-only helper shared by exporters. |

---

## 5. Internal helpers

| Script | Role | Direct use? |
|---|---|---|
| `scripts/failure_classifier.py` | Shared failure categorization for logs and exit codes. | Internal/import helper; CLI can run it for debugging only. |
| `scripts/quality_evaluator.py` | Lightweight quality evaluator. | Usually called by `quality-check`; direct use is acceptable for adapter debugging. |
| `scripts/checkpoint.py` | Checkpoint manager. | Called by `checkpoint`; direct use is acceptable if needed. |
| `scripts/offline_demo_smoke.py` | Offline smoke implementation. | Prefer `smoke offline-demo`; direct execution is release/dev only. |

---

## 6. What coding agents should understand


Canonical AutoMind flow:

```text
User request
  -> Brainstorm.md
  -> Requirements.md

  -> TestCases.md
  -> Plan.md
  -> workflow.json
  -> workflow-check
  -> Generator
  -> Delivery.md
  -> Evaluator
  -> Validation.md + evaluation.json
  -> completion-check
```

A coding agent must not memorize every script or improvise tool order. It must
follow this decision flow when choosing AutoMind commands/scripts:

```text
Need to work on a task?
  -> use ask/resume/status/summary.
Need better requirements/tests before coding?
  -> use plan or templates/phase2_planner_prompt.md, then workflow-check to refresh/validate workflow.json.
Need to verify generic code?
  -> use script-command or project-native tests, then quality-check.
Need Android/iOS device/app verification?
  -> preflight first, then project/APK/app/probe-flow/XCUITest command.
Need to export/install for another agent?
  -> export-skill/export-command.
Unsure which low-level script backs a command?
  -> read this catalog, but still prefer <AUTOMIND_CLI> commands.
```

The important model contract is:

1. **Requirements and tests are model-refined artifacts** (`Brainstorm.md`, `Requirements.md`, `TestCases.md`, `Plan.md`).
2. **Scripts are adapters** for execution, evidence collection, classification, export, or smoke tests.
3. **The loop consumes evidence** through `Validation.md`, `evaluation.json`, and `logs/iter-N/` rather than prose claims.
4. **Finish is gated** by `completion-check`: required `TC-*` cases must pass, required `AC-xxx` criteria must be covered, and evidence paths must exist.
5. **Environment/device/signing blockers are not product-code failures**.
6. **Sensitive actions** such as uninstalling apps, changing signing, or re-signing require explicit human approval.

| Show formal trace spans | `<AUTOMIND_CLI> trace <task-code> [--json|--write]` | `orchestrator/main.py`, `orchestrator/session/trace.py` | Observability | `trace.json` when `--write` | Projects `events.jsonl` + workflow/runtime state into OpenTelemetry-friendly spans. |
| Show improve suggestions | `<AUTOMIND_CLI> improve-suggestions [--limit N]` | `orchestrator/main.py`, `orchestrator/summary.py` | Phase 4 / Reuse | `.automind/summary/run-cards.jsonl` | Advisory suggestions derived from summary run cards; review before applying. |
| Evaluate harness process | `<AUTOMIND_CLI> process-check <task-code> [--json|--soft|--no-write]` | `orchestrator/main.py`, `orchestrator/process_eval.py` | Process Evals | `process-eval.json`, `trace.json` | Checks whether AutoMind followed required process gates; complements workflow-check/completion-check. |
