from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from orchestrator.audit import read_audit_log
from orchestrator.metrics import read_metrics
from orchestrator.metrics import flush_metrics, get_metrics
from orchestrator.observability import (
    ObservationValidationError,
    ingest_observation,
    validate_observation_payload,
)
from orchestrator.state import write_runtime_state


def _task(tmp_path: Path) -> Path:
    task_dir = tmp_path / "chat01"
    task_dir.mkdir()
    write_runtime_state(task_dir, {"taskId": "chat01", "status": "chat"})
    return task_dir


def _payload(value: int = 1) -> dict:
    return {
        "source": "lark-bridge",
        "audit": [
            {
                "type": "policy_evaluation",
                "phase": "conversation",
                "decisionType": "capability",
                "action": "allow",
                "reasonCode": "task_owned_by_conversation",
                "details": {
                    "entrypoint": "lark",
                    "capability": "task.inspect",
                    "decision": "allow",
                    "taskBound": True,
                },
            },
            {
                "type": "action_executed",
                "phase": "conversation",
                "details": {
                    "actionType": "task.inspect",
                    "target": "conversation_task",
                    "result": "completed",
                },
            },
        ],
        "metrics": [
            {"name": "capability_action_count", "value": value, "unit": "count"},
            {"name": "capability_total_duration", "value": 0.5, "unit": "seconds"},
        ],
    }


def test_ingest_observation_reuses_audit_and_metrics_files(tmp_path: Path) -> None:
    task_dir = _task(tmp_path)
    result = ingest_observation(task_dir, _payload())
    assert result["auditCount"] == 2
    assert result["metricCount"] == 2
    assert (task_dir / "audit.jsonl").exists()
    assert (task_dir / "audit.json").exists()
    assert (task_dir / "metrics.json").exists()
    entries = read_audit_log(task_dir)
    assert [entry["type"] for entry in entries] == [
        "policy_evaluation",
        "action_executed",
    ]
    assert entries[0]["source"] == "lark-bridge"
    metrics = read_metrics(task_dir)["aggregates"]
    assert metrics["capability_action_count"]["sum"] == 1
    assert metrics["capability_total_duration"]["sum"] == 0.5


def test_metric_observations_merge_across_independent_batches(tmp_path: Path) -> None:
    task_dir = _task(tmp_path)
    ingest_observation(task_dir, _payload(1))
    ingest_observation(task_dir, _payload(2))
    metric = read_metrics(task_dir)["aggregates"]["capability_action_count"]
    assert metric["sum"] == 3
    assert metric["count"] == 2
    assert metric["avg"] == 1.5


def test_normal_metrics_flush_preserves_external_observations(tmp_path: Path) -> None:
    task_dir = _task(tmp_path)
    ingest_observation(task_dir, _payload())
    get_metrics(task_dir).record_metric("iteration", 1)
    flush_metrics(task_dir)
    aggregates = read_metrics(task_dir)["aggregates"]
    assert aggregates["capability_action_count"]["sum"] == 1
    assert aggregates["iteration"]["sum"] == 1


def test_concurrent_batches_do_not_lose_samples(tmp_path: Path) -> None:
    task_dir = _task(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: ingest_observation(task_dir, _payload()), range(8)))
    metric = read_metrics(task_dir)["aggregates"]["capability_action_count"]
    assert metric["sum"] == 8
    assert metric["count"] == 8
    assert len(read_audit_log(task_dir)) == 16


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(source="unknown"),
        lambda p: p["metrics"][0].update(name="arbitrary_metric"),
        lambda p: p["metrics"][0].update(value=-1),
        lambda p: p["audit"][0]["details"].update(prompt="do not store"),
        lambda p: p["audit"][0].update(type="arbitrary_event"),
    ],
)
def test_observation_rejects_unbounded_or_sensitive_payloads(mutation) -> None:
    payload = _payload()
    mutation(payload)
    with pytest.raises(ObservationValidationError):
        validate_observation_payload(payload)


def test_cmd_observe_writes_batch(tmp_path: Path, monkeypatch, capsys) -> None:
    import orchestrator.commands.session as session_cmd

    task_dir = _task(tmp_path)
    monkeypatch.setattr(session_cmd, "get_task_dir", lambda _code: task_dir)
    session_cmd.cmd_observe("chat01", ["--json", json.dumps(_payload())])
    output = json.loads(capsys.readouterr().out)
    assert output["result"] == "ok"
    assert output["auditCount"] == 2
    assert read_metrics(task_dir)["aggregates"]["capability_action_count"]["sum"] == 1
