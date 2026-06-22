from __future__ import annotations

import json
from pathlib import Path

import orchestrator.config as config
import orchestrator.knowledge_index as knowledge_index
import orchestrator.summary as summary


def _patch_workspace(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    summary_dir = workspace / ".automind" / "summary"
    workspace.mkdir()
    summary_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "AUTOMIND_WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(config, "SUMMARY_DIR", summary_dir)
    monkeypatch.setattr(knowledge_index, "AUTOMIND_WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(knowledge_index, "SUMMARY_DIR", summary_dir)
    monkeypatch.setattr(knowledge_index, "KNOWLEDGE_INDEX_PATH", summary_dir / "index.jsonl")
    monkeypatch.setattr(knowledge_index, "KNOWLEDGE_RAW_DIR", summary_dir / "raw")
    monkeypatch.setattr(summary, "AUTOMIND_WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(summary, "SUMMARY_DIR", summary_dir)
    monkeypatch.setattr(summary, "KNOWLEDGE_RAW_DIR", summary_dir / "raw")
    return workspace, summary_dir


def test_validate_summary_refiner_no_action(monkeypatch, tmp_path: Path) -> None:
    workspace, _summary_dir = _patch_workspace(monkeypatch, tmp_path)
    task_dir = workspace / ".automind" / "tasks" / "task1"
    task_dir.mkdir(parents=True)
    cleaned, warnings = summary.validate_ai_summary_refinement(task_dir, {
        "schema": "automind.ai_summary_refinement.v1",
        "taskCode": "task1",
        "result": "no_action",
        "summary": "Deterministic summary is sufficient.",
        "knowledgeActions": [{"action": "no_action", "reason": "No durable reusable knowledge."}],
    })
    assert cleaned["result"] == "no_action"
    assert cleaned["knowledgeActions"] == [{"action": "no_action", "reason": "No durable reusable knowledge.", "evidence": []}]
    assert warnings == []


def test_apply_summary_refiner_upsert_raw_and_index(monkeypatch, tmp_path: Path) -> None:
    workspace, summary_dir = _patch_workspace(monkeypatch, tmp_path)
    task_dir = workspace / ".automind" / "tasks" / "ios_task"
    task_dir.mkdir(parents=True)
    (task_dir / "summary.md").write_text("# Summary\n")
    cleaned, warnings = summary.validate_ai_summary_refinement(task_dir, {
        "schema": "automind.ai_summary_refinement.v1",
        "taskCode": "ios_task",
        "result": "ok",
        "summary": "Promote iOS build lesson.",
        "knowledgeActions": [{
            "action": "upsert_raw",
            "reason": "Evidence-backed iOS build command should be reused.",
            "rawPath": ".automind/summary/raw/ios-build/example-ios-build.md",
            "content": "# ExampleApp iOS Build\n\nUse the recorded xcodebuild path only when scheme and signing match. Evidence: logs/iter-1/evaluator.log.",
            "evidence": ["logs/iter-1/evaluator.log"],
            "indexRecord": {
                "id": "example-ios-build",
                "title": "ExampleApp iOS build",
                "value": "Avoid repeated iOS build command discovery.",
                "confidence": "high",
                "phaseApplicability": ["plan", "generator", "evaluator"],
                "surfaces": ["ios", "build"],
                "triggers": ["xcodebuild", "ExampleApp"],
                "successfulPaths": ["Use the recorded xcodebuild path when preconditions match."],
            },
        }],
    })
    assert warnings == []
    applied, apply_warnings = summary.apply_ai_knowledge_actions(task_dir, cleaned)
    assert apply_warnings == []
    assert applied == [{"action": "upsert_raw", "rawPath": ".automind/summary/raw/ios-build/example-ios-build.md", "indexId": "example-ios-build"}]
    raw_file = workspace / ".automind" / "summary" / "raw" / "ios-build" / "example-ios-build.md"
    assert raw_file.exists()
    assert "recorded xcodebuild path" in raw_file.read_text()
    index_lines = (summary_dir / "index.jsonl").read_text().splitlines()
    assert len(index_lines) == 1
    record = json.loads(index_lines[0])
    assert record["id"] == "example-ios-build"
    assert record["rawPath"] == ".automind/summary/raw/ios-build/example-ios-build.md"


def test_validate_summary_refiner_rejects_unsafe_or_thin_raw(monkeypatch, tmp_path: Path) -> None:
    workspace, _summary_dir = _patch_workspace(monkeypatch, tmp_path)
    task_dir = workspace / ".automind" / "tasks" / "task2"
    task_dir.mkdir(parents=True)
    cleaned, warnings = summary.validate_ai_summary_refinement(task_dir, {
        "schema": "automind.ai_summary_refinement.v1",
        "result": "ok",
        "knowledgeActions": [{
            "action": "upsert_raw",
            "rawPath": "../../outside.md",
            "content": "too short",
            "indexRecord": {"id": "bad", "title": "Bad", "value": "Bad"},
        }],
    })
    assert cleaned["knowledgeActions"] == []
    assert any("too-short" in warning for warning in warnings)
