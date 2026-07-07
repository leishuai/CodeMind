"""Tests for ui_path_cache module."""
from pathlib import Path

import pytest

from orchestrator.ui_path_cache import (
    cache_ui_path,
    compute_ui_fingerprint,
    expire_cached_ui_paths,
    get_cached_ui_path,
    get_ui_path_cache_status,
    is_ui_path_cache_valid,
    mark_ui_path_expired,
    read_ui_path_cache,
    wait_for_ui_exploration,
    write_ui_path_cache,
)
from orchestrator.state import write_runtime_state


def test_ui_path_cache_status_initial_empty(tmp_path: Path) -> None:
    """Test UI path cache status is empty initially."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    
    status = get_ui_path_cache_status(task_dir)
    assert status == {}


def test_ui_path_cache_status_persisted_in_runtime_state(tmp_path: Path) -> None:
    """Test UI path cache status is persisted in runtime-state.json."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    
    write_runtime_state(task_dir, {
        "status": "ready",
        "nextAction": "run_generator",
        "uiPathCache": {"status": "completed", "platform": "ios", "exploredPaths": 3},
    })
    
    status = get_ui_path_cache_status(task_dir)
    assert status["status"] == "completed"
    assert status["platform"] == "ios"
    assert status["exploredPaths"] == 3


def test_read_ui_path_cache_empty(tmp_path: Path) -> None:
    """Test reading empty UI path cache."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    
    cache = read_ui_path_cache(task_dir)
    assert cache == {}


def test_write_and_read_ui_path_cache(tmp_path: Path) -> None:
    """Test writing and reading UI path cache."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    
    cache = {
        "TC-01": {
            "tcId": "TC-01",
            "goal": "play audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "playButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "valid",
        }
    }
    
    write_ui_path_cache(task_dir, cache)
    read_cache = read_ui_path_cache(task_dir)
    
    assert read_cache["TC-01"]["goal"] == "play audio"
    assert read_cache["TC-01"]["uiFingerprint"] == "abc123"


def test_compute_ui_fingerprint_from_hierarchy(tmp_path: Path) -> None:
    """Test computing UI fingerprint from screen hierarchy."""
    hierarchy = "<root><view id='main'><button id='play'>Play</button></view></root>"
    fingerprint = compute_ui_fingerprint(tmp_path / "task", hierarchy)
    
    assert isinstance(fingerprint, str)
    assert len(fingerprint) == 16
    assert fingerprint == compute_ui_fingerprint(tmp_path / "task", hierarchy)


def test_compute_ui_fingerprint_consistency(tmp_path: Path) -> None:
    """Test UI fingerprint consistency for same input."""
    hierarchy1 = "<root><view id='main'><button id='play'>Play</button></view></root>"
    hierarchy2 = "<root><view id='main'><button id='play'>Play</button></view></root>"
    
    fp1 = compute_ui_fingerprint(tmp_path / "task", hierarchy1)
    fp2 = compute_ui_fingerprint(tmp_path / "task", hierarchy2)
    
    assert fp1 == fp2


def test_compute_ui_fingerprint_different_for_different_hierarchy(tmp_path: Path) -> None:
    """Test UI fingerprint is different for different hierarchies."""
    hierarchy1 = "<root><view id='main'><button id='play'>Play</button></view></root>"
    hierarchy2 = "<root><view id='main'><button id='stop'>Stop</button></view></root>"
    
    fp1 = compute_ui_fingerprint(tmp_path / "task", hierarchy1)
    fp2 = compute_ui_fingerprint(tmp_path / "task", hierarchy2)
    
    assert fp1 != fp2


def test_is_ui_path_cache_valid_with_valid_cache(tmp_path: Path) -> None:
    """Test valid cache is detected as valid."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    
    cache = {
        "TC-01": {
            "tcId": "TC-01",
            "goal": "play audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "playButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "valid",
        }
    }
    
    write_ui_path_cache(task_dir, cache)
    
    is_valid, reason = is_ui_path_cache_valid(task_dir, "TC-01", "abc123")
    assert is_valid
    assert reason == "valid"


def test_is_ui_path_cache_invalid_when_fingerprint_changed(tmp_path: Path) -> None:
    """Test cache is invalid when UI fingerprint changed."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    
    cache = {
        "TC-01": {
            "tcId": "TC-01",
            "goal": "play audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "playButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "valid",
        }
    }
    
    write_ui_path_cache(task_dir, cache)
    
    is_valid, reason = is_ui_path_cache_valid(task_dir, "TC-01", "def456")
    assert not is_valid
    assert "UI fingerprint changed" in reason


def test_is_ui_path_cache_invalid_when_not_found(tmp_path: Path) -> None:
    """Test cache is invalid when TC not found."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    
    cache = {
        "TC-01": {
            "tcId": "TC-01",
            "goal": "play audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "playButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "valid",
        }
    }
    
    write_ui_path_cache(task_dir, cache)
    
    is_valid, reason = is_ui_path_cache_valid(task_dir, "TC-02", "abc123")
    assert not is_valid
    assert "no cached path for this TC" in reason


def test_get_cached_ui_path_returns_none_when_invalid(tmp_path: Path) -> None:
    """Test get_cached_ui_path returns None when cache is invalid."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    
    cache = {
        "TC-01": {
            "tcId": "TC-01",
            "goal": "play audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "playButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "valid",
        }
    }
    
    write_ui_path_cache(task_dir, cache)
    
    result = get_cached_ui_path(task_dir, "TC-01", "def456")
    assert result is None


def test_get_cached_ui_path_returns_entry_when_valid(tmp_path: Path) -> None:
    """Test get_cached_ui_path returns entry when cache is valid."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    
    cache = {
        "TC-01": {
            "tcId": "TC-01",
            "goal": "play audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "playButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "valid",
        }
    }
    
    write_ui_path_cache(task_dir, cache)
    
    result = get_cached_ui_path(task_dir, "TC-01", "abc123")
    assert result is not None
    assert result["goal"] == "play audio"


def test_cache_ui_path_stores_entry(tmp_path: Path) -> None:
    """Test cache_ui_path stores a new entry."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    
    action_sequence = [{"type": "click", "selector": "playButton"}, {"type": "wait", "condition": "audioStarted"}]
    cache_ui_path(task_dir, "TC-01", "play audio", action_sequence, "abc123")
    
    cache = read_ui_path_cache(task_dir)
    assert "TC-01" in cache
    assert cache["TC-01"]["goal"] == "play audio"
    assert cache["TC-01"]["actionSequence"] == action_sequence


def test_mark_ui_path_expired_updates_status(tmp_path: Path) -> None:
    """Test mark_ui_path_expired updates validity to expired."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    cache = {
        "TC-01": {
            "tcId": "TC-01",
            "goal": "play audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "playButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "valid",
        }
    }

    write_ui_path_cache(task_dir, cache)
    mark_ui_path_expired(task_dir, "TC-01")

    cache = read_ui_path_cache(task_dir)
    assert cache["TC-01"]["validity"] == "expired"


def test_mark_ui_path_expired_stores_reason(tmp_path: Path) -> None:
    """Test mark_ui_path_expired stores the reason and timestamp."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    cache = {
        "TC-01": {
            "tcId": "TC-01",
            "goal": "play audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "playButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "valid",
        }
    }

    write_ui_path_cache(task_dir, cache)
    mark_ui_path_expired(task_dir, "TC-01", reason="execution_failed")

    cache = read_ui_path_cache(task_dir)
    assert cache["TC-01"]["validity"] == "expired"
    assert cache["TC-01"]["expiredReason"] == "execution_failed"
    assert "expiredAt" in cache["TC-01"]


def test_is_ui_path_cache_invalid_when_expired(tmp_path: Path) -> None:
    """Test cache with validity=expired is considered invalid."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    cache = {
        "TC-01": {
            "tcId": "TC-01",
            "goal": "play audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "playButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "expired",
        }
    }

    write_ui_path_cache(task_dir, cache)

    is_valid, reason = is_ui_path_cache_valid(task_dir, "TC-01", "abc123")
    assert not is_valid
    assert "expired" in reason.lower()


def test_expire_cached_ui_paths_bulk(tmp_path: Path) -> None:
    """Test expire_cached_ui_paths marks multiple entries as expired."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    cache = {
        "TC-01": {
            "tcId": "TC-01",
            "goal": "play audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "playButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "valid",
        },
        "TC-02": {
            "tcId": "TC-02",
            "goal": "stop audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "stopButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "valid",
        },
        "TC-03": {
            "tcId": "TC-03",
            "goal": "skip track",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "nextButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "valid",
        },
    }

    write_ui_path_cache(task_dir, cache)
    expired_count = expire_cached_ui_paths(task_dir, ["TC-01", "TC-03"], reason="test_bulk_expire")

    assert expired_count == 2
    cache = read_ui_path_cache(task_dir)
    assert cache["TC-01"]["validity"] == "expired"
    assert cache["TC-02"]["validity"] == "valid"
    assert cache["TC-03"]["validity"] == "expired"


def test_expire_cached_ui_paths_skips_already_expired(tmp_path: Path) -> None:
    """Test expire_cached_ui_paths skips entries already expired."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    cache = {
        "TC-01": {
            "tcId": "TC-01",
            "goal": "play audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "playButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "expired",
        },
        "TC-02": {
            "tcId": "TC-02",
            "goal": "stop audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "stopButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "valid",
        },
    }

    write_ui_path_cache(task_dir, cache)
    expired_count = expire_cached_ui_paths(task_dir, ["TC-01", "TC-02"], reason="test")

    assert expired_count == 1


def test_cache_ui_path_records_audit(tmp_path: Path) -> None:
    """Test cache_ui_path writes an audit entry."""
    from orchestrator.audit import read_audit_log

    task_dir = tmp_path / "task"
    task_dir.mkdir()

    action_sequence = [{"type": "click", "selector": "playButton"}]
    cache_ui_path(task_dir, "TC-01", "play audio", action_sequence, "abc123")

    entries = read_audit_log(task_dir)
    action_entries = [e for e in entries if e.get("type") == "action_executed"]
    cache_writes = [
        e for e in action_entries
        if e.get("details", {}).get("actionType") == "ui_path_cache_write"
    ]
    assert len(cache_writes) >= 1
    assert cache_writes[0]["details"]["target"] == "TC-01"


def test_get_cached_ui_path_records_audit_hit(tmp_path: Path) -> None:
    """Test get_cached_ui_path records audit branch_taken on hit."""
    from orchestrator.audit import read_audit_log

    task_dir = tmp_path / "task"
    task_dir.mkdir()

    cache = {
        "TC-01": {
            "tcId": "TC-01",
            "goal": "play audio",
            "uiFingerprint": "abc123",
            "actionSequence": [{"type": "click", "selector": "playButton"}],
            "timestamp": "2026-07-06T10:00:00",
            "validity": "valid",
        }
    }
    write_ui_path_cache(task_dir, cache)

    result = get_cached_ui_path(task_dir, "TC-01", "abc123")
    assert result is not None

    entries = read_audit_log(task_dir)
    branch_entries = [e for e in entries if e.get("type") == "branch_taken"]
    hits = [e for e in branch_entries if e.get("details", {}).get("outcome") == "hit"]
    assert len(hits) >= 1


def test_get_cached_ui_path_records_audit_miss(tmp_path: Path) -> None:
    """Test get_cached_ui_path records audit branch_taken on miss."""
    from orchestrator.audit import read_audit_log

    task_dir = tmp_path / "task"
    task_dir.mkdir()

    result = get_cached_ui_path(task_dir, "TC-99", "abc123")
    assert result is None

    entries = read_audit_log(task_dir)
    branch_entries = [e for e in entries if e.get("type") == "branch_taken"]
    misses = [e for e in branch_entries if e.get("details", {}).get("outcome") == "miss"]
    assert len(misses) >= 1


def test_wait_for_ui_exploration_returns_immediately_if_completed(tmp_path: Path) -> None:
    """Test wait_for_ui_exploration returns immediately if already completed."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    
    write_runtime_state(task_dir, {
        "status": "ready",
        "nextAction": "run_generator",
        "uiPathCache": {"status": "completed", "platform": "ios"},
    })
    
    status = wait_for_ui_exploration(task_dir, max_wait=1)
    assert status["status"] == "completed"


def test_wait_for_ui_exploration_timeout(tmp_path: Path) -> None:
    """Test wait_for_ui_exploration times out after max_wait and marks timed_out."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    
    write_runtime_state(task_dir, {
        "status": "ready",
        "nextAction": "run_generator",
        "uiPathCache": {"status": "running", "platform": "ios"},
    })
    
    status = wait_for_ui_exploration(task_dir, max_wait=1)
    assert status["status"] == "timed_out"
    assert status["platform"] == "ios"


def test_summary_promotes_cached_ui_path_to_reuse(tmp_path: Path) -> None:
    """Verified UI path cache entries surface as successful reuse records."""
    from orchestrator.summary import _collect_cached_ui_path_records

    task_dir = tmp_path / "task"
    task_dir.mkdir()

    cache_ui_path(
        task_dir,
        "TC-01",
        "play audio",
        [{"type": "click", "selector": "playButton"}],
        "abc123",
    )

    records = _collect_cached_ui_path_records(task_dir)
    assert len(records) == 1
    assert records[0]["scope"] == "UI path cache: TC-01"
    assert "TC-01" in records[0]["purpose"]


def test_summary_skips_expired_cached_ui_path(tmp_path: Path) -> None:
    """Expired cache entries must not be promoted into reuse records."""
    from orchestrator.summary import _collect_cached_ui_path_records

    task_dir = tmp_path / "task"
    task_dir.mkdir()

    cache_ui_path(
        task_dir,
        "TC-01",
        "play audio",
        [{"type": "click", "selector": "playButton"}],
        "abc123",
    )
    mark_ui_path_expired(task_dir, "TC-01")

    records = _collect_cached_ui_path_records(task_dir)
    assert records == []
