---
name: common-build-verification-playbook
description: "Generic build, test, and verification reuse playbook for choosing proven commands, avoiding stale failed paths, and classifying build/test failures with evidence."
use_when:
  - "choosing build/test/verification commands"
  - "a build or test command fails"
  - "deciding whether a known successful or avoid path applies"
solves:
  - "prevents repeated command guessing"
  - "separates product failures from environment/tool/verifier failures"
  - "standardizes reusable evidence for future tasks"
---
# Build / Verification Reuse Playbook

This is the generic public-safe playbook for reusing known successful build and verification paths.
It applies to script, backend, web, desktop, Android, iOS, and other client tasks.

## Core rule

Before choosing build, test, launch, or verification commands, read task `Reuse.md` and look for:

- `Successful path:` entries — commands or methods already proven on this machine/project.
- `Avoid path:` entries — commands or methods that previously failed, were deprecated, or were misleading.
- local environment facts — cwd, virtualenv, SDK/device constraints, fixtures, preflight needs, and evidence paths.

Prefer a previously successful path when the current task scope and preconditions match. Do not replace a known-good command with a guessed command unless current evidence proves the known path is stale or irrelevant.

## How to apply

1. Match scope first:
   - same repository or platform;
   - same build system/runtime when known;
   - same verifier type: unit/integration/runtime/device/UI/script-command/probe-flow/XCUITest/browser.
2. Check preconditions:
   - cwd/project root;
   - required env vars or local virtualenv;
   - SDK/tool/device/signing/account availability;
   - fixture data or target route/screen/state.
3. Copy the selected path into `TestCases.md` and `Plan.md` as the concrete command/method and expected evidence.
4. If the path fails now, classify the reason before retrying:
   - product/runtime failure;
   - environment/tool/device/signing failure;
   - unrelated existing workspace failure;
   - verifier/test-harness failure;
   - stale reuse entry.
5. Record the result in `Validation.md`, `evaluation.json.evidence[]`, and the final `summary.md` so future tasks can reuse or avoid it.

## Build failure policy

A build failure is not automatically a product-code failure.

When build/test/runtime verification fails:

- preserve the exact command, cwd, env/tool versions, and full evidence path;
- identify whether the failing target is required for the current `TC-*`;
- use an existing known-good command first if one exists;
- if a safe temporary unblock is needed, record it as a `verificationUnblockChanges[]` item and restore or promote it before finish;
- do not claim completion while a temporary unblock remains active.

### Stale build cache vs real source defect

A compile failure that names many symbols which still exist in source is often a
stale incremental build output / classpath cache, not a source defect. Before
editing source:

- if the build tool reports a missing task/target/flavor (e.g. Gradle `Cannot
  locate tasks that match ...`), treat it as a verification-command / flavor
  configuration error and fix the command, not the product code;
- when a large set of unresolved/undefined symbols all point at one dependency
  module that still has the symbols in source, build that upstream module on its
  own — if it compiles, the downstream failure is a stale cache, not a defect;
- prefer a small-scope reset first: clean only the affected upstream/downstream
  module build outputs and rerun the failing compile task with a force-rerun
  flag (e.g. `--rerun-tasks`); widen to a full clean only if that still fails;
- do not chase the symbols one by one (patching imports, copying constants,
  moving functions) before a cache reset has been tried — that produces a dirty
  pseudo-fix.

## Retry / avoid-repeat policy

Use small bounded retries for transient verifier/runtime startup failures before escalating to replan or ask_user. A retry is useful only when it repeats the same safe command after a plausible transient condition changes or can be refreshed by the tool itself.

Good retry candidates:

- daemon/server startup races;
- temporary socket/listener failures;
- UI/device timing delays;
- network or subprocess transient errors already classified by the runner;
- command-level probe-flow retries where the same flow can succeed without product-code changes.

Do not keep retrying a known failed path if `Reuse.md` says it failed under the same conditions.
Only retry it when at least one condition changed, such as:

- dependency/tool/device/signing state changed;
- the command cwd or scheme/target was corrected;
- the failing product code was repaired;
- the test harness/probe-flow was repaired;
- the current task explicitly requires revalidating the old path.

After a bounded retry still fails, preserve both attempts' evidence and classify the blocker precisely. Do not turn a tool/server startup failure into a product-code failure, and do not ask the user before exhausting safe, already-approved local retry paths.

## Evidence to record for future reuse

A reusable successful path should include:

| Field | What to record |
|-------|----------------|
| Purpose | Build/test/install/launch/runtime/UI/API/device verification goal |
| Command / method | Exact command when safe, or method when command is generated/dynamic |
| Preconditions | cwd, env, tools, device/simulator, fixtures, accounts, permissions |
| Evidence | Log/report/screenshot/UI hierarchy/test result path |
| Scope | Platform/project area/testcase IDs where it applies |
| Reuse confidence | `high`, `medium`, or `low` based on repeatability and evidence quality |

A reusable avoid path should include the failed command/method, failure category, evidence path, and the condition required before trying again.

## Safety boundary

Keep this playbook generic. Do not store secrets, tokens, certificates, private device IDs, private bundle IDs, company/customer names, or local user-specific absolute paths in public preloaded summaries.
Local `.automind/summary/*` may contain user-machine facts, but exported public skills must not include private/business packs by default.

Last updated: 2026-06-12
