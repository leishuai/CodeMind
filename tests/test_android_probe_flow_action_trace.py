from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def _load_runner():
    path = Path("scripts/android_probe_flow_runner.py")
    spec = importlib.util.spec_from_file_location("android_probe_flow_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_action_trace_helper_resolves_selector_and_bounds() -> None:
    runner = _load_runner()
    xml = '''<hierarchy><node package="com.example.app" class="android.widget.ImageView" resource-id="com.example.app:id/play_button" text="" content-desc="Play" clickable="true" enabled="true" bounds="[520,1840][620,1940]" /></hierarchy>'''
    node = runner.resolve_node_from_xml(xml, {"resource_id": "com.example.app:id/play_button"})
    assert node["resource_id"] == "com.example.app:id/play_button"
    assert node["bounds"] == [520, 1840, 620, 1940]
    assert node["center"] == {"x": 570, "y": 1890}
    hit = runner.find_node_at_point(xml, 570, 1890)
    assert hit["content_desc"] == "Play"


def test_android_probe_flow_dry_run_still_writes_summary(tmp_path: Path) -> None:
    flow = tmp_path / "probe-flow.android.json"
    out = tmp_path / "out"
    flow.write_text(json.dumps({
        "platform": "android",
        "app": {"package": "com.example", "activity": ".MainActivity"},
        "steps": [
            {"type": "launch", "name": "Launch app"},
            {"type": "tap", "name": "Tap primary", "selector": {"resource_id": "com.example:id/primary"}, "critical": True},
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
    summary = json.loads((out / "probe-flow-summary.json").read_text())
    assert summary["dryRun"] is True
    assert summary["flowKind"] == "proof"
    assert len(summary["stepResults"]) == 2
    assert any(item["path"].endswith("probe-flow-summary.json") for item in summary["evidenceIndex"])


def test_android_probe_flow_strong_postcheck_missing_is_partial(tmp_path: Path) -> None:
    flow = tmp_path / "probe-flow.android.json"
    out = tmp_path / "out"
    flow.write_text(json.dumps({
        "platform": "android",
        "app": {"package": "com.example.app", "activity": ".MainActivity"},
        "steps": [
            {"type": "launch", "name": "Launch app"},
            {"type": "current_app", "name": "Assert package"},
            {"type": "dump_hierarchy", "name": "Dump startup UI"},
            {"type": "screenshot", "name": "Startup screenshot"},
        ],
        "postChecks": [{
            "type": "requires_refined_music_stop_flow",
            "strength": "strong",
            "expectedSignals": ["music_audio_finish", "stop_reason"],
        }],
    }))
    result = subprocess.run(
        [sys.executable, "scripts/android_probe_flow_runner.py", "--flow", str(flow), "--out", str(out), "--dry-run"],
        text=True,
        capture_output=True,
    )
    data = json.loads(result.stdout)
    assert result.returncode == 1
    assert data["result"] == "partial"
    assert data["flowKind"] == "discovery"
    assert data["semanticVerdict"]["missingEvidence"] == ["music_audio_finish", "stop_reason"]
    assert data["strongPostChecks"][0]["status"] == "missing"


def test_android_probe_summary_partial_maps_to_retry_generator(tmp_path: Path) -> None:
    from orchestrator.main import probe_summary_to_evaluation

    task_dir = tmp_path / "task01"
    (task_dir / "logs" / "iter-3").mkdir(parents=True)
    (task_dir / "TestCases.md").write_text("""
# TestCases

| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |
|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|
| TC-F04 | R01 / AC-001 | Functional | device | adb | android-probe-flow | play/pause proof | music_audio_finish + stop_reason | - | yes |
""")
    summary_path = task_dir / "logs" / "iter-3" / "probe-flow-summary.json"
    summary = {
        "result": "partial",
        "checks": [
            {"name": "Launch app", "ok": True, "detail": "ok"},
            {"name": "strong postChecks", "ok": False, "detail": "missing"},
        ],
        "strongPostChecks": [{
            "type": "requires_refined_music_stop_flow",
            "strength": "strong",
            "status": "missing",
            "expectedSignals": ["music_audio_finish", "stop_reason"],
            "observedSignals": [],
            "missingSignals": ["music_audio_finish", "stop_reason"],
        }],
        "semanticVerdict": {
            "result": "partial",
            "reason": "strong postCheck evidence is missing; continue refining/executing the proof flow",
            "missingEvidence": ["music_audio_finish", "stop_reason"],
        },
    }
    summary_path.write_text(json.dumps(summary))

    evaluation = probe_summary_to_evaluation(summary, 3, summary_path)

    assert evaluation["result"] == "partial"
    assert evaluation["nextAction"] == "retry_generator"
    assert evaluation["failedChecks"][0]["category"] == "validation_incomplete"
    assert "music_audio_finish" in evaluation["failedChecks"][0]["reason"]
    assert evaluation["testResults"][0]["testCaseId"] == "TC-F04"
    assert evaluation["testResults"][0]["result"] == "partial"
    assert evaluation["testResults"][0]["attemptIteration"] == 3
    assert evaluation["testResults"][0]["missingSignals"] == ["music_audio_finish", "stop_reason"]


def test_app_use_helpers_extract_tags_and_swipe_coords() -> None:
    runner = _load_runner()
    xml = '''<hierarchy>
      <node package="com.example.app" class="android.widget.TextView" text="Overview" content-desc="" bounds="[10,200][100,240]" />
      <node package="com.example.app" class="android.widget.TextView" text="Category" content-desc="" bounds="[20,260][120,300]" />
      <node package="com.example.app" class="android.widget.TextView" text="FeaturedTag" content-desc="" bounds="[140,260][280,300]" />
      <node package="com.example.app" class="android.widget.TextView" text="这是一段很长很长的Overview文本，不应该被当作 tag。" content-desc="" bounds="[20,320][800,500]" />
    </hierarchy>'''
    values = runner.extract_texts_near_anchor(xml, "Overview", below_only=True)
    tags = runner.classify_tags(values)
    assert "Category" in tags
    assert "FeaturedTag" in tags
    assert all("Overview文本" not in item for item in tags)
    assert runner.direction_to_swipe("up", 1000, 2000) == (500, 1560, 500, 600)
    assert runner.direction_to_swipe("down", 1000, 2000) == (500, 600, 500, 1560)


def test_android_probe_flow_dry_run_accepts_app_use_steps(tmp_path: Path) -> None:
    flow = tmp_path / "probe-flow.android.json"
    out = tmp_path / "out"
    flow.write_text(json.dumps({
        "platform": "android",
        "app": {"package": "com.example.app", "activity": "com.example.app.MainActivity"},
        "steps": [
            {"type": "launch", "name": "Launch app"},
            {"type": "tap", "name": "Tap 听书", "selector": {"text": "听书"}},
            {"type": "tap_nth", "name": "Tap first audio card", "selector": {"class": "android.view.ViewGroup"}, "index": 0},
            {"type": "scroll", "name": "Scroll intro", "direction": "up"},
            {"type": "scroll_until_text", "name": "Find tags", "text": "Overview", "direction": "up", "maxSwipes": 3},
            {"type": "extract_tags", "name": "Extract intro tags", "nearText": "Overview", "scope": "bottom_visible", "output": "intro-tags"},
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
    assert data["flowKind"] == "proof"
    assert [row["type"] for row in data["stepResults"]][-4:] == ["tap_nth", "scroll", "scroll_until_text", "extract_tags"]


def test_probe_summary_to_evaluation_includes_ui_exploration(tmp_path: Path) -> None:
    from orchestrator.main import probe_summary_to_evaluation

    task_dir = tmp_path / "task01"
    (task_dir / "logs" / "iter-2" / "probe-flow").mkdir(parents=True)
    (task_dir / "TestCases.md").write_text("""
# TestCases

| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |
|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|
| TC-F01 | R01 / AC-001 | Functional App/UI | device | adb | android-probe-flow | open audio detail and extract intro tags | action trace + tags | - | yes |
""")
    summary_path = task_dir / "logs" / "iter-2" / "probe-flow" / "probe-flow-summary.json"
    trace_path = task_dir / "logs" / "iter-2" / "probe-flow" / "action-trace.jsonl"
    trace_path.write_text('{"ok":true}\n')
    summary = {
        "result": "pass",
        "goal": "番茄畅听听书详情Overview底部 tags",
        "appUseMode": "user_path",
        "checks": [{"name": "extract tags", "ok": True, "detail": "ok"}],
        "actionTrace": [
            {
                "stepIndex": 2,
                "type": "tap",
                "name": "Tap 听书",
                "intent": "进入听书频道",
                "ok": True,
                "detail": "//*[@text=听书]",
                "evidenceAfter": {"screenshot": "after.png", "hierarchy": "after.xml"},
            },
            {
                "stepIndex": 8,
                "type": "extract_tags",
                "name": "Extract intro tags",
                "intent": "读取Overview底部 tag",
                "ok": True,
                "extracted": {"tags": ["Category", "FeaturedTag"]},
                "evidence": "intro-tags.json",
            },
        ],
        "stepResults": [],
    }
    summary_path.write_text(json.dumps(summary))

    evaluation = probe_summary_to_evaluation(summary, 2, summary_path)

    row = evaluation["testResults"][0]
    assert row["testCaseId"] == "TC-F01"
    assert row["uiExploration"]["mode"] == "user_path"
    assert row["uiExploration"]["platform"] == "android"
    assert row["uiExploration"]["stopReason"] == "proved"
    assert row["uiExploration"]["attempts"][0]["actionTried"] == "tap: Tap 听书"
    assert row["uiExploration"]["extracted"]["tags"] == ["Category", "FeaturedTag"]
