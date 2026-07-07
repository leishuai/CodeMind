"""Unit tests for metrics collection."""
from __future__ import annotations

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from orchestrator.metrics import (
    MetricsCollector,
    get_metrics,
    record_phase_start,
    record_phase_end,
    record_subphase_start,
    record_subphase_end,
    record_iter_phase_start,
    record_iter_phase_end,
    record_agent_call,
    record_iteration,
    flush_metrics,
    read_metrics,
    psutil,
    SUBPHASE_BUILD,
    SUBPHASE_INSTALL,
    SUBPHASE_UI_EXECUTION,
    SUBPHASE_PREFLIGHT,
    SUBPHASE_COMPLETION_GATE,
    SUBPHASE_FLOW_GENERATION,
    SUBPHASE_RESULT_ANALYSIS,
    SUBPHASE_WARM_BUILD_WAIT,
)
from orchestrator.state import read_runtime_state, update_runtime_state


class TestMetricsCollector:
    def test_start_and_stop_timer(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.start_timer("test_timer")
            time.sleep(0.01)
            duration = collector.stop_timer("test_timer")
            assert duration is not None
            assert duration >= 0.01
            assert duration < 0.5

    def test_stop_nonexistent_timer(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            result = collector.stop_timer("nonexistent")
            assert result is None

    def test_record_metric(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_metric("test_metric", 42, "units")
            collector.record_metric("test_metric", 48, "units")
            aggregates = collector.compute_aggregates()
            assert "test_metric" in aggregates
            assert aggregates["test_metric"]["sum"] == 90
            assert aggregates["test_metric"]["avg"] == 45
            assert aggregates["test_metric"]["count"] == 2
            assert aggregates["test_metric"]["unit"] == "units"

    def test_record_llm_tokens(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_llm_tokens(100, 200, "test-model")
            aggregates = collector.compute_aggregates()
            assert aggregates["llm_prompt_tokens"]["sum"] == 100
            assert aggregates["llm_completion_tokens"]["sum"] == 200
            assert aggregates["llm_total_tokens"]["sum"] == 300

    def test_record_phase_duration(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_phase_duration("planning", 15.5)
            aggregates = collector.compute_aggregates()
            assert aggregates["phase_planning_duration"]["sum"] == 15.5

    def test_record_cache_hit_miss(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_cache_hit("ui_path", "TC-001")
            collector.record_cache_miss("ui_path", "TC-002")
            collector.record_cache_hit("ui_path", "TC-003")
            aggregates = collector.compute_aggregates()
            assert aggregates["cache_ui_path_hit"]["sum"] == 2
            assert aggregates["cache_ui_path_miss"]["sum"] == 1

    def test_record_warm_build(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_warm_build(45.2, "completed", "ios")
            aggregates = collector.compute_aggregates()
            assert aggregates["warm_build_duration"]["sum"] == 45.2

    def test_record_resource_usage(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_resource_usage()
            aggregates = collector.compute_aggregates()
            if psutil is None:
                assert aggregates == {}
            else:
                assert "cpu_usage" in aggregates or "memory_usage" in aggregates

    def test_record_subphase(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_subphase(SUBPHASE_BUILD, 30.5, platform="android", iteration=1)
            collector.record_subphase(SUBPHASE_UI_EXECUTION, 15.2, platform="android", iteration=1)
            aggregates = collector.compute_aggregates()
            assert aggregates[f"subphase_{SUBPHASE_BUILD}_duration"]["sum"] == 30.5
            assert aggregates[f"subphase_{SUBPHASE_UI_EXECUTION}_duration"]["sum"] == 15.2

    def test_record_subphase_without_platform(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_subphase(SUBPHASE_BUILD, 10.0)
            aggregates = collector.compute_aggregates()
            assert aggregates[f"subphase_{SUBPHASE_BUILD}_duration"]["sum"] == 10.0

    def test_record_iteration(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_iteration(1)
            collector.record_iteration(2)
            data = collector.to_dict()
            assert "iterations" in data
            assert data["iterationCount"] == 2
            assert data["iterations"][0]["iteration"] == 1
            assert data["iterations"][1]["iteration"] == 2

    def test_record_iter_phase_duration(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_iter_phase_duration(1, "generator", 45.0)
            collector.record_iter_phase_duration(1, "evaluator", 30.0)
            collector.record_iter_phase_duration(2, "generator", 20.0)
            data = collector.to_dict()
            assert data["iterations"][0]["generator_duration"] == 45.0
            assert data["iterations"][0]["evaluator_duration"] == 30.0
            assert data["iterations"][1]["generator_duration"] == 20.0

    def test_record_subphase_in_iteration(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_subphase(SUBPHASE_BUILD, 25.0, platform="ios", iteration=1)
            collector.record_subphase(SUBPHASE_UI_EXECUTION, 10.0, platform="ios", iteration=1)
            data = collector.to_dict()
            assert data["iterations"][0][f"subphase_{SUBPHASE_BUILD}_duration"] == 25.0
            assert data["iterations"][0][f"subphase_{SUBPHASE_UI_EXECUTION}_duration"] == 10.0

    def test_record_agent_call(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_agent_call("generator", 120.5, retries=1, exit_code=0)
            collector.record_agent_call("evaluator", 60.0, retries=0, exit_code=0)
            collector.record_agent_call("generator", 30.0, retries=0, exit_code=1)
            aggregates = collector.compute_aggregates()
            assert aggregates["agent_generator_duration"]["sum"] == 150.5
            assert aggregates["agent_generator_duration"]["count"] == 2
            assert aggregates["agent_evaluator_duration"]["sum"] == 60.0
            assert aggregates["agent_generator_retries"]["sum"] == 1
            assert aggregates["agent_generator_failures"]["sum"] == 1

    def test_to_dict_structure(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_iteration(1)
            collector.record_iter_phase_duration(1, "generator", 10.0)
            collector.record_subphase(SUBPHASE_BUILD, 5.0, platform="android", iteration=1)
            collector.record_agent_call("generator", 8.0, retries=0, exit_code=0)
            data = collector.to_dict()
            assert "collectedAt" in data
            assert "taskDuration" in data
            assert "aggregates" in data
            assert "iterations" in data
            assert "iterationCount" in data
            assert data["iterationCount"] == 1

    def test_to_dict_no_iterations(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_phase_duration("planning", 5.0)
            data = collector.to_dict()
            assert "iterations" not in data
            assert "iterationCount" not in data

    def test_subphase_constants(self):
        assert SUBPHASE_BUILD == "build"
        assert SUBPHASE_INSTALL == "install"
        assert SUBPHASE_UI_EXECUTION == "ui_execution"
        assert SUBPHASE_PREFLIGHT == "preflight"
        assert SUBPHASE_COMPLETION_GATE == "completion_gate"
        assert SUBPHASE_FLOW_GENERATION == "flow_generation"
        assert SUBPHASE_RESULT_ANALYSIS == "result_analysis"
        assert SUBPHASE_WARM_BUILD_WAIT == "warm_build_wait"

    def test_flush_metrics(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector = MetricsCollector(task_dir)
            collector.record_phase_duration("planning", 10.0)
            collector.flush()
            metrics_path = task_dir / "metrics.json"
            assert metrics_path.exists()
            data = json.loads(metrics_path.read_text())
            assert data["taskDuration"] >= 0
            assert "aggregates" in data
            assert data["aggregates"]["phase_planning_duration"]["sum"] == 10.0
            state = read_runtime_state(task_dir)
            assert state.get("metricsRef") == "metrics.json"


class TestMetricsModule:
    def test_get_metrics_singleton(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            collector1 = get_metrics(task_dir)
            collector2 = get_metrics(task_dir)
            assert collector1 is collector2

    def test_record_phase_start_end(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            record_phase_start(task_dir, "test")
            time.sleep(0.01)
            record_phase_end(task_dir, "test")
            aggregates = get_metrics(task_dir).compute_aggregates()
            assert aggregates["phase_test_duration"]["sum"] >= 0.01

    def test_flush_metrics_to_state(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            record_phase_start(task_dir, "test")
            time.sleep(0.01)
            record_phase_end(task_dir, "test")
            flush_metrics(task_dir)
            metrics = read_metrics(task_dir)
            assert "taskDuration" in metrics
            assert "aggregates" in metrics

    def test_read_empty_metrics(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            metrics = read_metrics(task_dir)
            assert metrics == {}

    def test_record_subphase_start_end(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            record_subphase_start(task_dir, SUBPHASE_BUILD, platform="android", iteration=1)
            time.sleep(0.01)
            duration = record_subphase_end(task_dir, SUBPHASE_BUILD, platform="android", iteration=1)
            assert duration is not None
            assert duration >= 0.01
            aggregates = get_metrics(task_dir).compute_aggregates()
            assert f"subphase_{SUBPHASE_BUILD}_duration" in aggregates

    def test_record_iter_phase_start_end(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            record_iter_phase_start(task_dir, 1, "generator")
            time.sleep(0.01)
            record_iter_phase_end(task_dir, 1, "generator")
            data = get_metrics(task_dir).to_dict()
            assert "iterations" in data
            assert data["iterations"][0]["generator_duration"] >= 0.01

    def test_record_agent_call_module(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            record_agent_call(task_dir, "planner", 25.0, retries=0, exit_code=0)
            aggregates = get_metrics(task_dir).compute_aggregates()
            assert aggregates["agent_planner_duration"]["sum"] == 25.0

    def test_record_iteration_module(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            record_iteration(task_dir, 3)
            data = get_metrics(task_dir).to_dict()
            assert data["iterationCount"] == 1
            assert data["iterations"][0]["iteration"] == 3

    def test_flush_includes_iterations(self):
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            record_iteration(task_dir, 1)
            record_iter_phase_start(task_dir, 1, "generator")
            time.sleep(0.01)
            record_iter_phase_end(task_dir, 1, "generator")
            record_subphase_start(task_dir, SUBPHASE_UI_EXECUTION, platform="ios", iteration=1)
            time.sleep(0.01)
            record_subphase_end(task_dir, SUBPHASE_UI_EXECUTION, platform="ios", iteration=1)
            flush_metrics(task_dir)
            metrics = read_metrics(task_dir)
            assert "iterations" in metrics
            assert metrics["iterationCount"] == 1
            assert "generator_duration" in metrics["iterations"][0]
            assert f"subphase_{SUBPHASE_UI_EXECUTION}_duration" in metrics["iterations"][0]
