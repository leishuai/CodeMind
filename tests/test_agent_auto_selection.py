"""Tests for CodeMind coding-agent auto selection."""
from __future__ import annotations

from orchestrator import agents


def test_resolve_agent_auto_selects_first_available(monkeypatch):
    calls: list[str] = []

    def fake_preflight(name: str):
        calls.append(name)
        if name == "codex":
            return False, {"category": "missing_binary", "agent": name, "binary": "codex"}
        if name == "claude":
            return True, {"category": "ok", "agent": name, "binary": "claude"}
        return True, {"category": "ok", "agent": name, "binary": "traecli"}

    monkeypatch.setattr(agents, "preflight_agent", fake_preflight)

    selected, info = agents.resolve_agent("auto")

    assert selected == "claude"
    assert info["requested"] == "auto"
    assert info["selected"] == "claude"
    # Auto discovery records full diagnostics for every supported adapter, not
    # only the selected one, so a failure report can explain alternatives.
    assert calls == ["codex", "claude", "trae"]
    assert [item["agent"] for item in info["checked"]] == ["codex", "claude", "trae"]


def test_resolve_agent_auto_reports_all_missing(monkeypatch):
    def fake_preflight(name: str):
        binary = "traecli" if name == "trae" else name
        return False, {"category": "missing_binary", "agent": name, "binary": binary}

    monkeypatch.setattr(agents, "preflight_agent", fake_preflight)

    selected, info = agents.resolve_agent("auto")

    assert selected is None
    assert info["category"] == "no_available_agent"
    assert [item["agent"] for item in info["checked"]] == ["codex", "claude", "trae"]
    assert any("scaffold" in option for option in info["options"])


def test_resolve_agent_explicit_keeps_requested_agent(monkeypatch):
    def fake_preflight(name: str):
        assert name == "trae"
        return True, {"category": "ok", "agent": name, "binary": "traecli"}

    monkeypatch.setattr(agents, "preflight_agent", fake_preflight)

    selected, info = agents.resolve_agent("trae")

    assert selected == "trae"
    assert info["agent"] == "trae"
