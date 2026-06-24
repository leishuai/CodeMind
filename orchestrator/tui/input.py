"""Terminal input helpers for AutoMind TUI.

For non-interactive callers, keep the helper tiny and dependency-free: import
``readline`` when available, then delegate to ``input``.

For a real TTY, use a small raw-mode line editor instead of plain
``input()``/readline. AutoMind's shell prompt is colored and users often paste
large multi-line instructions. Readline/libedit handles that inconsistently on
macOS: redisplay can make Backspace look stuck before the prompt, and pasted
newlines can be accepted as Enter, sending only the first pasted line. The raw
editor below enables bracketed paste, keeps one editable buffer, supports basic
history/cursor keys, and returns the whole pasted multi-line text only after the
user presses Enter.
"""
from __future__ import annotations

import builtins
import os
import re
import shutil
import sys
import termios
import tty
import unicodedata
from functools import lru_cache

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_HISTORY: list[str] = []
_BRACKETED_PASTE_ON = "\x1b[?2004h"
_BRACKETED_PASTE_OFF = "\x1b[?2004l"
_PASTE_START = b"\x1b[200~"
_PASTE_END = b"\x1b[201~"


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


def _visible_text(text: str) -> str:
    """Render newlines visibly so a multi-line paste stays editable on one row."""
    return text.replace("\n", "\\n")


def _display_width(text: str) -> int:
    """Best-effort terminal column width for redraw cursor movement."""
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        if unicodedata.category(char)[0] == "C":
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _take_prefix_width(text: str, max_width: int) -> str:
    """Return the longest prefix whose display width is <= max_width."""
    if max_width <= 0:
        return ""
    out: list[str] = []
    width = 0
    for char in text:
        char_width = _display_width(char)
        if width + char_width > max_width:
            break
        out.append(char)
        width += char_width
    return "".join(out)


def _take_suffix_width(text: str, max_width: int) -> str:
    """Return the longest suffix whose display width is <= max_width."""
    if max_width <= 0:
        return ""
    out: list[str] = []
    width = 0
    for char in reversed(text):
        char_width = _display_width(char)
        if width + char_width > max_width:
            break
        out.append(char)
        width += char_width
    return "".join(reversed(out))


def _render_single_line_preview(text: str, cursor: int, max_width: int) -> tuple[str, int]:
    """Render a no-wrap preview plus cursor column for the raw-mode editor.

    The actual input buffer may contain hundreds of lines. Redrawing that whole
    buffer would let the terminal wrap it into many physical rows, while our
    next ``\r + clear line`` can only clear the current row. Keep display to one
    terminal row and use ellipses for hidden content; the returned value still
    points at the full, unmodified buffer.
    """
    max_width = max(1, max_width)
    cursor = max(0, min(cursor, len(text)))
    before = _visible_text(text[:cursor])
    after = _visible_text(text[cursor:])
    if _display_width(before + after) <= max_width:
        return before + after, _display_width(before)

    ellipsis = "…"
    after_width = _display_width(after)
    right_budget = min(after_width, max_width // 3) if after else 0
    left_budget = max_width - right_budget

    if _display_width(before) > left_budget:
        left = ellipsis + _take_suffix_width(before, max(0, left_budget - _display_width(ellipsis)))
    else:
        left = before

    cursor_col = _display_width(left)
    remaining = max(0, max_width - cursor_col)
    if after_width <= remaining:
        right = after
    elif remaining <= _display_width(ellipsis):
        right = _take_prefix_width(after, remaining)
    else:
        right = _take_prefix_width(after, remaining - _display_width(ellipsis)) + ellipsis
    return left + right, cursor_col


def _read_escape_sequence(fd: int) -> bytes:
    """Read a small ANSI escape/control sequence after the initial ESC byte."""
    second = os.read(fd, 1)
    if second != b"[":
        return b"\x1b" + second
    tail = bytearray()
    while len(tail) < 16:
        ch = os.read(fd, 1)
        tail.extend(ch)
        if ch in b"ABCDHF~":
            break
    return b"\x1b[" + bytes(tail)


def _read_bracketed_paste(fd: int) -> str:
    """Read bytes until the bracketed-paste terminator and normalize newlines."""
    data = bytearray()
    while True:
        ch = os.read(fd, 1)
        data.extend(ch)
        if data.endswith(_PASTE_END):
            del data[-len(_PASTE_END):]
            break
    text = bytes(data).decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_utf8_char(fd: int, first: bytes) -> str:
    """Read one UTF-8 character from raw stdin, starting with ``first``."""
    lead = first[0]
    if lead < 0x80:
        return first.decode("utf-8", errors="replace")
    if 0xC0 <= lead <= 0xDF:
        needed = 1
    elif 0xE0 <= lead <= 0xEF:
        needed = 2
    elif 0xF0 <= lead <= 0xF7:
        needed = 3
    else:
        needed = 0
    raw = bytearray(first)
    for _ in range(needed):
        raw.extend(os.read(fd, 1))
    return bytes(raw).decode("utf-8", errors="replace")


def _terminal_input(prompt: str) -> str:
    """Read one logical TUI input, preserving bracketed multi-line paste."""
    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    buffer: list[str] = []
    cursor = 0
    history_index: int | None = None
    draft: list[str] = []

    def redraw() -> None:
        prompt_width = _display_width(_ANSI_RE.sub("", prompt))
        terminal_width = shutil.get_terminal_size((80, 20)).columns
        max_preview_width = max(10, terminal_width - prompt_width - 1)
        rendered, cursor_col = _render_single_line_preview("".join(buffer), cursor, max_preview_width)
        sys.stdout.write("\r\x1b[0K" + prompt + rendered)
        back = _display_width(rendered) - cursor_col
        if back:
            sys.stdout.write(f"\x1b[{back}D")
        sys.stdout.flush()

    def replace_buffer(text: str) -> None:
        nonlocal buffer, cursor
        buffer = list(text)
        cursor = len(buffer)

    try:
        sys.stdout.write(_BRACKETED_PASTE_ON)
        sys.stdout.write(prompt)
        sys.stdout.flush()
        tty.setraw(fd)
        while True:
            raw = os.read(fd, 1)
            if raw in {b"\r", b"\n"}:
                value = "".join(buffer)
                if value.strip() and (not _HISTORY or _HISTORY[-1] != value):
                    _HISTORY.append(value)
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return value
            if raw == b"\x03":  # Ctrl+C
                sys.stdout.write("^C\r\n")
                sys.stdout.flush()
                raise KeyboardInterrupt
            if raw == b"\x04":  # Ctrl+D
                if not buffer:
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    raise EOFError
                if cursor < len(buffer):
                    del buffer[cursor]
                    redraw()
                continue
            if raw in {b"\x7f", b"\b"}:  # Backspace
                if cursor > 0:
                    del buffer[cursor - 1]
                    cursor -= 1
                    redraw()
                continue
            if raw == b"\x01":  # Ctrl+A
                cursor = 0
                redraw()
                continue
            if raw == b"\x05":  # Ctrl+E
                cursor = len(buffer)
                redraw()
                continue
            if raw == b"\x1b":
                seq = _read_escape_sequence(fd)
                if seq == _PASTE_START:
                    paste = _read_bracketed_paste(fd)
                    if paste:
                        buffer[cursor:cursor] = list(paste)
                        cursor += len(paste)
                        redraw()
                    continue
                if seq in {b"\x1b[D", b"\x1b[1D", b"\x1b[OD"}:  # Left
                    cursor = max(0, cursor - 1)
                    redraw()
                    continue
                if seq in {b"\x1b[C", b"\x1b[1C", b"\x1b[OC"}:  # Right
                    cursor = min(len(buffer), cursor + 1)
                    redraw()
                    continue
                if seq in {b"\x1b[H", b"\x1b[1~", b"\x1b[OH"}:  # Home
                    cursor = 0
                    redraw()
                    continue
                if seq in {b"\x1b[F", b"\x1b[4~", b"\x1b[OF"}:  # End
                    cursor = len(buffer)
                    redraw()
                    continue
                if seq == b"\x1b[3~" and cursor < len(buffer):  # Delete
                    del buffer[cursor]
                    redraw()
                    continue
                if seq == b"\x1b[A" and _HISTORY:  # Up
                    if history_index is None:
                        draft = buffer.copy()
                        history_index = len(_HISTORY) - 1
                    else:
                        history_index = max(0, history_index - 1)
                    replace_buffer(_HISTORY[history_index])
                    redraw()
                    continue
                if seq == b"\x1b[B" and history_index is not None:  # Down
                    if history_index < len(_HISTORY) - 1:
                        history_index += 1
                        replace_buffer(_HISTORY[history_index])
                    else:
                        history_index = None
                        buffer = draft.copy()
                        cursor = len(buffer)
                    redraw()
                    continue
                continue
            char = _read_utf8_char(fd, raw)
            if char and char >= " ":
                buffer[cursor:cursor] = list(char)
                cursor += len(char)
                redraw()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
        sys.stdout.write(_BRACKETED_PASTE_OFF)
        sys.stdout.flush()


def tui_input(prompt: str = "") -> str:
    """Read one TUI input with editing and bracketed-paste support when possible."""
    if sys.stdin.isatty() and sys.stdout.isatty():
        return _terminal_input(prompt)
    readline_enabled = enable_line_editing()
    safe_prompt = _readline_safe_prompt(prompt) if readline_enabled else prompt
    return builtins.input(safe_prompt)
