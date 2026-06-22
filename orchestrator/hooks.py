"""AutoMind phase hook entrypoints.

Hooks are intentionally small and deterministic. They are not summary-specific:
callers can run them before/after any phase to prepare phase-local context,
record learnings, or add future policy checks.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from orchestrator.knowledge_index import compute_reuse_gate, write_phase_reuse_context
from orchestrator.phase_transition import refresh_phase_transition_summary
from orchestrator.state import ensure_dir, read_runtime_state, rel_to_root, update_runtime_state


def run_before_phase_hooks(task_dir: Path, phase: str, reason: str = "before_phase") -> dict[str, Any]:
    """Run deterministic before-phase hooks and return a compact result.

    Current handlers:
    - phase_reuse_lookup: writes ``phase-reuse/<phase>.md`` from knowledge index.
    - reuse_gate: computes the machine-checkable reuse acknowledgement gate so
      gated phases (Generator/Evaluator) cannot run until the agent records
      ``phaseReuseRead=true`` + ``reuseApplied[]``/``reuseIgnored[]`` via
      ``automind reuse-ack``. Entering a phase resets that phase's ack so each
      retry iteration must re-read matched reuse before acting.
    """
    phase_reuse = write_phase_reuse_context(task_dir, phase, reason=reason)
    matches = phase_reuse.get("matches") or []
    gate = compute_reuse_gate(task_dir, phase, matches=matches)
    gate["acknowledged"] = False
    gate["acknowledgement"] = None

    state = read_runtime_state(task_dir) or {}
    reuse_gate = state.get("reuseGate") if isinstance(state.get("reuseGate"), dict) else {}
    reuse_gate = dict(reuse_gate)
    reuse_gate[phase] = gate

    result = {
        "phase": phase,
        "hook": f"before:{phase}",
        "ranAt": datetime.now().isoformat(timespec="seconds"),
        "handlers": ["phase_reuse_lookup", "reuse_gate"],
        "phaseReusePath": rel_to_root(phase_reuse["path"]),
        "matchCount": len(matches),
        "reuseGateRequired": gate["required"],
        "reuseGateAcknowledged": False,
        "repeatedFailure": bool(gate.get("repeatedFailure", {}).get("detected")),
    }
    update_runtime_state(task_dir, lastBeforePhaseHook=result, reuseGate=reuse_gate)
    refresh_phase_transition_summary(task_dir)
    return result


def run_after_phase_hooks(task_dir: Path, phase: str, payload: Optional[dict[str, Any]] = None, reason: str = "after_phase") -> dict[str, Any]:
    """Run deterministic after-phase hooks.

    MVP handler records a lightweight phase-learning card. Summary can later
    refine these cards into index/raw knowledge only when they contain real
    value.
    """
    logs_dir = task_dir / "logs" / "phase-learnings"
    ensure_dir(logs_dir)
    card = {
        "schema": "automind.phase_learning.v1",
        "phase": phase,
        "hook": f"after:{phase}",
        "reason": reason,
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "payload": payload or {},
    }
    # Keep one file per phase for planning phases, append numeric suffix when a
    # caller provides an iteration to avoid overwriting generator/evaluator cards.
    suffix = ""
    iteration = (payload or {}).get("iteration")
    if iteration is not None:
        suffix = f"-iter-{iteration}"
    path = logs_dir / f"{phase}{suffix}.json"
    path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n")
    result = {
        "phase": phase,
        "hook": f"after:{phase}",
        "ranAt": card["createdAt"],
        "handlers": ["phase_learning_record"],
        "phaseLearningPath": rel_to_root(path),
    }
    update_runtime_state(task_dir, lastAfterPhaseHook=result)
    refresh_phase_transition_summary(task_dir)
    return result
