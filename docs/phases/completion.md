# Completion phase

## Goal
Run final machine gates and produce a trustworthy finish state, human-readable
handoff, summary, and reusable learning.

## Read on entry
- `docs/workflow.md`
- `docs/phase4-summary.md`
- `workflow.json`
- `evaluation.json`
- `VerificationLedger.json`
- `Delivery.md`

## Inputs
- `workflow.json`
- `evaluation.json`
- `VerificationLedger.json`
- `delivery.json` / `Delivery.md`

## Outputs
- `completion-report.json`
- `VerificationLedger.json`
- `Report.html`
- `summary.md`
- reusable memory/record artifacts when applicable

## Checker
- `completion_contract`
- `completion-check` must pass before the task can be called finished.

## Human Handoff
- Generate or refresh `Report.html` only after completion is proven or at an
  explicit durable handoff point.
- In `Report.html`, the `Test Results` table must make each TC's primary proof
  obvious through `Key Evidence`: screenshot thumbnails when available,
  `evidenceAssessment.machineAnchor`, hardMetrics anchors, and the few runtime
  proof files users should inspect first.
- Keep complete noisy artifact lists available for traceability in the final
  `Evidence / Screenshots / Logs` column, preferably collapsed, rather than as
  the primary reading path.
- Runtime/UI TC rows should include screenshot evidence by default or explicitly
  state why no screenshot is linked.
- The final user-facing response is part of completion: say what was completed,
  what was generated, ask the user to open `Report.html` first, and call out the
  key proof/log/ledger files.

## Blockers
- Missing required testcase evidence.
- Acceptance criteria coverage gaps.
- Runtime proof policy failures.
- Stale or contradictory human-readable reports.

## Exit
Finish only after `completion-check` passes, `summary`/`record-check` complete,
`Report.html` is generated or inspected, and the final natural-language handoff
points the user to the report and key evidence files.
