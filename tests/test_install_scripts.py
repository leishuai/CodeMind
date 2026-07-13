"""Smoke tests for the CodeAutonomy install scripts.

These tests intentionally do not run the full installation (which requires git
repository access and mutates the filesystem). Instead, they cover:

- bash syntax parsing (bash -n);
- --help / -h flag behaviour (exit code, expected output);
- presence of structural markers (environment variables, helper functions,
  required script references).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = PROJECT_ROOT / "install.sh"
INSTALL_CURL_SH = PROJECT_ROOT / "install-curl.sh"


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    import os

    # Install scripts reference $HOME / $PATH (command -v git/python3). Keep a
    # minimal environment instead of clearing everything so --help mode works
    # on CI nodes that strip the environment.
    minimal_env = {
        "HOME": os.environ.get("HOME", str(Path.home())),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=minimal_env)
    return proc.returncode, proc.stdout, proc.stderr


def test_install_scripts_exist() -> None:
    assert INSTALL_SH.is_file(), f"install.sh not found at {INSTALL_SH}"
    assert INSTALL_CURL_SH.is_file(), f"install-curl.sh not found at {INSTALL_CURL_SH}"


def test_bash_syntax_check_install_sh() -> None:
    assert shutil.which("bash"), "bash is required for install-script smoke tests"
    code, _out, err = _run(["bash", "-n", str(INSTALL_SH)])
    assert code == 0, f"bash -n failed on install.sh: {err}"


def test_bash_syntax_check_install_curl_sh() -> None:
    assert shutil.which("bash"), "bash is required for install-script smoke tests"
    code, _out, err = _run(["bash", "-n", str(INSTALL_CURL_SH)])
    assert code == 0, f"bash -n failed on install-curl.sh: {err}"


def test_install_sh_help_long_flag() -> None:
    code, out, _err = _run(["bash", str(INSTALL_SH), "--help"])
    assert code == 0, f"install.sh --help exited with {code}"
    assert "CodeAutonomy installer" in out
    assert "AUTOMIND_REPO" in out
    assert "AUTOMIND_HOME" in out


def test_install_sh_help_short_flag() -> None:
    code, out, _err = _run(["bash", str(INSTALL_SH), "-h"])
    assert code == 0, f"install.sh -h exited with {code}"
    assert "CodeAutonomy installer" in out


def test_install_curl_sh_help_long_flag() -> None:
    code, out, _err = _run(["bash", str(INSTALL_CURL_SH), "--help"])
    assert code == 0, f"install-curl.sh --help exited with {code}"
    assert "bootstrap" in out.lower()
    assert "curl -fsSL" in out
    assert "AUTOMIND_REPO" in out


def test_install_curl_sh_help_short_flag() -> None:
    code, out, _err = _run(["bash", str(INSTALL_CURL_SH), "-h"])
    assert code == 0, f"install-curl.sh -h exited with {code}"
    assert "bootstrap" in out.lower()


def test_install_sh_structural_markers() -> None:
    """Guards against accidental removal of key structural elements."""
    text = INSTALL_SH.read_text()
    for marker in [
        "set -euo pipefail",
        "AUTOMIND_REPO",
        "AUTOMIND_HOME",
        "write_git_guard",
        "automind.sh",
        "install.sh",
        'PRIMARY_WRAPPER="$AUTOMIND_BIN_DIR/codeautonomy"',
        'LEGACY_WRAPPER="$AUTOMIND_BIN_DIR/automind"',
        "--command-name codeautonomy",
        "--command-name automind",
    ]:
        assert marker in text, f"install.sh missing structural marker: {marker}"


def test_install_curl_sh_structural_markers() -> None:
    """Guards against accidental removal of key structural elements."""
    text = INSTALL_CURL_SH.read_text()
    for marker in [
        "set -euo pipefail",
        "BOOTSTRAP_URL",
        "AUTOMIND_REPO",
        "AUTOMIND_HOME",
        "write_git_guard",
        "install.sh",
        "automind.sh",
    ]:
        assert marker in text, f"install-curl.sh missing structural marker: {marker}"
