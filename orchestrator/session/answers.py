"""Persist user answers for TUI and skill mode."""
from __future__ import annotations

import json
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.session.ask_user import normalize_pending_question
from orchestrator.session.events import append_event
from orchestrator.state import read_evaluation_json, read_runtime_state, update_runtime_state, write_evaluation_json


def answers_path(task_dir: Path) -> Path:
    return task_dir / "user-answers.json"


def read_answers(task_dir: Path) -> list[dict[str, Any]]:
    path = answers_path(task_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def write_answers(task_dir: Path, answers: list[dict[str, Any]]) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    answers_path(task_dir).write_text(json.dumps(answers, ensure_ascii=False, indent=2) + "\n")




def _recommended_option_id(question: dict[str, Any], option_ids: list[str]) -> str | None:
    recommended = question.get("recommended") or question.get("recommendedOption")
    if recommended and str(recommended) in option_ids:
        return str(recommended)
    return None


def resolve_selected_option(
    task_dir: Path,
    value: str | None,
    *,
    fallback_to_recommended: bool = False,
) -> str | None:
    """Resolve option id from id, 1-based index, prefix/semantic match, or answer text.

    With ``fallback_to_recommended`` the function returns the question's
    recommended option when free text cannot be matched to any explicit option,
    so a recorded answer never degrades into ``selectedOption=null``.
    """
    raw = str(value or "").strip()
    if not raw and not fallback_to_recommended:
        return None

    # Users often paste/type forms like:
    #   1
    #   --option 1
    #   answer 1
    #   answer <task> --option 1
    #   automind answer <task> --option 1
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()
    lowered = [token.lower() for token in tokens]
    if "--option" in lowered:
        idx = lowered.index("--option")
        if idx + 1 < len(tokens):
            raw = tokens[idx + 1].strip()
    elif tokens and lowered[0] == "automind" and len(tokens) >= 2 and lowered[1] == "answer":
        raw = tokens[-1].strip()
    elif tokens and lowered[0] == "answer":
        raw = tokens[-1].strip()

    question = normalize_pending_question(task_dir) or {}
    options = question.get("options") if isinstance(question.get("options"), list) else []
    option_ids = [str(opt.get("id")) for opt in options if isinstance(opt, dict) and opt.get("id")]

    # 1) exact id (case-sensitive then case-insensitive)
    if raw in option_ids:
        return raw
    raw_lower = raw.lower()
    for oid in option_ids:
        if oid.lower() == raw_lower:
            return oid

    # 2) 1-based index
    if raw.isdigit():
        index = int(raw) - 1
        if 0 <= index < len(option_ids):
            return option_ids[index]

    if raw_lower:
        # 3) prefix match on option id (either direction), must be unambiguous
        prefix_matches = [
            oid for oid in option_ids
            if oid.lower().startswith(raw_lower) or raw_lower.startswith(oid.lower())
        ]
        if len(set(prefix_matches)) == 1:
            return prefix_matches[0]

        # 4) semantic/substring match against option id + label, must be unambiguous
        semantic_matches: list[str] = []
        for opt in options:
            if not isinstance(opt, dict) or not opt.get("id"):
                continue
            oid = str(opt.get("id"))
            haystacks = [oid.lower(), str(opt.get("label") or "").lower(), str(opt.get("impact") or "").lower()]
            id_words = set(oid.lower().replace("-", "_").split("_"))
            answer_words = set(re.split(r"[\s_\-/]+", raw_lower)) - {""}
            if any(raw_lower and (raw_lower in hay or (hay and hay in raw_lower)) for hay in haystacks):
                semantic_matches.append(oid)
            elif answer_words and id_words and answer_words & id_words:
                semantic_matches.append(oid)
        if len(set(semantic_matches)) == 1:
            return semantic_matches[0]

    # 5) recommended fallback (opt-in): never let a recorded answer become null
    if fallback_to_recommended:
        return _recommended_option_id(question, option_ids)
    return None


def _update_planner_after_answer(state: dict[str, Any], answer: dict[str, Any], selected_option: str | None, now: str, answered_by: str) -> dict[str, Any]:
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    if not planner:
        return state
    planner = dict(planner)
    planner["needsUserInput"] = False
    planner["userAnswer"] = answer
    review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
    if review:
        review = dict(review)
        review["needsUserInput"] = False
        if selected_option == "stop":
            review["decision"] = "stop"
        elif selected_option in {"replan", "revise_scope_assumptions", "choose_alternative_approach"}:
            review["decision"] = "replan"
        else:
            review["decision"] = "auto_proceed"
        review["selectedOption"] = selected_option
        review["answeredAt"] = now
        review["confirmedAt"] = now
        review["confirmedBy"] = answered_by
        # "拦不拦截以用户诉求为准": the user explicitly chose the
        # full-auto option or typed a matching keyword — record fullAuto=true
        # so every subsequent completion gate skips ask_user. This mirrors
        # what build_pre_implementation_review_state writes when the original
        # request already carries a "full auto / 全自动" signal.
        # Keyword list must stay in sync with orchestrator.workflow.EXPLICIT_FULL_AUTO_KEYWORDS
        # and the local copy in orchestrator.main.build_pre_implementation_review_state.
        full_auto_keywords = (
            "一站到底", "全自动模式", "全自动", "不用问用户", "不用问我", "不用确认", "无需确认", "不要问", "直接实现",
            "full auto", "full-auto", "fully automatic", "no confirmation", "do not ask", "auto proceed",
        )
        answer_text_lower = str(answer.get("answerText") or "").lower()
        if selected_option == "confirm_full_auto_mode" or any(
            kw.lower() in answer_text_lower for kw in full_auto_keywords
        ):
            review["fullAuto"] = True
        bundle = review.get("decisionBundle") if isinstance(review.get("decisionBundle"), dict) else {}
        if bundle:
            bundle = dict(bundle)
            bundle["selectedOption"] = selected_option
            bundle["confirmedAt"] = now
            bundle["confirmedBy"] = answered_by
            review["decisionBundle"] = bundle
        planner["preImplementationReview"] = review
    state["planner"] = planner
    return state

def apply_user_answer(
    task_dir: Path,
    *,
    answer_text: str = "",
    selected_option: str | None = None,
    answered_by: str = "cli_user",
) -> dict[str, Any]:
    selected_option = (
        resolve_selected_option(task_dir, selected_option)
        or resolve_selected_option(task_dir, answer_text)
        or selected_option
        # Last resort: never persist selectedOption=null when the question has a
        # recommended option. A recorded answer must carry an actionable choice.
        or resolve_selected_option(task_dir, answer_text, fallback_to_recommended=True)
    )
    question = normalize_pending_question(task_dir) or {
        "id": "ask-001",
        "fromPhase": "unknown",
        "question": "",
        "options": [],
        "resumeTo": "workflow-check",
    }
    now = datetime.now().isoformat(timespec="seconds")
    answer = {
        "id": f"answer-{len(read_answers(task_dir)) + 1:03d}",
        "questionId": question.get("id"),
        "answeredAt": now,
        "answeredBy": answered_by,
        "fromPhase": question.get("fromPhase"),
        "question": question.get("question"),
        "answerText": answer_text,
        "selectedOption": selected_option,
        "resumeTo": question.get("resumeTo"),
        "delivery": {
            "mode": "next_invocation_prompt",
            "status": "pending",
        },
    }
    answers = read_answers(task_dir)
    answers.append(answer)
    write_answers(task_dir, answers)

    replan_options = {"replan", "revise_scope_assumptions", "choose_alternative_approach"}
    stop_requested = selected_option == "stop"
    replan_requested = selected_option in replan_options
    state = read_runtime_state(task_dir) or {}
    state = _update_planner_after_answer(state, answer, selected_option, now, answered_by)
    state.update({
        "status": "stopped_by_user" if stop_requested else ("replan_pending" if replan_requested else "planned"),
        "currentOwner": "human" if stop_requested else "planner",
        "nextAction": "stop" if stop_requested else ("replan" if replan_requested else "run_test_planner"),
        "latestUserAnswer": answer,
        "userAnswers": answers,
        "askUserQuestion": None,
        "pendingQuestionId": None,
    })
    update_runtime_state(task_dir, **state)

    evaluation = read_evaluation_json(task_dir)
    if isinstance(evaluation, dict):
        evaluation["userAnswer"] = answer
        evaluation["askUserQuestion"] = None
        evaluation["nextAction"] = "stop" if stop_requested else ("replan" if replan_requested else "retry_generator")
        write_evaluation_json(task_dir, evaluation)

    append_event(
        task_dir,
        "user_answered",
        f"User answered pending question: {selected_option or answer_text[:80] or 'answer recorded'}",
        phase=str(question.get("fromPhase") or "ask_user"),
        replace_key=f"ask_user:{question.get('id')}",
        data={"answerId": answer["id"], "selectedOption": selected_option},
    )
    return answer




def has_resolved_pre_implementation_answer(task_dir: Path) -> bool:
    """Return True once the pre-implementation ask_user question was answered.

    This gate is intentionally one-shot: the answer should be fed into the next
    planner/refiner invocation instead of asking the same pre-implementation
    question repeatedly. Replan/stop style answers remain explicit control
    signals and do not count as auto-proceed resolution.
    """
    replan_options = {"replan", "revise_scope_assumptions", "choose_alternative_approach"}
    for answer in reversed(read_answers(task_dir)):
        if not isinstance(answer, dict):
            continue
        from_phase = str(answer.get("fromPhase") or "")
        selected = str(answer.get("selectedOption") or "")
        if from_phase == "pre_implementation_review" and selected not in replan_options | {"stop"}:
            return True
    latest = (read_runtime_state(task_dir) or {}).get("latestUserAnswer")
    if isinstance(latest, dict):
        from_phase = str(latest.get("fromPhase") or "")
        selected = str(latest.get("selectedOption") or "")
        if from_phase == "pre_implementation_review" and selected not in replan_options | {"stop"}:
            return True
    return False


def latest_pending_answer(task_dir: Path) -> dict[str, Any] | None:
    """Return the latest user answer waiting to be delivered/applied."""
    answers = read_answers(task_dir)
    for answer in reversed(answers):
        if not isinstance(answer, dict):
            continue
        delivery = answer.get("delivery") if isinstance(answer.get("delivery"), dict) else {}
        # Terminal states: delivered (handed to the next agent turn) or applied
        # (confirmed consumed). Anything else is still awaiting delivery.
        if delivery.get("status") not in {"delivered", "applied"}:
            return answer
    return None


def latest_pending_answer_matches_question(task_dir: Path, question: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return pending answer if it appears to answer the current question.

    This prevents TUI/skill guidance from asking the same stale question again
    while the next agent turn is already applying the recorded answer.
    """
    if not question:
        return None
    answer = latest_pending_answer(task_dir)
    if not answer:
        return None
    qid = str(question.get("id") or "")
    aid = str(answer.get("questionId") or "")
    if qid and aid and qid == aid:
        return answer
    qtext = str(question.get("question") or "").strip()
    atext = str(answer.get("question") or "").strip()
    if qtext and atext and (qtext == atext or qtext in atext or atext in qtext):
        return answer
    selected = str(answer.get("selectedOption") or answer.get("answerText") or "").strip()
    if selected and any(token in selected for token in ["connect_real_device", "adb_fallback", "pause_for_environment", "approve_runtime_downgrade"]):
        return answer
    return None


def latest_answer_prompt_context(task_dir: Path) -> str:
    """Render latest pending user answer as prompt context for agent invocation."""
    answers = read_answers(task_dir)
    if not answers:
        return ""
    answer = answers[-1]
    delivery = answer.get("delivery") if isinstance(answer.get("delivery"), dict) else {}
    if delivery.get("status") in {"delivered", "applied"}:
        return ""
    lines = [
        "",
        "## Latest AutoMind user answer",
        "",
        "A pending user answer was recorded by AutoMind and must be applied before continuing.",
        f"- Question: {answer.get('question') or '-'}",
        f"- Answer: {answer.get('answerText') or '-'}",
        f"- Selected option: {answer.get('selectedOption') or '-'}",
        f"- From phase: {answer.get('fromPhase') or '-'}",
        f"- Resume to: {answer.get('resumeTo') or '-'}",
        "",
        "Update the relevant task artifacts (`Brainstorm.md`, `pre-implementation-review.json`, `runtime-state.json`, `evaluation.json`, `Plan.md`, or `TestCases.md`) to reflect this answer, then continue the workflow. Do not ask the same question again unless the answer is ambiguous or creates a new blocker.",
    ]
    return "\n".join(lines)


def mark_latest_answer_delivered(task_dir: Path, *, mode: str = "prompt") -> None:
    answers = read_answers(task_dir)
    if not answers:
        return
    answer = answers[-1]
    delivery = answer.get("delivery") if isinstance(answer.get("delivery"), dict) else {}
    # Do not downgrade an already-applied answer back to delivered.
    if delivery.get("status") == "applied":
        return
    delivery["mode"] = mode
    delivery["status"] = "delivered"
    delivery["deliveredAt"] = datetime.now().isoformat(timespec="seconds")
    answer["delivery"] = delivery
    answers[-1] = answer
    write_answers(task_dir, answers)
    update_runtime_state(task_dir, latestUserAnswer=answer, userAnswers=answers)


def mark_latest_answer_applied(task_dir: Path, *, applied_by: str = "harness") -> dict[str, Any] | None:
    """Mark the latest recorded answer as fully applied (terminal state).

    ``recorded -> delivered -> applied``. Once applied, the answer is no longer
    surfaced as a pending prompt and the next loop turn advances reliably without
    re-asking. Returns the updated answer, or ``None`` if there is nothing to mark.
    """
    answers = read_answers(task_dir)
    if not answers:
        return None
    answer = answers[-1]
    delivery = answer.get("delivery") if isinstance(answer.get("delivery"), dict) else {}
    now = datetime.now().isoformat(timespec="seconds")
    delivery.setdefault("deliveredAt", now)
    if delivery.get("status") != "delivered":
        delivery["status"] = "delivered"
    delivery["status"] = "applied"
    delivery["appliedAt"] = now
    delivery["appliedBy"] = applied_by
    answer["delivery"] = delivery
    answers[-1] = answer
    write_answers(task_dir, answers)
    update_runtime_state(task_dir, latestUserAnswer=answer, userAnswers=answers)
    return answer
