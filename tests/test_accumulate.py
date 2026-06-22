from pathlib import Path

import orchestrator.accumulate as accumulate
from orchestrator.accumulate import (
    looks_business_specific,
    parse_yaml_frontmatter,
    project_slug,
    sink_accumulated_lessons,
)


def _patch_dirs(tmp_path, monkeypatch, workspace_name="my-cool-app"):
    root = Path(tmp_path)
    technical = root / "accumulated" / "technical"
    business = root / "accumulated" / "business"
    monkeypatch.setattr(accumulate, "ACCUMULATED_TECHNICAL_DIR", technical)
    monkeypatch.setattr(accumulate, "ACCUMULATED_BUSINESS_DIR", business)
    monkeypatch.setattr(accumulate, "AUTOMIND_WORKSPACE_ROOT", root / workspace_name)
    return technical, business


def test_project_slug_sanitizes(tmp_path, monkeypatch):
    _, _ = _patch_dirs(tmp_path, monkeypatch)
    assert project_slug(tmp_path / "My Cool App!") == "my-cool-app"
    assert project_slug(tmp_path / "___") == "project"


def test_parse_yaml_frontmatter_round_trip():
    header = accumulate._yaml_frontmatter(
        schemaVersion=1, scope="technical", entryCount=3,
        updatedAt="2026-06-16T10:30:00",
    )
    body = "# Accumulated lessons\n\ncontent\n"
    meta, remainder = parse_yaml_frontmatter(header + body)
    assert meta.get("schemaVersion") == "1"
    assert meta.get("scope") == "technical"
    assert meta.get("entryCount") == "3"
    assert remainder.strip().startswith("# Accumulated lessons")
    # Missing frontmatter: no errors, empty dict returned.
    assert parse_yaml_frontmatter("just plain text") == ({}, "just plain text")


def test_parse_yaml_frontmatter_handles_quoted_values():
    header = accumulate._yaml_frontmatter(
        note="Has a colon: yes, and a backtick ``",
    )
    meta, _ = parse_yaml_frontmatter(header + "\nbody\n")
    assert "colon: yes" in meta.get("note", "")
    assert "backtick" in meta.get("note", "")


def test_looks_business_specific_signals(tmp_path, monkeypatch):
    _patch_dirs(tmp_path, monkeypatch)
    assert looks_business_specific("launch com.example.app via devicectl")
    assert looks_business_specific("path /Users/alice/proj/build")
    assert looks_business_specific("device 12345678-90AB-CDEF-0000")
    assert looks_business_specific("regression in mycoolapp module")
    assert not looks_business_specific("prefer resource-id selectors over text")


def test_sink_routes_technical_vs_business(tmp_path, monkeypatch):
    technical, business = _patch_dirs(tmp_path, monkeypatch)
    report = sink_accumulated_lessons(
        "task-1",
        final_result="pass",
        successful_paths=[
            {"purpose": "build", "command": "xcodebuild build", "confidence": "high"},
            {"purpose": "install com.example.app", "command": "devicectl install", "confidence": "high"},
        ],
        reusable=["prefer resource-id selectors over text"],
    )
    assert report["technical"] >= 1
    assert report["business"] >= 1

    body_file = technical / "auto-accumulated.md"
    assert body_file.exists()
    # The separate auto-accumulated-index.md is retired; the body file is both
    # the human view and the machine-scannable list.
    assert not (technical / "auto-accumulated-index.md").exists()

    body_text = body_file.read_text()
    # Body file must have leading YAML frontmatter.
    assert body_text.startswith("---")
    assert "schemaVersion: 1" in body_text
    assert "scope: technical" in body_text
    # Business bundle id routed to business, not technical.
    assert "com.example.app" not in body_text
    # Each entry carries an inline machine-scannable marker.
    assert "<!-- entry: " in body_text
    assert '"kind":' in body_text

    business_body = (business / report["slug"] / "auto-accumulated.md").read_text()
    assert "com.example.app" in business_body
    assert business_body.startswith("---")


def test_sink_skips_successful_paths_when_not_passed(tmp_path, monkeypatch):
    _, business = _patch_dirs(tmp_path, monkeypatch)
    sink_accumulated_lessons(
        "task-2",
        final_result="fail",
        successful_paths=[{"purpose": "build", "command": "xcodebuild build"}],
        avoid_paths=[{"failureCategory": "env", "path": "broken sim boot"}],
    )
    business_file = business / "my-cool-app" / "auto-accumulated.md"
    if business_file.exists():
        assert "xcodebuild" not in business_file.read_text()


def test_sink_dedups_by_canonical_key(tmp_path, monkeypatch):
    _, _ = _patch_dirs(tmp_path, monkeypatch)
    first = sink_accumulated_lessons(
        "task-3",
        final_result="pass",
        reusable=["Prefer  resource-id   selectors!"],
    )
    second = sink_accumulated_lessons(
        "task-4",
        final_result="pass",
        reusable=["prefer resource-id selectors"],
    )
    assert first["technical"] == 1
    assert second["technical"] == 0


def test_sink_enforces_entry_cap(tmp_path, monkeypatch):
    technical, _ = _patch_dirs(tmp_path, monkeypatch)
    monkeypatch.setattr(accumulate, "ACCUMULATED_MAX_ENTRIES_PER_FILE", 2)
    for i in range(5):
        sink_accumulated_lessons(
            f"task-cap-{i}",
            final_result="pass",
            reusable=[f"generic lesson number {i}"],
        )
    text = (technical / "auto-accumulated.md").read_text()
    # Entry meta lines OR legacy key lines — both should be capped at 2.
    entry_hits = text.count("<!-- entry:")
    legacy_hits = text.count("<!-- key:")
    assert entry_hits + legacy_hits == 2


def test_sink_frontmatter_entrycount_matches(tmp_path, monkeypatch):
    technical, _ = _patch_dirs(tmp_path, monkeypatch)
    sink_accumulated_lessons(
        "task-5",
        final_result="pass",
        reusable=["lesson one", "lesson two", "lesson three"],
    )
    body = (technical / "auto-accumulated.md").read_text()
    meta, _ = parse_yaml_frontmatter(body)
    assert meta.get("entryCount") == "3"
    assert meta.get("maxEntries") == str(accumulate.ACCUMULATED_MAX_ENTRIES_PER_FILE)


def test_sink_tags_entry_source(tmp_path, monkeypatch):
    technical, business = _patch_dirs(tmp_path, monkeypatch)
    sink_accumulated_lessons(
        "task-src",
        final_result="pass",
        successful_paths=[
            {"purpose": "build", "command": "xcodebuild build", "confidence": "high"},
            {"purpose": "AI synth", "command": "method only", "aiRefined": True},
        ],
        reusable=["AI refined: prefer resource-id selectors", "use deterministic waits"],
    )
    body = (technical / "auto-accumulated.md").read_text()
    # Both source values appear in the entry meta markers within the body file.
    assert '"source": "deterministic"' in body
    assert '"source": "ai_refined"' in body
    assert not (technical / "auto-accumulated-index.md").exists()


def test_sink_upserts_index_jsonl_only_for_ai_refined(tmp_path, monkeypatch):
    import json

    import orchestrator.knowledge_index as knowledge_index

    technical, _ = _patch_dirs(tmp_path, monkeypatch)
    report = sink_accumulated_lessons(
        "task-idx",
        final_result="pass",
        reusable=[
            "AI refined: prefer resource-id selectors over text",
            "use deterministic waits between taps",
        ],
    )
    # Only the ai_refined lesson is promoted to the scored retrieval pool.
    assert report["indexed"] == 1
    index_jsonl = technical / "index.jsonl"
    assert index_jsonl.exists()
    records = [json.loads(line) for line in index_jsonl.read_text().splitlines() if line.strip()]
    assert len(records) == 1
    rec = records[0]
    assert rec["confidence"] == "high"
    assert rec["source"] == "ai_refined"
    assert rec["id"].startswith("acc-technical-")
    assert rec["rawPath"].endswith("auto-accumulated.md")

    # load_knowledge_index picks up the accumulated sibling index.jsonl.
    monkeypatch.setattr(
        knowledge_index, "ACCUMULATED_INDEX_ROOT", Path(tmp_path) / "accumulated"
    )
    monkeypatch.setattr(knowledge_index, "GLOBAL_KNOWLEDGE_INDEX_PATH", Path(tmp_path) / "nope.jsonl")
    monkeypatch.setattr(knowledge_index, "KNOWLEDGE_INDEX_PATH", Path(tmp_path) / "nope2.jsonl")
    loaded = knowledge_index.load_knowledge_index()
    assert any(r["id"].startswith("acc-technical-") for r in loaded)


def test_sink_index_jsonl_upsert_dedups_by_id(tmp_path, monkeypatch):
    import json

    technical, _ = _patch_dirs(tmp_path, monkeypatch)
    sink_accumulated_lessons(
        "task-u1",
        final_result="pass",
        reusable=["AI refined: prefer resource-id selectors"],
    )
    sink_accumulated_lessons(
        "task-u2",
        final_result="pass",
        reusable=["AI refined:  prefer   resource-id selectors!"],
    )
    index_jsonl = technical / "index.jsonl"
    records = [json.loads(line) for line in index_jsonl.read_text().splitlines() if line.strip()]
    # Same canonical content -> same id -> upsert, not duplicate.
    assert len(records) == 1

