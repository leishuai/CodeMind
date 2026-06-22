from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_runner():
    path = Path("scripts/android_probe_flow_runner.py")
    spec = importlib.util.spec_from_file_location("android_probe_flow_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_android_overlay_candidate_nodes_classify_safe_and_sensitive() -> None:
    runner = _load_runner()
    xml = '''<hierarchy>
      <node package="com.example" class="android.widget.Button" text="稍后" content-desc="" clickable="true" enabled="true" bounds="[10,20][110,70]" />
      <node package="com.example" class="android.widget.Button" text="允许访问通讯录" content-desc="" clickable="true" enabled="true" bounds="[10,80][210,130]" />
      <node package="com.other" class="android.widget.Button" text="关闭" content-desc="" clickable="true" enabled="true" bounds="[10,140][110,190]" />
    </hierarchy>'''
    nodes = runner.overlay_candidate_nodes_from_xml(xml, "com.example")
    assert [node["text"] for node in nodes] == ["稍后", "允许访问通讯录"]

    safe = runner.rank_overlay_candidates(nodes, {})
    sensitive = runner.sensitive_overlay_candidates(nodes, {})
    assert [node["text"] for node in safe] == ["稍后"]
    assert [node["text"] for node in sensitive] == ["允许访问通讯录"]


def test_android_probe_flow_dry_run_accepts_ui_unblock(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    flow = tmp_path / "probe-flow.android.json"
    out = tmp_path / "out"
    flow.write_text(json.dumps({
        "platform": "android",
        "app": {"package": "com.example", "activity": ".MainActivity"},
        "uiUnblock": {"enabled": True, "maxAttempts": 2},
        "steps": [
            {"type": "launch", "name": "Launch app"},
            {"type": "tap", "name": "Tap primary", "selector": {"text": "Play"}},
        ],
    }))

    result = subprocess.run(
        [sys.executable, "scripts/android_probe_flow_runner.py", "--flow", str(flow), "--out", str(out), "--dry-run"],
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["result"] == "pass"
    assert json.loads((out / "probe-flow-summary.json").read_text())["result"] == "pass"


def test_sensitive_overlay_routes_to_whitelisted_ask_user(tmp_path: Path) -> None:
    import json

    from orchestrator.main import probe_summary_to_evaluation

    task_dir = tmp_path / "task01"
    iter_dir = task_dir / "logs" / "iter-1" / "probe-flow"
    iter_dir.mkdir(parents=True)
    (task_dir / "TestCases.md").write_text("""
# TestCases

| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / AutoMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |
|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|
| TC-F01 | R01 / AC-001 | Functional App/UI | device | adb | android-probe-flow | open target page | target page evidence | - | yes |
""")
    summary_path = iter_dir / "probe-flow-summary.json"
    (iter_dir / "action-trace.jsonl").write_text("")
    summary = {
        "result": "fail",
        "checks": [{
            "name": "target tap",
            "ok": False,
            "detail": "blocked_sensitive: sensitive overlay requires authorization",
            "evidence": "logs/iter-1/probe-flow/overlay-unblock.json",
        }],
        "uiUnblock": {
            "result": "blocked_sensitive",
            "sensitiveCandidates": [{
                "text": "允许访问通讯录",
                "classification": {
                    "category": "sensitive",
                    "sensitiveCategory": "positive_privacy_or_terms_consent",
                },
            }],
        },
    }
    summary_path.write_text(json.dumps(summary))

    evaluation = probe_summary_to_evaluation(summary, 1, summary_path)

    assert evaluation["result"] == "blocked"
    assert evaluation["nextAction"] == "ask_user"
    assert evaluation["failedChecks"][0]["category"] == "permission_blocked"
    assert evaluation["askUserQuestion"]["category"] == "unauthorized_destructive_or_sensitive"
    assert evaluation["askUserQuestion"]["sensitiveCandidates"][0]["text"] == "允许访问通讯录"
