import json
from pathlib import Path

from orchestrator.log_digest import build_log_digest
from orchestrator.context_packs import build_generator_context_pack


def test_log_digest_summarizes_large_logs_without_tail_preview(tmp_path: Path) -> None:
    task = tmp_path / ".automind" / "tasks" / "task01"
    iter_dir = task / "logs" / "iter-1"
    iter_dir.mkdir(parents=True)
    (iter_dir / "commands.md").write_text("# Commands\npytest\n")
    large = iter_dir / "build.log"
    large.write_text(("noise\n" * 200_000) + "BUILD FAILED: stop_reason missing\n")

    digest = build_log_digest(task, iter_dir)

    assert (iter_dir / "log-digest.json").exists()
    assert (iter_dir / "log-digest.md").exists()
    build_item = next(item for item in digest["files"] if item["path"].endswith("build.log"))
    assert build_item["recommendedReadMode"] in {"read_digest_then_targeted_grep", "oversized_targeted_grep_only"}
    assert "tailPreview" not in build_item
    assert any("BUILD FAILED" in line for line in build_item["keyLines"])


def test_generator_context_pack_points_to_log_digest(tmp_path: Path) -> None:
    task = tmp_path / ".automind" / "tasks" / "task01"
    task.mkdir(parents=True)
    for name in ["Brainstorm.md", "Requirements.md", "TestCases.md", "Plan.md", "Validation.md"]:
        (task / name).write_text(f"# {name}\ncontent\n")
    (task / "runtime-state.json").write_text("{}")
    iter_dir = task / "logs" / "iter-2"
    iter_dir.mkdir(parents=True)
    (iter_dir / "commands.md").write_text("# Commands\n")

    pack = build_generator_context_pack(task, 2, iter_dir)
    data = json.loads((iter_dir / "generator-context.json").read_text())
    md = (iter_dir / "generator-context.md").read_text()

    assert pack["validationOk"] is True
    assert data["logDigest"]["markdownPath"].endswith("logs/iter-2/log-digest.md")
    assert "Log Reading Policy" in md
    assert "log-digest.md" in md


def test_generator_context_pack_uses_bounded_excerpts_for_large_task_artifacts(tmp_path: Path) -> None:
    task = tmp_path / ".automind" / "tasks" / "task01"
    task.mkdir(parents=True)
    for name in ["Brainstorm.md", "Requirements.md", "TestCases.md", "Plan.md"]:
        (task / name).write_text(f"# {name}\nsmall\n")
    huge_validation = "# Validation\n" + ("NOISE_LINE\n" * 20_000) + "FINAL_SIGNAL stop_reason=remote_control\n"
    huge_delivery = "# Delivery\n" + ("DELIVERY_NOISE\n" * 20_000) + "DELIVERY_SIGNAL music_audio_stop\n"
    (task / "Validation.md").write_text(huge_validation)
    (task / "Delivery.md").write_text(huge_delivery)
    (task / "runtime-state.json").write_text("{}")
    (task / "evaluation.json").write_text('{"nextAction":"retry_generator"}')
    iter_dir = task / "logs" / "iter-3"
    iter_dir.mkdir(parents=True)

    build_generator_context_pack(task, 3, iter_dir)
    data = json.loads((iter_dir / "generator-context.json").read_text())
    md = (iter_dir / "generator-context.md").read_text()

    validation_item = next(item for item in data["files"] if item["path"].endswith("Validation.md"))
    delivery_item = next(item for item in data["files"] if item["path"].endswith("Delivery.md"))

    assert validation_item["includedMode"] == "structured_history_excerpt"
    assert delivery_item["includedMode"] == "structured_history_excerpt"
    assert validation_item["bytes"] > validation_item["excerptBytesLimit"]
    assert delivery_item["bytes"] > delivery_item["excerptBytesLimit"]
    assert "excerpt" not in validation_item
    assert "excerpt" not in delivery_item
    assert validation_item["sourceContent"] == "omitted_from_json_use_source_path_or_agent_facing_context"
    assert validation_item["excerptBytes"] < len(huge_validation) / 2
    assert delivery_item["excerptBytes"] < len(huge_delivery) / 2
    assert "Raw artifact is authoritative on disk" in md
    assert "FINAL_SIGNAL stop_reason=remote_control" in md
    assert "DELIVERY_SIGNAL music_audio_stop" in md
    assert "Raw artifacts remain on disk" in md
    assert "mode=structured_history_excerpt" in md


def test_evaluator_context_pack_uses_bounded_excerpts_for_large_task_artifacts(tmp_path: Path) -> None:
    from orchestrator.context_packs import build_evaluator_context_pack

    task = tmp_path / ".automind" / "tasks" / "task01"
    task.mkdir(parents=True)
    for name in ["Requirements.md", "TestCases.md", "Plan.md"]:
        (task / name).write_text(f"# {name}\nsmall\n")
    huge_validation = "# Validation\n" + ("VAL_NOISE\n" * 20_000) + "LATEST_EVAL_SIGNAL TC-F01 pass\n"
    huge_delivery = "# Delivery\n" + ("DELIVERY_NOISE\n" * 20_000) + "LATEST_DELIVERY_SIGNAL music_audio_stop\n"
    (task / "Validation.md").write_text(huge_validation)
    (task / "Delivery.md").write_text(huge_delivery)
    (task / "runtime-state.json").write_text('{"currentOwner":"evaluator"}')
    (task / "evaluation.json").write_text('{"nextAction":"retry_generator"}')
    iter_dir = task / "logs" / "iter-4"
    iter_dir.mkdir(parents=True)
    (iter_dir / "generator.log").write_text("SECRET_GENERATOR_TRANSCRIPT_SHOULD_NOT_APPEAR\n")

    pack = build_evaluator_context_pack(task, 4, iter_dir)
    data = json.loads((iter_dir / "evaluator-context.json").read_text())
    md = (iter_dir / "evaluator-context.md").read_text()

    validation_item = next(item for item in data["files"] if item["path"].endswith("Validation.md"))
    delivery_item = next(item for item in data["files"] if item["path"].endswith("Delivery.md"))

    assert pack["validationOk"] is True
    assert validation_item["includedMode"] == "structured_history_excerpt"
    assert delivery_item["includedMode"] == "structured_history_excerpt"
    assert "excerpt" not in validation_item
    assert "excerpt" not in delivery_item
    assert "LATEST_EVAL_SIGNAL TC-F01 pass" in md
    assert "LATEST_DELIVERY_SIGNAL music_audio_stop" in md
    assert "SECRET_GENERATOR_TRANSCRIPT_SHOULD_NOT_APPEAR" not in json.dumps(data)
    assert "bounded excerpts" in md


def test_context_pack_keeps_core_contract_files_more_complete_than_history(tmp_path: Path) -> None:
    task = tmp_path / ".automind" / "tasks" / "task01"
    task.mkdir(parents=True)
    large_requirements = "# Requirements\n" + ("AC-001 detail line\n" * 4_000) + "AC-999 FINAL CORE CONTRACT\n"
    large_validation = "# Validation\n" + ("history noise\n" * 20_000) + "FINAL_HISTORY_SIGNAL\n"
    (task / "Brainstorm.md").write_text("# Brainstorm\nsmall\n")
    (task / "Requirements.md").write_text(large_requirements)
    (task / "TestCases.md").write_text("# TestCases\nsmall\n")
    (task / "Plan.md").write_text("# Plan\nsmall\n")
    (task / "Validation.md").write_text(large_validation)
    (task / "runtime-state.json").write_text("{}")
    iter_dir = task / "logs" / "iter-5"
    iter_dir.mkdir(parents=True)

    build_generator_context_pack(task, 5, iter_dir)
    data = json.loads((iter_dir / "generator-context.json").read_text())
    req = next(item for item in data["files"] if item["path"].endswith("Requirements.md"))
    val = next(item for item in data["files"] if item["path"].endswith("Validation.md"))

    assert req["includedMode"] == "full"
    assert "excerpt" not in req
    assert "AC-999 FINAL CORE CONTRACT" in (iter_dir / "generator-context.md").read_text()
    assert val["includedMode"] == "structured_history_excerpt"
    assert req["excerptBytesLimit"] > val["excerptBytesLimit"]


def test_generator_prompt_does_not_require_full_delivery_validation_startup_read() -> None:
    text = Path("templates/generator_prompt.md").read_text()
    startup = text.split("Skill-mode continue-until-done contract:", 1)[0]
    assert "- {task_dir}/Validation.md" not in startup
    assert "- {task_dir}/Delivery.md" not in startup
    assert "generator-context.md" in startup
    assert "targeted raw sections" in startup


def test_context_pack_json_is_metadata_not_source_content(tmp_path: Path) -> None:
    task = tmp_path / ".automind" / "tasks" / "task01"
    task.mkdir(parents=True)
    marker = "UNIQUE_MARKER_ONLY_IN_MARKDOWN_CONTEXT"
    for name in ["Brainstorm.md", "Requirements.md", "TestCases.md", "Plan.md", "Validation.md"]:
        (task / name).write_text(f"# {name}\n{marker}\n")
    (task / "runtime-state.json").write_text("{}")
    iter_dir = task / "logs" / "iter-6"
    iter_dir.mkdir(parents=True)

    build_generator_context_pack(task, 6, iter_dir)
    data_text = (iter_dir / "generator-context.json").read_text()
    md = (iter_dir / "generator-context.md").read_text()

    assert marker not in data_text
    assert marker in md
    data = json.loads(data_text)
    assert data["agentFacingContext"]["markdownPath"].endswith("generator-context.md")
    assert all("excerpt" not in item and "content" not in item for item in data["files"])


def test_log_digest_excludes_context_pack_artifacts(tmp_path: Path) -> None:
    task = tmp_path / ".automind" / "tasks" / "task01"
    iter_dir = task / "logs" / "iter-1"
    iter_dir.mkdir(parents=True)
    (iter_dir / "generator-context.json").write_text("SHOULD_NOT_BE_LISTED")
    (iter_dir / "generator-context.md").write_text("SHOULD_NOT_BE_LISTED")
    (iter_dir / "generator-prompt.md").write_text("SHOULD_NOT_BE_LISTED")
    (iter_dir / "commands.md").write_text("# Commands\n")

    digest = build_log_digest(task, iter_dir)
    paths = {item["path"] for item in digest["files"]}

    assert "logs/iter-1/commands.md" in paths
    assert not any(path.endswith("generator-context.json") for path in paths)
    assert not any(path.endswith("generator-context.md") for path in paths)
    assert not any(path.endswith("generator-prompt.md") for path in paths)
