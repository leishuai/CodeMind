# Log Evidence Guide

CodeMind keeps raw logs as evidence, but agents should not read large logs by default. The goal is faster, more reliable loop execution without losing auditability.

## Reading order

1. `automind-workflow-state.json`, `evaluation.json`, `completion-report.json`
2. `Validation.md`, `Delivery.md`
3. latest `logs/iter-N/commands.md` and `logs/iter-N/log-digest.md`
4. `*summary*`, `*result*`, proof artifacts, action traces
5. targeted `grep` / `tail` of a specific raw log
6. only as a last resort: broad raw-log or build-intermediates search

## Digest policy

Each Generator/Evaluator context pack writes:

- `logs/iter-N/log-digest.json`
- `logs/iter-N/log-digest.md`

The digest records file sizes, mtimes, hashes, read modes, key PASS/FAIL/ERROR/WARN lines, and a small tail preview for bounded logs. Raw files remain on disk and are referenced by path.

## Size policy

- Small logs can be read directly.
- Large logs should be read via digest, targeted grep, or tail.
- Oversized logs should not be embedded into prompts/reports by default.
- Build intermediates and dex/string scans should be constrained with file-size/count limits and should produce summary files, not huge raw dumps.

## Evidence integrity

Compaction must not hide evidence. If a digest is used, keep the raw log path and hash. If a testcase depends on a specific line, include the line in a result/proof artifact and reference the raw log.
