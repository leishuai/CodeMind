"""User natural-language messages for CodeMind TUI/shell sessions."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.session.events import append_event
from orchestrator.state import read_runtime_state, update_runtime_state


def messages_path(task_dir: Path) -> Path:
    return task_dir / "user-messages.json"


def read_user_messages(task_dir: Path) -> list[dict[str, Any]]:
    path = messages_path(task_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def write_user_messages(task_dir: Path, messages: list[dict[str, Any]]) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    messages_path(task_dir).write_text(json.dumps(messages, ensure_ascii=False, indent=2) + "\n")


def append_user_message(task_dir: Path, text: str, *, source: str = "tui_shell") -> dict[str, Any]:
    messages = read_user_messages(task_dir)
    now = datetime.now().isoformat(timespec="seconds")
    message = {
        "id": f"user-message-{len(messages) + 1:03d}",
        "createdAt": now,
        "source": source,
        "text": text,
        "delivery": {"mode": "next_invocation_prompt", "status": "pending"},
    }
    messages.append(message)
    write_user_messages(task_dir, messages)
    update_runtime_state(task_dir, latestUserMessage=message, userMessages=messages)
    append_event(
        task_dir,
        "user_message",
        f"User message recorded: {text[:100]}",
        source=source,
        replace_key=f"user-message:{message['id']}",
        data={"messageId": message["id"]},
    )
    return message


def pending_user_messages_prompt_context(task_dir: Path) -> str:
    messages = [m for m in read_user_messages(task_dir) if (m.get("delivery") or {}).get("status") != "delivered"]
    if not messages:
        return ""
    lines = [
        "",
        "## CodeMind user messages from TUI/session",
        "",
        "The user sent the following natural-language message(s) through the CodeMind TUI/session. Treat them as user intent or clarification for the current task, reconcile them with existing Requirements/TestCases/Plan/workflow state, and continue through the CodeMind workflow. If the message changes scope or creates risk, update artifacts and route through ask_user/replan as appropriate.",
    ]
    for item in messages:
        lines.append(f"- {item.get('id')}: {item.get('text')}")
    return "\n".join(lines)


def mark_pending_user_messages_delivered(task_dir: Path, *, mode: str = "prompt") -> None:
    messages = read_user_messages(task_dir)
    if not messages:
        return
    changed = False
    for item in messages:
        delivery = item.get("delivery") if isinstance(item.get("delivery"), dict) else {}
        if delivery.get("status") != "delivered":
            delivery["mode"] = mode
            delivery["status"] = "delivered"
            delivery["deliveredAt"] = datetime.now().isoformat(timespec="seconds")
            item["delivery"] = delivery
            changed = True
    if changed:
        write_user_messages(task_dir, messages)
        state = read_runtime_state(task_dir) or {}
        latest = messages[-1]
        update_runtime_state(task_dir, latestUserMessage=latest, userMessages=messages)
