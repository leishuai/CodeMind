from __future__ import annotations

import json
from pathlib import Path

import orchestrator.config as config
import orchestrator.knowledge_index as knowledge_index
import orchestrator.reuse as reuse


def test_reuse_manifest_uses_index_and_phase_reuse_without_full_raw(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    workspace = tmp_path / "workspace"
    task_dir = workspace / ".automind" / "tasks" / "ios_task"
    summary_dir = workspace / ".automind" / "summary"
    raw_dir = summary_dir / "raw" / "ios-build"
    raw_dir.mkdir(parents=True)
    task_dir.mkdir(parents=True)

    raw_file = raw_dir / "example-ios-build.md"
    raw_file.write_text("# ExampleApp iOS Build\n\nVERY_DETAILED_RAW_COMMAND_THAT_SHOULD_NOT_BE_IN_REUSE_MANIFEST\n")
    index_path = summary_dir / "index.jsonl"
    index_path.write_text(json.dumps({
        "id": "example-ios-build",
        "title": "ExampleApp iOS build",
        "rawPath": ".automind/summary/raw/ios-build/example-ios-build.md",
        "description": "Known ExampleApp iOS build path.",
        "value": "Avoid repeated iOS build discovery.",
        "taskTypes": ["ios"],
        "projects": ["workspace"],
        "surfaces": ["ios", "build"],
        "phaseApplicability": ["plan", "generator", "evaluator"],
        "triggers": ["ExampleApp", "xcodebuild", "编译"],
        "confidence": "high",
        "successfulPaths": ["Use known ExampleApp build path when scope matches."],
        "avoidPaths": ["Do not recursively scan Pods/DerivedData."],
        "importantReminders": ["Do not change signing without approval."],
    }, ensure_ascii=False) + "\n")

    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "ios_task",
        "userInput": "ExampleApp iOS 编译验证",
        "taskType": "ios",
    }))
    (task_dir / "Requirements.md").write_text("# Requirements\n\nR01 ExampleApp iOS build must work.\n")

    monkeypatch.setattr(config, "AUTOMIND_ROOT", runtime)
    monkeypatch.setattr(config, "AUTOMIND_WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(config, "SUMMARY_DIR", summary_dir)
    monkeypatch.setattr(knowledge_index, "AUTOMIND_ROOT", runtime)
    monkeypatch.setattr(knowledge_index, "AUTOMIND_WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(knowledge_index, "SUMMARY_DIR", summary_dir)
    monkeypatch.setattr(knowledge_index, "KNOWLEDGE_INDEX_PATH", index_path)
    monkeypatch.setattr(knowledge_index, "KNOWLEDGE_RAW_DIR", summary_dir / "raw")
    monkeypatch.setattr(knowledge_index, "GLOBAL_KNOWLEDGE_INDEX_PATH", runtime / "summaries" / "index.jsonl")
    monkeypatch.setattr(knowledge_index, "GLOBAL_KNOWLEDGE_RAW_DIR", runtime / "summaries" / "raw")
    monkeypatch.setattr(reuse, "LOCAL_REUSE_INDEX_PATH", summary_dir / "local-reuse-index.md")
    monkeypatch.setattr(reuse, "SUMMARY_LESSONS_PATH", summary_dir / "lessons-learned.md")

    reuse_path = reuse.write_reuse_context(task_dir, reason="unit_test")
    reuse_text = reuse_path.read_text()
    plan_phase = (task_dir / "phase-reuse" / "plan.md").read_text()

    assert "# Reuse Manifest" in reuse_text
    assert "phase-reuse/plan.md" in reuse_text
    # Reuse.md keeps only index pointers (the entry ID), not the detail.
    assert "example-ios-build" in reuse_text
    assert "VERY_DETAILED_RAW_COMMAND" not in reuse_text
    # Phase-specific detail must NOT be duplicated into the manifest...
    assert "Do not recursively scan Pods/DerivedData" not in reuse_text
    assert "Do not change signing without approval" not in reuse_text
    # ...it lives only in the per-phase reuse file.
    assert "Top successful paths to consider" in plan_phase
    assert "Do not recursively scan Pods/DerivedData" in plan_phase
    assert "Do not change signing without approval" in plan_phase
    assert "VERY_DETAILED_RAW_COMMAND" not in plan_phase


def _setup_gate_task(monkeypatch, tmp_path: Path):
    runtime = tmp_path / "runtime"
    workspace = tmp_path / "workspace"
    task_dir = workspace / ".automind" / "tasks" / "ios_task"
    summary_dir = workspace / ".automind" / "summary"
    raw_dir = summary_dir / "raw" / "ios-build"
    raw_dir.mkdir(parents=True)
    task_dir.mkdir(parents=True)

    index_path = summary_dir / "index.jsonl"
    index_path.write_text(json.dumps({
        "id": "example-ios-build",
        "title": "ExampleApp iOS build",
        "rawPath": ".automind/summary/raw/ios-build/example-ios-build.md",
        "value": "Avoid repeated iOS build discovery.",
        "taskTypes": ["ios"],
        "projects": ["workspace"],
        "surfaces": ["ios", "build", "signing"],
        "phaseApplicability": ["generator", "evaluator"],
        "triggers": ["ExampleApp", "signing", "devicectl"],
        "confidence": "high",
        "successfulPaths": ["Reuse signed app + devicectl install/launch when business code unchanged."],
        "avoidPaths": ["Do not run idevicescreenshot.", "Do not run unnecessary full build."],
        "importantReminders": ["Classify signing issue before asking user."],
    }, ensure_ascii=False) + "\n")

    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "ios_task",
        "userInput": "ExampleApp iOS signing devicectl",
        "taskType": "ios",
    }))
    (task_dir / "Requirements.md").write_text("# Requirements\n\nR01 ExampleApp iOS build.\n")

    monkeypatch.setattr(config, "AUTOMIND_ROOT", runtime)
    monkeypatch.setattr(config, "AUTOMIND_WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(config, "SUMMARY_DIR", summary_dir)
    monkeypatch.setattr(knowledge_index, "AUTOMIND_ROOT", runtime)
    monkeypatch.setattr(knowledge_index, "AUTOMIND_WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(knowledge_index, "SUMMARY_DIR", summary_dir)
    monkeypatch.setattr(knowledge_index, "KNOWLEDGE_INDEX_PATH", index_path)
    monkeypatch.setattr(knowledge_index, "KNOWLEDGE_RAW_DIR", summary_dir / "raw")
    monkeypatch.setattr(knowledge_index, "GLOBAL_KNOWLEDGE_INDEX_PATH", runtime / "summaries" / "index.jsonl")
    monkeypatch.setattr(knowledge_index, "GLOBAL_KNOWLEDGE_RAW_DIR", runtime / "summaries" / "raw")
    return task_dir


def test_compute_reuse_gate_marks_generator_required_with_safe_paths(monkeypatch, tmp_path: Path) -> None:
    task_dir = _setup_gate_task(monkeypatch, tmp_path)
    gate = knowledge_index.compute_reuse_gate(task_dir, "generator")
    assert gate["required"] is True
    assert gate["matchCount"] >= 1
    assert any("devicectl" in p for p in gate["safePaths"])
    assert any("idevicescreenshot" in p for p in gate["avoidPaths"])


def test_record_reuse_ack_sets_acknowledged(monkeypatch, tmp_path: Path) -> None:
    task_dir = _setup_gate_task(monkeypatch, tmp_path)
    # Initialize gate via before-phase hook to mirror runtime flow.
    from orchestrator import hooks
    hooks.run_before_phase_hooks(task_dir, "generator", reason="test")
    gate = knowledge_index.record_reuse_ack(
        task_dir,
        "generator",
        phase_reuse_read=True,
        reuse_applied=["Reuse signed app + devicectl install/launch."],
    )
    assert gate["acknowledged"] is True
    assert gate["acknowledgement"]["reuseApplied"]


def test_reuse_gate_blocks_generator_without_ack(monkeypatch, tmp_path: Path) -> None:
    import orchestrator.workflow as workflow

    task_dir = _setup_gate_task(monkeypatch, tmp_path)
    from orchestrator import hooks
    hooks.run_before_phase_hooks(task_dir, "generator", reason="test")

    from orchestrator.state import read_runtime_state, update_runtime_state
    update_runtime_state(task_dir, nextAction="run_generator", currentOwner="generator")
    state = read_runtime_state(task_dir) or {}
    issues, _ = workflow.check_reuse_gate(task_dir, state)
    assert any("not acknowledged" in i for i in issues)

    knowledge_index.record_reuse_ack(
        task_dir, "generator", phase_reuse_read=True,
        reuse_applied=["devicectl install/launch"],
    )
    state = read_runtime_state(task_dir) or {}
    issues, _ = workflow.check_reuse_gate(task_dir, state)
    assert not any("not acknowledged" in i for i in issues)


def test_reuse_gate_skipped_for_authoritative_terminal_pass(monkeypatch, tmp_path: Path) -> None:
    """A finished task must not be asked for an evaluator reuse-ack.

    delivery_gate_applies() treats status=finished / nextAction=finish as
    at/past the verification boundary, which previously demanded an evaluator
    reuse_gate that a completed task never records (the offline-demo smoke
    false_finish). An authoritative terminal pass means the loop is done.
    """
    import orchestrator.workflow as workflow
    from orchestrator.state import update_runtime_state, read_runtime_state

    task_dir = _setup_gate_task(monkeypatch, tmp_path)
    (task_dir / "completion-report.json").write_text(json.dumps({
        "result": "pass",
        "completionVerdict": {"result": "pass"},
        "rawEvaluationClaim": {"result": "pass", "nextAction": "finish"},
        "testResults": [{"id": "TC-001", "status": "pass"}],
    }) + "\n")
    update_runtime_state(task_dir, status="finished", nextAction="finish", currentOwner="supervisor")
    state = read_runtime_state(task_dir) or {}

    issues, warnings = workflow.check_reuse_gate(task_dir, state)
    assert not any("not initialized" in i for i in issues)
    assert issues == []


def test_reuse_gate_forces_replan_when_repeated_failure_matches_avoid_path(monkeypatch, tmp_path: Path) -> None:
    """P0-3: a repeated failure whose category matches a known avoid-path while
    escalating to ask_user must be blocked in favor of replan."""
    import orchestrator.workflow as workflow

    task_dir = _setup_gate_task(monkeypatch, tmp_path)
    from orchestrator import hooks
    hooks.run_before_phase_hooks(task_dir, "generator", reason="test")
    knowledge_index.record_reuse_ack(
        task_dir, "generator", phase_reuse_read=True,
        reuse_applied=["devicectl install/launch"],
    )

    from orchestrator.state import read_runtime_state, update_runtime_state
    update_runtime_state(task_dir, nextAction="ask_user", currentOwner="generator")
    state = read_runtime_state(task_dir) or {}

    # Inject a repeated failure whose category matches the recorded avoid-path.
    gate = state["reuseGate"]["generator"]
    gate["avoidPaths"] = ["Do not retry external_runner_capability_blocked runner."]
    gate["repeatedFailure"] = {
        "detected": True,
        "category": "external_runner_capability_blocked",
        "sameProblemKey": "ios.xcuitest.ide_interface.unimplemented",
        "sameCategoryAskUserCount": 2,
    }

    issues, _ = workflow.check_reuse_gate(task_dir, state)
    assert any("known avoid-path" in i and "replan" in i for i in issues)


def test_reuse_gate_allows_ask_user_when_no_avoid_path_match(monkeypatch, tmp_path: Path) -> None:
    """A repeated failure that does not match any avoid-path and has a low
    ask-count should not be force-replanned by the P0-3 gate."""
    import orchestrator.workflow as workflow

    task_dir = _setup_gate_task(monkeypatch, tmp_path)
    from orchestrator import hooks
    hooks.run_before_phase_hooks(task_dir, "generator", reason="test")
    knowledge_index.record_reuse_ack(
        task_dir, "generator", phase_reuse_read=True,
        reuse_applied=["devicectl install/launch"],
    )

    from orchestrator.state import read_runtime_state, update_runtime_state
    update_runtime_state(task_dir, nextAction="ask_user", currentOwner="generator")
    state = read_runtime_state(task_dir) or {}

    gate = state["reuseGate"]["generator"]
    gate["avoidPaths"] = ["Do not run idevicescreenshot."]
    gate["repeatedFailure"] = {
        "detected": True,
        "category": "external_runner_signing_blocked",
        "sameProblemKey": "ios.xcuitest.runner.signing_blocked",
        "sameCategoryAskUserCount": 1,
    }

    issues, _ = workflow.check_reuse_gate(task_dir, state)
    assert not any("known avoid-path" in i for i in issues)

