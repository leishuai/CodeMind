import json
import sys
from pathlib import Path


def test_android_preflight_pass_is_partial_not_finish(monkeypatch, tmp_path: Path) -> None:
    from scripts import android_preflight

    task_code = "android_preflight_semantics"
    task_dir = tmp_path / ".automind" / "tasks" / task_code

    monkeypatch.setattr(android_preflight, "TASKS_DIR", tmp_path / ".automind" / "tasks")
    monkeypatch.setattr(android_preflight, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(android_preflight, "android_python", lambda: sys.executable)
    monkeypatch.setattr(android_preflight.shutil, "which", lambda name: "/bin/sh" if name == "adb" else None)

    def fake_run(cmd, timeout=30):
        text = " ".join(str(x) for x in cmd)
        if "devices" in text:
            return 0, "List of devices attached\nSERIAL123 device product:test\n"
        if "__import__" in text:
            return 0, '{"adbutils": true, "uiautomator2": true}\n'
        if "get-state" in text:
            return 0, "device\n"
        if "ro.product.model" in text:
            return 0, "Pixel\n"
        if "ro.product.brand" in text:
            return 0, "google\n"
        if "ro.build.version.sdk" in text:
            return 0, "35\n"
        if "ro.build.version.release" in text:
            return 0, "15\n"
        if "dumpsys power" in text:
            return 0, "Display Power: state=ON\n"
        if "dumpsys window policy" in text:
            return 0, "mScreenOnFully=true\n"
        if "dumpsys window windows" in text:
            return 0, "mCurrentFocus=Window{u0 com.example.app/com.Main}\n"
        if "dumpsys activity activities" in text:
            return 0, "ACTIVITY com.example.app/com.Main\n"
        if "settings get secure" in text:
            return 0, "1\n"
        return 0, "ok\n"

    monkeypatch.setattr(android_preflight, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["android_preflight.py", task_code, "7"])

    rc = android_preflight.main()

    assert rc == 0
    evaluation = json.loads((task_dir / "evaluation.json").read_text())
    state = json.loads((task_dir / "runtime-state.json").read_text())
    assert evaluation["preflightResult"] == "pass"
    assert evaluation["result"] == "partial"
    assert evaluation["nextAction"] == "retry_generator"
    assert evaluation["preflightOnly"] is True
    assert evaluation["proofRequired"] is True
    assert evaluation["failedChecks"][0]["category"] == "validation_incomplete"
    assert state["status"] == "retry_pending"
    assert state["nextAction"] == "retry_generator"


def test_android_preflight_statusbar_listed_but_not_focused_does_not_ask_user(monkeypatch, tmp_path: Path) -> None:
    from scripts import android_preflight

    task_code = "android_preflight_statusbar_diagnostic"
    task_dir = tmp_path / ".automind" / "tasks" / task_code

    monkeypatch.setattr(android_preflight, "TASKS_DIR", tmp_path / ".automind" / "tasks")
    monkeypatch.setattr(android_preflight, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(android_preflight, "android_python", lambda: sys.executable)
    monkeypatch.setattr(android_preflight.shutil, "which", lambda name: "/bin/sh" if name == "adb" else None)

    def fake_run(cmd, timeout=30):
        text = " ".join(str(x) for x in cmd)
        if "devices" in text:
            return 0, "List of devices attached\nSERIAL123 device product:test\n"
        if "__import__" in text:
            return 0, '{"adbutils": true, "uiautomator2": true}\n'
        if "get-state" in text:
            return 0, "device\n"
        if "ro.product.model" in text:
            return 0, "Pixel\n"
        if "ro.product.brand" in text:
            return 0, "google\n"
        if "ro.build.version.sdk" in text:
            return 0, "35\n"
        if "ro.build.version.release" in text:
            return 0, "15\n"
        if "dumpsys power" in text:
            return 0, "mWakefulness=Awake\nDisplay Power: state=ON\n"
        if "dumpsys window policy" in text:
            return 0, "mScreenOnFully=true\n"
        if "dumpsys window windows" in text:
            return 0, "Window #6 Window{u0 StatusBar}\nmCurrentFocus=Window{u0 com.huawei.android.launcher/.Launcher}\n"
        if "dumpsys activity activities" in text:
            return 0, "ACTIVITY com.huawei.android.launcher/.Launcher\n"
        if "settings get secure" in text:
            return 0, "1\n"
        return 0, "ok\n"

    monkeypatch.setattr(android_preflight, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["android_preflight.py", task_code, "8"])

    rc = android_preflight.main()

    assert rc == 0
    evaluation = json.loads((task_dir / "evaluation.json").read_text())
    assert evaluation["preflightResult"] == "pass"
    assert evaluation["nextAction"] == "retry_generator"
    assert "askUserQuestion" not in evaluation
    assert any(w["category"] == "diagnostic_only" for w in evaluation["warnings"])


def test_android_preflight_keyguard_focus_reports_deterministic_ask_user(monkeypatch, tmp_path: Path) -> None:
    from scripts import android_preflight

    task_code = "android_preflight_keyguard"
    task_dir = tmp_path / ".automind" / "tasks" / task_code

    monkeypatch.setattr(android_preflight, "TASKS_DIR", tmp_path / ".automind" / "tasks")
    monkeypatch.setattr(android_preflight, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(android_preflight, "android_python", lambda: sys.executable)
    monkeypatch.setattr(android_preflight.shutil, "which", lambda name: "/bin/sh" if name == "adb" else None)

    def fake_run(cmd, timeout=30):
        text = " ".join(str(x) for x in cmd)
        if "devices" in text:
            return 0, "List of devices attached\nSERIAL123 device product:test\n"
        if "__import__" in text:
            return 0, '{"adbutils": true, "uiautomator2": true}\n'
        if "get-state" in text:
            return 0, "device\n"
        if "ro.product.model" in text:
            return 0, "Pixel\n"
        if "ro.product.brand" in text:
            return 0, "google\n"
        if "ro.build.version.sdk" in text:
            return 0, "35\n"
        if "ro.build.version.release" in text:
            return 0, "15\n"
        if "dumpsys power" in text:
            return 0, "mWakefulness=Awake\nDisplay Power: state=ON\n"
        if "dumpsys window policy" in text:
            return 0, "mScreenOnFully=true\n"
        if "dumpsys window windows" in text:
            return 0, "mCurrentFocus=Window{u0 Keyguard}\n"
        if "dumpsys activity activities" in text:
            return 0, "ACTIVITY com.android.systemui/.keyguard.Keyguard\n"
        if "settings get secure" in text:
            return 0, "1\n"
        return 0, "ok\n"

    monkeypatch.setattr(android_preflight, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["android_preflight.py", task_code, "9"])

    rc = android_preflight.main()

    assert rc == 1
    evaluation = json.loads((task_dir / "evaluation.json").read_text())
    assert evaluation["preflightResult"] == "blocked"
    assert evaluation["nextAction"] == "ask_user"
    assert "CodeAutonomy detected" in evaluation["askUserQuestion"]["question"]
    assert "Is it" not in evaluation["askUserQuestion"]["question"]
    assert evaluation["failedChecks"][0]["name"] == "lockscreen focus"
