"""Automation helper setup and environment snapshot utilities.

This module owns project-local helper virtualenv setup for Android/iOS/visual
verification and the reusable environment snapshot written for evaluator runs.
It is imported by ``orchestrator.main``; public CLI entrypoints remain there.
"""
from __future__ import annotations

import json
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from orchestrator.config import (
    ANDROID_TOOLS_VENV,
    AUTOMATION_SETUP_DIR,
    AUTOMIND_ROOT,
    AUTOMIND_RUNTIME_ROOT,
    AUTOMIND_WORKSPACE_ROOT,
    IOS_TOOLS_VENV,
    VISUAL_TOOLS_VENV,
)
from orchestrator.console import error, run_cmd
from orchestrator.state import ensure_dir, read_runtime_state


def workspace_cwd() -> str:
    """cwd for user/project commands and task artifact operations."""
    return str(AUTOMIND_WORKSPACE_ROOT)


def collect_env_snapshot(task_dir: Path, iteration: int, runner_cmd: Optional[list[str]] = None) -> dict:
    """\u6536\u96c6\u672c\u8f6e\u8fd0\u884c\u73af\u5883，\u5199\u5165 logs/iter-N/env.json，\u5e2e\u52a9\u672c\u673a\u4e0b\u4e00\u4e2a task \u590d\u7528\u73af\u5883。"""
    def cmd_out(cmd: list[str]) -> str:
        code, stdout, stderr = run_cmd(cmd, cwd=workspace_cwd())
        if code != 0:
            return (stderr or stdout).strip()[:500]
        return stdout.strip()[:500]

    state = read_runtime_state(task_dir) or {}
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or str(Path.home() / "Library" / "Android" / "sdk")
    snapshot = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "taskId": state.get("taskId", task_dir.name),
        "iteration": iteration,
        "cwd": str(AUTOMIND_WORKSPACE_ROOT),
        "os": sys.platform,
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
            "prefix": sys.prefix,
            "venv": os.environ.get("VIRTUAL_ENV", ""),
        },
        "node": {
            "executable": shutil.which("node") or "",
            "version": cmd_out(["node", "--version"]) if shutil.which("node") else "missing",
        },
        "tools": {
            "adb": shutil.which("adb") or shutil.which(str(Path(android_home) / "platform-tools" / "adb")) or "missing",
            "adbutils": "unknown",
            "uiautomator2": "unknown",
            "xcodebuild": shutil.which("xcodebuild") or "missing",
        },
        "android": {
            "ANDROID_HOME": android_home,
            "deviceSerial": "",
            "model": "",
            "sdk": "",
        },
        "androidToolsPython": get_android_tools_python(),
        "runnerCommand": runner_cmd or [],
    }

    # Python package availability in the Android tools interpreter.
    android_py = snapshot["androidToolsPython"]
    for name in ["adbutils", "uiautomator2"]:
        code, stdout, stderr = run_cmd([
            android_py,
            "-c",
            f"import importlib.util; print('OK' if importlib.util.find_spec('{name}') else 'MISSING')",
        ], cwd=workspace_cwd())
        snapshot["tools"][name] = (stdout or stderr).strip() or "unknown"

    adb = snapshot["tools"].get("adb")
    if adb and adb != "missing":
        devices = cmd_out([adb, "devices", "-l"])
        snapshot["android"]["adbDevices"] = devices
        for line in devices.splitlines():
            if " device " in f" {line} ":
                snapshot["android"]["deviceSerial"] = line.split()[0]
                model_match = re.search(r"model:([^\s]+)", line)
                if model_match:
                    snapshot["android"]["model"] = model_match.group(1)
                break
        if snapshot["android"].get("deviceSerial"):
            serial = snapshot["android"]["deviceSerial"]
            snapshot["android"]["sdk"] = cmd_out([adb, "-s", serial, "shell", "getprop", "ro.build.version.sdk"])
            if not snapshot["android"].get("model"):
                snapshot["android"]["model"] = cmd_out([adb, "-s", serial, "shell", "getprop", "ro.product.model"])

    return snapshot



def _runtime_android_tools_candidates() -> list[Path]:
    runtime_venv = AUTOMIND_RUNTIME_ROOT / ".venv-android-tools"
    return [runtime_venv / "bin" / "python", runtime_venv / "bin" / "python3"]


def get_android_tools_python() -> str:
    """Return the preferred Python for Android harness tools on this machine.

    Prefer a project-local venv only when it has the required modules. If the
    project-local venv exists but setup failed (common under restricted pip
    network), fall back to the AutoMind runtime helper venv when it is already
    ready. Setup commands still create/repair the project-local venv; this
    fallback is read-only reuse of a known-good runtime helper.
    """
    candidates = [
        ANDROID_TOOLS_VENV / "bin" / "python",
        ANDROID_TOOLS_VENV / "bin" / "python3",
        *_runtime_android_tools_candidates(),
    ]
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK) and android_tools_python_ready(str(candidate)):
            return str(candidate)
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable


def android_tools_python_ready(python_exec: str) -> bool:
    """Check whether adbutils/uiautomator2 actually import in the selected Python.

    Uses a real import (not find_spec) so a half-installed/broken venv is
    rejected rather than picked (P2).
    """
    code, stdout, stderr = run_cmd([
        python_exec,
        "-c",
        "import adbutils, uiautomator2",
    ], cwd=workspace_cwd())
    return code == 0


AUTOMATION_TOOL_PROFILES = {
    "android": {
        "venv": ANDROID_TOOLS_VENV,
        "requirements": AUTOMIND_ROOT / "requirements" / "android-tools.txt",
        "packages": ["adbutils>=2.12,<3", "uiautomator2>=3.5,<4"],
        "modules": ["adbutils", "uiautomator2"],
        "systemTools": ["adb"],
        "notes": [
            "Installs Android Python automation helper packages into a project-local virtualenv only.",
            "Does not install Android Studio, Android SDK/platform-tools, adb, or change device settings.",
        ],
    },
    "ios": {
        "venv": IOS_TOOLS_VENV,
        "requirements": AUTOMIND_ROOT / "requirements" / "ios-tools.txt",
        "packages": ["pymobiledevice3>=9.12,<10"],
        "modules": ["pymobiledevice3"],
        "systemTools": ["xcodebuild", "xcrun"],
        "notes": [
            "Installs iOS Python screenshot/helper packages into a project-local virtualenv only.",
            "Does not install Xcode, change signing material, start tunneld/sudo services, or manipulate devices.",
        ],
    },
    "visual": {
        "venv": VISUAL_TOOLS_VENV,
        "requirements": AUTOMIND_ROOT / "requirements" / "visual-tools.txt",
        "packages": ["Pillow>=10,<12", "numpy>=1.26,<3", "imagehash>=4.3,<5"],
        "modules": ["PIL", "numpy", "imagehash"],
        "systemTools": [],
        "notes": [
            "Installs deterministic visual comparison helper packages into a project-local virtualenv only.",
            "Provides screenshot size/hash/diff inspection as a fallback when no vision-capable model is available.",
            "Does not install OCR engines, device SDKs, browser drivers, or privileged services.",
        ],
    },
}


def automation_venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def automation_setup_command_plan(target: str) -> dict:
    profile = AUTOMATION_TOOL_PROFILES[target]
    venv_dir = profile["venv"]
    py = automation_venv_python(venv_dir)
    packages = list(profile["packages"])
    requirements_path = profile.get("requirements")
    requirements = Path(requirements_path) if requirements_path else None
    install_cmd = [str(py), "-m", "pip", "install", "-U"]
    if requirements and requirements.exists():
        install_cmd += ["-r", str(requirements)]
    else:
        install_cmd += packages
    return {
        "target": target,
        "venv": str(venv_dir),
        "python": str(py),
        "requirements": str(requirements) if requirements else "",
        "requirementsExists": bool(requirements and requirements.exists()),
        "packages": packages,
        "commands": [
            [sys.executable, "-m", "venv", str(venv_dir)],
            [str(py), "-m", "pip", "install", "-U", "pip"],
            install_cmd,
        ],
        "systemToolsCheckedOnly": list(profile.get("systemTools", [])),
        "readyFallbacks": ([str(candidate) for candidate in _runtime_android_tools_candidates() if candidate.exists() and android_tools_python_ready(str(candidate))] if target == "android" else []),
        "willNotInstall": [
            "Android Studio / Android SDK / adb",
            "Xcode / Command Line Tools",
            "certificates, signing profiles, keychains, or device trust settings",
            "sudo services such as pymobiledevice3 tunneld",
        ],
        "notes": list(profile.get("notes", [])),
    }


def classify_setup_failure(log_text: str) -> dict:
    """Classify common helper setup failures into actionable diagnostics.

    Model-first triage: each return carries ``triageSource`` /
    ``needsModelReview``. Recognized DNS/permission/package-resolution patterns
    are ``code_deterministic``; the catch-all ``unknown`` fallback is
    ``requires_model_review`` so the caller reads the real step log instead of
    trusting a blind default.
    """
    lower = (log_text or "").lower()
    diagnostics: dict = {
        "category": "unknown",
        "summary": "Setup failed; inspect step logs for details.",
        "suggestions": [],
        "triageSource": "requires_model_review",
        "needsModelReview": True,
    }
    if "failed to resolve" in lower or "nameresolutionerror" in lower or "temporary failure in name resolution" in lower or "could not resolve host" in lower:
        diagnostics.update({
            "category": "network_or_dns",
            "summary": "Python package install could not resolve the package index host (network/DNS/proxy/private mirror issue).",
            "suggestions": [
                "Fix DNS/VPN/proxy/package-index access and rerun setup-automation-tools.",
                "Configure an approved pip index mirror via pip.conf/PIP_INDEX_URL if your environment requires one.",
                "Provide an approved offline wheelhouse and install from it if network is unavailable.",
                "If a runtime AutoMind .venv-android-tools is already ready, let preflight reuse it instead of reinstalling project-local helpers.",
                "For Android verification only, consider an explicit adb-only fallback if lower capability is acceptable.",
            ],
            "triageSource": "code_deterministic",
            "needsModelReview": False,
        })
    elif "permission denied" in lower or "not writable" in lower:
        diagnostics.update({
            "category": "permission",
            "summary": "Python package setup hit filesystem/cache permission issues.",
            "suggestions": [
                "Use a writable workspace and pip cache, or set PIP_CACHE_DIR to a writable path.",
                "Do not use sudo for AutoMind helper venvs; they are project-local user-space environments.",
            ],
            "triageSource": "code_deterministic",
            "needsModelReview": False,
        })
    elif "no matching distribution found" in lower or "could not find a version that satisfies" in lower:
        diagnostics.update({
            "category": "package_resolution",
            "summary": "pip could not resolve required helper package versions.",
            "suggestions": [
                "Check Python version compatibility and package index availability.",
                "Use a known-good runtime helper venv or approved mirror/wheelhouse.",
            ],
            "triageSource": "code_deterministic",
            "needsModelReview": False,
        })
    return diagnostics


def setup_failure_diagnostics(steps: list[dict]) -> list[dict]:
    out = []
    for step in steps:
        if step.get("exitCode") == 0:
            continue
        log_path = Path(str(step.get("log") or ""))
        log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
        diag = classify_setup_failure(log_text)
        diag.update({"step": step.get("name"), "log": str(log_path) if log_path else ""})
        out.append(diag)
    return out


def step_has_network_or_dns_failure(step: dict) -> bool:
    """Return whether a setup step failed for a transient package-index/network reason."""
    if step.get("exitCode") == 0:
        return False
    log_path = Path(str(step.get("log") or ""))
    log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
    return classify_setup_failure(log_text).get("category") == "network_or_dns"


def run_setup_step(cmd: list[str], log_dir: Path, name: str, timeout: int = 900) -> dict:
    started = datetime.now().isoformat(timespec="seconds")
    try:
        proc = subprocess.run(cmd, cwd=workspace_cwd(), text=True, capture_output=True, timeout=timeout)
        code = proc.returncode
        out = (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError as exc:
        code = 127
        out = f"[TOOL_MISSING] {cmd[0]} not found: {exc}"
    except subprocess.TimeoutExpired as exc:
        raw = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        code = 124
        out = raw + f"\n[TIMEOUT after {timeout}s]"
    ensure_dir(log_dir)
    (log_dir / f"{name}.log").write_text(out)
    (log_dir / f"{name}.exit-code.txt").write_text(str(code) + "\n")
    return {
        "name": name,
        "cmd": cmd,
        "exitCode": code,
        "startedAt": started,
        "log": str(log_dir / f"{name}.log"),
    }


def run_setup_step_with_bounded_network_retry(cmd: list[str], log_dir: Path, name: str, timeout: int = 900) -> dict:
    """Run a setup step, retrying once for transient network/DNS package-index failures.

    Helper venv setup is low-risk and project-local, so a single bounded retry
    is safe and avoids false blockers for momentary package-index/DNS hiccups.
    Keep retry evidence explicit and never loop indefinitely.
    """
    first = run_setup_step(cmd, log_dir, name, timeout=timeout)
    if not step_has_network_or_dns_failure(first):
        return first

    time.sleep(2)
    retry_name = f"{name}-retry1"
    retry = run_setup_step(cmd, log_dir, retry_name, timeout=timeout)
    retry["retryOf"] = name
    retry["retryReason"] = "network_or_dns"
    retry["previousAttempt"] = first
    return retry


def check_python_modules(python_exec: Path, modules: list[str]) -> dict:
    if not python_exec.exists():
        return {name: False for name in modules}
    code, stdout, stderr = run_cmd([
        str(python_exec),
        "-c",
        (
            "import importlib.util, json; "
            f"mods={modules!r}; "
            "print(json.dumps({name: bool(importlib.util.find_spec(name)) for name in mods}))"
        ),
    ], cwd=workspace_cwd())
    if code != 0:
        return {name: False for name in modules}
    try:
        data = json.loads(stdout or "{}")
    except Exception:
        return {name: False for name in modules}
    return {name: bool(data.get(name)) for name in modules}


def import_python_modules(python_exec: Path, modules: list[str]) -> dict:
    """Like check_python_modules but actually imports each module (P2).

    ``find_spec`` only proves the package is discoverable; it does not catch a
    half-installed or ABI-broken binary package (numpy/Pillow), which still has
    a spec but raises on import. A real import is the honest readiness signal.
    """
    if not python_exec.exists():
        return {name: False for name in modules}
    code, stdout, stderr = run_cmd([
        str(python_exec),
        "-c",
        (
            "import json; mods=" + repr(modules) + "; out={}\n"
            "for _m in mods:\n"
            "    try:\n"
            "        __import__(_m); out[_m]=True\n"
            "    except Exception:\n"
            "        out[_m]=False\n"
            "print(json.dumps(out))"
        ),
    ], cwd=workspace_cwd())
    if code != 0:
        return {name: False for name in modules}
    try:
        data = json.loads(stdout.strip().splitlines()[-1] if stdout.strip() else "{}")
    except Exception:
        return {name: False for name in modules}
    return {name: bool(data.get(name)) for name in modules}


# --- requirements satisfaction check (P1: detect when a venv needs reinstall) -
#
# A venv is only "stale" when an installed package no longer satisfies the
# requirements version constraints (or is missing). Merely editing the
# requirements file (relaxing a bound, comments, or a locally installed higher
# version that still satisfies the range) must NOT trigger a pip reinstall.
#
# The check runs a self-contained probe inside the target venv python (no third
# party deps) so the orchestrator and the standalone helper scripts compute the
# same answer without sharing code.

REQS_SATISFY_PROBE = r"""
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
except Exception as exc:
    print(json.dumps({"satisfied": True, "unsatisfied": [], "note": "requirements unreadable: %r" % exc}))
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


def requirements_satisfied(target: str) -> tuple[bool, list]:
    """Return (satisfied, unsatisfied[]) for the target venv vs its requirements.

    Satisfied means every requirement line resolves to an installed
    distribution whose version meets the version constraints. A locally
    installed higher version that is still within range counts as satisfied.
    """
    profile = AUTOMATION_TOOL_PROFILES.get(target) or {}
    venv_dir = profile.get("venv")
    req_path = profile.get("requirements")
    if not venv_dir or not Path(venv_dir).exists():
        return True, []
    if not req_path or not Path(req_path).exists():
        return True, []
    py = automation_venv_python(Path(venv_dir))
    if not py.exists():
        return True, []
    code, stdout, stderr = run_cmd([str(py), "-c", REQS_SATISFY_PROBE, str(req_path)], cwd=workspace_cwd())
    if code != 0 or not stdout.strip():
        # Probe failed for a non-version reason; do not force a rebuild on this
        # ambiguous signal (module-import readiness still guards real breakage).
        return True, []
    try:
        data = json.loads(stdout.strip().splitlines()[-1])
    except Exception:
        return True, []
    return bool(data.get("satisfied", True)), list(data.get("unsatisfied", []))


def venv_requirements_current(target: str) -> bool:
    """True when installed packages still satisfy the requirements constraints.

    A missing venv is handled by the module-import readiness check; a relaxed
    bound or a locally installed higher version that still satisfies the range
    is NOT treated as stale.
    """
    satisfied, _ = requirements_satisfied(target)
    return satisfied


def automation_tools_ready(target: str) -> tuple[bool, str]:
    """Single readiness gate: python exists + modules import + reqs satisfied.

    Used to decide whether an auto-setup is needed. Returns (ready, reason).
    """
    profile = AUTOMATION_TOOL_PROFILES.get(target) or {}
    venv_dir = profile.get("venv")
    modules = list(profile.get("modules", []))
    if not venv_dir:
        return False, f"unknown automation target: {target}"
    py = automation_venv_python(Path(venv_dir))
    if not py.exists():
        return False, f"venv python missing: {py}"
    imported = import_python_modules(py, modules)
    missing = [name for name, ok in imported.items() if not ok]
    if missing:
        return False, "modules fail to import: " + ", ".join(missing)
    satisfied, unsatisfied = requirements_satisfied(target)
    if not satisfied:
        return False, "installed versions no longer satisfy requirements: " + ", ".join(
            item.get("name", "?") for item in unsatisfied
        )
    return True, "ready"


def system_tool_snapshot(target: str) -> dict:
    tools = {}
    for name in AUTOMATION_TOOL_PROFILES[target].get("systemTools", []):
        found = shutil.which(name)
        if name == "adb" and not found:
            candidate = Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / "adb"
            if candidate.exists():
                found = str(candidate)
        tools[name] = found or "missing"
    return tools


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _glob_existing(root: Path, patterns: list[str], limit: int = 20) -> list[str]:
    matches: list[str] = []
    skip_dirs = {
        ".git", ".hg", ".svn", ".automind", "node_modules", ".venv", "venv",
        ".venv-android-tools", ".venv-ios-tools", ".venv-visual-tools",
        "Pods", "build", "dist", "DerivedData", ".gradle", "target",
    }
    for pattern in patterns:
        if pattern.startswith("**/"):
            subpattern = pattern[3:]
            paths = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [name for name in dirnames if name not in skip_dirs and not name.startswith(".tox")]
                current = Path(dirpath)
                names = filenames + dirnames
                for name in names:
                    if fnmatch.fnmatch(name, subpattern):
                        paths.append(current / name)
                        if len(paths) >= limit:
                            break
                if len(paths) >= limit:
                    break
        else:
            paths = sorted(root.glob(pattern))
        for path in paths:
            if path.exists():
                rel = _rel(root, path)
                if rel not in matches:
                    matches.append(rel)
            if len(matches) >= limit:
                return matches
    return matches


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="ignore"))
    except Exception:
        return {}


def _tool_version(binary: str) -> dict:
    found = shutil.which(binary)
    result = {"tool": binary, "path": found or "missing", "version": "missing"}
    if not found:
        return result
    try:
        proc = subprocess.run([found, "--version"], cwd=workspace_cwd(), text=True, capture_output=True, timeout=20)
        version = (proc.stdout or proc.stderr or "").strip().splitlines()
        result["version"] = version[0][:200] if version else "unknown"
    except Exception as exc:
        result["version"] = f"unknown: {exc}"
    return result


def _tool_snapshot(names: list[str]) -> dict:
    return {name: _tool_version(name) for name in names}


def _unique(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _js_package_manager(root: Path, package_json: dict) -> tuple[str, str, list[str]]:
    package_manager_field = str(package_json.get("packageManager") or "")
    lockfiles = _glob_existing(root, ["pnpm-lock.yaml", "yarn.lock", "package-lock.json", "npm-shrinkwrap.json", "bun.lockb", "bun.lock"])
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm", "pnpm-lock.yaml", lockfiles
    if (root / "yarn.lock").exists():
        return "yarn", "yarn.lock", lockfiles
    if (root / "package-lock.json").exists() or (root / "npm-shrinkwrap.json").exists():
        return "npm", "package-lock.json/npm-shrinkwrap.json", lockfiles
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        return "bun", "bun.lock/bun.lockb", lockfiles
    if package_manager_field.startswith("pnpm@"):
        return "pnpm", "packageManager field", lockfiles
    if package_manager_field.startswith("yarn@"):
        return "yarn", "packageManager field", lockfiles
    if package_manager_field.startswith("bun@"):
        return "bun", "packageManager field", lockfiles
    return "npm", "default npm fallback", lockfiles


def _js_install_command(root: Path, package_json: dict, manager: str) -> tuple[str, bool, list[str]]:
    """Return install command, whether it may mutate lockfiles, and notes."""
    notes = []
    has_lock = any((root / name).exists() for name in ["pnpm-lock.yaml", "yarn.lock", "package-lock.json", "npm-shrinkwrap.json", "bun.lockb", "bun.lock"])
    if manager == "pnpm":
        return ("pnpm install --frozen-lockfile" if has_lock else "pnpm install", not has_lock, notes)
    if manager == "yarn":
        field = str(package_json.get("packageManager") or "")
        yarn_berry = bool(re.match(r"yarn@([2-9]|\d{2,})", field))
        if has_lock:
            return ("yarn install --immutable" if yarn_berry else "yarn install --frozen-lockfile", False, notes)
        return "yarn install", True, notes
    if manager == "bun":
        return ("bun install --frozen-lockfile" if has_lock else "bun install", not has_lock, notes)
    if (root / "package-lock.json").exists() or (root / "npm-shrinkwrap.json").exists():
        return "npm ci", False, notes
    notes.append("No JavaScript lockfile found; npm install may create or update lockfiles.")
    return "npm install", True, notes


def _js_categories(package_json: dict, root: Path) -> list[str]:
    deps = {}
    for key in ["dependencies", "devDependencies", "peerDependencies", "optionalDependencies"]:
        raw = package_json.get(key)
        if isinstance(raw, dict):
            deps.update(raw)
    names = set(deps)
    categories: list[str] = []
    if names & {
        "vite", "next", "react", "react-dom", "vue", "@vue/cli-service", "@angular/core",
        "svelte", "@sveltejs/kit", "astro", "nuxt", "playwright", "@playwright/test", "cypress",
    } or _glob_existing(root, ["vite.config.*", "next.config.*", "angular.json", "playwright.config.*", "cypress.config.*"], limit=1):
        categories.append("web")
    if names & {"express", "fastify", "koa", "@nestjs/core", "hapi", "@hapi/hapi", "apollo-server", "graphql-yoga"}:
        categories.append("server")
    if names & {"react-native", "expo", "@capacitor/core", "@ionic/react", "electron", "@tauri-apps/api"}:
        categories.append("client")
    return categories or ["web/server-js"]


def _detect_js(root: Path) -> list[dict]:
    package_path = root / "package.json"
    if not package_path.exists():
        return []
    package_json = _read_json(package_path)
    scripts = package_json.get("scripts") if isinstance(package_json.get("scripts"), dict) else {}
    manager, manager_source, lockfiles = _js_package_manager(root, package_json)
    install_cmd, may_mutate, install_notes = _js_install_command(root, package_json, manager)
    verify_commands = []
    for script in ["lint", "typecheck", "test", "build", "e2e", "test:e2e", "dev", "start"]:
        if script in scripts:
            prefix = manager if manager in {"pnpm", "yarn", "bun"} else "npm run"
            if manager == "npm":
                cmd = "npm test" if script == "test" else f"npm run {script}"
            elif manager == "yarn":
                cmd = f"yarn {script}"
            elif manager == "pnpm":
                cmd = f"pnpm {script}"
            else:
                cmd = f"bun run {script}"
            verify_commands.append(cmd)
    tool_name = manager
    return [{
        "id": "js-package",
        "category": _js_categories(package_json, root),
        "ecosystem": "javascript/typescript",
        "files": _unique(["package.json"] + lockfiles + _glob_existing(root, ["vite.config.*", "next.config.*", "angular.json", "playwright.config.*", "cypress.config.*"], limit=10)),
        "packageManager": {"name": manager, "source": manager_source, "tool": _tool_version(tool_name)},
        "install": {
            "command": install_cmd,
            "lockfileFirst": bool(lockfiles),
            "mayMutateLockfile": may_mutate,
            "autoRunPolicy": "project-native; run only when required by TestCases/Plan and record evidence",
            "notes": install_notes,
        },
        "verifyCommandCandidates": verify_commands,
        "highImpactOrAskUser": [
            "Ask user if private registry/proxy/authentication blocks install.",
            "Ask user before changing package manager, deleting lockfiles, or rewriting lockfiles.",
            "Do not install browser drivers or system browsers silently; use project-native Playwright/Cypress install docs and ask when needed.",
        ],
    }]


def _detect_python(root: Path) -> list[dict]:
    files = _glob_existing(root, ["requirements*.txt", "pyproject.toml", "poetry.lock", "uv.lock", "Pipfile.lock", "Pipfile", "setup.py", "setup.cfg"], limit=30)
    if not files:
        return []
    command = ""
    manager = "python/pip"
    if (root / "uv.lock").exists():
        manager = "uv"
        command = "uv sync --frozen"
    elif (root / "poetry.lock").exists():
        manager = "poetry"
        command = "poetry install --no-interaction --sync"
    elif (root / "Pipfile.lock").exists():
        manager = "pipenv"
        command = "pipenv sync --dev"
    elif (root / "requirements.txt").exists():
        command = "python3 -m venv .venv && . .venv/bin/activate && python -m pip install -r requirements.txt"
    elif (root / "pyproject.toml").exists():
        command = "python3 -m venv .venv && . .venv/bin/activate && python -m pip install -e ."
    else:
        command = "python3 -m venv .venv && . .venv/bin/activate && python -m pip install -r <requirements-file>"
    verify = []
    if (root / "pytest.ini").exists() or (root / "tests").exists():
        verify.append(". .venv/bin/activate && python -m pytest")
    if (root / "manage.py").exists():
        verify.append(". .venv/bin/activate && python manage.py test")
    return [{
        "id": "python-project",
        "category": ["server"] if any(name in files for name in ["requirements.txt", "pyproject.toml", "Pipfile", "Pipfile.lock"]) else ["generic"],
        "ecosystem": "python",
        "files": files,
        "packageManager": {"name": manager, "tool": _tool_version(manager if manager in {"uv", "poetry", "pipenv"} else "python3")},
        "install": {
            "command": command,
            "lockfileFirst": any(name in files for name in ["uv.lock", "poetry.lock", "Pipfile.lock"]),
            "mayMutateLockfile": False,
            "autoRunPolicy": "project-native; prefer existing project venv instructions and record evidence",
            "notes": ["Use the target project's documented venv name if it already has one; do not confuse it with AutoMind helper .venv-* folders."],
        },
        "verifyCommandCandidates": verify,
        "highImpactOrAskUser": [
            "Ask user if private package index, credentials, database service, or network/proxy blocks install.",
            "Do not install system Python, database servers, or OS packages silently.",
        ],
    }]


def _detect_gradle_maven(root: Path) -> list[dict]:
    plans = []
    gradle_files = _glob_existing(root, ["settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts", "gradlew", "**/build.gradle", "**/build.gradle.kts"], limit=30)
    if gradle_files:
        is_android = bool(_glob_existing(root, ["**/AndroidManifest.xml"], limit=1))
        wrapper = (root / "gradlew").exists()
        runner = "./gradlew" if wrapper else "gradle"
        plans.append({
            "id": "gradle-project",
            "category": ["client", "android"] if is_android else ["server", "jvm"],
            "ecosystem": "gradle/android" if is_android else "gradle/jvm",
            "files": gradle_files,
            "packageManager": {"name": "gradle-wrapper" if wrapper else "gradle", "tool": {"tool": runner, "path": str(root / "gradlew") if wrapper else (shutil.which("gradle") or "missing"), "version": "wrapper" if wrapper else _tool_version("gradle").get("version")}},
            "install": {
                "command": f"{runner} test" if not is_android else f"{runner} assembleDebug testDebugUnitTest",
                "lockfileFirst": bool(_glob_existing(root, ["gradle.lockfile", "**/gradle.lockfile"], limit=1)),
                "mayMutateLockfile": False,
                "autoRunPolicy": "project-native Gradle resolution during build/test; record Gradle output as evidence",
                "notes": ["Do not run --refresh-dependencies, change repositories, or install Android SDK components silently."],
            },
            "verifyCommandCandidates": [f"{runner} test"] if not is_android else [f"{runner} assembleDebug", f"{runner} testDebugUnitTest"],
            "highImpactOrAskUser": [
                "Ask before installing Android Studio/SDK/build-tools or changing Gradle repositories.",
                "Ask before online dependency refresh if offline cache is missing and network/private Maven access is required.",
            ],
        })
    maven_files = _glob_existing(root, ["pom.xml", "mvnw"], limit=10)
    if maven_files:
        wrapper = (root / "mvnw").exists()
        runner = "./mvnw" if wrapper else "mvn"
        plans.append({
            "id": "maven-project",
            "category": ["server", "jvm"],
            "ecosystem": "maven",
            "files": maven_files,
            "packageManager": {"name": "maven-wrapper" if wrapper else "maven", "tool": {"tool": runner, "path": str(root / "mvnw") if wrapper else (shutil.which("mvn") or "missing"), "version": "wrapper" if wrapper else _tool_version("mvn").get("version")}},
            "install": {
                "command": f"{runner} test",
                "lockfileFirst": False,
                "mayMutateLockfile": False,
                "autoRunPolicy": "project-native Maven resolution during test/package; record output as evidence",
                "notes": ["Ask before changing repositories, mirrors, JDK, or local Maven settings."],
            },
            "verifyCommandCandidates": [f"{runner} test", f"{runner} package"],
            "highImpactOrAskUser": ["Ask before installing/changing JDK, Maven, repository credentials, or private mirrors."],
        })
    return plans


def _detect_ios(root: Path) -> list[dict]:
    files = _glob_existing(root, ["*.xcodeproj", "*.xcworkspace", "Podfile", "Podfile.lock", "Package.swift", "Package.resolved", "Gemfile", "Gemfile.lock"], limit=30)
    if not files:
        return []
    install_commands = []
    if (root / "Podfile").exists():
        install_commands.append("bundle exec pod install --deployment" if (root / "Gemfile").exists() else "pod install --deployment")
    if (root / "Package.swift").exists() or (root / "Package.resolved").exists():
        install_commands.append("xcodebuild -resolvePackageDependencies -workspace <App.xcworkspace> -scheme <Scheme>")
    return [{
        "id": "ios-project",
        "category": ["client", "ios"],
        "ecosystem": "xcode/ios",
        "files": files,
        "packageManager": {"name": "xcodebuild/cocoapods/spm", "tool": _tool_snapshot(["xcodebuild", "xcrun", "pod", "bundle"])},
        "install": {
            "command": " && ".join(install_commands) if install_commands else "xcodebuild -resolvePackageDependencies -project <App.xcodeproj> -scheme <Scheme>",
            "lockfileFirst": bool((root / "Podfile.lock").exists() or (root / "Package.resolved").exists()),
            "mayMutateLockfile": not bool((root / "Podfile.lock").exists() or (root / "Package.resolved").exists()),
            "autoRunPolicy": "project-native; safe for simulator verification when signing is not required; ask for real-device signing/trust",
            "notes": ["AutoMind setup-automation-tools ios only installs Python helper packages; it does not install Xcode, CocoaPods, signing profiles, or trust devices."],
        },
        "verifyCommandCandidates": ["xcodebuild test -workspace <App.xcworkspace> -scheme <Scheme> -destination 'platform=iOS Simulator,name=<Device>'"],
        "highImpactOrAskUser": [
            "Ask before changing signing team/profiles/keychains or trusting a physical device.",
            "Ask before installing Xcode, CocoaPods, Rosetta, or other system tools.",
        ],
    }]


def _detect_docker(root: Path) -> list[dict]:
    files = _glob_existing(root, ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"], limit=10)
    if not files:
        return []
    return [{
        "id": "docker-services",
        "category": ["server", "infrastructure"],
        "ecosystem": "docker",
        "files": files,
        "packageManager": {"name": "docker", "tool": _tool_snapshot(["docker"])},
        "install": {
            "command": "docker compose build" if any("compose" in f for f in files) else "docker build .",
            "lockfileFirst": False,
            "mayMutateLockfile": False,
            "autoRunPolicy": "read/validate config first; ask before starting long-running services or privileged containers",
            "notes": ["Prefer `docker compose config` as read-only preflight before build/up."],
        },
        "verifyCommandCandidates": ["docker compose config"] if any("compose" in f for f in files) else ["docker build ."],
        "highImpactOrAskUser": [
            "Ask before installing Docker Desktop, starting databases, exposing ports, or running privileged containers.",
            "Classify Docker daemon unavailable as environment_blocked, not product failure.",
        ],
    }]


def _detect_other_server(root: Path) -> list[dict]:
    plans = []
    if (root / "go.mod").exists():
        plans.append({
            "id": "go-project",
            "category": ["server"],
            "ecosystem": "go",
            "files": _glob_existing(root, ["go.mod", "go.sum"], limit=5),
            "packageManager": {"name": "go", "tool": _tool_version("go")},
            "install": {"command": "go mod download", "lockfileFirst": (root / "go.sum").exists(), "mayMutateLockfile": False, "autoRunPolicy": "project-native; record output", "notes": []},
            "verifyCommandCandidates": ["go test ./..."],
            "highImpactOrAskUser": ["Ask before changing GOPROXY, credentials, or installing system Go."],
        })
    if (root / "Cargo.toml").exists():
        plans.append({
            "id": "rust-project",
            "category": ["server", "native"],
            "ecosystem": "rust",
            "files": _glob_existing(root, ["Cargo.toml", "Cargo.lock"], limit=5),
            "packageManager": {"name": "cargo", "tool": _tool_version("cargo")},
            "install": {"command": "cargo fetch --locked" if (root / "Cargo.lock").exists() else "cargo fetch", "lockfileFirst": (root / "Cargo.lock").exists(), "mayMutateLockfile": not (root / "Cargo.lock").exists(), "autoRunPolicy": "project-native; record output", "notes": []},
            "verifyCommandCandidates": ["cargo test"],
            "highImpactOrAskUser": ["Ask before installing Rust toolchains or changing registry configuration."],
        })
    return plans


def build_project_dependency_report(root: Optional[Path] = None, task_code: str = "", iteration: Optional[int] = None) -> dict:
    """Build a read-only dependency report for target project stacks.

    This is intentionally not an installer. It detects project-native dependency
    commands for web/client/server projects and records what may be run by the
    Planner/Evaluator after TestCases/Plan choose the validation path.
    """
    root = (root or AUTOMIND_WORKSPACE_ROOT).expanduser().resolve()
    detectors = []
    for detect in [_detect_js, _detect_python, _detect_gradle_maven, _detect_ios, _detect_docker, _detect_other_server]:
        detectors.extend(detect(root))

    categories = sorted({category for item in detectors for category in item.get("category", [])})
    missing_tools = []
    for item in detectors:
        pm = item.get("packageManager", {})
        tool = pm.get("tool")
        tools = tool.values() if isinstance(tool, dict) and all(isinstance(v, dict) for v in tool.values()) else [tool]
        for one in tools:
            if isinstance(one, dict) and one.get("path") == "missing":
                missing_tools.append({"detector": item.get("id"), "tool": one.get("tool")})

    report = {
        "result": "pass",
        "readOnly": True,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "taskCode": task_code or "",
        "iteration": iteration,
        "policy": {
            "autoInstallsOnlyAutoMindHelperVenvs": ["android", "ios", "visual"],
            "doesNotAutoInstallTargetProjectDependencies": True,
            "targetProjectDepsUseProjectNativeCommands": True,
            "lockfileFirst": True,
            "systemOrHighImpactChangesRequireAskUser": True,
            "recordCommandsAndEvidenceInValidation": True,
        },
        "coverage": {
            "web": "detected by package.json/web config and uses npm/pnpm/yarn/bun lockfile commands",
            "client": "detected by Android/iOS/mobile/desktop markers and uses Gradle/Xcode/project-native runners",
            "server": "detected by Python/Node/JVM/Go/Rust/Docker markers and uses project-native package managers",
        },
        "detectedCategories": categories,
        "dependencyPlans": detectors,
        "missingTools": missing_tools,
        "classificationHint": "Missing package managers, SDKs, Docker daemons, signing, devices, or private registry credentials are environment/tool blockers, not product-code failures.",
        "nextActions": [
            "Use this report while refining TestCases.md preconditions and Plan.md verification commands.",
            "Run project-native install/build/test commands only when required by the selected testcase runbook.",
            "If a command requires private registry auth, system SDK install, Docker/service startup, signing, or device trust, route to ask_user with options.",
        ],
    }
    if not detectors:
        report["result"] = "unknown"
        report["nextActions"].insert(0, "No common web/client/server dependency markers were detected; use repository docs or ask_user before inventing install commands.")
    return report


def cmd_project_dependency_check(task_code: str = "", iteration: Optional[int] = None, root: Optional[Path] = None):
    root = (root or AUTOMIND_WORKSPACE_ROOT).expanduser().resolve()
    if task_code and iteration is None:
        state = read_runtime_state(root / ".automind" / "tasks" / task_code) or {}
        raw_iteration = state.get("iteration")
        try:
            iteration = int(raw_iteration)
        except Exception:
            iteration = 1
    report = build_project_dependency_report(root=root, task_code=task_code, iteration=iteration)
    if task_code:
        log_dir = root / ".automind" / "tasks" / task_code / "logs" / f"iter-{iteration or 1}"
    else:
        log_dir = root / ".automind" / "setup" / "dependency-check" / datetime.now().strftime("%Y%m%d-%H%M%S")
    ensure_dir(log_dir)
    report_path = log_dir / "dependency-check.json"
    report["reportPath"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_setup_automation_tools(target: str = "all", dry_run: bool = False):
    """Install project-local Python helper kits for Android/iOS verification.

    The public installer does not run it. Platform preflight/evaluator commands
    may call it automatically for required low-risk Python helper packages, and
    users may run it explicitly to pre-create the local venvs. High-impact
    system/device/signing/privileged actions remain outside this command.
    """
    target = (target or "all").lower()
    if target not in {"android", "ios", "visual", "all"}:
        error("Unknown automation tool target: " + target)
        print("Usage: python orchestrator.py setup-automation-tools [android|ios|visual|all] [--dry-run]")
        sys.exit(1)

    targets = ["android", "ios", "visual"] if target == "all" else [target]
    setup_root = AUTOMATION_SETUP_DIR
    ensure_dir(setup_root)
    report = {
        "result": "pass",
        "dryRun": dry_run,
        "root": str(AUTOMIND_WORKSPACE_ROOT),
        "targets": [],
        "policy": {
            "scope": "project-local Python virtual environments only",
            "notDuringInstall": True,
            "mayAutoRunForRequiredPythonHelpers": True,
            "doesNotInstallSystemSdksOrManipulateDevices": True,
        },
    }

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for item in targets:
        plan = automation_setup_command_plan(item)
        venv_dir = Path(plan["venv"])
        py = Path(plan["python"])
        target_log_dir = setup_root / item / timestamp
        target_report = {
            **plan,
            "systemTools": system_tool_snapshot(item),
            "steps": [],
            "modules": check_python_modules(py, list(AUTOMATION_TOOL_PROFILES[item]["modules"])),
            "status": "planned" if dry_run else "pending",
        }

        if not dry_run:
            if not py.exists():
                target_report["steps"].append(run_setup_step([sys.executable, "-m", "venv", str(venv_dir)], target_log_dir, "create-venv"))
            else:
                target_report["steps"].append({
                    "name": "create-venv",
                    "cmd": [sys.executable, "-m", "venv", str(venv_dir)],
                    "exitCode": 0,
                    "skipped": True,
                    "reason": "venv already exists",
                })
            if target_report["steps"][-1].get("exitCode") == 0:
                target_report["steps"].append(run_setup_step_with_bounded_network_retry([str(py), "-m", "pip", "install", "-U", "pip"], target_log_dir, "upgrade-pip"))
            if target_report["steps"][-1].get("exitCode") == 0:
                target_report["steps"].append(run_setup_step_with_bounded_network_retry(plan["commands"][2], target_log_dir, "install-packages"))

            target_report["modules"] = import_python_modules(py, list(AUTOMATION_TOOL_PROFILES[item]["modules"]))
            failed_steps = [step for step in target_report["steps"] if step.get("exitCode") != 0]
            missing_modules = [name for name, ok in target_report["modules"].items() if not ok]
            if failed_steps or missing_modules:
                target_report["status"] = "fail"
                target_report["failedSteps"] = failed_steps
                target_report["missingModules"] = missing_modules
                target_report["diagnostics"] = setup_failure_diagnostics(failed_steps)
                if item == "android":
                    ready_fallbacks = [str(candidate) for candidate in _runtime_android_tools_candidates() if candidate.exists() and android_tools_python_ready(str(candidate))]
                    target_report["readyFallbacks"] = ready_fallbacks
                    if ready_fallbacks:
                        target_report["fallbackAdvice"] = "A ready AutoMind runtime Android helper venv exists and can be reused by android-preflight/probe-flow; project-local setup can be repaired later."
                report["result"] = "fail"
            else:
                target_report["status"] = "ready"
                # Record the version satisfaction snapshot so a later check can
                # tell whether the installed packages still meet requirements
                # (P1: relaxed bounds / higher local versions are NOT stale).
                satisfied, unsatisfied = requirements_satisfied(item)
                target_report["requirementsSatisfied"] = satisfied
                target_report["requirementsUnsatisfied"] = unsatisfied

        ensure_dir(target_log_dir)
        (target_log_dir / "setup-report.json").write_text(json.dumps(target_report, ensure_ascii=False, indent=2) + "\n")
        target_report["reportPath"] = str(target_log_dir / "setup-report.json")
        report["targets"].append(target_report)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["result"] != "pass":
        sys.exit(1)
