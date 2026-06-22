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
from functools import lru_cache


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
    enable_line_editing()
    return builtins.input(prompt)
