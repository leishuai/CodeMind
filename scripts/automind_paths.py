"""Shared path resolution for CodeMind helper scripts.

Runtime root is the CodeMind installation/checkout containing scripts and
requirements. Workspace root is the caller's target project; task artifacts and
project-local helper virtualenvs live under it.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[1]


def resolve_workspace_root() -> Path:
    raw = os.environ.get("AUTOMIND_WORKSPACE_ROOT") or os.environ.get("AUTOMIND_PROJECT_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd().resolve()


WORKSPACE_ROOT = resolve_workspace_root()
TASKS_DIR = WORKSPACE_ROOT / ".automind" / "tasks"
SUMMARY_DIR = WORKSPACE_ROOT / ".automind" / "summary"
CHECKPOINTS_DIR = WORKSPACE_ROOT / ".automind" / "checkpoints"
ANDROID_TOOLS_PY = WORKSPACE_ROOT / ".venv-android-tools" / "bin" / "python"
RUNTIME_ANDROID_TOOLS_PY = RUNTIME_ROOT / ".venv-android-tools" / "bin" / "python"
IOS_TOOLS_PY = WORKSPACE_ROOT / ".venv-ios-tools" / "bin" / "python"
VISUAL_TOOLS_PY = WORKSPACE_ROOT / ".venv-visual-tools" / "bin" / "python"

# Propagate the resolved roots to child processes even if they run with cwd set
# to the CodeMind runtime checkout.
os.environ.setdefault("AUTOMIND_RUNTIME_ROOT", str(RUNTIME_ROOT))
os.environ.setdefault("AUTOMIND_WORKSPACE_ROOT", str(WORKSPACE_ROOT))


def workspace_path(path: str | Path) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (WORKSPACE_ROOT / p).resolve()


def runtime_path(path: str | Path) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (RUNTIME_ROOT / p).resolve()


def rel_to_workspace(path: str | Path) -> str:
    p = Path(path).resolve()
    try:
        return str(p.relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


# --- requirements satisfaction check (mirror of orchestrator.automation_tools)
#
# Helper scripts run standalone, so they re-derive the same check instead of
# importing the orchestrator package. A venv is only "stale" when an installed
# package no longer satisfies the requirements version constraints (or is
# missing); a relaxed bound or a locally installed higher version that still
# satisfies the range is NOT stale. Keep the venv dirs and requirements file
# names in sync with AUTOMATION_TOOL_PROFILES.

_AUTOMATION_VENV_DIRS = {
    "android": WORKSPACE_ROOT / ".venv-android-tools",
    "ios": WORKSPACE_ROOT / ".venv-ios-tools",
    "visual": WORKSPACE_ROOT / ".venv-visual-tools",
}
_AUTOMATION_REQ_FILES = {
    "android": RUNTIME_ROOT / "requirements" / "android-tools.txt",
    "ios": RUNTIME_ROOT / "requirements" / "ios-tools.txt",
    "visual": RUNTIME_ROOT / "requirements" / "visual-tools.txt",
}

_REQS_SATISFY_PROBE = r"""
import sys, json, re
try:
    from importlib import metadata as _md
except Exception:
    _md = None

def parse_version(v):
    # Drop the local segment, then truncate at the first pre/dev/post letter so
    # e.g. "2.0.dev0" -> "2.0" and "1.0rc1" -> "1.0" (numeric release only).
    head = re.split(r"[^0-9.]", v.split("+")[0], 1)[0]
    parts = re.findall(r"\d+", head)
    return tuple(int(p) for p in parts) if parts else (0,)

def cmp_ver(a, b):
    a, b = parse_version(a), parse_version(b)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return (a > b) - (a < b)

def satisfies(installed, spec):
    spec = (spec or "").strip()
    if not spec:
        return True
    for clause in spec.split(","):
        clause = clause.strip()
        m = re.match(r"(==|!=|>=|<=|~=|>|<)\s*(.+)", clause)
        if not m:
            continue
        op, ver = m.group(1), m.group(2).strip()
        c = cmp_ver(installed, ver)
        if op == ">=" and not c >= 0: return False
        if op == ">" and not c > 0: return False
        if op == "<=" and not c <= 0: return False
        if op == "<" and not c < 0: return False
        if op == "==" and not c == 0: return False
        if op == "!=" and not c != 0: return False
        if op == "~=" and not c >= 0: return False
    return True

req_path = sys.argv[1]
unsatisfied = []
try:
    lines = open(req_path, "r", encoding="utf-8").read().splitlines()
except Exception:
    print(json.dumps({"satisfied": True, "unsatisfied": []}))
    raise SystemExit(0)
for raw in lines:
    line = raw.split("#", 1)[0].strip()
    if not line:
        continue
    m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(.*)$", line)
    if not m:
        continue
    name, spec = m.group(1), m.group(2)
    if _md is None:
        continue
    try:
        installed = _md.version(name)
    except Exception:
        unsatisfied.append({"name": name, "spec": spec, "reason": "not_installed"})
        continue
    if not satisfies(installed, spec):
        unsatisfied.append({"name": name, "installed": installed, "spec": spec, "reason": "version_mismatch"})
print(json.dumps({"satisfied": not unsatisfied, "unsatisfied": unsatisfied}))
"""


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def venv_requirements_current(target: str) -> bool:
    """True when installed packages still satisfy the requirements constraints.

    A missing venv is handled by the module-import readiness check, so it is not
    flagged stale here. A relaxed bound or a locally installed higher version
    that still satisfies the range is NOT stale.
    """
    venv_dir = _AUTOMATION_VENV_DIRS.get(target)
    if not venv_dir or not venv_dir.exists():
        return True
    req = _AUTOMATION_REQ_FILES.get(target)
    if not req or not req.exists():
        return True
    py = _venv_python(venv_dir)
    if not py.exists():
        return True
    try:
        proc = subprocess.run(
            [str(py), "-c", _REQS_SATISFY_PROBE, str(req)],
            text=True, capture_output=True, timeout=30,
        )
    except Exception:
        return True
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return True
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return True
    return bool(data.get("satisfied", True))
