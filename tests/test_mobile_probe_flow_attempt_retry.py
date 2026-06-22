from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_main():
    path = Path("orchestrator/main.py")
    spec = importlib.util.spec_from_file_location("automind_main", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_ios_runner():
    path = Path("scripts/ios_probe_flow_runner.py")
    scripts_path = str(Path("scripts").resolve())
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location("ios_probe_flow_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_android_probe_flow_retries_runner_attempts(tmp_path: Path, monkeypatch) -> None:
    main = _load_main()
    task_dir = tmp_path / "android_retry_task"
    task_dir.mkdir()
    (task_dir / "probe-flow.android.json").write_text(json.dumps({
        "app": {"apk": "demo.apk", "package": "com.example", "activity": ".Main"},
        "steps": [],
    }))
    calls = {"count": 0}

    def fake_run_cmd(cmd, cwd=None, capture=True):
        calls["count"] += 1
        out_dir = Path(cmd[cmd.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        result = "fail" if calls["count"] == 1 else "pass"
        (out_dir / "probe-flow-summary.json").write_text(json.dumps({
            "result": result,
            "checks": [{"name": "smoke", "ok": result == "pass", "detail": result}],
            "stepResults": [],
        }))
        return (0, f"attempt-{calls['count']}", "")

    monkeypatch.setattr(main, "run_cmd", fake_run_cmd)

    def fake_gate(task_dir_arg, iteration, evaluation):
        report = task_dir_arg / "logs" / f"iter-{iteration}" / "ui-evidence-check.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({"result": "pass", "failedCount": 0, "warningCount": 0}))
        evaluation.setdefault("evidence", []).append({"type": "other", "note": "ui-evidence-check", "path": f"logs/iter-{iteration}/ui-evidence-check.json"})
        return evaluation

    monkeypatch.setattr(main, "run_ui_evidence_gate", fake_gate)
    code, _output = main.run_android_probe_flow_evaluator(task_dir, 1, task_dir / "logs" / "iter-1", dry_run=True, retries=1)

    assert code == 0
    assert calls["count"] == 2
    summary = json.loads((task_dir / "logs" / "iter-1" / "probe-flow" / "probe-flow-summary.json").read_text())
    assert summary["retries"] == 1
    assert [item["summaryResult"] for item in summary["attempts"]] == ["fail", "pass"]
    evaluation = json.loads((task_dir / "evaluation.json").read_text())
    # Completion gate may still block finish in this isolated unit fixture, but
    # the attempt retry evidence must survive into evaluation.
    assert any(item.get("note") == "android-probe-flow-attempt-1" for item in evaluation["evidence"])
    assert (task_dir / "logs" / "iter-1" / "android-probe-flow-attempt-1.log").exists()
    assert (task_dir / "logs" / "iter-1" / "ui-evidence-check.json").exists()


def test_ios_probe_flow_retries_xcuitest_attempts(tmp_path: Path, monkeypatch) -> None:
    runner = _load_ios_runner()
    workspace = tmp_path / "workspace"
    task_dir = workspace / ".automind" / "tasks" / "ios_retry_task"
    task_dir.mkdir(parents=True)
    (task_dir / "probe-flow.ios.json").write_text(json.dumps({
        "adapter": "xcuitest",
        "app": {"projectPath": "/tmp/Demo.xcodeproj", "scheme": "Demo", "deviceId": "FAKE", "bundleId": "com.example"},
        "testIntent": {"goal": "retry", "sources": ["TC-F01"], "steps": [{"type": "tap", "name": "Tap", "selector": {"accessibilityIdentifier": "go"}, "critical": True}]},
    }))
    monkeypatch.setattr(runner, "TASKS_DIR", workspace / ".automind" / "tasks")
    monkeypatch.setattr(runner, "WORKSPACE_ROOT", workspace)
    calls = {"count": 0}

    def fake_run(cmd, cwd=None, text=True, capture_output=True, timeout=None):
        calls["count"] += 1
        evaluation = {
            "iteration": 1,
            "result": "fail" if calls["count"] == 1 else "pass",
            "summary": "failed once" if calls["count"] == 1 else "passed",
            "failedChecks": [] if calls["count"] == 2 else [{"name": "xcuitest", "category": "test_failure", "reason": "transient"}],
            "evidence": [],
            "nextAction": "retry_generator" if calls["count"] == 1 else "finish",
        }
        (task_dir / "evaluation.json").write_text(json.dumps(evaluation))
        return SimpleNamespace(returncode=1 if calls["count"] == 1 else 0, stdout=f"out-{calls['count']}", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "run_preflight", lambda task_code, iteration, core_device_id=None: (True, {"result": "pass", "nextAction": "finish"}, "preflight-ok"))

    def fake_gate(task_dir_arg, iteration, evaluation):
        report = task_dir_arg / "logs" / f"iter-{iteration}" / "ui-evidence-check.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({"result": "pass", "failedCount": 0, "warningCount": 0}))
        evaluation.setdefault("evidence", []).append({"type": "other", "note": "ui-evidence-check", "path": f"logs/iter-{iteration}/ui-evidence-check.json"})
        return evaluation

    monkeypatch.setattr(runner, "run_ui_evidence_gate", fake_gate)
    monkeypatch.setattr(sys, "argv", ["ios_probe_flow_runner.py", "ios_retry_task", "1", "--retries", "1"])

    assert runner.main() == 0
    assert calls["count"] == 2
    iter_dir = task_dir / "logs" / "iter-1"
    attempts = json.loads((iter_dir / "ios-probe-flow-attempts.json").read_text())
    assert [item["exitCode"] for item in attempts["attempts"]] == [1, 0]
    evaluation = json.loads((task_dir / "evaluation.json").read_text())
    assert evaluation["result"] == "pass"
    assert any(item.get("note") == "ios-probe-flow-attempt-2" for item in evaluation["evidence"])
    assert (iter_dir / "ui-evidence-check.json").exists()


def test_ios_probe_flow_applies_signing_preflight_recommendation(tmp_path: Path, monkeypatch) -> None:
    runner = _load_ios_runner()
    workspace = tmp_path / "workspace"
    task_dir = workspace / ".automind" / "tasks" / "ios_signing_recommendation_task"
    iter_dir = task_dir / "logs" / "iter-1"
    iter_dir.mkdir(parents=True)
    (task_dir / "probe-flow.ios.json").write_text(json.dumps({
        "adapter": "xcuitest",
        "app": {
            "projectPath": "/tmp/Demo.xcodeproj",
            "scheme": "Demo",
            "deviceId": "FAKE",
            "team": "6C3A7NGGUC",
            "bundleId": "com.example",
        },
        "testIntent": {
            "goal": "signing",
            "sources": ["TC-F01"],
            "steps": [{"type": "tap", "name": "Tap", "selector": {"accessibilityIdentifier": "go"}, "critical": True}],
        },
    }))
    (iter_dir / "ios-signing-preflight-discover.log").write_text(json.dumps({
        "result": "pass",
        "recommendation": {
            "recommendedTeam": "53XNMZ925H",
            "recommendedCodeSignStyle": "Automatic",
            "automaticSigningViable": True,
            "canRetryWithExistingMaterial": True,
            "rebuildHint": "xcodebuild ... DEVELOPMENT_TEAM=53XNMZ925H CODE_SIGN_STYLE=Automatic",
        },
    }) + "\nWrote: logs/iter-1/ios-signing-preflight.json\n")
    monkeypatch.setattr(runner, "TASKS_DIR", workspace / ".automind" / "tasks")
    monkeypatch.setattr(runner, "WORKSPACE_ROOT", workspace)
    captured = {"cmd": []}

    def fake_run(cmd, cwd=None, text=True, capture_output=True, timeout=None):
        captured["cmd"] = cmd
        (task_dir / "evaluation.json").write_text(json.dumps({
            "iteration": 1,
            "result": "pass",
            "summary": "passed",
            "failedChecks": [],
            "evidence": [],
            "nextAction": "finish",
        }))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "run_preflight", lambda task_code, iteration, core_device_id=None: (True, {"result": "pass", "nextAction": "finish"}, "preflight-ok"))
    monkeypatch.setattr(runner, "run_ui_evidence_gate", lambda task_dir_arg, iteration, evaluation: evaluation)
    monkeypatch.setattr(sys, "argv", ["ios_probe_flow_runner.py", "ios_signing_recommendation_task", "1"])

    assert runner.main() == 0
    assert "--team" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--team") + 1] == "53XNMZ925H"
    command = json.loads((iter_dir / "ios-probe-flow-command.json").read_text())
    assert command["signingRecommendation"]["applied"] is True
    assert command["signingRecommendation"]["previousTeam"] == "6C3A7NGGUC"
    evaluation = json.loads((task_dir / "evaluation.json").read_text())
    assert evaluation["signingRecommendation"]["team"] == "53XNMZ925H"


def test_ios_probe_flow_uses_newest_signing_preflight_across_json_and_log(tmp_path: Path, monkeypatch) -> None:
    runner = _load_ios_runner()
    workspace = tmp_path / "workspace"
    task_dir = workspace / ".automind" / "tasks" / "ios_signing_newest_task"
    old_dir = task_dir / "logs" / "iter-1"
    new_dir = task_dir / "logs" / "iter-2"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    old_json = old_dir / "ios-signing-preflight.json"
    new_log = new_dir / "ios-signing-preflight-discover.log"
    old_json.write_text(json.dumps({
        "recommendation": {
            "recommendedTeam": "OLDTEAM123",
            "automaticSigningViable": True,
            "canRetryWithExistingMaterial": True,
        }
    }))
    new_log.write_text(json.dumps({
        "recommendation": {
            "recommendedTeam": "NEWTEAM456",
            "automaticSigningViable": True,
            "canRetryWithExistingMaterial": True,
        }
    }) + "\nWrote: logs/iter-2/ios-signing-preflight.json\n")
    os.utime(old_json, (100, 100))
    os.utime(new_log, (200, 200))
    monkeypatch.setattr(runner, "TASKS_DIR", workspace / ".automind" / "tasks")

    rec = runner.latest_signing_recommendation(task_dir)

    assert rec["recommendedTeam"] == "NEWTEAM456"
    assert rec["source"] == "logs/iter-2/ios-signing-preflight-discover.log"


def test_ios_probe_flow_root_install_failure_recommends_runner_strategy(tmp_path: Path, monkeypatch) -> None:
    runner = _load_ios_runner()
    workspace = tmp_path / "workspace"
    task_dir = workspace / ".automind" / "tasks" / "ios_root_install_task"
    task_dir.mkdir(parents=True)
    (task_dir / "probe-flow.ios.json").write_text(json.dumps({
        "adapter": "xcuitest",
        "app": {"projectPath": "/tmp/Demo.xcodeproj", "scheme": "Demo", "deviceId": "FAKE", "bundleId": "com.example"},
        "testIntent": {
            "goal": "root install",
            "sources": ["TC-F01"],
            "steps": [{"type": "tap", "name": "Tap", "selector": {"accessibilityIdentifier": "go"}, "critical": True}],
        },
    }))
    monkeypatch.setattr(runner, "TASKS_DIR", workspace / ".automind" / "tasks")
    monkeypatch.setattr(runner, "WORKSPACE_ROOT", workspace)

    def fake_run(cmd, cwd=None, text=True, capture_output=True, timeout=None):
        (task_dir / "evaluation.json").write_text(json.dumps({
            "iteration": 1,
            "result": "fail",
            "summary": "Root install style is not supported on this device",
            "failedChecks": [{
                "name": "xcodebuild physical XCUITest",
                "category": "external_runner_root_install_unsupported",
                "reason": "Root install style is not supported on this device",
            }],
            "evidence": [],
            "nextAction": "retry_generator",
        }))
        return SimpleNamespace(returncode=1, stdout="root-install", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "run_preflight", lambda task_code, iteration, core_device_id=None: (True, {"result": "pass", "nextAction": "finish"}, "preflight-ok"))
    monkeypatch.setattr(runner, "run_ui_evidence_gate", lambda task_dir_arg, iteration, evaluation: evaluation)
    monkeypatch.setattr(sys, "argv", ["ios_probe_flow_runner.py", "ios_root_install_task", "1"])

    assert runner.main() == 1
    evaluation = json.loads((task_dir / "evaluation.json").read_text())
    assert evaluation["nextAction"] == "retry_generator"
    assert "dry-run" in " ".join(evaluation["recommendedRunnerStrategy"]["doNotUse"])
    assert "test-without-building" in " ".join(evaluation["recommendedRunnerStrategy"]["try"])
    assert "external UI runner" in evaluation["recommendedRunnerStrategy"]["reason"]
    assert "iOS Simulator" in evaluation["recommendedRunnerStrategy"]["reason"]
    assert any("iOS Simulator" in item for item in evaluation["recommendedRunnerStrategy"]["try"])
