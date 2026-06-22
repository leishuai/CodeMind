import json
from pathlib import Path

from orchestrator.state import write_runtime_state


def test_agent_unavailable_and_timeout_are_safe_auto_recovery_categories() -> None:
    import orchestrator.main as main_mod

    assert main_mod.is_safe_auto_recovery_failure("agent_unavailable") is True
    assert main_mod.is_safe_auto_recovery_failure("agent_timeout") is True
    assert main_mod.is_safe_auto_recovery_failure("permission_denied") is False
    assert main_mod.is_safe_auto_recovery_failure("unauthorized_destructive") is False


def test_generator_agent_timeout_retries_without_ask_user(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "Requirements.md").write_text("# Requirements\n")
    (task_dir / "Validation.md").write_text("# Validation\n")
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "ready",
        "iteration": 0,
        "currentOwner": "generator",
        "nextAction": "run_generator",
        "planner": {"mode": "ai_test_planner"},
    })

    monkeypatch.setattr(main_mod, "get_task_dir", lambda _code: task_dir)
    monkeypatch.setattr(main_mod, "run_pre_build_workflow_gate", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_mod, "build_generator_context_pack", lambda _task_dir, _iteration, log_dir: {"markdownPath": log_dir / "generator-context.md", "jsonPath": log_dir / "generator-context.json", "validationOk": True, "validationIssues": []})
    monkeypatch.setattr(main_mod, "write_rendered_prompt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "run_agent", lambda *_args, **_kwargs: (1, "command timed out after 1s"))
    monkeypatch.setattr(main_mod, "finalize_task_records", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "ensure_summary_generated", lambda *_args, **_kwargs: None)

    assert main_mod.run_harness_loop("task01", agent="codex") is False

    evaluation = json.loads((task_dir / "evaluation.json").read_text())
    state = json.loads((task_dir / "runtime-state.json").read_text())

    assert evaluation["failedChecks"][0]["category"] == "agent_timeout"
    assert evaluation["nextAction"] == "retry_generator"
    assert "askUserQuestion" not in evaluation
    assert evaluation["autoRecovery"]["selected"] == "resume_after_recovery"
    assert state["status"] == "retry_pending"
    assert state["currentOwner"] == "generator"
    assert state["nextAction"] == "retry_generator"
    assert state.get("askUserQuestion") is None


def test_context_overflow_is_safe_and_classified() -> None:
    import orchestrator.main as main_mod

    output = "ERROR: Codex ran out of room in the model's context window. Start a new thread or clear earlier history before retrying."

    assert main_mod.classify_agent_execution_failure(output) == "agent_context_overflow"
    assert main_mod.is_safe_auto_recovery_failure("agent_context_overflow") is True




def test_context_overflow_in_persisted_log_overrides_timeout_output(tmp_path: Path) -> None:
    import orchestrator.main as main_mod

    log = tmp_path / "generator.log"
    log.write_text(
        "wrapper says command timed out\n"
        "ERROR: Codex ran out of room in the model's context window. "
        "Start a new thread or clear earlier history before retrying.\n"
    )

    assert main_mod.classify_agent_execution_failure_with_log(
        "command timed out after 7200s",
        log,
    ) == "agent_context_overflow"


def test_generator_context_overflow_clears_primary_session_and_retries_fresh(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "Requirements.md").write_text("# Requirements\n")
    (task_dir / "Validation.md").write_text("# Validation\n")
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "ready",
        "iteration": 0,
        "currentOwner": "generator",
        "nextAction": "run_generator",
        "planner": {"mode": "ai_test_planner"},
        "agentSessions": {
            "primary": {
                "agent": "codex",
                "sessionId": "019eaad7-ffbf-7d30-92ed-468da9de191b",
                "policy": "primary-persistent",
            }
        },
    })

    monkeypatch.setattr(main_mod, "get_task_dir", lambda _code: task_dir)
    monkeypatch.setattr(main_mod, "run_pre_build_workflow_gate", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_mod, "build_generator_context_pack", lambda _task_dir, _iteration, log_dir: {"markdownPath": log_dir / "generator-context.md", "jsonPath": log_dir / "generator-context.json", "validationOk": True, "validationIssues": []})
    monkeypatch.setattr(main_mod, "write_rendered_prompt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "run_agent", lambda *_args, **_kwargs: (1, "ERROR: Codex ran out of room in the model's context window. Start a new thread or clear earlier history before retrying."))
    monkeypatch.setattr(main_mod, "finalize_task_records", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "ensure_summary_generated", lambda *_args, **_kwargs: None)

    assert main_mod.run_harness_loop("task01", agent="codex") is False

    evaluation = json.loads((task_dir / "evaluation.json").read_text())
    state = json.loads((task_dir / "runtime-state.json").read_text())
    commands = (task_dir / "logs" / "iter-1" / "commands.md").read_text()

    assert evaluation["failedChecks"][0]["category"] == "agent_context_overflow"
    assert evaluation["nextAction"] == "retry_generator"
    assert evaluation["autoRecovery"]["selected"] == "fresh_session_resume"
    assert state["status"] == "retry_pending"
    assert state["agentSessions"].get("primary", {}).get("sessionId") in (None, "")
    assert "AUTOMIND_AGENT_SESSION_POLICY=fresh" in commands


def test_safe_auto_resume_supervisor_retries_without_user(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "retry_pending",
        "currentOwner": "generator",
        "nextAction": "retry_generator",
    })
    (task_dir / "evaluation.json").write_text(json.dumps({
        "iteration": 1,
        "result": "blocked",
        "summary": "network timeout",
        "failedChecks": [{"name": "agent_execution", "category": "agent_timeout", "reason": "timed out"}],
        "nextAction": "retry_generator",
        "autoRecovery": {"selected": "resume_after_recovery", "source": "generator_agent_failure"},
    }))

    calls = []

    def fake_loop(_task_code, agent="auto", mode="cli"):
        calls.append((agent, mode))
        if len(calls) == 1:
            return False
        write_runtime_state(task_dir, {
            "taskId": "task01",
            "status": "finished",
            "currentOwner": "automind",
            "nextAction": "finish",
        })
        return True

    monkeypatch.setattr(main_mod, "get_task_dir", lambda _code: task_dir)
    monkeypatch.setattr(main_mod, "run_harness_loop", fake_loop)

    assert main_mod.run_harness_loop_with_safe_auto_resume("task01", agent="codex", max_auto_resumes=2) is True
    assert len(calls) == 2
    events = [json.loads(line) for line in (task_dir / "events.jsonl").read_text().splitlines()]
    auto_events = [event for event in events if event.get("type") == "safe_auto_resume"]
    assert auto_events
    assert auto_events[-1]["data"]["category"] == "agent_timeout"


def test_safe_auto_resume_context_overflow_clears_primary_session(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "retry_pending",
        "currentOwner": "generator",
        "nextAction": "retry_generator",
        "agentSessions": {"primary": {"agent": "codex", "sessionId": "abc123"}},
    })
    (task_dir / "evaluation.json").write_text(json.dumps({
        "iteration": 1,
        "result": "blocked",
        "summary": "context overflow",
        "failedChecks": [{"name": "agent_execution", "category": "agent_context_overflow", "reason": "context window"}],
        "nextAction": "retry_generator",
        "autoRecovery": {"selected": "fresh_session_resume", "source": "generator_agent_failure"},
    }))

    calls = []

    def fake_loop(_task_code, agent="auto", mode="cli"):
        calls.append(1)
        return len(calls) >= 2

    monkeypatch.setattr(main_mod, "get_task_dir", lambda _code: task_dir)
    monkeypatch.setattr(main_mod, "run_harness_loop", fake_loop)

    assert main_mod.run_harness_loop_with_safe_auto_resume("task01", agent="codex", max_auto_resumes=1) is True
    state = json.loads((task_dir / "runtime-state.json").read_text())
    assert state["agentSessions"].get("primary", {}).get("sessionId") in (None, "")
    assert state["safeAutoResume"]["selected"] == "fresh_session_resume"


def test_safe_auto_resume_stops_at_limit(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "retry_pending",
        "currentOwner": "generator",
        "nextAction": "retry_generator",
    })
    (task_dir / "evaluation.json").write_text(json.dumps({
        "iteration": 1,
        "result": "blocked",
        "summary": "stalled",
        "failedChecks": [{"name": "agent_execution", "category": "agent_stalled_no_output", "reason": "idle"}],
        "nextAction": "retry_generator",
        "autoRecovery": {"selected": "resume_after_recovery", "source": "generator_agent_failure"},
    }))

    calls = []
    monkeypatch.setattr(main_mod, "get_task_dir", lambda _code: task_dir)
    monkeypatch.setattr(main_mod, "run_harness_loop", lambda *_args, **_kwargs: calls.append(1) and False)

    assert main_mod.run_harness_loop_with_safe_auto_resume("task01", agent="codex", max_auto_resumes=1) is False
    assert len(calls) == 2
    state = json.loads((task_dir / "runtime-state.json").read_text())
    assert state["safeAutoResume"]["status"] == "limit_reached"
    assert state["safeAutoResume"]["category"] == "agent_stalled_no_output"


def test_resume_evidence_synthesis_continues_to_generator_retry(tmp_path: Path, monkeypatch) -> None:
    """resume should not stop after evidence synthesis when completion still
    routes to retry_generator; it should continue into the Generator turn."""
    import orchestrator.main as main_mod

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "Requirements.md").write_text("# Requirements\n\n- R01 [AC-001] Do it\n")
    (task_dir / "TestCases.md").write_text("# TestCases\n\n- TC-F01 required runtime [AC-001]\n")
    (task_dir / "Validation.md").write_text("# Validation\n")
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "retry_pending",
        "iteration": 0,
        "currentOwner": "generator",
        "nextAction": "retry_generator",
        "planner": {
            "mode": "ai_test_planner",
            "needsUserInput": False,
            "preImplementationReview": {"decision": "auto_proceed", "questions": []},
        },
    })
    (task_dir / "evaluation.json").write_text(json.dumps({
        "iteration": 0,
        "result": "fail",
        "summary": "missing testResults",
        "failedChecks": [],
        "evidence": [],
        "nextAction": "retry_generator",
    }))

    called = {"generator": False}
    synthesized = {
        "iteration": 0,
        "result": "fail",
        "summary": "synthesized partial evidence",
        "failedChecks": [{"name": "runtime", "category": "validation_failure", "reason": "not proved"}],
        "evidence": [],
        "testResults": [{"testCaseId": "TC-F01", "result": "partial", "evidenceAssessment": {"verdict": "not_proved"}}],
        "nextAction": "retry_generator",
    }

    monkeypatch.setattr(main_mod, "get_task_dir", lambda _code: task_dir)
    monkeypatch.setattr(main_mod, "should_synthesize_evaluation", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_mod, "synthesize_evaluation_from_evidence", lambda *_args, **_kwargs: synthesized)
    monkeypatch.setattr(main_mod, "apply_completion_gate", lambda _task_dir, evaluation, **_kwargs: (evaluation, {"result": "fail"}))
    monkeypatch.setattr(main_mod, "run_pre_build_workflow_gate", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_mod, "build_generator_context_pack", lambda _task_dir, _iteration, log_dir: {"markdownPath": log_dir / "generator-context.md", "jsonPath": log_dir / "generator-context.json", "validationOk": True, "validationIssues": []})
    monkeypatch.setattr(main_mod, "write_rendered_prompt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "run_agent", lambda *_args, **_kwargs: called.__setitem__("generator", True) or (1, "command timed out after 1s"))
    monkeypatch.setattr(main_mod, "finalize_task_records", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "ensure_summary_generated", lambda *_args, **_kwargs: None)

    assert main_mod.run_harness_loop("task01", agent="codex") is False
    assert called["generator"] is True
