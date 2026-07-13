"""Shared session core tests for TUI/skill reuse."""
from __future__ import annotations

import json
from pathlib import Path

from orchestrator.session.answers import apply_user_answer, read_answers
from orchestrator.session.ask_user import normalize_pending_question
from orchestrator.session.events import append_event, read_events, render_timeline_events
from orchestrator.session.instructions import build_next_instruction
from orchestrator.state import get_tui_chat_task_code, read_runtime_state, seed_task_primary_session_from_tui_chat, write_runtime_state
from tests.test_runtime_proof_gate import _write_workflow_task


def test_events_keep_non_heartbeat_replace_keys_in_chronological_order(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    append_event(task_dir, "phase_status", "requirements working", replace_key="phase:requirements")
    append_event(task_dir, "log", "agent output")
    append_event(task_dir, "phase_status", "requirements done", replace_key="phase:requirements")

    rendered = render_timeline_events(read_events(task_dir))

    assert [item["message"] for item in rendered] == ["requirements working", "agent output", "requirements done"]


def test_apply_user_answer_persists_answer_and_unblocks_state(tmp_path: Path) -> None:
    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state["status"] = "human_input_pending"
    state["currentOwner"] = "human"
    state["nextAction"] = "ask_user"
    state["askUserQuestion"] = {
        "question": "Proceed?",
        "options": [{"id": "confirm", "label": "Confirm"}],
        "recommended": "confirm",
    }
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))

    pending = normalize_pending_question(task_dir)
    answer = apply_user_answer(task_dir, selected_option="confirm", answer_text="Yes, proceed.")
    new_state = json.loads((task_dir / "runtime-state.json").read_text())

    assert pending and pending["question"] == "Proceed?"
    assert answer["selectedOption"] == "confirm"
    assert read_answers(task_dir)[-1]["answerText"] == "Yes, proceed."
    assert new_state["status"] == "planned"
    assert new_state["nextAction"] == "run_test_planner"
    assert read_events(task_dir)[-1]["type"] == "user_answered"


def test_build_next_instruction_surfaces_latest_user_answer(tmp_path: Path, monkeypatch) -> None:
    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    import orchestrator.state as state_mod

    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")
    apply_user_answer(task_dir, selected_option="confirm", answer_text="Proceed with plan.")

    payload = build_next_instruction("task01", task_dir)

    assert payload["result"] == "ok"
    assert payload["latestUserAnswer"]["answerText"] == "Proceed with plan."
    assert "latest user answer" in payload["nextActionPrompt"]


def test_agent_io_streams_stdout_to_events(tmp_path: Path) -> None:
    from orchestrator.session.agent_io import stream_agent_command

    task_dir = tmp_path / "task"
    code, stdout, stderr = stream_agent_command(
        ["python3", "-c", "print('hello agent')"],
        task_dir=task_dir,
        agent="codex",
        phase="generator",
        timeout=10,
    )
    events = read_events(task_dir)

    assert code == 0
    assert "hello agent" in stdout
    assert stderr == ""
    assert any(event["type"] == "agent_output" and event["message"] == "hello agent" for event in events)
    assert [event["type"] for event in render_timeline_events(events)[:3]] == ["agent_started", "agent_output", "agent_done"]


def test_tui_owned_loop_prompts_initial_human_input(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.tui.session import run_tui_owned_loop

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state["status"] = "human_input_pending"
    state["currentOwner"] = "human"
    state["nextAction"] = "ask_user"
    state["askUserQuestion"] = {"question": "Proceed?", "options": [{"id": "confirm", "label": "Confirm"}]}
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    monkeypatch.setattr("builtins.input", lambda _prompt="": "confirm")
    calls = {"count": 0}

    def fake_loop() -> bool:
        calls["count"] += 1
        current = json.loads((task_dir / "runtime-state.json").read_text())
        current["status"] = "finished"
        current["nextAction"] = "finish"
        (task_dir / "runtime-state.json").write_text(json.dumps(current, ensure_ascii=False, indent=2))
        return True

    ok = run_tui_owned_loop("task01", task_dir, agent="codex", run_loop=fake_loop)

    assert ok is True
    assert calls["count"] == 1
    assert read_answers(task_dir)[-1]["selectedOption"] == "confirm"


def test_command_shell_normalizes_natural_ask() -> None:
    from orchestrator.tui.shell import _normalize_shell_line

    assert _normalize_shell_line("ask 添加一个埋点") == ["ask", "添加一个埋点", "auto"]
    assert _normalize_shell_line("ask 添加一个埋点 codex --tui") == ["ask", "添加一个埋点", "codex", "--tui"]
    assert _normalize_shell_line("automind status task01") == ["status", "task01"]


def test_latest_answer_prompt_context_and_delivery(tmp_path: Path) -> None:
    from orchestrator.session.answers import (
        apply_user_answer,
        latest_answer_prompt_context,
        mark_latest_answer_delivered,
    )

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    apply_user_answer(task_dir, answer_text="继续按Home方案", selected_option="confirm", answered_by="test")
    context = latest_answer_prompt_context(task_dir)
    assert "Latest CodeAutonomy user answer" in context
    assert "继续按Home方案" in context
    mark_latest_answer_delivered(task_dir, mode="generator_prompt")
    answers = read_answers(task_dir)
    assert answers[-1]["delivery"]["status"] == "delivered"
    assert latest_answer_prompt_context(task_dir) == ""


def test_formal_trace_builds_task_phase_and_event_spans(tmp_path: Path) -> None:
    from orchestrator.session.trace import build_trace, render_trace_text, write_trace

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    append_event(
        task_dir,
        "agent_done",
        "codex completed",
        phase="generator",
        source="agent_io",
        span_id="agent:generator:1",
        parent_span_id="phase:generator",
        action="run_agent",
        status="ok",
        evidence_refs=["logs/iter-1/generator.log"],
    )

    trace = build_trace("task01", task_dir)
    path_trace = write_trace("task01", task_dir)
    text = render_trace_text(trace)

    assert trace["schemaVersion"] == 1
    assert any(span["spanId"] == "task" for span in trace["spans"])
    assert any(span["spanId"] == "agent:generator:1" for span in trace["spans"])
    assert "Trace: task01" in text
    assert (task_dir / "trace.json").exists()
    assert path_trace["summary"]["spanCount"] >= trace["summary"]["spanCount"]


def test_process_eval_reports_process_findings(tmp_path: Path) -> None:
    from orchestrator.process_eval import render_process_eval, run_process_eval

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    report = run_process_eval("task01", task_dir, write=True)
    text = render_process_eval(report)

    assert report["schemaVersion"] == 1
    assert report["result"] in {"pass", "warn", "fail"}
    assert (task_dir / "process-eval.json").exists()
    assert (task_dir / "trace.json").exists()
    assert "Process Eval: task01" in text
    assert any(item["id"] == "trace-present" for item in report["passes"])


def test_user_message_context_delivery(tmp_path: Path) -> None:
    from orchestrator.session.messages import (
        append_user_message,
        mark_pending_user_messages_delivered,
        pending_user_messages_prompt_context,
        read_user_messages,
    )

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    message = append_user_message(task_dir, "请优先复用项目里的验证脚本", source="test")
    context = pending_user_messages_prompt_context(task_dir)
    assert message["delivery"]["status"] == "pending"
    assert "CodeAutonomy user messages" in context
    assert "请优先复用" in context
    mark_pending_user_messages_delivered(task_dir, mode="planner_prompt")
    assert read_user_messages(task_dir)[-1]["delivery"]["status"] == "delivered"
    assert pending_user_messages_prompt_context(task_dir) == ""


def test_shell_defaults_current_task_for_task_commands(monkeypatch) -> None:
    import orchestrator.tui.shell as shell

    monkeypatch.setattr(shell, "read_current_task", lambda: "task01")
    assert shell._normalize_shell_line("status") == ["status", "task01"]
    assert shell._normalize_shell_line("trace --json") == ["trace", "task01", "--json"]
    assert shell._normalize_shell_line("status explicit_task") == ["status", "explicit_task"]


def test_shell_natural_language_routes_to_current_task_message(monkeypatch) -> None:
    import orchestrator.tui.shell as shell

    monkeypatch.setattr(shell, "read_current_task", lambda: "task01")
    assert shell._natural_language_argv("where am i?") == ["message", "task01", "--text", "where am i?", "--resume", "auto"]


def test_shell_natural_language_without_current_task_uses_shared_tui_chat(monkeypatch) -> None:
    import orchestrator.tui.shell as shell

    monkeypatch.setattr(shell, "read_current_task", lambda: None)
    monkeypatch.setattr(shell, "ensure_tui_chat_task", lambda: None)
    monkeypatch.setattr(shell, "get_tui_chat_task_code", lambda: "__tui_chat__")
    assert shell._natural_language_argv("where am i?") == ["message", "__tui_chat__", "--text", "where am i?", "--resume", "auto"]


def test_tui_owned_loop_ctrl_c_pauses_task(tmp_path: Path) -> None:
    from orchestrator.tui.session import run_tui_owned_loop

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")

    def interrupted_loop() -> bool:
        raise KeyboardInterrupt

    ok = run_tui_owned_loop("task01", task_dir, agent="codex", run_loop=interrupted_loop)
    state = json.loads((task_dir / "runtime-state.json").read_text())

    assert ok is False
    assert state["status"] == "paused_by_user"
    assert state["interruptedByUser"] is True
    assert state["resumeRecovery"]["reason"] == "keyboard_interrupt"
    assert state["resumeRecovery"]["clearedPrimaryAgentSession"] is False

def test_seed_task_primary_session_from_tui_chat(tmp_path: Path, monkeypatch) -> None:
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "task01", "status": "planned"})

    chat_dir = tmp_path / get_tui_chat_task_code()
    chat_dir.mkdir()
    write_runtime_state(chat_dir, {
        "taskId": get_tui_chat_task_code(),
        "status": "chat",
        "agentSessions": {
            "primary": {
                "agent": "codex",
                "sessionId": "sess-123",
                "policy": "primary-persistent",
                "role": "planner_generator_repair",
            }
        },
    })

    monkeypatch.setattr("orchestrator.state.get_tui_chat_task_dir", lambda: chat_dir)

    assert seed_task_primary_session_from_tui_chat(task_dir, agent="auto") is True
    seeded = (read_runtime_state(task_dir) or {}).get("agentSessions", {}).get("primary", {})
    assert seeded.get("sessionId") == "sess-123"
    assert seeded.get("agent") == "codex"


def test_tui_chat_task_code_can_be_scoped_per_process_env(monkeypatch) -> None:
    from orchestrator.state import get_tui_chat_task_code

    monkeypatch.setenv("AUTOMIND_TUI_CHAT_TASK", "__tui_chat_proc_123")
    assert get_tui_chat_task_code() == "__tui_chat_proc_123"


def test_cmd_message_hidden_tui_chat_uses_direct_agent_chat(tmp_path: Path, monkeypatch, capsys) -> None:
    import orchestrator.commands.session as session_cmd

    task_code = "__tui_chat_test__"
    task_dir = tmp_path / task_code
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"taskId": task_code, "status": "chat"})

    monkeypatch.setattr(session_cmd, "get_task_dir", lambda code: task_dir)
    monkeypatch.setattr(session_cmd, "get_tui_chat_task_code", lambda: task_code)
    monkeypatch.setattr(session_cmd, "append_user_message", lambda *_args, **_kwargs: {"id": "user-message-001"})

    called = {}
    def fake_run_agent(mode, agent, prompt, agent_task_dir, phase="generator", quiet=False):
        called.update({"mode": mode, "agent": agent, "prompt": prompt, "task_dir": agent_task_dir, "phase": phase, "quiet": quiet})
        return 0, "codex\nYou are in /tmp/demo\nhook: Stop\ntokens used\n42"

    monkeypatch.setattr(session_cmd, "run_agent", fake_run_agent)

    session_cmd.cmd_message(task_code, ["--text", "where am i?", "--resume", "auto"], resume_callback=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resume_callback should not be called for hidden tui chat")))
    out = capsys.readouterr().out
    assert "User message recorded: user-message-001" in out
    assert "auto>" in out
    assert "You are in /tmp/demo" in out
    assert called == {
        "mode": "cli",
        "agent": "auto",
        "prompt": "where am i?",
        "task_dir": task_dir,
        "phase": "generator",
        "quiet": True,
    }


def test_cmd_resume_accepts_tui_flag(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"taskId": "task01", "status": "ready"})
    monkeypatch.setattr(main_mod, "get_task_dir", lambda _code: task_dir)
    called = {}
    monkeypatch.setattr(main_mod, "run_tui_owned_loop", lambda task_code, _task_dir, agent, run_loop: called.update({"task": task_code, "agent": agent}) or None)

    main_mod.cmd_resume("task01", "auto", tui=True)
    assert called == {"task": "task01", "agent": "auto"}


def test_extract_agent_reply_strips_codex_runtime_noise() -> None:
    from orchestrator.agents import extract_agent_reply

    noisy = """OpenAI Codex v0.131.0
--------
workdir: /tmp/demo
session id: 019e8838-6e52-7311-a566-c385db160ca2
--------
user
where am i
hook: UserPromptSubmit
codex
You are in:

`/tmp/demo`
hook: Stop
tokens used
123
"""
    assert extract_agent_reply("codex", noisy) == "You are in:\n\n`/tmp/demo`"


def test_cmd_help_includes_update_command(capsys) -> None:
    from orchestrator import main as main_mod

    main_mod.cmd_help()
    out = capsys.readouterr().out

    assert "update" in out
    assert "Update CodeAutonomy runtime" in out


def test_cmd_update_prefers_install_curl_bootstrap(tmp_path: Path, monkeypatch, capsys) -> None:
    from orchestrator import main as main_mod

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    bootstrap = runtime / "install-curl.sh"
    bootstrap.write_text("#!/usr/bin/env bash\n")
    installer = runtime / "install.sh"
    installer.write_text("#!/usr/bin/env bash\n")
    calls = []
    monkeypatch.setattr(main_mod, "AUTOMIND_ROOT", runtime)
    monkeypatch.setattr(main_mod.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))

    main_mod.cmd_update([])
    out = capsys.readouterr().out

    assert calls
    assert calls[0][0] == ["bash", str(bootstrap)]
    assert calls[0][1]["env"]["AUTOMIND_UPDATE"] == "1"
    assert "CodeAutonomy update complete" in out


def test_cmd_update_falls_back_to_local_install_without_bootstrap(tmp_path: Path, monkeypatch, capsys) -> None:
    from orchestrator import main as main_mod

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    installer = runtime / "install.sh"
    installer.write_text("#!/usr/bin/env bash\n")
    calls = []
    monkeypatch.setattr(main_mod, "AUTOMIND_ROOT", runtime)
    monkeypatch.setattr(main_mod.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))

    main_mod.cmd_update([])
    captured = capsys.readouterr()

    assert calls
    assert calls[0][0] == ["bash", str(installer)]
    assert calls[0][1]["cwd"] == str(runtime)
    assert "without fetching remote updates" in captured.out
    assert "CodeAutonomy local refresh complete" in captured.out


def test_cmd_update_git_free_runtime_sets_current_automind_home(tmp_path: Path, monkeypatch) -> None:
    from orchestrator import main as main_mod

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    bootstrap = runtime / "install-curl.sh"
    bootstrap.write_text("#!/usr/bin/env bash\n")
    (runtime / ".git").write_text(
        "CodeAutonomy runtime install is intentionally not a Git checkout.\n",
        encoding="utf-8",
    )
    calls = []
    monkeypatch.delenv("AUTOMIND_HOME", raising=False)
    monkeypatch.setattr(main_mod, "AUTOMIND_ROOT", runtime)
    monkeypatch.setattr(main_mod.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))

    main_mod.cmd_update([])

    assert calls
    assert calls[0][0] == ["bash", str(bootstrap)]
    assert calls[0][1]["env"]["AUTOMIND_HOME"] == str(runtime)


def test_cmd_update_source_checkout_does_not_bind_automind_home_to_source(tmp_path: Path, monkeypatch) -> None:
    from orchestrator import main as main_mod

    source = tmp_path / "source"
    source.mkdir()
    (source / ".git").mkdir()
    bootstrap = source / "install-curl.sh"
    bootstrap.write_text("#!/usr/bin/env bash\n")
    calls = []
    monkeypatch.delenv("AUTOMIND_HOME", raising=False)
    monkeypatch.setattr(main_mod, "AUTOMIND_ROOT", source)
    monkeypatch.setattr(main_mod.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))

    main_mod.cmd_update([])

    assert calls
    assert calls[0][0] == ["bash", str(bootstrap)]
    assert "AUTOMIND_HOME" not in calls[0][1]["env"]


def test_cmd_message_hidden_tui_chat_reuses_cached_agent(tmp_path: Path, monkeypatch, capsys) -> None:
    import orchestrator.commands.session as session_cmd

    task_code = "__tui_chat_cached__"
    task_dir = tmp_path / task_code
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {
        "taskId": task_code,
        "status": "chat",
        "agentSessions": {"primary": {"agent": "codex", "sessionId": "sess-1"}},
    })
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda code: task_dir)
    monkeypatch.setattr(session_cmd, "get_tui_chat_task_code", lambda: task_code)
    monkeypatch.setattr(session_cmd, "append_user_message", lambda *_args, **_kwargs: {"id": "user-message-002"})
    called = {}

    def fake_run_agent(mode, agent, prompt, agent_task_dir, phase="generator", quiet=False):
        called.update({"agent": agent, "quiet": quiet})
        return 0, "codex\nYou're welcome!\nhook: Stop\ntokens used\n12"

    monkeypatch.setattr(session_cmd, "run_agent", fake_run_agent)
    session_cmd.cmd_message(task_code, ["--text", "cool. thx", "--resume", "auto"])
    out = capsys.readouterr().out
    assert "codex>" in out
    assert "You're welcome!" in out
    assert called == {"agent": "codex", "quiet": True}


def test_tui_owned_loop_heartbeat_status_uses_replace_key(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.tui import session as tui_session

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state.update({
        "status": "generating",
        "currentOwner": "generator",
        "nextAction": "run_generator",
        "iteration": 1,
        "heartbeat": {"lastBeatAt": "2026-06-04T13:00:00", "owner": "generator", "note": "iter-1"},
    })
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    monkeypatch.setattr(tui_session, "render_tui_snapshot", lambda *_args, **_kwargs: "snapshot")

    stop = tui_session.threading.Event()
    thread = tui_session._start_heartbeat_status_thread("task01", task_dir, stop, interval=0.01)
    import time

    time.sleep(0.035)
    stop.set()
    thread.join(timeout=1)

    events = read_events(task_dir)
    heartbeat_events = [event for event in events if event.get("type") == "heartbeat_status"]
    rendered = render_timeline_events(events)
    rendered_heartbeat = [event for event in rendered if event.get("replaceKey") == "heartbeat:status"]

    updated_state = json.loads((task_dir / "runtime-state.json").read_text())

    assert len(heartbeat_events) >= 1
    assert len(rendered_heartbeat) == 1
    assert "owner=generator" in rendered_heartbeat[0]["message"]
    assert "lastBeatAge=" in rendered_heartbeat[0]["message"]
    assert updated_state.get("heartbeat", {}).get("lastBeatAt")
    assert updated_state.get("heartbeat", {}).get("lastBeatAt") != "2026-06-04T13:00:00"


def test_tui_snapshot_uses_heartbeat_event_fallback(tmp_path: Path) -> None:
    from orchestrator.tui.app import render_tui_snapshot

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state.update({"status": "evaluating", "currentOwner": "evaluator", "nextAction": "run_evaluator", "iteration": 5})
    state.pop("heartbeat", None)
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    append_event(task_dir, "heartbeat_status", "Heartbeat: task01 status=evaluating owner=evaluator", replace_key="heartbeat:status")

    out = render_tui_snapshot("task01", task_dir, show_logo=False)

    assert "Heartbeat:" in out
    assert "event-fallback" in out
    assert "Heartbeat: -" not in out


def test_command_shell_interrupts_child_without_traceback(monkeypatch, capsys) -> None:
    from orchestrator.tui import shell as shell_mod

    inputs = iter(["status task01", "exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

    class FakeProc:
        returncode = 130
        sent = []
        terminated = False
        killed = False

        def wait(self, timeout=None):
            if timeout is None:
                raise KeyboardInterrupt()
            return self.returncode

        def send_signal(self, sig):
            self.sent.append(sig)

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    fake = FakeProc()
    monkeypatch.setattr(shell_mod.subprocess, "Popen", lambda *_args, **_kwargs: fake)

    assert shell_mod.run_command_shell() == 0
    captured = capsys.readouterr()

    assert "Ctrl+C received" in captured.err
    assert "Traceback" not in captured.err
    assert fake.sent


def test_tui_owned_loop_prints_welcome_logo(tmp_path: Path, capsys) -> None:
    from orchestrator.tui.app import LOGO
    from orchestrator.tui.session import run_tui_owned_loop
    from orchestrator.version import automind_version_label

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")

    def fake_loop() -> bool:
        state = json.loads((task_dir / "runtime-state.json").read_text())
        state["status"] = "finished"
        state["nextAction"] = "finish"
        (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
        return True

    assert run_tui_owned_loop("task01", task_dir, agent="codex", run_loop=fake_loop) is True
    out = capsys.readouterr().out

    assert LOGO.splitlines()[0] in out
    assert automind_version_label() in out
    assert "TUI-owned CodeAutonomy session" in out
    assert out.count(automind_version_label()) == 1


def test_cmd_ask_tui_uses_owned_loop_not_background(monkeypatch, tmp_path: Path) -> None:
    from orchestrator import main as main_mod

    task_dir = tmp_path / "task"
    called = {}
    monkeypatch.setattr(main_mod, "scaffold_task_artifacts", lambda user_input: ("task01", task_dir))
    monkeypatch.setattr(main_mod, "seed_task_primary_session_from_tui_chat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "write_current_task", lambda task_code: called.setdefault("current", task_code))
    monkeypatch.setattr(main_mod, "run_tui_owned_loop", lambda task_code, task_dir_arg, agent, run_loop, **kwargs: called.update({"task": task_code, "taskDir": task_dir_arg, "agent": agent, "runLoopResult": run_loop(), "tuiKwargs": kwargs}) or True)
    monkeypatch.setattr(main_mod, "run_harness_loop", lambda task_code, agent="auto": called.update({"harness": (task_code, agent)}) or True)

    main_mod.cmd_ask("do thing", agent="codex", tui=True)

    assert called["current"] == "task01"
    assert called["task"] == "task01"
    assert called["taskDir"] == task_dir
    assert called["agent"] == "codex"
    assert called["harness"] == ("task01", "codex")


def test_tui_owned_loop_answers_pending_question_and_continues(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.tui import session as tui_session

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state.update({
        "status": "ready",
        "nextAction": "run_test_planner",
        "currentOwner": "planner",
        "askUserQuestion": {
            "question": "Choose target?",
            "options": [{"id": "use_real_device", "label": "Real", "impact": "best"}],
            "recommended": "use_real_device",
        },
    })
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    monkeypatch.setattr(tui_session, "render_tui_snapshot", lambda *_args, **_kwargs: "snapshot")
    answers = []
    monkeypatch.setattr(tui_session, "prompt_for_pending_answer", lambda *_args, **_kwargs: answers.append("use_real_device") or "use_real_device")

    calls = []

    def fake_loop() -> bool:
        calls.append("loop")
        state = json.loads((task_dir / "runtime-state.json").read_text())
        if len(calls) == 1:
            state.update({"status": "human_input_pending", "nextAction": "ask_user", "currentOwner": "human"})
        else:
            state.update({"status": "finished", "nextAction": "finish", "currentOwner": "automind"})
        (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
        return state["status"] == "finished"

    assert tui_session.run_tui_owned_loop("task01", task_dir, agent="codex", run_loop=fake_loop) is True
    assert calls == ["loop", "loop"]
    assert answers == ["use_real_device"]


def test_tui_snapshot_can_optionally_compact_agent_output(tmp_path: Path) -> None:
    from orchestrator.session.events import append_event
    from orchestrator.tui.app import _compact_timeline_events, render_tui_snapshot

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    append_event(task_dir, "agent_started", "codex started for planner", phase="planner", source="agent_io", replace_key="agent:planner")
    events = []
    for idx in range(50):
        event = append_event(task_dir, "agent_output", f"line {idx} " + ("x" * 420), phase="planner", source="codex")
        events.append(event)
    append_event(task_dir, "agent_done", "codex completed for planner", phase="planner", source="agent_io", replace_key="agent:planner")

    compacted = _compact_timeline_events(events, max_agent_output=12)
    out = render_tui_snapshot("task01", task_dir, limit=80)

    assert compacted[0]["type"] == "agent_output_compacted"
    assert "earlier agent output lines hidden" in compacted[0]["message"]
    assert "earlier agent output lines hidden in TUI" not in out
    assert "agent" in out
    assert "codex ›" in out
    assert "line 0" in out
    assert "line 49" in out
    assert "…" in out


def test_tui_snapshot_can_hide_logo(tmp_path: Path) -> None:
    from orchestrator.tui.app import LOGO, render_tui_snapshot
    from orchestrator.version import automind_version_label

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    out = render_tui_snapshot("task01", task_dir, show_logo=False)

    assert LOGO.splitlines()[0] not in out
    assert automind_version_label() not in out
    assert "Task:" in out


def test_runtime_language_instruction_follows_user_input_language() -> None:
    from orchestrator.reuse import apply_runtime_language_instruction, detect_preferred_language

    chinese_prompt = apply_runtime_language_instruction("body", "为MediaPlay模块新增埋点")
    english_prompt = apply_runtime_language_instruction("body", "Add a music stop analytics event")

    assert detect_preferred_language("为MediaPlay模块新增埋点") == "zh"
    assert "primarily Chinese" in chinese_prompt
    assert "Communicate with the user in Chinese" in chinese_prompt
    assert "body" in chinese_prompt
    assert "primarily English" in english_prompt


def test_tui_snapshot_formats_heartbeat_status_specially(tmp_path: Path) -> None:
    from orchestrator.session.events import append_event
    from orchestrator.tui.app import render_tui_snapshot

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    append_event(task_dir, "heartbeat_status", "Heartbeat: task01 status=planning owner=planner", replace_key="heartbeat:status")

    out = render_tui_snapshot("task01", task_dir, show_logo=False)

    assert "♥ heartbeat" in out
    assert "status=planning" in out


def test_tui_snapshot_formats_agent_grep_line_numbers(tmp_path: Path) -> None:
    from orchestrator.session.events import append_event
    from orchestrator.tui.app import render_tui_snapshot

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    append_event(task_dir, "agent_output", "13:- CodeAutonomy release readiness entry", phase="planner", source="codex")

    out = render_tui_snapshot("task01", task_dir, show_logo=False)

    assert "L13" in out
    assert "CodeAutonomy release readiness entry" in out


def test_render_timeline_events_only_replaces_heartbeat(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    append_event(task_dir, "agent_started", "planner started", replace_key="agent:planner")
    append_event(task_dir, "agent_done", "planner done", replace_key="agent:planner")
    append_event(task_dir, "heartbeat_status", "hb1", replace_key="heartbeat:status")
    append_event(task_dir, "heartbeat_status", "hb2", replace_key="heartbeat:status")

    rendered = render_timeline_events(read_events(task_dir))

    assert [event["type"] for event in rendered] == ["agent_started", "agent_done", "heartbeat_status"]
    assert rendered[-1]["message"] == "hb2"


def test_runtime_language_instruction_adds_single_agent_constraint() -> None:
    from orchestrator.reuse import apply_runtime_language_instruction

    prompt = apply_runtime_language_instruction("body", "为MediaPlay模块新增埋点")
    assert "Communicate with the user in Chinese" in prompt


def test_prompt_templates_preserve_agent_native_tool_usage() -> None:
    for rel in [
        Path("templates/phase2_planner_prompt.md"),
        Path("templates/generator_prompt.md"),
        Path("templates/evaluator_prompt.md"),
    ]:
        text = rel.read_text()
        assert "thin orchestration wrapper" in text
        assert "including native subagent/delegation features" in text
        assert "If an agent-native tool repeatedly fails" in text


def test_tui_snapshot_classifies_agent_output_kinds(tmp_path: Path) -> None:
    from orchestrator.session.events import append_event
    from orchestrator.tui.app import render_tui_snapshot

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    append_event(task_dir, "agent_output", "**Examining tool schema issues**", phase="planner", source="codex")
    append_event(task_dir, "agent_output", "I'm checking the current task artifacts", phase="planner", source="codex")
    append_event(task_dir, "agent_output", "2026 ERROR something failed", phase="planner", source="codex")
    append_event(task_dir, "agent_output", "13:- grep result", phase="planner", source="codex")

    out = render_tui_snapshot("task01", task_dir, show_logo=False)

    assert "progress:" in out
    assert "visible analysis:" in out
    assert "error:" in out
    assert "tool:" in out


def test_tui_snapshot_keeps_all_agent_output_by_default(tmp_path: Path) -> None:
    from orchestrator.session.events import append_event
    from orchestrator.tui.app import render_tui_snapshot

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    for idx in range(60):
        append_event(task_dir, "agent_output", f"line {idx}", phase="planner", source="codex")

    out = render_tui_snapshot("task01", task_dir, show_logo=False)

    assert "earlier agent output lines hidden" not in out
    assert "line 0" in out
    assert "line 59" in out


def test_parse_ask_args_joins_unquoted_natural_language() -> None:
    from orchestrator.main import parse_ask_args

    user_input, agent, force_tui, force_detached = parse_ask_args(["为MediaPlay模块新增", "music_audio_stop", "埋点"])

    assert user_input == "为MediaPlay模块新增 music_audio_stop 埋点"
    assert agent == "auto"
    assert not force_tui
    assert not force_detached


def test_parse_ask_args_uses_final_supported_agent_only() -> None:
    from orchestrator.main import parse_ask_args

    user_input, agent, force_tui, force_detached = parse_ask_args(["为Media", "新增", "codex", "--tui"])

    assert user_input == "为Media 新增"
    assert agent == "codex"
    assert force_tui
    assert not force_detached


def test_apply_user_answer_numeric_option_clears_pending_review_and_evaluation(tmp_path: Path) -> None:
    from orchestrator.session.answers import resolve_selected_option
    from orchestrator.state import read_evaluation_json

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    question = {
        "question": "Use device?",
        "options": [
            {"id": "use_real_device", "label": "Real"},
            {"id": "use_simulator_emulator", "label": "Emulator"},
        ],
        "recommended": "use_real_device",
    }
    state.update({"status": "human_input_pending", "currentOwner": "human", "nextAction": "ask_user", "askUserQuestion": question})
    state["planner"] = {
        "needsUserInput": True,
        "preImplementationReview": {"needsUserInput": True, "decision": "ask_user", "decisionBundle": {}},
    }
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    (task_dir / "evaluation.json").write_text(json.dumps({"nextAction": "ask_user", "askUserQuestion": question}, ensure_ascii=False, indent=2))

    assert resolve_selected_option(task_dir, "1") == "use_real_device"
    assert resolve_selected_option(task_dir, "answer task01 --option 1") == "use_real_device"
    answer = apply_user_answer(task_dir, answer_text="1")
    new_state = json.loads((task_dir / "runtime-state.json").read_text())
    evaluation = read_evaluation_json(task_dir)

    assert answer["selectedOption"] == "use_real_device"
    assert normalize_pending_question(task_dir) is None
    assert new_state["planner"]["needsUserInput"] is False
    assert new_state["planner"]["preImplementationReview"]["decision"] == "auto_proceed"
    assert new_state["nextAction"] == "run_test_planner"
    assert evaluation["askUserQuestion"] is None
    assert evaluation["nextAction"] == "retry_generator"


def test_pre_implementation_answer_is_one_shot_for_planner_state(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod
    from orchestrator.session.answers import has_resolved_pre_implementation_answer

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state.update({
        "status": "human_input_pending",
        "currentOwner": "human",
        "nextAction": "ask_user",
        "askUserQuestion": {
            "question": "Use device?",
            "options": [{"id": "use_real_device", "label": "Real"}],
        },
        "planner": {
            "needsUserInput": True,
            "preImplementationReview": {"needsUserInput": True, "decision": "ask_user"},
        },
    })
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    apply_user_answer(task_dir, answer_text="1")
    # Simulate a stale pre-refiner review surviving in state; the recorded answer should win.
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state["planner"]["needsUserInput"] = True
    state["planner"]["preImplementationReview"]["needsUserInput"] = True
    state["planner"]["preImplementationReview"]["decision"] = "ask_user"
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))

    monkeypatch.setattr(main_mod, "run_agent", lambda *_args, **_kwargs: (0, "planner ok"))
    monkeypatch.setattr(main_mod, "run_before_phase_hooks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "run_after_phase_hooks", lambda *_args, **_kwargs: None)

    assert has_resolved_pre_implementation_answer(task_dir) is True
    main_mod.run_ai_test_planner(task_dir, agent="codex")
    new_state = json.loads((task_dir / "runtime-state.json").read_text())

    assert new_state["planner"]["needsUserInput"] is False
    assert new_state["planner"]["preImplementationReview"]["needsUserInput"] is False
    assert new_state["planner"]["preImplementationReview"]["decision"] == "auto_proceed"


def test_pre_implementation_ask_question_bundles_direction_risk_and_device() -> None:
    from orchestrator.main import format_pre_implementation_ask_question

    text = format_pre_implementation_ask_question(
        [
            "Please confirm the Brainstorm/Spec direction before implementation: goal and scope.",
            "Confirm the concrete success criteria and verification evidence before implementation.",
            "Real-device-first policy: CodeAutonomy detected connected physical device(s): ANDROID HRY.",
        ],
        {"reason": "Non-trivial implementation and device target need confirmation.", "approvalScope": "scope/risks/verification"},
    )

    assert "one consolidated confirmation" in text
    assert "implementation direction" in text
    assert "required AC/TestCase/evidence expectations" in text
    assert "Real-device-first policy" in text
    assert "scope/risks/verification" in text


def test_scaffold_does_not_ask_before_phase2_refiner(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod
    import orchestrator.state as state_mod

    monkeypatch.setattr(state_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")
    monkeypatch.setattr(main_mod, "TASKS_DIR", tmp_path / ".automind" / "tasks")
    monkeypatch.setattr(main_mod, "discover_connected_physical_devices", lambda _task_type: {"devices": [{"label": "ANDROID HRY"}], "mocked": False})

    _task_code, task_dir = main_mod.scaffold_task_artifacts("为 Android MediaPlay新增 music_audio_stop 埋点")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    review = state["planner"]["preImplementationReview"]

    assert state["currentOwner"] == "planner"
    assert state["nextAction"] == "run_generator"
    assert state.get("askUserQuestion") is None
    assert state["planner"]["needsUserInput"] is False
    assert review["decision"] == "auto_proceed"
    assert review["needsUserInput"] is False
    assert review["defaultPolicy"] == "scaffold_auto_approved"
    assert review["source"] == "deterministic_scaffold"
    assert "formal pre-implementation review" in review["reason"]


def test_formal_pre_implementation_policy_can_still_ask_for_non_trivial_work(monkeypatch) -> None:
    import orchestrator.main as main_mod

    monkeypatch.setattr(main_mod, "discover_connected_physical_devices", lambda _task_type: {"devices": [], "mocked": True})

    review = main_mod.build_pre_implementation_review_state("为 Android MediaPlay新增 music_audio_stop 埋点")

    assert review["decision"] == "ask_user"
    assert review["needsUserInput"] is True
    assert review["defaultPolicy"] == "ask_user_for_non_trivial_implementation"
    assert review["source"] == "pre_implementation_review_policy"


def test_mobile_review_defaults_to_real_device_without_ask_when_device_detected(monkeypatch) -> None:
    import orchestrator.main as main_mod

    monkeypatch.setattr(
        main_mod,
        "discover_connected_physical_devices",
        lambda _task_type: {"devices": [{"label": "Shuai iPhone"}], "mocked": False},
    )

    review = main_mod.build_pre_implementation_review_state("为 iOS App MediaPlay新增 music_audio_stop 埋点并做 screenshot 验证")

    assert review["decision"] == "ask_user"
    assert review["needsUserInput"] is True
    assert not any("Real-device-first policy" in q for q in review["questions"])
    assert review["decisionBundle"]["verificationTarget"] == "real_device"
    assert review["decisionBundle"]["askOnDeviceMissing"] is False
    assert review["decisionBundle"]["requiresDeviceSelection"] is False
    assert review["decisionBundle"]["deviceSelectionPolicy"] == "default_single_connected_real_device"
    assert review["decisionBundle"]["screenshotEvidenceDefaultAllowed"] is True
    assert any("will use it by default" in q and "Screenshot capture is default allowed" in q for q in review["questions"])


def test_mobile_review_defaults_to_simulator_when_no_device(monkeypatch) -> None:
    import orchestrator.main as main_mod

    monkeypatch.setattr(main_mod, "discover_connected_physical_devices", lambda _task_type: {"devices": [], "mocked": False})

    review = main_mod.build_pre_implementation_review_state("为 iOS App MediaPlay新增 music_audio_stop 埋点并做 screenshot 验证")

    assert review["decision"] == "ask_user"
    assert review["needsUserInput"] is True
    assert review["recommendedOption"] == "use_simulator_emulator"
    assert review["decisionBundle"]["verificationTarget"] == "simulator_emulator"
    assert review["decisionBundle"]["askOnDeviceMissing"] is False
    assert review["decisionBundle"]["requiresDeviceSelection"] is False
    assert review["decisionBundle"]["deviceSelectionPolicy"] == "default_simulator_emulator_when_real_device_unavailable"
    assert review["decisionBundle"]["realDeviceFallbackPolicy"] == "try_simulator_emulator_by_default"
    assert review["decisionBundle"]["screenshotEvidenceDefaultAllowed"] is True
    assert review["decisionBundle"]["runtimeProofRequired"] == "yes"
    assert any("try simulator/emulator verification by default" in q and "Screenshot capture is default allowed" in q for q in review["questions"])


def test_mobile_review_asks_user_to_choose_when_multiple_devices_detected(monkeypatch) -> None:
    import orchestrator.main as main_mod

    monkeypatch.setattr(
        main_mod,
        "discover_connected_physical_devices",
        lambda _task_type: {"devices": [{"label": "Shuai iPhone"}, {"label": "Test iPhone"}], "mocked": False},
    )

    review = main_mod.build_pre_implementation_review_state("为 iOS App MediaPlay新增 music_audio_stop 埋点并做 screenshot 验证")

    assert review["decision"] == "ask_user"
    assert review["needsUserInput"] is True
    assert review["recommendedOption"] == "choose_real_device"
    assert review["decisionBundle"]["verificationTarget"] == "real_device_pending_selection"
    assert review["decisionBundle"]["requiresDeviceSelection"] is True
    assert review["decisionBundle"]["deviceSelectionPolicy"] == "ask_user_choose_connected_real_device"
    assert review["decisionBundle"]["screenshotEvidenceDefaultAllowed"] is True
    assert {"use_real_device_1", "use_real_device_2"}.issubset({option["id"] for option in review["options"]})
    assert any("Please choose which real device" in q for q in review["questions"])


def test_phase2_prompt_requires_merging_hard_and_soft_questions() -> None:
    text = Path("templates/phase2_planner_prompt.md").read_text()
    assert "deterministic scaffold may already contain hard/preflight questions" in text
    assert "model-derived soft questions" in text
    assert "one pre-implementation decision bundle" in text


def test_generate_task_code_uses_readable_technical_slug() -> None:
    from orchestrator.main import generate_task_code

    code = generate_task_code("为MediaPlay模块新增 music_audio_stop 埋点")

    assert code.startswith("mediaplay_music_audio_stop_")
    assert len(code.split("_")) >= 5


def test_generate_task_code_uses_chinese_keyword_slug_when_no_english() -> None:
    from orchestrator.main import generate_task_code

    code = generate_task_code("为MediaPlay模块新增停止埋点")

    assert code.startswith("mediaplay_")


def test_next_instruction_prefers_workflow_blocker_over_raw_next_action(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.session.instructions as instructions

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "planned",
        "currentOwner": "planner",
        "nextAction": "run_generator",
        "iteration": 0,
    }))
    workflow_state = {
        "overallStatus": "blocked",
        "currentPhase": "pre_implementation_review",
        "expectedNext": [{"phase": "pre_implementation_review", "reason": "gate"}],
        "blockedBy": [{"phase": "pre_implementation_review", "type": "pending_review"}],
    }
    monkeypatch.setattr(instructions, "check_workflow_consistency", lambda _task: (False, {"workflowState": workflow_state}))

    payload = instructions.build_next_instruction("task01", task_dir)

    assert payload["nextAction"] == "run_generator"
    assert payload["stateSummary"].get("nextPhase")
    assert payload["workflowSignal"] == workflow_state
    keys = list(payload.keys())
    assert keys.index("stateSummary") < keys.index("effectiveNext") < keys.index("workflowSignal")
    assert any("workflowSignal/workflowState as local resolver input" in item for item in payload["checklist"])
    assert "before following runtime-state nextAction=run_generator" in payload["nextActionPrompt"]


def test_next_instruction_ignores_stale_blocker_when_fresh_workflow_check_passed(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.session.instructions as instructions

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    (task_dir / "logs").mkdir(parents=True)
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "evaluating",
        "currentOwner": "evaluator",
        "nextAction": "run_evaluator",
        "iteration": 5,
    }))
    for name in ["Brainstorm.md", "Requirements.md", "TestCases.md", "Plan.md"]:
        (task_dir / name).write_text("ok")
    pass_report = {
        "result": "pass",
        "issues": [],
        "workflowState": {"result": "pass", "issueCount": 0, "expectedNext": []},
    }
    (task_dir / "logs" / "workflow-check-current.log").write_text(json.dumps(pass_report))
    blocked_state = {
        "result": "fail",
        "issueCount": 3,
        "currentPhase": "brainstorm",
        "expectedNext": [{"phase": "brainstorm"}],
    }
    monkeypatch.setattr(instructions, "check_workflow_consistency", lambda _task: (False, {"workflowState": blocked_state}))

    payload = instructions.build_next_instruction("task01", task_dir)

    assert payload["workflowState"].get("result") == "pass"
    assert "Workflow is blocked" not in payload["nextActionPrompt"]
    assert "completion-check" in payload["nextActionPrompt"] or "Effective next phase" in payload["nextActionPrompt"]


def test_next_instruction_ignores_cross_workspace_task_lookup_false_blocker(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.session.instructions as instructions

    task_dir = tmp_path / "other_repo" / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "evaluating",
        "currentOwner": "evaluator",
        "nextAction": "run_evaluator",
        "iteration": 5,
    }))
    monkeypatch.setattr(instructions, "check_workflow_consistency", lambda _task: (False, {"issues": ["Task does not exist: task01"], "workflowState": {}}))
    monkeypatch.setattr(instructions, "refresh_phase_transition_summary", lambda _task_dir: {
        "nextPhase": "planning",
        "nextAction": "run_test_planner",
        "nextOwner": "planner",
        "reason": "workflow-check has hard blockers",
        "basis": ["workflow-check failed", "Task does not exist: task01"],
    })

    payload = instructions.build_next_instruction("task01", task_dir)

    assert payload["workflowState"].get("result") == "pass"
    assert payload["effectiveNext"].get("phase") in {"evaluation", "run_evaluator", "completion"}
    assert "Workflow is blocked" not in payload["nextActionPrompt"]
    assert "completion-check" in payload["nextActionPrompt"] or "Effective next phase" in payload["nextActionPrompt"]


def test_tui_snapshot_shows_effective_next_and_running_status(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.tui.app as app
    from orchestrator.session.events import append_event

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "planning",
        "currentOwner": "planner",
        "nextAction": "run_generator",
        "iteration": 0,
        "heartbeat": {"lastBeatAt": "2026-06-04T21:27:34", "owner": "planner", "note": "phase2_refiner"},
    }))
    append_event(task_dir, "agent_output", "hook: PostToolUse Completed", phase="planner", source="codex")
    monkeypatch.setattr(
        app,
        "build_next_instruction",
        lambda _task, _dir: {
            "workflowState": {"overallStatus": "blocked", "currentPhase": "pre_implementation_review", "expectedNext": [{"phase": "pre_implementation_review"}], "blockedBy": [1, 2]},
            "stateSummary": {"nextPhase": "pre_implementation_review", "nextAction": "resolve_workflow_blockers", "nextOwner": "planner", "reason": "workflow-check blocker"},
            "effectiveNext": {"action": "resolve_workflow_blockers", "phase": "pre_implementation_review", "summary": "resolve pre_implementation_review blockers before runtime-state nextAction=run_generator"},
            "nextActionPrompt": "Workflow is blocked at pre_implementation_review.",
        },
    )

    out = app.render_tui_snapshot("task01", task_dir, show_logo=False)

    assert "Runtime next:" in out
    assert "Phase next:" in out
    assert "Effective next:" in out
    assert "workflow-check blocker" in out
    assert "Input disabled: CodeAutonomy is running." in out
    assert "Last heartbeat:" in out
    assert "Last event:" in out
    assert "quiet for several minutes" in out


def test_tui_snapshot_places_next_instruction_before_timeline(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.tui.app as app
    from orchestrator.session.events import append_event

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task01",
        "status": "planning",
        "currentOwner": "planner",
        "nextAction": "run_test_planner",
        "iteration": 0,
    }))
    append_event(task_dir, "agent_output", "codex router error", phase="planner", source="codex")
    monkeypatch.setattr(
        app,
        "build_next_instruction",
        lambda _task, _dir: {
            "workflowState": {"overallStatus": "blocked", "currentPhase": "pre_implementation_review", "expectedNext": [{"phase": "pre_implementation_review"}], "blockedBy": [1]},
            "stateSummary": {"nextPhase": "pre_implementation_review", "nextAction": "resolve_workflow_blockers", "reason": "workflow-check blocker"},
            "effectiveNext": {"summary": "resolve pre_implementation_review blockers before runtime-state nextAction=run_generator"},
            "nextActionPrompt": "Refine the artifact owning workflow-check issues, rerun workflow-check, and do not enter Generator until it passes.",
        },
    )

    out = app.render_tui_snapshot("task01", task_dir, show_logo=False)

    assert out.index("Next instruction:") < out.index("Timeline:")
    assert out.index("Next instruction:") < out.index("codex router error")


def test_android_device_discovery_uses_android_home_adb_when_path_missing(tmp_path: Path, monkeypatch) -> None:
    import os
    import stat
    import orchestrator.main as main_mod

    sdk = tmp_path / "sdk"
    adb = sdk / "platform-tools" / "adb"
    adb.parent.mkdir(parents=True)
    adb.write_text("#!/bin/sh\necho 'List of devices attached'\necho 'ABC123 device product:demo model:Pixel_8 device:oriole'\n")
    adb.chmod(adb.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(main_mod.shutil, "which", lambda _name: None)
    monkeypatch.setenv("ANDROID_HOME", str(sdk))

    result = main_mod.discover_android_physical_devices_with_diagnostics()

    assert result["devices"][0]["id"] == "ABC123"
    assert result["devices"][0]["name"] == "Pixel_8"
    assert result["diagnostics"]["strategy"] == "known_sdk_path"
    assert result["diagnostics"]["adbPath"] == str(adb)


def test_device_discovery_diagnostic_distinguishes_non_ready_device() -> None:
    from orchestrator.main import format_device_discovery_diagnostic

    text = format_device_discovery_diagnostic({
        "diagnostics": {
            "android": {
                "adbPath": "/tmp/adb",
                "error": "physical device(s) detected but not ready/authorized",
                "nonReadyDevices": [{"id": "ABC123", "state": "unauthorized"}],
            }
        }
    })

    assert "adb=/tmp/adb" in text
    assert "ABC123:unauthorized" in text
    assert "not ready/authorized" in text


def test_tui_input_enables_readline_before_delegating(monkeypatch) -> None:
    import builtins
    import orchestrator.tui.input as tui_input_mod

    calls = []
    tui_input_mod.enable_line_editing.cache_clear()
    monkeypatch.setattr(builtins, "input", lambda prompt="": calls.append(prompt) or "typed")
    monkeypatch.setattr(tui_input_mod, "enable_line_editing", lambda: calls.append("readline") or True)

    assert tui_input_mod.tui_input("prompt> ") == "typed"
    assert calls == ["readline", "prompt> "]


def test_tui_input_wraps_ansi_prompt_for_readline_width(monkeypatch) -> None:
    import builtins
    import orchestrator.tui.input as tui_input_mod
    from orchestrator.tui.style import BLUE, style

    prompts = []
    long_text = "ask long input that should keep readline prompt width stable"
    ansi_prompt = f"\033[1m{BLUE}automind> \033[0m"
    monkeypatch.setattr(builtins, "input", lambda prompt="": prompts.append(prompt) or long_text)
    monkeypatch.setattr(tui_input_mod, "enable_line_editing", lambda: True)

    assert tui_input_mod.tui_input(ansi_prompt) == long_text
    assert prompts
    assert "\001\033[" in prompts[0]
    assert "\002" in prompts[0]
    assert prompts[0].replace("\001", "").replace("\002", "") == ansi_prompt


def test_command_shell_uses_tui_input(monkeypatch) -> None:
    from orchestrator.tui import shell as shell_mod

    prompts = []
    values = iter(["exit"])
    monkeypatch.setattr(shell_mod, "tui_input", lambda prompt="": prompts.append(prompt) or next(values))

    assert shell_mod.run_command_shell() == 0
    assert prompts and "automind" in prompts[0]


def test_tui_terminal_input_preserves_bracketed_multiline_paste() -> None:
    import json
    import os
    import pty
    import select
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    result_path = Path('/tmp/automind_tui_input_result_pytest.json')
    result_path.unlink(missing_ok=True)
    master, slave = pty.openpty()
    code = textwrap.dedent(f"""
        import json
        import sys
        from pathlib import Path
        sys.path.insert(0, {str(Path.cwd())!r})
        from orchestrator.tui.input import _terminal_input
        value = _terminal_input('automind> ')
        Path({str(result_path)!r}).write_text(json.dumps({{'value': value}}))
    """)
    proc = subprocess.Popen([sys.executable, '-c', code], stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    try:
        initial = b''
        for _ in range(30):
            if select.select([master], [], [], 0.05)[0]:
                initial += os.read(master, 4096)
                if b'automind> ' in initial:
                    break
            if proc.poll() is not None:
                break
        assert b'automind> ' in initial, initial
        os.write(master, b'\x1b[200~first line\nsecond line\x1b[201~')
        rendered = b''
        for _ in range(20):
            if select.select([master], [], [], 0.05)[0]:
                rendered += os.read(master, 4096)
                if b'first line\\nsecond line' in rendered:
                    break
        assert b'first line\\nsecond line' in rendered
        os.write(master, b'\r')
        for _ in range(60):
            if proc.poll() is not None:
                break
            if select.select([master], [], [], 0.05)[0]:
                os.read(master, 4096)
        else:
            raise AssertionError('terminal input child did not exit')
    finally:
        try:
            os.close(master)
        except OSError:
            pass
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)

    assert proc.returncode == 0
    assert json.loads(result_path.read_text()) == {'value': 'first line\nsecond line'}


def test_tui_terminal_input_long_paste_stays_single_line_preview() -> None:
    import json
    import os
    import pty
    import select
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    result_path = Path('/tmp/automind_tui_input_long_result_pytest.json')
    result_path.unlink(missing_ok=True)
    master, slave = pty.openpty()
    code = textwrap.dedent(f"""
        import json
        import shutil
        import sys
        from pathlib import Path
        sys.path.insert(0, {str(Path.cwd())!r})
        import orchestrator.tui.input as input_mod
        input_mod.shutil.get_terminal_size = lambda fallback=(80, 20): shutil.os.terminal_size((40, 20))
        value = input_mod._terminal_input('automind> ')
        Path({str(result_path)!r}).write_text(json.dumps({{'value': value}}))
    """)
    proc = subprocess.Popen([sys.executable, '-c', code], stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    pasted = '\n'.join(f'line {i} with enough text to wrap badly' for i in range(12))
    try:
        initial = b''
        for _ in range(30):
            if select.select([master], [], [], 0.05)[0]:
                initial += os.read(master, 4096)
                if b'automind> ' in initial:
                    break
            if proc.poll() is not None:
                break
        assert b'automind> ' in initial, initial
        os.write(master, b'\x1b[200~' + pasted.encode() + b'\x1b[201~')
        rendered = b''
        for _ in range(20):
            if select.select([master], [], [], 0.05)[0]:
                rendered += os.read(master, 4096)
                if b'\xe2\x80\xa6' in rendered or b'line 11' in rendered:
                    break
        # The preview must not redraw by printing many physical wrapped prompt rows.
        assert rendered.count(b'automind> ') <= 1, rendered
        os.write(master, b'\r')
        for _ in range(60):
            if proc.poll() is not None:
                break
            if select.select([master], [], [], 0.05)[0]:
                os.read(master, 4096)
        else:
            raise AssertionError('terminal input child did not exit')
    finally:
        try:
            os.close(master)
        except OSError:
            pass
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)

    assert proc.returncode == 0
    assert json.loads(result_path.read_text()) == {'value': pasted}


def test_generator_ask_user_gate_pauses_before_evaluator(tmp_path: Path) -> None:
    import orchestrator.main as main_mod
    from orchestrator.state import read_runtime_state, write_evaluation_json

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    question = {
        "id": "ask-openspec-approval-001",
        "question": "Approve the concrete OpenSpec proposal?",
        "options": [{"id": "approve_proposal", "label": "Approve"}],
    }
    write_evaluation_json(task_dir, {
        "iteration": 2,
        "result": "blocked",
        "nextAction": "ask_user",
        "askUserQuestion": question,
    })

    assert main_mod.should_pause_after_generator_for_human_input(task_dir, 2) is True
    state = read_runtime_state(task_dir)
    assert state["status"] == "human_input_pending"
    assert state["currentOwner"] == "human"
    assert state["nextAction"] == "ask_user"
    assert state["askUserQuestion"]["id"] == "ask-openspec-approval-001"


def test_android_probe_flow_evaluator_called_without_undefined_retries(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    task_dir.mkdir(parents=True, exist_ok=True)
    calls = {}
    monkeypatch.setattr(main_mod, "get_task_dir", lambda _task_code: task_dir)
    monkeypatch.setattr(main_mod, "run_pre_build_workflow_gate", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_mod, "run_before_phase_hooks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "run_after_phase_hooks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "build_generator_context_pack", lambda _task_dir, _iteration, log_dir: {"markdownPath": log_dir / "generator-context.md", "jsonPath": log_dir / "generator-context.json", "validationOk": True, "validationIssues": []})
    monkeypatch.setattr(main_mod, "build_evaluator_context_pack", lambda _task_dir, _iteration, log_dir: {"markdownPath": log_dir / "evaluator-context.md", "jsonPath": log_dir / "evaluator-context.json", "validationOk": True, "validationIssues": []})
    monkeypatch.setattr(main_mod, "write_rendered_prompt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "run_agent", lambda *_args, **_kwargs: (0, "generator finished"))
    monkeypatch.setattr(main_mod, "finalize_task_records", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "ensure_summary_generated", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "append_validation_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "is_android_probe_flow_task", lambda _task_dir: True)
    monkeypatch.setattr(main_mod, "run_quality_evaluator", lambda *_args, **_kwargs: (None, "quality skipped"))
    monkeypatch.setattr(main_mod, "apply_completion_gate", lambda _task_dir, evaluation, **_kwargs: (evaluation, {"result": "pass", "issues": []}))

    def fake_android_evaluator(task_dir_arg, iteration, iter_log_dir, dry_run=False, force_flow=False, retries=0):
        calls["retries"] = retries
        main_mod.write_evaluation_json(task_dir_arg, {
            "iteration": iteration,
            "result": "pass",
            "summary": "ok",
            "failedChecks": [],
            "evidence": [],
            "nextAction": "finish",
        })
        return 0, "android evaluator ok"

    monkeypatch.setattr(main_mod, "run_android_probe_flow_evaluator", fake_android_evaluator)

    assert main_mod.run_harness_loop("task01", agent="codex") is True
    assert calls["retries"] == 0


def test_script_command_without_command_reroutes_android_task_to_probe_flow(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = read_runtime_state(task_dir)
    state.update({"taskType": "android", "scriptCommand": None, "verifyCommand": None})
    write_runtime_state(task_dir, state)
    calls = {"android": False}

    def fake_android(task_dir_arg, iteration, iter_log_dir, dry_run=False, force_flow=False, retries=0):
        calls["android"] = True
        main_mod.write_evaluation_json(task_dir_arg, {
            "iteration": iteration,
            "result": "partial",
            "summary": "probe pending",
            "failedChecks": [],
            "evidence": [],
            "nextAction": "retry_generator",
        })
        return 0, "android probe-flow"

    monkeypatch.setattr(main_mod, "run_android_probe_flow_evaluator", fake_android)

    code, output = main_mod.run_script_command_evaluator(task_dir, 3, task_dir / "logs" / "iter-3")

    assert code == 0
    assert calls["android"] is True
    assert "rerouting to android-probe-flow" in output
    evaluation = main_mod.read_evaluation_json(task_dir)
    assert evaluation["summary"] == "probe pending"


def test_evaluator_capability_surface_lists_script_command_only_when_explicit(tmp_path: Path) -> None:
    from orchestrator.context_packs import build_evaluator_capability_surface

    task_dir = _write_workflow_task(tmp_path, verification_target="unit")
    state = read_runtime_state(task_dir)
    state.update({"taskType": "script", "scriptCommand": None, "verifyCommand": None})
    write_runtime_state(task_dir, state)

    surface = build_evaluator_capability_surface(task_dir)
    names = [item["name"] for item in surface["deterministicEvaluators"]]
    assert "script-command" not in names

    state["verifyCommand"] = "pytest tests/test_demo.py"
    write_runtime_state(task_dir, state)
    surface = build_evaluator_capability_surface(task_dir)
    names = [item["name"] for item in surface["deterministicEvaluators"]]
    assert "script-command" in names


def test_script_command_without_explicit_command_reports_not_applicable(tmp_path: Path) -> None:
    import orchestrator.main as main_mod

    task_dir = _write_workflow_task(tmp_path, verification_target="unit")
    state = read_runtime_state(task_dir)
    state.update({"taskType": "script", "scriptCommand": None, "verifyCommand": None})
    write_runtime_state(task_dir, state)

    code, output = main_mod.run_script_command_evaluator(task_dir, 3, task_dir / "logs" / "iter-3")

    assert code == 0
    assert "not applicable" in output
    evaluation = main_mod.read_evaluation_json(task_dir)
    assert evaluation["result"] == "blocked"
    assert evaluation["failedChecks"][0]["category"] == "evaluator_route_unavailable"


def test_answer_help_does_not_record_answer(tmp_path: Path, capsys, monkeypatch) -> None:
    from orchestrator.commands.session import cmd_answer
    import orchestrator.commands.session as session_cmd

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _task_code: task_dir)

    cmd_answer("task01", ["--help"])

    out = capsys.readouterr().out
    assert "Usage: answer" in out
    assert not (task_dir / "user-answers.json").exists()


def test_setup_automation_tools_help_does_not_run_setup(capsys, monkeypatch) -> None:
    import sys

    import orchestrator.main as main_mod

    called = {"setup": False}
    monkeypatch.setattr(sys, "argv", ["orchestrator.py", "setup-automation-tools", "ios", "--help"])
    monkeypatch.setattr(
        main_mod,
        "cmd_setup_automation_tools",
        lambda *_args, **_kwargs: called.update({"setup": True}),
    )

    main_mod.main()

    out = capsys.readouterr().out
    assert "Usage: python orchestrator.py setup-automation-tools" in out
    assert "--help is help-only" in out
    assert called == {"setup": False}


def test_resume_normalizes_stale_evaluating_state_when_evaluation_asks_user(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod
    from orchestrator.state import read_runtime_state, write_evaluation_json

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = read_runtime_state(task_dir)
    state.update({"status": "evaluating", "currentOwner": "evaluator", "nextAction": "run_evaluator", "iteration": 2})
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    question = {"id": "ask-approval", "question": "Approve?", "options": [{"id": "approve", "label": "Approve"}]}
    write_evaluation_json(task_dir, {"iteration": 2, "result": "blocked", "nextAction": "ask_user", "askUserQuestion": question})
    monkeypatch.setattr(main_mod, "get_task_dir", lambda _task_code: task_dir)
    called = {"loop": False}
    monkeypatch.setattr(main_mod, "run_harness_loop", lambda *_args, **_kwargs: called.update({"loop": True}) or True)

    main_mod.cmd_resume("task01", agent="codex", tui=False)

    new_state = read_runtime_state(task_dir)
    assert called["loop"] is False
    assert new_state["status"] == "human_input_pending"
    assert new_state["nextAction"] == "ask_user"
    assert new_state["askUserQuestion"]["id"] == "ask-approval"


def test_resume_human_input_non_tty_prints_pending_question_guidance(tmp_path: Path, monkeypatch, capsys) -> None:
    import orchestrator.main as main_mod
    from orchestrator.state import read_runtime_state, write_evaluation_json

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = read_runtime_state(task_dir)
    question = {
        "question": "Approve proposal?",
        "options": [
            {"id": "approve_proposal", "label": "Approve proposal", "impact": "Proceed to implementation."},
            {"id": "hold_before_code", "label": "Hold before code"},
        ],
        "recommendedOption": "approve_proposal",
    }
    state.update({"status": "human_input_pending", "currentOwner": "human", "nextAction": "ask_user", "askUserQuestion": question})
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    write_evaluation_json(task_dir, {"iteration": 2, "result": "blocked", "nextAction": "ask_user", "askUserQuestion": question})
    monkeypatch.setattr(main_mod, "get_task_dir", lambda _task_code: task_dir)
    called = {"loop": False, "tui": False}
    monkeypatch.setattr(main_mod, "run_harness_loop", lambda *_args, **_kwargs: called.update({"loop": True}) or True)
    monkeypatch.setattr(main_mod, "run_tui_owned_loop", lambda *_args, **_kwargs: called.update({"tui": True}) or True)

    main_mod.cmd_resume("task01", agent="codex", tui=False)

    out = capsys.readouterr().out
    assert called == {"loop": False, "tui": False}
    assert "Question:" in out
    assert "Approve proposal?" in out
    assert "approve_proposal - Approve proposal" in out
    assert "Recommended option: approve_proposal" in out
    assert "automind answer task01 --option <option-id>" in out
    assert "automind resume task01 codex --tui" in out


def test_resume_human_input_tui_opens_answer_prompt_loop(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod
    from orchestrator.state import read_runtime_state

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = read_runtime_state(task_dir)
    state.update({"status": "human_input_pending", "currentOwner": "human", "nextAction": "ask_user", "askUserQuestion": {"question": "Proceed?"}})
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    monkeypatch.setattr(main_mod, "get_task_dir", lambda _task_code: task_dir)
    called = {}
    monkeypatch.setattr(main_mod, "run_tui_owned_loop", lambda task_code, task_dir_arg, agent, run_loop: called.update({"task": task_code, "agent": agent, "taskDir": task_dir_arg}) or True)

    main_mod.cmd_resume("task01", agent="codex", tui=True)

    assert called == {"task": "task01", "agent": "codex", "taskDir": task_dir}


def test_android_tools_python_falls_back_to_ready_runtime_venv(monkeypatch, tmp_path):
    import importlib
    import os

    from orchestrator import automation_tools

    workspace = tmp_path / "workspace"
    runtime = tmp_path / "runtime"
    project_py = workspace / ".venv-android-tools" / "bin" / "python"
    runtime_py = runtime / ".venv-android-tools" / "bin" / "python"
    project_py.parent.mkdir(parents=True)
    runtime_py.parent.mkdir(parents=True)
    project_py.write_text("")
    runtime_py.write_text("")
    project_py.chmod(0o755)
    runtime_py.chmod(0o755)

    monkeypatch.setattr(automation_tools, "ANDROID_TOOLS_VENV", workspace / ".venv-android-tools")
    monkeypatch.setattr(automation_tools, "AUTOMIND_RUNTIME_ROOT", runtime)
    monkeypatch.setattr(os, "access", lambda path, mode: True)
    monkeypatch.setattr(
        automation_tools,
        "android_tools_python_ready",
        lambda python_exec: str(python_exec) == str(runtime_py),
    )

    assert automation_tools.get_android_tools_python() == str(runtime_py)


def test_tui_ctrl_c_clears_primary_agent_session(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.tui.session import run_tui_owned_loop

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state["agentSessions"] = {"primary": {"agent": "codex", "sessionId": "sess-old", "policy": "primary-persistent"}}
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))

    def interrupted_loop() -> bool:
        raise KeyboardInterrupt

    ok = run_tui_owned_loop("task01", task_dir, agent="codex", run_loop=interrupted_loop)
    state = json.loads((task_dir / "runtime-state.json").read_text())

    assert ok is False
    assert state["status"] == "paused_by_user"
    assert state["resumeRecovery"]["clearedPrimaryAgentSession"] is True
    assert "primary" not in state.get("agentSessions", {})
    cleared = state["agentSessions"]["clearedPrimarySessions"][-1]
    assert cleared["sessionId"] == "sess-old"
    assert cleared["clearReason"] == "keyboard_interrupt"


def test_cmd_resume_paused_by_user_clears_stale_primary_session(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "paused_by_user",
        "currentOwner": "human",
        "nextAction": "resume_after_user_interrupt",
        "agentSessions": {"primary": {"agent": "codex", "sessionId": "sess-old", "policy": "primary-persistent"}},
    })
    monkeypatch.setattr(main_mod, "get_task_dir", lambda _code: task_dir)
    monkeypatch.setattr(main_mod, "run_harness_loop", lambda *_args, **_kwargs: True)

    main_mod.cmd_resume("task01", "codex", tui=False)
    state = read_runtime_state(task_dir) or {}

    assert state["status"] == "retry_pending"
    assert state["resumeRecovery"]["clearedPrimaryAgentSession"] is True
    assert "primary" not in state.get("agentSessions", {})
    assert state["agentSessions"]["clearedPrimarySessions"][-1]["clearReason"] == "resume_after_user_interrupt"


def test_cmd_resume_unanswered_task_state_question_prompts_before_generator(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "retry_pending",
        "currentOwner": "automind",
        "nextAction": "retry_generator",
        "askUserQuestion": {
            "id": "ask-env",
            "question": "Fix Android env?",
            "options": [{"id": "fix", "label": "Fix"}],
            "recommendedOption": "fix",
        },
    })
    (task_dir / "Requirements.md").write_text("# Requirements\n")
    monkeypatch.setattr(main_mod, "get_task_dir", lambda _code: task_dir)
    called = {}
    monkeypatch.setattr(main_mod, "run_harness_loop", lambda *_args, **_kwargs: called.update({"ran": True}) or True)

    main_mod.cmd_resume("task01", "codex", tui=False)
    state = read_runtime_state(task_dir) or {}

    assert called == {}
    assert state["status"] == "human_input_pending"
    assert state["currentOwner"] == "human"
    assert state["nextAction"] == "ask_user"


def test_run_harness_loop_stops_before_generator_when_task_state_question_unanswered(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.main as main_mod

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "Requirements.md").write_text("# Requirements\n")
    (task_dir / "Validation.md").write_text("# Validation\n")
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "retry_pending",
        "iteration": 1,
        "currentOwner": "automind",
        "nextAction": "retry_generator",
        "askUserQuestion": {"id": "ask-env", "question": "Fix Android env?", "options": []},
    })
    monkeypatch.setattr(main_mod, "get_task_dir", lambda _code: task_dir)
    monkeypatch.setattr(main_mod, "run_agent", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("generator should not run")))

    assert main_mod.run_harness_loop("task01", agent="codex") is False
    state = read_runtime_state(task_dir) or {}
    assert state["status"] == "human_input_pending"
    assert state["currentOwner"] == "human"
    assert state["nextAction"] == "ask_user"


def test_tui_hides_agent_output_when_waiting_for_human_answer(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.tui import app

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "human_input_pending",
        "currentOwner": "human",
        "nextAction": "ask_user",
        "iteration": 7,
        "askUserQuestion": {"id": "ask-env", "question": "Retry environment?", "options": [{"id": "retry", "label": "Retry"}]},
    })
    append_event(task_dir, "agent_output", "stale codex project analysis should not be the visual focus", phase="generator", source="codex")
    monkeypatch.setattr(
        app,
        "build_next_instruction",
        lambda _task, _dir: {
            "workflowState": {"overallStatus": "blocked", "currentPhase": "pre_implementation_review", "expectedNext": [], "blockedBy": [1]},
            "effectiveNext": {"summary": "ask_user"},
            "nextActionPrompt": "Ask the user the pending question.",
        },
    )

    out = app.render_tui_snapshot("task01", task_dir, show_logo=False)

    assert "Input enabled: CodeAutonomy needs user input." in out
    assert "Retry environment?" in out
    assert "stale codex project analysis" not in out
    assert "agent output lines hidden" in out


def test_codex_tui_decline_primary_session_with_approval_never_log_is_cleared_for_fresh_approval(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.agents import build_agent_cli_command

    task_dir = tmp_path / "task01"
    (task_dir / "logs" / "iter-7").mkdir(parents=True)
    (task_dir / "logs" / "iter-7" / "generator.log").write_text(
        "OpenAI Codex v0.131.0\napproval: never\nsession id: sess-old\n"
    )
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "agentSessions": {
            "primary": {
                "agent": "codex",
                "sessionId": "sess-old",
                "policy": "primary-persistent",
            }
        },
        "latestUserAnswer": {
            "selectedOption": "approval_capable_or_host_runner",
            "answerText": "Use a fresh approval-capable Codex session; do not resume approval=never.",
        },
        "codexDangerousBypass": {"enabled": False, "consent": "user_declined"},
    })
    monkeypatch.delenv("AUTOMIND_CODEX_ASK_FOR_APPROVAL", raising=False)

    cmd, meta = build_agent_cli_command("codex", "prompt", task_dir, phase="generator")
    state = read_runtime_state(task_dir) or {}

    assert meta["sessionAction"] == "new"
    assert cmd[:3] == ["codex", "--dangerously-bypass-approvals-and-sandbox", "exec"]
    assert "primary" not in state.get("agentSessions", {})
    cleared = state["agentSessions"]["clearedPrimarySessions"][-1]
    assert cleared["sessionId"] == "sess-old"
    assert cleared["clearReason"] == "agent_execution_mode_changed_to_bypass"


def test_codex_primary_session_not_cleared_when_desired_policy_is_never(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.agents import build_agent_cli_command

    task_dir = tmp_path / "task01"
    (task_dir / "logs" / "iter-7").mkdir(parents=True)
    (task_dir / "logs" / "iter-7" / "generator.log").write_text("approval: never\n")
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "agentSessions": {
            "primary": {
                "agent": "codex",
                "sessionId": "sess-old",
                "policy": "primary-persistent",
            }
        },
        "latestUserAnswer": {"selectedOption": "approval_capable_or_host_runner"},
        "codexDangerousBypass": {"enabled": False, "consent": "user_declined"},
    })
    monkeypatch.setenv("AUTOMIND_CODEX_ASK_FOR_APPROVAL", "never")

    cmd, meta = build_agent_cli_command("codex", "prompt", task_dir, phase="generator")

    assert meta["sessionAction"] == "new"
    assert cmd[:3] == ["codex", "--dangerously-bypass-approvals-and-sandbox", "exec"]


def test_codex_dangerous_bypass_is_explicit_task_consent(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.agents import build_agent_cli_command

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"codexDangerousBypass": {"enabled": True, "consent": "user_approved"}})

    cmd, meta = build_agent_cli_command("codex", "prompt", task_dir, phase="generator")

    assert meta["sessionAction"] == "new"
    assert cmd[:3] == ["codex", "--dangerously-bypass-approvals-and-sandbox", "exec"]
    assert "--sandbox" not in cmd
    assert "--ask-for-approval" not in cmd


def test_generator_falls_back_to_bypass_when_task_has_no_policy(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.agents import build_agent_cli_command

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)

    cmd, meta = build_agent_cli_command("codex", "prompt", task_dir, phase="generator")

    assert meta["sessionAction"] == "new"
    assert cmd[:3] == ["codex", "--dangerously-bypass-approvals-and-sandbox", "exec"]
    assert "--ask-for-approval" not in cmd
    assert "--sandbox" not in cmd


def test_codex_dangerous_bypass_primary_session_is_reused(tmp_path: Path) -> None:
    from orchestrator.agents import build_agent_cli_command, record_agent_session_after_run

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)

    write_runtime_state(task_dir, {"agentExecutionPolicy": {"bypassApprovals": True, "consent": "user_approved"}})
    cmd1, meta1 = build_agent_cli_command("codex", "planner", task_dir, phase="planner")
    record_agent_session_after_run(task_dir, meta1, "session id: 019e0000-0000-7000-8000-000000000abc\n", 0)
    cmd2, meta2 = build_agent_cli_command("codex", "generator", task_dir, phase="generator")

    assert cmd1[:3] == ["codex", "--dangerously-bypass-approvals-and-sandbox", "exec"]
    assert meta2["sessionAction"] == "resume"
    assert meta2["agentExecutionBypass"] is True
    assert cmd2[:4] == ["codex", "--dangerously-bypass-approvals-and-sandbox", "exec", "resume"]
    assert cmd2[4:7] == ["--skip-git-repo-check", "--", "019e0000-0000-7000-8000-000000000abc"]


def test_codex_resume_prompt_starting_with_dash_is_positional(tmp_path: Path) -> None:
    from orchestrator.agents import build_agent_cli_command, record_agent_session_after_run

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)

    write_runtime_state(task_dir, {"agentExecutionPolicy": {"bypassApprovals": True, "consent": "user_approved"}})
    cmd1, meta1 = build_agent_cli_command("codex", "planner", task_dir, phase="planner")
    record_agent_session_after_run(task_dir, meta1, "session id: 019e0000-0000-7000-8000-000000000def\n", 0)
    prompt = "- maxHeight = bottom - top - 40;\n+ coverLength = preferredCoverLength;"
    cmd2, meta2 = build_agent_cli_command("codex", prompt, task_dir, phase="generator")

    assert meta2["sessionAction"] == "resume"
    assert "--" in cmd2
    separator_index = cmd2.index("--")
    assert cmd2[separator_index + 1] == "019e0000-0000-7000-8000-000000000def"
    assert cmd2[separator_index + 2] == prompt


def test_codex_dangerous_bypass_respects_tui_decline(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.agents import build_agent_cli_command

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"codexDangerousBypass": {"enabled": False, "consent": "user_declined"}})

    cmd, _meta = build_agent_cli_command("codex", "prompt", task_dir, phase="generator")

    assert cmd[:3] == ["codex", "--dangerously-bypass-approvals-and-sandbox", "exec"]
    assert "--ask-for-approval" not in cmd
    assert "--sandbox" not in cmd


def test_codex_evaluator_always_bypasses_even_after_tui_decline(tmp_path: Path) -> None:
    from orchestrator.agents import build_agent_cli_command

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"codexDangerousBypass": {"enabled": False, "consent": "user_declined"}})

    cmd, meta = build_agent_cli_command("codex", "prompt", task_dir, phase="evaluator")

    assert meta["sessionPolicy"] == "fresh-isolated"
    assert meta["agentExecutionBypass"] is True
    assert meta["executionModeReason"] == "evaluator_always_bypassed"
    assert cmd[:3] == ["codex", "--dangerously-bypass-approvals-and-sandbox", "exec"]
    assert "--ask-for-approval" not in cmd
    assert "--sandbox" not in cmd


def test_tui_records_default_agent_execution_bypass_without_prompt(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.tui.session import _prompt_for_agent_execution_policy

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"taskId": "task01", "status": "created"})
    def fail_input(prompt=""):
        raise AssertionError("TUI should not prompt for default bypass")
    monkeypatch.setattr("orchestrator.tui.session.tui_input", fail_input)

    _prompt_for_agent_execution_policy("task01", task_dir, "codex")

    state = read_runtime_state(task_dir) or {}
    policy = state.get("agentExecutionPolicy")
    assert policy["bypassApprovals"] is True
    assert policy["consent"] == "default_bypass"
    assert policy["evaluatorAlwaysBypass"] is True
    legacy = state.get("codexDangerousBypass")
    assert legacy["enabled"] is True


def test_tui_does_not_prompt_and_defaults_bypass_on_empty_input(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.tui.session import _prompt_for_agent_execution_policy

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"taskId": "task01", "status": "created"})
    def fail_input(prompt=""):
        raise AssertionError("TUI should not prompt for default bypass")
    monkeypatch.setattr("orchestrator.tui.session.tui_input", fail_input)

    _prompt_for_agent_execution_policy("task01", task_dir, "codex")

    policy = (read_runtime_state(task_dir) or {}).get("agentExecutionPolicy")
    assert policy["bypassApprovals"] is True
    assert policy["consent"] == "default_bypass"


def test_tui_auto_agent_prompts_when_auto_resolves_to_supported_agent(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.tui.session import _prompt_for_agent_execution_policy

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"taskId": "task01", "status": "created"})
    def fail_input(prompt=""):
        raise AssertionError("TUI should not prompt for default bypass")
    monkeypatch.setattr("orchestrator.tui.session.tui_input", fail_input)
    monkeypatch.setattr("orchestrator.agents.resolve_agent", lambda agent: ("codex", {"category": "ok", "requested": agent}))

    _prompt_for_agent_execution_policy("task01", task_dir, "auto")

    policy = (read_runtime_state(task_dir) or {}).get("agentExecutionPolicy")
    assert policy["bypassApprovals"] is True
    assert policy["consent"] == "default_bypass"




def test_all_coding_agent_evaluators_are_fresh_and_bypassed(tmp_path: Path) -> None:
    from orchestrator.agents import build_agent_cli_command

    for agent, expected_flag in [
        ("codex", "--dangerously-bypass-approvals-and-sandbox"),
        ("claude", "--dangerously-skip-permissions"),
        ("trae", "--yolo"),
    ]:
        task_dir = tmp_path / agent
        task_dir.mkdir(parents=True)
        write_runtime_state(task_dir, {"codexDangerousBypass": {"enabled": False, "consent": "user_declined"}})

        cmd, meta = build_agent_cli_command(agent, "prompt", task_dir, phase="evaluator")

        assert meta["sessionPolicy"] == "fresh-isolated"
        assert meta["sessionAction"] == "fresh"
        assert expected_flag in cmd


def test_agent_idle_watchdog_stops_no_output_process(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.session.agent_io import stream_agent_command

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"taskId": "task01", "status": "running"})
    monkeypatch.setenv("AUTOMIND_AGENT_IDLE_TIMEOUT_SECONDS", "1")

    code, stdout, stderr = stream_agent_command(
        ["/bin/sh", "-c", "sleep 5"],
        task_dir=task_dir,
        agent="test-agent",
        phase="generator",
        timeout=30,
    )

    assert code == -4
    assert stdout == ""
    assert "stalled_no_output" in stderr


def test_claude_command_mode_falls_back_to_bypass_without_task_policy(tmp_path: Path) -> None:
    from orchestrator.agents import build_agent_cli_command

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)

    cmd, meta = build_agent_cli_command("claude", "prompt", task_dir, phase="generator")

    assert meta["sessionPolicy"] == "primary-persistent"
    assert meta["agentExecutionBypass"] is True
    assert "--dangerously-skip-permissions" in cmd


def test_claude_command_mode_uses_bypass_when_task_policy_allows(tmp_path: Path) -> None:
    from orchestrator.agents import build_agent_cli_command

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"agentExecutionPolicy": {"bypassApprovals": True, "consent": "user_approved"}})

    cmd, meta = build_agent_cli_command("claude", "prompt", task_dir, phase="generator")

    assert meta["agentExecutionBypass"] is True
    assert "--dangerously-skip-permissions" in cmd


def test_claude_primary_session_resumes_with_resume_flag(tmp_path: Path) -> None:
    from orchestrator.agents import build_agent_cli_command, record_agent_session_after_run

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)

    cmd1, meta1 = build_agent_cli_command("claude", "planner", task_dir, phase="planner")
    # First call creates a brand-new session via --session-id <uuid>.
    assert meta1["sessionAction"] == "new"
    assert "--session-id" in cmd1
    assert "--resume" not in cmd1
    session_id = meta1["sessionId"]

    record_agent_session_after_run(task_dir, meta1, "", 0)
    cmd2, meta2 = build_agent_cli_command("claude", "generator", task_dir, phase="generator")

    # Reusing the persisted primary session must use --resume, not --session-id,
    # otherwise claude rejects it with "Session ID ... is already in use".
    assert meta2["sessionAction"] == "resume"
    assert meta2["sessionId"] == session_id
    assert "--resume" in cmd2
    assert "--session-id" not in cmd2
    assert cmd2[cmd2.index("--resume") + 1] == session_id


def test_claude_command_enables_stream_json_by_default(tmp_path: Path) -> None:
    from orchestrator.agents import build_agent_cli_command

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)

    cmd, _meta = build_agent_cli_command("claude", "prompt", task_dir, phase="generator")

    assert "--print" in cmd
    assert "--output-format" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in cmd


def test_claude_command_stream_json_can_be_disabled(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.agents import build_agent_cli_command

    monkeypatch.setenv("AUTOMIND_CLAUDE_STREAM_JSON", "0")
    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)

    cmd, _meta = build_agent_cli_command("claude", "prompt", task_dir, phase="generator")

    assert "--print" in cmd
    assert "stream-json" not in cmd
    assert "--output-format" not in cmd


def test_claude_stream_decode_prefers_result_text() -> None:
    from orchestrator.agents import _claude_stream_decode_output

    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "abc"}) + "\n",
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "partial"}]}}) + "\n",
        json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "FINAL ANSWER"}) + "\n",
    ]

    assert _claude_stream_decode_output(lines) == "FINAL ANSWER"


def test_claude_stream_decode_falls_back_to_assistant_text() -> None:
    from orchestrator.agents import _claude_stream_decode_output

    lines = [
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}) + "\n",
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "world"}]}}) + "\n",
    ]

    assert _claude_stream_decode_output(lines) == "hello\nworld"


def test_claude_stream_decode_passes_through_non_json() -> None:
    from orchestrator.agents import _claude_stream_decode_output

    lines = ["plain text reply\n", "second line\n"]

    assert _claude_stream_decode_output(lines) == "plain text reply\nsecond line\n"


def test_claude_stream_display_extracts_assistant_text_and_suppresses_noise() -> None:
    from orchestrator.agents import _claude_stream_event_to_display

    assistant = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "thinking..."}]}})
    assert _claude_stream_event_to_display(assistant) == "thinking..."

    system = json.dumps({"type": "system", "subtype": "init"})
    assert _claude_stream_event_to_display(system) is None

    ok_result = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done"})
    assert _claude_stream_event_to_display(ok_result) is None

    assert _claude_stream_event_to_display("raw non-json line") == "raw non-json line"


def test_trae_command_mode_falls_back_to_yolo_without_task_policy(tmp_path: Path) -> None:
    from orchestrator.agents import build_agent_cli_command

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)

    cmd, meta = build_agent_cli_command("trae", "prompt", task_dir, phase="generator")

    assert meta["sessionPolicy"] == "primary-persistent"
    assert meta["agentExecutionBypass"] is True
    assert "--yolo" in cmd
    assert "--allowed-tool" in cmd


def test_trae_command_mode_uses_yolo_when_task_policy_allows(tmp_path: Path) -> None:
    from orchestrator.agents import build_agent_cli_command

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {"agentExecutionPolicy": {"bypassApprovals": True, "consent": "user_approved"}})

    cmd, meta = build_agent_cli_command("trae", "prompt", task_dir, phase="generator")

    assert meta["agentExecutionBypass"] is True
    assert "--yolo" in cmd


def test_declined_task_policy_still_uses_bypass_for_all_agents(tmp_path: Path) -> None:
    from orchestrator.agents import build_agent_cli_command

    for agent, forbidden in [
        ("codex", "--dangerously-bypass-approvals-and-sandbox"),
        ("claude", "--dangerously-skip-permissions"),
        ("trae", "--yolo"),
    ]:
        task_dir = tmp_path / agent
        task_dir.mkdir(parents=True)
        write_runtime_state(task_dir, {"agentExecutionPolicy": {"bypassApprovals": False, "consent": "user_declined"}})

        cmd, meta = build_agent_cli_command(agent, "prompt", task_dir, phase="generator")

        assert meta.get("agentExecutionBypass") is True
        assert forbidden in cmd

def test_build_next_instruction_surfaces_false_finish_recovery_for_invalid_evaluation(tmp_path: Path, monkeypatch) -> None:
    import orchestrator.session.instructions as instructions

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    (task_dir / "runtime-state.json").write_text(json.dumps({
        "status": "completed",
        "currentOwner": "ai",
        "nextAction": "done",
    }, ensure_ascii=False, indent=2))

    monkeypatch.setattr(
        instructions,
        "check_workflow_consistency",
        lambda _task_code: (
            False,
            {
                "workflowState": {
                    "currentPhase": "ai",
                    "expectedNext": [{"phase": "evaluation"}],
                    "blockedBy": [
                        {"phase": "evaluation", "issue": "evaluation.json missing nextAction"},
                        {"phase": "evaluation", "issue": "evaluation.json missing testResults"},
                    ],
                }
            },
        ),
    )

    payload = instructions.build_next_instruction("task01", task_dir)

    assert payload["effectiveNext"]["phase"] == "evaluation"
    assert payload["effectiveNext"]["action"] == "resolve_workflow_blockers"
    assert "Workflow is blocked at evaluation" in payload["nextActionPrompt"]
    assert "Do not trust runtime-state nextAction=done or completed/finished wording" in payload["nextActionPrompt"]
    assert "Produce a valid evaluation.json" in payload["nextActionPrompt"]


def test_parse_ask_args_accepts_trae_cn_agent_suffix() -> None:
    from orchestrator.main import parse_ask_args

    user_input, agent, force_tui, force_detached = parse_ask_args(["做一个任务", "trae-cn"])

    assert user_input == "做一个任务"
    assert agent == "trae-cn"
    assert force_tui is False
    assert force_detached is False


def test_trae_cn_adapter_uses_traecli_yolo(tmp_path: Path) -> None:
    from orchestrator.agents import build_agent_cli_command

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    cmd, meta = build_agent_cli_command("trae-cn", "prompt", task_dir, phase="generator")

    assert cmd[0:2] == ["traecli", "-p"]
    assert "--yolo" in cmd
    assert "--json" in cmd
    assert meta["agent"] == "trae-cn"
    assert meta["supportsPersistentSession"] is True


def test_ios_task_does_not_enable_android_agent_env(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.agents import build_agent_subprocess_env

    task_dir = tmp_path / "ios-task"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "ios-task", "taskType": "ios"})
    (task_dir / "Requirements.md").write_text("iOS 真机验证，不涉及 Android。", encoding="utf-8")

    _env, diagnostics = build_agent_subprocess_env(task_dir)

    assert diagnostics["androidEnvEnabled"] is False
    assert diagnostics["reason"] == "not_android_task"


def test_iteration_threshold_rotates_codex_primary_session(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.agents import build_agent_cli_command

    task_dir = tmp_path / "task01"
    task_dir.mkdir(parents=True)
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "iteration": 20,
        "agentExecutionPolicy": {"bypassApprovals": True, "consent": "user_approved"},
        "agentSessions": {
            "primary": {
                "agent": "codex",
                "sessionId": "sess-old",
                "policy": "primary-persistent",
                "createdIteration": 7,
                "agentExecutionBypass": True,
            }
        },
    })
    monkeypatch.setenv("AUTOMIND_AGENT_SESSION_ITERATION_THRESHOLD", "12")

    cmd, meta = build_agent_cli_command("codex", "prompt", task_dir, phase="generator")
    state = read_runtime_state(task_dir) or {}

    assert meta["sessionAction"] == "new"
    assert cmd[:3] == ["codex", "--dangerously-bypass-approvals-and-sandbox", "exec"]
    cleared = state["agentSessions"]["clearedPrimarySessions"][-1]
    assert cleared["sessionId"] == "sess-old"
    assert cleared["clearReason"] == "iteration_threshold_rotation_12"


def test_tui_owned_loop_continues_replan_pending_to_finish(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.tui import session as tui_session

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state.update({"status": "replan_pending", "currentOwner": "planner", "nextAction": "run_test_planner", "iteration": 4})
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    monkeypatch.setattr(tui_session, "render_tui_snapshot", lambda *_args, **_kwargs: "snapshot")

    calls = []

    def fake_loop() -> bool:
        calls.append("loop")
        state = json.loads((task_dir / "runtime-state.json").read_text())
        if len(calls) == 1:
            state.update({"status": "replan_pending", "currentOwner": "planner", "nextAction": "run_test_planner", "iteration": 4})
        else:
            state.update({"status": "finished", "currentOwner": "automind", "nextAction": "finish", "iteration": 5})
        (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
        return state["status"] == "finished"

    assert tui_session.run_tui_owned_loop("task01", task_dir, agent="codex", run_loop=fake_loop) is True
    assert calls == ["loop", "loop"]


def test_tui_owned_loop_stops_after_repeated_unchanged_replan_pending(tmp_path: Path, monkeypatch) -> None:
    from orchestrator.tui import session as tui_session

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state.update({"status": "replan_pending", "currentOwner": "planner", "nextAction": "run_test_planner", "iteration": 4})
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
    monkeypatch.setattr(tui_session, "render_tui_snapshot", lambda *_args, **_kwargs: "snapshot")

    calls = []

    def fake_loop() -> bool:
        calls.append("loop")
        # Leave runtime-state unchanged and return False to simulate an idle loop.
        return False

    assert tui_session.run_tui_owned_loop("task01", task_dir, agent="codex", run_loop=fake_loop) is False
    assert len(calls) == 4


def test_long_running_compile_authorization_ask_user_is_rejected() -> None:
    """Long-running/compile-duration authorization must not pause the loop even
    when labeled with an otherwise-whitelisted category."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "system_or_external_dependency",
            "question": "This is a long compile that may take 30 minutes. Should I start the build?",
            "reason": "Full clean build is time-consuming.",
            "options": [{"id": "yes", "label": "Start"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert any("long-running" in i for i in issues)


def test_long_running_ask_user_rejected_in_chinese() -> None:
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "system_or_external_dependency",
            "question": "编译比较耗时，是否开始编译？",
            "options": [{"id": "yes", "label": "开始"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert any("long-running" in i for i in issues)


def test_genuine_signing_ask_user_still_allowed() -> None:
    """A real hard-interrupt (signing) ask_user must not be falsely rejected by
    the long-running guard."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "real_device_or_signing",
            "question": "No valid signing identity found. Which signing path should CodeAutonomy use?",
            "reason": "Code signing failed: errSecInternalComponent.",
            "options": [{"id": "configure", "label": "Configure signing"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert issues == []


def test_device_operation_delegation_ask_user_is_rejected() -> None:
    """When the device is reachable, delegating in-app operation back to the human
    is not a valid hard-interrupt; the gate must reject it."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "real_device_or_signing",
            "question": "I cannot operate your physical device. Please confirm how to verify play/skip/error.",
            "reason": "T6 needs play/skip/error actions and logcat capture on the phone.",
            "options": [{"id": "manual", "label": "I will operate the phone"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert any("delegating device operation" in i for i in issues)


def test_device_operation_delegation_ask_user_rejected_in_chinese() -> None:
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "real_device_or_signing",
            "question": "我无法操控你的真机，请确认验证执行方式。",
            "options": [{"id": "manual", "label": "我来操作"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert any("delegating device operation" in i for i in issues)


def test_genuine_device_gate_ask_user_still_allowed() -> None:
    """A real device/permission gate (no device, unlock/trust needed) must still
    pause the loop even if delegation-style wording also appears."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "real_device_or_signing",
            "question": "No device in state=device — CodeAutonomy cannot operate the phone until you unlock it and approve the USB debugging trust prompt.",
            "reason": "adb devices shows no device; trust prompt unresolved.",
            "options": [{"id": "connect", "label": "Connect and trust"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert issues == []


def test_completion_gate_rewrites_long_running_ask_user_to_retry() -> None:
    """End-to-end: apply_completion_gate must rewrite a long-compile ask_user to
    retry_generator so the autonomous loop continues."""
    import tempfile
    from orchestrator.completion import apply_completion_gate

    with tempfile.TemporaryDirectory() as d:
        task_dir = Path(d)
        evaluation = {
            "iteration": 1,
            "result": "blocked",
            "summary": "waiting for build authorization",
            "failedChecks": [],
            "evidence": [],
            "nextAction": "ask_user",
            "askUserQuestion": {
                "category": "system_or_external_dependency",
                "question": "The full build will take a long time. Authorize compile?",
                "options": [{"id": "yes", "label": "Start"}],
            },
        }
        enriched, _report = apply_completion_gate(task_dir, evaluation)
        assert enriched["nextAction"] == "retry_generator"
        assert "ask_user_not_whitelisted" in (enriched.get("warnings") or [])


def test_temporary_unblock_patch_ask_user_is_rejected() -> None:
    """Authorizing a temporary verification-unblock code/script/wrapper patch is
    CodeAutonomy's own job and must not pause the loop."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "system_or_external_dependency",
            "question": "May I apply a temporary patch to the generated libtool wrapper and rerun the build once?",
            "reason": "The wrapper returns 1 but the archive is produced; a verification unblock is needed.",
            "options": [{"id": "yes", "label": "Approve"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert any("temporary verification-unblock" in i for i in issues)


def test_temporary_unblock_patch_ask_user_rejected_in_chinese() -> None:
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "system_or_external_dependency",
            "question": "是否允许对 generated build wrapper 生成的 libtool wrapper 做一次临时验证 unblock 补丁并重跑一次 validate-build-ios？",
            "reason": "archive 已生成但 wrapper 返回 1，需要临时补丁解除构建阻塞。",
            "options": [{"id": "yes", "label": "允许临时补丁并重跑"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert any("temporary verification-unblock" in i for i in issues)


def test_destructive_patch_ask_user_still_allowed() -> None:
    """A temporary change that is genuinely destructive/sensitive (delete/reset)
    must still pause the loop and not be suppressed by the temp-unblock guard."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "unauthorized_destructive_or_sensitive",
            "question": "May I temporarily delete and reset the local database to unblock the build?",
            "reason": "The stale database is failing migrations.",
            "options": [{"id": "yes", "label": "Approve"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert issues == []


def test_signing_temp_patch_is_auto_handled_not_paused() -> None:
    """Signing/certificate/keychain-for-signing is NOT a sensitive guard:
    CodeAutonomy may re-sign with the user's own certs / automatic signing, so a
    temporary signing patch is auto-handled (rewritten back to retry) rather than
    paused."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "real_device_or_signing",
            "question": "May I temporarily modify the signing certificate to unblock the build?",
            "reason": "No valid code signing identity is available.",
            "options": [{"id": "yes", "label": "Approve"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert any("temporary verification-unblock" in i for i in issues)


def test_compatible_runner_runtime_proof_ask_user_is_rejected() -> None:
    """A local reversible compatible/external runner unblock for runtime proof
    is CodeAutonomy's own job and must not ask_user."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "system_or_external_dependency",
            "question": "required runtime proof 仍缺失：当前真机/可见模拟器/XCUITest/app-smoke 路径都无法证明 TC-F01/TC-F02/TC-F04。请选择下一步 runtime 证明策略。",
            "reason": "Runner/destination/test-target 环境卡住，但可创建 compatible external runner 解除验证阻塞。",
            "options": [
                {"id": "approve_compatible_runner_unblock", "label": "授权兼容 runner", "impact": "允许创建/调整任务本地或项目内验证 runner，使 deployment target 兼容 iOS 18.7.8；需记录 verification unblock。"},
                {"id": "approve_runtime_downgrade", "label": "批准降级完成", "impact": "放弃 required runtime/device proof。"},
            ],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert any("temporary verification-unblock" in i for i in issues)


def test_runtime_downgrade_ask_user_allowed() -> None:
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "system_or_external_dependency",
            "riskTier": "sensitive_hard_gate",
            "question": "Approve runtime downgrade and finish with static evidence only?",
            "reason": "This waives required runtime proof.",
            "options": [{"id": "approve_runtime_downgrade", "label": "Approve downgrade"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert issues == []


def test_completion_gate_rewrites_temporary_unblock_ask_user_to_retry() -> None:
    """End-to-end: a temporary-unblock ask_user is rewritten to retry_generator
    and the residual askUserQuestion is cleared so the tick gate cannot deadlock."""
    import tempfile
    from orchestrator.completion import apply_completion_gate

    with tempfile.TemporaryDirectory() as d:
        task_dir = Path(d)
        evaluation = {
            "iteration": 1,
            "result": "blocked",
            "summary": "waiting for unblock authorization",
            "failedChecks": [],
            "evidence": [],
            "nextAction": "ask_user",
            "askUserQuestion": {
                "category": "system_or_external_dependency",
                "question": "是否允许做一次临时补丁并重跑 validate-build-ios？",
                "options": [{"id": "yes", "label": "允许"}],
            },
        }
        enriched, _report = apply_completion_gate(task_dir, evaluation)
        assert enriched["nextAction"] == "retry_generator"
        assert "ask_user_not_whitelisted" in (enriched.get("warnings") or [])
        assert enriched.get("askUserQuestion") is None


def test_completion_gate_keeps_legitimate_ask_user_despite_boundary_violation() -> None:
    """A genuine hard-interrupt ask_user must not be swallowed by a co-occurring
    boundary violation; the human decision is gathered first."""
    import tempfile
    from orchestrator.completion import apply_completion_gate

    with tempfile.TemporaryDirectory() as d:
        task_dir = Path(d)
        evaluation = {
            "iteration": 1,
            "result": "blocked",
            "summary": "needs signing decision",
            "failedChecks": [],
            "evidence": [],
            "nextAction": "ask_user",
            "askUserQuestion": {
                "category": "real_device_or_signing",
                "question": "No valid signing identity found. Which signing path should CodeAutonomy use?",
                "reason": "Code signing failed: errSecInternalComponent.",
                "options": [{"id": "configure", "label": "Configure signing"}],
            },
            "evaluatorChanges": [
                {"id": "ECG-001", "category": "product_code", "files": ["src/app/Main.swift"], "reason": "x"},
            ],
        }
        enriched, _report = apply_completion_gate(task_dir, evaluation)
        assert enriched["nextAction"] == "ask_user"
        assert enriched.get("askUserQuestion") is not None
        assert "evaluator_boundary_violation" in (enriched.get("warnings") or [])


def test_completion_gate_boundary_violation_clears_pending_when_rewriting() -> None:
    """When a boundary violation rewrites a non-whitelisted ask_user to
    retry_generator, the residual askUserQuestion must be cleared."""
    import tempfile
    from orchestrator.completion import apply_completion_gate

    with tempfile.TemporaryDirectory() as d:
        task_dir = Path(d)
        evaluation = {
            "iteration": 1,
            "result": "blocked",
            "summary": "wrapper patch + boundary violation",
            "failedChecks": [],
            "evidence": [],
            "nextAction": "ask_user",
            "askUserQuestion": {
                "category": "system_or_external_dependency",
                "question": "是否允许做一次临时 wrapper 补丁并重跑？",
                "options": [{"id": "yes", "label": "允许"}],
            },
            "evaluatorChanges": [
                {"id": "ECG-001", "category": "product_code", "files": ["src/app/Main.swift"], "reason": "x"},
            ],
        }
        enriched, _report = apply_completion_gate(task_dir, evaluation)
        assert enriched["nextAction"] == "retry_generator"
        assert enriched.get("askUserQuestion") is None


def test_risk_tier_safe_self_service_ask_user_is_rejected() -> None:
    """When the model self-assesses riskTier=safe_self_service, CodeAutonomy trusts it
    and rejects the pause regardless of wording."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "system_or_external_dependency",
            "riskTier": "safe_self_service",
            "question": "Should I proceed with the verification step?",
            "reason": "I can apply a reversible env tweak and retry.",
            "reversible": True,
            "selfServiceRationale": "Tweak is checkpointed and restored before finish.",
            "options": [{"id": "yes", "label": "Proceed"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert any("safe_self_service" in i for i in issues)


def test_risk_tier_safe_self_service_trusted_even_without_keyword_match() -> None:
    """A safe_self_service ask whose text matches NO legacy keyword marker is still
    rejected -- the model label alone is enough to keep the loop running."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "system_or_external_dependency",
            "riskTier": "safe_self_service",
            "question": "需要我继续推进验证吗？",
            "reason": "可以自行处理。",
            "options": [{"id": "yes", "label": "继续"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert issues != []


def test_risk_tier_sensitive_hard_gate_ask_user_is_allowed() -> None:
    """When the model self-assesses riskTier=sensitive_hard_gate, the loop is
    allowed to pause without keyword second-guessing."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "real_device_or_signing",
            "riskTier": "sensitive_hard_gate",
            "question": "Approve overwrite install on the connected device?",
            "reason": "This replaces the existing build.",
            "options": [{"id": "yes", "label": "Approve"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert issues == []


def test_risk_tier_safe_label_with_strong_irreversible_signal_is_honored() -> None:
    """Anti self-contradiction safety net: model says safe_self_service but the
    text screams an irreversible/account-security action -> honor the pause."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "unauthorized_destructive_or_sensitive",
            "riskTier": "safe_self_service",
            "question": "May I delete and wipe the user database to unblock?",
            "reason": "It is in the way.",
            "options": [{"id": "yes", "label": "Delete"}],
        },
    }
    issues, warnings = validate_ask_user_category(evaluation)
    assert issues == []
    assert any("safe_self_service" in w for w in warnings)


def test_production_alone_does_not_block_safe_self_service() -> None:
    """`production` is intentionally NOT a safety keyword: a safe_self_service ask
    mentioning production (without a strong irreversible/account signal) is still
    rejected so automation is not over-cautious."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "system_or_external_dependency",
            "riskTier": "safe_self_service",
            "question": "Rebuild the production config flag and rerun the build?",
            "reason": "Just a reversible build-config toggle.",
            "reversible": True,
            "options": [{"id": "yes", "label": "Proceed"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert any("safe_self_service" in i for i in issues)


def test_legacy_keyword_fallback_used_when_risk_tier_absent() -> None:
    """Without riskTier, the deterministic keyword vetos still apply (backward
    compatible safe default)."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "system_or_external_dependency",
            "question": "May I apply a temporary patch to the wrapper and retry?",
            "options": [{"id": "yes", "label": "Approve"}],
        },
    }
    issues, _ = validate_ask_user_category(evaluation)
    assert any("temporary verification-unblock" in i for i in issues)


def test_completion_gate_rewrites_safe_self_service_ask_user_to_retry() -> None:
    """End-to-end: a safe_self_service ask_user is rewritten to retry_generator and
    the residual askUserQuestion is cleared."""
    import tempfile
    from orchestrator.completion import apply_completion_gate

    with tempfile.TemporaryDirectory() as d:
        task_dir = Path(d)
        evaluation = {
            "iteration": 1,
            "result": "blocked",
            "summary": "self-serviceable pause",
            "failedChecks": [],
            "evidence": [],
            "nextAction": "ask_user",
            "askUserQuestion": {
                "category": "system_or_external_dependency",
                "riskTier": "safe_self_service",
                "question": "可以继续验证吗？",
                "options": [{"id": "yes", "label": "继续"}],
            },
        }
        enriched, _report = apply_completion_gate(task_dir, evaluation)
        assert enriched["nextAction"] == "retry_generator"
        assert "ask_user_not_whitelisted" in (enriched.get("warnings") or [])
        assert enriched.get("askUserQuestion") is None


def test_full_auto_intent_overrides_sensitive_hard_gate() -> None:
    """拦不拦截以用户诉求为准: when the user explicitly chose full-auto, even a
    model-declared sensitive_hard_gate must not pause the loop."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "unauthorized_destructive_or_sensitive",
            "riskTier": "sensitive_hard_gate",
            "question": "Proceed with uninstall + reinstall to verify?",
            "options": [{"id": "yes", "label": "Proceed"}],
        },
    }
    # No intent -> the sensitive_hard_gate is honored (pause allowed).
    issues_default, _ = validate_ask_user_category(evaluation)
    assert issues_default == []
    # Full-auto intent -> rewritten away from ask_user.
    issues_auto, _ = validate_ask_user_category(evaluation, {"fullAuto": True})
    assert any("full-auto" in i for i in issues_auto)


def test_user_preauthorized_action_overrides_sensitive_hard_gate() -> None:
    """A model claim userAuthorized=true, corroborated by a non-empty
    destructiveActionsAllowList, releases the pre-authorized sensitive action."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "unauthorized_destructive_or_sensitive",
            "riskTier": "sensitive_hard_gate",
            "userAuthorized": True,
            "question": "Reset the app data to re-run the first-launch flow?",
            "options": [{"id": "yes", "label": "Reset"}],
        },
    }
    intent = {"fullAuto": False, "preAuthorizedActions": ["reset app data"]}
    issues, _ = validate_ask_user_category(evaluation, intent)
    assert any("userAuthorized" in i for i in issues)


def test_user_authorized_claim_without_allow_list_still_pauses() -> None:
    """A userAuthorized=true claim with NO allow list is not trusted: a model
    cannot fabricate consent out of thin air."""
    from orchestrator.completion import validate_ask_user_category

    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "unauthorized_destructive_or_sensitive",
            "riskTier": "sensitive_hard_gate",
            "userAuthorized": True,
            "question": "Delete the production bucket to unblock?",
            "options": [{"id": "yes", "label": "Delete"}],
        },
    }
    intent = {"fullAuto": False, "preAuthorizedActions": []}
    issues, _ = validate_ask_user_category(evaluation, intent)
    assert issues == []


def test_read_user_autonomy_intent_reads_full_auto_and_allow_list(tmp_path: Path) -> None:
    """The intent reader surfaces fullAuto and destructiveActionsAllowList from
    runtime-state.planner.preImplementationReview."""
    from orchestrator.completion import read_user_autonomy_intent
    from orchestrator.state import write_runtime_state

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "planner": {
            "preImplementationReview": {
                "fullAuto": True,
                "decisionBundle": {"destructiveActionsAllowList": ["reset app data", "uninstall app"]},
            }
        },
    })
    intent = read_user_autonomy_intent(task_dir)
    assert intent["fullAuto"] is True
    assert intent["preAuthorizedActions"] == ["reset app data", "uninstall app"]
    # Missing task_dir / state degrades to the conservative default.
    assert read_user_autonomy_intent(None) == {"fullAuto": False, "preAuthorizedActions": []}


def test_completion_gate_honors_full_auto_intent_end_to_end(tmp_path: Path) -> None:
    """End-to-end: with fullAuto in runtime-state, a sensitive_hard_gate ask_user
    is rewritten to retry_generator and the residual question cleared."""
    from orchestrator.completion import apply_completion_gate
    from orchestrator.state import write_runtime_state

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "planner": {"preImplementationReview": {"fullAuto": True}},
    })
    evaluation = {
        "iteration": 1,
        "result": "blocked",
        "summary": "wants to pause",
        "failedChecks": [],
        "evidence": [],
        "nextAction": "ask_user",
        "askUserQuestion": {
            "category": "unauthorized_destructive_or_sensitive",
            "riskTier": "sensitive_hard_gate",
            "question": "May I uninstall and reinstall to verify?",
            "options": [{"id": "yes", "label": "Proceed"}],
        },
    }
    enriched, _report = apply_completion_gate(task_dir, evaluation)
    assert enriched["nextAction"] == "retry_generator"
    assert enriched.get("askUserQuestion") is None


def test_apply_evaluation_result_retry_clears_runtime_pending_question(tmp_path: Path) -> None:
    """retry_generator transition must clear stale askUserQuestion/pendingQuestion
    in runtime-state so the skill-mode tick gate cannot deadlock."""
    from orchestrator.evaluation_result import apply_evaluation_result

    task_dir = _write_workflow_task(tmp_path, verification_target="real_device")
    state = json.loads((task_dir / "runtime-state.json").read_text())
    state["askUserQuestion"] = {"question": "stale?", "options": [{"id": "y", "label": "Y"}]}
    state["pendingQuestion"] = {"question": "stale?"}
    (task_dir / "runtime-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))

    apply_evaluation_result(task_dir, {"iteration": 2, "result": "fail", "nextAction": "retry_generator"})
    new_state = json.loads((task_dir / "runtime-state.json").read_text())

    assert new_state["nextAction"] == "retry_generator"
    assert new_state.get("askUserQuestion") is None
    assert new_state.get("pendingQuestion") is None


def test_completion_check_failure_syncs_workflow_state_to_generator_retry(tmp_path: Path, monkeypatch, capsys) -> None:
    """CLI completion-check false-finish rewrite must update central workflow state.

    Without this, phase-gate can see workflowControlState.nextPhase=evaluation
    while phaseSummary/effectiveNext correctly route back to delivery, causing
    a route-drift failure right when the task should continue repair.
    """
    import json as _json
    import orchestrator.commands.knowledge as knowledge_cmd
    import orchestrator.commands.session as session_cmd
    from orchestrator.state import write_evaluation_json, write_runtime_state
    from orchestrator.workflow_state import emit_workflow_event, read_workflow_state

    task_dir = tmp_path / ".automind" / "tasks" / "task01"
    task_dir.mkdir(parents=True)
    (task_dir / "Requirements.md").write_text("# Requirements\n\n### R01\n- **AC-001**: must pass\n")
    (task_dir / "TestCases.md").write_text("# TestCases\n\n| ID | Requirement/AC | Required? |\n|----|----------------|-----------|\n| TC-F01 | AC-001 | yes |\n")
    (task_dir / "Plan.md").write_text("# Plan\n")
    (task_dir / "Delivery.md").write_text("# Delivery\n")
    write_runtime_state(task_dir, {"taskId": "task01", "status": "evaluating", "currentOwner": "evaluator", "nextAction": "run_evaluator", "iteration": 1})
    emit_workflow_event(task_dir, {
        "type": "phase_action_completed",
        "phase": "delivery",
        "action": "run_generator",
        "nextAction": "run_evaluation",
        "nextPhase": "evaluation",
        "plannedNextPhase": "completion",
        "iteration": 1,
    })
    write_evaluation_json(task_dir, {"iteration": 1, "result": "pass", "nextAction": "finish", "testResults": []})

    import orchestrator.phase_transition as phase_transition
    import orchestrator.session.instructions as instructions

    workflow_report = {"result": "pass", "workflowState": {"currentPhase": "delivery", "expectedNext": [{"phase": "delivery"}], "issueCount": 0}}
    monkeypatch.setattr(knowledge_cmd, "get_task_dir", lambda _task: task_dir)
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _task: task_dir)
    monkeypatch.setattr(knowledge_cmd, "print_report_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(phase_transition, "check_workflow_consistency", lambda _task: (True, workflow_report))
    monkeypatch.setattr(instructions, "check_workflow_consistency", lambda _task: (True, workflow_report))

    try:
        knowledge_cmd.cmd_completion_check("task01")
    except SystemExit as exc:
        assert exc.code == 1
    capsys.readouterr()

    workflow = read_workflow_state(task_dir)
    assert workflow["currentPhase"] == "delivery"
    assert workflow["nextPhase"] == "delivery"
    assert workflow["nextAction"] == "retry_generator"

    session_cmd.cmd_phase_gate("task01", ["auto", "--soft"])
    payload = _json.loads(capsys.readouterr().out)
    assert payload["routeDrift"] == []
    assert payload["result"] == "pass"
    assert payload["phaseReuseRefresh"]["phase"] == "generator"

def test_pre_implementation_ask_question_follows_chinese_user_input() -> None:
    from orchestrator.main import format_pre_implementation_ask_question

    text = format_pre_implementation_ask_question(
        ["是否确认按 Requirements/TestCases/Plan 中的默认事件契约与已连接真机验证路径进入实现？"],
        {"reason": "非低风险 iOS 客户端埋点变更", "approvalScope": "requirements_testcases_runtime_evidence"},
        user_input="增加一个埋点统计，它的作用是记录在app冷启动后用户首次触发点击、滚动等操作手势的距离首次冷启动的时间",
    )

    assert "预实现确认" in text
    assert "Requirements / TestCases 重点摘要" in text
    assert "decision bundle" in text
    assert "需求是否清楚" in text
    assert "验证目标" in text
    assert "是否授权任何非低风险操作" in text
    assert "需要确认的问题/决策" in text
    assert "原因：非低风险" in text
    assert "Pre-implementation decision bundle" not in text
    assert "First review the key planning docs" not in text


def test_pre_implementation_ask_question_keeps_english_for_english_user_input() -> None:
    from orchestrator.main import format_pre_implementation_ask_question

    text = format_pre_implementation_ask_question(
        ["Confirm requirements?"],
        {"reason": "Non-trivial app change", "approvalScope": "scope"},
        user_input="Add an analytics event for first interaction after cold launch",
    )

    assert "Pre-implementation decision bundle" in text
    assert "Requirements/TestCases highlights" in text
    assert "confirm this decision bundle" in text
    assert "预实现确认" not in text

def test_pre_implementation_ask_question_includes_requirement_and_testcase_highlights() -> None:
    from orchestrator.main import format_pre_implementation_ask_question

    review = {
        "reason": "非低风险 iOS 客户端埋点变更",
        "approvalScope": "requirements_testcases_runtime_evidence",
        "requirements": [
            {"id": "R01", "title": "捕获本次冷启动后的首次有效用户 touch 交互", "acceptanceCriteria": ["AC-001", "AC-002"]},
            {"id": "R02", "title": "使用现有启动时间口径计算首次交互耗时", "acceptanceCriteria": ["AC-003"]},
        ],
        "testCases": [
            {"id": "TC-F01", "title": "静态验证事件名、字段和 hook 位置", "required": True, "runtimeLevel": "static", "acceptanceCriteriaRefs": ["AC-001"]},
            {"id": "TC-F02", "title": "真机冷启动后首次点击/滚动并抓目标 event payload", "required": True, "runtimeLevel": "device/runtime", "acceptanceCriteriaRefs": ["AC-001", "AC-003"]},
            {"id": "TC-QP07", "title": "可选性能观察", "required": False, "runtimeLevel": "runtime/static", "acceptanceCriteriaRefs": ["AC-003"]},
        ],
    }

    text = format_pre_implementation_ask_question(
        ["是否确认按 Requirements/TestCases/Plan 中的默认事件契约与已连接真机验证路径进入实现？"],
        review,
        user_input="增加一个埋点统计，它的作用是记录在app冷启动后用户首次触发点击、滚动等操作手势的距离首次冷启动的时间",
    )

    assert "Requirements 重点" in text
    assert "- R01: 捕获本次冷启动后的首次有效用户 touch 交互" in text
    assert "Required TestCases 重点" in text
    assert "- TC-F02: 真机冷启动后首次点击/滚动并抓目标 event payload" in text
    assert "TC-QP07" not in text


def test_run_harness_loop_has_no_unbound_local_names() -> None:
    """Guard against NameError-class regressions inside run_harness_loop.

    The pre-implementation ask_user branch once referenced `planner_user_input`,
    a variable that only exists in `run_ai_test_planner`'s scope. Python treats
    such a name as a global lookup, so it raises NameError only when that exact
    branch executes at runtime — which unit tests of the helper function never
    triggered. This test statically scans the function for names that are read
    but never bound anywhere in the function and not provided by the module
    namespace or builtins.
    """
    import ast
    import builtins as _builtins
    import inspect
    import textwrap

    import orchestrator.main as main_mod

    source = inspect.getsource(main_mod.run_harness_loop)
    func_node = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(func_node, ast.FunctionDef)

    bound: set[str] = set()
    loaded: set[str] = set()

    for node in ast.walk(func_node):
        if isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            elif isinstance(node.ctx, ast.Load):
                loaded.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)

    allowed = bound | set(dir(main_mod)) | set(dir(_builtins))
    unbound = sorted(name for name in loaded if name not in allowed)

    assert unbound == [], f"run_harness_loop reads names never bound/imported: {unbound}"


def test_normalize_pending_question_recovers_from_state_pending_question(tmp_path: Path) -> None:
    """When askUserQuestion is null, recover the real bundle from pendingQuestion."""
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "human_input_pending",
        "currentOwner": "human",
        "nextAction": "ask_user",
        "askUserQuestion": None,
        "pendingQuestion": {
            "category": "pre_implementation_review",
            "question": "确认Media范围与真机验证？",
            "options": [
                {"id": "use_real_device_music_scope", "label": "确认Media+真机"},
                {"id": "replan_all_audio_scope", "label": "改全音频范围"},
            ],
            "recommendedOption": "use_real_device_music_scope",
        },
    })

    pending = normalize_pending_question(task_dir)

    assert pending is not None
    assert pending["question"] == "确认Media范围与真机验证？"
    assert [opt["id"] for opt in pending["options"]] == ["use_real_device_music_scope", "replan_all_audio_scope"]
    assert pending["recommended"] == "use_real_device_music_scope"


def test_normalize_pending_question_recovers_from_pre_implementation_review(tmp_path: Path) -> None:
    """When neither askUserQuestion nor pendingQuestion exists, recover from the
    durable planner.preImplementationReview artifact."""
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "human_input_pending",
        "currentOwner": "human",
        "nextAction": "ask_user",
        "askUserQuestion": None,
        "planner": {
            "preImplementationReview": {
                "decision": "ask_user",
                "questions": ["请确认实现方向与真机验证授权。"],
                "options": [
                    {"id": "confirm", "label": "确认"},
                    {"id": "stop", "label": "停止"},
                ],
                "recommendedOption": "confirm",
            }
        },
    })

    pending = normalize_pending_question(task_dir)

    assert pending is not None
    assert "请确认实现方向" in pending["question"]
    assert [opt["id"] for opt in pending["options"]] == ["confirm", "stop"]
    assert pending["recommended"] == "confirm"


def test_normalize_pending_question_ignores_stale_evaluation_question_when_retrying(tmp_path: Path) -> None:
    """A rewritten evaluation may retain askUserQuestion for auditability; it is
    pending only while evaluation.nextAction is still ask_user."""
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "retry_pending",
        "currentOwner": "generator",
        "nextAction": "retry_generator",
    })
    (task_dir / "evaluation.json").write_text(json.dumps({
        "result": "fail",
        "nextAction": "retry_generator",
        "askUserQuestion": {
            "category": "system_or_external_dependency",
            "question": "May I temporarily patch the generated wrapper and rerun?",
            "options": [{"id": "yes", "label": "yes"}],
        },
    }))

    assert normalize_pending_question(task_dir) is None


def test_normalize_pending_question_ignores_answered_preimplementation_review(tmp_path: Path) -> None:
    """Keep historical review questions in runtime-state without turning them
    back into a live ask_user gate after the answer was applied."""
    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "retry_pending",
        "currentOwner": "generator",
        "nextAction": "retry_generator",
        "planner": {
            "preImplementationReview": {
                "decision": "auto_proceed",
                "needsUserInput": False,
                "questions": ["请确认实现方向。"],
                "options": [{"id": "confirm", "label": "确认"}],
                "recommendedOption": "confirm",
            }
        },
    })

    assert normalize_pending_question(task_dir) is None


def test_explicit_auto_unconditionally_sets_full_auto_and_auto_proceed() -> None:
    """User said 全自动/一站到底 in the original request → decision must be
    auto_proceed and fullAuto must be True, even when the deterministic review
    otherwise would flag high-risk keywords or open brainstorm questions."""
    from orchestrator.main import build_pre_implementation_review_state

    # High-risk keywords + explicit brainstorm questions + 全自动 in the
    # same request → must still auto_proceed with fullAuto=true.
    review = build_pre_implementation_review_state(
        "实现一个支付/登录系统，需要删除旧数据，全自动，先设计再改代码",
        questions=["请确认删除的旧数据范围"],
    )

    assert review["decision"] == "auto_proceed"
    assert review["fullAuto"] is True
    assert review["needsUserInput"] is False
    options_ids = [str(opt.get("id")) for opt in review.get("options", [])]
    assert "confirm_full_auto_mode" in options_ids


def test_full_auto_option_present_in_any_non_scaffold_review() -> None:
    """Even a plain implementation request without full-auto signal must still
    expose the 全自动模式 option so the user can pick it during the ask_user
    prompt."""
    from orchestrator.main import build_pre_implementation_review_state

    review = build_pre_implementation_review_state("实现一个简单的 TODO list CRUD")

    options_ids = [str(opt.get("id")) for opt in review.get("options", [])]
    assert "confirm_full_auto_mode" in options_ids


def test_answer_confirm_full_auto_mode_writes_fullauto_in_runtime_state(tmp_path: Path) -> None:
    """User selects the 全自动模式 option during an ask_user prompt → the
    runtime-state planner.preImplementationReview.fullAuto must be True so the
    completion gate keeps rewriting subsequent ask_user requests to
    retry_generator."""
    from orchestrator.session.answers import apply_user_answer
    from orchestrator.state import read_runtime_state, write_runtime_state

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "ask_user_pending",
        "currentOwner": "human",
        "nextAction": "ask_user",
        "planner": {
            "needsUserInput": True,
            "preImplementationReview": {
                "decision": "ask_user",
                "needsUserInput": True,
                "questions": ["请确认实现方向"],
                "options": [
                    {"id": "confirm_full_auto_mode", "label": "全自动模式 / Full auto mode", "impact": "Skip all ask_user gates"},
                    {"id": "confirm_recommended_direction", "label": "Confirm recommended direction", "impact": "Continue"},
                    {"id": "stop", "label": "Stop", "impact": "Stop"},
                ],
                "recommendedOption": "confirm_recommended_direction",
                "fullAuto": False,
            },
        },
    })

    apply_user_answer(task_dir, answer_text="", selected_option="confirm_full_auto_mode", answered_by="cli_user")

    state = read_runtime_state(task_dir) or {}
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
    assert review.get("fullAuto") is True, f"Expected fullAuto=True in review; got {review}"
    assert review.get("decision") == "auto_proceed"
    assert state.get("nextAction") in {"retry_generator", "run_test_planner", "planned"}


def test_answer_free_text_quanzidong_triggers_fullauto(tmp_path: Path) -> None:
    """User replied 全自动 in free text (no explicit option id) →
    _update_planner_after_answer must still flip fullAuto to True so the
    subsequent completion gates honor the user's autonomy intent."""
    from orchestrator.session.answers import apply_user_answer
    from orchestrator.state import read_runtime_state, write_runtime_state

    task_dir = tmp_path / "task01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {
        "taskId": "task01",
        "status": "ask_user_pending",
        "currentOwner": "human",
        "nextAction": "ask_user",
        "planner": {
            "needsUserInput": True,
            "preImplementationReview": {
                "decision": "ask_user",
                "needsUserInput": True,
                "questions": ["请确认实现方向"],
                "options": [
                    {"id": "confirm_recommended_direction", "label": "Confirm recommended direction", "impact": "Continue"},
                ],
                "recommendedOption": "confirm_recommended_direction",
                "fullAuto": False,
            },
        },
    })

    # Plain free-text reply that says "全自动". Without a matching option id
    # the semantic resolver in apply_user_answer still flips fullAuto=True.
    apply_user_answer(task_dir, answer_text="全自动，直接跑", selected_option=None, answered_by="cli_user")

    state = read_runtime_state(task_dir) or {}
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
    assert review.get("fullAuto") is True, f"Expected fullAuto=True from free-text 全自动 reply; got {review}"


def test_preimplementation_ask_question_mentions_fullauto_tip() -> None:
    """The ask_user question body must tell the user they can reply 全自动 /
    full auto to skip every subsequent gate. This guards against the
    "how do I make it stop asking me" UX regression."""
    from orchestrator.main import format_pre_implementation_ask_question

    question = format_pre_implementation_ask_question(
        ["请确认实现方向"],
        review={
            "reason": "",
            "approvalScope": "scope/approach/verification",
            "userInput": "实现一个 TODO list",
        },
        user_input="实现一个 TODO list",
    )

    assert "全自动" in question
    assert "full auto" in question.lower()
    assert "不再询问" in question or "不打断" in question or "no further interruption" in question.lower()


def test_tui_welcome_mentions_fullauto_tip() -> None:
    from orchestrator.tui.session import render_tui_welcome

    banner = render_tui_welcome()
    assert "全自动" in banner or "full auto" in banner.lower()



def test_pre_implementation_runtime_runner_soft_pause_is_rejected(tmp_path: Path) -> None:
    """Planner/pre-implementation gate must use the same ask_user policy as
    completion: compatible/external runner runtime-proof unblock is self-service.
    """
    from orchestrator.main import pre_implementation_ask_user_policy_issues

    ask_question = {
        "question": "required runtime proof 仍缺失：请选择下一步 runtime 证明策略。",
        "reason": "runner/destination/test-target 环境卡住，但可通过 compatible external runner 做本地可逆 verification unblock。",
        "options": [
            {"id": "approve_compatible_runner_unblock", "label": "授权兼容 runner", "impact": "记录 VUC 后自动尝试。"},
        ],
    }
    issues = pre_implementation_ask_user_policy_issues(tmp_path, ask_question)
    assert any("temporary verification-unblock" in issue for issue in issues)
