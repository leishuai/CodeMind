"""Console and command helpers for the CodeMind orchestrator."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from orchestrator.config import AUTOMIND_WORKSPACE_ROOT, BLUE, GREEN, NC, RED, YELLOW


def log(msg: str):
    print(f"{BLUE}[CodeMind]{NC} {msg}")


def success(msg: str):
    print(f"{GREEN}[CodeMind]{NC} {msg}")


def warn(msg: str):
    print(f"{YELLOW}[CodeMind]{NC} {msg}")


def error(msg: str):
    print(f"{RED}[CodeMind]{NC} {msg}", file=sys.stderr)


def run_cmd(cmd: list, capture: bool = True, cwd: str = None, timeout: int | None = None) -> tuple:
    """Run command and return (returncode, stdout, stderr)."""
    if timeout is None:
        raw_timeout = os.environ.get("AUTOMIND_CMD_TIMEOUT", "43200")
        try:
            timeout = int(raw_timeout)
        except ValueError:
            timeout = 43200
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            cwd=cwd or str(AUTOMIND_WORKSPACE_ROOT),
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timeout after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


def read_tail(path: Path, limit_chars: int = 12_000) -> str:
    """Read a bounded tail from a text file for prompt context."""
    if not path.exists():
        return ""
    text = path.read_text(errors="ignore")
    if len(text) <= limit_chars:
        return text
    return text[-limit_chars:]


def read_head(path: Path, limit_chars: int = 4_000) -> str:
    """Read a bounded head from a text file for curated seed context."""
    if not path.exists():
        return ""
    text = path.read_text(errors="ignore")
    if len(text) <= limit_chars:
        return text
    return text[:limit_chars].rstrip() + "\n\n...[truncated for Reuse.md]..."
