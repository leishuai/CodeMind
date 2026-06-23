"""Terminal input helpers for AutoMind TUI.

Python's plain ``input()`` only gets arrow-key editing when the ``readline``
module has been imported. Some AutoMind entry points did not import it, so
left/right arrows were echoed as raw ANSI sequences such as ``^[[C`` and those
bytes polluted the editable line. Keep the helper tiny and dependency-free:
import readline when available, then delegate to ``input`` so tests and
non-interactive shells keep the same behavior.
"""
from __future__ import annotations

import builtins
import re
from functools import lru_cache

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _readline_safe_prompt(prompt: str) -> str:
    """Wrap ANSI escape sequences so readline excludes them from prompt width.

    GNU readline/libedit treat bytes between \001 and \002 as non-printing.
    Without these markers, a colored prompt is counted as visible text. Long input
    lines then wrap at the wrong column, and Backspace can trigger a broken
    redisplay that appears to erase the whole line.
    """
    if not prompt or "\x1b" not in prompt:
        return prompt
    return _ANSI_RE.sub(lambda match: "\001" + match.group(0) + "\002", prompt)


@lru_cache(maxsize=1)
def enable_line_editing() -> bool:
    """Enable terminal line editing for ``input`` when readline is available.

    Returns True when readline/libedit was imported successfully. If unavailable,
    callers still fall back to normal ``input``; the command remains usable, just
    without rich arrow-key editing.
    """
    try:
        import readline  # noqa: F401  # imported for side effect on input()
    except Exception:
        return False
    return True


def tui_input(prompt: str = "") -> str:
    """Read one TUI line with readline/libedit editing enabled when possible."""
    readline_enabled = enable_line_editing()
    safe_prompt = _readline_safe_prompt(prompt) if readline_enabled else prompt
    return builtins.input(safe_prompt)
