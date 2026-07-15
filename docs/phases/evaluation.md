# Evaluation phase

## Goal
Independently verify the delivered change against required testcases, acceptance criteria, and runtime policy.

## Read on entry
- `docs/workflow.md`
- `docs/phase3-verification.md`
- `docs/references/state-actions.md`
- `docs/references/verification-flow.md`
- `workflow.json`
- `Delivery.md`
- `delivery.json`
- `testcases.json`
- `logs/iter-N/iteration-purpose.md` / `.json`
- `logs/iter-N/evaluator-context.md` / `.json`

## Hard inputs
- `Delivery.md/json` and changed-file summary.
- `Requirements.md/json`, `TestCases.md/json`, `workflow.json`.
- Required TC list, runtime target, probe-flow/script/build command.
- Evidence logs/screenshots/device outputs as required.

## Hard outputs
- `Validation.md`.
- `evaluation.json` with `result`, `nextAction`, `testResults[]`, `evidence[]`, optional lightweight `evidenceIndex[]`, and `failedChecks[]`.
- Evidence files under `logs/iter-N/`.
- Verification checklist progress when applicable.

`evidenceIndex[]` is not a separate manifest file. It is a compact helper field
inside existing evaluation or iteration summary/result JSON. Entries should stay
small: `path` plus optional `type`, `tc`, and `signal`. Runner/script outputs
should create it when they know what artifacts they produced; the Evaluator or
coding agent only adds entries for special project-native evidence that cannot
be inferred automatically.

## Gate
- Evaluator owns verification and route classification, not product implementation.
- If implementation/Delivery/evidence is incomplete but Requirements and TestCases remain valid, set `nextAction=retry_generator` to route back to Generator.
- If requirements, TC design, validation target, or proof strategy is wrong, set `nextAction=replan`.
- Environment blockers, startup-only checks, and preflight-only checks cannot satisfy required runtime TCs.
- Weak/current/advisory results cannot overwrite final completion pass.
- Captured-but-missing evidence is still evidence: use `signal: "missing:<name>"` or `missingSignals[]` when a log/screenshot/report was collected but did not show the expected signal.
- Prefer project-native tests/reports, CodeMind runner/probe-flow artifacts, runtime or scoped diagnostic logs, and mock/test sink evidence before raw DB/file/state inspection.
- Database/file/state inspection is optional project-specific evidence; do not make raw DB access the default proof path when log/test/probe/diagnostic evidence can prove the case more safely.

## Downstream contract
Completion-check consumes `evaluation.json`, `Validation.md`, required TC/AC mappings, and evidence paths. If Evaluator routes `retry_generator`, Generator consumes the failure classification and missing-proof details.

## Checker
- `evaluation_contract`
- Must produce result, nextAction, testcase results, evidence, and failed checks when relevant.

## Blockers
- Missing required runtime evidence.
- Unsafe device/install/signing/external dependency conditions.
- Repeated same failure requiring replan or ask_user.

## Exit
Proceed to `completion` when evaluation claims finish and `automind-workflow-state.json` routes to `summary/completion`. Otherwise refresh/read `automind-workflow-state.json`; `evaluation.json.nextAction` (`retry_generator`, `replan`, `ask_user`, or `stop`) is a local resolver signal, not a competing workflow truth.
