# CodeMind Summaries

This directory is CodeMind's long-lived summary knowledge system.

It has two roles:

1. **Preloaded summaries**: curated, business-agnostic experience packs available before a task starts.
2. **Accumulated summaries**: lessons discovered during ongoing tasks and promoted from task summaries when they become reusable.

Summary is not raw log storage. Raw evidence stays in `.automind/tasks/<task>/logs/`; summary stores reusable conclusions, decision rules, pitfalls, and concise known successful/failed build or verification paths.

In full runtime mode, new tasks receive `.automind/tasks/<task>/Reuse.md`. It combines local accumulated memory from `.automind/summary/*`, task-type-relevant public-safe seeds from `summaries/preloaded/`, and local/private project lessons from `summaries/accumulated/business/` when those files exist in the runtime checkout. Preloaded packs stay generic; business/project knowledge must not live under `summaries/preloaded/`.

## Directory layout

```text
summaries/
├── README.md
├── preloaded/          # generic public-safe packs, e.g. build, real-device, UI automation, logs
└── accumulated/
    ├── inbox/          # newly discovered lessons waiting for triage
    ├── business/       # promoted business/project/domain lessons
    └── technical/      # promoted technical lessons not yet elevated into preloaded packs
```

## Classification

Use the right destination:

- **Preloaded**: generic, reusable, public-safe playbooks that can help any coding agent before local memory exists. Keep them curated and high-signal; preloaded is a product asset, not an archive.
- **Accumulated business**: tied to a concrete product/domain/repository. Example: `<project-slug>`.
- **Accumulated technical**: reusable platform/tooling patterns that are not yet stable enough to become preloaded.

A lesson may be recorded in one place and referenced from another. Avoid duplicating full text in many places.

## When to record

Record a summary item when at least one is true:

- A build/install/debug pitfall was verified and is likely to recur.
- A command path is confirmed as safe/unsafe for a real project.
- A blocked decision point requires human policy, data-loss approval, or credentials.
- A failure was misclassified and future agents are likely to repeat the mistake.
- A task generated a reusable command, probe-flow, runner behavior, device readiness rule, UI automation rule, or log-reading rule.

Do not record:

- Raw logs or huge command output.
- Secrets, tokens, certificates, p12 contents, or unredacted environment dumps.
- One-off speculation not backed by evidence.
- Noise that only matters within a single task and is already in `Validation.md`.
- Business/project-specific material under `summaries/preloaded/`.

## Promotion flow

1. During a task, keep detailed evidence in `.automind/tasks/<task>/`.
2. At task close, generate `summary.md` and append `.automind/summary/local-reuse-index.md`. The default extractor is deterministic; optional AI summary refinement can classify and condense the deterministic seed before validated output is merged.
3. Task summaries automatically accumulate reusable lessons into the machine-global `summaries/accumulated/`, routed to:
   - `summaries/accumulated/technical/...` for cross-project, business-agnostic, public-safe lessons, or
   - `summaries/accumulated/business/<project-slug>/...` for project-bound lessons.
4. When a technical lesson becomes broadly useful, public-safe, and high-leverage, promote or merge it into a generic pack under `summaries/preloaded/<topic>.md`. Use `ios-*`, `android-*`, `client-*`, or `common-*` naming depending on scope. This promotion is a maintainer-only, git-distributed step and is intentionally not automated.
5. Preloaded summaries are not frozen. When new verified evidence changes a generic recommendation, update the preloaded pack in place and record what changed; do not keep stale default guidance active. Remove or compact guidance that stops being useful as model capability improves.

## File format

Prefer small markdown files with:

- Context / scope
- Verified facts
- Recommended path
- Known successful path: purpose, exact command/method, cwd/preconditions, evidence, scope, confidence
- Avoid / deprecated path: failed command/method, failure category, evidence, condition required before retry
- Decision points requiring human confirmation
- Evidence pointers
- Last updated date
- Change note when updating preloaded guidance

## Concision and de-duplication

Keep summary knowledge concise. Do not repeat the same rule in many files.

- Put each core rule in one canonical location.
- Other files should link to it and only add platform-specific differences.
- Prefer short evidence-backed bullets over long repeated explanations.
- If two summaries say the same thing, merge them or mark one as a pointer.
