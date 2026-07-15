"""Durable recovery state for persistent front-end conversation sessions."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


STATE_FILE = "conversation-state.json"
TURNS_FILE = "conversation-turns.jsonl"
RECENT_TURN_LIMIT = 8
TURN_TEXT_LIMIT = 12000


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default_state() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "turnCount": 0,
        "generation": 0,
        "generationStartedAtTurn": 0,
        "syncedThroughTurnId": None,
        "summaryVersion": 0,
        "contextSummary": "",
        "recentTurns": [],
        "updatedAt": _now(),
    }


def read_conversation_state(task_dir: Path) -> dict[str, Any]:
    path = task_dir / STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    if not isinstance(data, dict):
        return _default_state()
    return {**_default_state(), **data}


def write_conversation_state(task_dir: Path, state: dict[str, Any]) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = _now()
    (task_dir / STATE_FILE).write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def start_generation(task_dir: Path, state: dict[str, Any], reason: str) -> dict[str, Any]:
    updated = {
        **state,
        "generation": int(state.get("generation", 0) or 0) + 1,
        "generationStartedAtTurn": int(state.get("turnCount", 0) or 0),
        "lastSessionResetReason": reason,
    }
    write_conversation_state(task_dir, updated)
    return updated


def should_rotate(state: dict[str, Any], threshold: int) -> bool:
    if threshold <= 0:
        return False
    current = int(state.get("turnCount", 0) or 0)
    started = int(state.get("generationStartedAtTurn", 0) or 0)
    return current > started and current - started >= threshold


def build_recovery_prompt(prompt: str, state: dict[str, Any]) -> str:
    summary = str(state.get("contextSummary") or "").strip()
    turns = state.get("recentTurns")
    if not isinstance(turns, list):
        turns = []
    if not summary and not turns:
        return prompt

    lines = [
        "The provider conversation session was recreated. Recover only from the",
        "durable visible-conversation context below, then handle the current turn.",
        "Treat live workspace/task metadata in the current request as authoritative.",
    ]
    if summary:
        lines.extend(["", "## Rolling conversation summary", summary])
    if turns:
        lines.extend(["", "## Recent visible turns"])
        for turn in turns[-RECENT_TURN_LIMIT:]:
            if not isinstance(turn, dict):
                continue
            user = str(turn.get("userText") or "").strip()
            assistant = str(turn.get("assistantReply") or "").strip()
            if user:
                lines.append(f"- user: {user[:2000]}")
            if assistant:
                lines.append(f"- assistant: {assistant[:3000]}")
    lines.extend(["", "## Current turn", prompt])
    return "\n".join(lines)


def append_conversation_turn(
    task_dir: Path,
    state: dict[str, Any],
    *,
    user_text: str,
    assistant_output: str,
    status: str,
) -> dict[str, Any]:
    turn_number = int(state.get("turnCount", 0) or 0) + 1
    turn_id = f"turn-{turn_number:06d}"
    turn = {
        "id": turn_id,
        "createdAt": _now(),
        "generation": int(state.get("generation", 0) or 0),
        "userText": user_text[:TURN_TEXT_LIMIT],
        "assistantReply": extract_assistant_reply(assistant_output)[:TURN_TEXT_LIMIT],
        "status": status,
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    with (task_dir / TURNS_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(turn, ensure_ascii=False) + "\n")

    summary = extract_context_summary(assistant_output)
    recent = state.get("recentTurns")
    if not isinstance(recent, list):
        recent = []
    recent = [*recent, turn][-RECENT_TURN_LIMIT:]
    updated = {
        **state,
        "turnCount": turn_number,
        "syncedThroughTurnId": turn_id,
        "recentTurns": recent,
    }
    if summary:
        updated["contextSummary"] = summary
        updated["summaryVersion"] = int(state.get("summaryVersion", 0) or 0) + 1
    write_conversation_state(task_dir, updated)
    return updated


def append_internal_result(
    task_dir: Path,
    state: dict[str, Any],
    *,
    assistant_output: str,
    status: str,
) -> dict[str, Any]:
    """Record a tool-result follow-up without inventing a second user turn."""
    event = {
        "kind": "internal_result",
        "createdAt": _now(),
        "generation": int(state.get("generation", 0) or 0),
        "forTurnId": state.get("syncedThroughTurnId"),
        "assistantReply": extract_assistant_reply(assistant_output)[
            :TURN_TEXT_LIMIT
        ],
        "status": status,
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    with (task_dir / TURNS_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    recent = state.get("recentTurns")
    if not isinstance(recent, list):
        recent = []
    recent = [dict(item) for item in recent if isinstance(item, dict)]
    if recent:
        recent[-1]["assistantReply"] = extract_assistant_reply(assistant_output)[
            :TURN_TEXT_LIMIT
        ]
        recent[-1]["status"] = status
    summary = extract_context_summary(assistant_output)
    updated = {**state, "recentTurns": recent[-RECENT_TURN_LIMIT:]}
    if summary:
        updated["contextSummary"] = summary
        updated["summaryVersion"] = int(state.get("summaryVersion", 0) or 0) + 1
    write_conversation_state(task_dir, updated)
    return updated


def extract_context_summary(raw: str) -> str:
    """Read contextSummary from the first JSON object without owning its schema."""
    start = raw.find("{")
    if start < 0:
        return ""
    try:
        data, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    value = data.get("contextSummary")
    return value.strip()[:12000] if isinstance(value, str) else ""


def extract_assistant_reply(raw: str) -> str:
    """Persist only the user-visible reply, not actions or protocol metadata."""
    start = raw.find("{")
    if start >= 0:
        try:
            data, _ = json.JSONDecoder().raw_decode(raw[start:])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("reply"), str):
            return data["reply"].strip()
    return raw.strip()


def is_context_overflow(output: str) -> bool:
    text = (output or "").lower()
    return any(
        marker in text
        for marker in (
            "context window",
            "context length",
            "context overflow",
            "ran out of room",
            "maximum context",
        )
    )
