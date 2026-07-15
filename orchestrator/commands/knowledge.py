"""Knowledge, summary, gate, and diagnostic CLI command handlers."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from orchestrator.completion import apply_completion_gate, build_completion_report, write_completion_ledger
from orchestrator.config import AUTOMIND_ROOT, SUMMARY_DIR, TASKS_DIR
from orchestrator.console import error, success, warn
from orchestrator.evaluation_result import apply_evaluation_result
from orchestrator.knowledge_index import evaluate_knowledge_store, find_knowledge_record, load_knowledge_index, read_raw_excerpt, search_knowledge, summarize_knowledge_record
from orchestrator.process_eval import render_process_eval, run_process_eval
from orchestrator.records import check_task_records, reconcile_validation_status, validation_status_issues
from orchestrator.reports import build_summary_reuse_status, print_report_manifest
from orchestrator.reuse import build_preloaded_context, preloaded_summary_files_for_task_type
from orchestrator.state import clear_current_task, get_task_dir, list_tasks, read_current_task, read_evaluation_json, read_notifications, read_runtime_state, rel_to_root, tick_iteration, update_runtime_state, write_evaluation_json
from orchestrator.summary import generate_summary, render_improve_suggestions
from orchestrator.workflow import check_workflow_consistency
from orchestrator.workflow_contract import validate_workflow_contract, write_workflow_contract
from orchestrator.phase_transition import refresh_phase_transition_summary

def cmd_reuse(limit: int = 80):
    """\u67e5\u770b\u672c\u673a\u590d\u7528\u7d22\u5f15，\u5e2e\u52a9\u4e0b\u4e00\u4e2a task \u5feb\u901f\u590d\u7528\u6210\u529f\u8def\u5f84。"""
    path = SUMMARY_DIR / "local-reuse-index.md"
    if not path.exists():
        warn("No local reuse index yet. Finish a task and generate a summary first.")
        return
    lines = path.read_text(errors="ignore").splitlines()
    for line in lines[-limit:]:
        print(line)


def cmd_reuse_ack(argv: list[str]) -> None:
    """Record/inspect the machine-checkable reuse acknowledgement gate.

    Usage:
      reuse-ack show <task-code> [phase]
      reuse-ack <task-code> <phase> [--read] [--applied A;B] [--ignored C;D]
                [--decision retry|ask_user|replan] [--note "..."]

    The acknowledgement turns "I considered reuse" into a record: gated phases
    (generator/evaluator) cannot proceed in workflow-check until this is set with
    phaseReuseRead=true. Repeated-failure / signing / device / build cases must
    show matched safe reuse paths were applied (reuseApplied) before ask_user.
    """
    from orchestrator.knowledge_index import compute_reuse_gate, record_reuse_ack

    if not argv or argv[0] in {"help", "-h", "--help"}:
        print("Usage:")
        print("  reuse-ack show <task-code> [phase]")
        print("  reuse-ack <task-code> <phase> [--read] [--applied A;B] [--ignored C;D] [--decision retry|ask_user|replan] [--note \"...\"]")
        return

    if argv[0] == "show":
        if len(argv) < 2:
            error("Please provide a task code")
            sys.exit(1)
        task_dir = get_task_dir(argv[1])
        if not task_dir.exists():
            error(f"Task does not exist: {argv[1]}")
            sys.exit(1)
        state = read_runtime_state(task_dir) or {}
        reuse_gate = state.get("reuseGate") if isinstance(state.get("reuseGate"), dict) else {}
        if len(argv) > 2:
            phase = argv[2]
            gate = reuse_gate.get(phase) or compute_reuse_gate(task_dir, phase)
            print(json.dumps(gate, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(reuse_gate, ensure_ascii=False, indent=2))
        return

    if len(argv) < 2:
        error("Please provide a task code and phase")
        print("Example: reuse-ack <task-code> generator --read --applied \"devicectl install/launch\"")
        sys.exit(1)
    task_code = argv[0]
    phase = argv[1]
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)

    def _opt(flag: str) -> Optional[str]:
        if flag in argv:
            idx = argv.index(flag)
            if len(argv) > idx + 1:
                return argv[idx + 1]
        return None

    def _list_opt(flag: str) -> list[str]:
        raw = _opt(flag)
        if not raw:
            return []
        return [item.strip() for item in raw.split(";") if item.strip()]

    phase_reuse_read = "--read" in argv
    reuse_applied = _list_opt("--applied")
    reuse_ignored = _list_opt("--ignored")
    decision = _opt("--decision")
    note = _opt("--note")
    # Applying a reuse path implies it was read.
    if reuse_applied or reuse_ignored:
        phase_reuse_read = True

    gate = record_reuse_ack(
        task_dir,
        phase,
        phase_reuse_read=phase_reuse_read,
        reuse_applied=reuse_applied,
        reuse_ignored=reuse_ignored,
        decision=decision,
        note=note,
    )
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    if gate.get("acknowledged"):
        success(f"Reuse acknowledged for {task_code}/{phase}")
    else:
        warn(f"Reuse ack recorded but phaseReuseRead is false for {task_code}/{phase}")


def _print_knowledge_record(record: dict, *, json_mode: bool = False) -> None:
    summary = summarize_knowledge_record(record)
    if json_mode:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    print(f"- id: {summary.get('id')}")
    if summary.get("title"):
        print(f"  title: {summary.get('title')}")
    if summary.get("value") or summary.get("description"):
        print(f"  value: {summary.get('value') or summary.get('description')}")
    print(f"  raw: {summary.get('rawPath') or '-'}")
    print(f"  confidence: {summary.get('confidence') or '-'}")
    if summary.get("score") is not None:
        print(f"  score: {summary.get('score')} ({', '.join(summary.get('matchReasons') or []) or '-'})")
    for key in ["phaseApplicability", "surfaces", "triggers", "successfulPaths", "avoidPaths", "importantReminders"]:
        values = summary.get(key) or []
        if values:
            preview = "; ".join(str(item) for item in values[:4])
            suffix = " ..." if len(values) > 4 else ""
            print(f"  {key}: {preview}{suffix}")

def cmd_knowledge(argv: list[str]) -> None:
    """Inspect deterministic knowledge index search/show results."""
    if not argv or argv[0] in {"help", "-h", "--help"}:
        print("Usage:")
        print("  knowledge list [--json]")
        print("  knowledge search <task-code> <phase> [--limit N] [--json]")
        print("  knowledge show <id> [--raw] [--json]")
        print("  knowledge evaluate [--json] [--strict]")
        return
    sub = argv[0]
    json_mode = "--json" in argv
    if sub == "list":
        records = load_knowledge_index()
        if json_mode:
            print(json.dumps([summarize_knowledge_record(record) for record in records], ensure_ascii=False, indent=2))
            return
        if not records:
            warn("No knowledge index records found. Expected .automind/summary/index.jsonl or summaries/index.jsonl.")
            return
        for record in records:
            _print_knowledge_record(record)
        return
    if sub == "search":
        positional = [arg for arg in argv[1:] if not arg.startswith("--")]
        if len(positional) < 2:
            error("Usage: knowledge search <task-code> <phase> [--limit N] [--json]")
            sys.exit(1)
        limit = 5
        if "--limit" in argv:
            idx = argv.index("--limit")
            try:
                limit = int(argv[idx + 1])
            except Exception:
                error("--limit requires an integer value")
                sys.exit(1)
        task_dir = get_task_dir(positional[0])
        if not task_dir.exists():
            error(f"Task does not exist: {positional[0]}")
            sys.exit(1)
        matches = search_knowledge(task_dir, positional[1], limit=limit)
        if json_mode:
            print(json.dumps([summarize_knowledge_record(record) for record in matches], ensure_ascii=False, indent=2))
            return
        if not matches:
            warn("No indexed knowledge matched this task/phase.")
            return
        for record in matches:
            _print_knowledge_record(record)
        return
    if sub == "evaluate":
        report = evaluate_knowledge_store()
        if json_mode:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("Knowledge evaluation")
            print(f"- ok: {report.get('ok')}")
            print(f"- indexes: {report.get('indexCount')}")
            print(f"- records: {report.get('recordCount')}")
            print(f"- referenced raw: {report.get('referencedRawCount')}")
            counts = report.get("severityCounts") or {}
            print(f"- issues: {report.get('issueCount')} (errors={counts.get('error', 0)}, warnings={counts.get('warning', 0)}, info={counts.get('info', 0)})")
            for item in (report.get("issues") or [])[:80]:
                loc = item.get("path") or item.get("id") or "-"
                line = f":{item.get('line')}" if item.get("line") is not None else ""
                print(f"  - [{item.get('severity')}] {item.get('code')}: {loc}{line} - {item.get('message')}")
            if len(report.get("issues") or []) > 80:
                print(f"  ... {len(report.get('issues') or []) - 80} more issues; rerun with --json for full output")
        if "--strict" in argv and not report.get("ok"):
            sys.exit(1)
        return
    if sub == "show":
        positional = [arg for arg in argv[1:] if not arg.startswith("--")]
        if not positional:
            error("Usage: knowledge show <id> [--raw] [--json]")
            sys.exit(1)
        record = find_knowledge_record(positional[0])
        if not record:
            error(f"Knowledge record not found: {positional[0]}")
            sys.exit(1)
        if json_mode:
            data = summarize_knowledge_record(record)
            if "--raw" in argv:
                data["rawExcerpt"] = read_raw_excerpt(record)
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return
        _print_knowledge_record(record)
        if "--raw" in argv:
            excerpt = read_raw_excerpt(record)
            print("\n## Raw excerpt")
            if excerpt is None:
                warn("Raw file missing or not readable.")
            else:
                print(excerpt)
        return
    error(f"Unknown knowledge command: {sub}")
    sys.exit(1)

def cmd_summary_compact(keep_recent: int = 60, max_chars: int = 200_000) -> None:
    """\u538b\u7f29\u5168\u5c40 summary store\uff08\u53bb\u91cd + \u88c1\u526a\uff09\u907f\u514d\u65e0\u9650\u589e\u957f\u3002"""
    from orchestrator.summary import compact_global_summary_stores
    report = compact_global_summary_stores(
        keep_recent_tasks=int(keep_recent),
        max_chars=int(max_chars),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

def _preloaded_frontmatter_issues(path: Path) -> list[str]:
    """Return frontmatter issues for one preloaded summary file."""
    text = path.read_text(errors="ignore") if path.exists() else ""
    issues: list[str] = []
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        return ["missing_frontmatter"]
    raw = text[4:text.find("\n---\n", 4)]
    for field in ["name:", "description:", "use_when:", "solves:"]:
        if field not in raw:
            issues.append(f"missing_{field.rstrip(':')}")
    for list_field in ["use_when", "solves"]:
        idx = raw.find(f"{list_field}:")
        if idx != -1:
            tail = raw[idx:].splitlines()[1:]
            has_item = False
            for line in tail:
                if line and not line.startswith(" "):
                    break
                if line.strip().startswith("- "):
                    has_item = True
                    break
            if not has_item:
                issues.append(f"empty_{list_field}")
    return issues

def cmd_preloaded_check() -> None:
    """Validate preloaded summary naming, metadata, and Reuse.md discovery."""
    preloaded_root = AUTOMIND_ROOT / "summaries" / "preloaded"
    allowed_prefixes = ("common-", "client-", "ios-", "android-")
    issues: list[str] = []
    packs: list[dict] = []

    if not preloaded_root.exists():
        issues.append("summaries/preloaded missing")
    else:
        for child in sorted(preloaded_root.iterdir(), key=lambda p: p.name):
            if child.name == "README.md":
                continue
            if child.is_dir():
                issues.append(f"obsolete preloaded directory not allowed: {child}")
                continue
            if not child.is_file() or child.suffix != ".md":
                continue
            if child.stem in {"business", "technical"}:
                issues.append(f"obsolete subgroup not allowed: {child}")
            if not child.stem.startswith(allowed_prefixes):
                issues.append(f"invalid prefix: {child}")
            fm_issues = _preloaded_frontmatter_issues(child)
            for item in fm_issues:
                issues.append(f"{child}: {item}")
            packs.append({"name": child.stem, "path": rel_to_root(child), "frontmatterIssues": fm_issues})

    discovery: dict[str, list[str]] = {}
    context_checks: dict[str, dict] = {}
    for task_type in ["script", "ios", "android", "dual"]:
        files = preloaded_summary_files_for_task_type(task_type)
        names = [path.stem for path in files]
        discovery[task_type] = names
        context = build_preloaded_context(task_type, limit_chars=6_000)
        context_checks[task_type] = {
            "hasProgressiveIndex": bool(context and "Path: `summaries/preloaded/" in context and "read this file on demand" in context),
            "bytes": len(context),
        }
        if files and not context_checks[task_type]["hasProgressiveIndex"]:
            issues.append(f"{task_type}: generated context is not progressive index shaped")

    # Selection invariants.
    if any(name.startswith("ios-") for name in discovery.get("android", [])):
        issues.append("android discovery included ios-* pack")
    if any(name.startswith("android-") for name in discovery.get("ios", [])):
        issues.append("ios discovery included android-* pack")
    if not any(name.startswith("common-") for name in discovery.get("script", [])):
        issues.append("script discovery missing common-* pack")

    report = {
        "result": "pass" if not issues else "fail",
        "issueCount": len(issues),
        "issues": issues,
        "packs": packs,
        "discovery": discovery,
        "contextChecks": context_checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if issues:
        sys.exit(1)

def cmd_record_check(task_code: str):
    """[Diagnostic alias] \u68c0\u67e5 task \u8bb0\u5f55\u5b8c\u6574\u6027.

    `record-check` is now embedded in `completion-check` finalize, so calling
    this directly is mainly useful for diagnosing partial state. Prefer
    `completion-check` as the single Finish gate.
    """
    ok, issues = check_task_records(task_code)
    if ok:
        success(f"Record check passed: {task_code}")
        return
    warn(f"Record check found {len(issues)} issues: {task_code}")
    for issue in issues:
        print(f"- {issue}")
    sys.exit(1)

def cmd_notifications(task_code: str, limit: int | None = None):
    """Print the long-running task's pending user notifications.

    Notifications are produced by ``state.notify_user`` and stored at
    ``.automind/tasks/<task>/notifications.jsonl``. This command tails the
    file as JSON so external supervisors / users can see pending external
    blockers, pause/resume hints, and "ready for review" prompts without
    scraping iteration logs.
    """
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)
    entries = read_notifications(task_dir, limit=limit)
    if not entries:
        success(f"No pending notifications for {task_code}")
        return
    print(json.dumps({"task": task_code, "count": len(entries), "entries": entries}, ensure_ascii=False, indent=2))

def _doctor_classify(state: dict, stale_seconds: int) -> tuple[str, float | None]:
    """Return (verdict, age_seconds) for the given state.

    Mirrors the original verdict rules used in cmd_doctor's per-task report.
    """
    status = state.get("status") or "unknown"
    heartbeat = state.get("heartbeat") if isinstance(state.get("heartbeat"), dict) else {}
    last_beat = heartbeat.get("lastBeatAt")
    age_seconds: float | None = None
    if isinstance(last_beat, str):
        try:
            beat_dt = datetime.fromisoformat(last_beat)
            age_seconds = (datetime.now() - beat_dt).total_seconds()
        except ValueError:
            age_seconds = None

    if status in {"finished", "aborted"}:
        verdict = status
    elif status in {"paused", "paused_for_external", "pause_for_external"}:
        verdict = "paused"
    elif age_seconds is None:
        verdict = "unknown"
    elif age_seconds > stale_seconds:
        verdict = "stalled"
    else:
        verdict = "active"
    return verdict, age_seconds

def cmd_doctor_scan(stale_seconds: int = 600, auto_resume: bool = False, dry_run: bool = False, agent: str = "auto"):
    """Scan all tasks and prompt resume for recoverable (stalled) ones.

    Interactive prompt accepts y/n/all/skip. If ``auto_resume`` is True or stdin
    is not a TTY, all stalled tasks are auto-resumed without prompting.
    ``dry_run`` records the would-be resume calls in stdout instead of actually
    invoking ``cmd_resume`` (used by tests).
    """
    codes = list_tasks()
    stalled: list[str] = []
    for code in codes:
        td = get_task_dir(code)
        state = read_runtime_state(td) or {}
        verdict, _ = _doctor_classify(state, stale_seconds)
        if verdict == "stalled":
            stalled.append(code)

    print(f"doctor scan: {len(codes)} task(s), {len(stalled)} stalled")
    if not stalled:
        print("resume called: 0")
        print("skipped: 0")
        return

    resume_count = 0
    skip_count = 0
    apply_all = auto_resume
    skip_all = False

    for code in stalled:
        if skip_all:
            decision = "n"
        elif apply_all:
            decision = "y"
        else:
            try:
                raw = input(f"[doctor] resume task '{code}'? [y/n/all/skip]: ").strip().lower()
            except EOFError:
                raw = "n"
            if raw in {"all", "a"}:
                apply_all = True
                decision = "y"
            elif raw in {"skip", "s"}:
                skip_all = True
                decision = "n"
            elif raw in {"y", "yes"}:
                decision = "y"
            else:
                decision = "n"

        if decision == "y":
            if dry_run:
                print(f"[doctor] dry-run resume: {code} (agent={agent})")
            else:
                from orchestrator.main import cmd_resume  # lazy to avoid command-module cycle
                cmd_resume(code, agent)
            resume_count += 1
        else:
            print(f"[doctor] skipped: {code}")
            skip_count += 1

    print(f"resume called: {resume_count}")
    print(f"skipped: {skip_count}")

def cmd_doctor(task_code: str, stale_seconds: int = 600):
    """Diagnose a long-running task without scanning iteration logs.

    Combines heartbeat freshness, task status, last notification, and the
    last few progress.log entries into a single JSON snapshot. External
    supervisors / users invoke this when a task has been running for a long
    time to decide whether to wait, resume, or ask the user for input.

    Verdict rules:
      - finished/aborted -> verdict matches status (no stall judgement).
      - status in {paused, paused_for_external} -> "paused".
      - heartbeat.lastBeatAt older than ``stale_seconds`` -> "stalled".
      - otherwise -> "active".
    """
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)

    state = read_runtime_state(task_dir) or {}
    status = state.get("status") or "unknown"
    heartbeat = state.get("heartbeat") if isinstance(state.get("heartbeat"), dict) else {}
    last_beat = heartbeat.get("lastBeatAt")
    now = datetime.now()
    age_seconds: float | None = None
    if isinstance(last_beat, str):
        try:
            beat_dt = datetime.fromisoformat(last_beat)
            age_seconds = (now - beat_dt).total_seconds()
        except ValueError:
            age_seconds = None

    if status in {"finished", "aborted"}:
        verdict = status
    elif status in {"paused", "paused_for_external", "pause_for_external"}:
        verdict = "paused"
    elif age_seconds is None:
        verdict = "unknown"
    elif age_seconds > stale_seconds:
        verdict = "stalled"
    else:
        verdict = "active"

    notifications = read_notifications(task_dir, limit=3)
    progress_tail: list[dict] = []
    progress_path = task_dir / "progress.log"
    if progress_path.exists():
        try:
            lines = progress_path.read_text(errors="ignore").splitlines()
            for raw in lines[-5:]:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    progress_tail.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        except Exception:
            progress_tail = []

    report = {
        "task": task_code,
        "verdict": verdict,
        "status": status,
        "iteration": state.get("iteration"),
        "currentOwner": state.get("currentOwner"),
        "nextAction": state.get("nextAction"),
        "heartbeat": {
            "lastBeatAt": last_beat,
            "owner": heartbeat.get("owner"),
            "note": heartbeat.get("note"),
            "ageSeconds": round(age_seconds, 1) if age_seconds is not None else None,
            "staleThresholdSeconds": stale_seconds,
        },
        "notificationsTail": notifications,
        "progressTail": progress_tail,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if verdict == "stalled":
        sys.exit(2)

def cmd_workflow_check(task_code: str):
    """Check CodeMind workflow artifact continuity."""
    task_dir = get_task_dir(task_code)
    ok, report = check_workflow_consistency(task_code)
    if task_dir.exists():
        refresh_phase_transition_summary(task_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if ok:
        success(f"Workflow check passed: {task_code}")
        return
    warn(f"Workflow check failed: {task_code}")
    sys.exit(1)

def cmd_workflow_contract(task_code: str):
    """Materialize and validate workflow.json without running the full workflow-check."""
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)
    path = write_workflow_contract(task_dir)
    data = json.loads(path.read_text())
    issues, warnings = validate_workflow_contract(task_dir, data)
    report = {
        "task": task_code,
        "path": rel_to_root(path),
        "result": "pass" if not issues else "fail",
        "issues": issues,
        "warnings": warnings,
        "contract": data,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if issues:
        warn(f"Workflow contract failed: {task_code}")
        sys.exit(1)
    success(f"Workflow contract generated: {rel_to_root(path)}")

def cmd_completion_check(task_code: str):
    """Check final testcase/AC coverage and evidence before accepting finish."""
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)
    current = read_evaluation_json(task_dir)
    if current is None:
        report, enriched = build_completion_report(task_dir, allow_synthesize_pass=False)
        ledger_path = write_completion_ledger(task_dir, report)
        enriched.setdefault("coverage", {})
        enriched["coverage"]["ledgerPath"] = rel_to_root(ledger_path)
    else:
        # Persist normalized coverage/testResults and, for false finish claims,
        # mutate evaluation.json the same way the harness final gate would.
        enriched, report = apply_completion_gate(
            task_dir,
            current,
            allow_synthesize_pass=False,
            fail_next_action="retry_generator",
        )
    if current is not None:
        write_evaluation_json(task_dir, enriched)
        apply_evaluation_result(task_dir, enriched)
    if report.get("result") == "pass":
        # Embed record-check finalize: reconcile Validation.md status marker
        # and surface any remaining marker mismatches as warnings (do not fail
        # the gate just for marker drift; completion proof itself already
        # passed).
        reconcile_validation_status(task_dir)
        marker_issues = validation_status_issues(task_dir)
        if marker_issues:
            report.setdefault("warnings", [])
            if isinstance(report.get("warnings"), list):
                report["warnings"].extend(marker_issues)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("result") == "pass":
        update_runtime_state(task_dir, status="finished", currentOwner="supervisor", nextAction="finish")
        refresh_phase_transition_summary(task_dir)
        success(f"Completion check passed: {task_code}")
        # Skill-mode automation: drop the active-task marker once the task
        # is proven done, so subsequent Hook invocations on this workspace
        # do not loop on a finished task.
        active = read_current_task()
        if active == task_code:
            clear_current_task()
        print_report_manifest(task_code, heading="Reports to inspect before final handoff")
        summary_reuse = build_summary_reuse_status(task_dir)
        if not summary_reuse.get("ok"):
            warn("Completion is proven, but summary/reuse memory is not generated yet. Run before final handoff:")
            print(f"- ./automind.sh summary {task_code}")
            print(f"- ./automind.sh record-check {task_code}  # diagnostic alias")
            for missing_path in summary_reuse.get("missing", []):
                print(f"- missing: {missing_path}")
        return
    warn(f"Completion check failed: {task_code}")
    print_report_manifest(task_code, heading="Reports to inspect before retry/replan")
    sys.exit(1)

def cmd_process_check(task_code: str, args: list[str]) -> None:
    task_dir = TASKS_DIR / task_code
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)
    as_json = "--json" in args
    no_write = "--no-write" in args
    report = run_process_eval(task_code, task_dir, write=not no_write)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_process_eval(report))
        if not no_write:
            success(f"Process eval written: {task_dir / 'process-eval.json'}")
    if report.get("result") == "fail" and "--soft" not in args:
        sys.exit(1)

def cmd_improve_suggestions(args: list[str]) -> None:
    limit = 80
    if "--limit" in args:
        idx = args.index("--limit")
        if idx + 1 < len(args):
            try:
                limit = int(args[idx + 1])
            except ValueError:
                error("--limit must be an integer")
                sys.exit(1)
    print(render_improve_suggestions(limit=limit))

def cmd_tick_iteration(task_code: str, phase: str = "generic") -> None:
    """Skill-mode automation: increment iteration counter and check budget.

    Skill mode lacks the orchestrator while-loop, so each Generator/Evaluator
    turn must opt-in to budget enforcement. Exits non-zero when the budget is
    exhausted so callers (or Hooks) can halt the agent before runaway costs.
    """
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)
    from orchestrator.session.ask_user import normalize_pending_question
    from orchestrator.session.answers import latest_pending_answer_matches_question

    pending = normalize_pending_question(task_dir)
    if pending and latest_pending_answer_matches_question(task_dir, pending) is None:
        info = {
            "task": task_code,
            "phase": phase,
            "nextAction": "ask_user",
            "question": pending.get("question"),
            "nextActionPrompt": "A pending CodeMind question exists and has no recorded answer. Halt this agent turn and ask the user; record the answer with automind answer before continuing.",
        }
        print(json.dumps(info, ensure_ascii=False, indent=2))
        sys.exit(2)

    info = tick_iteration(task_dir, phase=phase)
    info["task"] = task_code
    if info.get("budgetExhausted"):
        info["nextActionPrompt"] = (
            f"Iteration budget exhausted for {task_code}: {info['iteration']}/{info['budget']}. "
            "Halt the loop and escalate to ask_user(category=repeated_same_failure)."
        )
    else:
        info["nextActionPrompt"] = (
            f"Iteration {info['iteration']}/{info['budget']} ({phase}). "
            "Continue Generator/Evaluator turn as planned."
        )
    print(json.dumps(info, ensure_ascii=False, indent=2))
    if info.get("budgetExhausted"):
        sys.exit(2)

def cmd_summary(task_code: str, ai_agent: Optional[str] = None):
    """Generate task summary, optionally using AI refinement."""
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        return
    generate_summary(task_code, reason="manual", ai_agent=ai_agent)
    print_report_manifest(task_code, heading="Reports to inspect / share")
