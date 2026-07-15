from __future__ import annotations

import json

from orchestrator.session.conversation import (
    append_conversation_turn,
    read_conversation_state,
    start_generation,
)
from orchestrator.state import read_runtime_state, write_runtime_state


def test_cmd_converse_uses_primary_phase_without_user_message_pollution(
    tmp_path, monkeypatch, capsys
) -> None:
    import orchestrator.commands.session as session_cmd

    task_dir = tmp_path / "chat01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "chat01", "status": "chat"})
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _code: task_dir)
    monkeypatch.setattr(
        session_cmd,
        "resolve_agent",
        lambda _agent: ("codex", {"requested": "auto"}),
    )
    monkeypatch.setattr(
        session_cmd,
        "append_user_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("converse must not append to user-messages.json")
        ),
    )
    calls = []

    def fake_run(mode, agent, prompt, agent_task_dir, phase="generic", quiet=False):
        calls.append((mode, agent, prompt, agent_task_dir, phase, quiet))
        return 0, json.dumps(
            {
                "version": 1,
                "reply": "你好",
                "contextSummary": "用户开始自然交流。",
                "actions": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(session_cmd, "run_agent", fake_run)
    session_cmd.cmd_converse(
        "chat01",
        ["--text", "protocol prompt", "--user-text", "你好", "--agent", "auto"],
    )

    assert calls == [
        ("cli", "codex", "protocol prompt", task_dir, "conversation", True)
    ]
    assert not (task_dir / "user-messages.json").exists()
    state = read_conversation_state(task_dir)
    assert state["turnCount"] == 1
    assert state["contextSummary"] == "用户开始自然交流。"
    assert (task_dir / "conversation-turns.jsonl").exists()
    assert '"reply": "你好"' in capsys.readouterr().out
    turn = json.loads((task_dir / "conversation-turns.jsonl").read_text())
    assert turn["assistantReply"] == "你好"
    assert "actions" not in turn


def test_cmd_converse_healthy_session_sends_only_current_prompt(
    tmp_path, monkeypatch
) -> None:
    import orchestrator.commands.session as session_cmd

    task_dir = tmp_path / "chat02"
    task_dir.mkdir()
    write_runtime_state(
        task_dir,
        {
            "taskId": "chat02",
            "status": "chat",
            "agentSessions": {
                "primary": {
                    "agent": "codex",
                    "sessionId": "session-keep",
                    "executionMode": "conversation_read_only",
                }
            },
        },
    )
    state = start_generation(task_dir, read_conversation_state(task_dir), "initial")
    append_conversation_turn(
        task_dir,
        state,
        user_text="上一轮",
        assistant_output='{"contextSummary":"已有摘要","reply":"ok","actions":[]}',
        status="ok",
    )
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _code: task_dir)
    calls = []
    monkeypatch.setattr(
        session_cmd,
        "run_agent",
        lambda mode, agent, prompt, agent_task_dir, phase="generic", quiet=False: (
            calls.append((agent, prompt, phase)) or (0, '{"reply":"新回复","actions":[]}')
        ),
    )

    session_cmd.cmd_converse(
        "chat02",
        ["--text", "only-new-turn", "--user-text", "新消息", "--agent", "auto"],
    )
    assert calls == [("codex", "only-new-turn", "conversation")]


def test_cmd_converse_new_session_bootstraps_from_durable_recovery(
    tmp_path, monkeypatch
) -> None:
    import orchestrator.commands.session as session_cmd

    task_dir = tmp_path / "chat03"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "chat03", "status": "chat"})
    state = start_generation(task_dir, read_conversation_state(task_dir), "initial")
    append_conversation_turn(
        task_dir,
        state,
        user_text="之前的问题",
        assistant_output='{"contextSummary":"用户在讨论登录页","reply":"之前回复","actions":[]}',
        status="ok",
    )
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _code: task_dir)
    prompts = []
    monkeypatch.setattr(
        session_cmd,
        "run_agent",
        lambda mode, agent, prompt, agent_task_dir, phase="generic", quiet=False: (
            prompts.append(prompt) or (0, '{"reply":"继续","actions":[]}')
        ),
    )

    session_cmd.cmd_converse(
        "chat03",
        ["--text", "current protocol", "--user-text", "继续", "--agent", "codex"],
    )
    assert "Rolling conversation summary" in prompts[0]
    assert "用户在讨论登录页" in prompts[0]
    assert "之前的问题" in prompts[0]
    assert prompts[0].endswith("current protocol")


def test_cmd_converse_context_overflow_retries_once_with_recovery(
    tmp_path, monkeypatch
) -> None:
    import orchestrator.commands.session as session_cmd

    task_dir = tmp_path / "chat04"
    task_dir.mkdir()
    write_runtime_state(
        task_dir,
        {
            "taskId": "chat04",
            "status": "chat",
            "agentSessions": {
                "primary": {
                    "agent": "codex",
                    "sessionId": "session-full",
                    "executionMode": "conversation_read_only",
                }
            },
        },
    )
    state = start_generation(task_dir, read_conversation_state(task_dir), "initial")
    append_conversation_turn(
        task_dir,
        state,
        user_text="旧消息",
        assistant_output='{"contextSummary":"可恢复摘要","reply":"旧回复","actions":[]}',
        status="ok",
    )
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _code: task_dir)
    prompts = []

    def fake_run(mode, agent, prompt, agent_task_dir, phase="generic", quiet=False):
        prompts.append(prompt)
        if len(prompts) == 1:
            return 1, "ERROR: context window exceeded"
        return 0, '{"reply":"恢复成功","contextSummary":"新摘要","actions":[]}'

    monkeypatch.setattr(session_cmd, "run_agent", fake_run)
    session_cmd.cmd_converse(
        "chat04",
        ["--text", "current turn", "--user-text", "新消息", "--agent", "auto"],
    )

    assert prompts[0] == "current turn"
    assert "可恢复摘要" in prompts[1]
    assert prompts[1].endswith("current turn")
    runtime = read_runtime_state(task_dir) or {}
    assert not runtime.get("agentSessions", {}).get("primary")


def test_cmd_converse_internal_result_updates_last_turn_without_incrementing(
    tmp_path, monkeypatch
) -> None:
    import orchestrator.commands.session as session_cmd

    task_dir = tmp_path / "chat-internal"
    task_dir.mkdir()
    write_runtime_state(
        task_dir,
        {
            "taskId": "chat-internal",
            "status": "chat",
            "agentSessions": {
                "primary": {
                    "agent": "codex",
                    "sessionId": "session-keep",
                    "executionMode": "conversation_read_only",
                }
            },
        },
    )
    state = start_generation(task_dir, read_conversation_state(task_dir), "initial")
    append_conversation_turn(
        task_dir,
        state,
        user_text="测试通过了吗",
        assistant_output='{"reply":"我先查询。","contextSummary":"查询验证状态","actions":[]}',
        status="ok",
    )
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _code: task_dir)
    monkeypatch.setattr(
        session_cmd,
        "run_agent",
        lambda *_args, **_kwargs: (
            0,
            '{"reply":"测试已经通过。","contextSummary":"验证已通过","actions":[]}',
        ),
    )

    session_cmd.cmd_converse(
        "chat-internal",
        [
            "--text",
            "tool result prompt",
            "--user-text",
            "测试通过了吗",
            "--agent",
            "auto",
            "--internal",
        ],
    )

    updated = read_conversation_state(task_dir)
    assert updated["turnCount"] == 1
    assert updated["recentTurns"][-1]["userText"] == "测试通过了吗"
    assert updated["recentTurns"][-1]["assistantReply"] == "测试已经通过。"


def test_cmd_converse_record_only_persists_slash_input_without_agent_call(
    tmp_path, monkeypatch
) -> None:
    import orchestrator.commands.session as session_cmd

    task_dir = tmp_path / "chat-slash"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "chat-slash", "status": "chat"})
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _code: task_dir)
    monkeypatch.setattr(
        session_cmd,
        "run_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("record-only must not invoke an agent")
        ),
    )

    session_cmd.cmd_converse(
        "chat-slash",
        [
            "--text",
            "deterministic slash command",
            "--user-text",
            "/status",
            "--record-only",
        ],
    )
    state = read_conversation_state(task_dir)
    assert state["turnCount"] == 1
    assert state["recentTurns"][-1]["userText"] == "/status"
    assert state["recentTurns"][-1]["status"] == "pending_result"


def test_conversation_phase_is_primary_and_evaluator_stays_isolated(
    tmp_path, monkeypatch
) -> None:
    import orchestrator.agents as agents

    task_dir = tmp_path / "chat05"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "chat05", "status": "chat"})
    monkeypatch.setattr(agents, "_agent_bypass_approvals_enabled", lambda _task: False)

    conversation_cmd, conversation_meta = agents.build_agent_cli_command(
        "codex", "hello", task_dir, phase="conversation"
    )
    _, evaluator_meta = agents.build_agent_cli_command(
        "codex", "verify", task_dir, phase="evaluator"
    )

    assert conversation_meta["sessionRole"] == "primary"
    assert conversation_meta["sessionPolicy"] == "primary-persistent"
    assert conversation_meta["executionMode"] == "conversation_read_only"
    assert "--sandbox" in conversation_cmd
    assert "read-only" in conversation_cmd
    assert "--dangerously-bypass-approvals-and-sandbox" not in conversation_cmd
    assert evaluator_meta["sessionRole"] == "evaluator"
    assert evaluator_meta["sessionPolicy"] == "fresh-isolated"


def test_codex_and_claude_conversation_sessions_resume_read_only(
    tmp_path, monkeypatch
) -> None:
    import orchestrator.agents as agents

    monkeypatch.setattr(agents, "_claude_stream_json_enabled", lambda: False)
    cases = [
        (
            "codex",
            "session id: 019e0000-0000-7000-8000-000000000123\nOK",
            ["codex", "--ask-for-approval", "never", "exec", "resume"],
        ),
        (
            "claude",
            "OK",
            ["claude", "--print", "--permission-mode", "plan", "--tools", ""],
        ),
    ]
    for agent, output, expected_prefix in cases:
        task_dir = tmp_path / agent
        task_dir.mkdir()
        write_runtime_state(task_dir, {"taskId": agent, "status": "chat"})

        command1, meta1 = agents.build_agent_cli_command(
            agent, "first", task_dir, phase="conversation"
        )
        assert meta1["sessionAction"] == "new"
        assert meta1["executionMode"] == "conversation_read_only"
        assert "dangerously" not in " ".join(command1)
        agents.record_agent_session_after_run(task_dir, meta1, output, 0)
        recorded = (read_runtime_state(task_dir) or {})["agentSessions"]["primary"]
        assert recorded["role"] == "conversation_orchestrator"

        command2, meta2 = agents.build_agent_cli_command(
            agent, "second", task_dir, phase="conversation"
        )
        assert meta2["sessionAction"] == "resume"
        assert command2[: len(expected_prefix)] == expected_prefix


def test_conversation_never_falls_back_to_generator_bypass_when_primary_policy_disabled(
    tmp_path, monkeypatch
) -> None:
    import orchestrator.agents as agents

    task_dir = tmp_path / "restricted"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "restricted", "status": "chat"})
    monkeypatch.setenv("AUTOMIND_AGENT_SESSION_POLICY", "fresh")

    command, meta = agents.build_agent_cli_command(
        "codex", "hello", task_dir, phase="conversation"
    )
    assert meta["sessionPolicy"] == "primary-persistent"
    assert meta["executionMode"] == "conversation_read_only"
    assert "read-only" in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
