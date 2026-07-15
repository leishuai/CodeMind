"""Normalize pending ask_user questions from workflow/task/evaluation artifacts."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from orchestrator.state import read_evaluation_json, read_runtime_state


def _question_from_obj(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get("question") or obj.get("text") or "").strip()
    return str(obj or "").strip()


def _slugify(text: str, *, limit: int = 24) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return slug[:limit].strip("-")


def derive_question_id(
    *,
    explicit: Any = None,
    from_phase: str = "",
    iteration: Any = None,
    question: str = "",
    options: list[Any] | None = None,
) -> str:
    """Build a stable, unique-per-question id.

    Prefers an explicit non-degenerate id. Otherwise derives one from
    phase + iteration + a short content hash so distinct questions never all
    collapse into ``ask-001``.
    """
    explicit_str = str(explicit or "").strip()
    if explicit_str and explicit_str not in {"ask-001", "ask-000"}:
        return explicit_str

    phase_slug = _slugify(from_phase) or "ask"
    try:
        iter_part = f"iter{int(iteration)}" if iteration is not None else ""
    except (TypeError, ValueError):
        iter_part = _slugify(str(iteration))

    option_ids = ",".join(
        str(opt.get("id")) for opt in (options or []) if isinstance(opt, dict) and opt.get("id")
    )
    digest = hashlib.sha1(f"{from_phase}|{question}|{option_ids}".encode("utf-8")).hexdigest()[:8]
    parts = ["ask", phase_slug]
    if iter_part:
        parts.append(iter_part)
    parts.append(digest)
    return "-".join(p for p in parts if p)


def normalize_pending_question(task_dir: Path, workflow: dict[str, Any] | None = None) -> dict[str, Any] | None:
    state = read_runtime_state(task_dir) or {}
    evaluation = read_evaluation_json(task_dir) or {}
    workflow = workflow or {}
    pending = workflow.get("pendingUserAction") if isinstance(workflow.get("pendingUserAction"), dict) else {}

    state_next = str(state.get("nextAction") or "").strip().lower()
    evaluation_next = str(evaluation.get("nextAction") or "").strip().lower()

    ask = state.get("askUserQuestion")
    if not ask and isinstance(evaluation, dict) and evaluation_next == "ask_user":
        ask = evaluation.get("askUserQuestion")
    # Fallbacks for tasks whose askUserQuestion was never written (e.g. a planner
    # agent that recorded the question only under state.pendingQuestion, or a run
    # that crashed before run_harness_loop built askUserQuestion). Recover the
    # real question/options from the durable planner artifacts so the TUI shows
    # the actual decision bundle instead of a generic free-text prompt.
    if not ask and isinstance(state.get("pendingQuestion"), dict):
        ask = state.get("pendingQuestion")
    if not ask:
        planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
        review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
        review_decision = str(review.get("decision") or "").strip().lower() if isinstance(review, dict) else ""
        review_needs_input = bool(review.get("needsUserInput")) if isinstance(review, dict) else False
        # Recover a pre-implementation question only when the current control
        # state is actually waiting for ask_user, or the durable review still
        # declares an unresolved ask_user decision. Older tasks can retain the
        # original review bundle after the answer was applied and the planner
        # decision became auto_proceed; that must not be treated as a fresh
        # pending question during later retry_generator phases.
        should_recover_review = (
            state_next == "ask_user"
            or evaluation_next == "ask_user"
            or review_decision == "ask_user"
            or review_needs_input
        )
        if should_recover_review and isinstance(review, dict) and (review.get("questions") or review.get("options")):
            review_questions = review.get("questions") if isinstance(review.get("questions"), list) else []
            ask = {
                "question": "\n".join(str(q).strip() for q in review_questions if str(q).strip()),
                "options": review.get("options") if isinstance(review.get("options"), list) else [],
                "recommended": review.get("recommendedOption"),
            }

    question = _question_from_obj(ask)
    options = ask.get("options") if isinstance(ask, dict) and isinstance(ask.get("options"), list) else []
    recommended = (ask.get("recommended") or ask.get("recommendedOption")) if isinstance(ask, dict) else None

    if not question:
        question = str(pending.get("reason") or "").strip()
    if not options and isinstance(pending.get("options"), list):
        options = pending.get("options") or []

    if not question and not options and state_next != "ask_user" and evaluation_next != "ask_user":
        return None

    if pending.get("phase"):
        from_phase = pending.get("phase")
    elif evaluation_next == "ask_user":
        from_phase = "evaluation"
    else:
        from_phase = "pre_implementation_review"

    explicit_id = (
        state.get("pendingQuestionId")
        or (ask.get("id") if isinstance(ask, dict) else None)
        or evaluation.get("questionId")
    )
    question_id = derive_question_id(
        explicit=explicit_id,
        from_phase=str(from_phase),
        iteration=state.get("iteration") if state.get("iteration") is not None else evaluation.get("iteration"),
        question=question,
        options=options,
    )
    return {
        "id": question_id,
        "fromPhase": from_phase,
        "source": "evaluation" if evaluation_next == "ask_user" else "runtime-state",
        "question": question or "CodeMind needs user input before continuing.",
        "options": options,
        "recommended": recommended,
        "resumeTo": pending.get("resumeAfter") or from_phase,
        "status": "pending",
    }
