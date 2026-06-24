from __future__ import annotations

import json
from pathlib import Path

import orchestrator.config as config
import orchestrator.knowledge_index as knowledge_index


def _make_preloaded(runtime: Path) -> None:
    preloaded = runtime / "summaries" / "preloaded"
    preloaded.mkdir(parents=True)
    (preloaded / "ios-custom-build-bazel-build.md").write_text(
        "---\n"
        "name: ios-custom-build-bazel-build\n"
        "description: \"iOS custom build wrapper/Bazel/Xcode build diagnostics.\"\n"
        "use_when:\n"
        "  - \"iOS custom_build_wrapper bazel libtool build\"\n"
        "  - \"custom_build_wrapper.sh --build is killed\"\n"
        "solves:\n"
        "  - \"keeps build-wrapper failures classified with stable evidence\"\n"
        "---\n"
        "# iOS custom build wrapper Bazel Build\n\nbody\n"
    )
    (preloaded / "android-readiness.md").write_text(
        "---\n"
        "name: android-readiness\n"
        "description: \"Android real-device readiness.\"\n"
        "use_when:\n"
        "  - \"adb device classification\"\n"
        "---\n"
        "# Android readiness\n\nbody\n"
    )
    (preloaded / "README.md").write_text("# readme, must be skipped\n")


def _setup(monkeypatch, tmp_path: Path, user_input: str, task_type: str) -> Path:
    runtime = tmp_path / "runtime"
    workspace = tmp_path / "workspace"
    task_dir = workspace / ".automind" / "tasks" / "task"
    summary_dir = workspace / ".automind" / "summary"
    summary_dir.mkdir(parents=True)
    task_dir.mkdir(parents=True)
    _make_preloaded(runtime)

    (task_dir / "runtime-state.json").write_text(json.dumps({
        "taskId": "task", "userInput": user_input, "taskType": task_type,
    }))

    monkeypatch.setattr(config, "AUTOMIND_ROOT", runtime)
    monkeypatch.setattr(config, "AUTOMIND_WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(knowledge_index, "AUTOMIND_ROOT", runtime)
    monkeypatch.setattr(knowledge_index, "AUTOMIND_WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(knowledge_index, "PRELOADED_DIR", runtime / "summaries" / "preloaded")
    monkeypatch.setattr(knowledge_index, "KNOWLEDGE_INDEX_PATH", summary_dir / "index.jsonl")
    monkeypatch.setattr(knowledge_index, "GLOBAL_KNOWLEDGE_INDEX_PATH", runtime / "summaries" / "index.jsonl")
    monkeypatch.setattr(knowledge_index, "ACCUMULATED_INDEX_ROOT", runtime / "summaries" / "accumulated")
    return task_dir


def test_preloaded_records_join_retrieval_pool(monkeypatch, tmp_path: Path) -> None:
    records = {}
    runtime = tmp_path / "runtime"
    _make_preloaded(runtime)
    monkeypatch.setattr(knowledge_index, "PRELOADED_DIR", runtime / "summaries" / "preloaded")
    loaded = knowledge_index._load_preloaded_records()
    ids = {r["id"] for r in loaded}
    assert "preloaded-ios-custom-build-bazel-build" in ids
    assert "preloaded-android-readiness" in ids
    # README.md must be skipped.
    assert all("readme" not in r["id"].lower() for r in loaded)


def test_preloaded_ios_pack_matches_ios_build_task(monkeypatch, tmp_path: Path) -> None:
    task_dir = _setup(monkeypatch, tmp_path, "iOS custom_build_wrapper bazel libtool build for ExampleUIMacros", "ios")
    matches = knowledge_index.search_knowledge(task_dir, "generator")
    ids = {m["id"] for m in matches}
    assert "preloaded-ios-custom-build-bazel-build" in ids
    # The android pack must not leak into an iOS task.
    assert "preloaded-android-readiness" not in ids


def test_preloaded_does_not_leak_into_unrelated_task(monkeypatch, tmp_path: Path) -> None:
    task_dir = _setup(monkeypatch, tmp_path, "refactor a python logging helper", "script")
    matches = knowledge_index.search_knowledge(task_dir, "generator")
    ids = {m["id"] for m in matches}
    assert "preloaded-ios-custom-build-bazel-build" not in ids
    assert "preloaded-android-readiness" not in ids


def test_format_raw_path_for_reuse_emits_absolute_runtime_path(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(knowledge_index, "AUTOMIND_ROOT", runtime)
    monkeypatch.setattr(knowledge_index, "AUTOMIND_WORKSPACE_ROOT", workspace)

    runtime_rec = {"rawPath": "summaries/preloaded/ios-custom-build-bazel-build.md", "_source": "runtime"}
    out = knowledge_index.format_raw_path_for_reuse(runtime_rec)
    assert str(runtime) in out
    assert out.endswith("(runtime)")
    assert not out.lstrip("`").startswith("summaries/")  # not a bare relative path

    ws_rec = {"rawPath": ".automind/summary/raw/x.md", "_source": "workspace"}
    out_ws = knowledge_index.format_raw_path_for_reuse(ws_rec)
    assert str(workspace) in out_ws
    assert out_ws.endswith("(workspace)")


def test_preloaded_match_reason_is_self_explaining(monkeypatch, tmp_path: Path) -> None:
    """P1-C: a matched preloaded pack must be tagged as a curated baseline (not a
    bare ``preloaded``) and still carry its concrete match signals."""
    task_dir = _setup(monkeypatch, tmp_path, "iOS custom_build_wrapper bazel libtool build for ExampleUIMacros", "ios")
    matches = knowledge_index.search_knowledge(task_dir, "generator")
    ios_pack = next(m for m in matches if m["id"] == "preloaded-ios-custom-build-bazel-build")
    reasons = ios_pack["_matchReasons"]
    assert "preloaded(curated baseline)" in reasons
    # The concrete signals that justify the match are still present.
    assert any(r.startswith("trigger=") for r in reasons)
    assert "taskType=ios" in reasons
