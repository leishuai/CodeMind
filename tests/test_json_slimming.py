import json
from pathlib import Path

from orchestrator.phase_contracts import build_requirements_contract
from orchestrator.workflow_contract import write_workflow_contract
from orchestrator.session.trace import write_trace
from orchestrator.state import write_runtime_state


def test_requirements_contract_does_not_duplicate_full_markdown_per_requirement(tmp_path: Path) -> None:
    task = tmp_path / "task01"
    task.mkdir()
    repeated_body = "LONG_REQUIREMENT_BODY " * 200
    (task / "Requirements.md").write_text(
        "# Requirements\n\n"
        "## R01 First requirement\n"
        f"{repeated_body}\n"
        "- AC-001 must hold\n\n"
        "## R02 Second requirement\n"
        f"{repeated_body}\n"
        "- AC-002 must hold\n"
    )

    contract = build_requirements_contract(task)

    payload = json.dumps(contract["requirements"], ensure_ascii=False)
    assert "LONG_REQUIREMENT_BODY" not in payload
    for req in contract["requirements"]:
        assert "sourceRef" in req
        assert "text" not in req
        assert len(req["title"]) < 260


def test_workflow_contract_uses_compact_testcase_intent(tmp_path: Path) -> None:
    task = tmp_path / "task01"
    task.mkdir()
    write_runtime_state(task, {"taskId": "task01", "taskType": "android", "status": "planned"})
    (task / "Brainstorm.md").write_text("# Brainstorm\n")
    (task / "Requirements.md").write_text("# Requirements\n\n## R01 Req\n- AC-001 ac\n")
    long_steps = "tap/search/assert " * 300
    long_expected = "music_audio_stop observed " * 300
    (task / "TestCases.md").write_text(
        "# TestCases\n\n"
        "| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeAutonomy command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n"
        "|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n"
        f"| TC-F01 | R01 / AC-001 | Functional | device | Android device | ./gradlew test {long_steps} | {long_steps} | {long_expected} | - | yes |\n"
    )
    (task / "Plan.md").write_text("# Plan\n")

    path = write_workflow_contract(task)
    data = json.loads(path.read_text())
    tc = data["testcases"][0]

    assert "source" not in tc
    assert tc["sourceRef"] == {"path": "TestCases.md", "id": "TC-F01"}
    assert tc["intent"]["goal"] == "Prove TC-F01"
    assert len(json.dumps(tc, ensure_ascii=False)) < 5_000
    assert "truncated" in json.dumps(tc, ensure_ascii=False)


def test_trace_write_uses_jsonl_sidecar_for_large_payloads(tmp_path: Path) -> None:
    task = tmp_path / "task01"
    task.mkdir()
    write_runtime_state(task, {"taskId": "task01", "status": "running"})
    with (task / "events.jsonl").open("w", encoding="utf-8") as fh:
        for idx in range(1100):
            fh.write(json.dumps({
                "ts": "2026-06-10T01:00:00",
                "type": "agent_log",
                "message": "X" * 5000,
                "phase": "generator",
                "source": "agent",
                "spanId": f"event:{idx}",
                "data": {"rawOutput": "Y" * 5000, "items": list(range(200))},
            }, ensure_ascii=False) + "\n")

    trace = write_trace("task01", task)

    assert trace["spansTruncated"] > 0
    assert trace["spansRef"] == "trace-spans.jsonl"
    assert (task / "trace-spans.jsonl").exists()
    assert (task / "trace.json").stat().st_size < 2_500_000
    assert "truncated" in (task / "trace.json").read_text()
