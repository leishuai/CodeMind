# Preloaded Summaries

Preloaded summaries are curated, business-agnostic summary files available before a task starts.

They solve the first-run problem: before `.automind/summary/*` has any local task lessons, CodeAutonomy can still give the Planner/Generator/Evaluator a small set of evidence-backed hints through `.automind/tasks/<task>/Reuse.md`.

Preloaded is intentionally flat:

```text
summaries/preloaded/<topic>.md
```

Do not create `business/` or `technical/` subfolders here. Preloaded packs are technical by nature and must stay generic. Business/project-specific knowledge belongs under:

```text
summaries/accumulated/business/<project-slug>/
```


## Naming convention

CodeAutonomy discovers preloaded summaries by filename prefix when generating `Reuse.md`. Keep names short, explicit, and searchable by capability:

- `ios-*` — iOS-specific playbooks verified through real iOS project/device work.
- `android-*` — Android-specific playbooks; add only after Android evidence is accumulated.
- `client-*` — cross-client/mobile patterns shared by iOS and Android.
- `common-*` — truly cross-stack guidance that applies beyond clients, such as generic build/test/reuse policy.

Do not use business/project prefixes in preloaded packs. Business-specific lessons belong in `summaries/accumulated/business/<project-slug>/`.



## README frontmatter contract

Every preloaded summary file must start with a small YAML-style frontmatter block, similar to `SKILL.md`, so `Reuse.md` can build a useful overview without reading the whole pack:

```yaml
---
name: ios-signing-install
description: "One sentence explaining what this pack is for."
use_when:
  - "When this pack should be loaded."
solves:
  - "The concrete recurring problem it helps solve."
---
```

`description` should be one concise but informative sentence. `use_when` and `solves` should be short bullets. Keep this metadata stable and high-signal; it is what models see first in the progressive-loading index.

## Progressive loading

`Reuse.md` should not inline entire preloaded packs. It includes a compact index only: pack name/title, runtime-relative `Path`, one-line `Summary` from frontmatter `description`, and a fixed `Load` hint. The model should read the referenced file only when the current task needs that capability. `Path` is relative to the CodeAutonomy runtime root (for example `~/.automind/automind` or `$AUTOMIND_HOME`), not the target project and not `.automind/summary/`.

Example:

- iOS task: load `common-*`, `client-*`, and `ios-*` pack overviews first; read `ios-signing-install.md` only when signing/install is relevant.
- Android task: load `common-*`, `client-*`, and future `android-*` pack overviews first; do not read iOS packs.
- Generic script/server task: load `common-*` overviews only.

This keeps context small and prevents preloaded from becoming another prompt dump. Keep each pack `description` short enough to work as a one-line `Summary`; longer operational detail belongs in the pack body and is loaded on demand.

## Curation bar

Preloaded must stay small and high-signal. Add or keep a pack only when it captures a reusable pattern that materially helps a coding agent solve a hard problem faster or more safely. Prefer fewer, sharper packs over broad notes.

A preloaded item should usually include:

- the problem it solves;
- when to apply it;
- the recommended path;
- known avoid paths;
- evidence expectations;
- safety/human-approval boundaries.

Do not add:

- one-off project facts;
- raw logs;
- obvious advice that stronger models can infer unaided;
- stale tactics kept only because they once worked;
- broad checklists without evidence-backed decision rules.

As model capability improves, preloaded summaries should become more selective, not larger. Retire or compress guidance when it no longer provides meaningful leverage.

## What belongs here

Preloaded packs should help coding agents and other models solve common engineering problems without human hand-holding, especially:

1. compile/build/verification paths;
2. real-device run/install/launch readiness;
3. UI automation strategy and repair heuristics;
4. log collection and log-reading/classification rules.

They may be selected automatically by task type, but they remain hints only:
current `Requirements.md`, `TestCases.md`, platform verification-flow references,
active device-operation rules, and fresh evidence always win.

## Updating preloaded summaries

Preloaded does not mean immutable. It means "available before future tasks start."

When a task produces stronger public-safe evidence, update the relevant preloaded summary directly. Use a short change note and preserve the safety boundary, especially around data deletion, credentials, certificates, and system/keychain changes.


## Validation

After adding or editing a preloaded summary, run:

```bash
./automind.sh preloaded-check
pytest -q tests/test_preloaded_reuse.py
```

The check verifies naming prefixes, frontmatter, task-type discovery, and progressive `Reuse.md` index generation.

## Current packs

- `common-build-verification-playbook.md` — generic rule for reusing known successful build/test/verification paths and avoiding known failed paths.
- `android-bytecode-transform-cache.md` — Android bytecode-transform cache and runtime log evidence diagnostics.
- `android-readiness.md` — Android real-device readiness, adb/device classification, helper venv reuse, and probe-flow evidence.
- `client-ui-repair.md` — common Android/iOS rule: generic repair heuristics are reusable; concrete flows must come from test intent.
- `ios-app-smoke.md` — minimal iOS installed-app launch/process evidence.
- `ios-external-xcuitest-runner.md` — external runner pattern for apps without usable UI test targets.
- `ios-readiness.md` — OCR-based blocker classification policy.
- `ios-screenshot.md` — physical-device screenshot backend and tunneld safety boundary.
- `ios-signing-install.md` — common iOS policy: prefer old-Team signing to preserve app data; uninstall is explicit fallback.
- `ios-ui-actions.md` — XCUITest/action-plan strategy and selector policy.
