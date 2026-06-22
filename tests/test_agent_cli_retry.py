"""TC-R01: agent CLI 重试 + 心跳。

AC-001: mock subprocess.run 抛 TimeoutExpired 三次后第四次成功 → 重试 3 次、间隔约 1s/3s/9s。
AC-002: stdout 含 ≥3 行 `[heartbeat] retry=`。
"""
from __future__ import annotations

from orchestrator import agents


def _make_stream_responses(*responses):
    """Return a fake stream_agent_command that yields (code, stdout, stderr)."""
    iterator = iter(responses)
    calls = []

    def side_effect(*args, **kwargs):
        calls.append((args, kwargs))
        return next(iterator)

    side_effect.calls = calls
    return side_effect


def test_retry_on_timeout_then_success(monkeypatch, capsys, tmp_path):
    """AC-001: 3 次 TimeoutExpired 后第 4 次成功 → 总共 4 次 subprocess 调用，3 次重试。"""

    sleeps: list[float] = []
    monkeypatch.setattr(agents.time, "sleep", lambda s: sleeps.append(float(s)))

    # build_agent_cli_command needs a real adapter; mock to avoid filesystem deps.
    monkeypatch.setattr(
        agents,
        "build_agent_cli_command",
        lambda agent, prompt, task_dir, phase="generic": (
            ["echo", "fake"],
            {"agent": agent, "phase": phase, "sessionRole": "primary",
             "sessionPolicy": "primary-persistent", "sessionAction": "new",
             "sessionId": "", "supportsPersistentSession": True},
        ),
    )
    monkeypatch.setattr(agents, "record_agent_session_after_run", lambda *a, **k: None)

    side = _make_stream_responses(
        (-1, "", "Command timeout after 1s"),
        (-1, "", "Command timeout after 1s"),
        (-1, "", "Command timeout after 1s"),
        (0, "ok", ""),
    )
    monkeypatch.setattr(agents, "stream_agent_command", side)

    code, output = agents.run_agent_cli("codex", "p", tmp_path, phase="generator")

    assert code == 0
    assert len(side.calls) == 4, f"expected 4 stream calls, got {len(side.calls)}"
    assert all(call[1]["phase"] == "generator" for call in side.calls)
    assert all(call[1]["task_dir"] == tmp_path for call in side.calls)
    # AC-001: 3 次重试，间隔 1/3/9
    assert sleeps == [1.0, 3.0, 9.0], f"backoff sequence wrong: {sleeps}"

    # AC-002: stdout 至少 3 行 [heartbeat] retry=
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    heartbeat_lines = [ln for ln in combined.splitlines() if "[heartbeat] retry=" in ln]
    assert len(heartbeat_lines) >= 3, (
        f"expected ≥3 heartbeat retry lines, got {len(heartbeat_lines)}: {heartbeat_lines}"
    )


def test_no_retry_on_clean_failure(monkeypatch, tmp_path):
    """code != 0 但非 transient → 不重试。"""

    sleeps: list[float] = []
    monkeypatch.setattr(agents.time, "sleep", lambda s: sleeps.append(float(s)))
    monkeypatch.setattr(
        agents,
        "build_agent_cli_command",
        lambda *a, **k: (["echo", "x"], {"agent": "codex", "phase": "generator",
                                          "sessionRole": "primary", "sessionPolicy": "primary-persistent",
                                          "sessionAction": "new", "sessionId": "",
                                          "supportsPersistentSession": True}),
    )
    monkeypatch.setattr(agents, "record_agent_session_after_run", lambda *a, **k: None)

    side = _make_stream_responses((2, "", "bad request"))
    monkeypatch.setattr(agents, "stream_agent_command", side)
    code, _ = agents.run_agent_cli("codex", "p", tmp_path, phase="generator")
    assert code == 2
    assert len(side.calls) == 1
    assert sleeps == []


def test_immediate_success_no_retry(monkeypatch, tmp_path):
    sleeps: list[float] = []
    monkeypatch.setattr(agents.time, "sleep", lambda s: sleeps.append(float(s)))
    monkeypatch.setattr(
        agents,
        "build_agent_cli_command",
        lambda *a, **k: (["echo", "x"], {"agent": "codex", "phase": "generator",
                                          "sessionRole": "primary", "sessionPolicy": "primary-persistent",
                                          "sessionAction": "new", "sessionId": "",
                                          "supportsPersistentSession": True}),
    )
    monkeypatch.setattr(agents, "record_agent_session_after_run", lambda *a, **k: None)

    side = _make_stream_responses((0, "ok", ""))
    monkeypatch.setattr(agents, "stream_agent_command", side)
    code, _ = agents.run_agent_cli("codex", "p", tmp_path, phase="generator")
    assert code == 0
    assert len(side.calls) == 1
    assert sleeps == []


def test_agent_pause_for_human_input_is_not_retried_or_logged_as_failure(monkeypatch, capsys, tmp_path):
    """code -3 means stream_agent_command intentionally stopped at ask_user gate."""
    sleeps: list[float] = []
    records: list[tuple] = []
    monkeypatch.setattr(agents.time, "sleep", lambda s: sleeps.append(float(s)))
    monkeypatch.setattr(
        agents,
        "build_agent_cli_command",
        lambda *a, **k: (["echo", "x"], {"agent": "codex", "phase": "generator",
                                          "sessionRole": "primary", "sessionPolicy": "primary-persistent",
                                          "sessionAction": "resume", "sessionId": "abc",
                                          "supportsPersistentSession": True}),
    )
    monkeypatch.setattr(agents, "record_agent_session_after_run", lambda *a, **k: records.append(a))

    side = _make_stream_responses((-3, "partial output", "Agent stopped because task requires human input"))
    monkeypatch.setattr(agents, "stream_agent_command", side)

    code, output = agents.run_agent_cli("codex", "p", tmp_path, phase="generator")

    assert code == -3
    assert "partial output" in output
    assert len(side.calls) == 1
    assert sleeps == []
    assert records
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "paused because task requires human input" in combined
    assert "Agent execution failed" not in combined


def test_stream_agent_command_waits_for_agent_after_ask_user_is_written(tmp_path):
    """Default policy: notice ask_user, but let the current agent turn finish."""
    import json
    import sys

    from orchestrator.session.agent_io import stream_agent_command

    script = tmp_path / "slow_writer.py"
    marker = tmp_path / "marker.txt"
    script.write_text(
        "import json, pathlib, sys, time\n"
        "task_dir = pathlib.Path(sys.argv[1])\n"
        "for i in range(6):\n"
        "    print('loop-output', i, flush=True)\n"
        "    if i == 2:\n"
        "        (task_dir / 'evaluation.json').write_text(json.dumps({'iteration': 1, 'result': 'blocked', 'nextAction': 'ask_user', 'askUserQuestion': {'question': 'Continue?'}}))\n"
        "    time.sleep(0.02)\n"
        "pathlib.Path(sys.argv[2]).write_text('completed')\n"
    )

    code, stdout, stderr = stream_agent_command(
        [sys.executable, str(script), str(tmp_path), str(marker)],
        task_dir=tmp_path,
        agent="codex",
        phase="generator",
        cwd=str(tmp_path),
        timeout=10,
    )

    assert code == 0
    assert stderr == ""
    assert "loop-output 5" in stdout
    assert marker.exists(), "agent script should finish normally by default"
    events = [json.loads(line) for line in (tmp_path / 'events.jsonl').read_text().splitlines()]
    event_types = [event.get('type') for event in events]
    assert 'agent_input_pending' in event_types
    assert 'agent_stopped_for_input' not in event_types


def test_stream_agent_command_can_interrupt_on_ask_user_when_opted_in(monkeypatch, tmp_path):
    """Escape hatch: env opt-in can still stop runaway agents at ask_user."""
    import json
    import sys

    from orchestrator.session.agent_io import stream_agent_command

    monkeypatch.setenv("AUTOMIND_INTERRUPT_AGENT_ON_ASK_USER", "1")
    script = tmp_path / "slow_writer_interrupt.py"
    marker = tmp_path / "marker-interrupt.txt"
    script.write_text(
        "import json, pathlib, sys, time\n"
        "task_dir = pathlib.Path(sys.argv[1])\n"
        "for i in range(50):\n"
        "    print('loop-output', i, flush=True)\n"
        "    if i == 2:\n"
        "        (task_dir / 'evaluation.json').write_text(json.dumps({'iteration': 1, 'result': 'blocked', 'nextAction': 'ask_user', 'askUserQuestion': {'question': 'Continue?'}}))\n"
        "    time.sleep(0.05)\n"
        "pathlib.Path(sys.argv[2]).write_text('completed')\n"
    )

    code, stdout, stderr = stream_agent_command(
        [sys.executable, str(script), str(tmp_path), str(marker)],
        task_dir=tmp_path,
        agent="codex",
        phase="generator",
        cwd=str(tmp_path),
        timeout=10,
    )

    assert code == -3
    assert "requires human input" in stderr
    assert "loop-output 2" in stdout
    assert not marker.exists(), "opt-in interrupt should stop before normal completion"
    events = [json.loads(line) for line in (tmp_path / 'events.jsonl').read_text().splitlines()]
    event_types = [event.get('type') for event in events]
    assert 'agent_input_pending' in event_types
    assert 'agent_stopped_for_input' in event_types
