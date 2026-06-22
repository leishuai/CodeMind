from __future__ import annotations

from pathlib import Path

from orchestrator.summary import (
    _command_core,
    _command_is_blocked,
    _select_latest_non_blocked_command,
    extract_manual_intervention_lessons,
)


def test_command_core_drops_flags_and_keeps_executable_subcommand() -> None:
    assert _command_core("xcodebuild test -scheme App -destination id=...") == "xcodebuild test"
    assert _command_core("  devicectl device install app --device X ") == "devicectl device"
    assert _command_core("") == ""
    assert _command_core("--only-flags here") == "here"


def test_command_is_blocked_matches_core_against_signature() -> None:
    sigs = ["xcodebuild test hit root install style is not supported"]
    assert _command_is_blocked("xcodebuild test -scheme App", sigs) is True
    # A different command core must not be considered blocked.
    assert _command_is_blocked("devicectl device install app", sigs) is False
    # Empty command is never blocked.
    assert _command_is_blocked("", sigs) is False


def test_select_latest_non_blocked_skips_blocked_and_prefers_recent() -> None:
    blocked = ["xcodebuild test root install style"]
    commands = [
        {"command": "devicectl device install app"},
        {"command": "xcodebuild test -scheme App"},  # blocked, latest
    ]
    chosen = _select_latest_non_blocked_command(commands, blocked)
    assert chosen is not None
    assert chosen["command"] == "devicectl device install app"


def test_select_latest_returns_none_when_all_blocked() -> None:
    blocked = ["xcodebuild test root install style"]
    commands = [{"command": "xcodebuild test -scheme App"}]
    assert _select_latest_non_blocked_command(commands, blocked) is None


def test_select_latest_returns_most_recent_when_nothing_blocked() -> None:
    commands = [
        {"command": "echo first"},
        {"command": "echo second"},
    ]
    chosen = _select_latest_non_blocked_command(commands, [])
    assert chosen is not None
    assert chosen["command"] == "echo second"


def test_extract_manual_intervention_lessons_picks_up_human_bypass_dirs(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    # iter-* dirs must be ignored: they are covered by the command scanner.
    (logs / "iter-1").mkdir(parents=True)
    (logs / "iter-1" / "commands.md").write_text("cmd")
    # Human-intervention dirs that the iter-* scan never sees.
    (logs / "manual-cache-reset").mkdir()
    (logs / "manual-cache-reset" / "app-assemble-debug-after-cache-reset.log").write_text("ok")
    (logs / "cleanup").mkdir()
    (logs / "cleanup" / "git-clean-preview.txt").write_text("preview")
    (logs / "manual-cleanup-20260612-183531").mkdir()
    (logs / "manual-cleanup-20260612-183531" / "task-working-tree.patch").write_text("patch")

    lessons = extract_manual_intervention_lessons(logs)

    assert len(lessons) == 3
    joined = "\n".join(lessons)
    assert "manual-cache-reset" in joined
    assert "app-assemble-debug-after-cache-reset.log" in joined
    assert "cleanup" in joined
    assert "manual-cleanup-20260612-183531" in joined
    # iter-* content must never leak into manual-intervention lessons.
    assert "iter-1" not in joined


def test_extract_manual_intervention_lessons_skips_empty_and_missing() -> None:
    # Missing logs dir -> no lessons, no error.
    assert extract_manual_intervention_lessons(Path("/nonexistent/logs/xyz")) == []


def test_extract_manual_intervention_lessons_ignores_empty_dirs(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    (logs / "manual-empty").mkdir(parents=True)  # no files inside
    assert extract_manual_intervention_lessons(logs) == []
