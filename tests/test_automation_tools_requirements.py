"""Tests for P1 (requirements satisfaction) and P2 (real-import readiness).

P1 no longer fingerprints the requirements file: a venv is only "stale" when an
installed package no longer satisfies the version constraints. A relaxed bound
or a locally installed higher version that still satisfies the range must NOT
trigger a reinstall.
"""
import sys
from pathlib import Path

from orchestrator import automation_tools


def _make_profile(tmp_path, body="adbutils>=2\n"):
    venv = tmp_path / ".venv-fake-tools"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    req = tmp_path / "fake-tools.txt"
    req.write_text(body)
    return {
        "venv": venv,
        "requirements": req,
        "packages": ["adbutils"],
        "modules": ["adbutils"],
        "systemTools": [],
        "notes": [],
    }


def test_requirements_satisfied_when_installed_version_in_range(monkeypatch, tmp_path):
    profile = _make_profile(tmp_path, "pkg>=1.0,<2\n")
    monkeypatch.setitem(automation_tools.AUTOMATION_TOOL_PROFILES, "fake", profile)
    monkeypatch.setattr(automation_tools, "automation_venv_python", lambda venv_dir: Path(sys.executable))
    # Pretend the probe reports an installed 1.5 that satisfies >=1.0,<2.
    monkeypatch.setattr(
        automation_tools, "run_cmd",
        lambda *a, **k: (0, '{"satisfied": true, "unsatisfied": []}', ""),
    )
    satisfied, unsatisfied = automation_tools.requirements_satisfied("fake")
    assert satisfied is True
    assert unsatisfied == []
    assert automation_tools.venv_requirements_current("fake") is True


def test_higher_local_version_in_range_is_not_stale(monkeypatch, tmp_path):
    profile = _make_profile(tmp_path, "pkg>=1.0,<3\n")
    monkeypatch.setitem(automation_tools.AUTOMATION_TOOL_PROFILES, "fake", profile)
    monkeypatch.setattr(automation_tools, "automation_venv_python", lambda venv_dir: Path(sys.executable))
    # A higher local version (e.g. 2.9) that still satisfies the range.
    monkeypatch.setattr(
        automation_tools, "run_cmd",
        lambda *a, **k: (0, '{"satisfied": true, "unsatisfied": []}', ""),
    )
    assert automation_tools.venv_requirements_current("fake") is True


def test_relaxed_requirements_edit_does_not_trigger_reinstall(monkeypatch, tmp_path):
    # Editing the file content alone (relaxing a bound) must not flip stale as
    # long as the installed version still satisfies the new constraint.
    profile = _make_profile(tmp_path, "pkg>=2\n")
    monkeypatch.setitem(automation_tools.AUTOMATION_TOOL_PROFILES, "fake", profile)
    monkeypatch.setattr(automation_tools, "automation_venv_python", lambda venv_dir: Path(sys.executable))
    monkeypatch.setattr(
        automation_tools, "run_cmd",
        lambda *a, **k: (0, '{"satisfied": true, "unsatisfied": []}', ""),
    )
    profile["requirements"].write_text("pkg>=1\n")  # relaxed lower bound
    assert automation_tools.venv_requirements_current("fake") is True


def test_version_below_constraint_is_stale(monkeypatch, tmp_path):
    profile = _make_profile(tmp_path, "pkg>=2\n")
    monkeypatch.setitem(automation_tools.AUTOMATION_TOOL_PROFILES, "fake", profile)
    monkeypatch.setattr(automation_tools, "automation_venv_python", lambda venv_dir: Path(sys.executable))
    monkeypatch.setattr(
        automation_tools, "run_cmd",
        lambda *a, **k: (0, '{"satisfied": false, "unsatisfied": [{"name": "pkg", "installed": "1.0", "spec": ">=2", "reason": "version_mismatch"}]}', ""),
    )
    satisfied, unsatisfied = automation_tools.requirements_satisfied("fake")
    assert satisfied is False
    assert unsatisfied[0]["name"] == "pkg"
    assert automation_tools.venv_requirements_current("fake") is False


def test_missing_venv_is_not_stale(monkeypatch, tmp_path):
    profile = _make_profile(tmp_path)
    # Point the profile at a non-existent venv dir.
    profile["venv"] = tmp_path / "nope-venv"
    monkeypatch.setitem(automation_tools.AUTOMATION_TOOL_PROFILES, "fake", profile)
    # Missing venv => handled by import readiness, not flagged stale here.
    assert automation_tools.venv_requirements_current("fake") is True


def test_no_requirements_file_is_not_stale(monkeypatch, tmp_path):
    profile = _make_profile(tmp_path)
    profile["requirements"].unlink()
    monkeypatch.setitem(automation_tools.AUTOMATION_TOOL_PROFILES, "fake", profile)
    assert automation_tools.venv_requirements_current("fake") is True


def test_parse_version_truncates_at_first_letter(tmp_path):
    # Pre/dev/post-release letters and everything after must be dropped so the
    # comparison uses the numeric release only. Run the real probe so the
    # embedded parse_version is exercised end to end.
    import subprocess, json
    req = tmp_path / "reqs.txt"
    # pip's installed version certainly satisfies >=2.0.dev0 once the dev suffix
    # is truncated to 2.0; a bogus letter suffix must not break parsing either.
    req.write_text("pip>=2.0.dev0\n")
    proc = subprocess.run(
        [sys.executable, "-c", automation_tools.REQS_SATISFY_PROBE, str(req)],
        text=True, capture_output=True, timeout=30,
    )
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["satisfied"] is True
    # 1.0rc1 truncates to 1.0, so a >999999 lower bound is still unsatisfied.
    req.write_text("pip>=999999.0rc1\n")
    proc = subprocess.run(
        [sys.executable, "-c", automation_tools.REQS_SATISFY_PROBE, str(req)],
        text=True, capture_output=True, timeout=30,
    )
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["satisfied"] is False


def test_probe_real_version_logic_end_to_end(tmp_path):
    # Exercise the real probe inside the current interpreter against a fake
    # requirements file using a package that is certainly installed (pip).
    req = tmp_path / "reqs.txt"
    req.write_text("pip>=1\n# comment\n")
    import subprocess
    proc = subprocess.run(
        [sys.executable, "-c", automation_tools.REQS_SATISFY_PROBE, str(req)],
        text=True, capture_output=True, timeout=30,
    )
    assert proc.returncode == 0
    import json
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["satisfied"] is True

    req.write_text("pip>=999999\n")  # impossible high lower bound
    proc = subprocess.run(
        [sys.executable, "-c", automation_tools.REQS_SATISFY_PROBE, str(req)],
        text=True, capture_output=True, timeout=30,
    )
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["satisfied"] is False


def test_import_python_modules_real_import(tmp_path):
    py = Path(sys.executable)
    result = automation_tools.import_python_modules(py, ["json", "this_module_does_not_exist_xyz"])
    assert result["json"] is True
    assert result["this_module_does_not_exist_xyz"] is False


def test_automation_tools_ready_gate(monkeypatch, tmp_path):
    profile = _make_profile(tmp_path)
    real_py = Path(sys.executable)
    monkeypatch.setattr(automation_tools, "automation_venv_python", lambda venv_dir: real_py)
    profile["modules"] = ["json"]
    monkeypatch.setitem(automation_tools.AUTOMATION_TOOL_PROFILES, "fake", profile)
    # Modules always import in this gate test; vary only the satisfaction probe.
    monkeypatch.setattr(automation_tools, "import_python_modules", lambda py, mods: {m: True for m in mods})

    # Modules import but versions unsatisfied => not ready.
    monkeypatch.setattr(
        automation_tools, "requirements_satisfied",
        lambda target: (False, [{"name": "pkg"}]),
    )
    ready, reason = automation_tools.automation_tools_ready("fake")
    assert ready is False
    assert "satisfy" in reason

    # Everything satisfied => ready.
    monkeypatch.setattr(
        automation_tools, "requirements_satisfied",
        lambda target: (True, []),
    )
    ready, reason = automation_tools.automation_tools_ready("fake")
    assert ready is True
    assert reason == "ready"


def test_script_mirror_real_probe(tmp_path):
    """scripts/automind_paths re-derives the same satisfaction check standalone."""
    sys.path.insert(0, str(Path("scripts").resolve()))
    import automind_paths

    venv = tmp_path / ".venv-fake-tools"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    # Use the current interpreter as the venv python so the probe can run.
    (venv / "bin" / "python").symlink_to(sys.executable)
    req = tmp_path / "fake-tools.txt"
    req.write_text("pip>=1\n")
    automind_paths._AUTOMATION_VENV_DIRS["fake"] = venv
    automind_paths._AUTOMATION_REQ_FILES["fake"] = req
    try:
        assert automind_paths.venv_requirements_current("fake") is True
        req.write_text("pip>=999999\n")
        assert automind_paths.venv_requirements_current("fake") is False
    finally:
        automind_paths._AUTOMATION_VENV_DIRS.pop("fake", None)
        automind_paths._AUTOMATION_REQ_FILES.pop("fake", None)
