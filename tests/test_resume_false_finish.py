import json
from pathlib import Path

from orchestrator.state import read_runtime_state


def test_cmd_resume_reopens_finished_task_when_completion_failed(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "finished",
        "iteration": 3,
        "currentOwner": "generator",
        "nextAction": "finish",
        "completionCheck": "fail",
        "completionVerdict": {"result": "fail", "overridesRawEvaluation": True},
    }))
    (task_dir / "evaluation.json").write_text(json.dumps({
        "iteration": 3,
        "result": "pass",
        "nextAction": "finish",
    }))

    calls = {"loop": 0}
    monkeypatch.setattr(main, "get_task_dir", lambda _task_code: task_dir)
    monkeypatch.setattr(main, "run_harness_loop", lambda *a, **k: calls.__setitem__("loop", calls["loop"] + 1) or False)

    main.cmd_resume("task01", "codex", tui=False)

    state = read_runtime_state(task_dir) or {}
    assert calls["loop"] == 1
    assert state["status"] == "retry_pending"
    assert state["nextAction"] == "retry_generator"
    assert state["falseFinishRecovery"]["reason"] == "finished_without_completion_pass"


def test_cmd_resume_keeps_stable_finished_task_closed(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "finished",
        "iteration": 3,
        "currentOwner": "automind",
        "nextAction": "finish",
        "completionCheck": "pass",
        "completionVerdict": {"result": "pass", "overridesRawEvaluation": False},
    }))
    (task_dir / "completion-report.json").write_text(json.dumps({
        "result": "pass",
        "completionVerdict": {"result": "pass", "overridesRawEvaluation": False},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish", "iteration": 3},
        "testResults": [],
    }))

    calls = {"loop": 0}
    monkeypatch.setattr(main, "get_task_dir", lambda _task_code: task_dir)
    monkeypatch.setattr(main, "run_harness_loop", lambda *a, **k: calls.__setitem__("loop", calls["loop"] + 1) or False)

    main.cmd_resume("task01", "codex", tui=False)

    assert calls["loop"] == 0
    state = read_runtime_state(task_dir) or {}
    assert state["status"] == "finished"

def test_cmd_resume_reopens_completed_done_task_when_completion_failed(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "completed",
        "iteration": 1,
        "currentOwner": "ai",
        "nextAction": "done",
        "completionCheck": "fail",
        "completionVerdict": {"result": "fail", "overridesRawEvaluation": True},
    }))

    calls = {"loop": 0}
    monkeypatch.setattr(main, "get_task_dir", lambda _task_code: task_dir)
    monkeypatch.setattr(main, "run_harness_loop", lambda *a, **k: calls.__setitem__("loop", calls["loop"] + 1) or False)

    main.cmd_resume("task01", "codex", tui=False)

    state = read_runtime_state(task_dir) or {}
    assert calls["loop"] == 1
    assert state["status"] == "retry_pending"
    assert state["nextAction"] == "retry_generator"
    assert state["falseFinishRecovery"]["previousStatus"] == "completed"
    assert state["falseFinishRecovery"]["previousNextAction"] == "done"


def test_cmd_resume_reopens_ledger_only_pass_without_completion_report(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "finished",
        "iteration": 4,
        "currentOwner": "automind",
        "nextAction": "finish",
        "completionCheck": "pass",
        "completionVerdict": {"result": "pass"},
    }))
    (task_dir / "VerificationLedger.json").write_text(json.dumps({
        "result": "pass",
        "completionVerdict": {"result": "pass"},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish", "iteration": 4},
        "testResults": [],
    }))

    calls = {"loop": 0}
    monkeypatch.setattr(main, "get_task_dir", lambda _task_code: task_dir)
    monkeypatch.setattr(main, "run_harness_loop", lambda *a, **k: calls.__setitem__("loop", calls["loop"] + 1) or False)

    main.cmd_resume("task01", "codex", tui=False)

    state = read_runtime_state(task_dir) or {}
    assert calls["loop"] == 1
    assert state["status"] == "retry_pending"
    assert state["falseFinishRecovery"]["authoritativeCompletionPass"] is False


def test_recover_task_bound_agent_precedence() -> None:
    import orchestrator.main as main

    # Live primary session wins over policy and planner.
    assert main.recover_task_bound_agent({
        "agentSessions": {"primary": {"agent": "claude"}},
        "agentExecutionPolicy": {"agent": "codex"},
        "planner": {"agent": "trae"},
    }) == "claude"
    # Falls back to execution policy when there is no primary session.
    assert main.recover_task_bound_agent({
        "agentExecutionPolicy": {"agent": "claude"},
        "planner": {"agent": "codex"},
    }) == "claude"
    # Falls back to planner.agent when nothing else is recorded.
    assert main.recover_task_bound_agent({"planner": {"agent": "claude"}}) == "claude"
    # Nothing reusable -> None (caller keeps `auto`).
    assert main.recover_task_bound_agent({}) is None
    assert main.recover_task_bound_agent({"planner": {"agent": "unknown-agent"}}) is None


def test_cmd_resume_keeps_task_bound_agent_when_not_specified(tmp_path: Path, monkeypatch) -> None:
    """A claude-bound task resumed without an explicit agent must keep claude,
    not silently switch to codex via codex-first `auto` discovery."""
    import orchestrator.main as main

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "retry_pending",
        "iteration": 5,
        "currentOwner": "generator",
        "nextAction": "retry_generator",
        "planner": {"agent": "claude"},
        "agentExecutionPolicy": {"agent": "claude"},
        "agentSessions": {"primary": {"agent": "claude"}},
    }))

    seen = {"agent": None}
    monkeypatch.setattr(main, "get_task_dir", lambda _task_code: task_dir)
    monkeypatch.setattr(
        main,
        "run_harness_loop_with_safe_auto_resume",
        lambda task_code, agent="auto", **k: seen.__setitem__("agent", agent) or True,
    )

    # No explicit agent -> defaults to "auto" at the entrypoint.
    main.cmd_resume("task01", "auto", tui=False)
    assert seen["agent"] == "claude"


def test_cmd_resume_explicit_agent_overrides_task_binding(tmp_path: Path, monkeypatch) -> None:
    """An explicit agent argument is an intentional switch and must win."""
    import orchestrator.main as main

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "retry_pending",
        "iteration": 5,
        "currentOwner": "generator",
        "nextAction": "retry_generator",
        "planner": {"agent": "claude"},
        "agentExecutionPolicy": {"agent": "claude"},
    }))

    seen = {"agent": None}
    monkeypatch.setattr(main, "get_task_dir", lambda _task_code: task_dir)
    monkeypatch.setattr(
        main,
        "run_harness_loop_with_safe_auto_resume",
        lambda task_code, agent="auto", **k: seen.__setitem__("agent", agent) or True,
    )

    main.cmd_resume("task01", "codex", tui=False)
    assert seen["agent"] == "codex"
