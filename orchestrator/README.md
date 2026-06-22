# AutoMind Orchestrator

This folder is required. It contains the core AutoMind loop engine used by `automind.sh` and by repair/rerun helpers.

## Responsibility

The orchestrator package owns the AutoMind task lifecycle:

- create and resume tasks;
- write/read `.automind/tasks/<task>/` state;
- generate core artifacts (`Requirements.md`, `Plan.md`, `Validation.md`, `evaluation.json`, ``runtime-state.json`, summaries);
- route Generator/Evaluator loop decisions from `evaluation.json.nextAction`;
- enforce record checks and reusable summaries;
- coordinate generic script verification, Android/iOS probe-flow evaluators, and context-isolated evaluator packs.

## Boundary

- `automind.sh` is the stable user-facing CLI router.
- `orchestrator/main.py` remains the CLI entrypoint and loop/state-machine coordinator.
- `orchestrator/agents.py` owns agent CLI preflight/dispatch.
- `orchestrator/automation_tools.py` owns project-local Android/iOS/visual helper setup and environment snapshots.
- `orchestrator/artifacts.py` owns markdown artifact parsing, TC/AC/ID helpers, evidence normalization, and checklist parsing.
- `orchestrator/classification.py` owns generic product-agnostic text classification helpers used by scaffolding and gates.
- `orchestrator/completion.py` owns completion-check, TC/AC/evidence ledgers, and temporary verification-unblock validation.
- `orchestrator/context_packs.py` owns Generator/Evaluator context-pack creation and validation.
- `orchestrator/records.py` owns record-check and final task-record completeness updates.
- `orchestrator/reports.py` owns `status` guidance and user-facing report manifests.
- `orchestrator/resume.py` owns stage-level recovery decisions for interrupted tasks.
- `orchestrator/reuse.py` owns prompt rendering and Reuse.md/preloaded summary context assembly.
- `orchestrator/summary.py` owns summary generation, reusable path extraction, and AI summary-refiner seed/validation helpers.
- `orchestrator/workflow.py` owns workflow-check and Phase 2 cross-artifact gate validation.
- `orchestrator/config.py`, `console.py`, and `state.py` contain shared configuration, terminal helpers, and runtime-state persistence plus runtime-stateing.
- `scripts/` contains narrower platform adapters and helper tools.
- `docs/`, `schemas/`, `templates/`, and `examples/` define the public workflow contract.

Do not put local task evidence in this folder. Runtime data belongs under `.automind/tasks/` and is ignored by git.
