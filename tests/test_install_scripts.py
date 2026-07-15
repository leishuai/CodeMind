"""Smoke tests for the CodeMind install scripts.

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
LARK_BUILD_SH = PROJECT_ROOT / "scripts" / "ensure_lark_bridge_build.sh"


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
    assert LARK_BUILD_SH.is_file(), f"Lark build helper not found at {LARK_BUILD_SH}"


def test_bash_syntax_check_install_sh() -> None:
    assert shutil.which("bash"), "bash is required for install-script smoke tests"
    code, _out, err = _run(["bash", "-n", str(INSTALL_SH)])
    assert code == 0, f"bash -n failed on install.sh: {err}"


def test_bash_syntax_check_install_curl_sh() -> None:
    assert shutil.which("bash"), "bash is required for install-script smoke tests"
    code, _out, err = _run(["bash", "-n", str(INSTALL_CURL_SH)])
    assert code == 0, f"bash -n failed on install-curl.sh: {err}"


def test_bash_syntax_check_lark_build_helper() -> None:
    assert shutil.which("bash"), "bash is required for install-script smoke tests"
    code, _out, err = _run(["bash", "-n", str(LARK_BUILD_SH)])
    assert code == 0, f"bash -n failed on Lark build helper: {err}"


def test_install_sh_help_long_flag() -> None:
    code, out, _err = _run(["bash", str(INSTALL_SH), "--help"])
    assert code == 0, f"install.sh --help exited with {code}"
    assert "CodeMind installer" in out
    assert "AUTOMIND_REPO" in out
    assert "AUTOMIND_HOME" in out


def test_install_sh_help_short_flag() -> None:
    code, out, _err = _run(["bash", str(INSTALL_SH), "-h"])
    assert code == 0, f"install.sh -h exited with {code}"
    assert "CodeMind installer" in out


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


def test_installers_invalidate_lark_bridge_runtime_cache() -> None:
    for script in [INSTALL_SH, INSTALL_CURL_SH]:
        text = script.read_text()
        assert 'lark-bridge/dist' in text
        assert 'lark-bridge/node_modules' in text
        assert 'rm -rf' in text


def test_install_sh_creates_canonical_and_legacy_cli_wrappers() -> None:
    text = INSTALL_SH.read_text()
    assert 'write_wrapper "codemind"' in text
    assert 'write_wrapper "automind"' in text
    assert 'export AUTOMIND_CLI_DISPLAY="$name"' in text


def test_installers_advertise_codemind_and_preserve_runtime_namespace() -> None:
    install_text = INSTALL_SH.read_text()
    bootstrap_text = INSTALL_CURL_SH.read_text()
    assert "CodeMind installation complete." in install_text
    assert "/codemind and the legacy /automind alias" in install_text
    assert "CodeMind curl bootstrap installer" in bootstrap_text
    for text in (install_text, bootstrap_text):
        assert 'AUTOMIND_HOME="${AUTOMIND_HOME:-$HOME/.automind/automind}"' in text


def _minimal_bridge(root: Path) -> Path:
    bridge = root / "lark-bridge"
    (bridge / "src").mkdir(parents=True)
    (bridge / "package.json").write_text('{"scripts":{"build":"tsc"}}\n')
    (bridge / "package-lock.json").write_text('{"lockfileVersion":3}\n')
    (bridge / "tsconfig.json").write_text('{"compilerOptions":{}}\n')
    (bridge / "src" / "main.ts").write_text("export const value = 1;\n")
    return bridge


def _fake_tool(bin_dir: Path, name: str, body: str) -> Path:
    tool = bin_dir / name
    tool.write_text("#!/usr/bin/env bash\nset -e\n" + body)
    tool.chmod(0o755)
    return tool


def test_lark_build_helper_accepts_node_18(tmp_path: Path) -> None:
    bridge = _minimal_bridge(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_tool(fake_bin, "node", "echo v18.20.0\n")
    _fake_tool(
        fake_bin,
        "npm",
        'if [[ "$1" == "ci" ]]; then mkdir -p node_modules; fi\n'
        'if [[ "$1 $2" == "run build" ]]; then mkdir -p dist; echo built > dist/main.js; fi\n',
    )
    import os

    result = subprocess.run(
        ["bash", str(LARK_BUILD_SH), str(bridge)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
    )
    assert result.returncode == 0, result.stderr
    assert (bridge / "dist" / "main.js").exists()


def test_lark_build_helper_rejects_old_node(tmp_path: Path) -> None:
    bridge = _minimal_bridge(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_tool(fake_bin, "node", "echo v16.20.2\n")
    import os

    result = subprocess.run(
        ["bash", str(LARK_BUILD_SH), str(bridge)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
    )
    assert result.returncode != 0
    assert "Node.js 18 or newer" in result.stderr
    assert "v16.20.2" in result.stderr


def test_lark_build_helper_rejects_unparseable_node_version(tmp_path: Path) -> None:
    bridge = _minimal_bridge(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_tool(fake_bin, "node", "echo development-build\n")
    import os

    result = subprocess.run(
        ["bash", str(LARK_BUILD_SH), str(bridge)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
    )
    assert result.returncode != 0
    assert "Unable to determine the Node.js version" in result.stderr


def test_lark_build_helper_uses_fingerprints_and_lazy_npm(tmp_path: Path) -> None:
    bridge = tmp_path / "lark-bridge"
    src = bridge / "src"
    fake_bin = tmp_path / "bin"
    src.mkdir(parents=True)
    fake_bin.mkdir()

    (bridge / "package.json").write_text('{"scripts":{"build":"tsc"}}\n')
    (bridge / "package-lock.json").write_text('{"lockfileVersion":3}\n')
    (bridge / "tsconfig.json").write_text('{"compilerOptions":{}}\n')
    (src / "main.ts").write_text("export const value = 1;\n")

    npm_log = tmp_path / "npm.log"
    fake_npm = fake_bin / "npm"
    fake_npm.write_text(
        "#!/usr/bin/env bash\n"
        "set -e\n"
        "echo \"$*\" >> \"$NPM_LOG\"\n"
        "if [[ \"$1\" == \"ci\" || \"$1\" == \"install\" ]]; then mkdir -p node_modules; fi\n"
        "if [[ \"$1 $2\" == \"run build\" ]]; then mkdir -p dist; echo built > dist/main.js; fi\n"
    )
    fake_npm.chmod(0o755)
    fake_node = fake_bin / "node"
    fake_node.write_text("#!/usr/bin/env bash\necho v18.20.0\n")
    fake_node.chmod(0o755)

    import os

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "NPM_LOG": str(npm_log),
    }

    def run_helper(*extra: str) -> None:
        subprocess.run(
            ["bash", str(LARK_BUILD_SH), str(bridge), *extra],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    run_helper()
    assert npm_log.read_text().splitlines() == ["ci", "run build"]

    run_helper()
    assert npm_log.read_text().splitlines() == ["ci", "run build"]

    (src / "main.ts").write_text("export const value = 2;\n")
    run_helper()
    assert npm_log.read_text().splitlines() == ["ci", "run build", "run build"]

    (bridge / "package-lock.json").write_text('{"lockfileVersion":3,"changed":true}\n')
    run_helper()
    assert npm_log.read_text().splitlines() == [
        "ci",
        "run build",
        "run build",
        "ci",
        "run build",
    ]


def test_lark_build_helper_stabilizes_when_npm_install_creates_lockfile(tmp_path: Path) -> None:
    bridge = tmp_path / "lark-bridge"
    src = bridge / "src"
    fake_bin = tmp_path / "bin"
    src.mkdir(parents=True)
    fake_bin.mkdir()

    (bridge / "package.json").write_text('{"scripts":{"build":"tsc"}}\n')
    (bridge / "tsconfig.json").write_text('{"compilerOptions":{}}\n')
    (src / "main.ts").write_text("export const value = 1;\n")

    npm_log = tmp_path / "npm.log"
    fake_npm = fake_bin / "npm"
    fake_npm.write_text(
        "#!/usr/bin/env bash\n"
        "set -e\n"
        "echo \"$*\" >> \"$NPM_LOG\"\n"
        "if [[ \"$1\" == \"install\" ]]; then\n"
        "  mkdir -p node_modules\n"
        "  echo '{\"lockfileVersion\":3}' > package-lock.json\n"
        "fi\n"
        "if [[ \"$1 $2\" == \"run build\" ]]; then mkdir -p dist; echo built > dist/main.js; fi\n"
    )
    fake_npm.chmod(0o755)
    fake_node = fake_bin / "node"
    fake_node.write_text("#!/usr/bin/env bash\necho v18.20.0\n")
    fake_node.chmod(0o755)

    import os

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "NPM_LOG": str(npm_log),
    }
    command = ["bash", str(LARK_BUILD_SH), str(bridge)]
    subprocess.run(command, check=True, capture_output=True, text=True, env=env)
    subprocess.run(command, check=True, capture_output=True, text=True, env=env)

    assert npm_log.read_text().splitlines() == ["install", "run build"]
