# Offline Demo Artifact Shape

After running:

```bash
./automind.sh smoke offline-demo
```

CodeAutonomy creates this local evidence shape:

```text
.automind/tasks/offline_demo_smoke/
  Require.md
  Plan.md
  Delivery.md
  Validation.md
  evaluation.json
  runtime-state.json
  summary.md
  logs/iter-1/
    commands.md
    evaluator.log
```

The important release contract is:

- `Validation.md` records environment, commands, evidence, reusable findings, and avoid-repeat notes.
- `evaluation.json` records the machine-readable result and `nextAction`.
- `summary.md` records reusable conclusions.
- `record-check` must pass before the task is considered reusable.

Minimal successful `evaluation.json` shape:

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
