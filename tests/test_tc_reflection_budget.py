from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_main():
    path = Path("orchestrator/main.py")
    spec = importlib.util.spec_from_file_location("automind_main", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tc_reflection_budget_counts_failed_test_results_once_per_iteration(tmp_path: Path) -> None:
    main = _load_main()
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    evaluation = {
        "iteration": 1,
        "result": "fail",
        "summary": "TC failed",
        "failedChecks": [],
        "testResults": [{"testCaseId": "TC-F01", "result": "fail", "required": True}],
        "nextAction": "retry_generator",
    }

    first = main.apply_tc_reflection_budget(task_dir, evaluation, 1)
    assert first["tcReflectionCounts"] == {"TC-F01": 1}

    second = main.apply_tc_reflection_budget(task_dir, evaluation, 1)
    assert second["tcReflectionCounts"] == {"TC-F01": 1}

    third = main.apply_tc_reflection_budget(task_dir, evaluation, 2)
    assert third["tcReflectionCounts"] == {"TC-F01": 2}


def test_tc_reflection_budget_exhaustion_blocks_retry(tmp_path: Path, monkeypatch) -> None:
    main = _load_main()
    monkeypatch.setattr(main, "MAX_REFLECTIONS_PER_TC", 2)
    # Keep the legacy immediate-ask_user semantics for this case.
    monkeypatch.setattr(main, "AUTONOMOUS_REPLAN_AFTER_BUDGET", 0)
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    evaluation = {
        "iteration": 1,
        "result": "fail",
        "summary": "TC keeps failing",
        "failedChecks": [{"name": "case", "category": "validation_failure", "reason": "TC-F02 failed"}],
        "nextAction": "retry_generator",
    }

    one = main.apply_tc_reflection_budget(task_dir, dict(evaluation), 1)
    assert one["nextAction"] == "retry_generator"

    two = main.apply_tc_reflection_budget(task_dir, dict(evaluation, iteration=2), 2)
    assert two["result"] == "blocked"
    assert two["nextAction"] == "ask_user"
    assert two["askUserQuestion"]["category"] == "repeated_same_failure"
    assert two["tcReflectionCounts"] == {"TC-F02": 2}
    assert any(check.get("category") == "repeated_same_failure" for check in two["failedChecks"])


def test_tc_reflection_budget_tries_autonomous_replan_before_ask_user(tmp_path: Path, monkeypatch) -> None:
    main = _load_main()
    monkeypatch.setattr(main, "MAX_REFLECTIONS_PER_TC", 2)
    monkeypatch.setattr(main, "AUTONOMOUS_REPLAN_AFTER_BUDGET", 2)
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    evaluation = {
        "iteration": 1,
        "result": "fail",
        "summary": "TC keeps failing",
        "failedChecks": [{"name": "case", "category": "build_failure", "reason": "TC-F02 libtool ExampleUIMacros failed"}],
        "nextAction": "retry_generator",
    }

    # iter 1: first failure, not yet exhausted.
    one = main.apply_tc_reflection_budget(task_dir, dict(evaluation, iteration=1), 1)
    assert one["nextAction"] == "retry_generator"

    # iter 2: same signature reaches budget -> autonomous replan #1, not ask_user.
    two = main.apply_tc_reflection_budget(task_dir, dict(evaluation, iteration=2), 2)
    assert two["nextAction"] == "replan"
    assert "nextActionPrompt" in two

    # iter 3: still same signature -> autonomous replan #2.
    three = main.apply_tc_reflection_budget(task_dir, dict(evaluation, iteration=3), 3)
    assert three["nextAction"] == "replan"

    # iter 4: autonomous replan budget exhausted -> ask_user.
    four = main.apply_tc_reflection_budget(task_dir, dict(evaluation, iteration=4), 4)
    assert four["result"] == "blocked"
    assert four["nextAction"] == "ask_user"
    assert four["askUserQuestion"]["category"] == "repeated_same_failure"


def test_tc_reflection_budget_changed_signature_resets_path(tmp_path: Path, monkeypatch) -> None:
    main = _load_main()
    monkeypatch.setattr(main, "MAX_REFLECTIONS_PER_TC", 2)
    monkeypatch.setattr(main, "AUTONOMOUS_REPLAN_AFTER_BUDGET", 2)
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    def eval_with(reason: str, iteration: int) -> dict:
        return {
            "iteration": iteration,
            "result": "fail",
            "summary": "TC failing",
            "failedChecks": [{"name": "case", "category": "build_failure", "reason": reason}],
            "nextAction": "retry_generator",
        }

    # Two iterations with DIFFERENT signatures must not exhaust the budget,
    # because a changed failure signature is real progress.
    first = main.apply_tc_reflection_budget(task_dir, eval_with("TC-F03 libtool ExampleUIMacros failed", 1), 1)
    assert first["nextAction"] == "retry_generator"
    second = main.apply_tc_reflection_budget(task_dir, eval_with("TC-F03 missing screenshot evidence proof", 2), 2)
    assert second["nextAction"] == "retry_generator"


def test_derive_failure_signature_stable_for_same_root_cause() -> None:
    main = _load_main()
    a = main.derive_failure_signature(
        {"failedChecks": [{"category": "build_failure", "reason": "TC-F01 libtool ExampleUIMacros link failed"}]},
        "TC-F01",
    )
    b = main.derive_failure_signature(
        {"failedChecks": [{"category": "build_failure", "reason": "TC-F01 libtool ExampleUIMacros link failed again"}]},
        "TC-F01",
    )
    c = main.derive_failure_signature(
        {"failedChecks": [{"category": "build_failure", "reason": "TC-F01 simulator runtime missing"}]},
        "TC-F01",
    )
    assert a == b
    assert a != c


def test_derive_failure_signature_stable_across_changing_log_paths() -> None:
    """P1-A: the same root cause must keep one signature even when the reason text
    embeds a changing log path / timestamp / iteration number. Otherwise the
    per-signature budget never converges on a genuine deadlock."""
    main = _load_main()
    e1 = {"failedChecks": [{"category": "build_failure", "reason": "ExampleUIMacros libtool failed; see logs/iter-17/build/build-log-2026-06-17T10-22.log"}]}
    e2 = {"failedChecks": [{"category": "build_failure", "reason": "ExampleUIMacros libtool failed; see logs/iter-19/build/build-log-2026-06-17T11-48.log"}]}
    sig1 = main.derive_failure_signature(e1, "TC-F01")
    sig2 = main.derive_failure_signature(e2, "TC-F01")
    assert sig1 == sig2
    # The volatile path/timestamp/iteration tokens must not appear in the signature.
    for noise in ("2026", "iter", "17", "19", "log", ".log", "/"):
        assert noise not in sig1


def test_derive_failure_signature_order_independent() -> None:
    """A reordered reason describing the same root cause yields the same signature."""
    main = _load_main()
    a = main.derive_failure_signature(
        {"failedChecks": [{"category": "build_failure", "reason": "libtool link error in ExampleUIMacros target"}]},
        "TC-F01",
    )
    b = main.derive_failure_signature(
        {"failedChecks": [{"category": "build_failure", "reason": "ExampleUIMacros target had a libtool link error"}]},
        "TC-F01",
    )
    assert a == b


def test_derive_failure_signature_distinguishes_shared_prefix_root_causes() -> None:
    """Two different root causes that share leading tokens must stay distinct, so a
    real change of failure mode still reads as progress."""
    main = _load_main()
    compile_fail = main.derive_failure_signature(
        {"failedChecks": [{"category": "build_failure", "reason": "apple archive bazel compile ExampleUIMacros"}]},
        "TC-F01",
    )
    signing_fail = main.derive_failure_signature(
        {"failedChecks": [{"category": "build_failure", "reason": "apple archive bazel codesign provisioning"}]},
        "TC-F01",
    )
    assert compile_fail != signing_fail


def test_build_replan_context_empty_without_history(tmp_path: Path) -> None:
    """P1-B: with no recorded failure signatures or ruled-out hypotheses the
    planner prompt must be unchanged (empty additive context)."""
    main = _load_main()
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    assert main.build_replan_context(task_dir) == ""


def test_build_replan_context_includes_repeated_signatures_and_ruled_out(tmp_path: Path) -> None:
    """P1-B: repeated failure signatures and ledger ruled-out/remaining hypotheses
    are folded into a 'do not repeat' block for the replanning planner."""
    import json

    main = _load_main()
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "t",
        "tcFailureSignatureCounts": {
            "TC-F01": {"build_failure:exampleuimacros-libtool": 3, "build_failure:other": 1},
        },
    }))
    (task_dir / "tc-attempts.json").write_text(json.dumps({
        "schema": "automind.tc_attempts.v1",
        "progressByTc": {
            "TC-F01": {
                "ruledOut": ["clean rebuild does not fix libtool"],
                "remainingHypotheses": ["try a different build target"],
            },
        },
    }))

    context = main.build_replan_context(task_dir)
    assert "do not repeat" in context.lower()
    # Repeated signature (count>=2) is listed; the count-1 signature is filtered out.
    assert "exampleuimacros-libtool" in context
    assert "build_failure:other" not in context
    assert "clean rebuild does not fix libtool" in context
    assert "try a different build target" in context


def test_testcase_ids_from_evaluation_extracts_failed_checks_and_results() -> None:
    main = _load_main()
    evaluation = {
        "testResults": [
            {"testCaseId": "TC-F01", "result": "pass"},
            {"testCaseId": "TC-F02", "result": "blocked"},
        ],
        "failedChecks": [
            {"name": "assert", "reason": "Failed required TC: TC-F03"},
            {"name": "batch", "testCaseIds": ["TC-F04", "not-a-tc"]},
        ],
    }
    assert main.testcase_ids_from_evaluation(evaluation) == {"TC-F02", "TC-F03", "TC-F04"}
