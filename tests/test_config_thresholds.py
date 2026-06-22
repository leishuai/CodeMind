"""TC-R04: 阈值常量测试。

AC-008: 默认 MAX_ITERATIONS=1000、MAX_REFLECTIONS_PER_TC=10。
AC-009: env AUTOMIND_MAX_REFLECTIONS_PER_TC=3 后 import 后断言 == 3。
"""
from __future__ import annotations

import importlib
import os


def _reload_config():
    import orchestrator.config as cfg
    return importlib.reload(cfg)


def test_default_max_iterations_is_1000(monkeypatch):
    monkeypatch.delenv("AUTOMIND_MAX_ITERATIONS", raising=False)
    cfg = _reload_config()
    assert cfg.MAX_ITERATIONS == 1000, (
        f"Default MAX_ITERATIONS expected 1000, got {cfg.MAX_ITERATIONS}"
    )


def test_default_max_reflections_per_tc_is_10(monkeypatch):
    monkeypatch.delenv("AUTOMIND_MAX_REFLECTIONS_PER_TC", raising=False)
    cfg = _reload_config()
    assert hasattr(cfg, "MAX_REFLECTIONS_PER_TC"), (
        "config.py must export MAX_REFLECTIONS_PER_TC"
    )
    assert cfg.MAX_REFLECTIONS_PER_TC == 10, (
        f"Default MAX_REFLECTIONS_PER_TC expected 10, got {cfg.MAX_REFLECTIONS_PER_TC}"
    )


def test_env_override_max_reflections_per_tc(monkeypatch):
    monkeypatch.setenv("AUTOMIND_MAX_REFLECTIONS_PER_TC", "3")
    cfg = _reload_config()
    assert cfg.MAX_REFLECTIONS_PER_TC == 3


def test_env_override_max_iterations(monkeypatch):
    monkeypatch.setenv("AUTOMIND_MAX_ITERATIONS", "42")
    cfg = _reload_config()
    assert cfg.MAX_ITERATIONS == 42
