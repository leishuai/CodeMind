import json
from pathlib import Path

from orchestrator.evidence_synthesis import build_tc_evidence_matrix, synthesize_evaluation_from_evidence
from orchestrator.state import write_runtime_state


def _write_contract(task: Path) -> None:
    task.mkdir(parents=True)
    (task / "TestCases.md").write_text(
        "# TestCases\n\n"
        "| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / CodeMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n"
        "|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n"
        "| TC-F05 | R01 / AC-005 | Functional | runtime | device | probe | trigger play fail | music_audio_stop stop_reason=play_fail | - | yes |\n"
        "| TC-F06 | R01 / AC-006 | Functional | runtime | device | probe | remote control | music_audio_stop stop_reason=remote_control | - | yes |\n"
    )
    (task / "Requirements.md").write_text("# Requirements\n")
    (task / "Plan.md").write_text("# Plan\n")
    write_runtime_state(task, {"taskId": task.name, "iteration": 8, "status": "retry_pending", "currentOwner": "generator", "nextAction": "retry_generator"})


def test_build_tc_evidence_matrix_from_summary_artifacts(tmp_path: Path) -> None:
    task = tmp_path / "task01"
    _write_contract(task)
    iter_dir = task / "logs" / "iter-17" / "play-fail"
    iter_dir.mkdir(parents=True)
    (iter_dir / "play-fail-summary.json").write_text(json.dumps({
        "scenario": "TC-F05-play-fail-final-apk",
        "rows": [{"event": "music_audio_stop", "stop_reason": "play_fail"}],
    }))

    matrix = build_tc_evidence_matrix(task)

    assert matrix["latestEvidenceIteration"] == 17
    rows = {row["testCaseId"]: row for row in matrix["testResults"]}
    assert rows["TC-F05"]["candidateResult"] == "pass_candidate"
    assert rows["TC-F05"]["result"] == "partial"
    assert "stop_reason=play_fail" in rows["TC-F05"]["observedSignals"]
    assert rows["TC-F06"]["result"] == "not_run"
    assert rows["TC-F06"]["candidateResult"] == "missing_evidence"


def test_synthesize_evaluation_updates_partial_testresults_without_finish(tmp_path: Path) -> None:
    task = tmp_path / "task01"
    _write_contract(task)
    (task / "evaluation.json").write_text(json.dumps({
        "iteration": 8,
        "result": "blocked",
        "summary": "Generator timed out",
        "failedChecks": [{"name": "agent", "reason": "timeout", "category": "agent_timeout"}],
        "nextAction": "retry_generator",
    }))
    iter_dir = task / "logs" / "iter-17" / "runtime"
    iter_dir.mkdir(parents=True)
    (iter_dir / "remote-summary.json").write_text(json.dumps({
        "scenario": "TC-F06-remote-control-final-apk",
        "rows": [{"event": "music_audio_stop", "stop_reason": "remote_control"}],
    }))

    evaluation = synthesize_evaluation_from_evidence(task, reason="test")

    assert evaluation is not None
    assert evaluation["iteration"] == 17
    assert evaluation["result"] == "blocked"
    assert evaluation["nextAction"] == "retry_generator"
    assert isinstance(evaluation["testResults"], list)
    assert evaluation["evidenceSynthesis"]["doesNotChangeTaskLevelConclusion"] is True
    saved = json.loads((task / "evaluation.json").read_text())
    assert saved["testResults"]
    assert (task / "tc-attempts.json").exists()



def test_build_tc_evidence_matrix_uses_lightweight_evidence_index(tmp_path: Path) -> None:
    task = tmp_path / "task01"
    _write_contract(task)
    iter_dir = task / "logs" / "iter-18" / "probe-flow"
    iter_dir.mkdir(parents=True)
    (iter_dir / "action-03-after.png").write_text("fake image")
    (iter_dir / "logcat.txt").write_text("captured logcat without target event")
    (iter_dir / "probe-flow-summary.json").write_text(json.dumps({
        "result": "partial",
        "evidenceIndex": [
            {
                "path": "logs/iter-18/probe-flow/action-03-after.png",
                "type": "screenshot",
                "tc": "TC-F05",
                "signal": "screen_after_action",
            },
            {
                "path": "logs/iter-18/probe-flow/logcat.txt",
                "type": "logcat",
                "tc": "TC-F06",
                "signal": "missing:music_audio_stop",
            },
            {
                "path": "logs/iter-18/probe-flow/probe-flow-summary.json",
                "type": "summary",
                "signal": "unassigned_summary",
            },
        ],
    }))

    matrix = build_tc_evidence_matrix(task)
    rows = {row["testCaseId"]: row for row in matrix["testResults"]}

    assert matrix["latestEvidenceIteration"] == 18
    assert {item["path"] for item in matrix["evidenceIndex"]} >= {
        "logs/iter-18/probe-flow/action-03-after.png",
        "logs/iter-18/probe-flow/logcat.txt",
        "logs/iter-18/probe-flow/probe-flow-summary.json",
    }
    assert "logs/iter-18/probe-flow/action-03-after.png" in rows["TC-F05"]["evidence"]
    assert "screen_after_action" in rows["TC-F05"]["observedSignals"]
    assert "logs/iter-18/probe-flow/logcat.txt" in rows["TC-F06"]["evidence"]
    assert "music_audio_stop" in rows["TC-F06"]["missingSignals"]
    assert "unassigned_summary" not in rows["TC-F05"]["observedSignals"]


def test_synthesize_evaluation_persists_evidence_index(tmp_path: Path) -> None:
    task = tmp_path / "task01"
    _write_contract(task)
    iter_dir = task / "logs" / "iter-19" / "runtime"
    iter_dir.mkdir(parents=True)
    (iter_dir / "runtime-summary.json").write_text(json.dumps({
        "result": "partial",
        "evidenceIndex": [
            {"path": "logs/iter-19/runtime/runtime-summary.json", "type": "summary", "tc": "TC-F05", "signal": "runtime_summary_present"}
        ],
    }))

    evaluation = synthesize_evaluation_from_evidence(task, reason="test")

    assert evaluation is not None
    assert evaluation["evidenceIndex"] == [
        {"path": "logs/iter-19/runtime/runtime-summary.json", "type": "summary", "tc": "TC-F05", "signal": "runtime_summary_present"}
    ]
    saved = json.loads((task / "evaluation.json").read_text())
    assert saved["evidenceIndex"] == evaluation["evidenceIndex"]


def test_matrix_carries_forward_prior_real_fail_instead_of_not_run(tmp_path: Path) -> None:
    """P0-5: a real prior fail in tc-attempts must not be diluted to not_run when
    no fresh evidence maps to that TC this pass."""
    task = tmp_path / "task01"
    _write_contract(task)
    # Prior ledger says TC-F06 genuinely failed at iter-16.
    (task / "tc-attempts.json").write_text(json.dumps({
        "schema": "automind.tc_attempts.v1",
        "attempts": {
            "TC-F06": [{
                "iteration": 16,
                "result": "fail",
                "source": "evaluator",
                "evidence": ["logs/iter-16/runtime/remote-summary.json"],
                "missingSignals": ["stop_reason=remote_control"],
                "summary": "remote control did not stop playback",
            }],
        },
    }))
    # Fresh evidence only for TC-F05.
    iter_dir = task / "logs" / "iter-17" / "play-fail"
    iter_dir.mkdir(parents=True)
    (iter_dir / "play-fail-summary.json").write_text(json.dumps({
        "scenario": "TC-F05-play-fail-final-apk",
        "rows": [{"event": "music_audio_stop", "stop_reason": "play_fail"}],
    }))

    matrix = build_tc_evidence_matrix(task)
    rows = {row["testCaseId"]: row for row in matrix["testResults"]}

    assert rows["TC-F06"]["result"] == "fail"
    assert "tc-attempts-carry-forward" in rows["TC-F06"]["sources"]
    assert rows["TC-F06"]["evidence"] == ["logs/iter-16/runtime/remote-summary.json"]
    # TC-F05 still synthesized fresh from this pass.
    assert rows["TC-F05"]["candidateResult"] == "pass_candidate"


def test_matrix_ignores_unknown_prior_attempt(tmp_path: Path) -> None:
    """Exploratory/unknown ledger attempts must not be carried forward as a real
    verdict; the TC stays not_run."""
    task = tmp_path / "task01"
    _write_contract(task)
    (task / "tc-attempts.json").write_text(json.dumps({
        "schema": "automind.tc_attempts.v1",
        "attempts": {
            "TC-F06": [{"iteration": 16, "result": "unknown", "source": "generator"}],
        },
    }))
    iter_dir = task / "logs" / "iter-17" / "play-fail"
    iter_dir.mkdir(parents=True)
    (iter_dir / "play-fail-summary.json").write_text(json.dumps({
        "scenario": "TC-F05-play-fail-final-apk",
        "rows": [{"event": "music_audio_stop", "stop_reason": "play_fail"}],
    }))

    matrix = build_tc_evidence_matrix(task)
    rows = {row["testCaseId"]: row for row in matrix["testResults"]}
    assert rows["TC-F06"]["result"] == "not_run"
    assert rows["TC-F06"]["candidateResult"] == "missing_evidence"


def test_synthesize_preserves_prior_ask_user_classification(tmp_path: Path) -> None:
    """P0-5: synthesis must not downgrade a real ask_user/fail evaluation to the
    generic retry_generator/validation_failure placeholder."""
    task = tmp_path / "task01"
    _write_contract(task)
    (task / "evaluation.json").write_text(json.dumps({
        "iteration": 8,
        "result": "fail",
        "summary": "repeated build failure",
        "failedChecks": [{
            "name": "repeated_same_failure",
            "reason": "ExampleUIMacros libtool fails every retry",
            "category": "repeated_same_failure",
        }],
        "nextAction": "ask_user",
    }))
    iter_dir = task / "logs" / "iter-17" / "runtime"
    iter_dir.mkdir(parents=True)
    (iter_dir / "remote-summary.json").write_text(json.dumps({
        "scenario": "TC-F06-remote-control-final-apk",
        "rows": [{"event": "music_audio_stop", "stop_reason": "remote_control"}],
    }))

    evaluation = synthesize_evaluation_from_evidence(task, reason="test")

    assert evaluation is not None
    # Real prior decision preserved, not diluted.
    assert evaluation["nextAction"] == "ask_user"
    assert evaluation["result"] == "fail"
    names = {c.get("name") for c in evaluation["failedChecks"]}
    assert "repeated_same_failure" in names
    # Synthesis still annotates that final judgment is pending.
    assert "tc_evidence_synthesis_pending_final_judgment" in names

