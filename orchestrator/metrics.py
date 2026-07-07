"""Metrics collection for AutoMind task execution.

This module provides a lightweight, structured metrics system to track:
- Phase durations (Planning, Generator, Evaluator, Summary)
- Per-iteration phase breakdowns (iter-N generator / evaluator durations)
- Sub-phase timings (build, install, ui_execution, completion_gate, etc.)
- Agent call durations and retry counts
- LLM token consumption
- Build/compilation times
- Cache hit/miss statistics
- Iteration counts and retry behavior
- Resource usage (CPU, memory)

Metrics are stored in a standalone ``metrics.json`` file under the task directory.
``runtime-state.json`` only keeps a ``metricsRef`` pointer to keep the core state
lean and separate from observability data.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from orchestrator.state import read_runtime_state, update_runtime_state

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    psutil = None


# Standard sub-phase names used across platforms. Platforms may record a subset;
# missing entries simply do not appear in aggregates.
SUBPHASE_BUILD = "build"
SUBPHASE_INSTALL = "install"
SUBPHASE_UI_EXECUTION = "ui_execution"
SUBPHASE_RESULT_ANALYSIS = "result_analysis"
SUBPHASE_COMPLETION_GATE = "completion_gate"
SUBPHASE_PREFLIGHT = "preflight"
SUBPHASE_FLOW_GENERATION = "flow_generation"
SUBPHASE_WARM_BUILD_WAIT = "warm_build_wait"


class MetricsCollector:
    """Collect and manage metrics for a single AutoMind task."""

    def __init__(self, task_dir: Path):
        self.task_dir = task_dir
        self._timers: dict[str, float] = {}
        self._metrics: dict[str, dict] = {}
        self._start_time = time.time()
        self._iterations: list[dict] = []
        self._current_iter: int | None = None

    def start_timer(self, name: str) -> None:
        """Start a timer with the given name."""
        self._timers[name] = time.time()

    def stop_timer(self, name: str) -> float | None:
        """Stop the timer and record the duration. Returns the duration in seconds."""
        if name not in self._timers:
            return None
        duration = time.time() - self._timers.pop(name)
        self._record_timing(name, duration)
        return duration

    def record_metric(self, name: str, value: int | float | str, unit: str = "") -> None:
        """Record a simple metric with optional unit."""
        if name not in self._metrics:
            self._metrics[name] = {"values": [], "unit": unit}
        self._metrics[name]["values"].append(value)
        if unit:
            self._metrics[name]["unit"] = unit

    def record_llm_tokens(self, prompt_tokens: int, completion_tokens: int, model: str = "") -> None:
        """Record LLM token consumption."""
        self.record_metric("llm_prompt_tokens", prompt_tokens, "tokens")
        self.record_metric("llm_completion_tokens", completion_tokens, "tokens")
        self.record_metric("llm_total_tokens", prompt_tokens + completion_tokens, "tokens")
        if model:
            self.record_metric("llm_model", model)

    def record_phase_duration(self, phase: str, duration: float) -> None:
        """Record a phase duration explicitly."""
        self._record_timing(f"phase_{phase.lower()}_duration", duration)

    def record_iteration(self, iteration: int) -> None:
        """Record an iteration number."""
        self.record_metric("iteration", iteration)
        self._current_iter = iteration
        while len(self._iterations) <= iteration:
            self._iterations.append({})
        self._iterations[iteration]["iteration"] = iteration

    def record_iter_phase_duration(self, iteration: int, phase: str, duration: float) -> None:
        """Record a phase duration for a specific iteration."""
        while len(self._iterations) <= iteration:
            self._iterations.append({})
        self._iterations[iteration]["iteration"] = iteration
        self._iterations[iteration][f"{phase}_duration"] = round(duration, 3)

    def record_subphase(self, subphase: str, duration: float,
                        platform: str = "", iteration: int | None = None) -> None:
        """Record a sub-phase duration (build, install, ui_execution, etc.)."""
        key = f"subphase_{subphase}_duration"
        self._record_timing(key, duration)
        if platform:
            self.record_metric(f"subphase_{subphase}_platform", platform)
        if iteration is not None:
            while len(self._iterations) <= iteration:
                self._iterations.append({})
            self._iterations[iteration]["iteration"] = iteration
            self._iterations[iteration][f"subphase_{subphase}_duration"] = round(duration, 3)

    def record_agent_call(self, phase: str, duration: float,
                          retries: int = 0, exit_code: int = 0) -> None:
        """Record an agent call duration and retry count for a given phase."""
        key = f"agent_{phase.lower()}_duration"
        self._record_timing(key, duration)
        self.record_metric(f"agent_{phase.lower()}_retries", retries)
        if exit_code != 0:
            self.record_metric(f"agent_{phase.lower()}_failures", 1)

    def record_cache_hit(self, cache_type: str, tc_id: str) -> None:
        """Record a cache hit."""
        self.record_metric(f"cache_{cache_type}_hit", 1)

    def record_cache_miss(self, cache_type: str, tc_id: str) -> None:
        """Record a cache miss."""
        self.record_metric(f"cache_{cache_type}_miss", 1)

    def record_warm_build(self, duration: float, status: str, platform: str) -> None:
        """Record warm build metrics."""
        self._record_timing("warm_build_duration", duration)
        self.record_metric("warm_build_status", status)
        self.record_metric("warm_build_platform", platform)

    def record_resource_usage(self) -> None:
        """Record current resource usage (CPU, memory)."""
        if psutil is None:
            return
        try:
            process = psutil.Process()
            memory_mb = process.memory_info().rss / (1024 * 1024)
            cpu_percent = process.cpu_percent()
            self.record_metric("cpu_usage", cpu_percent, "%")
            self.record_metric("memory_usage", round(memory_mb, 2), "MB")
        except Exception:
            pass

    def _record_timing(self, name: str, duration: float) -> None:
        """Internal: Record a timing metric."""
        if name not in self._metrics:
            self._metrics[name] = {"values": [], "unit": "seconds"}
        self._metrics[name]["values"].append(duration)

    def compute_aggregates(self) -> dict:
        """Compute aggregate statistics for all metrics."""
        result: dict = {}
        for name, data in self._metrics.items():
            values = data.get("values", [])
            unit = data.get("unit", "")
            if not values:
                continue

            if isinstance(values[0], (int, float)):
                numeric = [float(v) for v in values]
                result[name] = {
                    "min": round(min(numeric), 3),
                    "max": round(max(numeric), 3),
                    "avg": round(sum(numeric) / len(numeric), 3),
                    "sum": round(sum(numeric), 3),
                    "count": len(numeric),
                    "unit": unit,
                }
            else:
                result[name] = {
                    "last": values[-1],
                    "count": len(values),
                    "unit": unit,
                }
        return result

    def to_dict(self) -> dict:
        """Export the full metrics structure as a dict."""
        aggregates = self.compute_aggregates()
        iterations = [it for it in self._iterations if it]  # skip empty placeholders
        metrics_data = {
            "collectedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "taskDuration": round(time.time() - self._start_time, 2),
            "aggregates": aggregates,
        }
        if iterations:
            metrics_data["iterations"] = iterations
            metrics_data["iterationCount"] = len(iterations)
        return metrics_data

    def flush(self) -> None:
        """Write all metrics to metrics.json and keep a reference in runtime-state.json."""
        data = self.to_dict()
        self.task_dir.mkdir(parents=True, exist_ok=True)
        path = self.task_dir / "metrics.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        update_runtime_state(self.task_dir, metricsRef="metrics.json")


def metrics_path(task_dir: Path) -> Path:
    """Return the path to metrics.json."""
    return task_dir / "metrics.json"


_metrics_instances: dict[str, MetricsCollector] = {}


def get_metrics(task_dir: Path) -> MetricsCollector:
    """Get or create a MetricsCollector for the given task directory."""
    key = str(task_dir)
    if key not in _metrics_instances:
        _metrics_instances[key] = MetricsCollector(task_dir)
    return _metrics_instances[key]


def read_metrics(task_dir: Path) -> dict:
    """Read metrics from metrics.json.

    Falls back to runtime-state.json.metrics for backward compatibility with
    tasks created before the split.
    """
    path = metrics_path(task_dir)
    if path.exists():
        try:
            data = json.loads(path.read_text(errors="ignore"))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    state = read_runtime_state(task_dir) or {}
    metrics = state.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def record_phase_start(task_dir: Path, phase: str) -> None:
    """Record the start of a phase."""
    get_metrics(task_dir).start_timer(f"phase_{phase.lower()}")


def record_phase_end(task_dir: Path, phase: str) -> None:
    """Record the end of a phase and compute duration."""
    collector = get_metrics(task_dir)
    duration = collector.stop_timer(f"phase_{phase.lower()}")
    if duration is not None:
        collector.record_phase_duration(phase, duration)


def record_iter_phase_start(task_dir: Path, iteration: int, phase: str) -> None:
    """Record the start of a phase within a specific iteration."""
    get_metrics(task_dir).start_timer(f"iter_{iteration}_phase_{phase.lower()}")


def record_iter_phase_end(task_dir: Path, iteration: int, phase: str) -> None:
    """Record the end of a phase within a specific iteration."""
    collector = get_metrics(task_dir)
    key = f"iter_{iteration}_phase_{phase.lower()}"
    duration = collector.stop_timer(key)
    if duration is not None:
        collector.record_iter_phase_duration(iteration, phase, duration)


def record_subphase_start(task_dir: Path, subphase: str,
                          platform: str = "", iteration: int | None = None) -> None:
    """Start timing a sub-phase (build, install, ui_execution, etc.)."""
    collector = get_metrics(task_dir)
    key = _subphase_timer_key(subphase, platform, iteration)
    collector.start_timer(key)


def record_subphase_end(task_dir: Path, subphase: str,
                        platform: str = "", iteration: int | None = None) -> float | None:
    """End timing a sub-phase and record the duration."""
    collector = get_metrics(task_dir)
    key = _subphase_timer_key(subphase, platform, iteration)
    duration = collector.stop_timer(key)
    if duration is not None:
        collector.record_subphase(subphase, duration, platform=platform, iteration=iteration)
    return duration


def _subphase_timer_key(subphase: str, platform: str, iteration: int | None) -> str:
    parts = ["subphase", subphase]
    if platform:
        parts.append(platform)
    if iteration is not None:
        parts.append(f"iter{iteration}")
    return "_".join(parts)


def record_agent_call(task_dir: Path, phase: str, duration: float,
                      retries: int = 0, exit_code: int = 0) -> None:
    """Record an agent call for a given phase."""
    get_metrics(task_dir).record_agent_call(phase, duration, retries=retries, exit_code=exit_code)


def record_iteration(task_dir: Path, iteration: int) -> None:
    """Record the current iteration number."""
    get_metrics(task_dir).record_iteration(iteration)


def record_llm_usage(task_dir: Path, prompt_tokens: int, completion_tokens: int, model: str = "") -> None:
    """Record LLM token usage."""
    get_metrics(task_dir).record_llm_tokens(prompt_tokens, completion_tokens, model)


def flush_metrics(task_dir: Path) -> None:
    """Flush all metrics to runtime-state.json."""
    get_metrics(task_dir).flush()
