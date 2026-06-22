import json
from pathlib import Path

from orchestrator.context_packs import build_generator_context_pack, build_evaluator_context_pack
from orchestrator.iteration_planning import build_exploration_context, write_iteration_purpose
from orchestrator.state import write_runtime_state
from orchestrator.tc_attempts import record_tc_attempts


def _base_task(task: Path) -> None:
    task.mkdir(parents=True)
    (task / "Brainstorm.md").write_text("# Brainstorm\n")
    (task / "Requirements.md").write_text("# Requirements\n")
    (task / "TestCases.md").write_text("# TestCases\n\n| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / AutoMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n| TC-F04 | R01 / AC-004 | Functional | runtime | app | probe | find play path | music_audio_stop | - | yes |\n")
    (task / "Plan.md").write_text("# Plan\n")
    (task / "Validation.md").write_text("# Validation\n")
    (task / "Delivery.md").write_text("# Delivery\n")
    write_runtime_state(task, {"taskId": task.name, "iteration": 3, "status": "retry_pending", "currentOwner": "generator", "nextAction": "retry_generator"})


def test_iteration_purpose_records_exploration_context(tmp_path: Path) -> None:
    task = tmp_path / "task01"
    _base_task(task)
    record_tc_attempts(task, {
        "iteration": 2,
        "testResults": [{
            "testCaseId": "TC-F04",
            "result": "partial",
            "progressKind": "navigation",
            "hypothesis": "catalog tab exposes play button",
            "actionTried": "tap catalog",
            "expectedSignal": "play button appears",
            "outcome": "catalog opened but no play button",
            "ruledOut": ["catalog tab play button"],
            "remainingHypotheses": ["detail page play button", "bottom mini-player"],
        }],
    })
    iter_dir = task / "logs" / "iter-3"

    purpose = write_iteration_purpose(task, 3, "generator", iter_dir)

    assert purpose["mode"] == "exploration_convergence"
    assert purpose["explorationContext"]["items"][0]["ruledOut"] == ["catalog tab play button"]
    assert "bottom mini-player" in (iter_dir / "iteration-purpose.md").read_text()
    state = json.loads((task / "runtime-state.json").read_text())
    assert state["latestIterationPurpose"]["path"].endswith("iteration-purpose.md")


def test_context_pack_includes_iteration_purpose_and_tc_attempts(tmp_path: Path) -> None:
    task = tmp_path / "task01"
    _base_task(task)
    record_tc_attempts(task, {"iteration": 1, "testResults": [{"testCaseId": "TC-F04", "result": "partial", "remainingHypotheses": ["detail page"]}]})
    iter_dir = task / "logs" / "iter-4"
    write_iteration_purpose(task, 4, "generator", iter_dir)

    gen = build_generator_context_pack(task, 4, iter_dir)
    ev = build_evaluator_context_pack(task, 4, iter_dir)

    gen_md = Path(gen["markdownPath"]).read_text()
    ev_md = Path(ev["markdownPath"]).read_text()
    assert "iteration-purpose.md" in gen_md
    assert "tc-attempts.json" in gen_md
    assert "iteration-purpose.md" in ev_md
    assert "tc-attempts.json" in ev_md


def test_build_exploration_context_is_advisory_not_guard(tmp_path: Path) -> None:
    task = tmp_path / "task01"
    _base_task(task)
    context = build_exploration_context(task)
    assert context["rule"].endswith("not a hard guard.")
    assert context["items"] == []


def test_iteration_purpose_md_warns_on_no_narrowing_invalid_retry(tmp_path: Path) -> None:
    """P0-B: repeated attempts that never ruled anything out or proposed a new
    candidate must render an explicit invalid-retry WARNING in the purpose md."""
    task = tmp_path / "task01"
    _base_task(task)
    # Two failing rounds, both pure summaries -> narrowingRounds stays 0.
    record_tc_attempts(task, {"iteration": 1, "testResults": [{"testCaseId": "TC-F04", "result": "fail", "summary": "no pause control found"}]})
    record_tc_attempts(task, {"iteration": 2, "testResults": [{"testCaseId": "TC-F04", "result": "fail", "summary": "still no pause control"}]})
    iter_dir = task / "logs" / "iter-3"

    purpose = write_iteration_purpose(task, 3, "generator", iter_dir)

    item = purpose["explorationContext"]["items"][0]
    assert item["narrowingRounds"] == 0
    assert item["attemptCount"] >= 2
    md = (iter_dir / "iteration-purpose.md").read_text()
    assert "WARNING" in md
    assert "invalid retry pattern" in md


def test_iteration_purpose_md_no_warning_when_narrowing(tmp_path: Path) -> None:
    """P0-B: when attempts ruled paths out / proposed candidates, no invalid-retry
    WARNING should appear."""
    task = tmp_path / "task01"
    _base_task(task)
    record_tc_attempts(task, {"iteration": 1, "testResults": [{
        "testCaseId": "TC-F04",
        "result": "partial",
        "ruledOut": ["catalog tab play button"],
        "remainingHypotheses": ["detail page play button"],
    }]})
    record_tc_attempts(task, {"iteration": 2, "testResults": [{
        "testCaseId": "TC-F04",
        "result": "partial",
        "nextSelectorCandidates": ["identifier == 'player_pause'"],
    }]})
    iter_dir = task / "logs" / "iter-3"

    purpose = write_iteration_purpose(task, 3, "generator", iter_dir)

    item = purpose["explorationContext"]["items"][0]
    assert item["narrowingRounds"] >= 1
    md = (iter_dir / "iteration-purpose.md").read_text()
    assert "WARNING" not in md
