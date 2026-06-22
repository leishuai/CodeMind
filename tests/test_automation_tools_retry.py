from pathlib import Path

from orchestrator import automation_tools


def test_setup_step_network_failure_retries_once(monkeypatch, tmp_path):
    calls = []

    def fake_run_setup_step(cmd, log_dir, name, timeout=900):
        calls.append(name)
        log = Path(log_dir) / f"{name}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        if name == "install-packages":
            log.write_text("Could not resolve host: pypi.org")
            return {"name": name, "cmd": cmd, "exitCode": 1, "log": str(log)}
        log.write_text("ok")
        return {"name": name, "cmd": cmd, "exitCode": 0, "log": str(log)}

    monkeypatch.setattr(automation_tools, "run_setup_step", fake_run_setup_step)
    monkeypatch.setattr(automation_tools.time, "sleep", lambda seconds: None)

    result = automation_tools.run_setup_step_with_bounded_network_retry(["python", "-m", "pip"], tmp_path, "install-packages")

    assert calls == ["install-packages", "install-packages-retry1"]
    assert result["exitCode"] == 0
    assert result["retryOf"] == "install-packages"
    assert result["retryReason"] == "network_or_dns"
    assert result["previousAttempt"]["exitCode"] == 1


def test_setup_step_non_network_failure_does_not_retry(monkeypatch, tmp_path):
    calls = []

    def fake_run_setup_step(cmd, log_dir, name, timeout=900):
        calls.append(name)
        log = Path(log_dir) / f"{name}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("Permission denied")
        return {"name": name, "cmd": cmd, "exitCode": 1, "log": str(log)}

    monkeypatch.setattr(automation_tools, "run_setup_step", fake_run_setup_step)

    result = automation_tools.run_setup_step_with_bounded_network_retry(["python", "-m", "pip"], tmp_path, "install-packages")

    assert calls == ["install-packages"]
    assert result["exitCode"] == 1
    assert "retryOf" not in result
