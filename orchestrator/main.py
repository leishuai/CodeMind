#!/usr/bin/env python3
"""
AutoMind Orchestrator
Core orchestrator: manage tasks, generate artifacts, and run the Agent Loop
"""

from __future__ import annotations

import os
import sys
import json
import subprocess
import shutil
import re
import hashlib
import tempfile
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Literal

if __package__ in {None, ""}:
    # Preserve direct execution: `python3 orchestrator/main.py ...`.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.config import (
    AGENT_ADAPTERS,
    AUTOMIND_ROOT,
    AUTOMIND_WORKSPACE_ROOT,
    LOCAL_REUSE_INDEX_PATH,
    SUMMARY_DIR,
    TASKS_DIR,
    MAX_ITERATIONS,
    MAX_REFLECTIONS_PER_TC,
    AUTONOMOUS_REPLAN_AFTER_BUDGET,
    TASK_STATUSES,
)
from orchestrator.console import error, log, read_tail, run_cmd, success, warn
from orchestrator.warm_build import (
    is_incremental_build_possible,
    start_warm_build,
    wait_for_warm_build,
)
from orchestrator.ui_path_cache import (
    cache_ui_path,
    compute_ui_fingerprint,
    expire_cached_ui_paths,
    get_cached_ui_path,
    wait_for_ui_exploration,
)
from orchestrator.metrics import (
    record_phase_start,
    record_phase_end,
    record_subphase_start,
    record_subphase_end,
    record_iter_phase_start,
    record_iter_phase_end,
    record_agent_call,
    record_iteration,
    flush_metrics,
    SUBPHASE_BUILD,
    SUBPHASE_INSTALL,
    SUBPHASE_UI_EXECUTION,
    SUBPHASE_PREFLIGHT,
    SUBPHASE_COMPLETION_GATE,
    SUBPHASE_FLOW_GENERATION,
    SUBPHASE_RESULT_ANALYSIS,
)
from orchestrator.audit import (
    record_decision,
    record_branch,
    record_action,
    record_gate,
    record_policy,
    record_recovery,
)
from orchestrator.automation_tools import (
    AUTOMATION_TOOL_PROFILES,
    automation_setup_command_plan,
    build_project_dependency_report,
    collect_env_snapshot,
    cmd_project_dependency_check,
    cmd_setup_automation_tools,
    get_android_tools_python,
)
from orchestrator.reuse import accumulated_business_summary_files, apply_runtime_language_instruction, build_preloaded_context, detect_preferred_language, preloaded_summary_files_for_task_type, render_prompt_template, write_rendered_prompt, write_reuse_context
from orchestrator.knowledge_index import evaluate_knowledge_store, find_knowledge_record, load_knowledge_index, read_raw_excerpt, search_knowledge, summarize_knowledge_record
from orchestrator.agents import extract_agent_reply, run_agent
from orchestrator.classification import has_negative_mobile_device_signal
from orchestrator.evaluation_result import apply_evaluation_result
from orchestrator.harness_profiles import get_harness_profile
from orchestrator.probe_records import init_task_artifacts, write_json_file, write_probe_record
from orchestrator.validation_history import append_validation_history
from orchestrator.commands.session import cmd_answer, cmd_continue, cmd_event, cmd_message, cmd_phase_gate, cmd_report, cmd_trace, cmd_tui, print_answer_usage
from orchestrator.commands.status import cmd_logs, cmd_status
from orchestrator.commands.knowledge import cmd_completion_check, cmd_doctor, cmd_doctor_scan, cmd_improve_suggestions, cmd_knowledge, cmd_notifications, cmd_preloaded_check, cmd_process_check, cmd_record_check, cmd_reuse, cmd_reuse_ack, cmd_summary, cmd_summary_compact, cmd_tick_iteration, cmd_workflow_check, cmd_workflow_contract
from orchestrator.evidence_contract import attach_required_test_results
from orchestrator.commands.mobile import cmd_android_probe_flow, cmd_android_project_probe, cmd_ios_command_probe, cmd_ios_project_probe
from orchestrator.commands.smoke import cmd_agent_failure_records_smoke, cmd_agent_session_policy_smoke, cmd_context_pack_smoke, cmd_demo_retry, cmd_planner_smoke, cmd_policy_guards_smoke, cmd_resume_recovery_smoke, cmd_reuse_playbook_smoke, cmd_smoke, cmd_summary_refiner_smoke, cmd_unblock_gate_smoke
from orchestrator.workflow import (
    check_workflow_consistency,
    record_workflow_check_state,
    validate_planner_artifacts,
)
from orchestrator.context_packs import build_evaluator_context_pack, build_generator_context_pack
from orchestrator.completion import (
    apply_completion_gate,
    build_completion_report,
    read_user_autonomy_intent,
    validate_ask_user_category,
    write_completion_ledger,
)
from orchestrator.summary import (
    ensure_summary_generated,
    generate_summary,
    build_summary_refiner_seed,
    render_improve_suggestions,
    validate_ai_summary_refinement,
)
from orchestrator.resume import build_resume_recovery_entry
from orchestrator.state_reducer import reconcile_task_state
from orchestrator.reports import (
    build_status_guidance,
    build_summary_reuse_status,
    generate_html_report,
    print_report_manifest,
)
from orchestrator.records import check_task_records, finalize_task_records, reconcile_validation_status, set_validation_status_marker, validation_status_issues
from orchestrator.artifacts import (
    extract_declared_testcases,
    primary_requirements_path,
    read_requirements_contract_text,
    requirement_contract_paths,
    task_uses_single_file_requirements,
)
from orchestrator.workflow_contract import write_workflow_contract, validate_workflow_contract, _normalize_runtime_level
from orchestrator.tc_attempts import read_tc_attempts, record_tc_attempts
from orchestrator.evidence_synthesis import should_synthesize_evaluation, synthesize_evaluation_from_evidence
from orchestrator.iteration_planning import write_iteration_purpose
from orchestrator.session.answers import apply_user_answer
from orchestrator.session.events import append_event
from orchestrator.session.instructions import build_next_instruction
from orchestrator.session.ask_user import normalize_pending_question
from orchestrator.tui.app import run_tui
from orchestrator.tui.session import run_tui_owned_loop
from orchestrator.tui.shell import run_command_shell
from orchestrator.version import automind_version_label
from orchestrator.session.answers import has_resolved_pre_implementation_answer, latest_answer_prompt_context, latest_pending_answer_matches_question, mark_latest_answer_applied, mark_latest_answer_delivered
from orchestrator.session.trace import build_trace, render_trace_text, write_trace
from orchestrator.session.messages import append_user_message, mark_pending_user_messages_delivered, pending_user_messages_prompt_context
from orchestrator.process_eval import render_process_eval, run_process_eval
from orchestrator.hooks import run_after_phase_hooks, run_before_phase_hooks
from orchestrator.phase_transition import refresh_phase_transition_summary
from orchestrator.state import (
    append_progress_log,
    clear_current_task,
    clear_task_primary_session,
    ensure_dir,
    get_task_dir,
    get_tui_chat_task_code,
    list_tasks,
    read_current_task,
    read_evaluation_json,
    read_notifications,
    read_runtime_state,
    rel_to_root,
    task_has_authoritative_terminal_pass,
    seed_task_primary_session_from_tui_chat,
    tick_iteration,
    update_heartbeat,
    update_runtime_state,
    write_current_task,
    write_evaluation_json,
    write_runtime_state,
)


# ============================================================
# Agent preflight
# ============================================================


def pre_implementation_ask_user_policy_issues(task_dir: Path, ask_question: dict) -> list[str]:
    """Return ask_user policy issues for a pre-implementation review question.

    Planner/pre-implementation review predates the Evaluator completion gate, so
    it can otherwise pause on a self-serviceable verification unblock before
    `apply_completion_gate` has a chance to rewrite it. Reuse the same hard-
    interrupt whitelist here: local, reversible, auditable compatible/external
    runner fixes must continue autonomously; runtime downgrade, user-provided
    device/environment, signing/keychain/device-trust changes, sudo/tunneld,
    uninstall/delete/reset, external upload, account login, payment, and other
    irreversible/high-impact operations remain legitimate ask_user gates.
    """
    question = dict(ask_question or {})
    question.setdefault("category", "system_or_external_dependency")
    evaluation = {
        "nextAction": "ask_user",
        "askUserQuestion": question,
    }
    issues, _warnings = validate_ask_user_category(evaluation, read_user_autonomy_intent(task_dir))
    return issues


def should_pause_after_generator_for_human_input(task_dir: Path, iteration: int) -> bool:
    """Honor ask_user gates produced by Generator before starting Evaluator.

    Generators can legitimately stop after producing proposal/design artifacts
    and write ``evaluation.json.nextAction=ask_user`` (for example OpenSpec
    approval, signing/device authorization, or other human-governed gates). The
    harness must not continue into Evaluator and overwrite/ignore that gate.
    """
    evaluation = read_evaluation_json(task_dir)
    if isinstance(evaluation, dict) and evaluation.get("nextAction") == "ask_user":
        eval_iter = int(evaluation.get("iteration") or iteration)
        apply_evaluation_result(task_dir, evaluation)
        pending = normalize_pending_question(task_dir)
        update_runtime_state(
            task_dir,
            status="human_input_pending",
            iteration=eval_iter,
            currentOwner="human",
            nextAction="ask_user",
            askUserQuestion=evaluation.get("askUserQuestion"),
            pendingQuestionId=(pending or {}).get("id"),
        )
        return True

    state = read_runtime_state(task_dir) or {}
    if state.get("status") == "human_input_pending" or state.get("nextAction") == "ask_user":
        pending = normalize_pending_question(task_dir)
        update_runtime_state(
            task_dir,
            status="human_input_pending",
            iteration=int(state.get("iteration") or iteration),
            currentOwner="human",
            nextAction="ask_user",
            askUserQuestion=state.get("askUserQuestion"),
            pendingQuestionId=state.get("pendingQuestionId") or (pending or {}).get("id"),
        )
        return True
    return False


def runtime_timeout(env_var: str = "AUTOMIND_CMD_TIMEOUT", default: int = 43200) -> int:
    """Return a wide runtime timeout for long build/test/verifier commands.

    Short discovery probes still use their own small timeouts. This helper is
    for commands that may legitimately run for a long time, such as agent,
    build, XCUITest, probe-flow, or project-native verification.
    """
    raw = os.environ.get(env_var) or os.environ.get("AUTOMIND_CMD_TIMEOUT") or str(default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        warn(f"Invalid {env_var}/AUTOMIND_CMD_TIMEOUT={raw!r}; using {default}s")
        value = default
    return max(1, value)



def generate_task_code(user_input: str) -> str:
    """Generate a readable, stable-enough task code from user input.

    Format: ``<semantic-slug>_<MMDDHHMMSS>_<hash4>``.
    The short hash keeps names unique while the slug keeps TUI/task folders
    recognizable, especially for mixed Chinese + technical English requests.
    """
    text = str(user_input or "")
    stop_words = {
        "a", "an", "and", "or", "the", "to", "for", "of", "in", "on", "with",
        "by", "from", "new", "add", "update", "fix", "implement", "create",
        "using", "use",
    }
    english_tokens = [
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]*", text)
        if token.lower() not in stop_words and len(token) > 1
    ]

    # Preserve common technical identifiers split by the regex, e.g.
    # ``music_audio_stop`` -> music/audio/stop. Include several tokens instead
    # of the old first-token-only behavior.
    slug_tokens: list[str] = []
    for token in english_tokens:
        if token not in slug_tokens:
            slug_tokens.append(token)
        if len(slug_tokens) >= 5:
            break

    if not slug_tokens:
        zh_keyword_map = [
            ("安卓", "android"),
            ("Media", "music"),
            ("Play", "playback"),
            ("埋点", "analytics"),
            ("停止", "stop"),
            ("歌曲", "song"),
            ("视频", "video"),
            ("登录", "login"),
            ("崩溃", "crash"),
            ("修复", "fix"),
        ]
        for keyword, token in zh_keyword_map:
            if keyword in text and token not in slug_tokens:
                slug_tokens.append(token)
            if len(slug_tokens) >= 5:
                break

    digest = hashlib.md5(text.encode()).hexdigest()[:4]
    slug = "_".join(slug_tokens[:5]) or f"task_{digest}"
    slug = re.sub(r"[^a-z0-9_]+", "_", slug).strip("_")[:48] or f"task_{digest}"
    timestamp = datetime.now().strftime("%m%d%H%M%S")
    return f"{slug}_{timestamp}_{digest}"



# Module-level guard so the legacy probe-flow filename warning prints once.
_PROBE_FLOW_LEGACY_WARNED: set[str] = set()


def resolve_probe_flow_path(task_dir: Path, platform: str) -> Path:
    """Return the canonical probe-flow path for a task, with legacy fallback.

    Prefers the platform-suffixed name (`probe-flow.android.json` /
    `probe-flow.ios.json`). When the new file is absent but the legacy
    `probe-flow.json` exists, returns the legacy path and emits a one-time
    deprecation warning. AutoMind never renames files on disk automatically.
    """
    suffix = "android" if platform == "android" else "ios"
    new_path = task_dir / f"probe-flow.{suffix}.json"
    legacy_path = task_dir / "probe-flow.json"
    if new_path.exists():
        return new_path
    if legacy_path.exists():
        key = f"{task_dir}:{suffix}"
        if key not in _PROBE_FLOW_LEGACY_WARNED:
            _PROBE_FLOW_LEGACY_WARNED.add(key)
            warn(
                f"`probe-flow.json` is deprecated; rename to `probe-flow.{suffix}.json` (task: {task_dir.name})."
            )
        return legacy_path
    # Neither exists: return the canonical name so callers can create it there.
    return new_path



















# ============================================================
# Task\u7ba1\u7406
# ============================================================


def extract_user_verify_command(user_input: str) -> str | None:
    """Extract explicit verification command from a user request.

    Supported forms:
    - verifyCommand: `...`
    - scriptCommand: `...`
    - \u9a8c\u8bc1\u547d\u4ee4：`...`
    - \u8fd0\u884c\u547d\u4ee4：`...`
    """
    for pattern in [
        r"(?:scriptCommand|verifyCommand|\u9a8c\u8bc1\u547d\u4ee4|\u8fd0\u884c\u547d\u4ee4)\s*[:：]\s*`([^`\n]+)`",
        r"(?:scriptCommand|verifyCommand|\u9a8c\u8bc1\u547d\u4ee4|\u8fd0\u884c\u547d\u4ee4)\s*[:：]\s*([^\n]+)",
    ]:
        m = re.search(pattern, user_input, flags=re.IGNORECASE)
        if m:
            value = m.group(1).strip().strip()
            if value:
                return value
    return None


def extract_ios_app_config(user_input: str) -> dict:
    """Extract minimal iosApp config from ask text.

    Supported markers are intentionally simple so humans/outer systems can pass
    structured hints without a separate parser:
    - iosProject/projectPath: `...`
    - iosWorkspace/workspacePath: `...`
    - iosScheme/scheme: `...`
    - iosDeviceId/xcodebuildDeviceId/deviceId: `...`
    - team/DEVELOPMENT_TEAM: `...`
    - bundleId/PRODUCT_BUNDLE_IDENTIFIER: `...`
    - targetBundleId/appUnderTest: `...` (the app the UI runner should drive,
      e.g. a real product like com.example.app; distinct from the runner/host bundleId)

    For the local AutoMind iOS demo, provide a convenience default when the ask
    explicitly mentions the demo/XCUITest path. This is a local sample profile,
    not a framework assumption for arbitrary iOS projects.
    """
    mapping = {
        "projectPath": ["iosProject", "projectPath", "xcodeproj"],
        "workspacePath": ["iosWorkspace", "workspacePath", "xcworkspace"],
        "scheme": ["iosScheme", "scheme"],
        "xcodebuildDeviceId": ["iosDeviceId", "xcodebuildDeviceId", "deviceId"],
        "team": ["team", "DEVELOPMENT_TEAM", "developmentTeam"],
        "bundleId": ["bundleId", "PRODUCT_BUNDLE_IDENTIFIER", "productBundleIdentifier"],
        "targetBundleId": ["targetBundleId", "appUnderTest", "targetBundle"],
        "configuration": ["configuration", "iosConfiguration"],
    }
    config: dict[str, str] = {}
    for out_key, keys in mapping.items():
        for key in keys:
            for pattern in [
                rf"(?:{re.escape(key)})\s*[:=：]\s*`([^`\n]+)`",
                rf"(?:{re.escape(key)})\s*[:=：]\s*([^\n,，]+)",
            ]:
                m = re.search(pattern, user_input, flags=re.IGNORECASE)
                if m:
                    value = m.group(1).strip().strip('"\'')
                    if value:
                        config[out_key] = value
                        break
            if out_key in config:
                break

    text = user_input.lower()
    mentions_demo = ("automindiosdemo" in text or "autoMindIOSDemo" in user_input or "ios demo" in text or "ios \u771f\u673a xcuitest" in text or "xcuitest demo" in text)
    if mentions_demo:
        config.setdefault("projectPath", "demos/ios-simulator-demo/AutoMindIOSDemo.xcodeproj")
        config.setdefault("scheme", "AutoMindIOSDemo")
        config.setdefault("bundleId", "ai.openclaw.automind.demo")
        config.setdefault("configuration", "Debug")
    return config

def create_task(user_input: str) -> tuple[str, Path]:
    """
    \u521b\u5efa\u65b0Task
    \u8fd4\u56de: (task_code, task_dir)
    """
    task_code = generate_task_code(user_input)
    task_dir = get_task_dir(task_code)

    ensure_dir(task_dir)
    ensure_dir(task_dir / "logs")

    # \u5199\u5165\u7528\u6237\u539f\u59cb\u9700\u6c42
    (task_dir / ".user_input.txt").write_text(user_input)

    script_command = extract_user_verify_command(user_input)
    task_type = infer_task_type(user_input)
    # An explicit verify/script command is the strongest signal for the generic
    # command harness. Platform-specific adapters can still be used explicitly
    # via android-probe-flow / future ios-probe-flow commands.
    if script_command:
        task_type = "script"
    harness_profile = get_harness_profile(task_type)

    state = {
        "taskId": task_code,
        "userInput": user_input,
        "taskType": task_type,
        "harnessProfile": harness_profile,
        "status": "created",
        "iteration": 0,
        "maxIterations": MAX_ITERATIONS,
        "currentOwner": "planner",
        "nextAction": "generate_requirements",
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "updatedAt": datetime.now().isoformat(timespec="seconds")
    }
    if script_command:
        state["scriptCommand"] = script_command
        state["verifyCommand"] = script_command
    if task_type == "ios":
        ios_app = extract_ios_app_config(user_input)
        if ios_app:
            state["iosApp"] = ios_app
    write_runtime_state(task_dir, state)
    write_reuse_context(task_dir, reason="task_created")

    success(f"Task created: {task_code}")
    return task_code, task_dir



def split_user_requirement_units(user_input: str) -> list[str]:
    """Best-effort lightweight requirement splitting without pretending to be a full spec engine."""
    cleaned = re.sub(r"`[^`]+`", "", user_input).strip()
    # Use punctuation/newlines as weak separators. Do not split on spaces or
    # hyphens: platform names, English phrases, and "iOS login page" should stay
    # one unit until the AI Planner performs semantic decomposition.
    parts = [p.strip() for p in re.split(r"[。；;\n]+", cleaned) if p.strip()]
    # Keep it small: one or a few meaningful units. Long, vague text remains one unit.
    units: list[str] = []
    for part in parts:
        if any(marker in part for marker in ["，", ",", "、"]):
            subparts = [x.strip() for x in re.split(r"[，,、]+", part) if x.strip()]
            if 1 < len(subparts) <= 5:
                units.extend(subparts)
            else:
                units.append(part)
        else:
            units.append(part)
    # Drop pure command marker fragments; keep order and dedupe.
    result: list[str] = []
    seen: set[str] = set()
    for unit in units or [user_input.strip()]:
        if re.search(r"^(scriptCommand|verifyCommand|\u9a8c\u8bc1\u547d\u4ee4|\u8fd0\u884c\u547d\u4ee4)\s*[:：]", unit, flags=re.I):
            continue
        unit = unit.strip()
        if unit and unit not in seen:
            seen.add(unit)
            result.append(unit)
    return result or [user_input.strip()]


def infer_workspace_client_platforms() -> set[str]:
    """Best-effort client platform detection from the target workspace root.

    Keep this conservative: only root/near-root project markers count, and demo
    folders in the AutoMind runtime are ignored.
    """
    root = AUTOMIND_WORKSPACE_ROOT
    platforms: set[str] = set()
    ignored_dirs = {".git", ".automind", "dist", "docs", "examples", "demos", "scripts", "summaries", "templates"}
    try:
        entries = list(root.iterdir())
    except Exception:
        return platforms

    if any(root.glob("*.xcodeproj")) or any(root.glob("*.xcworkspace")) or (root / "Podfile").exists():
        platforms.add("ios")
    if (root / "settings.gradle").exists() or (root / "settings.gradle.kts").exists():
        if (root / "app" / "build.gradle").exists() or (root / "app" / "build.gradle.kts").exists() or list(root.glob("*/src/main/AndroidManifest.xml")):
            platforms.add("android")

    for entry in entries:
        if not entry.is_dir() or entry.name in ignored_dirs or entry.name.startswith("."):
            continue
        try:
            if any(entry.glob("*.xcodeproj")) or any(entry.glob("*.xcworkspace")) or (entry / "Podfile").exists():
                platforms.add("ios")
            if (entry / "src" / "main" / "AndroidManifest.xml").exists() or (entry / "build.gradle").exists() or (entry / "build.gradle.kts").exists():
                platforms.add("android")
        except Exception:
            continue
    return platforms


def is_client_development_or_verification_task_detail(user_input: str, task_type: str | None = None) -> dict[str, Any]:
    """Rich model-first variant: decide whether this is real client/app work.

    Returns a dict with:
      isClientTask: bool — the thin-wrapper return value.
      triageSource: "code_deterministic" when the decision rests on a hard
          structural fact (non-mobile task type, explicit negative signal) or
          on an explicit client keyword. "requires_model_review" when the
          positive decision relies only on the weak
          workspace-platform + action-keyword heuristic, or when a mobile
          task has no client/action signal at all.
      needsModelReview: True iff the decision is heuristic enough to re-examine.
      matchedKeyword: which signal bucket fired.
      reason: short human-readable description.
    """
    resolved_task_type = task_type or infer_task_type(user_input)
    if resolved_task_type not in {"ios", "android", "dual"}:
        return {
            "isClientTask": False, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "non_mobile_task_type",
            "reason": f"任务类型为 {resolved_task_type}，不是 ios/android/dual，按定义不是客户端/App 任务。",
        }
    if has_negative_mobile_device_signal(user_input):
        return {
            "isClientTask": False, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "negative_mobile_signal",
            "reason": "请求中明确声明无需移动设备/真机验证。",
        }
    text = (user_input or "").lower()
    workspace_platforms = infer_workspace_client_platforms()
    workspace_matches_platform = bool(workspace_platforms) and (
        resolved_task_type == "dual" or resolved_task_type in workspace_platforms
    )
    client_markers = [
        "app", "ui", "screen", "page", "client", "mobile", "frontend",
        "activity", "view", "controller", "apk", "ipa", "bundle", "xcodebuild",
        "gradle", "launch", "install", "device", "simulator", "emulator",
        "crash", "tap", "click",
    ]
    chinese_client_markers = [
        "客户端", "移动端", "应用", "页面", "界面", "屏幕", "视图", "入口",
        "启动", "安装", "打包", "包名", "崩溃", "闪退", "真机", "模拟器",
        "点击",
    ]
    action_markers = [
        "fix", "implement", "add", "modify", "change", "build", "verify",
        "test", "run", "launch", "install", "repair", "develop",
        "修复", "实现", "新增", "修改", "开发", "验证", "测试", "运行", "启动", "安装",
    ]
    matched_client_en = next((m for m in client_markers if m in text), None)
    matched_client_zh = next((m for m in chinese_client_markers if m in (user_input or "")), None)
    explicit_client_signal = bool(matched_client_en or matched_client_zh)
    development_or_verification_signal = any(marker in text for marker in action_markers)
    if explicit_client_signal:
        return {
            "isClientTask": True, "triageSource": "code_deterministic",
            "needsModelReview": False,
            "matchedKeyword": "client_token:" + (matched_client_en or matched_client_zh or ""),
            "reason": "请求中出现明确的客户端/UI关键词。",
        }
    if workspace_matches_platform and development_or_verification_signal:
        return {
            "isClientTask": True, "triageSource": "requires_model_review",
            "needsModelReview": True, "matchedKeyword": "workspace_plus_action",
            "reason": "没有显式客户端关键词，仅凭 workspace 平台匹配 + 动作词推断为客户端任务；这是弱启发式，模型应结合项目上下文确认是否确实涉及客户端/UI。",
        }
    return {
        "isClientTask": False, "triageSource": "requires_model_review",
        "needsModelReview": True, "matchedKeyword": "mobile_task_no_client_signal",
        "reason": "任务类型是移动端但没有任何客户端/动作信号，关键词无法确定，模型应再确认这是否真的是客户端开发/验证任务。",
    }


def is_client_development_or_verification_task(user_input: str, task_type: str | None = None) -> bool:
    """Return True only for client/app work, not every mention of iOS/Android. Thin
    wrapper over is_client_development_or_verification_task_detail; callers needing
    triage metadata should use the _detail variant."""
    return is_client_development_or_verification_task_detail(user_input, task_type)["isClientTask"]


def run_short_device_command(cmd: list[str], timeout: int = 5) -> tuple[int, str]:
    """Run a read-only device-discovery command with a short timeout."""
    try:
        proc = subprocess.run(cmd, cwd=str(AUTOMIND_WORKSPACE_ROOT), text=True, capture_output=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return 124, str(exc)


def _android_adb_candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    for env_name in ["ANDROID_HOME", "ANDROID_SDK_ROOT"]:
        raw = os.environ.get(env_name)
        if raw:
            candidates.append(Path(raw) / "platform-tools" / "adb")
    candidates.extend([
        Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / "adb",
        Path.home() / "Android" / "Sdk" / "platform-tools" / "adb",
        Path("/opt/homebrew/bin/adb"),
        Path("/usr/local/bin/adb"),
    ])
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def _adb_path_from_running_server() -> str | None:
    """Best-effort macOS fallback: locate adb binary backing a running server.

    Some user shells/agent processes do not have platform-tools in PATH while
    Android Studio has already started an adb server. In that case `which adb`
    fails even though the device bridge is available. `lsof -d txt` can expose
    the executable path without modifying device state; if unavailable we just
    skip this fallback.
    """
    try:
        proc = subprocess.run(["pgrep", "-x", "adb"], text=True, capture_output=True, timeout=2)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    for raw_pid in proc.stdout.splitlines():
        pid = raw_pid.strip()
        if not pid.isdigit():
            continue
        try:
            info = subprocess.run(["lsof", "-a", "-p", pid, "-d", "txt", "-Fn"], text=True, capture_output=True, timeout=2)
        except Exception:
            continue
        for line in info.stdout.splitlines():
            if not line.startswith("n"):
                continue
            path = line[1:]
            if path.endswith("/adb") and Path(path).exists():
                return path
    return None


def resolve_adb_command() -> tuple[list[str] | None, dict]:
    """Resolve adb even when the coding-agent shell PATH is incomplete."""
    diagnostics: dict = {"strategy": None, "checked": []}
    found = shutil.which("adb")
    if found:
        diagnostics.update({"strategy": "PATH", "adbPath": found})
        return [found], diagnostics

    for path in _android_adb_candidate_paths():
        diagnostics["checked"].append(str(path))
        if path.exists():
            diagnostics.update({"strategy": "known_sdk_path", "adbPath": str(path)})
            return [str(path)], diagnostics

    running = _adb_path_from_running_server()
    if running:
        diagnostics.update({"strategy": "running_adb_server_executable", "adbPath": running})
        return [running], diagnostics

    diagnostics.update({
        "strategy": "not_found",
        "error": "adb binary not found in PATH, Android SDK env vars, common SDK locations, or running adb server executable",
    })
    return None, diagnostics


def discover_android_physical_devices_with_diagnostics() -> dict:
    adb_cmd, diagnostics = resolve_adb_command()
    if not adb_cmd:
        return {"devices": [], "diagnostics": diagnostics}
    code, output = run_short_device_command([*adb_cmd, "devices", "-l"], timeout=5)
    diagnostics.update({"exitCode": code, "rawOutput": output[-2000:]})
    if code != 0:
        diagnostics["error"] = "adb devices failed"
        return {"devices": [], "diagnostics": diagnostics}

    devices: list[dict] = []
    non_ready: list[dict] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0].lower().startswith("list"):
            continue
        serial, state = parts[0], parts[1]
        attrs: dict[str, str] = {}
        for token in parts[2:]:
            if ":" in token:
                key, value = token.split(":", 1)
                attrs[key] = value
        model = attrs.get("model") or attrs.get("device") or "Android device"
        item = {
            "platform": "android",
            "id": serial,
            "name": model,
            "state": state,
            "detail": " ".join(parts[2:]),
        }
        if state == "device" and not serial.startswith("emulator-"):
            devices.append(item)
        elif not serial.startswith("emulator-"):
            non_ready.append(item)
    diagnostics["nonReadyDevices"] = non_ready
    if not devices and non_ready:
        diagnostics["error"] = "physical device(s) detected but not ready/authorized"
    elif not devices:
        diagnostics["error"] = "adb found but no authorized physical device listed"
    return {"devices": devices, "diagnostics": diagnostics}


def discover_android_physical_devices() -> list[dict]:
    return discover_android_physical_devices_with_diagnostics().get("devices", [])


def discover_ios_physical_devices() -> list[dict]:
    if not shutil.which("xcrun"):
        return []
    devices: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="automind-devices-") as tmp:
        json_path = Path(tmp) / "devicectl-devices.json"
        code, output = run_short_device_command(
            ["xcrun", "devicectl", "list", "devices", "--json-output", str(json_path)],
            timeout=6,
        )
        if code == 0 and json_path.exists():
            try:
                data = json.loads(json_path.read_text())
                for item in ((data.get("result") or {}).get("devices") or []):
                    props = item.get("deviceProperties") or {}
                    hw = item.get("hardwareProperties") or {}
                    conn = item.get("connectionProperties") or {}
                    identifier = item.get("identifier") or hw.get("udid")
                    if not identifier:
                        continue
                    devices.append({
                        "platform": "ios",
                        "id": identifier,
                        "name": props.get("name") or hw.get("productType") or "iOS device",
                        "detail": " ".join(filter(None, [
                            str(props.get("osVersionNumber") or ""),
                            str(conn.get("transportType") or ""),
                            str(props.get("developerModeStatus") or ""),
                        ])).strip(),
                    })
            except Exception:
                devices = []
        if devices:
            return devices

    code, output = run_short_device_command(["xcrun", "xctrace", "list", "devices"], timeout=6)
    if code != 0:
        return []
    in_simulators = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if "simulator" in lower and line.startswith("=="):
            in_simulators = True
            continue
        if line.startswith("=="):
            in_simulators = False
            continue
        if in_simulators or "simulator" in lower:
            continue
        match = re.match(r"(.+?)\s+\(([^()]*)\)\s+\(([^()]*)\)", line)
        if match:
            devices.append({
                "platform": "ios",
                "id": match.group(3).strip(),
                "name": match.group(1).strip(),
                "detail": match.group(2).strip(),
            })
    return devices


def discover_connected_physical_devices(task_type: str) -> dict:
    """Return current read-only physical-device discovery for mobile review gates."""
    mock = os.environ.get("AUTOMIND_MOCK_CONNECTED_DEVICES")
    if mock is not None:
        try:
            raw = json.loads(mock)
            devices = raw if isinstance(raw, list) else raw.get("devices", [])
            return {"devices": devices if isinstance(devices, list) else [], "mocked": True}
        except Exception:
            return {"devices": [], "mocked": True, "error": "invalid AUTOMIND_MOCK_CONNECTED_DEVICES"}

    platforms = ["ios", "android"] if task_type == "dual" else [task_type]
    devices: list[dict] = []
    diagnostics: dict = {}
    if "android" in platforms:
        android_result = discover_android_physical_devices_with_diagnostics()
        devices.extend(android_result.get("devices") or [])
        diagnostics["android"] = android_result.get("diagnostics") or {}
    if "ios" in platforms:
        ios_devices = discover_ios_physical_devices()
        devices.extend(ios_devices)
        diagnostics["ios"] = {"deviceCount": len(ios_devices)}
    return {"devices": devices, "mocked": False, "diagnostics": diagnostics}


def format_detected_devices(devices: list[dict]) -> str:
    items = []
    for idx, device in enumerate(devices, start=1):
        if not isinstance(device, dict):
            items.append(f"Device {idx}")
            continue
        platform = str(device.get("platform") or "").upper().strip()
        name = device.get("name") or device.get("label") or device.get("udid") or device.get("serial") or f"device {idx}"
        label = f"{platform + ' ' if platform else ''}{name}"
        identifier = device.get("id") or device.get("udid") or device.get("serial")
        detail = device.get("detail")
        if identifier and str(identifier) not in str(label):
            label += f" ({identifier})"
        if detail:
            label += f" [{detail}]"
        items.append(label)
    return "; ".join(items)


def format_device_discovery_diagnostic(device_discovery: dict) -> str:
    diagnostics = device_discovery.get("diagnostics") if isinstance(device_discovery.get("diagnostics"), dict) else {}
    android = diagnostics.get("android") if isinstance(diagnostics.get("android"), dict) else {}
    if not android:
        return ""
    adb_path = android.get("adbPath")
    error = android.get("error")
    non_ready = android.get("nonReadyDevices") if isinstance(android.get("nonReadyDevices"), list) else []
    parts: list[str] = []
    if adb_path:
        parts.append(f"adb={adb_path}")
    elif android.get("strategy") == "not_found":
        parts.append("adb binary not found in AutoMind/Codex environment")
    if non_ready:
        states = ", ".join(f"{item.get('id')}:{item.get('state')}" for item in non_ready if isinstance(item, dict))
        if states:
            parts.append(f"non-ready device states: {states}")
    if error:
        parts.append(str(error))
    return "; ".join(parts)


def mentions_hardstop_device_unavailable_phrasing_detail(user_input: str) -> dict[str, Any]:
    """Rich model-first variant: detect "real-device unavailable" constraints.

    Returns a dict with:
      mentionsUnavailable: bool — the thin-wrapper return value.
      triageSource: "code_deterministic" when an explicit anchor+unavailable
          pair matched (high confidence the user said device is unavailable),
          OR when no device anchor word appears at all (confidently no such
          claim). "requires_model_review" when a device anchor word appears
          near a possibly-negative context but no explicit pair matched, since
          natural-language negation is easy to miss.
      needsModelReview: True iff a device anchor appeared without a clean pair.
      matchedKeyword: which bucket fired.
      reason: short human-readable description.
    """
    if not user_input:
        return {
            "mentionsUnavailable": False, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "empty_input",
            "reason": "空输入，没有任何设备不可用表述。",
        }
    lower = user_input.lower()
    cn_unavail = ["不可用", "不能用", "无法使用", "用不了", "没真机", "无真机", "无可用"]
    en_unavail = [
        "unavailable",
        "not available",
        "no available",
        "cannot use",
        "can not use",
        "no real device",
        "no physical device",
        "without a real device",
        "without real device",
    ]
    cn_anchor_tokens = ["真机", "物理机", "实体设备"]
    en_anchor_tokens = ["real device", "physical device"]
    has_cn_anchor = any(token in user_input for token in cn_anchor_tokens)
    has_en_anchor = any(token in lower for token in en_anchor_tokens)
    has_cn_pair = has_cn_anchor and any(token in user_input for token in cn_unavail)
    has_en_pair = has_en_anchor and any(token in lower for token in en_unavail)
    if has_cn_pair or has_en_pair:
        return {
            "mentionsUnavailable": True, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "anchor_unavailable_pair",
            "reason": "检出真机/physical device 锚点词与不可用否定词成对出现，明确表达真机不可用。",
        }
    if has_cn_anchor or has_en_anchor:
        return {
            "mentionsUnavailable": False, "triageSource": "requires_model_review",
            "needsModelReview": True, "matchedKeyword": "anchor_without_pair",
            "reason": "出现真机/physical device 锚点词但没匹配到成对的不可用否定词；自然语言否定表达多样（如“手头没设备”“暂时连不上手机”），模型应确认用户是否其实在表达真机不可用。",
        }
    return {
        "mentionsUnavailable": False, "triageSource": "code_deterministic",
        "needsModelReview": False, "matchedKeyword": "no_device_anchor",
        "reason": "完全没有真机/physical device 锚点词，确定不是真机不可用表述。",
    }


def mentions_hardstop_device_unavailable_phrasing(user_input: str) -> bool:
    """Return True when the user says real-device verification is unavailable.

    Users may mention "真机不可用" / "no real device" as a constraint. Do not
    misread the bare token "真机" / "real device" as a positive request to use
    a real device. Current policy is real-device preferred, then
    simulator/emulator by default when real-device verification is unavailable;
    ask_user only for multiple-device selection, no runnable dynamic fallback,
    or separate sensitive actions.

    Thin wrapper over mentions_hardstop_device_unavailable_phrasing_detail;
    callers needing triage metadata should use the _detail variant.
    """
    return mentions_hardstop_device_unavailable_phrasing_detail(user_input)["mentionsUnavailable"]


def mobile_task_needs_verification_target_review_detail(user_input: str, task_type: str | None = None) -> dict[str, Any]:
    """Rich model-first variant: decide whether the early mobile target review
    should run (i.e. ask the user real-device vs simulator/emulator).

    Returns a dict with:
      needsReview: bool — the thin-wrapper return value.
      triageSource: "code_deterministic" when a hard structural fact decides it
          (non-mobile, not a client task, explicit unavailable pair, or an
          explicit device-target keyword already present).
          "requires_model_review" for the fallback "no target keyword found ->
          assume unclear", because absence of keywords is not proof the user
          failed to express a target.
      needsModelReview: True iff the positive review trigger came from the
          weak "no keyword" fallback.
      matchedKeyword: which bucket fired.
      reason: short human-readable description.
    """
    text = (user_input or "").lower()
    resolved_task_type = task_type or infer_task_type(user_input)
    if resolved_task_type not in {"ios", "android", "dual"}:
        return {
            "needsReview": False, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "non_mobile_task_type",
            "reason": "非移动端任务，不需要做移动验证目标审查。",
        }
    if not is_client_development_or_verification_task(user_input, resolved_task_type):
        return {
            "needsReview": False, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "not_client_task",
            "reason": "不是客户端开发/验证任务，不需要移动验证目标审查。",
        }
    # If the user mentions "真机不可用" / "device unavailable", do NOT treat
    # that as a positive real-device choice. Keep the mobile target review
    # active so AutoMind can record the simulator/emulator fallback policy.
    if mentions_hardstop_device_unavailable_phrasing(user_input):
        return {
            "needsReview": True, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "device_unavailable",
            "reason": "用户表达真机不可用，需保留审查以记录模拟器/仿真器 fallback 策略。",
        }
    if any(keyword in text for keyword in [
        "真机", "物理机", "实体设备", "模拟器", "仿真器",
        "real device", "physical device", "simulator", "emulator",
    ]):
        return {
            "needsReview": False, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "explicit_target",
            "reason": "用户已明确提到真机或模拟器验证目标，无需再追问。",
        }
    return {
        "needsReview": True, "triageSource": "requires_model_review",
        "needsModelReview": True, "matchedKeyword": "no_target_keyword",
        "reason": "没有命中真机/模拟器关键词就判定“目标不清”，这是反向弱启发式；用户可能用其它措辞已表达过目标，模型应结合上下文确认是否真的需要追问。",
    }


def mobile_task_needs_verification_target_review(user_input: str, task_type: str | None = None) -> bool:
    """Return True when the early mobile target review should run. Thin wrapper
    over mobile_task_needs_verification_target_review_detail; callers needing
    triage metadata should use the _detail variant."""
    return mobile_task_needs_verification_target_review_detail(user_input, task_type)["needsReview"]


def mobile_verification_mentions_simulator_only_detail(user_input: str, task_type: str | None = None) -> dict[str, Any]:
    """Rich model-first variant: detect an explicit simulator/emulator-only choice.

    Returns a dict with:
      simulatorOnly: bool — the thin-wrapper return value.
      triageSource: "code_deterministic" for structural no-ops and for the
          clean "simulator present, real-device absent" signal.
          "requires_model_review" when both simulator and real-device keywords
          appear (mixed/sequenced intent like "先模拟器跑通再上真机"), because the
          simple boolean combination would silently drop the real-device part.
      needsModelReview: True iff mixed simulator+real-device signals appeared.
      matchedKeyword: which bucket fired.
      reason: short human-readable description.
    """
    text = (user_input or "").lower()
    resolved_task_type = task_type or infer_task_type(user_input)
    if resolved_task_type not in {"ios", "android", "dual"}:
        return {
            "simulatorOnly": False, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "non_mobile_task_type",
            "reason": "非移动端任务，无所谓模拟器选择。",
        }
    if not is_client_development_or_verification_task(user_input, resolved_task_type):
        return {
            "simulatorOnly": False, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "not_client_task",
            "reason": "不是客户端任务，无所谓模拟器选择。",
        }
    has_simulator = any(keyword in text for keyword in ["模拟器", "仿真器", "simulator", "emulator"])
    has_real_device = any(keyword in text for keyword in ["真机", "物理机", "实体设备", "real device", "physical device"])
    if has_simulator and has_real_device:
        return {
            "simulatorOnly": False, "triageSource": "requires_model_review",
            "needsModelReview": True, "matchedKeyword": "mixed_simulator_and_real",
            "reason": "同时出现模拟器与真机关键词（可能是“先模拟器跑通再上真机”这类混合/有先后顺序的表达），纯布尔组合会误读，模型应确认用户真正的验证目标。",
        }
    if has_simulator:
        return {
            "simulatorOnly": True, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "simulator_only",
            "reason": "只出现模拟器/仿真器关键词、无真机关键词，明确为模拟器优先。",
        }
    return {
        "simulatorOnly": False, "triageSource": "code_deterministic",
        "needsModelReview": False, "matchedKeyword": "no_simulator_signal",
        "reason": "没有模拟器关键词，不是模拟器-only。",
    }


def mobile_verification_mentions_simulator_only(user_input: str, task_type: str | None = None) -> bool:
    """Return True when a mobile task explicitly selects simulator/emulator only.

    NOTE: When the user explicitly chose simulator/emulator, AutoMind respects
    that choice. We still record this signal so audit logs/reuse can show the
    decision, but it MUST NOT escalate to ``ask_user`` — see the
    "follow-user-stated-target" policy in AGENTS.md / docs/workflow.md.

    Thin wrapper over mobile_verification_mentions_simulator_only_detail;
    callers needing triage metadata should use the _detail variant.
    """
    return mobile_verification_mentions_simulator_only_detail(user_input, task_type)["simulatorOnly"]


def detect_brainstorm_questions_detail(user_input: str) -> dict[str, Any]:
    """Rich model-first variant: seed brainstorm clarification questions plus triage.

    Returns a dict with:
      questions: list[str] — the thin-wrapper return value.
      triageSource: always "requires_model_review" — these are only keyword-
          seeded hints. Whether any of them actually blocks implementation is
          the Phase 2 model refiner's decision, not a code-deterministic fact.
      needsModelReview: always True — the model owns the final question set.
      seededReasons: list[str] — which keyword buckets fired, for auditing.
      reason: short human-readable description.
    """
    questions: list[str] = []
    seeded_reasons: list[str] = []
    text = user_input.lower()
    has_verify = bool(extract_user_verify_command(user_input))
    task_type = infer_task_type(user_input)
    if not has_verify and not any(k in text for k in ["\u9a8c\u8bc1", "test", "pytest", "npm test", "\u622a\u56fe", "\u542f\u52a8", "build"]):
        questions.append("Verification method is unclear: confirm whether to use a script command, platform runner, screenshots/logs, or human acceptance.")
        seeded_reasons.append("verification_method_unclear")
    if mobile_task_needs_verification_target_review(user_input, task_type):
        questions.append(
            "Mobile verification target is unclear: should AutoMind verify on a real physical device or on a simulator/emulator? "
            "Recommended: use a real device for device-specific behavior and a simulator/emulator for faster smoke checks when acceptable. Real-device verification may require user authorization such as trusting the computer, enabling Developer Mode/USB debugging, unlocking the device, and approving signing or permission prompts."
        )
        seeded_reasons.append("mobile_target_unclear")
    if any(k in text for k in ["\u4f18\u5316", "\u5b8c\u5584", "\u6539\u8fdb", "\u66f4\u597d"]) and not any(k in text for k in ["\u6307\u6807", "\u6807\u51c6", "\u901a\u8fc7", "\u5931\u8d25"]):
        questions.append("Success criteria are not specific enough: confirm what should count as pass.")
        seeded_reasons.append("success_criteria_vague")
    if any(k in text for k in ["\u767b\u5f55", "\u652f\u4ed8", "\u5220\u9664", "\u4e0a\u4f20", "\u8054\u7f51"]) and "\u6388\u6743" not in text:
        questions.append("This may involve state changes or external actions: confirm authorization scope and safety boundaries.")
        seeded_reasons.append("possible_sensitive_action")
    return {
        "questions": questions,
        "triageSource": "requires_model_review",
        "needsModelReview": True,
        "seededReasons": seeded_reasons,
        "reason": "这些只是基于关键词种子化的澄清问题；哪些假设真正阻塞实现由 Phase 2 model refiner 决定。",
    }


def detect_brainstorm_questions(user_input: str) -> list[str]:
    """Detect lightweight clarification questions before freezing Spec.

    Brainstorm is an active design review artifact. This helper only seeds
    likely questions; the Phase 2 model refiner owns the final decision about
    which assumptions block implementation.

    Thin wrapper over detect_brainstorm_questions_detail; callers needing
    triage metadata should use the _detail variant.
    """
    return detect_brainstorm_questions_detail(user_input)["questions"]


def build_pre_implementation_review_state(user_input: str, questions: list[str] | None = None, *, scaffold_mode: bool = False) -> dict:
    """Return the default review state for Phase 2.

    In deterministic scaffold mode this must not become a user gate. The
    scaffold is a fully automated bootstrap surface: it records early risk and
    review hints, then lets the Phase 2 Refiner / formal pre-implementation
    review ask the one bundled user question if needed.

    Outside scaffold mode, this helper still describes the formal review
    policy used by smokes and legacy paths.
    """
    questions = questions if questions is not None else detect_brainstorm_questions(user_input)
    text = user_input.lower()
    # Keep this intentionally generic. Domain-specific uncertainty should be
    # judged by the Phase 2 model planner from project/request context, not by
    # keyword lists in core code.
    high_risk_keywords = [
        "支付", "充值", "退款", "删除", "下线", "发布", "上线", "上传",
        "联网", "登录", "账号", "隐私", "权限", "生产", "prod",
        "payment", "delete", "remove account", "publish", "release",
        "upload", "login", "credential", "privacy", "production",
    ]
    vague_keywords = ["优化", "完善", "改进", "更好", "提升", "better", "improve", "optimize"]
    implementation_keywords = [
        "实现", "新增", "修改", "修复", "开发", "构建", "创建", "接入", "重构", "优化", "完善", "改进",
        "implement", "add", "modify", "change", "fix", "build", "create", "integrate", "refactor", "update", "improve",
    ]
    verify_only_keywords = [
        "验证", "检查", "运行", "分析", "总结", "status", "verify", "test", "check", "inspect", "analyze", "summarize",
    ]
    explicit_auto_keywords = [
        "一站到底", "全自动模式", "全自动", "不用问用户", "不用问我", "不用确认", "无需确认", "不要问", "直接实现",
        "full auto", "full-auto", "fully automatic", "no confirmation", "do not ask", "auto proceed",
    ]
    has_verify = bool(extract_user_verify_command(user_input))
    has_high_risk = any(keyword in text for keyword in high_risk_keywords)
    looks_like_implementation = any(keyword in text for keyword in implementation_keywords)
    looks_verify_only = any(keyword in text for keyword in verify_only_keywords) and not looks_like_implementation
    explicit_auto = any(keyword in text for keyword in explicit_auto_keywords)
    is_vague = any(keyword in text for keyword in vague_keywords) and not any(
        keyword in text for keyword in ["指标", "标准", "通过", "失败", "acceptance", "criteria", "verify"]
    )

    blockers: list[str] = []
    review_questions: list[str] = list(questions)
    if questions:
        blockers.append("Brainstorm.md has clarification questions that may affect scope or verification.")
    if looks_like_implementation and not explicit_auto:
        blockers.append("Non-trivial implementation work requires one pre-implementation user confirmation unless the user explicitly requested full-auto/no-confirmation mode.")
        review_questions.append(
            "Please review the key planning artifacts before approving — especially Requirements.md and TestCases.md (plus Brainstorm.md/Plan.md). A wrong requirement or test design sends the whole route off course and wastes all later development and verification. Then confirm the one-shot pre-implementation decision bundle: whether the requirement is clear, goal/scope/non-goals, recommended approach, key assumptions, known risks, verification direction, must-pass acceptance criteria/TestCases/evidence, rollback/replan boundaries, and authorization for any non-low-risk operations."
        )
    if has_high_risk:
        blockers.append("Request may involve account, production, external, destructive, or sensitive side effects.")
        review_questions.append(
            "Confirm the safe implementation scope and authorization boundaries before code changes."
        )
    if is_vague and not has_verify:
        blockers.append("Success criteria are vague and no explicit verification command was provided.")
        review_questions.append("Confirm the concrete success criteria and verification evidence before implementation.")
    task_type = infer_task_type(user_input)
    mobile_target_unclear = mobile_task_needs_verification_target_review(user_input, task_type)
    simulator_only_requested = mobile_verification_mentions_simulator_only(user_input, task_type)
    should_discover_devices = (
        is_client_development_or_verification_task(user_input, task_type)
        and task_type in {"ios", "android", "dual"}
    )
    device_discovery = discover_connected_physical_devices(task_type) if should_discover_devices else {"devices": [], "mocked": False}
    connected_devices = device_discovery.get("devices") or []
    connected_device_text = format_detected_devices(connected_devices)
    device_diagnostic_text = format_device_discovery_diagnostic(device_discovery)
    connected_device_count = len(connected_devices)
    if mobile_target_unclear:
        review_questions = [
            question for question in review_questions
            if "Mobile verification target is unclear" not in question
        ]
        if connected_device_count == 1:
            review_questions.append(
                "Real-device verification is the default for this client/app task. "
                f"AutoMind detected one connected physical device ({connected_device_text}) and will use it by default for development, debugging, verification, and screenshot evidence unless the user explicitly chooses another target. "
                "Screenshot capture is default allowed verification evidence; separate ask_user is still required for sensitive actions such as uninstall, re-signing/signing-material changes, sudo/tunneld, keychain/certificate/private-key changes, account/payment/production/external-upload actions, or unrelated data deletion."
            )
        elif connected_device_count > 1:
            blockers.append("Multiple connected real devices detected; user must choose the target device before implementation.")
            review_questions.append(
                "Multiple connected physical devices were detected for this client/app task: "
                f"{connected_device_text}. "
                "Please choose which real device AutoMind should use for development, debugging, verification, and screenshot evidence. "
                "Screenshot capture is default allowed verification evidence; separate ask_user is still required for sensitive actions such as uninstall, re-signing/signing-material changes, sudo/tunneld, keychain/certificate/private-key changes, account/payment/production/external-upload actions, or unrelated data deletion."
            )
        else:
            diagnostic_sentence = f" Discovery diagnostic: {device_diagnostic_text}." if device_diagnostic_text else ""
            review_questions.append(
                "Real-device verification is preferred for this client/app task, but no authorized connected physical device was detected by AutoMind. "
                f"{diagnostic_sentence} "
                "AutoMind will try simulator/emulator verification by default when real-device verification is unavailable. "
                "Screenshot capture is default allowed verification evidence once a runnable target exists. "
                "Ask_user is still required only for sensitive actions such as uninstall, re-signing/signing-material changes, sudo/tunneld, keychain/certificate/private-key changes, account/payment/production/external-upload actions, unrelated data deletion, or static-only verification when no runnable simulator/emulator path exists."
            )
    # When the user explicitly chose simulator/emulator, AutoMind respects that
    # choice. We still record the signal in mobileVerification for audit/reuse,
    # but never escalate to ask_user even if connected devices are detected.
    # See "follow-user-stated-target" policy in AGENTS.md / docs/workflow.md.

    # Default mobile verification policy: iOS/Android real-device verification
    # and screenshot collection are normal verification behaviors, not
    # permission-worthy actions by themselves. AutoMind should prefer a
    # detected physical device automatically and continue without ask_user.
    # ask_user is reserved for meaningful downgrades (no device available and
    # must fall back to simulator/static-only) or separate sensitive actions
    # such as uninstall/re-sign/sudo/keychain/external effects handled by other
    # gates.
    is_client_runtime_task = (
        task_type in {"ios", "android", "dual"}
        and is_client_development_or_verification_task(user_input, task_type)
    )
    explicit_real_device_signal = any(
        keyword in user_input.lower()
        for keyword in ["真机", "物理机", "实体设备", "real device", "physical device"]
    ) and not mentions_hardstop_device_unavailable_phrasing(user_input)
    user_already_picked_target = bool(simulator_only_requested) or bool(explicit_real_device_signal)
    # The mobile_target_unclear block above owns no-device and multi-device
    # ask_user routing. This default-policy block only records the general rule:
    # screenshot evidence is allowed by default; target downgrades and sensitive
    # side effects are still governed by explicit user decisions.

    deduped_questions: list[str] = []
    seen_questions: set[str] = set()
    for question in review_questions:
        if question and question not in seen_questions:
            seen_questions.add(question)
            deduped_questions.append(question)

    decision = "ask_user" if blockers else "auto_proceed"
    # User explicitly said "全自动/一站到底/full auto": that alone suffices to
    # skip every ask_user gate — including high-risk signal and open questions.
    # "拦不拦截以用户诉求为准": the user's autonomy intent overrides every
    # deterministic blocker so the loop runs end-to-end without interruption.
    if explicit_auto:
        decision = "auto_proceed"
    if looks_verify_only and not has_high_risk and not questions and not is_vague:
        decision = "auto_proceed"
    if scaffold_mode:
        # Deterministic scaffold must never park the task for user input. It is
        # an automated bootstrap pass; any real user decision must be produced
        # later by the Phase 2 Refiner / formal pre-implementation review after
        # Requirements, TestCases, and Plan are coherent.
        decision = "auto_proceed"
    default_options = [
        {
            "id": "confirm_full_auto_mode",
            "label": "全自动模式 / Full auto mode",
            "impact": "Skip all ask_user gates and run to completion autonomously. Equivalent to saying 全自动/一站到底 in the original request. Confirmation of scope/approach/assumptions is implied; the loop will not ask again.",
        },
        {
            "id": "confirm_recommended_direction",
            "label": "Confirm recommended direction",
            "impact": "Continue to refine Requirements/TestCases/Plan, run workflow-check, then implement.",
        },
        {
            "id": "revise_scope_assumptions",
            "label": "Revise scope/assumptions",
            "impact": "Update Brainstorm/Spec before downstream requirements, tests, and implementation.",
        },
        {
            "id": "choose_alternative_approach",
            "label": "Choose alternative approach",
            "impact": "Regenerate downstream artifacts for a different technical path.",
        },
        {
            "id": "stop",
            "label": "Stop",
            "impact": "Do not implement this task.",
        },
    ]
    if mobile_target_unclear:
        simulator_option = {
            "id": "use_simulator_emulator",
            "label": "Confirm direction + use simulator/emulator fallback" if not connected_devices else "Confirm direction + use simulator/emulator",
            "impact": "Confirm the current implementation direction/scope/assumptions/AC-TC evidence bundle, then verify with simulator/emulator. Faster and easier setup, but evidence may not cover real-device hardware/OS/signing differences.",
        }
        real_device_option = {
            "id": "use_real_device",
            "label": "Confirm direction + use detected real device" if connected_devices else "Confirm direction + connect/use real device",
            "impact": (
                f"Confirm the current implementation direction/scope/assumptions/AC-TC evidence bundle, then use detected device(s): {connected_device_text}. Strongest evidence for device/OS/signing/permission/integration behavior; user authorization may still be required."
                if connected_devices
                else "Confirm the current implementation direction/scope/assumptions/AC-TC evidence bundle, then use a real device. Requires the user to connect/unlock/trust a device and may require Developer Mode, USB debugging, signing, or permission prompts before verification can proceed."
            ),
        }
        both_option = {
            "id": "use_both",
            "label": "Confirm direction + use both",
            "impact": "Confirm the current implementation direction/scope/assumptions/AC-TC evidence bundle, then verify with both real device and simulator/emulator. Highest confidence, but costs more setup and runtime.",
        }
        if connected_device_count > 1:
            device_options = []
            for idx, device in enumerate(connected_devices, start=1):
                label = str(device.get("label") or device.get("name") or device.get("udid") or device.get("serial") or f"Device {idx}") if isinstance(device, dict) else f"Device {idx}"
                device_options.append({
                    "id": f"use_real_device_{idx}",
                    "label": f"Confirm direction + use {label}",
                    "impact": f"Confirm the current implementation direction/scope/assumptions/AC-TC evidence bundle, then use this real device for development, debugging, verification, and screenshot evidence.",
                })
            default_options = device_options + [simulator_option, both_option]
        elif connected_device_count == 1:
            default_options = [real_device_option, simulator_option, both_option]
        else:
            default_options = [simulator_option, real_device_option, both_option]
        default_options.extend([
            {
                "id": "revise_scope_assumptions",
                "label": "Revise scope",
                "impact": "Clarify target/device constraints before implementation.",
            },
            {
                "id": "stop",
                "label": "Stop",
                "impact": "Do not implement this task.",
            },
        ])
    recommended_option = "confirm_recommended_direction"
    if mobile_target_unclear:
        recommended_option = "use_real_device"
        if connected_device_count > 1:
            recommended_option = "choose_real_device"
        elif connected_device_count == 0:
            recommended_option = "use_simulator_emulator"
    # One-shot DecisionBundle: a single confirmation surface that records every
    # decision the planner needs from the user before Generator edits product
    # code. Fields default to neutral/unknown values so the user can confirm
    # them in one pass; planner/evaluator then read them as the source of
    # truth instead of re-prompting per phase.
    explicit_real_device_requested = (
        any(keyword in user_input.lower() for keyword in ["真机", "物理机", "实体设备", "real device", "physical device"])
        and not mentions_hardstop_device_unavailable_phrasing(user_input)
    )
    explicit_both_targets_requested = any(
        keyword in user_input.lower()
        for keyword in ["真机和模拟器", "模拟器和真机", "real device and simulator", "simulator and real device", "both"]
    )
    if mobile_target_unclear:
        if connected_device_count == 1 and is_client_runtime_task and not simulator_only_requested:
            verification_target = "real_device"
        elif connected_device_count > 1 and is_client_runtime_task and not simulator_only_requested:
            verification_target = "real_device_pending_selection"
        elif connected_device_count == 0 and is_client_runtime_task and not simulator_only_requested:
            verification_target = "simulator_emulator"
        elif recommended_option == "use_real_device":
            verification_target = "real_device"
        elif recommended_option == "use_simulator_emulator":
            verification_target = "simulator_emulator"
        else:
            verification_target = "unknown"
    elif explicit_both_targets_requested:
        verification_target = "both"
    elif explicit_real_device_requested:
        verification_target = "real_device"
    elif simulator_only_requested:
        verification_target = "simulator_emulator"
    elif task_type in {"ios", "android", "dual"}:
        verification_target = "unknown"
    else:
        verification_target = "not_applicable"
    decision_bundle = {
        "verificationTarget": verification_target,
        "destructiveActionsAllowList": [],
        "visualFallbackPolicy": "measurable_first_then_ai_review_then_user_confirm",
        "requiredTC": [],
        "requiredAC": [],
        "mustPassEvidence": [],
        "simulatorOnlyConfirmed": bool(simulator_only_requested),
        "askOnDeviceMissing": False,
        "requiresDeviceSelection": bool(is_client_runtime_task and connected_device_count > 1 and not simulator_only_requested),
        "deviceSelectionPolicy": (
            "default_single_connected_real_device" if is_client_runtime_task and connected_device_count == 1 and not simulator_only_requested else
            "ask_user_choose_connected_real_device" if is_client_runtime_task and connected_device_count > 1 and not simulator_only_requested else
            "default_simulator_emulator_when_real_device_unavailable" if is_client_runtime_task and connected_device_count == 0 and not simulator_only_requested else
            "user_requested_simulator_emulator" if simulator_only_requested else
            "not_applicable"
        ),
        "defaultDevice": connected_devices[0] if is_client_runtime_task and connected_device_count == 1 and not simulator_only_requested else None,
        "screenshotEvidenceDefaultAllowed": bool(is_client_runtime_task),
        "realDeviceFallbackPolicy": (
            "try_simulator_emulator_by_default" if is_client_runtime_task and connected_device_count == 0 and not simulator_only_requested else
            "use_single_connected_real_device" if is_client_runtime_task and connected_device_count == 1 and not simulator_only_requested else
            "ask_user_choose_real_device" if is_client_runtime_task and connected_device_count > 1 and not simulator_only_requested else
            "user_selected_simulator_emulator" if simulator_only_requested else
            "not_applicable"
        ),
        # Runtime-proof contract for client/app behavior tasks. Default policy:
        # iOS / Android client task that changes runtime behavior MUST have at
        # least one runtime/device/simulator TC pass before completion-check
        # can pass. Planner may downgrade only with explicit user
        # approval recorded under runtimeDowngradeApproval. See completion.py
        # gate `runtime_proof_gate` and workflow.py runtime-proof checks.
        "runtimeProofRequired": (
            "yes"
            if task_type in {"ios", "android", "dual"}
            and is_client_development_or_verification_task(user_input, task_type)
            else "auto"
        ),
        "runtimeDowngradeApproval": None,
        "taskType": task_type,
        "confirmedAt": None,
        "confirmedBy": None,
    }
    return {
        "required": True,
        "decision": decision,
        "needsUserInput": decision == "ask_user",
        "reason": (
            "Deterministic scaffold auto-approved; formal pre-implementation review must bundle any real user decisions after Phase 2 artifacts are refined."
            if scaffold_mode
            else (" ".join(blockers) if blockers else "Explicit full-auto/no-confirmation or low-risk verification-only/mechanical scope allows auto-proceed.")
        ),
        "questions": deduped_questions,
        # User-stated autonomy intent. `fullAuto` is the ground-truth signal that
        # the user explicitly asked AutoMind not to interrupt (一站到底/全自动/
        # 不用确认/full-auto/no-confirmation). The completion gate honors this as
        # an authoritative override so even a model-declared sensitive_hard_gate
        # is auto-handled instead of paused -- "拦不拦截以用户诉求为准".
        "fullAuto": bool(explicit_auto),
        "approvalScope": "brainstorm_spec_direction_scope_assumptions_approach_verification_direction_mobile_target_known_ac_tc_evidence",
        "recommendedOption": recommended_option,
        "options": default_options,
        "mobileVerification": {
            "clientTask": is_client_development_or_verification_task(user_input, task_type),
            "targetUnclear": mobile_target_unclear,
            "simulatorOnlyRequested": bool(simulator_only_requested),
            "simulatorOnlyRequestedWithConnectedDevice": bool(simulator_only_requested and connected_devices),
            "taskType": task_type,
            "connectedPhysicalDevices": connected_devices,
            "deviceDiscoveryMocked": bool(device_discovery.get("mocked")),
            "deviceDiscoveryDiagnostics": device_discovery.get("diagnostics") if isinstance(device_discovery.get("diagnostics"), dict) else {},
            "recommendedTarget": recommended_option if mobile_target_unclear else None,
        },
        "decisionBundle": decision_bundle,
        "defaultPolicy": "scaffold_auto_approved" if scaffold_mode else "ask_user_for_non_trivial_implementation",
        "source": "deterministic_scaffold" if scaffold_mode else "pre_implementation_review_policy",
        "checkedAt": datetime.now().isoformat(timespec="seconds"),
    }




def _shorten_for_ask_user(value: object, max_chars: int = 96) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _summarize_preimplementation_requirements(review: dict, language: str, limit: int = 6) -> list[str]:
    items: list[str] = []
    source = review.get("requirements") if isinstance(review.get("requirements"), list) else []
    if not source:
        source = review.get("requirementSummary") if isinstance(review.get("requirementSummary"), list) else []
    for item in source:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id") or item.get("requirementId") or "R??")
        title = item.get("title") or item.get("summary") or item.get("description") or item.get("text")
        acs = item.get("acceptanceCriteria") or item.get("acceptanceCriteriaRefs") or item.get("ac") or []
        if isinstance(acs, list):
            ac_ids = [str(ac.get("id") if isinstance(ac, dict) else ac) for ac in acs][:4]
        else:
            ac_ids = []
        suffix = f"（AC: {', '.join(ac_ids)}）" if ac_ids and language == "zh" else (f" (AC: {', '.join(ac_ids)})" if ac_ids else "")
        items.append(f"- {rid}: {_shorten_for_ask_user(title)}{suffix}")
        if len(items) >= limit:
            break
    return items


def _summarize_preimplementation_testcases(review: dict, language: str, limit: int = 8) -> list[str]:
    items: list[str] = []
    source = review.get("testCases") if isinstance(review.get("testCases"), list) else []
    if not source:
        source = review.get("requiredTestCases") if isinstance(review.get("requiredTestCases"), list) else []
    for item in source:
        if not isinstance(item, dict):
            continue
        required = item.get("required")
        if required is False:
            continue
        tid = str(item.get("id") or item.get("testCaseId") or "TC-??")
        title = item.get("title") or item.get("summary") or item.get("description") or item.get("name") or item.get("type")
        runtime = item.get("runtimeLevel") or item.get("runtime") or ""
        acs = item.get("acceptanceCriteriaRefs") or item.get("acceptanceCriteria") or []
        if isinstance(acs, list):
            ac_ids = [str(ac.get("id") if isinstance(ac, dict) else ac) for ac in acs][:5]
        else:
            ac_ids = []
        detail_parts = []
        if runtime:
            detail_parts.append(f"runtime={runtime}")
        if ac_ids:
            detail_parts.append(f"AC={', '.join(ac_ids)}")
        detail = f"（{'; '.join(detail_parts)}）" if detail_parts and language == "zh" else (f" ({'; '.join(detail_parts)})" if detail_parts else "")
        items.append(f"- {tid}: {_shorten_for_ask_user(title)}{detail}")
        if len(items) >= limit:
            break
    return items


def _enrich_review_with_planning_summaries(review: dict, task_dir: Path) -> dict:
    """Attach compact Requirements/TestCases data for pre-implementation ask_user."""
    enriched = dict(review or {})
    if not isinstance(enriched.get("requirements"), list):
        req_path = task_dir / "requirements.json"
        try:
            req_data = json.loads(req_path.read_text()) if req_path.exists() else {}
        except Exception:
            req_data = {}
        reqs = req_data.get("requirements") if isinstance(req_data.get("requirements"), list) else []
        enriched["requirements"] = [
            {
                "id": item.get("id"),
                "title": item.get("title") or item.get("description") or item.get("text"),
                "acceptanceCriteria": item.get("acceptanceCriteria") or item.get("acceptanceCriteriaRefs") or [],
            }
            for item in reqs
            if isinstance(item, dict)
        ]
    if not isinstance(enriched.get("testCases"), list):
        tc_path = task_dir / "testcases.json"
        try:
            tc_data = json.loads(tc_path.read_text()) if tc_path.exists() else {}
        except Exception:
            tc_data = {}
        cases = tc_data.get("testcases") if isinstance(tc_data.get("testcases"), list) else []
        enriched["testCases"] = [
            {
                "id": item.get("id"),
                "title": item.get("title") or item.get("summary") or item.get("description") or item.get("name") or (
                    (item.get("runbook") or {}).get("steps", [None])[0]
                    if isinstance(item.get("runbook"), dict) and isinstance((item.get("runbook") or {}).get("steps"), list) and (item.get("runbook") or {}).get("steps")
                    else item.get("type")
                ),
                "required": item.get("required"),
                "runtimeLevel": item.get("runtimeLevel"),
                "acceptanceCriteriaRefs": item.get("acceptanceCriteriaRefs") or item.get("acceptanceCriteria") or [],
            }
            for item in cases
            if isinstance(item, dict) and item.get("required") is not False
        ]
    return enriched


def format_pre_implementation_ask_question(review_questions: list[str], review: dict, user_input: object = None) -> str:
    """Format the one-shot pre-implementation decision bundle for TUI/CLI.

    User-facing ask_user text follows the language of the original `automind ask`
    request. Artifact names, commands, event names, and option IDs stay literal.
    Requirements/TestCases highlights are a review aid; they do not replace the
    one-shot decision bundle.
    """
    language = detect_preferred_language(
        user_input if user_input is not None else review.get("userInput") or review.get("originalUserInput") or review.get("request")
    )
    questions = [str(q).strip() for q in review_questions if str(q).strip()]
    if not questions:
        if language == "zh":
            questions = [
                "请确认是否按上述 Requirements/TestCases/Plan 和本次 decision bundle 进入实现；如果不同意，请说明要调整的需求、测试、事件口径、验证目标或授权边界。"
            ]
        else:
            questions = [
                "Please confirm whether AutoMind may proceed with the Requirements/TestCases/Plan and this decision bundle; if not, specify the requirement, testcase, event contract, verification target, or authorization boundary to change."
            ]
    reason = str(review.get("reason") or "").strip()
    approval_scope = str(review.get("approvalScope") or "goal/scope/approach/assumptions/verification/evidence").strip()
    requirement_lines = _summarize_preimplementation_requirements(review, language)
    testcase_lines = _summarize_preimplementation_testcases(review, language)

    if language == "zh":
        lines = [
            "预实现确认：AutoMind 在修改产品代码前需要一次性确认规划和授权。",
            "提示：如果你只想让 AutoMind 自主跑完全流程、不再打断你，请回复「全自动」或选择「全自动模式 / Full auto mode」选项。AutoMind 将把你说的范围、目标、授权一次性记录到 runtime-state，并在此后每个 completion gate 自动放行，不再询问。",
            "请先快速检查下面的 Requirements / TestCases 重点摘要；摘要只是帮助 review，不能替代 decision bundle 本身。完整细节仍以 Requirements.md、TestCases.md、Brainstorm.md、Plan.md 为准。",
        ]
        if requirement_lines:
            lines.extend(["", "Requirements 重点：", *requirement_lines])
        if testcase_lines:
            lines.extend(["", "Required TestCases 重点：", *testcase_lines])
        lines.extend([
            "",
            "请在确认上述重点后，继续确认本次 decision bundle：",
            "- 需求是否清楚，目标/范围/非目标是否正确；",
            "- 实现方向、关键假设、边界和可回滚性是否认可；",
            "- 已知风险是否可接受，是否需要先调整口径或 replan；",
            "- 必须通过的 AC / TestCase / 证据预期是否认可；",
            "- 验证目标（真机/模拟器/两者）和截图/日志/payload 等证据要求是否认可；",
            "- 是否授权任何非低风险操作；未列入授权的卸载、重签名、sudo、账号登录、外部上传、支付/删除/生产影响等仍需另行 ask_user；",
            "- 宿主 coding agent 可能因 sandbox 外命令或高风险命令请求你的 approval，请保持可响应或提前授予合适权限。",
        ])
        if reason:
            lines.append(f"- 原因：{reason}")
        lines.append(f"- 确认范围：{approval_scope}")
        lines.extend(["", "需要确认的问题/决策："])
    else:
        lines = [
            "Pre-implementation decision bundle: AutoMind needs one consolidated confirmation before changing product code.",
            "Tip: to let AutoMind run autonomously end-to-end without further interruption, reply \"full auto\" or select the \"全自动模式 / Full auto mode\" option. AutoMind records your stated scope/goals/authorization in runtime-state and auto-proceeds at every subsequent completion gate.",
            "First review the Requirements/TestCases highlights below. The highlights are only a review aid; they do not replace the decision bundle itself. Full details remain in Requirements.md, TestCases.md, Brainstorm.md, and Plan.md.",
        ]
        if requirement_lines:
            lines.extend(["", "Requirements highlights:", *requirement_lines])
        if testcase_lines:
            lines.extend(["", "Required TestCases highlights:", *testcase_lines])
        lines.extend([
            "",
            "After reviewing the highlights, confirm this decision bundle:",
            "- requirement clarity, goal/scope/non-goals;",
            "- implementation direction, key assumptions, boundaries, and rollback/replan expectations;",
            "- known risks and whether any event contract or plan should change first;",
            "- required AC/TestCase/evidence expectations;",
            "- verification target (real device / simulator / both) and screenshot/log/payload evidence expectations;",
            "- authorization for any non-low-risk operations; unlisted uninstall, re-signing, sudo, login, external upload, payment/delete/production-impacting actions still require a separate ask_user;",
            "- host coding-agent command approvals may interrupt the loop for sandbox-external or high-risk commands, so please stay available or grant the needed permission mode up front.",
        ])
        if reason:
            lines.append(f"- Reason: {reason}")
        lines.append(f"- Approval scope: {approval_scope}")
        lines.extend(["", "Questions / decisions to confirm:"])
    for idx, question in enumerate(questions, start=1):
        lines.append(f"{idx}. {question}")
    return "\n".join(lines)

def generate_brainstorm_md(task_dir: Path, user_input: str) -> str:
    """Generate Brainstorm.md for clarification and open assumptions."""
    path = task_dir / "Brainstorm.md"
    questions = detect_brainstorm_questions(user_input)
    review_state = build_pre_implementation_review_state(user_input, questions, scaffold_mode=True)
    display_questions = review_state.get("questions") or questions
    q_text = "\n".join(f"- Q{idx:02d}: {q}" for idx, q in enumerate(display_questions, start=1)) or "- No blocking questions detected by the deterministic scaffold; Phase 2 Refiner must still actively review assumptions, alternatives, ACs, required TCs, and evidence before implementation."
    option_lines = "\n".join(
        f"  - `{option.get('id')}` ({option.get('label', option.get('id'))}): {option.get('impact', '')}"
        for option in review_state.get("options", [])
    ) or "  - `confirm_recommended_direction`: continue to refine Require/TestCases/Plan, run workflow-check, then implement."
    review_policy = (
        "Ask the user and do not implement until the decision is resolved."
        if review_state["needsUserInput"]
        else "Auto-proceed is allowed; still keep this review decision visible for audit."
    )
    content = f"""# Brainstorm

## Purpose

Actively explore the request before freezing the Spec: project/context observations, assumptions, alternatives, recommendation, non-obvious risks, verification strategy, and decisions that need user confirmation. Brainstorm does not replace Spec; it prevents coding against an unvalidated technical plan.

## Original user input

{user_input}

## Clarification questions / decisions

{q_text}

## User intent digest

- Translate the user's words into: user/job goal, expected business or product outcome, scope/non-goals, success signal, affected user/system surface, and what should not change.
- Phase 2 Refiner must replace this scaffold with concrete interpretation before implementation.

## Project/context observations

- Repository instructions to check: `AGENTS.md`, `.agents.md`, `.cursor/rules`, `.github/copilot-instructions.md` when present.
- Docs/runbooks to check: `README*`, `docs/`, architecture notes, setup guides, CI workflow files.
- Scripts/tools to check: `scripts/`, `tools/`, `bin/`, `Makefile`, package-manager scripts, Gradle/Maven wrappers, Fastlane lanes, project-local helpers.
- Record project-specific constraints, preferred commands, verification entry points, existing domain concepts/business flows, and any scripts/docs intentionally ignored with reasons.
- Phase 2 Refiner must replace this scaffold with concrete repository findings before implementation.

## Business/product suggestions

- Note non-obvious product/business improvements, edge cases, UX/ops implications, or explicitly state why no extra business suggestion applies.

## Risk and opportunity register

- List product risk, technical risk, verification risk, rollout/rollback risk, and opportunity/upsell/quality improvements that should influence Requirements/TestCases/Plan.

## Approach options

- Provide 2-3 plausible approaches with trade-offs, then choose one.

## Recommendation

- State the recommended direction and why it fits the user goal and project context.

## Pre-implementation user review

- Decision: `{review_state['decision']}`
- Needs user input before code changes: `{str(review_state['needsUserInput']).lower()}`
- Reason: {review_state['reason']}
- Policy: {review_policy}
- Approval scope: review the key planning artifacts first — especially `Requirements.md` and `TestCases.md` (plus `Brainstorm.md`/`Plan.md`), since a wrong requirement or test design derails the whole route and wastes all downstream development and verification — then confirm the one-shot pre-implementation decision bundle before implementation, including whether the requirement is clear, goal, scope/non-goals, recommended approach, key assumptions, known risks, verification direction, any known must-pass acceptance criteria, required TestCases, evidence, rollback/replan boundaries, and authorization for non-low-risk operations such as overwrite install, uninstall/delete/reset, account login, signing/device trust changes, privilege escalation, external upload, payment, or production-impacting actions.
- Recommended option: `{review_state.get('recommendedOption', 'confirm_recommended_direction')}`
- Options:
{option_lines}

## User Decision Bundle (one-shot confirmation)

This bundle collects every decision the planner needs from the user in a
single confirmation pass. Planner/Evaluator must read these fields from
`runtime-state.json.planner.preImplementationReview.decisionBundle` instead of
re-prompting per phase. The user may confirm, override, or extend any field
before Generator edits product/runtime code.

- `verificationTarget`: `{review_state.get('decisionBundle', {}).get('verificationTarget', 'unknown')}` (real_device / simulator_emulator / both / not_applicable / unknown)
- `destructiveActionsAllowList`: `{review_state.get('decisionBundle', {}).get('destructiveActionsAllowList', [])}` (empty means no destructive actions are pre-authorized)
- `visualFallbackPolicy`: `{review_state.get('decisionBundle', {}).get('visualFallbackPolicy', 'measurable_first_then_ai_review_then_user_confirm')}`
- `requiredTC`: `{review_state.get('decisionBundle', {}).get('requiredTC', [])}` (must-pass TestCase IDs known up-front)
- `requiredAC`: `{review_state.get('decisionBundle', {}).get('requiredAC', [])}` (must-pass acceptance criterion IDs)
- `mustPassEvidence`: `{review_state.get('decisionBundle', {}).get('mustPassEvidence', [])}` (evidence artifacts that must be attached)
- `simulatorOnlyConfirmed`: `{str(review_state.get('decisionBundle', {}).get('simulatorOnlyConfirmed', False)).lower()}`
- `askOnDeviceMissing`: `{str(review_state.get('decisionBundle', {}).get('askOnDeviceMissing', False)).lower()}`
- `runtimeProofRequired`: `{review_state.get('decisionBundle', {}).get('runtimeProofRequired', 'auto')}` (yes / no / auto — iOS/Android client behavior tasks default to `yes`; downgrade requires explicit user approval)
- `runtimeDowngradeApproval`: `{review_state.get('decisionBundle', {}).get('runtimeDowngradeApproval') or 'none'}` (object with `approvedBy`, `approvedAt`, `reason`, `acceptedRiskCategories[]` when user explicitly approves a non-runtime finish)
- `taskType`: `{review_state.get('decisionBundle', {}).get('taskType', 'unknown')}` (ios / android / dual / script / other)
- `confirmedAt`: `{review_state.get('decisionBundle', {}).get('confirmedAt') or 'pending'}`
- `confirmedBy`: `{review_state.get('decisionBundle', {}).get('confirmedBy') or 'pending'}`

## Assumptions

- Initial deterministic scaffold may be refined by the Phase 2 Refiner before implementation.

## Proactive design expansion

- Phase 2 Refiner must replace this scaffold with project-specific context observations, 2-3 plausible approaches, trade-offs, a recommended approach, non-obvious edge cases, and verification risks before Generator work.
- If the recommendation, assumptions, must-pass ACs, required TCs, or evidence strategy need user confirmation, keep `Decision: ask_user` and do not edit product/runtime code.

## Current handling policy

- If blocking questions exist, ask the user first or mark the task as `replan/blocked`.
- If questions do not block progress, continue generating/refining Spec but record them under assumptions/constraints.
- If the user provided an explicit `verifyCommand/scriptCommand`, prefer the generic script harness and do not block on missing product details.
- After this pre-implementation gate is resolved, continue automatically through Generator implement/repair -> Evaluator verify/re-verify -> completion gate until finish is proven, `ask_user` needs a human decision, or an explicit unsafe/non-recoverable stop condition occurs.
"""
    path.write_text(content)
    update_runtime_state(
        task_dir,
        brainstorm=str(path),
        brainstormQuestionCount=len(questions),
        planner={
            "mode": "deterministic_scaffold",
            "artifactsRefined": False,
            "needsUserInput": review_state["needsUserInput"],
            "preImplementationReview": review_state,
            "notes": "Deterministic scaffold auto-approved; Phase 2 Refiner / formal pre-implementation review owns any real user decision before implementation.",
        },
    )
    log(f"Brainstorm.md generated: {path}")
    return str(path)

def generate_testcases_md(task_dir: Path, user_input: str) -> str:
    """Generate lightweight test cases linked to Spec requirement units."""
    test_path = task_dir / "TestCases.md"
    state = read_runtime_state(task_dir) or {}
    task_type = state.get("taskType") or infer_task_type(user_input)
    script_command = state.get("scriptCommand") or extract_user_verify_command(user_input)
    units = split_user_requirement_units(user_input)
    is_client_ui = infer_client_ui_task(user_input, task_type)
    ui_entry = infer_ui_entry_target(user_input) if is_client_ui else ""

    rows = []
    if script_command:
        rows.append(("TC-F01", "R01..Rn / AC-001", "Functional", "runtime", "shell + project dependencies + required fixture/input data", f"`{script_command}` or `<AUTOMIND_CLI> script-command <task-code> 1`", f"Prepare cwd/dependencies/fixtures -> run `{script_command}` from the intended project root -> assert exit code/stdout/stderr/output files/state changes match the acceptance criteria -> collect command/environment evidence.", "`logs/iter-1/commands.md`, `logs/iter-1/evaluator.log`, `logs/iter-1/env.json`; exit code 0; expected output/state observed; evaluation.json pass", "-", "yes"))
    elif task_type == "android":
        rows.append(("TC-F01", "R01..Rn / AC-001", "Functional", "device/runtime", "adb + Android device/emulator + optional `.venv-android-tools`; app/test account/fixture ready", "`<AUTOMIND_CLI> android-preflight <task-code> 1` then `<AUTOMIND_CLI> android-probe-flow <task-code> 1`", f"Prepare Android preflight -> build/test/install the target app if applicable -> launch/open `{ui_entry or 'the target activity/deep link/home or feature entry state'}` -> perform the required tap/input/navigation/API/background actions -> assert visible UI element/text, state/log/event/API result -> collect per-TC screenshot/UI hierarchy/logcat/probe-flow summary.", "preflight env log, command log, per-TC screenshot/UI hierarchy/logcat/probe-flow summary, evaluation.json", "-", "yes"))
    elif task_type == "ios":
        rows.append(("TC-F01", "R01..Rn / AC-001", "Functional", "device/runtime", "Xcode/xcodebuild + simulator/device + optional `.venv-ios-tools`; app/test account/fixture ready", "`<AUTOMIND_CLI> ios-preflight <task-code> 1` then `<AUTOMIND_CLI> ios-xcuitest <task-code> 1 ...` or project-native `xcodebuild test`", f"Prepare iOS preflight -> build/test/install or launch the app as applicable -> open `{ui_entry or 'the target screen/deep link/home or feature entry state'}` -> perform the required tap/input/navigation/API/background actions -> assert visible UI element/text, state/log/event/API result -> collect `.xcresult`, per-TC screenshot/attachment, and device/app logs.", "xcodebuild log, `.xcresult`, per-TC screenshot/attachment/device log when applicable, evaluation.json", "-", "yes"))
    else:
        rows.append(("TC-F01", "R01..Rn / AC-001", "Functional", "runtime", "project-native test/build/runtime command + fixture/input data", "Project-native test/build/runtime command; use `<AUTOMIND_CLI> script-command <task-code> 1` only after setting an explicit verifyCommand/scriptCommand", "Prepare dependencies/fixtures -> run the smallest project-native command or start the target program/server -> open/call the target entry point/page/API/CLI state when applicable -> perform the required action -> assert output/log/state/API response/UI element against ACs. Static inspection alone is not enough.", "test report/runtime output/screenshot when applicable; `logs/iter-1/commands.md`, `logs/iter-1/evaluator.log`, evaluation.json", "-", "yes"))

    if is_client_ui and task_type not in {"android", "ios"}:
        rows[0] = (
            rows[0][0],
            rows[0][1],
            rows[0][2],
            "runtime",
            "project-native build/test/runtime command + fixture/account/state + browser/UI automation when applicable",
            "Project-native UI/E2E/runtime command; use `<AUTOMIND_CLI> script-command <task-code> 1` only after setting an explicit verifyCommand/scriptCommand",
            f"Prepare dependencies/fixtures -> build/package or start the app/server if needed -> launch/open `{ui_entry}` -> perform the concrete user/tool actions required by the request -> assert visible UI element/text, output/log/state/API response, or crash-free behavior -> collect screenshot/log/test-report/runtime evidence.",
            "`logs/iter-1/commands.md`, `logs/iter-1/evaluator.log`, test report/runtime output/screenshot/UI evidence when applicable, evaluation.json",
            rows[0][8],
            rows[0][9],
        )

    next_idx = 2
    # Add smoke rows only when multiple requirement units exist. Keep non-key lightweight.
    for idx, _unit in enumerate(units[1:], start=2):
        rows.append((f"TC-S{idx:02d}", f"R{idx:02d}", "Smoke", "runtime", "same tools as TC-F01 unless refined", "reuse TC-F01 command or add a narrower project-native assertion", "Prepare the same fixture/state as TC-F01 -> run/open the narrower subpath for this requirement -> perform the minimal action -> assert the expected output/state/log/UI signal.", "command/test output and evaluator evidence under `logs/iter-N/`", "TC-F01", "yes"))
        next_idx = idx + 1

    quality_rows = [
        (f"TC-QP{next_idx:02d}", "R01..Rn", "Quality: Performance/UX", "runtime/static", "same runtime evidence as TC-F01 plus lightweight timing/log data when available", "`<AUTOMIND_CLI> quality-check <task-code> 1 --merge` after functional pass", "Collect key-path duration_ms, launch/first-screen/key-action duration, jank/wait/retry signs; mobile tasks may use logs, screenshot/hierarchy timestamps, or runner summary.", "duration has baseline or threshold; no obvious timeout/jank/loading hang; `logs/iter-1/quality-summary.json` or equivalent evidence", "TC-F01", "no"),
        (f"TC-QA{next_idx + 1:02d}", "R01..Rn", "Quality: Architecture", "static", "source tree + grep/AST/project tests when useful", "`<AUTOMIND_CLI> quality-check <task-code> 1 --merge` or focused static review after functional pass", "Inspect change scope, module boundaries, class/function call relationships, duplicate logic, responsibility leaks.", "architecture notes/quality-summary; no obvious coupling, cycles, or unmaintainable forks", "TC-F01", "no"),
    ]
    rows.extend(quality_rows)

    table = "\n".join(
        f"| {tc} | {req} | {kind} | {runtime} | {tools} | {command} | {method} | {expect} | {dep} | {required} |"
        for tc, req, kind, runtime, tools, command, method, expect, dep, required in rows
    )
    content = f"""# TestCases

## Design principles

- Key Path: covers the main flow and key AutoMind loop capabilities; failures deserve evidence analysis and repair.
- Smoke: non-critical coverage; one successful run and evidence is enough, no excessive polishing.
- Quality: beyond function itself, explicitly consider performance duration, interaction smoothness, stability, architecture boundaries, and class/function relationship sanity; explain when not applicable.
- Each TestCase must map to `Requirements.md` requirement units and acceptance criteria, and must produce evidence.
- Required functional/key-path cases should dynamically run the changed behavior whenever a runnable path exists. Static inspection alone is not enough for functional completion.
- Runtime/UI TestCases should capture screenshot evidence by default for each executed TC (Android screenshot/hierarchy, iOS XCUITest screenshot attachment/xcresult, or Web screenshot/trace). If a screenshot cannot be captured, evaluation/report output must say why and still attach machine-checkable logs/state evidence.
- If runtime verification is impossible, mark the case blocked/manual and record the missing command, fixture, environment, device, or approval needed.
- App/UI/client-facing runtime verification decision: {"yes" if is_client_ui else "no"}{f" (entry target: `{ui_entry}`; refine from project evidence if needed)" if is_client_ui else " (no app/UI signal in the request; use project-native unit/integration/runtime command unless the Phase 2 Refiner finds a UI surface)."}
- App/UI/client-facing tasks must explicitly decide whether verification requires build/package, install/deploy or server start, launch/open, and a UI/user journey flow.
- Each required functional testcase must be a mini runbook: prepare/preflight -> build/install/deploy/start if needed -> launch/open a precise entry target -> perform concrete actions -> assert concrete results -> collect evidence.
- For App/UI work, name the entry screen/page/route/activity/view/controller/state whenever it is knowable from the request or project. If it is not knowable, mark the testcase as blocked/replan/ask_user instead of writing a vague "verify UI works" row.
- Phase 2 Refiner must treat this scaffold as provisional. Before implementation,
  refine required cases from real project/request context and ask the user when
  the test target, entry point, assertions, or evidence strategy needs
  confirmation.

## Testcase list

| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / AutoMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |
|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|
{table}

## Composition policy

- One or more small tasks may be combined into one Key Path testcase.
- Non-critical small tasks should usually become Smoke cases; do not overbuild edge-state validation.
- Quality cases do not require heavy benchmarks every time, but should provide lightweight comparable evidence: duration_ms, key-stage duration, runner summary, log timestamps, static structure checks, or architecture notes.
- Quality standards should be lenient early: timing fluctuation without baseline and heuristic architecture concerns should be `warn`; clear timeout, hang, crash, severe coupling/cycle should become `fail`.
- If Quality fail triggers Generator fixes and changes runtime code, the next iteration must first rerun the selected/affected functional batch. This is selected-path regression, not necessarily the full product suite. After functional pass, rerun relevant Quality cases by category batch to avoid functional regression after every single quality case.
- Only clearly non-runtime changes such as docs, thresholds, or diagnostic text may skip functional regression, and evaluation.json must record the skip reason.
- If failure reveals platform/verifier issues, fix flow/evaluator first; if it is product implementation failure, send it to Generator.
- After the pre-implementation review gate is resolved, continue automatically
  through Generator implement/repair -> Evaluator verify/re-verify ->
  `completion-check` until finish is proven, `ask_user` needs a human decision,
  or an explicit unsafe/non-recoverable stop condition occurs.

## Next step

Evaluator selects the smallest necessary cases from `Requirements.md` acceptance criteria and this file. Platform flow/scripts are derived from TestCases verification methods. An iteration is not one testcase; it is one harness loop attempt: complete the selected functional batch first, do not insert quality after each functional testcase; if a prerequisite functional case fails, fail fast and mark dependent cases as not_run/skipped_dependency; only after the functional batch is acceptable should lightweight quality-check / relevant quality category batch run. Merge functional and quality results into one `evaluation.json` and `Validation.md`. TestCases prevents validation drift: if the validation target changes, update TestCases/Requirements first instead of improvising in the runner.
"""
    test_path.write_text(content)
    update_runtime_state(task_dir, testCases=str(test_path), testCasesVersion="v1")
    log(f"TestCases.md generated: {test_path}")
    return str(test_path)

# ============================================================
# \u6587\u6863\u751f\u6210
# ============================================================

def generate_requirements_md(task_dir: Path, user_input: str) -> str:
    """Generate the authoritative Requirements.md contract for a new task."""
    script_command = extract_user_verify_command(user_input)
    task_type = infer_task_type(user_input)
    if script_command:
        task_type = "script"
    harness = get_harness_profile(task_type)
    harness_md = ""
    if harness.get("primaryTools"):
        harness_md = f"""
## Harness Profile
- **Task type**: {task_type}
- **Recommended tools**: {', '.join(harness['primaryTools'])}
- **Fallback**: {', '.join(harness['fallbackTools']) if harness['fallbackTools'] else '-'}

### Preflight
"""
        for item in harness.get("preflight", []):
            harness_md += f"- {item}\n"
        harness_md += "\n### Recommended validation actions\n"
        for item in harness.get("recommendedActions", []):
            harness_md += f"- {item}\n"

    verification_command_md = f"## Verification Command\n`{script_command}`\n\n" if script_command else ""
    units = split_user_requirement_units(user_input)
    req_blocks: list[str] = []
    for idx, unit in enumerate(units or ["(initial scaffold)"], start=1):
        rid = f"R{idx:02d}"
        acid = f"AC-{idx:03d}"
        req_blocks.append(
            f"### {rid} — {unit}\n\n"
            f"- **{acid}**: Project can compile or run the selected verification successfully\n"
            f"  - Verification method: xcodebuild / gradle / Android SDK build chain / script command as applicable\n"
            f"  - Covered by: see TestCases.md\n"
            f"  - Timeout policy: Retry 3 times\n"
        )

    requirements_path = task_dir / "Requirements.md"
    requirements_md = (
        "# Requirements\n\n"
        "<!-- AutoMind canonical contract: Requirements.md is the single source of truth.\n"
        "     New tasks must not generate or depend on Spec.md / Require.md. -->\n\n"
        "## User Request\n\n"
        f"{user_input}\n\n"
        "## Task Type\n\n"
        f"`{task_type}`\n"
        f"{harness_md}{verification_command_md}"
        "## Non-goals / out of scope\n\n"
        "- Do not expand platform capabilities unrelated to the current user goal.\n"
        "- Do not exhaust every possible path; cover only the necessary key path or smoke checks.\n\n"
        "## Requirements with inline Acceptance Criteria\n\n"
        + "\n".join(req_blocks)
        + "\n## Definition of Done\n\n"
        "- Every Rxx has at least one inline AC-xxx referenced by a TestCase.\n"
        "- Evaluator produces `evaluation.json`; `record-check` passes; `summary.md` generated.\n"
    )
    requirements_path.write_text(requirements_md)
    update_runtime_state(task_dir, requirements=str(requirements_path), requirementsVersion="v1")
    log(f"Requirements.md generated: {requirements_path}")
    return str(requirements_path)

def infer_task_type_detail(user_input: str) -> dict[str, Any]:
    """Rich model-first variant: determine task type with triage metadata.

    Returns a dict with:
      taskType: "ios" | "android" | "dual" | "script"
      triageSource: "code_deterministic" when explicit platform keywords
          matched or workspace has a single unambiguous platform.
          "requires_model_review" when only weak signals exist.
      needsModelReview: True iff the caller should re-examine before committing.
      matchedKeyword: the concrete signal bucket that fired.
      reason: short human-readable description.
    """
    text = (user_input or "").lower()
    dual_keywords = [
        "双端", "ios和android", "ios/android", "android和ios",
        "安卓和ios", "ios和安卓", "cross-platform", "跨平台",
    ]
    ios_keywords = ["ios", "swift", "iphone", "ipad", "苹果真机", "苹果手机"]
    android_keywords = [
        "android", "安卓", "kotlin", "apk", "adb", "安卓真机", "android真机",
    ]
    if any(k in text for k in dual_keywords):
        return {
            "taskType": "dual", "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "dual_explicit",
            "reason": "用户明确提到双端或跨平台。",
        }
    if any(k in text for k in ios_keywords):
        return {
            "taskType": "ios", "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "ios_explicit",
            "reason": "用户明确提到 iOS/苹果设备。",
        }
    if any(k in text for k in android_keywords):
        return {
            "taskType": "android", "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "android_explicit",
            "reason": "用户明确提到 Android/安卓设备。",
        }
    workspace_platforms = infer_workspace_client_platforms()
    if workspace_platforms == {"ios"}:
        return {
            "taskType": "ios", "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "workspace_only_ios",
            "reason": "无显式平台关键词，但 workspace 全是 iOS。",
        }
    if workspace_platforms == {"android"}:
        return {
            "taskType": "android", "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "workspace_only_android",
            "reason": "无显式平台关键词，但 workspace 全是 Android。",
        }
    if {"ios", "android"}.issubset(workspace_platforms):
        return {
            "taskType": "script", "triageSource": "requires_model_review",
            "needsModelReview": True, "matchedKeyword": "workspace_both_ios_android",
            "reason": "workspace 同时检出 iOS 与 Android 项目，纯关键词无法决定默认走哪端，模型应再确认。",
        }
    weak_client_markers = [
        "app", "ui", "screen", "page", "frontend", "web", "browser",
        "mobile", "desktop", "route", "view",
    ]
    has_weak = bool(re.search(r"\b(" + "|".join(weak_client_markers) + r")\b", text)) or any(
        token in (user_input or "") for token in [
            "页面", "界面", "按钮", "前端", "客户端", "移动端",
            "浏览器", "屏幕", "路由", "视图", "入口",
        ]
    )
    if has_weak:
        return {
            "taskType": "script", "triageSource": "requires_model_review",
            "needsModelReview": True, "matchedKeyword": "weak_client_signal",
            "reason": "检出弱客户端/UI关键词但没有明确平台；可能是 client-ui 任务，模型应再确认。",
        }
    return {
        "taskType": "script", "triageSource": "code_deterministic",
        "needsModelReview": False, "matchedKeyword": "none",
        "reason": "没有检出平台或客户端相关关键词，默认脚本/后端/通用任务。",
    }


def infer_task_type(user_input: str) -> str:
    """根据用户输入推断Task type。Thin wrapper; callers needing triage metadata should use infer_task_type_detail."""
    return infer_task_type_detail(user_input)["taskType"]




def infer_client_ui_task_detail(user_input: str, task_type: str | None = None) -> dict[str, Any]:
    """Rich model-first variant: decide whether the request describes client/UI-facing work.

    Returns a dict with:
      isClientUi: bool
      triageSource: "code_deterministic" when strong keywords fired or explicit
          negative signal present. "requires_model_review" when ambiguous.
      needsModelReview: True iff the decision is heuristic enough to re-examine.
      matchedKeyword: human-readable label for which signal fired.
      reason: short human-readable description.
    """
    raw_task_type = (task_type or infer_task_type(user_input)).lower()
    has_negative_signal = has_negative_mobile_device_signal(user_input)
    if raw_task_type in {"ios", "android", "dual"} and not has_negative_signal:
        return {
            "isClientUi": True, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "task_type_" + raw_task_type,
            "reason": "任务类型已明确为客户端任务，且没有负向移动信号。",
        }
    lower = (user_input or "").lower()
    strong_match = re.search(r"\b(app|ui|screen|page|button|frontend|web|browser|desktop|route|view)\b", lower)
    if strong_match:
        return {
            "isClientUi": True, "triageSource": "code_deterministic",
            "needsModelReview": False,
            "matchedKeyword": "strong_ui_token:" + strong_match.group(1),
            "reason": "请求中出现英文强 UI 关键词（app/ui/page/button/web/browser 等）。",
        }
    chinese_ui_tokens = [
        "页面", "界面", "按钮", "前端", "客户端", "移动端", "浏览器",
        "桌面", "屏幕", "路由", "视图", "入口", "闪退", "崩溃",
    ]
    for t in chinese_ui_tokens:
        if t in (user_input or ""):
            return {
                "isClientUi": True, "triageSource": "code_deterministic",
                "needsModelReview": False, "matchedKeyword": "zh_ui_token:" + t,
                "reason": f"请求中出现中文 UI/客户端关键词：{t}。",
            }
    if has_negative_signal:
        return {
            "isClientUi": False, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "negative_mobile_signal",
            "reason": "请求中明确声明无需移动设备/真机验证。",
        }
    return {
        "isClientUi": False, "triageSource": "requires_model_review",
        "needsModelReview": True, "matchedKeyword": "ambiguous",
        "reason": "没有强 UI 关键词，也没有显式否定移动信号；可能是后端/脚本任务，模型应再确认。",
    }


def infer_client_ui_task(user_input: str, task_type: str | None = None) -> bool:
    """Best-effort detection for client/UI-facing work. Thin wrapper over
    infer_client_ui_task_detail; callers needing triage metadata should use
    the _detail variant."""
    return infer_client_ui_task_detail(user_input, task_type)["isClientUi"]


def infer_ui_entry_target_detail(user_input: str) -> dict[str, Any]:
    """Rich model-first variant: extract user-mentioned UI entry target with
    triage metadata.

    Returns a dict with:
      entryTarget: str — page/screen/route/entry name.
      triageSource: "code_deterministic" when Chinese phrase, English
          page/screen phrase, or URL path matched. "requires_model_review"
          when fallback default produced.
      needsModelReview: True iff no explicit entry was found — the Planner
          should look at project context (routes file, navigation tree, etc.)
      matchedKeyword: which regex bucket fired (or "fallback").
      reason: short human-readable description.
    """
    text = (user_input or "").strip()
    chinese_match = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9_/-]{1,24}(?:页|页面|界面|屏幕|路由|入口|视图))",
        text,
    )
    if chinese_match:
        candidate = chinese_match.group(1)
        for prefix in [
            "修复", "实现", "新增", "修改", "检查", "验证", "打开", "启动",
            "进入", "定位", "测试", "构建",
        ]:
            if candidate.startswith(prefix) and len(candidate) > len(prefix) + 1:
                candidate = candidate[len(prefix):]
                break
        return {
            "entryTarget": candidate, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "chinese_entry",
            "reason": f"从请求中抽取到明确的中文 UI 入口：{candidate}。",
        }
    english_match = re.search(
        r"\b([A-Za-z0-9_/-]{1,30}(?:\s+[A-Za-z0-9_/-]{1,30}){0,2}\s+(?:page|screen|route|view|entry|activity))\b",
        text, flags=re.IGNORECASE,
    )
    if english_match:
        target = english_match.group(1).strip()
        return {
            "entryTarget": target, "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "english_entry",
            "reason": f"从请求中抽取到明确的英文 UI 入口：{target}。",
        }
    route_match = re.search(r"(?<!\w)(/[A-Za-z0-9_./:-]+)", text)
    if route_match:
        return {
            "entryTarget": route_match.group(1), "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "path_route",
            "reason": f"从请求中抽取到明确的路径/路由：{route_match.group(1)}。",
        }
    return {
        "entryTarget": "the target page/screen/route/activity/state identified from the request or project context",
        "triageSource": "requires_model_review",
        "needsModelReview": True, "matchedKeyword": "fallback",
        "reason": "请求中没有明确提到页面/屏幕/路由/入口等关键词，应由模型或项目路由文件推断。",
    }


def infer_ui_entry_target(user_input: str) -> str:
    """Extract user-mentioned UI entry target without inventing a product flow. Thin wrapper over infer_ui_entry_target_detail; callers needing triage should use the _detail variant."""
    return infer_ui_entry_target_detail(user_input)["entryTarget"]




def generate_plan_md(task_dir: Path, user_input: str) -> str:
    """Generate Plan.md template."""
    plan_path = task_dir / "Plan.md"
    script_command = extract_user_verify_command(user_input)
    task_type = infer_task_type(user_input)
    if script_command:
        task_type = "script"

    if task_type == "ios":
        verify_path = "Build first, then launch iOS simulator/device when applicable, then verify with project-native tests, XCUITest, screenshots/logs, or device evidence. Static inspection alone is not enough for required functional cases."
        command_path = "<AUTOMIND_CLI> ios-preflight <task-code> 1, then <AUTOMIND_CLI> ios-xcuitest <task-code> 1 ... or project-native xcodebuild test."
    elif task_type == "android":
        verify_path = "Run Android preflight first, build/test/install/launch when applicable, inspect UI hierarchy/tap/assert with uiautomator2, and use probe-flow runner or project-native tests. Static inspection alone is not enough for required functional cases."
        command_path = "<AUTOMIND_CLI> android-preflight <task-code> 1, then <AUTOMIND_CLI> android-probe-flow <task-code> 1 or project-native Gradle test."
    elif task_type == "dual":
        verify_path = "Split iOS and Android verification paths, complete both, then merge evidence."
        command_path = "Run platform-specific iOS and Android commands from TestCases.md; both sides need dynamic evidence or explicit blockers."
    else:
        verify_path = f"Run verification command directly: {script_command}" if script_command else "Run script or project-native tests and compare output/results. Static inspection alone is not enough for required functional cases."
        command_path = f"`{script_command}` or <AUTOMIND_CLI> script-command <task-code> 1" if script_command else "Set verifyCommand/scriptCommand or use the project-native test/build/runtime command; then <AUTOMIND_CLI> script-command <task-code> 1 when configured."

    harness = get_harness_profile(task_type)
    harness_lines = ""
    if harness.get("primaryTools"):
        harness_lines += f"- Recommended tools: {', '.join(harness['primaryTools'])}\n"
        if harness.get("fallbackTools"):
            harness_lines += f"- Fallback: {', '.join(harness['fallbackTools'])}\n"
        for item in harness.get("recommendedActions", []):
            harness_lines += f"- {item}\n"
    else:
        harness_lines = "- Use the smallest verification method suitable for the task type.\n"

    units = split_user_requirement_units(user_input)
    implementation_rows = []
    for idx, unit in enumerate(units, start=1):
        implementation_rows.append(
            f"| T{idx:02d} | Implement/repair minimal work for R{idx:02d}: {unit} | R{idx:02d} / AC-001 / TC-F01 | todo | generator | - | - |"
        )
    if not implementation_rows:
        implementation_rows.append("| T01 | Implement/repair the requested behavior | R01 / AC-001 / TC-F01 | todo | generator | - | - |")
    implementation_table = "\n".join(implementation_rows)

    testcases = extract_declared_testcases(task_dir)
    verification_rows = []
    if testcases:
        for tc in testcases:
            required_label = "yes" if tc.get("required") else "no"
            owner_label = "evaluator"
            source_bits = []
            source_bits.extend(tc.get("requirements") or [])
            source_bits.extend(tc.get("acceptanceCriteria") or [])
            source = " / ".join(source_bits) if source_bits else "-"
            verification_rows.append(
                f"| {tc['id']} | {tc.get('type') or 'TestCase'} | {source} | {required_label} | todo | {owner_label} | - | - |"
            )
    else:
        verification_rows.append("| TC-F01 | First functional verification | R01 / AC-001 | yes | todo | evaluator | - | - |")
    verification_table = "\n".join(verification_rows)

    template = f"""# Plan

## Task Type
{task_type}

## Subtasks
1. Clarify goal, input, and output.
2. Implement the smallest runnable version.
3. Run verification and record results.
4. If verification fails, fix based on evaluation feedback and retry.

## Verification Path
{verify_path}

## Concrete verification command/tool path

- Primary command/tool path: {command_path}
- Required functional cases from `TestCases.md` must produce dynamic evidence when a runnable path exists.
- For App/UI/client-facing work, explicitly decide whether to build/package, install/deploy or start a dev/test server, launch/open the app, and run a UI/user journey flow. If not required, explain why.
- The verification plan must name the preparation/preflight, concrete entry target/page/screen/route/activity/state, action sequence, assertions, and evidence to collect for the first functional batch.
- If no runnable path exists, mark the affected case blocked/manual and record the missing command, fixture, environment, device, or user approval.

## First functional batch

- Initial scaffold: run `TC-F01` / the first `TC-F*` cases from `TestCases.md` before quality checks. Phase 2 Refiner must replace this with concrete task-specific `TC-*` IDs before implementation when richer project context is available.

## Implementation Checklist

Update this checklist every Generator round. Use status values `todo`,
`in_progress`, `done`, `blocked`, or `needs_replan`. Generator owns
implementation `T*` rows and should not mark verification `TC-*` rows as pass.

| ID | Work item | Source | Status | Owner | Evidence | Notes |
|----|-----------|--------|--------|-------|----------|-------|
{implementation_table}

## Verification Checklist

Update this checklist every Evaluator/verification round from
`evaluation.json.testResults` and evidence. Use status values `todo`, `running`,
`pass`, `fail`, `blocked`, `skipped_dependency`, `not_run`, or `needs_rerun`.
Only Evaluator/verification should mark required `TC-*` as `pass`.

| ID | TestCase | Source | Required | Status | Owner | Evidence | Notes |
|----|----------|--------|----------|--------|-------|----------|-------|
{verification_table}

## Harness Hints
{harness_lines}
## Risks
- Prioritize a minimal verifiable loop before enhancements.
- If repeated iterations make no progress, replan or ask the user for a strategy/device/environment decision rather than silently accepting weaker evidence.
- If the issue is environment/device/tooling, do not misclassify it as product code failure.

## Current Principles
- This Plan is executable only after the pre-implementation review gate is resolved (`auto_proceed` or user-confirmed `ask_user`) and `workflow-check` passes.
- After the gate is resolved, continue automatically through Generator implement/repair -> Evaluator verify/re-verify -> `completion-check` until finish is proven, `ask_user` needs a human decision, or an explicit unsafe/non-recoverable stop condition occurs.
- Prefer the smallest verifiable loop.
- Complete the key structure before expanding capability.
- Avoid unnecessary design complexity.
"""

    plan_path.write_text(template)
    log(f"Plan.md generated: {plan_path}")
    return str(plan_path)


def generate_validation_md(task_dir: Path) -> str:
    """Generate Validation.md template."""
    val_path = task_dir / "Validation.md"

    template = """# Validation Report

## Status
<!-- In Progress -->

## Latest Verification Summary
- Latest verification time: -
- Latest conclusion: IN PROGRESS
- Key evidence: -
- Next step: run the first validation round.

## Record Protocol
Each validation round must record: environment, preconditions, commands, result, failure category, evidence, reusable findings, avoid-repeat notes, and next step.

Goal: help the same user on the same machine reuse local knowledge in the next task, avoiding repeated exploration, repeated installs, and repeated environment guesses.

### Iteration Template
```md
### Iteration N - <validation topic>
- Time: YYYY-MM-DD HH:mm:ss
- Environment: cwd=..., python=..., venv=..., sdk=..., device=...
- Preconditions: ...
- Commands:
  ```bash
  ...
  ```
- Result: PASS / FAIL / BLOCKED
- Failure category: `validation_failure` / `tool_missing` / ...
- Evidence:
  - `logs/iter-N/...`
- Reusable findings: ...
- Avoid repeating: ...
- Next step: ...
```

## Acceptance Results

| Acceptance item | Result | Reason |
|--------|------|------|
| Pending | IN PROGRESS | Not validated yet |

## Failure Analysis

## Next Steps
Run Phase 3 verification.
"""

    val_path.write_text(template)
    log(f"Validation.md generated: {val_path}")
    return str(val_path)


def set_validation_status(task_dir: Path, status: str):
    """Update Validation.md status marker without overwriting history."""
    val_path = task_dir / "Validation.md"
    if not val_path.exists():
        generate_validation_md(task_dir)
    set_validation_status_marker(task_dir, status)






def normalize_evaluation(task_dir: Path, raw: Optional[dict], iteration: int) -> tuple[dict, list[str]]:
    """
    Normalize Evaluator output into an orchestrator-consumable evaluation.
    Return (evaluation, validation_errors).
    """
    errors: list[str] = []

    if raw is None:
        errors.append("missing_or_invalid_evaluation_json")
        return {
            "iteration": iteration,
            "result": "fail",
            "summary": "Evaluator did not produce a valid evaluation.json",
            "failedChecks": [
                {
                    "name": "evaluation_json",
                    "reason": "missing_or_invalid_evaluation_json"
                }
            ],
            "nextAction": "retry_generator"
        }, errors

    result = str(raw.get("result", "fail")).lower()
    next_action = str(raw.get("nextAction", "retry_generator"))
    summary = raw.get("summary", "")
    failed_checks = raw.get("failedChecks", [])

    allowed_results = {"pass", "fail", "blocked", "in_progress"}
    allowed_actions = {"finish", "retry_generator", "replan", "ask_user", "stop", "stop_blocked", "pause_for_external"}

    if result not in allowed_results:
        errors.append(f"invalid_result:{result}")
        result = "fail"

    if next_action not in allowed_actions:
        errors.append(f"invalid_nextAction:{next_action}")
        next_action = "retry_generator"

    if result == "pass" and next_action != "finish":
        errors.append("pass_result_requires_finish_nextAction")
        next_action = "finish"

    if result in {"fail", "blocked"} and next_action == "finish":
        errors.append("non_pass_result_cannot_finish")
        next_action = "retry_generator" if result == "fail" else "ask_user"

    if result == "in_progress" and next_action == "finish":
        errors.append("in_progress_result_cannot_finish")
        next_action = "retry_generator"

    if not isinstance(summary, str) or not summary.strip():
        errors.append("missing_summary")
        summary = "Evaluator did not provide a valid summary"

    if not isinstance(failed_checks, list):
        errors.append("failedChecks_must_be_array")
        failed_checks = []

    allowed_categories = {
        "agent_unavailable",
        "agent_timeout",
        "invalid_evaluation_output",
        "environment_blocked",
        "build_failure",
        "install_failure",
        "launch_failure",
        "test_failure",
        "validation_failure",
        "mobile_device_unavailable",
        "tool_missing",
        "tool_limitation",
        "permission_blocked",
        "old_team_signing_available",
        "signing_material_blocked",
        "provisioning_profile_blocked",
        "needs_replan",
        "unknown",
        "no_progress",
        "repeated_same_failure",
    }
    normalized_failed_checks = []
    for idx, check in enumerate(failed_checks):
        if not isinstance(check, dict):
            errors.append(f"failedChecks[{idx}]_must_be_object")
            normalized_failed_checks.append({
                "name": f"failed_check_{idx}",
                "reason": str(check),
                "category": "unknown",
            })
            continue
        category = check.get("category")
        if category not in allowed_categories:
            errors.append(f"failedChecks[{idx}]_invalid_or_missing_category")
            check = {**check, "category": "unknown"}
        normalized_failed_checks.append(check)
    failed_checks = normalized_failed_checks

    failed_categories = {
        str(check.get("category", ""))
        for check in failed_checks
        if isinstance(check, dict)
    }
    human_or_environment_categories = {
        "agent_unavailable",
        "agent_timeout",
        "mobile_device_unavailable",
        "tool_missing",
        "tool_limitation",
        "permission_blocked",
        "signing_material_blocked",
        "provisioning_profile_blocked",
    }
    replanable_blocked_categories = {
        "environment_blocked",
        "needs_replan",
        "unknown",
    }
    repairable_categories = {
        "build_failure",
        "install_failure",
        "launch_failure",
        "test_failure",
        "validation_failure",
        "unknown",
    }
    # `stop` is treated as a legacy alias of `stop_blocked` (non-recoverable
    # hard stop). Both should still be normalized to a recoverable next action
    # when failedChecks indicate the failure is actually retryable, replanable,
    # or human-resolvable.
    hard_stop_actions = {"stop", "stop_blocked"}
    if next_action in hard_stop_actions and result == "blocked" and failed_categories & human_or_environment_categories:
        errors.append("recoverable_blocked_stop_normalized_to_ask_user")
        next_action = "ask_user"
    if next_action in hard_stop_actions and result == "blocked" and failed_categories & replanable_blocked_categories:
        errors.append("replanable_blocked_stop_normalized_to_replan")
        next_action = "replan"
    if next_action in hard_stop_actions and result == "fail" and failed_categories & repairable_categories:
        errors.append("repairable_fail_stop_normalized_to_retry_generator")
        next_action = "retry_generator"

    normalized = {
        **raw,
        "iteration": int(raw.get("iteration", iteration) or iteration),
        "result": result,
        "summary": summary,
        "failedChecks": failed_checks,
        "nextAction": next_action,
    }

    if next_action == "ask_user" and not normalized.get("askUserQuestion"):
        normalized["askUserQuestion"] = {
            "question": "AutoMind needs a human/system decision before continuing this verification loop. Fix the listed blocker, choose a verification target, or replan?",
            "reason": summary,
            "options": [
                {"id": "fix_and_resume", "label": "Fix and resume", "impact": "Resolve the environment/device/tool/permission issue and continue the same task."},
                {"id": "replan_strategy", "label": "Replan strategy", "impact": "Revise requirements, TestCases, or verification target before continuing."},
                {"id": "switch_verifier_or_agent", "label": "Switch verifier/agent", "impact": "Use another runnable verification path or coding-agent runtime."},
            ],
            "recommended": "fix_and_resume",
            "source": "normalize_evaluation",
            "failedCategories": sorted(failed_categories),
        }

    if errors:
        normalized.setdefault("warnings", [])
        if isinstance(normalized["warnings"], list):
            normalized["warnings"].extend(errors)

    return normalized, errors




def testcase_ids_from_evaluation(evaluation: dict) -> set[str]:
    """Extract TC ids that need another reflection attempt from evaluation."""
    ids: set[str] = set()
    pattern = re.compile(r"\bTC(?:[-_][A-Za-z]{1,4})?[-_]?\d{2,3}\b")
    for row in evaluation.get("testResults") or []:
        if not isinstance(row, dict):
            continue
        result = str(row.get("result") or "").lower()
        if result in {"fail", "blocked", "warn", "not_run"}:
            tc_id = row.get("testCaseId") or row.get("tcId")
            if isinstance(tc_id, str) and pattern.fullmatch(tc_id):
                ids.add(tc_id)
    for check in evaluation.get("failedChecks") or []:
        if not isinstance(check, dict):
            continue
        for key in ["testCaseId", "tcId", "testCase"]:
            value = check.get(key)
            if isinstance(value, str):
                ids.update(pattern.findall(value))
        for key in ["testCaseIds", "tcIds", "testCases"]:
            value = check.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        ids.update(pattern.findall(item))
        ids.update(pattern.findall(json.dumps(check, ensure_ascii=False)))
    return ids


_SIGNATURE_STOPWORDS = {
    "test", "case", "testcase", "failed", "failure", "error", "errors", "with",
    "this", "that", "from", "into", "still", "again", "could", "cannot", "does",
    "required", "evidence", "result", "results", "check", "checks", "reason",
    "after", "before", "while", "during", "because", "which", "their", "there",
    "budget", "reflection", "exhausted", "verification", "verify",
    # Log/path/iteration noise: these recur every round and would otherwise let
    # an identical root cause drift its signature (e.g. a changing log path),
    # which falsely reads as progress and prevents the budget from converging.
    "log", "logs", "iter", "iteration", "build",
}


def derive_failure_signature(evaluation: dict, tc_id: str) -> str:
    """Derive a stable, coarse signature describing *why* a TC keeps failing.

    Counting reflections by (TC, signature) instead of TC alone lets a changed
    failure signature (i.e. real progress) reset the per-signature budget, while
    a repeated identical root cause (e.g. the same build error) keeps converging
    toward the budget. The signature deliberately stays coarse and stable across
    iterations: failure category plus a few distinctive lowercase error tokens.
    """
    category = ""
    texts: list[str] = []

    def _consume_check(check: dict) -> None:
        nonlocal category
        cat = str(check.get("category") or "").strip()
        if cat and not category:
            category = cat
        texts.append(str(check.get("reason") or check.get("name") or ""))

    tc_checks = []
    other_checks = []
    for check in evaluation.get("failedChecks") or []:
        if not isinstance(check, dict):
            continue
        blob = json.dumps(check, ensure_ascii=False)
        (tc_checks if (tc_id and tc_id in blob) else other_checks).append(check)
    # Prefer checks that reference this TC; fall back to global failedChecks so
    # collateral TCs blocked by a shared root cause share the same signature.
    for check in (tc_checks or other_checks):
        _consume_check(check)

    for row in evaluation.get("testResults") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("testCaseId") or row.get("tcId") or "") != tc_id:
            continue
        for key in ("missingSignals", "observedSignals"):
            value = row.get(key)
            if isinstance(value, list):
                texts.extend(str(item) for item in value)

    text = " ".join(texts).lower()
    # Tokens must start with a letter and be alpha-numeric/underscore words.
    # This excludes pure numbers, timestamps, and filesystem paths (slashes/dots
    # are no longer kept), so the same root cause keeps a stable signature across
    # iterations even when the reason text embeds a changing log path.
    tokens = [
        token
        for token in re.findall(r"[a-z][a-z0-9_]{3,}", text)
        if token not in _SIGNATURE_STOPWORDS
    ]
    # Prefer the most distinctive (longer) domain tokens before truncating, then
    # sort the chosen tokens so the signature is order-independent and stable.
    chosen = sorted(set(tokens), key=lambda t: (-len(t), t))[:4]
    distinctive = sorted(chosen)
    signature = category or "uncategorized"
    if distinctive:
        signature += ":" + "-".join(distinctive)
    return signature


def apply_tc_reflection_budget(task_dir: Path, evaluation: dict, iteration: int) -> dict:
    """Track per-TestCase reflection attempts and converge after the budget.

    A reflection is counted when an evaluation round reports a failing/blocked
    TC-* result or failedChecks references a TC id. Counting is two-dimensional:
    the legacy per-TC ``tcReflectionCounts`` is preserved for visibility, while a
    per-(TC, failure-signature) ``tcFailureSignatureCounts`` drives convergence
    so that a *changed* failure signature (real progress) resets the budget for
    that path. When a signature is repeated up to the budget, AutoMind first runs
    a bounded number of autonomous ``replan`` rounds and only escalates to a
    human ``ask_user`` after those are exhausted.
    """
    if evaluation.get("result") == "pass":
        state = read_runtime_state(task_dir) or {}
        counts = state.get("tcReflectionCounts") if isinstance(state.get("tcReflectionCounts"), dict) else {}
        if counts:
            evaluation["tcReflectionCounts"] = counts
        return evaluation
    tc_ids = testcase_ids_from_evaluation(evaluation)
    if not tc_ids:
        return evaluation
    state = read_runtime_state(task_dir) or {}
    counts = dict(state.get("tcReflectionCounts") or {}) if isinstance(state.get("tcReflectionCounts"), dict) else {}
    last_iterations = dict(state.get("tcReflectionLastIteration") or {}) if isinstance(state.get("tcReflectionLastIteration"), dict) else {}
    sig_counts_raw = state.get("tcFailureSignatureCounts") if isinstance(state.get("tcFailureSignatureCounts"), dict) else {}
    sig_counts: dict[str, dict[str, int]] = {
        tc: dict(sigs) for tc, sigs in sig_counts_raw.items() if isinstance(sigs, dict)
    }
    for tc_id in sorted(tc_ids):
        if int(last_iterations.get(tc_id, -1) or -1) == iteration:
            continue
        counts[tc_id] = int(counts.get(tc_id, 0) or 0) + 1
        last_iterations[tc_id] = iteration
        signature = derive_failure_signature(evaluation, tc_id)
        tc_sig_map = dict(sig_counts.get(tc_id) or {})
        tc_sig_map[signature] = int(tc_sig_map.get(signature, 0) or 0) + 1
        sig_counts[tc_id] = tc_sig_map
    update_runtime_state(
        task_dir,
        tcReflectionCounts=counts,
        tcReflectionLastIteration=last_iterations,
        tcFailureSignatureCounts=sig_counts,
    )
    evaluation["tcReflectionCounts"] = counts
    evaluation["tcFailureSignatureCounts"] = sig_counts
    # A path is exhausted only when the *same* failure signature recurs up to the
    # budget. Changed signatures (progress) keep the budget open.
    exhausted = sorted(
        tc_id
        for tc_id in tc_ids
        if max((sig_counts.get(tc_id) or {}).values(), default=0) >= MAX_REFLECTIONS_PER_TC
    )
    if not exhausted:
        # No repeated-signature deadlock right now: reset the autonomous replan
        # budget so a future deadlock gets its own fresh allowance.
        if int(state.get("autonomousReplanAttempts") or 0):
            update_runtime_state(task_dir, autonomousReplanAttempts=0)
        return evaluation
    if evaluation.get("nextAction") not in {"retry_generator", "replan"}:
        return evaluation

    _budget_ledger = read_tc_attempts(task_dir)
    _budget_progress_by_tc = _budget_ledger.get("progressByTc") if isinstance(_budget_ledger.get("progressByTc"), dict) else {}
    replan_attempts = int(state.get("autonomousReplanAttempts") or 0)
    replan_last_iter = int(state.get("autonomousReplanLastIteration", -1) or -1)
    # Detect the "invalid retry" pattern: repeated failures on an exhausted TC
    # that never narrowed the search space (nothing ruled out, no new candidate).
    # This is the loop spinning in place, and the replan prompt must demand
    # structured narrowing rather than another identical attempt.
    no_narrowing = sorted(
        tc_id
        for tc_id in exhausted
        if int((_budget_progress_by_tc.get(tc_id) or {}).get("narrowingRounds") or 0) == 0
    )
    if replan_attempts < AUTONOMOUS_REPLAN_AFTER_BUDGET:
        # Try an autonomous replan before interrupting the human. Guard the
        # increment so multiple budget calls within one iteration count once.
        if replan_last_iter != iteration:
            replan_attempts += 1
            update_runtime_state(
                task_dir,
                autonomousReplanAttempts=replan_attempts,
                autonomousReplanLastIteration=iteration,
            )
        evaluation.setdefault("failedChecks", []).append({
            "name": "tc_reflection_budget_autonomous_replan",
            "category": "repeated_same_failure",
            "reason": (
                f"Same failure signature repeated for {', '.join(exhausted)} "
                f"(budget max={MAX_REFLECTIONS_PER_TC}); attempting autonomous replan "
                f"{replan_attempts}/{AUTONOMOUS_REPLAN_AFTER_BUDGET} before asking the user."
                + (
                    f" No-narrowing detected for {', '.join(no_narrowing)}: prior failing "
                    "attempts ruled nothing out and proposed no new candidate (invalid retry)."
                    if no_narrowing else ""
                )
            ),
            "testCaseIds": exhausted,
            **({"noNarrowingTestCaseIds": no_narrowing} if no_narrowing else {}),
        })
        evaluation["nextAction"] = "replan"
        evaluation["nextActionPrompt"] = (
            "Autonomous replan triggered by a repeated identical failure signature. "
            "Do NOT just retry the same path. (1) State the concrete root cause of the "
            "repeated failure from the latest evidence. (2) Read the matched Reuse.md / "
            "phase-reuse and high-confidence preloaded packs and apply a not-yet-tried "
            "known-good path for this exact failure before any new approach. (3) If reuse "
            "is exhausted, choose a materially different approach (different command, "
            "build target, verifier, or assumption) and record why in the owning artifact. "
            "Only escalate to ask_user for genuinely sensitive/blocked steps."
            + (
                " CRITICAL: the last attempts narrowed nothing (no ruledOut, no new "
                "selector/hypothesis candidate). This is the invalid-retry pattern that "
                "made the loop spin in place. Before the next attempt you MUST extract "
                "concrete next candidates from the latest observed evidence (UI hierarchy "
                "dump, logs, screenshots) and record them in testResults[].uiExploration "
                "(ruledOut + remainingHypotheses + nextSelectorCandidates). An attempt that "
                "cannot name what it ruled out or what it will try differently is not allowed."
                if no_narrowing else ""
            )
        )
        evaluation["summary"] = (
            "Autonomous replan after repeated same-signature failure: "
            + str(evaluation.get("summary") or "verification keeps failing the same way")
        )
        return evaluation

    # Autonomous replan budget exhausted: escalate to a human decision.
    evaluation.setdefault("failedChecks", []).append({
        "name": "tc_reflection_budget",
        "category": "repeated_same_failure",
        "reason": (
            f"TestCase reflection budget exhausted for {', '.join(exhausted)}; "
            f"max={MAX_REFLECTIONS_PER_TC} and autonomous replan budget "
            f"({AUTONOMOUS_REPLAN_AFTER_BUDGET}) is used up."
        ),
        "testCaseIds": exhausted,
    })
    evaluation["result"] = "blocked"
    evaluation["nextAction"] = "ask_user"
    evaluation["summary"] = "TC reflection budget exhausted: " + str(evaluation.get("summary") or "verification keeps failing")
    evaluation["askUserQuestion"] = {
        "category": "repeated_same_failure",
        "question": f"AutoMind has retried the same TestCase(s) up to the per-TC reflection limit ({MAX_REFLECTIONS_PER_TC}) and autonomous replan did not resolve it. Continue, replan, or pause?",
        "reason": evaluation["summary"],
        "options": [
            {"id": "replan_strategy", "label": "Replan strategy", "impact": "Revise TestCases/Plan or choose another verifier before continuing."},
            {"id": "continue_with_more_reflections", "label": "Continue anyway", "impact": "Override the per-TC reflection budget for this task."},
            {"id": "pause", "label": "Pause", "impact": "Stop automatic retries and inspect evidence manually."},
        ],
        "recommended": "replan_strategy",
        "source": "tc_reflection_budget",
        "testCaseIds": exhausted,
    }
    return evaluation

def run_quality_evaluator(task_dir: Path, iteration: int) -> tuple[dict | None, str]:
    """Run lightweight quality-check after functional evaluation and merge into evaluation.json."""
    task_code = task_dir.name
    cmd = [
        sys.executable,
        str(AUTOMIND_ROOT / "scripts" / "quality_evaluator.py"),
        task_code,
        "--root",
        str(AUTOMIND_WORKSPACE_ROOT),
        "--iteration",
        str(iteration),
        "--merge",
    ]
    code, stdout, stderr = run_cmd(cmd, capture=True, cwd=str(AUTOMIND_WORKSPACE_ROOT))
    output = (stdout or "") + (stderr or "")
    if code != 0:
        warn(f"quality-check failed: {output[:300]}")
        return None, output
    return read_evaluation_json(task_dir), output


def fallback_evaluation_from_validation(task_dir: Path, iteration: int) -> dict:
    """Compatibility fallback for older Evaluators when evaluation.json is missing/invalid."""
    val_path = task_dir / "Validation.md"
    if not val_path.exists():
        return {
            "iteration": iteration,
            "result": "fail",
            "summary": "Validation.md was not generated",
            "failedChecks": [{"name": "validation_output", "reason": "Evaluator did not generate Validation.md", "category": "invalid_evaluation_output"}],
            "nextAction": "stop"
        }

    content = val_path.read_text()
    normalized = content.lower()
    pass_markers = ["finished", "passed", "✅ pass", "\u7ed3\u8bba：\u901a\u8fc7", "\u5df2\u5b8c\u6210"]
    fail_markers = ["❌ fail", "\u7ed3\u8bba：fail", "status | fail", "Status | fail"]
    last_pass_pos = max((normalized.rfind(marker) for marker in pass_markers), default=-1)
    last_fail_pos = max((normalized.rfind(marker) for marker in fail_markers), default=-1)

    if last_pass_pos != -1 and (last_fail_pos == -1 or last_pass_pos > last_fail_pos):
        return {
            "iteration": iteration,
            "result": "pass",
            "summary": "Compatibility fallback: Validation.md text indicates pass",
            "failedChecks": [],
            "nextAction": "finish",
            "warnings": ["fallback_from_validation_md"]
        }

    return {
        "iteration": iteration,
        "result": "fail",
        "summary": "Compatibility fallback: no valid evaluation.json and Validation.md does not clearly pass",
        "failedChecks": [{"name": "evaluation_json", "reason": "missing_or_invalid_evaluation_json", "category": "invalid_evaluation_output"}],
        "nextAction": "retry_generator",
        "warnings": ["fallback_from_validation_md"]
    }



def _format_probe_flow_action_evidence(summary: dict, limit: int = 8) -> list[str]:
    """Return compact Markdown bullets for critical UI action evidence.

    Keep this intentionally small: report the action, target, result, and the
    after screenshot/hierarchy paths. Detailed JSONL remains the source of
    truth for deeper debugging.
    """
    actions = summary.get("actionTrace") or []
    if not isinstance(actions, list) or not actions:
        return []
    lines: list[str] = []
    for action in actions[:limit]:
        if not isinstance(action, dict):
            continue
        idx = action.get("stepIndex", "?")
        name = action.get("name") or action.get("intent") or "UI action"
        typ = action.get("type") or "action"
        result = "PASS" if action.get("ok") else "FAIL"
        selector = action.get("selector")
        target = "-"
        if isinstance(selector, dict):
            for key in ["resource_id", "text", "desc", "xpath"]:
                if selector.get(key):
                    target = f"{key}={selector.get(key)}"
                    break
            if target == "-" and {"x", "y"}.issubset(selector):
                target = f"coord=({selector.get('x')},{selector.get('y')})"
        node = action.get("resolvedNode") or action.get("resolvedNodeBefore")
        node_text = ""
        if isinstance(node, dict):
            node_bits = []
            for key in ["resource_id", "text", "content_desc", "class"]:
                if node.get(key):
                    node_bits.append(f"{key}={node.get(key)}")
            if node.get("bounds"):
                node_bits.append(f"bounds={node.get('bounds')}")
            if node_bits:
                node_text = "; node: " + ", ".join(node_bits[:4])
        after = action.get("evidenceAfter") if isinstance(action.get("evidenceAfter"), dict) else {}
        shot = after.get("screenshot") or "-"
        hierarchy = after.get("hierarchy") or "-"
        detail = str(action.get("detail") or "").strip()
        detail_text = f"; detail: {detail[:180]}" if detail else ""
        lines.append(f"- Step {idx} `{typ}` {result}: {name}; target: `{target}`{node_text}{detail_text}")
        lines.append(f"  - after screenshot: `{shot}`")
        lines.append(f"  - after hierarchy: `{hierarchy}`")
    if len(actions) > limit:
        lines.append(f"- ... {len(actions) - limit} more critical actions in `action-trace.jsonl`.")
    return lines


def append_android_probe_flow_validation(task_dir: Path, iteration: int, evaluation: dict, iter_log_dir: Path, summary: dict | None = None) -> None:
    """Append a focused Android probe-flow validation section with UI action evidence."""
    val_path = task_dir / "Validation.md"
    if not val_path.exists():
        val_path.write_text("# Validation\n")
    summary = summary or {}
    result = str(evaluation.get("result", "unknown")).upper()
    category = _first_failed_check_category(evaluation) if evaluation.get("failedChecks") else "none"
    rel = rel_to_root
    action_lines = _format_probe_flow_action_evidence(summary)
    action_block = "\n".join(action_lines) if action_lines else "- No critical UI action trace was produced."
    trace_path = iter_log_dir / "probe-flow" / "action-trace.jsonl"
    summary_path = iter_log_dir / "probe-flow" / "probe-flow-summary.json"
    block = f"""

## Iteration {iteration} - Android Probe Flow

### Result

{result}. {evaluation.get('summary', '')}

- failure category: `{category}`
- nextAction: `{evaluation.get('nextAction', '-')}`

### Evidence

- `{rel(iter_log_dir / 'evaluator.log')}`
- `{rel(summary_path)}`
- `{rel(trace_path)}`
- `{rel(iter_log_dir / 'commands.md')}`
- `{rel(iter_log_dir / 'env.json')}`
- `{rel(task_dir / 'evaluation.json')}`

### Client UI action evidence

{action_block}
"""
    content = val_path.read_text(errors="ignore")
    # Avoid duplicate append when a supervisor resumes after writing evaluation.
    marker = f"## Iteration {iteration} - Android Probe Flow"
    if marker in content:
        return
    val_path.write_text(content + block)


def append_script_command_validation(task_dir: Path, iteration: int, command: str, evaluation: dict, iter_log_dir: Path) -> None:
    """Append reusable human-readable validation notes for generic script harness."""
    val_path = task_dir / "Validation.md"
    if not val_path.exists():
        val_path.write_text("# Validation\n")
    result = str(evaluation.get("result", "unknown")).upper()
    summary = evaluation.get("summary", "")
    failed = evaluation.get("failedChecks", [])
    category = "-"
    if isinstance(failed, list) and failed and isinstance(failed[0], dict):
        category = failed[0].get("category", "unknown")
    rel = rel_to_root
    block = f"""

## Iteration {iteration} - Script Command Harness

### Environment

- cwd: `{AUTOMIND_WORKSPACE_ROOT}`
- runtime: `{AUTOMIND_ROOT}`
- task: `{task_dir.name}`
- harness: `script-command`

### Commands

```bash
{command}
```

### Result

{result}. {summary}

- \u5931\u8d25Category：`{category}`
- nextAction: `{evaluation.get('nextAction', '-')}`

### Evidence

- `{rel(iter_log_dir / 'script-command.log')}`
- `{rel(iter_log_dir / 'evaluator.log')}`
- `{rel(iter_log_dir / 'commands.md')}`
- `{rel(iter_log_dir / 'env.json')}`
- `{rel(task_dir / 'evaluation.json')}`

### Reusable findings

This task can be verified by rerunning the script command above. The command exit code is the harness contract: `0` means pass, non-zero means fail and should feed back to Generator or an external fix.

### Avoid repeating

Do not require a mobile/platform probe-flow when a non-platform project already exposes a reliable explicit verification command. Use `script-command` only for explicit `scriptCommand`/`verifyCommand`; platform/runtime proof should stay on the platform Evaluator path.
"""
    val_path.write_text(val_path.read_text(errors="ignore") + block)



def _first_failed_check_category(evaluation: dict) -> str:
    failed_checks = evaluation.get("failedChecks")
    if isinstance(failed_checks, list) and failed_checks and isinstance(failed_checks[0], dict):
        return str(failed_checks[0].get("category") or "unknown")
    return "unknown"


def classify_agent_execution_failure_detail(output: str) -> dict[str, Any]:
    """Rich model-first variant: classify an external agent CLI failure.

    Returns a dict with:
      category: str — the thin-wrapper return value
          (agent_context_overflow / agent_stalled_no_output / agent_timeout /
          agent_unavailable).
      triageSource: "code_deterministic" when a specific terminal signal
          matched. "requires_model_review" for the catch-all
          ``agent_unavailable`` fallback, which is "no known pattern matched"
          rather than a positively identified unavailability.
      needsModelReview: True iff the fallback fired.
      matchedKeyword: which signal bucket fired.
      reason: short human-readable description.
    """
    text = (output or "").lower()
    if (
        "ran out of room in the model's context window" in text
        or "ran out of room in the model’s context window" in text
        or "context window" in text and "start a new thread" in text
        or "context length exceeded" in text
        or "maximum context length" in text
        or "input is too long" in text and "context" in text
    ):
        return {
            "category": "agent_context_overflow", "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "context_overflow",
            "reason": "命中明确的上下文窗口耗尽信号。",
        }
    if "agent stalled with no output" in text or "agent idle timeout" in text or "stalled_no_output" in text:
        return {
            "category": "agent_stalled_no_output", "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "stalled_no_output",
            "reason": "命中明确的 agent 无输出停滞信号。",
        }
    if "command timeout" in text or "timed out" in text or "timeout after" in text:
        return {
            "category": "agent_timeout", "triageSource": "code_deterministic",
            "needsModelReview": False, "matchedKeyword": "timeout",
            "reason": "命中明确的超时信号。",
        }
    return {
        "category": "agent_unavailable", "triageSource": "requires_model_review",
        "needsModelReview": True, "matchedKeyword": "fallback",
        "reason": "没有命中任何已知终端信号，兜底归类为 agent_unavailable；不同 CLI 措辞各异，模型应复核输出确认真实失败原因（是否其实是超时/上下文溢出/产品错误）。",
    }


def classify_agent_execution_failure(output: str) -> str:
    """Classify failures from an external agent CLI invocation. Thin wrapper over
    classify_agent_execution_failure_detail; callers needing triage metadata
    should use the _detail variant."""
    return classify_agent_execution_failure_detail(output)["category"]


def _read_failure_log_tail(path: Path, limit: int = 128_000) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > limit:
                fh.seek(size - limit)
            return fh.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def classify_agent_execution_failure_with_log(output: str, log_path: Path | None = None) -> str:
    """Classify agent failure, letting persisted log tail override wrapper timeouts.

    Some agent wrappers report a timeout while the persisted phase log contains
    the real terminal error (for example Codex context-window exhaustion). The
    terminal log signal should drive recovery because it determines whether a
    fresh session is required.
    """
    if log_path is not None:
        tail = _read_failure_log_tail(log_path)
        if tail:
            from_tail = classify_agent_execution_failure(tail)
            if from_tail == "agent_context_overflow":
                return from_tail
    return classify_agent_execution_failure(output)


SAFE_AUTO_RECOVERY_FAILURE_CATEGORIES = {"agent_unavailable", "agent_timeout", "agent_stalled_no_output", "agent_context_overflow"}


def is_safe_auto_recovery_failure(category: str) -> bool:
    """Return whether an agent/runtime failure can continue without ask_user.

    These categories are infrastructure/runtime interruptions. Retrying or
    resuming the same AutoMind task preserves local artifacts and does not grant
    new data access, change user data, switch accounts, alter device trust, or
    perform destructive actions. Keep this whitelist intentionally narrow.
    """
    return str(category or "").strip() in SAFE_AUTO_RECOVERY_FAILURE_CATEGORIES


def write_agent_failure_iteration_records(
    task_dir: Path,
    iteration: int,
    iter_log_dir: Path,
    agent: str,
    phase: str,
    output: str,
    evaluation: dict,
) -> None:
    """Write minimum reusable records when an agent process fails.

    Agent/runtime failures happen outside Generator/Evaluator control. AutoMind
    still needs complete iteration records so resume, record-check, and future
    local reuse do not have to parse partial stdout.
    """
    ensure_dir(iter_log_dir)
    failure_category = _first_failed_check_category(evaluation)
    phase = phase.lower().strip() or "agent"
    runner_cmd = ["automind", "resume", task_dir.name, agent, f"# phase={phase}"]
    env = collect_env_snapshot(task_dir, iteration, runner_cmd)
    env.update({
        "phase": phase,
        "agent": agent,
        "agentFailure": True,
        "failureCategory": failure_category,
        "failureSummary": evaluation.get("summary", ""),
        "agentTimeoutSeconds": os.environ.get("AUTOMIND_AGENT_TIMEOUT", os.environ.get("AUTOMIND_CMD_TIMEOUT", "43200")),
        "log": rel_to_root(iter_log_dir / f"{phase}.log"),
    })
    (iter_log_dir / "env.json").write_text(json.dumps(env, ensure_ascii=False, indent=2) + "\n")

    prompt_path = iter_log_dir / f"{phase}-prompt.md"
    log_path = iter_log_dir / f"{phase}.log"
    commands_text = f"""# Iteration {iteration} Agent Failure Commands

## Environment

- cwd: `{AUTOMIND_WORKSPACE_ROOT}`
- runtime: `{AUTOMIND_ROOT}`
- task: `{task_dir.name}`
- phase: `{phase}`
- agent: `{agent}`
- timeout: `{env.get('agentTimeoutSeconds')}`

## Commands

The concrete agent command is generated by the `{agent}` adapter and receives
the rendered prompt below. To retry after fixing the runtime/environment:

```bash
AUTOMIND_AGENT_TIMEOUT=${{AUTOMIND_AGENT_TIMEOUT:-{env.get('agentTimeoutSeconds')}}} automind resume {task_dir.name} {agent}
```

If the failure category is `agent_context_overflow`, retry with a fresh primary
session instead of resuming the saturated agent thread:

```bash
AUTOMIND_AGENT_SESSION_POLICY=fresh AUTOMIND_AGENT_TIMEOUT=${{AUTOMIND_AGENT_TIMEOUT:-{env.get('agentTimeoutSeconds')}}} automind resume {task_dir.name} {agent}
```

## Result

- result: `{evaluation.get('result')}`
- nextAction: `{evaluation.get('nextAction')}`
- failure category: `{env.get('failureCategory')}`
- summary: {evaluation.get('summary')}

## Evidence

- `{rel_to_root(log_path)}`
"""
    if prompt_path.exists():
        commands_text += f"- `{rel_to_root(prompt_path)}`\n"
    commands_text += f"""- `{rel_to_root(iter_log_dir / 'env.json')}`
- `{rel_to_root(task_dir / 'evaluation.json')}`

## Reusable findings

Agent/runtime failures are external interruptions. Resume the same task after
the agent CLI/runtime recovers. If the category is `agent_context_overflow`,
start a fresh primary agent session because the previous coding-agent thread is
saturated; the durable task artifacts remain the handoff. If the category is
`agent_stalled_no_output`, inspect agent stdout/events first; it means the
process produced no output for the idle watchdog window, not that product
validation failed. Increase `AUTOMIND_AGENT_IDLE_TIMEOUT_SECONDS` only for known
quiet-but-healthy agents.

## Avoid repeating

Do not classify this as a product-code validation failure without later
Evaluator evidence. Do not claim finish from this blocked agent invocation.
"""
    (iter_log_dir / "commands.md").write_text(commands_text)

    if phase == "generator":
        status_path = iter_log_dir / "workspace-status.txt"
        code, stdout, stderr = run_cmd(["git", "status", "--short"], cwd=str(AUTOMIND_WORKSPACE_ROOT), timeout=30)
        if code == 0:
            status_path.write_text(stdout or "(clean)\n")
        else:
            status_path.write_text((stderr or stdout or "git status unavailable").strip() + "\n")

        delivery_path = task_dir / "Delivery.md"
        existing = delivery_path.read_text(errors="ignore") if delivery_path.exists() else ""
        section_marker = f"## Iteration {iteration} - Generator blocked by agent/runtime failure"
        if section_marker not in existing:
            header = "# Delivery\n\n" if not existing.strip() else "\n"
            delivery_path.write_text(existing.rstrip() + header + f"""{section_marker}

- Environment: cwd=`{AUTOMIND_WORKSPACE_ROOT}`; runtime=`{AUTOMIND_ROOT}`; agent=`{agent}`
- Commands: see `logs/iter-{iteration}/commands.md`; retry with `automind resume {task_dir.name} {agent}` after runtime recovery.
- Result: BLOCKED. The Generator agent did not complete, so AutoMind does not claim a completed implementation for this invocation.
- Failure category: `{env.get('failureCategory')}`
- Evidence:
  - `logs/iter-{iteration}/{phase}.log`
  - `logs/iter-{iteration}/commands.md`
  - `logs/iter-{iteration}/env.json`
  - `logs/iter-{iteration}/workspace-status.txt`
- Covered testcases: none proven by this blocked Generator invocation; required `TC-*` rows remain pending until a later successful Generator/Evaluator round.
- Reusable findings: Long agent runs may need `AUTOMIND_AGENT_TIMEOUT=<seconds>`; external agent/runtime interruption is recoverable through `resume`. If failure category is `agent_context_overflow`, retry with `AUTOMIND_AGENT_SESSION_POLICY=fresh` or after AutoMind clears the stale primary session.
- Avoid repeating: Do not treat this blocked Delivery as proof of product/runtime completion. Inspect working-tree diff if the agent may have made partial edits before timing out.
""")



# ============================================================
# Probe Flow \u81ea\u52a8\u751f\u6210\u4e0e\u8c03\u5ea6
# ============================================================
























def is_android_probe_flow_task(task_dir: Path) -> bool:
    """\u5224\u65ad\u5f53\u524dTask\u662f\u5426\u5e94\u7531 Android probe-flow runner \u627f\u62c5 Evaluator。"""
    state = read_runtime_state(task_dir) or {}
    harness = state.get("harnessProfile") or {}
    return state.get("taskType") == "android" or harness.get("name") == "android-v1"


def extract_android_app_config(task_dir: Path) -> tuple[Optional[dict], list[str]]:
    """
    \u4ece runtime-state / \u5df2\u6709 probe-flow / \u6587\u672c\u4ea7\u7269\u4e2d\u63d0\u53d6 Android app \u4fe1\u606f。
    \u8fd4\u56de (app, warnings)。app \u81f3\u5c11\u9700\u8981 apk/package/activity \u624d\u80fd\u6267\u884c runner。
    """
    warnings: list[str] = []
    state = read_runtime_state(task_dir) or {}

    # 1) \u663e\u5f0fStatus\u4f18\u5148，\u4fbf\u4e8e\u672a\u6765\u7531 Generator/Planner \u5199\u5165。
    for key in ("androidApp", "app"):
        value = state.get(key)
        if isinstance(value, dict):
            return value, warnings

    harness = state.get("harnessProfile") or {}
    if isinstance(harness.get("app"), dict):
        return harness["app"], warnings

    # 2) \u5df2\u6709 probe-flow \u662f\u6700\u53ef\u4fe1\u6765\u6e90。
    flow_path = resolve_probe_flow_path(task_dir, "android")
    if flow_path.exists():
        try:
            flow = json.loads(flow_path.read_text())
            app = flow.get("app")
            if isinstance(app, dict):
                return app, warnings
        except json.JSONDecodeError as exc:
            warnings.append(f"existing_probe_flow_invalid_json:{exc}")

    # 3) \u6587\u672c\u4e2d\u652f\u6301\u663e\u5f0f\u58f0\u660e，\u683c\u5f0f\u5982：apk: xxx / package: xxx / activity: xxx。
    combined = "\n".join(
        path.read_text(errors="ignore")
        for path in [*requirement_contract_paths(task_dir), task_dir / "Plan.md", task_dir / "Delivery.md"]
        if path.exists()
    )
    app: dict[str, str] = {}
    patterns = {
        "apk": r"(?:apk|APK)\s*[:：]\s*`?([^`\n]+?\.apk)`?(?:\s|$)",
        "package": r"(?:package|\u5305\u540d)\s*[:：]\s*`?([A-Za-z0-9_.]+)`?",
        "activity": r"(?:activity|Activity|\u5165\u53e3)\s*[:：]\s*`?([A-Za-z0-9_.$/]+)`?",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, combined)
        if match:
            app[key] = match.group(1).strip()
    if {"apk", "package", "activity"}.issubset(app):
        return app, warnings

    # 4) AutoMind demo \u573a\u666f fallback：\u4ec5\u5bf9\u660e\u786e demo/probe/minimal \u8f93\u5165\u4f7f\u7528。
    user_input = state.get("userInput", "")
    demo_keywords = ["demo", "minimal", "probe", "\u6700\u5c0f", "\u6f14\u793a", "\u6837\u4f8b"]
    if any(k in user_input.lower() for k in demo_keywords) or any(k in user_input for k in demo_keywords):
        demo_apk = AUTOMIND_ROOT / "demos" / "android-minimal-demo" / "build" / "AutoMindAndroidDemo-debug.apk"
        demo_build = AUTOMIND_ROOT / "demos" / "android-minimal-demo" / "build_apk.sh"
        if demo_build.exists():
            return {
                "apk": rel_to_root(demo_apk),
                "package": "ai.openclaw.automind.demo",
                "activity": ".MainActivity",
                "buildCommand": "./demos/android-minimal-demo/build_apk.sh",
            }, warnings

    return None, warnings


def build_default_android_probe_steps(req_text: str, app: Optional[dict] = None) -> list[dict]:
    """
    从 Requirements.md验收标准中生成保守的默认 Android probe-flow steps。
    \u5f53\u524d v1 \u662f\u89c4\u5219/\u6a21\u677f\u751f\u6210：\u5148\u8986\u76d6\u5b89\u88c5、\u542f\u52a8、\u57fa\u7840\u6587\u672c、\u6309\u94ae\u70b9\u51fb、\u5b8c\u6210\u65ad\u8a00、\u622a\u56fe、\u505c\u6b62。
    \u540e\u7eed\u53ef\u66ff\u6362\u4e3a LLM-based flow generator。
    """
    expected_package = (app or {}).get("package")
    steps: list[dict] = [
        {"type": "install", "name": "install apk", "uninstall": False},
        {"type": "launch", "name": "launch app"},
    ]

    # Rule-based extraction of likely expected texts from docs.
    expected_texts: list[str] = []
    for pattern in [
        r"assert(?:_text)?\s*[:：]\s*`?([^`\n]+)`?",
        r"\u671f\u671b\u6587\u672c\s*[:：]\s*`?([^`\n]+)`?",
        r"\u65ad\u8a00\u6587\u672c\s*[:：]\s*`?([^`\n]+)`?",
    ]:
        for match in re.finditer(pattern, req_text, flags=re.IGNORECASE):
            value = match.group(1).strip().strip("。.; - ")
            if value and value not in expected_texts:
                expected_texts.append(value)

    # Optional popup conventions: generated probe-flows should tolerate
    # environment-dependent dialogs. If the popup exists, handle it; if it does
    # not, continue without marking the evaluation failed.
    optional_popup_keywords = ["\u5982\u679c\u9047\u5230\u5f39\u7a97", "\u9047\u5230\u5f39\u7a97", "\u5173\u95ed\u5f39\u7a97", "\u8df3\u8fc7\u5f39\u7a97", "\u53ef\u9009\u5f39\u7a97", "optional popup", "if popup"]
    wants_optional_popup_handling = any(k.lower() in req_text.lower() for k in optional_popup_keywords)
    if wants_optional_popup_handling:
        steps.extend([
            {"type": "tap_if_present", "name": "close optional popup by text", "selector": {"text": "\u5173\u95ed"}, "timeout": 1, "optional": True},
            {"type": "tap_if_present", "name": "skip optional popup by text", "selector": {"text": "\u8df3\u8fc7"}, "timeout": 1, "optional": True},
            {"type": "tap_if_present", "name": "cancel optional popup by text", "selector": {"text": "\u53d6\u6d88"}, "timeout": 1, "optional": True},
        ])

    # Privacy / first-run dialog conventions for real Android apps.
    # Keep this non-destructive: only assert that the dialog exists; do not tap
    # Agree/Disagree because that changes app state and requires human consent.
    privacy_keywords = [
        "\u9690\u79c1", "\u4e2a\u4eba\u4fe1\u606f\u4fdd\u62a4", "\u4e2a\u4eba\u4fe1\u606f\u4fdd\u62a4\u6307\u5f15", "\u7528\u6237\u534f\u8bae", "\u9996\u6b21\u542f\u52a8", "\u9996\u542f",
        "privacy", "privacy dialog", "first run", "first-run",
    ]
    wants_privacy_dialog = any(k.lower() in req_text.lower() for k in privacy_keywords)
    if wants_privacy_dialog:
        for text in ["\u4e2a\u4eba\u4fe1\u606f\u4fdd\u62a4\u6307\u5f15", "\u540c\u610f", "\u4e0d\u540c\u610f"]:
            if text not in expected_texts:
                expected_texts.append(text)

    # AutoMind demo conventions.
    if "AutoMind Android Harness Demo" in req_text or "probe_button" in req_text or "Probe state" in req_text:
        for text in ["AutoMind Android Harness Demo", "Probe state: Idle"]:
            if text not in expected_texts:
                expected_texts.append(text)

    has_probe_action = "probe_button" in req_text or "Run Probe" in req_text or "Probe state" in req_text
    pre_action_texts = []
    for text in expected_texts:
        # Text that is explicitly expected after tapping should not be asserted before the tap.
        if has_probe_action and text == "Probe state: Completed":
            continue
        pre_action_texts.append(text)

    for idx, text in enumerate(pre_action_texts, start=1):
        steps.append({"type": "assert_text", "name": f"assert text {idx}: {text}", "text": text, "timeout": 5, "interval": 1})

    # If the requirement mentions a probe button or no concrete action exists, use the stable demo selector.
    if has_probe_action:
        steps.extend([
            {"type": "tap", "name": "tap probe button", "selector": {"desc": "probe_button", "text": "Run Probe"}},
            {"type": "assert_text", "name": "assert completed", "text": "Probe state: Completed"},
        ])

    if wants_privacy_dialog:
        steps.extend([
            {"type": "assert_selector", "name": "assert privacy confirm button selector", "selector": {"resource_id": f"{expected_package}:id/confirm_btn"}, "timeout": 5, "interval": 1},
            {"type": "assert_selector", "name": "assert privacy negative button selector", "selector": {"resource_id": f"{expected_package}:id/negative_btn"}, "timeout": 5, "interval": 1},
        ])

    # Optional readiness contract. Keep the generator generic: stable
    # selectors/texts must come from explicit app config, not from built-in
    # product keywords such as a particular app's tabs or content areas.
    readiness = (app or {}).get("readiness") if isinstance(app, dict) else None
    if not isinstance(readiness, dict):
        readiness = {}
    readiness_enabled = bool(readiness) and readiness.get("enabled", True) is not False
    if readiness_enabled:
        for item in readiness.get("optionalTapSelectors", []):
            if not isinstance(item, dict) or not item.get("selector"):
                continue
            steps.append({
                "type": "tap_if_present",
                "name": item.get("name", "tap optional readiness selector"),
                "selector": item.get("selector"),
                "timeout": item.get("timeout", 2),
                "wait": item.get("wait", 1),
                "optional": True,
            })
        wait_seconds = readiness.get("waitSeconds")
        if wait_seconds:
            steps.append({"type": "wait", "name": "wait for readiness target", "seconds": wait_seconds})
        for item in readiness.get("selectors", []):
            if not isinstance(item, dict) or not item.get("selector"):
                continue
            steps.append({
                "type": "assert_selector",
                "name": f"assert readiness selector: {item.get('name', 'selector')}",
                "selector": item.get("selector"),
                "timeout": item.get("timeout", 5),
                "interval": item.get("interval", 1),
            })
        for text in readiness.get("texts", []):
            if isinstance(text, str) and text.strip():
                steps.append({"type": "assert_text", "name": f"assert readiness text: {text}", "text": text, "timeout": 5, "interval": 1})

    # For real apps, the smallest useful probe-flow should still prove that
    # device state can be observed by the evaluator, not only that launch did
    # not crash. These two generic evidence steps keep the default flow useful
    # for real APKs before feature-specific assertions exist.
    current_app_step = {"type": "current_app", "name": "assert current app package"}
    if expected_package:
        current_app_step["expected"] = expected_package

    steps.extend([
        current_app_step,
        {"type": "dump_hierarchy", "name": "capture UI hierarchy"},
        {"type": "assert_app_hierarchy", "name": "assert app exposes verifiable UI hierarchy", "minNodes": 1, "timeout": 8, "interval": 1},
        {"type": "screenshot", "name": "final screenshot", "output": "final-screenshot"},
        {"type": "stop", "name": "stop app"},
    ])
    return steps


def _cache_probe_flow_paths(task_dir: Path, flow_path: Optional[Path], summary: dict) -> None:
    """Cache the verified probe-flow steps for reuse in later runs.

    Called only after the Android probe-flow completion gate confirms
    nextAction=finish, so we cache steps that were actually proven on-device.
    The cache is keyed by target TC id and validated by a UI fingerprint so a
    later run can skip re-generating steps when the source UI is unchanged.
    """
    if not flow_path:
        return
    try:
        flow = json.loads(Path(flow_path).read_text())
    except (OSError, json.JSONDecodeError):
        return
    steps = flow.get("steps")
    if not isinstance(steps, list) or not steps:
        return
    target_ids = infer_probe_target_testcases(task_dir, summary)
    if not target_ids:
        return
    fingerprint = compute_ui_fingerprint(task_dir)
    goal = str(flow.get("name") or task_dir.name)
    for tc_id in target_ids:
        cache_ui_path(task_dir, tc_id, goal, steps, fingerprint)


def _expire_cached_probe_flow_on_failure(task_dir: Path, flow_path: Optional[Path], evaluation: dict) -> None:
    """Expire cached UI paths when a cached probe-flow fails on-device.

    Only expires entries when the current probe-flow was reused from cache
    (flow.reusedFromCache == True and flow.reusedTc is set). The failure reason
    is captured from evaluation.summary for audit trail.
    """
    if not flow_path:
        return
    try:
        flow = json.loads(Path(flow_path).read_text())
    except (OSError, json.JSONDecodeError):
        return
    if not flow.get("reusedFromCache"):
        return
    tc_id = flow.get("reusedTc")
    if not tc_id:
        return
    reason = str(evaluation.get("summary") or "probe_flow_execution_failed")[:200]
    expire_cached_ui_paths(task_dir, [tc_id], reason=reason)


def _reuse_cached_probe_flow(task_dir: Path, flow_path: Path) -> bool:
    """Reuse a previously verified probe-flow when the source UI is unchanged.

    Returns True when a valid cached flow was written to flow_path, letting the
    caller skip step regeneration. The cache is only reused when its UI
    fingerprint matches the current workspace, so structural/source changes
    invalidate it and force fresh generation.
    """
    declared = extract_declared_testcases(task_dir)
    runtime_ids = [
        tc.get("id") for tc in declared
        if tc.get("required") and _normalize_runtime_level(tc.get("runtimeLevel")) in {"device", "runtime"}
    ]
    runtime_ids = [str(x) for x in runtime_ids if x]
    if not runtime_ids:
        return False
    fingerprint = compute_ui_fingerprint(task_dir)
    for tc_id in runtime_ids:
        cached = get_cached_ui_path(task_dir, tc_id, fingerprint)
        if not cached:
            continue
        steps = cached.get("actionSequence")
        if not isinstance(steps, list) or not steps:
            continue
        app, _warnings = extract_android_app_config(task_dir)
        if not app:
            return False
        state = read_runtime_state(task_dir) or {}
        flow = {
            "platform": "android",
            "name": f"{state.get('taskId', task_dir.name)} reused android probe flow",
            "app": {
                "apk": app.get("apk"),
                "package": app.get("package"),
                "activity": app.get("activity"),
            },
            "steps": steps,
            "reusedFromCache": True,
            "reusedTc": tc_id,
        }
        flow_path.write_text(json.dumps(flow, ensure_ascii=False, indent=2))
        update_runtime_state(task_dir, probeFlow=str(flow_path), androidApp=app)
        log(f"Reusing cached UI probe-flow for {tc_id} (UI fingerprint matched)")
        return True
    return False


def generate_probe_flow_json(task_dir: Path, force: bool = False) -> tuple[Optional[Path], Optional[dict]]:
    """根据 Requirements.md验收标准生成 Android probe-flow.android.json。"""
    flow_path = resolve_probe_flow_path(task_dir, "android")
    if flow_path.exists() and not force:
        try:
            return flow_path, json.loads(flow_path.read_text())
        except json.JSONDecodeError:
            warn(f"Existing probe-flow file is invalid JSON; regenerating: {flow_path}")
    # When regenerating (force or invalid JSON), always write to the canonical
    # platform-suffixed name; never overwrite the legacy `probe-flow.json`.
    flow_path = task_dir / "probe-flow.android.json"

    # Prefer a previously verified flow when the source UI is unchanged, so we
    # skip fresh step generation. Falls back to generation on any cache miss.
    if _reuse_cached_probe_flow(task_dir, flow_path):
        try:
            return flow_path, json.loads(flow_path.read_text())
        except json.JSONDecodeError:
            pass

    app, warnings = extract_android_app_config(task_dir)
    if not app:
        return None, {
            "warnings": warnings,
            "reason": "missing_android_app_config",
        }

    req_text = read_requirements_contract_text(task_dir)
    state = read_runtime_state(task_dir) or {}
    flow = {
        "platform": "android",
        "name": f"{state.get('taskId', task_dir.name)} auto-generated android probe flow",
        "app": {
            "apk": app.get("apk"),
            "package": app.get("package"),
            "activity": app.get("activity"),
        },
        "steps": build_default_android_probe_steps(req_text, app),
    }
    flow_path.write_text(json.dumps(flow, ensure_ascii=False, indent=2))
    update_runtime_state(task_dir, probeFlow=str(flow_path), androidApp=app)
    return flow_path, flow



def write_iter_record_files(task_dir: Path, iteration: int, iter_log_dir: Path, runner_cmd: list[str], evaluation: Optional[dict] = None) -> None:
    """\u5199\u5165 env.json / commands.md，\u8ba9\u6bcf\u8f6e\u9a8c\u8bc1\u8bb0\u5f55Reusable。"""
    ensure_dir(iter_log_dir)
    env = collect_env_snapshot(task_dir, iteration, runner_cmd)
    (iter_log_dir / "env.json").write_text(json.dumps(env, ensure_ascii=False, indent=2))

    commands = [
        f"# Iteration {iteration} Commands",
        "",
        f"- cwd: `{AUTOMIND_WORKSPACE_ROOT}`",
        "- runner:",
        "```bash",
        " ".join(runner_cmd),
        "```",
    ]
    if evaluation:
        commands.extend([
            "",
            "- evaluation:",
            f"  - result: `{evaluation.get('result')}`",
            f"  - nextAction: `{evaluation.get('nextAction')}`",
            f"  - summary: {evaluation.get('summary')}",
        ])
    (iter_log_dir / "commands.md").write_text("\n".join(commands) + "\n")


def _adapter_hard_metric(result: str, evidence_ref: str = "") -> dict:
    """Return the deterministic adapter metric used to anchor proved TC rows."""
    result_passed = result == "pass"
    metric = {
        "name": "adapter_result",
        "value": result,
        "expected": "pass",
        "passed": result_passed,
    }
    if evidence_ref:
        metric["evidence"] = evidence_ref
    return metric


def _assessment_has_proved_anchor(assessment: dict) -> bool:
    """Return True when a proved verdict already has a completion-gate anchor."""
    if not isinstance(assessment, dict):
        return False
    hard_metrics = assessment.get("hardMetrics")
    if isinstance(hard_metrics, list):
        for metric in hard_metrics:
            if not isinstance(metric, dict):
                continue
            if metric.get("passed") is True:
                return True
            if "expected" in metric and metric.get("value") == metric.get("expected"):
                return True
    secondary = assessment.get("secondaryAssessment")
    if isinstance(secondary, dict):
        sec_verdict = str(secondary.get("verdict") or "").strip().lower()
        if sec_verdict in {"proved", "manual_confirmed"}:
            primary_assessor = str(assessment.get("assessor") or "").strip().lower()
            secondary_assessor = str(secondary.get("assessor") or "").strip().lower()
            independent = secondary.get("independent")
            if independent is True or (independent is not False and secondary_assessor and secondary_assessor != primary_assessor):
                return True
    return False


def _row_acceptance_criteria(row: dict, declared_by_norm: dict[str, dict]) -> list[str]:
    tc_id = str(row.get("testCaseId") or row.get("id") or row.get("name") or "")
    declared = declared_by_norm.get(re.sub(r"[-_\s]", "", tc_id).upper(), {})
    acceptance = row.get("acceptanceCriteria") or row.get("ac") or declared.get("acceptanceCriteria") or []
    if isinstance(acceptance, list):
        return [str(item) for item in acceptance if str(item).strip()]
    if isinstance(acceptance, str):
        return [item for item in re.findall(r"\bAC[-_]?\d{2,3}\b", acceptance, flags=re.IGNORECASE)]
    return []


def _merge_unique_strings(existing, additions: list[str]) -> list[str]:
    merged: list[str] = []
    for value in [*(existing if isinstance(existing, list) else []), *additions]:
        text = str(value).strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def add_test_results_from_declared_cases(
    task_dir: Path,
    evaluation: dict,
    result: str,
    evidence_refs: list[str],
    reason: str,
) -> dict:
    """Populate or enrich adapter-owned testResults for one-shot evaluators.

    The adapter's own result is mapped to required TestCases. Optional/quality
    cases remain not_run unless the adapter reports them explicitly. If an
    adapter already emitted explicit rows, preserve them but repair deterministic
    adapter-owned pass rows so ``evidenceAssessment.verdict=proved`` always has
    the hardMetrics anchor required by completion-check.
    """
    declared = extract_declared_testcases(task_dir)
    declared_by_norm = {tc.get("normalizedId"): tc for tc in declared}
    primary_evidence = evidence_refs[0] if evidence_refs else ""
    result_passed = result == "pass"
    adapter_metric = _adapter_hard_metric(result, primary_evidence)

    explicit_results = evaluation.get("testResults")
    if isinstance(explicit_results, list) and explicit_results:
        enriched_rows: list[dict] = []
        for raw_row in explicit_results:
            if not isinstance(raw_row, dict):
                enriched_rows.append(raw_row)
                continue
            row = dict(raw_row)
            row_result = str(row.get("result") or result or "").strip().lower()
            assessment = row.get("evidenceAssessment")
            if not isinstance(assessment, dict):
                assessment = {}
            else:
                assessment = dict(assessment)
            assessor = str(assessment.get("assessor") or "").strip().lower()
            verdict = str(assessment.get("verdict") or "").strip().lower()
            adapter_owned = assessor in {"deterministic_adapter", "script-command"}
            # Only deterministic adapter-owned proved pass rows are auto-anchored.
            # Model/Evaluator semantic proof still needs its own hardMetrics or
            # an independent secondaryAssessment, preserving completion strictness.
            if row_result == "pass" and verdict == "proved" and adapter_owned and not _assessment_has_proved_anchor(assessment):
                metric_evidence = primary_evidence
                row_evidence = row.get("evidence")
                if isinstance(row_evidence, list) and row_evidence:
                    metric_evidence = str(row_evidence[0])
                assessment["hardMetrics"] = [
                    *([m for m in assessment.get("hardMetrics", []) if isinstance(m, dict)] if isinstance(assessment.get("hardMetrics"), list) else []),
                    _adapter_hard_metric("pass", metric_evidence),
                ]
                assessment.setdefault("reason", reason)
                row["evidenceAssessment"] = assessment
                row["evidence"] = _merge_unique_strings(row.get("evidence"), evidence_refs)
                row["acceptanceCriteria"] = _row_acceptance_criteria(row, declared_by_norm)
            enriched_rows.append(row)
        return {**evaluation, "testResults": enriched_rows}

    return attach_required_test_results(
        task_dir,
        evaluation,
        result,
        evidence_refs,
        reason,
        source="deterministic_adapter",
        observed_signals=[reason] if result_passed else [],
    )


def classify_android_probe_failure_detail(reason: str) -> dict[str, Any]:
    """Rich model-first variant: classify an Android probe-flow failure reason.

    Returns a dict with:
      category: str — the thin-wrapper's first tuple element.
      message: str — the thin-wrapper's second tuple element.
      triageSource: "code_deterministic" when a specific signature matched.
          "requires_model_review" for the catch-all ``validation_failure``
          fallback, which routes to the product-defect path; misrouting an
          unrecognized infra/env reason there is a high-risk default.
      needsModelReview: True iff the fallback fired.
      matchedKeyword: which signature bucket fired.
      reason: short human-readable description (distinct from input ``reason``).
    """
    lower = (reason or "").lower()
    if "blocked_sensitive" in lower or "sensitive overlay" in lower or "requires authorization" in lower:
        return {
            "category": "permission_blocked",
            "message": "UI overlay requires explicit authorization; auto-unblock is limited to safe dismiss/close actions.",
            "triageSource": "code_deterministic", "needsModelReview": False,
            "matchedKeyword": "blocked_sensitive",
            "reason": "命中敏感弹窗/需要授权信号。",
        }
    if "install_failed_update_incompatible" in lower or "signatures do not match" in lower:
        return {
            "category": "environment_blocked",
            "message": "Android install blocked: same package is already installed with a different signature.",
            "triageSource": "code_deterministic", "needsModelReview": False,
            "matchedKeyword": "signature_mismatch",
            "reason": "命中安装签名不匹配信号。",
        }
    if "can't find any android device" in lower or "no devices" in lower or "device offline" in lower:
        return {
            "category": "system_or_external_dependency",
            "message": "Android device/emulator is unavailable or offline.",
            "triageSource": "code_deterministic", "needsModelReview": False,
            "matchedKeyword": "device_unavailable",
            "reason": "命中设备不可用/离线信号。",
        }
    if "permission denied" in lower or "not authorized" in lower or "unauthorized" in lower:
        return {
            "category": "permission_blocked",
            "message": "Android device permission/authorization blocked verification.",
            "triageSource": "code_deterministic", "needsModelReview": False,
            "matchedKeyword": "permission_denied",
            "reason": "命中权限/授权拒绝信号。",
        }
    return {
        "category": "validation_failure",
        "message": reason or "probe-flow step failed",
        "triageSource": "requires_model_review", "needsModelReview": True,
        "matchedKeyword": "fallback",
        "reason": "没有命中任何已知签名，兜底归类为 validation_failure（=产品缺陷路径）；这是高风险默认，模型应复核原始 reason 确认是否其实是环境/设备/权限类问题而非产品缺陷。",
    }


def classify_android_probe_failure(reason: str) -> tuple[str, str]:
    """Classify an Android probe-flow failure reason into (category, message).
    Thin wrapper over classify_android_probe_failure_detail; callers needing
    triage metadata should use the _detail variant."""
    detail = classify_android_probe_failure_detail(reason)
    return detail["category"], detail["message"]


def infer_probe_target_testcases(task_dir: Path, summary: dict) -> list[str]:
    """Infer target TC ids for a probe-flow result without constraining scheduling.

    Prefer explicit flow/summary targetTestCases. Fallback to required device/runtime
    cases because Android probe-flow evidence is usually runtime proof.
    """
    explicit = summary.get("targetTestCases") or summary.get("testCases")
    if isinstance(explicit, list):
        ids = [str(x).strip() for x in explicit if str(x).strip()]
        if ids:
            return ids
    flow_ref = summary.get("flow")
    if isinstance(flow_ref, str) and flow_ref.strip():
        try:
            flow = json.loads(Path(flow_ref).read_text())
            explicit = flow.get("targetTestCases") or flow.get("testCases")
            if isinstance(explicit, list):
                ids = [str(x).strip() for x in explicit if str(x).strip()]
                if ids:
                    return ids
        except (OSError, json.JSONDecodeError):
            pass
    declared = extract_declared_testcases(task_dir)
    runtime_ids = [
        tc.get("id") for tc in declared
        if tc.get("required") and _normalize_runtime_level(tc.get("runtimeLevel")) in {"device", "runtime"}
    ]
    return [str(x) for x in runtime_ids if x]


def build_probe_test_results(
    task_dir: Path,
    summary: dict,
    iteration: int,
    result: str,
    evidence_items: list[dict],
    reason: str,
    missing_signals: list[str] | None = None,
    observed_signals: list[str] | None = None,
) -> list[dict]:
    target_ids = infer_probe_target_testcases(task_dir, summary)
    if not target_ids:
        return []
    evidence_refs = []
    for item in evidence_items:
        if isinstance(item, dict) and item.get("path"):
            evidence_refs.append(str(item.get("path")))
    normalized = "pass" if result == "pass" else "partial" if result == "partial" else "fail" if result == "fail" else "blocked"
    verdict = "proved" if normalized == "pass" else "not_proved"
    flow_kind = str(summary.get("flowKind") or "").strip().lower()
    progress_kind = str(summary.get("progressKind") or "").strip().lower()
    if progress_kind not in {"navigation", "control_discovery", "proof", "evidence", "repair", "blocked", "unknown"}:
        if normalized == "pass":
            progress_kind = "proof"
        elif missing_signals:
            progress_kind = "evidence"
        elif flow_kind == "discovery":
            progress_kind = "control_discovery"
        else:
            progress_kind = "unknown"
    action_trace = summary.get("actionTrace") if isinstance(summary.get("actionTrace"), list) else []
    attempts = []
    for idx, action in enumerate(action_trace, start=1):
        if not isinstance(action, dict):
            continue
        action_name = str(action.get("name") or action.get("intent") or action.get("type") or "UI action")
        action_type = str(action.get("type") or "action")
        ok = bool(action.get("ok"))
        attempts.append({
            "attemptId": f"A{idx}",
            "progressKind": "proof" if ok and normalized == "pass" else "navigation" if ok else "control_discovery",
            "hypothesis": str(action.get("intent") or action_name),
            "actionTried": f"{action_type}: {action_name}",
            "expectedSignal": str(summary.get("expectedSignal") or "post-action page/state evidence"),
            "outcome": "success" if ok else str(action.get("detail") or "failed"),
            "evidence": [x for x in [
                ((action.get("evidenceAfter") or {}).get("screenshot") if isinstance(action.get("evidenceAfter"), dict) else None),
                ((action.get("evidenceAfter") or {}).get("hierarchy") if isinstance(action.get("evidenceAfter"), dict) else None),
                action.get("evidence"),
            ] if x],
            "ruledOut": [] if ok else [str(action.get("detail") or action_name)],
            "remainingHypotheses": summary.get("remainingHypotheses") or [],
            **({"extracted": action.get("extracted")} if isinstance(action.get("extracted"), dict) else {}),
        })
    extracted = {}
    for action in action_trace:
        if isinstance(action, dict) and isinstance(action.get("extracted"), dict):
            extracted.update(action.get("extracted") or {})
    ui_exploration = {
        "goal": summary.get("goal") or summary.get("hypothesis") or reason,
        "mode": summary.get("appUseMode") or summary.get("mode") or "unknown",
        "platform": "android",
        "attempts": attempts,
        "stopReason": "proved" if normalized == "pass" else "needs_repair" if normalized in {"partial", "fail"} else "blocked",
        **({"extracted": extracted} if extracted else {}),
    }
    return [{
        "testCaseId": tc_id,
        "result": normalized,
        "attemptIteration": iteration,
        "progressKind": progress_kind,
        "attemptType": summary.get("attemptType") or ("proof" if progress_kind == "proof" else "exploration"),
        "hypothesis": summary.get("hypothesis") or "",
        "actionTried": summary.get("actionTried") or summary.get("nextActionTried") or "",
        "expectedSignal": summary.get("expectedSignal") or "",
        "outcome": summary.get("outcome") or reason,
        "ruledOut": summary.get("ruledOut") or [],
        "remainingHypotheses": summary.get("remainingHypotheses") or [],
        "uiExploration": ui_exploration,
        "evidence": evidence_refs,
        "observedSignals": observed_signals or [],
        "missingSignals": missing_signals or [],
        "summary": reason,
        "reason": reason,
        "source": "android-probe-flow",
        "evidenceAssessment": {
            "verdict": verdict,
            "assessor": "deterministic_adapter",
            "reason": reason,
            "hardMetrics": [{
                "name": "android_probe_flow_result",
                "passed": normalized == "pass",
                "evidence": evidence_refs[0] if evidence_refs else "",
            }],
        },
    } for tc_id in target_ids]


def _task_dir_from_probe_summary_path(summary_path: Path) -> Path:
    """Infer task dir from probe-flow-summary.json paths.

    Supports both legacy `logs/iter-N/probe-flow-summary.json` and current
    nested `logs/iter-N/probe-flow/probe-flow-summary.json` layouts.
    """
    cur = summary_path.parent
    for parent in [cur, *cur.parents]:
        if parent.name == "logs":
            return parent.parent
    return summary_path.parent


def probe_summary_to_evaluation(summary: dict, iteration: int, summary_path: Path) -> dict:
    """Convert probe-flow-summary.json into AutoMind evaluation.json."""
    task_dir = _task_dir_from_probe_summary_path(summary_path)
    result = summary.get("result", "fail")
    failed_checks = []
    for item in summary.get("checks", []):
        if isinstance(item, dict) and not item.get("ok"):
            raw_reason = item.get("detail", "probe-flow step failed")
            triage = classify_android_probe_failure_detail(str(raw_reason))
            failed_checks.append({
                "name": item.get("name", "probe_flow_step"),
                "reason": triage["message"],
                "rawReason": raw_reason,
                "category": triage["category"],
                "evidence": item.get("evidence") or str(summary_path),
                # Model-first triage: surface whether code confidently classified
                # this failure (code_deterministic) or fell back to the generic
                # validation_failure default (requires_model_review). The isolated
                # Evaluator reads evaluation.json and must re-examine rawReason
                # itself instead of trusting a blind fallback category.
                "triageSource": triage["triageSource"],
                "needsModelReview": triage["needsModelReview"],
            })

    overlay = summary.get("uiUnblock") if isinstance(summary.get("uiUnblock"), dict) else {}
    overlay_sensitive_candidates = None
    if overlay:
        overlay_sensitive_candidates = overlay.get("sensitiveCandidates")
        if not overlay_sensitive_candidates:
            for attempt in overlay.get("attempts") or []:
                if isinstance(attempt, dict) and attempt.get("sensitiveCandidates"):
                    overlay_sensitive_candidates = attempt.get("sensitiveCandidates")
                    break
        if result != "pass" and overlay_sensitive_candidates and not any(
            isinstance(item, dict) and item.get("name") == "ui_overlay_authorization"
            for item in failed_checks
        ):
            failed_checks.append({
                "name": "ui_overlay_authorization",
                "reason": "UI overlay requires explicit authorization; safe auto-unblock cannot click sensitive or ambiguous actions.",
                "category": "permission_blocked",
                "evidence": overlay.get("evidence") or str(summary_path),
            })

    evidence_items = [{"type": "other", "note": "probe-flow-summary", "path": str(summary_path)}]
    action_trace_path = summary_path.parent / "action-trace.jsonl"
    if action_trace_path.exists():
        evidence_items.append({"type": "other", "note": "ui-action-trace", "path": str(action_trace_path)})
    screenshots = []
    for step in summary.get("stepResults", []) or []:
        if isinstance(step, dict) and step.get("screenshotAfter"):
            screenshots.append(step.get("screenshotAfter"))
    for shot in screenshots[:5]:
        evidence_items.append({"type": "screenshot", "note": "critical-action-after", "path": str(shot)})

    if result == "partial":
        strong_post_checks = summary.get("strongPostChecks") if isinstance(summary.get("strongPostChecks"), list) else []
        semantic = summary.get("semanticVerdict") if isinstance(summary.get("semanticVerdict"), dict) else {}
        missing = [str(x).strip() for x in (semantic.get("missingEvidence") or []) if str(x).strip()]
        if not missing:
            for item in strong_post_checks:
                if isinstance(item, dict):
                    missing.extend(str(x).strip() for x in (item.get("missingSignals") or []) if str(x).strip())
        reason = semantic.get("reason") or "Android probe-flow made progress, but required proof evidence is incomplete"
        return {
            "iteration": iteration,
            "result": "partial",
            "summary": reason,
            "failedChecks": [{
                "name": "probe_flow_semantic_gate",
                "reason": "missing proof evidence: " + ", ".join(missing[:8]) if missing else "probe-flow proof evidence is incomplete",
                "category": "validation_incomplete",
                "evidence": str(summary_path),
            }],
            "evidence": evidence_items,
            "testResults": build_probe_test_results(task_dir, summary, iteration, "partial", evidence_items, reason, missing_signals=missing),
            "strongPostChecks": strong_post_checks,
            "semanticVerdict": semantic or {"result": "partial", "missingEvidence": missing},
            "nextAction": "retry_generator",
        }

    if result == "pass":
        post_checks = summary.get("postChecks") if isinstance(summary.get("postChecks"), list) else []
        if not post_checks:
            flow_ref = summary.get("flow")
            if isinstance(flow_ref, str) and flow_ref.strip():
                try:
                    flow_post_checks = json.loads(Path(flow_ref).read_text()).get("postChecks")
                    if isinstance(flow_post_checks, list):
                        post_checks = flow_post_checks
                except (OSError, json.JSONDecodeError):
                    post_checks = []
        strong_post_checks: list[dict] = []
        missing_signals: list[str] = []
        for item in post_checks:
            if not isinstance(item, dict):
                continue
            if str(item.get("strength") or "").strip().lower() != "strong":
                continue
            expected = [str(x).strip() for x in (item.get("expectedSignals") or []) if str(x).strip()]
            observed = [str(x).strip() for x in (item.get("observedSignals") or []) if str(x).strip()]
            missing = [signal for signal in expected if signal not in observed]
            strong_post_checks.append({
                "type": str(item.get("type") or "strong_post_check"),
                "strength": "strong",
                "status": "missing" if missing else "proved",
                "expectedSignals": expected,
                "observedSignals": observed,
                "missingSignals": missing,
            })
            missing_signals.extend(missing)

        if missing_signals:
            reason = "Android probe-flow passed startup checks, but strong runtime evidence is still missing"
            return {
                "iteration": iteration,
                "result": "partial",
                "summary": reason,
                "failedChecks": [{
                    "name": check.get("type", "strong_post_check"),
                    "reason": "missing strong post-check evidence: " + ", ".join(check.get("missingSignals", [])[:6]),
                    "category": "validation_incomplete",
                    "evidence": str(summary_path),
                } for check in strong_post_checks if check.get("missingSignals")],
                "evidence": evidence_items,
                "testResults": build_probe_test_results(task_dir, summary, iteration, "partial", evidence_items, reason, missing_signals=missing_signals),
                "strongPostChecks": strong_post_checks,
                "semanticVerdict": {
                    "result": "partial",
                    "reason": "startup/install/launch evidence exists, but strong post-check signals required for behavioral proof are missing",
                    "missingEvidence": missing_signals,
                },
                "nextAction": "retry_generator",
            }

        reason = "Android probe-flow runner passed"
        observed_signals: list[str] = []
        for check in strong_post_checks:
            observed_signals.extend(str(x) for x in (check.get("observedSignals") or []) if str(x).strip())
        return {
            "iteration": iteration,
            "result": "pass",
            "summary": reason,
            "failedChecks": [],
            "evidence": evidence_items,
            "testResults": build_probe_test_results(task_dir, summary, iteration, "pass", evidence_items, reason, observed_signals=observed_signals),
            "strongPostChecks": strong_post_checks,
            "semanticVerdict": {
                "result": "proved",
                "reason": "probe-flow summary satisfied required strong post-check signals",
                "missingEvidence": [],
            },
            "nextAction": "finish",
        }

    checks = failed_checks or [{
        "name": "probe_flow_runner",
        "reason": "runner returned fail without failed check detail",
        "category": "validation_failure",
        "evidence": str(summary_path),
    }]
    categories = {str(item.get("category")) for item in checks if isinstance(item, dict)}
    next_action = "ask_user" if categories & {"environment_blocked", "system_or_external_dependency", "permission_blocked"} else "retry_generator"
    eval_result = "blocked" if next_action == "ask_user" else "fail"
    reason = "Android probe-flow runner blocked" if next_action == "ask_user" else "Android probe-flow runner failed"
    evaluation = {
        "iteration": iteration,
        "result": eval_result,
        "summary": reason,
        "failedChecks": checks,
        "evidence": evidence_items,
        "testResults": build_probe_test_results(task_dir, summary, iteration, eval_result, evidence_items, reason),
        "nextAction": next_action,
    }
    if next_action == "ask_user" and categories & {"permission_blocked"}:
        if overlay_sensitive_candidates:
            evaluation["askUserQuestion"] = {
                "category": "unauthorized_destructive_or_sensitive",
                "question": "A UI overlay blocks the Android verification flow, but the available action is sensitive or ambiguous. May AutoMind perform the explicitly listed action for this verification run?",
                "reason": "Auto-unblock is limited to safe dismiss/close actions. Sensitive candidates were detected in overlay-unblock evidence.",
                "options": [
                    {"id": "authorize_once", "label": "Authorize this action once", "impact": "AutoMind may continue the same runtime verification path for this task only."},
                    {"id": "manual_handle", "label": "I will handle it manually", "impact": "User clears the overlay/device state, then AutoMind retries verification."},
                    {"id": "replan", "label": "Replan verification", "impact": "Avoid this sensitive overlay or use another verifier path."},
                ],
                "recommended": "manual_handle",
                "evidence": [str(summary_path), str(action_trace_path)] if action_trace_path.exists() else [str(summary_path)],
                "sensitiveCandidates": overlay_sensitive_candidates[:5] if isinstance(overlay_sensitive_candidates, list) else overlay_sensitive_candidates,
            }
    return evaluation



def extract_script_command(task_dir: Path) -> tuple[str | None, list[str]]:
    """Extract a generic verification command for non-mobile/script tasks.

    Priority:
    1. runtime-state.json `scriptCommand`
    2. fenced command marker in Requirements.md / Plan.md: `scriptCommand: ...` or `verifyCommand: ...`
    """
    warnings: list[str] = []
    state = read_runtime_state(task_dir) or {}
    cmd = state.get("scriptCommand") or state.get("verifyCommand")
    if isinstance(cmd, str) and cmd.strip():
        return cmd.strip(), warnings

    docs = []
    for path in [*requirement_contract_paths(task_dir), task_dir / "Plan.md"]:
        if path.exists():
            docs.append(path.read_text(errors="ignore"))
    text = "\n".join(docs)
    for pattern in [
        r"(?:scriptCommand|verifyCommand|\u9a8c\u8bc1\u547d\u4ee4|\u8fd0\u884c\u547d\u4ee4)\s*[:：]\s*`([^`\n]+)`",
        r"(?:scriptCommand|verifyCommand|\u9a8c\u8bc1\u547d\u4ee4|\u8fd0\u884c\u547d\u4ee4)\s*[:：]\s*([^\n]+)",
    ]:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            value = m.group(1).strip()
            if value:
                return value, warnings
    warnings.append("missing_script_command")
    return None, warnings


def script_command_no_screenshot_reason(tc: dict) -> str | None:
    """Return a noScreenshotReason for non-UI script-command testcases.

    Completion requires screenshot evidence for runtime/device UI flows by
    default. A generic script-command verifier may still declare
    runtime/static-runtime level because it executes code, but when the testcase
    surface is command/log/API/file based and does not ask for screenshot/UI
    evidence, the correct artifact is an explicit noScreenshotReason plus
    machine-checkable command evidence.
    """
    testcase_type = str(tc.get("type") or "").lower()
    source = " ".join(
        str(tc.get(key) or "")
        for key in ["preconditions", "command", "steps", "expectedEvidence", "source"]
    ).lower()
    text = f"{testcase_type} {source}"
    ui_substrings = [
        "app/ui",
        "screen",
        "page",
        "visual",
        "screenshot",
        "xcresult",
        "hierarchy",
        "browser",
        "playwright",
        "tap",
        "click",
        "swipe",
        "xctest",
        "xcuitest",
        "uiautomator",
        "截图",
        "页面",
        "界面",
        "视觉",
        "点击",
    ]
    ui_word_patterns = [
        r"\bui\b",
        r"\bdom\b",
    ]
    if any(token in text for token in ui_substrings) or any(re.search(pattern, text) for pattern in ui_word_patterns):
        return None
    return (
        "No screenshot captured because this script-command testcase verifies a "
        "non-UI command/log/API/file surface; command log, env snapshot, exit "
        "code, and hardMetrics are the machine-checkable evidence."
    )


def run_script_command_evaluator(task_dir: Path, iteration: int, iter_log_dir: Path) -> tuple[int, str]:
    """Run a project-agnostic verification command and emit evaluation.json.

    This is the minimal non-mobile harness path: any stack can participate in
    AutoMind if it can expose a command whose exit code represents pass/fail.
    """
    command, warnings = extract_script_command(task_dir)
    ensure_dir(iter_log_dir)
    if not command and is_android_probe_flow_task(task_dir):
        msg = "script-command invoked for Android task without explicit command; rerouting to android-probe-flow"
        (iter_log_dir / "script-command-reroute.log").write_text(msg + "\n")
        code, output = run_android_probe_flow_evaluator(task_dir, iteration, iter_log_dir, retries=0)
        return code, msg + "\n" + output

    if not command:
        evaluation = {
            "iteration": iteration,
            "result": "blocked",
            "summary": "No explicit scriptCommand/verifyCommand; generic script-command is not applicable",
            "failedChecks": [{
                "name": "script_command",
                "reason": "generic script-command only runs when runtime-state.json or Requirements/Plan declares scriptCommand/verifyCommand; choose a platform/project-native Evaluator instead",
                "category": "evaluator_route_unavailable",
                "evidence": "runtime-state.json / Requirements.md / Plan.md",
            }],
            "warnings": warnings,
            "nextAction": "replan",
        }
        write_evaluation_json(task_dir, evaluation)
        write_iter_record_files(task_dir, iteration, iter_log_dir, [], evaluation)
        return 0, json.dumps(evaluation, ensure_ascii=False, indent=2)

    started = datetime.now()
    proc = subprocess.run(["bash", "-lc", command], cwd=str(AUTOMIND_WORKSPACE_ROOT), text=True, capture_output=True, timeout=runtime_timeout("AUTOMIND_SCRIPT_COMMAND_TIMEOUT"))
    output = proc.stdout + proc.stderr
    log_path = iter_log_dir / "script-command.log"
    log_path.write_text(output)
    # Keep the standard evaluator.log alias so record-check and future agents do
    # not need script-command-specific knowledge.
    (iter_log_dir / "evaluator.log").write_text(output)
    duration_ms = int((datetime.now() - started).total_seconds() * 1000)

    commands_path = iter_log_dir / "commands.md"
    commands_path.write_text(f"# Commands\n\n```bash\n{command}\n```\n")
    env = collect_env_snapshot(task_dir, iteration, ["bash", "-lc", command])
    (iter_log_dir / "env.json").write_text(json.dumps(env, ensure_ascii=False, indent=2))
    declared_testcases = extract_declared_testcases(task_dir)

    def script_command_test_results(command_passed: bool) -> list[dict]:
        """Map a single script-command result to declared testcase rows.

        For generic script tasks, the configured command is the deterministic
        evaluator for the selected required batch. Optional/quality rows remain
        `not_run` unless a richer evaluator reports them explicitly.
        """
        rows: list[dict] = []
        for tc in declared_testcases:
            if tc.get("required"):
                no_screenshot_reason = script_command_no_screenshot_reason(tc)
                row = {
                    "testCaseId": tc["id"],
                    "result": "pass" if command_passed else "fail",
                    "required": True,
                    "quality": bool(tc.get("quality", False)),
                    "acceptanceCriteria": tc.get("acceptanceCriteria", []),
                    "evidence": [str(log_path), f"logs/iter-{iteration}/commands.md", f"logs/iter-{iteration}/env.json"],
                    "observedSignals": ["script-command exit code 0"] if command_passed else [],
                    "missingSignals": [] if command_passed else [f"script-command exit code {proc.returncode}"],
                    "reason": "script-command exit code 0" if command_passed else f"script-command exit code {proc.returncode}",
                    "evidenceAssessment": {
                        "verdict": "proved" if command_passed else "not_proved",
                        "assessor": "script-command",
                        "reason": "Configured command exit code represents the selected testcase verdict; logs/commands/env are attached as hard evidence.",
                        "hardMetrics": [
                            {
                                "name": "exit_code",
                                "value": int(proc.returncode),
                                "expected": 0,
                                "passed": bool(command_passed),
                                "evidence": str(log_path),
                            }
                        ],
                    },
                }
                if no_screenshot_reason:
                    row["noScreenshotReason"] = no_screenshot_reason
                rows.append({
                    **row,
                })
            else:
                rows.append({
                    "testCaseId": tc["id"],
                    "result": "not_run",
                    "required": False,
                    "quality": bool(tc.get("quality", False)),
                    "acceptanceCriteria": tc.get("acceptanceCriteria", []),
                    "evidence": [],
                    "reason": "Optional testcase not selected by script-command evaluator.",
                })
        return rows

    if proc.returncode == 0:
        evaluation = {
            "iteration": iteration,
            "result": "pass",
            "summary": "Script command verification passed",
            "failedChecks": [],
            "evidence": [{"type": "log", "path": str(log_path), "note": "script-command-log"}],
            "testResults": script_command_test_results(True),
            "metrics": {"durationMs": duration_ms},
            "nextAction": "finish",
        }
    else:
        evaluation = {
            "iteration": iteration,
            "result": "fail",
            "summary": f"Script command verification failed with exit code {proc.returncode}",
            "failedChecks": [{
                "name": "script_command",
                "reason": f"command exited with code {proc.returncode}: {command}\n--- output tail ---\n{output[-1000:]}",
                "category": "test_failure",
                "evidence": str(log_path),
            }],
            "evidence": [{"type": "log", "path": str(log_path), "note": "script-command-log"}],
            "testResults": script_command_test_results(False),
            "metrics": {"durationMs": duration_ms},
            "nextAction": "retry_generator",
        }
    evaluation, _completion_report = apply_completion_gate(
        task_dir,
        evaluation,
        allow_synthesize_pass=False,
        fail_next_action="retry_generator",
    )
    write_evaluation_json(task_dir, evaluation)
    write_iter_record_files(task_dir, iteration, iter_log_dir, ["bash", "-lc", command], evaluation)
    append_script_command_validation(task_dir, iteration, command, evaluation, iter_log_dir)
    return 0, output

def run_android_preflight_evaluator(task_dir: Path, iteration: int, iter_log_dir: Path) -> tuple[bool, dict | None, str]:
    """Run Android preflight before real probe-flow execution."""
    script = AUTOMIND_ROOT / "scripts" / "android_preflight.py"
    cmd = [sys.executable, str(script), task_dir.name, str(iteration)]
    state = read_runtime_state(task_dir) or {}
    android_app = state.get("androidApp") or {}
    if isinstance(android_app, dict) and android_app.get("serial"):
        cmd += ["--serial", str(android_app.get("serial"))]
    proc = subprocess.run(cmd, cwd=str(AUTOMIND_WORKSPACE_ROOT), text=True, capture_output=True, timeout=runtime_timeout("AUTOMIND_PREFLIGHT_TIMEOUT", 300))
    output = proc.stdout + proc.stderr
    ensure_dir(iter_log_dir)
    (iter_log_dir / "android-preflight-before-probe-flow.log").write_text(output)
    evaluation = read_evaluation_json(task_dir)
    return bool(
        evaluation
        and evaluation.get("result") in {"pass", "partial", "in_progress"}
        and evaluation.get("nextAction") in {"finish", "retry_generator"}
    ), evaluation, output



def run_ui_evidence_gate(task_dir: Path, iteration: int, evaluation: dict) -> dict:
    """Run UI evidence completeness gate and attach its report to evaluation."""
    script = AUTOMIND_ROOT / "scripts" / "ui_evidence_check.py"
    if not script.exists():
        return evaluation
    proc = subprocess.run(
        [sys.executable, str(script), task_dir.name, str(iteration), "--json"],
        cwd=str(AUTOMIND_WORKSPACE_ROOT),
        text=True,
        capture_output=True,
        timeout=runtime_timeout("AUTOMIND_UI_EVIDENCE_TIMEOUT", 300),
    )
    report_rel = f"logs/iter-{iteration}/ui-evidence-check.json"
    evidence = evaluation.setdefault("evidence", [])
    if not any(isinstance(item, dict) and item.get("path") == report_rel for item in evidence):
        evidence.append({"type": "other", "note": "ui-evidence-check", "path": report_rel})
    report_path = task_dir / report_rel
    report = {}
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text())
        except json.JSONDecodeError:
            report = {}
    if proc.returncode != 0 and report.get("result") != "fail":
        report = {"result": "fail", "summary": (proc.stdout + proc.stderr)[-1000:] or "ui-evidence-check failed to run"}
    if report.get("result") == "fail":
        evaluation.setdefault("failedChecks", []).append({
            "name": "ui_evidence_check",
            "category": "evidence_incomplete",
            "reason": report.get("summary") or "UI evidence completeness check failed",
            "evidence": report_rel,
        })
        if evaluation.get("result") == "pass" or evaluation.get("nextAction") == "finish":
            evaluation["result"] = "blocked"
            evaluation["nextAction"] = "retry_generator"
            evaluation["summary"] = "UI evidence check blocked completion: " + str(report.get("summary") or "evidence incomplete")
    return evaluation

def run_android_probe_flow_evaluator(task_dir: Path, iteration: int, iter_log_dir: Path, dry_run: bool = False, force_flow: bool = False, retries: int = 0) -> tuple[int, str]:
    """\u81ea\u52a8Generate/run Android probe-flow，\u5e76\u5199\u56de evaluation.json / Validation.md \u6240\u9700Evidence。"""
    record_subphase_start(task_dir, SUBPHASE_FLOW_GENERATION, platform="android", iteration=iteration)
    flow_path, flow_or_error = generate_probe_flow_json(task_dir, force=force_flow)
    record_subphase_end(task_dir, SUBPHASE_FLOW_GENERATION, platform="android", iteration=iteration)
    if not flow_path:
        evaluation = {
            "iteration": iteration,
            "result": "blocked",
            "summary": "Missing Android app config; cannot generate probe-flow.json automatically",
            "failedChecks": [{
                "name": "android_app_config",
                "reason": (flow_or_error or {}).get("reason", "missing_android_app_config"),
                "category": "needs_replan",
                "evidence": "runtime-state.json / Requirements.md / Delivery.md",
            }],
            "nextAction": "replan",
            "warnings": (flow_or_error or {}).get("warnings", []),
        }
        write_evaluation_json(task_dir, evaluation)
        write_iter_record_files(task_dir, iteration, iter_log_dir, [], evaluation)
        return 0, json.dumps(evaluation, ensure_ascii=False, indent=2)

    # Real device execution should first pass platform readiness checks.
    # Dry-run skips preflight so schema/config checks can run without a device.
    if not (dry_run or os.environ.get("AUTOMIND_ANDROID_PROBE_DRY_RUN") == "1"):
        record_subphase_start(task_dir, SUBPHASE_PREFLIGHT, platform="android", iteration=iteration)
        preflight_ok, preflight_eval, preflight_output = run_android_preflight_evaluator(task_dir, iteration, iter_log_dir)
        record_subphase_end(task_dir, SUBPHASE_PREFLIGHT, platform="android", iteration=iteration)
        if not preflight_ok:
            evaluation = preflight_eval or {
                "iteration": iteration,
                "result": "blocked",
                "summary": "Android probe-flow blocked by preflight",
                "failedChecks": [{"name": "android_preflight", "category": "mobile_device_unavailable", "reason": preflight_output[-1000:]}],
                "nextAction": "replan",
            }
            evidence = evaluation.setdefault("evidence", [])
            evidence.append({"type": "log", "path": str(iter_log_dir / "android-preflight-before-probe-flow.log")})
            evaluation["summary"] = "Android probe-flow blocked by preflight: " + str(evaluation.get("summary", "preflight failed"))
            evaluation["nextAction"] = "ask_user" if evaluation.get("nextAction") == "ask_user" else "replan"
            write_evaluation_json(task_dir, evaluation)
            write_iter_record_files(task_dir, iteration, iter_log_dir, [], evaluation)
            return 0, json.dumps(evaluation, ensure_ascii=False, indent=2)

    app = (flow_or_error or {}).get("app", {}) if isinstance(flow_or_error, dict) else {}
    state = read_runtime_state(task_dir) or {}
    android_app = state.get("androidApp") or app
    build_command = android_app.get("buildCommand") if isinstance(android_app, dict) else None
    build_log = ""
    if build_command:
        record_subphase_start(task_dir, SUBPHASE_BUILD, platform="android", iteration=iteration)
        code, stdout, stderr = run_cmd(["bash", "-lc", build_command], cwd=str(AUTOMIND_WORKSPACE_ROOT))
        record_subphase_end(task_dir, SUBPHASE_BUILD, platform="android", iteration=iteration)
        build_log = stdout + stderr
        (iter_log_dir / "android-build.log").write_text(build_log)
        if code != 0:
            evaluation = {
                "iteration": iteration,
                "result": "fail",
                "summary": "Android APK build failed; probe-flow runner was not executed",
                "failedChecks": [{
                    "name": "android_build",
                    "reason": build_log[-1000:] or "build command failed",
                    "category": "build_failure",
                    "evidence": str(iter_log_dir / "android-build.log"),
                }],
                "nextAction": "retry_generator",
            }
            write_evaluation_json(task_dir, evaluation)
            write_iter_record_files(task_dir, iteration, iter_log_dir, ["bash", "-lc", build_command], evaluation)
            return 0, json.dumps(evaluation, ensure_ascii=False, indent=2)

    out_dir = iter_log_dir / "probe-flow"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    runner = AUTOMIND_ROOT / "scripts" / "android_probe_flow_runner.py"
    android_python = get_android_tools_python()
    runner_cmd = [
        android_python,
        str(runner),
        "--flow",
        str(flow_path),
        "--out",
        str(out_dir),
    ]
    if dry_run or os.environ.get("AUTOMIND_ANDROID_PROBE_DRY_RUN") == "1":
        runner_cmd.append("--dry-run")
    ensure_dir(iter_log_dir)
    max_attempts = max(1, int(retries) + 1)
    attempts: list[dict] = []
    output = ""
    code = 1
    summary_path = out_dir / "probe-flow-summary.json"
    record_subphase_start(task_dir, SUBPHASE_UI_EXECUTION, platform="android", iteration=iteration)
    for attempt in range(1, max_attempts + 1):
        code, stdout, stderr = run_cmd(runner_cmd, cwd=str(AUTOMIND_WORKSPACE_ROOT))
        attempt_output = stdout + stderr
        attempt_log = iter_log_dir / f"android-probe-flow-attempt-{attempt}.log"
        attempt_log.write_text(attempt_output)
        attempt_summary_result = None
        if summary_path.exists():
            try:
                attempt_summary_result = json.loads(summary_path.read_text()).get("result")
            except json.JSONDecodeError:
                attempt_summary_result = "invalid_json"
        attempts.append({
            "attempt": attempt,
            "exitCode": code,
            "summaryResult": attempt_summary_result,
            "log": f"logs/iter-{iteration}/android-probe-flow-attempt-{attempt}.log",
            "retried": attempt < max_attempts and (code != 0 or attempt_summary_result not in {"pass", None}),
        })
        output += f"\n--- attempt {attempt}/{max_attempts} exit={code} summary={attempt_summary_result} ---\n" + attempt_output
        if code == 0 and attempt_summary_result in {"pass", None}:
            break
    record_subphase_end(task_dir, SUBPHASE_UI_EXECUTION, platform="android", iteration=iteration)
    (iter_log_dir / "evaluator.log").write_text(output)

    record_subphase_start(task_dir, SUBPHASE_RESULT_ANALYSIS, platform="android", iteration=iteration)
    summary: dict = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text())
            summary["attempts"] = attempts
            summary["retries"] = max_attempts - 1
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n")
            evaluation = probe_summary_to_evaluation(summary, iteration, summary_path)
        except json.JSONDecodeError as exc:
            evaluation = {
                "iteration": iteration,
                "result": "fail",
                "summary": "probe-flow-summary.json is not valid JSON",
                "failedChecks": [{"name": "probe_flow_summary", "reason": str(exc), "category": "invalid_evaluation_output", "evidence": str(summary_path)}],
                "nextAction": "retry_generator",
            }
    else:
        lower_output = output.lower()
        if "modulenotfounderror" in lower_output or "no module named" in lower_output:
            category = "tool_missing"
        elif "no device" in lower_output or "device not found" in lower_output or "unauthorized" in lower_output:
            category = "mobile_device_unavailable"
        else:
            category = "validation_failure"
        next_action = "ask_user" if category in {"mobile_device_unavailable", "tool_missing"} else "retry_generator"
        evaluation = {
            "iteration": iteration,
            "result": "blocked" if category in {"mobile_device_unavailable", "tool_missing"} else "fail",
            "summary": "Android probe-flow runner did not generate summary",
            "failedChecks": [{
                "name": "probe_flow_runner",
                "reason": output[-1000:] or f"runner exited with code {code}",
                "category": category,
                "evidence": str(iter_log_dir / "evaluator.log"),
            }],
            "nextAction": next_action,
        }
        evaluation["attempts"] = attempts
        if next_action == "ask_user":
            evaluation["askUserQuestion"] = {
                "question": "Android probe-flow could not run because required device/tool readiness is missing. What should AutoMind do next?",
                "reason": output[-1000:] or f"runner exited with code {code}",
                "options": [
                    {"id": "fix_and_retry", "label": "Fix readiness and retry", "impact": "Connect/authorize device or repair local helper tools, then continue real verification."},
                    {"id": "switch_dry_run", "label": "Use dry-run temporarily", "impact": "Validate probe-flow shape without real-device evidence; completion still needs required runtime evidence."},
                    {"id": "replan_verification", "label": "Replan verification", "impact": "Revise TestCases/Plan for a runnable verification target."},
                ],
                "recommended": "fix_and_retry",
                "source": "android_probe_flow_runner",
                "defaultAction": "retry",
            }

    evidence = evaluation.setdefault("evidence", [])
    for item in attempts:
        if isinstance(item, dict):
            evidence.append({"type": "log", "note": f"android-probe-flow-attempt-{item.get('attempt')}", "path": item.get("log")})
    record_subphase_end(task_dir, SUBPHASE_RESULT_ANALYSIS, platform="android", iteration=iteration)

    if evaluation.get("nextAction") == "finish":
        evidence_refs = [str(summary_path)] if summary_path.exists() else [str(iter_log_dir / "evaluator.log")]
        evaluation = add_test_results_from_declared_cases(
            task_dir,
            evaluation,
            "pass",
            evidence_refs,
            "Android probe-flow adapter returned pass.",
        )
        record_subphase_start(task_dir, SUBPHASE_COMPLETION_GATE, platform="android", iteration=iteration)
        evaluation, _completion_report = apply_completion_gate(
            task_dir,
            evaluation,
            allow_synthesize_pass=False,
            fail_next_action="retry_generator",
        )
        record_subphase_end(task_dir, SUBPHASE_COMPLETION_GATE, platform="android", iteration=iteration)
        if evaluation.get("nextAction") == "finish":
            _cache_probe_flow_paths(task_dir, flow_path, summary if isinstance(summary, dict) else {})
    else:
        _expire_cached_probe_flow_on_failure(task_dir, flow_path, evaluation)

    if evaluation.get("testResults"):
        record_tc_attempts(task_dir, evaluation, source="android-probe-flow")
    write_evaluation_json(task_dir, evaluation)
    write_iter_record_files(task_dir, iteration, iter_log_dir, runner_cmd, evaluation)
    append_android_probe_flow_validation(task_dir, iteration, evaluation, iter_log_dir, summary if isinstance(summary, dict) else None)
    evaluation = run_ui_evidence_gate(task_dir, iteration, evaluation)
    write_evaluation_json(task_dir, evaluation)
    return 0, output


def run_ios_probe_flow_evaluator(task_dir: Path, iteration: int, iter_log_dir: Path, dry_run: bool = False) -> tuple[int, str]:
    """Run minimal iOS probe-flow by delegating to scripts/ios_probe_flow_runner.py."""
    script = AUTOMIND_ROOT / "scripts" / "ios_probe_flow_runner.py"
    cmd = [sys.executable, str(script), task_dir.name, str(iteration)]
    if dry_run:
        cmd.append("--dry-run")
    record_subphase_start(task_dir, SUBPHASE_UI_EXECUTION, platform="ios", iteration=iteration)
    proc = subprocess.run(cmd, cwd=str(AUTOMIND_WORKSPACE_ROOT), text=True, capture_output=True, timeout=runtime_timeout("AUTOMIND_EVALUATOR_TIMEOUT"))
    record_subphase_end(task_dir, SUBPHASE_UI_EXECUTION, platform="ios", iteration=iteration)
    output = proc.stdout + proc.stderr
    ensure_dir(iter_log_dir)
    (iter_log_dir / "ios-probe-flow-wrapper.log").write_text(output)
    if not (iter_log_dir / "evaluator.log").exists():
        (iter_log_dir / "evaluator.log").write_text(output)
    # The delegated runner writes evaluation.json / Validation.md. Add final
    # coverage when it reports finish but does not yet emit explicit testResults.
    evaluation = read_evaluation_json(task_dir)
    if evaluation and evaluation.get("nextAction") == "finish":
        evidence_refs = [str(iter_log_dir / "ios-probe-flow-wrapper.log"), str(iter_log_dir / "evaluator.log")]
        evaluation = add_test_results_from_declared_cases(
            task_dir,
            evaluation,
            "pass",
            evidence_refs,
            "iOS probe-flow adapter returned pass.",
        )
        record_subphase_start(task_dir, SUBPHASE_COMPLETION_GATE, platform="ios", iteration=iteration)
        evaluation, _completion_report = apply_completion_gate(
            task_dir,
            evaluation,
            allow_synthesize_pass=False,
            fail_next_action="replan",
        )
        record_subphase_end(task_dir, SUBPHASE_COMPLETION_GATE, platform="ios", iteration=iteration)
        write_evaluation_json(task_dir, evaluation)
    latest_evaluation = read_evaluation_json(task_dir)
    if latest_evaluation:
        latest_evaluation = run_ui_evidence_gate(task_dir, iteration, latest_evaluation)
        write_evaluation_json(task_dir, latest_evaluation)
    return 0, output


def attempt_android_probe_flow_self_repair(task_dir: Path, dry_run: bool = False) -> tuple[bool, dict | None, str]:
    """Try Android probe-flow self-repair before sending a failure back to Generator.

    This is intentionally a thin platform hook: Android implements
    evidence -> safe patch -> rerun today; iOS can later provide the same
    contract without changing the main loop shape.

    Returns (changed_and_reran, latest_evaluation, tool_output).
    """
    script = AUTOMIND_ROOT / "scripts" / "probe_flow_repair_suggest.py"
    if not script.exists():
        return False, None, "probe_flow_repair_suggest.py not found"

    cmd = [sys.executable, str(script), task_dir.name, "--root", str(AUTOMIND_WORKSPACE_ROOT), "--rerun"]
    if dry_run or os.environ.get("AUTOMIND_ANDROID_PROBE_DRY_RUN") == "1":
        cmd.append("--dry-run")

    proc = subprocess.run(cmd, cwd=str(AUTOMIND_WORKSPACE_ROOT), text=True, capture_output=True)
    output = proc.stdout + proc.stderr

    # exitCode=2 means no safe patch was applied; this is a normal no-op, not
    # an infrastructure failure. Any non-zero other than 2 is treated as a
    # failed repair attempt and the loop falls back to Generator.
    if proc.returncode != 0:
        return False, None, output

    latest = read_evaluation_json(task_dir)
    return True, latest, output


def should_try_platform_self_repair_detail(task_dir: Path, evaluation: dict) -> dict[str, Any]:
    """Rich model-first variant: decide whether to try platform-level self-repair
    before escalating back to the Generator.

    Returns a dict with:
      shouldTry: bool — the thin-wrapper return value.
      triageSource: "code_deterministic" only when the decision is a hard no.
          Otherwise "requires_model_review" because absence of a blocker is
          heuristic, not proof that repair will help.
      needsModelReview: True iff the positive "go ahead" is heuristic and should
          be re-examined by the Evaluator before acting.
      blockersFound: list of concrete blocked categories that fired.
      reason: short human-readable description.
    """
    if not is_android_probe_flow_task(task_dir):
        return {
            "shouldTry": False, "triageSource": "code_deterministic",
            "needsModelReview": False, "blockersFound": [],
            "reason": "当前任务不是 Android probe-flow 任务，没有可执行的平台自我修复路径。",
        }
    if evaluation.get("result") != "fail" or evaluation.get("nextAction") != "retry_generator":
        return {
            "shouldTry": False, "triageSource": "code_deterministic",
            "needsModelReview": False, "blockersFound": [],
            "reason": "当前评估结果不是 fail + retry_generator，无需走平台自我修复路径。",
        }
    failed = evaluation.get("failedChecks", [])
    if not isinstance(failed, list):
        return {
            "shouldTry": False, "triageSource": "code_deterministic",
            "needsModelReview": False, "blockersFound": [],
            "reason": "评估中 failedChecks 字段不是列表（结构异常），拒绝在结构不清时做修复。",
        }
    blocked_categories = {
        "build_failure", "install_failure", "agent_unavailable",
        "permission_blocked", "mobile_device_unavailable", "tool_missing",
    }
    found_blockers: list[str] = []
    for check in failed:
        if isinstance(check, dict) and check.get("category") in blocked_categories:
            found_blockers.append(check.get("category", ""))
    if found_blockers:
        return {
            "shouldTry": False, "triageSource": "code_deterministic",
            "needsModelReview": False, "blockersFound": found_blockers,
            "reason": f"检测到产品级/基础设施级失败类别：{found_blockers}，自我修复无效，应交由 Generator 或人工处理。",
        }
    return {
        "shouldTry": True, "triageSource": "requires_model_review",
        "needsModelReview": True, "blockersFound": [],
        "reason": "没有检测到硬拦截类别，但这只是启发式判断，模型应重新检查失败条目，确认是否确实是 probe-flow 层面问题。",
    }


def should_try_platform_self_repair(task_dir: Path, evaluation: dict) -> bool:
    """Conservative gate for platform-level flow repair. Thin wrapper over
    should_try_platform_self_repair_detail; callers needing triage should use
    the _detail variant."""
    return should_try_platform_self_repair_detail(task_dir, evaluation)["shouldTry"]











def can_run_evaluator_without_generator(task_dir: Path) -> bool:
    """Return True when the task already has a concrete evaluator command.

    This keeps AutoMind useful for existing projects: if a project already
    exposes `npm test`, `pytest`, `gradle test`, etc., we can run the harness
    and produce evaluation without requiring a Coding Agent Generator first.
    """
    return get_evaluator_only_kind(task_dir) is not None


def is_ios_probe_flow_task(task_dir: Path) -> bool:
    state = read_runtime_state(task_dir) or {}
    harness = state.get("harnessProfile") or {}
    return state.get("taskType") == "ios" and (task_dir / "probe-flow.ios.json").exists() or harness.get("name") == "ios-probe-flow"


def can_run_ios_xcuitest_without_generator(task_dir: Path) -> bool:
    """Return True when an iOS task has enough config to run XCUITest directly."""
    state = read_runtime_state(task_dir) or {}
    if state.get("taskType") != "ios":
        return False
    ios = state.get("iosApp") or state.get("iosDevice") or {}
    if not isinstance(ios, dict):
        return False
    has_project = bool(ios.get("projectPath") or ios.get("project_path") or ios.get("workspacePath") or ios.get("workspace_path"))
    return bool(has_project and ios.get("scheme") and (ios.get("xcodebuildDeviceId") or ios.get("xcodebuild_device_id") or ios.get("deviceId") or ios.get("device_id")))


def get_evaluator_only_kind(task_dir: Path) -> str | None:
    """Return the concrete evaluator-only adapter kind for a task, if any."""
    state = read_runtime_state(task_dir) or {}
    if state.get("taskType") == "script":
        command, _warnings = extract_script_command(task_dir)
        if command:
            return "script"
    if is_ios_probe_flow_task(task_dir):
        return "ios-probe-flow"
    if can_run_ios_xcuitest_without_generator(task_dir):
        return "ios-xcuitest"
    return None


def run_ios_xcuitest_evaluator(task_dir: Path, iteration: int, iter_log_dir: Path) -> tuple[int, str]:
    """Run the reusable iOS XCUITest evaluator script for configured iOS tasks."""
    state = read_runtime_state(task_dir) or {}
    ios = state.get("iosApp") or state.get("iosDevice") or {}
    if not isinstance(ios, dict):
        ios = {}
    script = AUTOMIND_ROOT / "scripts" / "ios_xcuitest_runner.py"
    cmd = [sys.executable, str(script), task_dir.name, str(iteration)]
    project_path = ios.get("projectPath") or ios.get("project_path")
    workspace_path = ios.get("workspacePath") or ios.get("workspace_path")
    if project_path:
        cmd += ["--project-path", str(project_path)]
    if workspace_path:
        cmd += ["--workspace-path", str(workspace_path)]
    if ios.get("scheme"):
        cmd += ["--scheme", str(ios.get("scheme"))]
    device_id = ios.get("xcodebuildDeviceId") or ios.get("xcodebuild_device_id") or ios.get("deviceId") or ios.get("device_id")
    if device_id:
        cmd += ["--device-id", str(device_id)]
    if ios.get("team"):
        cmd += ["--team", str(ios.get("team"))]
    bundle_id = ios.get("bundleId") or ios.get("bundle_id")
    if bundle_id:
        cmd += ["--bundle-id", str(bundle_id)]
    if ios.get("configuration"):
        cmd += ["--configuration", str(ios.get("configuration"))]

    record_subphase_start(task_dir, SUBPHASE_UI_EXECUTION, platform="ios", iteration=iteration)
    proc = subprocess.run(cmd, cwd=str(AUTOMIND_WORKSPACE_ROOT), text=True, capture_output=True, timeout=runtime_timeout("AUTOMIND_EVALUATOR_TIMEOUT"))
    record_subphase_end(task_dir, SUBPHASE_UI_EXECUTION, platform="ios", iteration=iteration)
    output = proc.stdout + proc.stderr
    ensure_dir(iter_log_dir)
    (iter_log_dir / "ios-xcuitest-runner.log").write_text(output)
    # The runner owns evaluator.log, evaluation.json, Validation.md, and env/commands.
    # Keep a copy only if the runner failed before creating one.
    evaluator_log = iter_log_dir / "evaluator.log"
    if not evaluator_log.exists():
        evaluator_log.write_text(output)
    evaluation = read_evaluation_json(task_dir)
    if evaluation and evaluation.get("nextAction") == "finish":
        evidence_refs = [str(iter_log_dir / "ios-xcuitest-runner.log"), str(evaluator_log)]
        evaluation = add_test_results_from_declared_cases(
            task_dir,
            evaluation,
            "pass",
            evidence_refs,
            "iOS XCUITest adapter returned pass.",
        )
        record_subphase_start(task_dir, SUBPHASE_COMPLETION_GATE, platform="ios", iteration=iteration)
        evaluation, _completion_report = apply_completion_gate(
            task_dir,
            evaluation,
            allow_synthesize_pass=False,
            fail_next_action="replan",
        )
        record_subphase_end(task_dir, SUBPHASE_COMPLETION_GATE, platform="ios", iteration=iteration)
        write_evaluation_json(task_dir, evaluation)
    return 0, output






































































def iter_dir_for(task_dir: Path, iteration: int) -> Path:
    """Return the log directory for a concrete loop iteration."""
    return task_dir / "logs" / f"iter-{iteration}"




































def run_pre_build_workflow_gate(
    task_code: str,
    task_dir: Path,
    agent: str,
    mode: Literal["cli", "llm"],
) -> bool:
    """Enforce workflow-check before Generator edits product/runtime code.

    The docs/prompts treat `workflow-check before Build` as a hard gate. This
    function makes the detached CLI-owned loop obey the same rule instead of
    relying on prompt text alone.
    """
    workflow_ok, workflow_report = check_workflow_consistency(task_code)
    record_workflow_check_state(task_dir, workflow_report, "pre_build_gate")
    if workflow_ok:
        success("Pre-build workflow-check passed")
        return True

    warn("Pre-build workflow-check failed; Generator must not start yet")
    for issue in workflow_report.get("issues", [])[:10]:
        warn(f"  - {issue}")

    # Keep automation strong but bounded: give the AI Planner one chance to
    # repair Phase 2 artifacts, then stop with an explicit route instead of
    # letting Generator code against incoherent requirements/tests.
    if os.environ.get("AUTOMIND_SKIP_AI_PLANNER", "0") != "1":
        update_runtime_state(
            task_dir,
            status="replan_pending",
            currentOwner="planner",
            nextAction="run_test_planner",
        )
        log("Pre-build gate: running Phase 2 Refiner once to fix workflow-check issues")
        planner_ok = run_ai_test_planner(task_dir, agent=agent, mode=mode)
        workflow_ok, workflow_report = check_workflow_consistency(task_code)
        record_workflow_check_state(task_dir, workflow_report, "pre_build_gate_after_replan")
        if workflow_ok:
            success("Pre-build workflow-check passed after replanning")
            clear_replan_signal_after_planner(task_dir, int((read_runtime_state(task_dir) or {}).get("iteration", 0) or 0), reason="pre_build_gate_after_replan_pass")
            return True
        if not planner_ok:
            warn("Phase 2 Refiner did not complete during pre-build gate")
    else:
        warn("Pre-build gate cannot auto-replan because AUTOMIND_SKIP_AI_PLANNER=1")

    state = read_runtime_state(task_dir) or {}
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
    needs_user = (
        planner.get("needsUserInput") is True
        or review.get("decision") == "ask_user"
        or any("user" in str(issue).lower() or "human" in str(issue).lower() for issue in workflow_report.get("issues", []))
    )
    next_action = "ask_user" if needs_user else "replan"
    evaluation = {
        "iteration": max(int(state.get("iteration", 0) or 0), 1),
        "result": "blocked",
        "summary": "Pre-build workflow-check blocked Generator; Phase 2 artifacts must be corrected before code changes.",
        "failedChecks": [{
            "name": "workflow_check",
            "reason": "; ".join(workflow_report.get("issues", [])[:10]) or "workflow-check failed",
            "category": "needs_replan",
            "evidence": rel_to_root(task_dir / "Plan.md"),
        }],
        "evidence": [
            {"type": "other", "path": rel_to_root(task_dir / "Brainstorm.md"), "note": "planner/user-review gate"},
            {"type": "other", "path": rel_to_root(task_dir / "Plan.md"), "note": "plan gate"},
        ],
        "nextAction": next_action,
    }
    if next_action == "ask_user":
        evaluation["askUserQuestion"] = {
            "question": "workflow-check failed before implementation. Please review/correct Brainstorm.md, Requirements.md, TestCases.md, Plan.md, and the pre-implementation review decision before AutoMind edits code.",
            "options": [],
            "source": "pre_build_workflow_gate",
            "issues": workflow_report.get("issues", []),
            "brainstorm": rel_to_root(task_dir / "Brainstorm.md"),
            "plan": rel_to_root(task_dir / "Plan.md"),
        }
    write_evaluation_json(task_dir, evaluation)
    apply_evaluation_result(task_dir, evaluation)
    return False




def clear_replan_signal_after_planner(task_dir: Path, iteration: int, *, reason: str) -> None:
    """Advance stale evaluation replan route after Planner/workflow-check succeeds.

    `evaluation.json.nextAction=replan` is a loop-control request, not a
    permanent task truth. Once the Planner has run and workflow-check passes,
    keeping that stale signal causes state_reducer to route back to planning
    forever and makes the TUI-owned loop stop at replan_pending.
    """
    evaluation = read_evaluation_json(task_dir) or {}
    if evaluation.get("nextAction") != "replan":
        return
    updated = dict(evaluation)
    resolved_at = datetime.now().isoformat(timespec="seconds")
    updated["previousNextAction"] = "replan"
    updated["nextAction"] = "retry_generator"
    updated["result"] = "fail" if updated.get("result") in {None, "blocked"} else updated.get("result")
    updated["iteration"] = int(updated.get("iteration") or iteration or 1)
    updated["summary"] = "Planner/workflow-check completed after replan; resume Generator/Evaluator loop."
    updated["replanResolution"] = {
        "resolvedAt": resolved_at,
        "reason": reason,
        "previousNextAction": "replan",
        "nextAction": "retry_generator",
        "requiresFreshPlannerWorkflowPass": True,
    }
    notes = updated.setdefault("notes", [])
    note = {"source": "clear_replan_signal_after_planner", "reason": reason, "resolvedAt": resolved_at, "previousNextAction": "replan", "nextAction": "retry_generator"}
    if isinstance(notes, list):
        notes.append(note)
    else:
        updated["notes"] = [note]
    write_evaluation_json(task_dir, updated)
    apply_evaluation_result(task_dir, updated)
    refresh_phase_transition_summary(task_dir)

def write_planner_fallback_record(task_dir: Path, agent: str, reason: str, output: str = "") -> None:
    """Record that deterministic scaffold was used because AI planning was unavailable."""
    planner_dir = task_dir / "logs" / "planner"
    ensure_dir(planner_dir)
    (planner_dir / "planner.log").write_text(output or reason)
    (planner_dir / "commands.md").write_text(
        "# Commands\n\n"
        "No AI Phase 2 Refiner command completed successfully. AutoMind used the deterministic scaffold generated by orchestrator/main.py.\n"
    )
    (planner_dir / "env.json").write_text(json.dumps({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "taskCode": task_dir.name,
        "agent": agent,
        "fallbackReason": reason,
    }, ensure_ascii=False, indent=2) + "\n")
    (task_dir / "Validation.md").open("a").write(
        "\n## Planner fallback\n\n"
        f"- Environment: cwd=`{AUTOMIND_WORKSPACE_ROOT}`; runtime=`{AUTOMIND_ROOT}`; agent=`{agent}`\n"
        "- Commands: see `logs/planner/commands.md`\n"
        "- Result: BLOCKED for AI planning; deterministic scaffold kept the task usable.\n"
        f"- Evidence: `logs/planner/planner.log`, `logs/planner/env.json`\n"
        "- Reusable findings: If the agent CLI is unavailable, AutoMind can still create a conservative scaffold and let skill-mode agents refine it manually.\n"
        "- Avoid repeating: Do not treat scaffold TestCases as final when richer model planning is available.\n"
    )


def build_replan_context(task_dir: Path) -> str:
    """Summarize already-tried failure signatures and ruled-out hypotheses.

    A bounded autonomous replan only helps if the planner does not re-propose the
    exact path that already failed. This reads observational state written by the
    budget (``tcFailureSignatureCounts``) and the attempt ledger
    (``progressByTc.ruledOut`` / ``remainingHypotheses``) and renders a compact
    "do not repeat these" block. It is purely additive context: when there is no
    such history it returns an empty string so the planner prompt is unchanged.
    """
    state = read_runtime_state(task_dir) or {}
    sig_counts = state.get("tcFailureSignatureCounts")
    repeated: list[str] = []
    if isinstance(sig_counts, dict):
        for tc_id in sorted(sig_counts):
            sigs = sig_counts.get(tc_id)
            if not isinstance(sigs, dict):
                continue
            for sig, count in sorted(sigs.items(), key=lambda kv: -int(kv[1] or 0)):
                if int(count or 0) >= 2:
                    repeated.append(f"{tc_id}: `{sig}` (x{int(count or 0)})")

    ruled_out: list[str] = []
    remaining: list[str] = []
    ledger = read_tc_attempts(task_dir)
    progress = ledger.get("progressByTc") if isinstance(ledger, dict) else None
    if isinstance(progress, dict):
        for tc_id in sorted(progress):
            info = progress.get(tc_id)
            if not isinstance(info, dict):
                continue
            for value in info.get("ruledOut") or []:
                entry = f"{tc_id}: {value}"
                if entry not in ruled_out:
                    ruled_out.append(entry)
            for value in info.get("remainingHypotheses") or []:
                entry = f"{tc_id}: {value}"
                if entry not in remaining:
                    remaining.append(entry)

    if not (repeated or ruled_out or remaining):
        return ""
    lines = [
        "",
        "## Replan context: do not repeat what already failed",
        "",
        "AutoMind is replanning because earlier attempts kept failing the same way. "
        "Choose a materially different path; do not re-propose a path matching a "
        "repeated failure signature or an already ruled-out hypothesis below.",
    ]
    if repeated:
        lines.append("")
        lines.append("Repeated failure signatures (TC: signature x count):")
        lines.extend(f"- {item}" for item in repeated[:8])
    if ruled_out:
        lines.append("")
        lines.append("Already ruled-out hypotheses:")
        lines.extend(f"- {item}" for item in ruled_out[:8])
    if remaining:
        lines.append("")
        lines.append("Remaining hypotheses worth trying:")
        lines.extend(f"- {item}" for item in remaining[:8])
    lines.append("")
    return "\n".join(lines)


def run_ai_test_planner(task_dir: Path, agent: str = "auto", mode: Literal["cli", "llm"] = "cli") -> bool:
    """Ask a coding agent to refine Brainstorm/Requirements/TestCases/Plan before Generator."""
    record_phase_start(task_dir, "planning")
    planner_dir = task_dir / "logs" / "planner"
    ensure_dir(planner_dir)
    update_runtime_state(task_dir, status="planning", currentOwner="planner", nextAction="run_test_planner")
    update_heartbeat(task_dir, owner="planner", note="phase2_refiner")
    append_progress_log(task_dir, "planner-phase-start", owner="planner")
    write_reuse_context(task_dir, reason="before_phase2_refiner")
    run_before_phase_hooks(task_dir, "brainstorm", reason="before_phase2_refiner")
    run_before_phase_hooks(task_dir, "requirements", reason="before_phase2_refiner")
    run_before_phase_hooks(task_dir, "testcases", reason="before_phase2_refiner")
    run_before_phase_hooks(task_dir, "plan", reason="before_phase2_refiner")
    try:
        planner_user_input = (read_runtime_state(task_dir) or {}).get("userInput", "")
        prompt = render_prompt_template(
            "phase2_planner_prompt.md",
            task_dir=task_dir,
            user_input=planner_user_input,
        )
        prompt = apply_runtime_language_instruction(prompt, planner_user_input)
    except FileNotFoundError as exc:
        write_planner_fallback_record(task_dir, agent, str(exc))
        update_runtime_state(task_dir, planner={"mode": "deterministic_fallback", "ok": False, "reason": str(exc)})
        return False

    answer_context = latest_answer_prompt_context(task_dir)
    message_context = pending_user_messages_prompt_context(task_dir)
    replan_context = build_replan_context(task_dir)
    extra_context = answer_context + message_context + replan_context
    prompt_with_answers = prompt + extra_context if extra_context else prompt
    _agent_start = time.time()
    code, output = run_agent(mode, agent, prompt_with_answers, task_dir, phase="planner")
    _agent_duration = time.time() - _agent_start
    record_agent_call(task_dir, "planner", _agent_duration, exit_code=code)
    update_heartbeat(task_dir, owner="planner", note=f"phase2_refiner_exit_{code}")
    append_progress_log(task_dir, f"planner-phase-end exit={code}", owner="planner", level="info" if code == 0 else "warn")
    if answer_context and code == 0:
        mark_latest_answer_applied(task_dir, applied_by="planner")
    if message_context and code == 0:
        mark_pending_user_messages_delivered(task_dir, mode="planner_prompt")
    run_after_phase_hooks(task_dir, "plan", payload={"agent": agent, "mode": mode, "exitCode": code}, reason="after_phase2_refiner")
    write_rendered_prompt(planner_dir, "planner-prompt.md", prompt_with_answers)
    (planner_dir / "planner.log").write_text(output)
    (planner_dir / "commands.md").write_text(
        "# Commands\n\n"
        f"AI Phase 2 Refiner invoked through agent `{agent}`. See `planner.log` for the raw agent output.\n"
    )
    (planner_dir / "env.json").write_text(json.dumps({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "taskCode": task_dir.name,
        "agent": agent,
        "mode": mode,
        "exitCode": code,
    }, ensure_ascii=False, indent=2) + "\n")

    ok, issues = validate_planner_artifacts(task_dir)
    planner_state = {
        "mode": "ai_test_planner",
        "agent": agent,
        "ok": ok and code == 0,
        "exitCode": code,
        "issues": issues,
        "log": rel_to_root(planner_dir / "planner.log"),
    }
    existing_state = read_runtime_state(task_dir) or {}
    existing_planner = existing_state.get("planner") if isinstance(existing_state.get("planner"), dict) else {}
    existing_review = existing_planner.get("preImplementationReview") if isinstance(existing_planner.get("preImplementationReview"), dict) else {}
    pre_impl_answered = has_resolved_pre_implementation_answer(task_dir)
    explicit_needs_user_input = (existing_planner.get("needsUserInput") is True or existing_state.get("nextAction") == "ask_user") and not pre_impl_answered
    if "needsUserInput" not in planner_state:
        planner_state["needsUserInput"] = explicit_needs_user_input
    if existing_review and "preImplementationReview" not in planner_state:
        existing_review = dict(existing_review)
        if pre_impl_answered:
            existing_review["needsUserInput"] = False
            if existing_review.get("decision") == "ask_user":
                existing_review["decision"] = "auto_proceed"
        planner_state["preImplementationReview"] = existing_review
    if ok and code == 0 and "artifactsRefined" not in planner_state:
        planner_state["artifactsRefined"] = True
    update_runtime_state(task_dir, planner=planner_state)
    if code != 0:
        write_planner_fallback_record(task_dir, agent, "agent execution failed", output)
        return False
    if not ok:
        write_planner_fallback_record(task_dir, agent, "planner artifact validation failed: " + "; ".join(issues), output)
        return False

    (task_dir / "Validation.md").open("a").write(
        "\n## Planner refinement\n\n"
        f"- Environment: cwd=`{AUTOMIND_WORKSPACE_ROOT}`; runtime=`{AUTOMIND_ROOT}`; agent=`{agent}`\n"
        "- Commands: see `logs/planner/commands.md`\n"
        "- Result: PASS. AI Phase 2 Refiner refined task artifacts before implementation.\n"
        "- Evidence: `logs/planner/planner.log`, `logs/planner/env.json`\n"
        "- Reusable findings: Use model planning to refine requirements, acceptance criteria, tests, and plan; keep scripts as validation/execution scaffolding.\n"
        "- Avoid repeating: Do not let platform runners invent validation targets; update TestCases/Requirements first.\n"
    )
    record_phase_end(task_dir, "planning")
    return True


# ============================================================
# Harness Loop
# ============================================================

def run_loop_preflight(task_dir: Path, evaluator_kind: str | None, iteration: int) -> tuple[bool, dict | None, str]:
    """Run explicit loop preflight for evaluator-only platform tasks.

    Probe-flow adapters already run their own preflight internally to keep the
    preflight evidence attached to the probe-flow iteration. This hook makes
    direct platform evaluators expose readiness as an explicit loop phase.
    """
    if evaluator_kind != "ios-xcuitest":
        return True, None, ""

    log("Preflight Phase: iOS device readiness")
    task_code = task_dir.name
    state = read_runtime_state(task_dir) or {}
    ios = state.get("iosApp") or state.get("iosDevice") or {}
    core_device_id = None
    if isinstance(ios, dict):
        core_device_id = ios.get("coreDeviceId") or ios.get("core_device_id")
    script = AUTOMIND_ROOT / "scripts" / "ios_preflight.py"
    cmd = [sys.executable, str(script), task_code, str(iteration)]
    if core_device_id:
        cmd += ["--device-id", str(core_device_id)]
    proc = subprocess.run(cmd, cwd=str(AUTOMIND_WORKSPACE_ROOT), text=True, capture_output=True, timeout=runtime_timeout("AUTOMIND_PREFLIGHT_TIMEOUT", 300))
    output = proc.stdout + proc.stderr
    iter_log_dir = task_dir / "logs" / f"iter-{iteration}"
    ensure_dir(iter_log_dir)
    (iter_log_dir / "loop-preflight.log").write_text(output)
    evaluation = read_evaluation_json(task_dir)
    ok = bool(evaluation and evaluation.get("result") in {"pass", "in_progress"} and evaluation.get("nextAction") in {"finish", "retry_generator"})
    update_runtime_state(
        task_dir,
        preflight={
            "iteration": iteration,
            "kind": evaluator_kind,
            "ok": ok,
            "checkedAt": datetime.now().isoformat(timespec="seconds"),
            "log": str(iter_log_dir / "loop-preflight.log"),
            "result": evaluation.get("result") if evaluation else "missing_evaluation",
        }
    )
    return ok, evaluation, output


def run_harness_loop(
    task_code: str,
    agent: Literal["codex", "claude", "trae", "trae-cn"] = "codex",
    mode: Literal["cli", "llm"] = "cli",
    mock_sequence: Optional[list[str]] = None
) -> bool:
    """
    \u8fd0\u884c Harness Loop
    \u8fd4\u56de: True \u6210\u529f，False \u5931\u8d25
    """
    task_dir = get_task_dir(task_code)

    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        return False

    req_path = primary_requirements_path(task_dir)
    val_path = task_dir / "Validation.md"

    if not req_path.exists():
        error(f"Requirements.md does not exist: {req_path}")
        return False

    if has_unanswered_pending_question(task_dir):
        warn("Task has an unanswered ask_user gate; pausing before starting Generator/Evaluator.")
        normalize_unanswered_pending_question_state(task_dir)
        return False

    # \u8bfb\u53d6 Prompts Template

    # Loop
    state = read_runtime_state(task_dir) or {}
    derived = reconcile_task_state(task_dir, reason="run_harness_loop_start")
    effective = derived.get("effective") if isinstance(derived, dict) else {}
    if isinstance(effective, dict) and effective.get("nextAction") == "finish" and derived.get("terminalAllowed"):
        log("Task has authoritative terminal pass; not reopening Generator/Evaluator. Run summary/report commands explicitly if needed.")
        ensure_summary_generated(task_code, reason="already_finished")
        return True
    state = read_runtime_state(task_dir) or state
    try:
        state_max_iterations = int(state.get("maxIterations", 0) or 0)
    except (TypeError, ValueError):
        state_max_iterations = 0
    loop_limit = max(MAX_ITERATIONS, state_max_iterations)
    if state.get("maxIterations") != loop_limit:
        update_runtime_state(task_dir, maxIterations=loop_limit)
        state = read_runtime_state(task_dir) or state
    iteration = int(state.get("iteration", 0))
    existing_evaluation = read_evaluation_json(task_dir)
    recovery_entry = build_resume_recovery_entry(task_dir, state, existing_evaluation)
    recovery_stage = recovery_entry.get("stage") if recovery_entry.get("recoverable") else "normal"
    recovery_iteration = int(recovery_entry.get("iteration", iteration) or iteration or 1)
    skip_pre_loop_planning = recovery_stage in {"generator", "evaluator"}
    skip_first_generator_for_recovery = recovery_stage == "evaluator"
    if recovery_entry.get("recoverable"):
        log(f"Resume recovery: {recovery_entry.get('reason')}")
        update_runtime_state(
            task_dir,
            resumeRecovery={
                **recovery_entry,
                "detectedAt": datetime.now().isoformat(timespec="seconds"),
            },
        )
        if recovery_stage in {"generator", "evaluator"}:
            # The loop increments at the top of each iteration; subtract one so
            # the interrupted phase can be retried at the same iteration number.
            iteration = max(0, recovery_iteration - 1)
        elif recovery_stage == "planner":
            update_runtime_state(task_dir, status="replan_pending", currentOwner="planner", nextAction="run_test_planner")

    # Resume optimization: if the task is already waiting after an Android
    # probe-flow failure, first try to repair the verification flow itself.
    # This preserves AutoMind's core goal: don't ask the Coding Agent to change
    # product code until verifier/environment issues have been ruled out.
    if state.get("status") == "retry_pending" and existing_evaluation and should_try_platform_self_repair(task_dir, existing_evaluation):
        log("Resume preflight: try platform self-repair before Generator retry")
        repaired, repaired_evaluation, repair_output = attempt_android_probe_flow_self_repair(task_dir)
        repair_log_dir = task_dir / "logs" / f"iter-{iteration + 1}"
        ensure_dir(repair_log_dir)
        (repair_log_dir / "platform-self-repair.log").write_text(repair_output)
        if repaired and repaired_evaluation:
            evaluation, validation_errors = normalize_evaluation(task_dir, repaired_evaluation, int(repaired_evaluation.get("iteration", iteration + 1) or iteration + 1))
            if validation_errors:
                warn(f"platform self-repair evaluation warning: {', '.join(validation_errors)}")
            write_evaluation_json(task_dir, evaluation)
            apply_evaluation_result(task_dir, evaluation)
            iteration = int(evaluation.get("iteration", iteration + 1) or iteration + 1)
            if evaluation.get("nextAction") == "finish":
                success("Platform self-repair completed task before Generator retry")
                finalize_task_records(task_code, "platform_self_repair_finish")
                ensure_summary_generated(task_code, reason="platform_self_repair_finish")
                return True
            if evaluation.get("nextAction") not in {"retry_generator", "replan"}:
                finalize_task_records(task_code, "platform_self_repair_stop")
                ensure_summary_generated(task_code, reason="platform_self_repair_stop")
                return False
        else:
            warn("Resume preflight self-repair did not apply a safe patch; continue to Generator")

    # Evidence synthesis checkpoint: when proof summaries already exist but
    # evaluation.json is missing/stale testResults, update the TC-level ledger
    # before starting another broad Generator turn.  This does not claim finish.
    latest_state = read_runtime_state(task_dir) or state
    latest_evaluation = read_evaluation_json(task_dir) or existing_evaluation or {}
    if (
        latest_state.get("status") == "retry_pending"
        and latest_state.get("currentOwner") == "generator"
        and should_synthesize_evaluation(task_dir, latest_evaluation)
    ):
        synthesized = synthesize_evaluation_from_evidence(task_dir, reason="pre_generator_checkpoint")
        if synthesized:
            gated_synthesized, _synthesis_report = apply_completion_gate(task_dir, synthesized)
            write_evaluation_json(task_dir, gated_synthesized)
            apply_evaluation_result(task_dir, gated_synthesized)
            next_after_synthesis = str(gated_synthesized.get("nextAction") or "").strip().lower()
            if next_after_synthesis == "finish":
                success("Evidence synthesis checkpoint plus completion gate proved finish")
                finalize_task_records(task_code, "evidence_synthesis_finish")
                ensure_summary_generated(task_code, reason="evidence_synthesis_finish")
                return True
            if next_after_synthesis in {"ask_user", "stop"}:
                success("Evidence synthesis checkpoint updated evaluation.json.testResults; stopping on hard gate: " + next_after_synthesis)
                finalize_task_records(task_code, "evidence_synthesis_" + next_after_synthesis)
                ensure_summary_generated(task_code, reason="evidence_synthesis_" + next_after_synthesis)
                return False
            log(
                "Evidence synthesis checkpoint updated evaluation.json.testResults; "
                f"completion gate nextAction={next_after_synthesis or 'unknown'}, continuing resume route."
            )

    route = reconcile_task_state(task_dir, reason="run_harness_loop_route")
    route_effective = route.get("effective") if isinstance(route, dict) else {}
    if isinstance(route_effective, dict) and route_effective.get("nextAction") == "finish" and route.get("terminalAllowed"):
        log("Task reached authoritative terminal pass during resume preflight; hard-stopping loop.")
        ensure_summary_generated(task_code, reason="already_finished")
        return True
    if isinstance(route_effective, dict) and route_effective.get("nextAction") == "run_test_planner":
        log("Effective state requests Planner; running Phase 2 Refiner before any Generator retry.")
        planner_ok = run_ai_test_planner(task_dir, agent=agent, mode=mode)
        if not planner_ok:
            finalize_task_records(task_code, "planner_route_failed")
            ensure_summary_generated(task_code, reason="planner_route_failed")
            return False
        workflow_ok_after_plan, workflow_report_after_plan = check_workflow_consistency(task_code)
        record_workflow_check_state(task_dir, workflow_report_after_plan, "route_planner_after_replan")
        if workflow_ok_after_plan:
            clear_replan_signal_after_planner(task_dir, iteration, reason="route_planner_workflow_pass")
        state = read_runtime_state(task_dir) or state
        iteration = int(state.get("iteration", iteration) or iteration)

    evaluator_only_kind = get_evaluator_only_kind(task_dir)
    evaluator_only = evaluator_only_kind is not None
    update_runtime_state(task_dir,
        status="ready",
        iteration=iteration,
        currentOwner="evaluator" if evaluator_only else "generator",
        nextAction="run_evaluator" if evaluator_only else "run_generator",
        evaluatorOnlyKind=evaluator_only_kind or ""
    )
    if not evaluator_only:
        planner_state = read_runtime_state(task_dir) or {}
        planner_info = planner_state.get("planner") if isinstance(planner_state.get("planner"), dict) else {}
        if skip_pre_loop_planning:
            log("Resume recovery: skip Phase 2 Refiner and pre-build workflow gate; continuing from persisted phase artifacts")
        elif planner_info.get("mode") != "ai_test_planner" and os.environ.get("AUTOMIND_SKIP_AI_PLANNER", "0") != "1":
            update_runtime_state(task_dir, currentOwner="planner", nextAction="run_test_planner")
            log("Phase 2 Refiner: refine Brainstorm/Requirements/TestCases/Plan with model intelligence")
            planner_ok = run_ai_test_planner(task_dir, agent=agent, mode=mode)
            if planner_ok:
                success("Phase 2 Refiner completed")
            else:
                warn("Phase 2 Refiner unavailable or incomplete; continuing with deterministic scaffold")
        elif os.environ.get("AUTOMIND_SKIP_AI_PLANNER", "0") == "1":
            update_runtime_state(task_dir, planner={"mode": "skipped", "ok": False, "reason": "AUTOMIND_SKIP_AI_PLANNER=1"})

        planner_state = read_runtime_state(task_dir) or {}
        planner_info = planner_state.get("planner") if isinstance(planner_state.get("planner"), dict) else {}
        review = planner_info.get("preImplementationReview") if isinstance(planner_info.get("preImplementationReview"), dict) else {}
        decision = review.get("decision") or planner_info.get("preImplementationDecision")
        pre_impl_answered = has_resolved_pre_implementation_answer(task_dir)
        if not skip_pre_loop_planning and not pre_impl_answered and (planner_info.get("needsUserInput") is True or decision == "ask_user"):
            warn("Pre-implementation user review requires human input; pausing before Generator")
            review_options = review.get("options") if isinstance(review.get("options"), list) else []
            review_questions = review.get("questions") if isinstance(review.get("questions"), list) else []
            review_for_ask = _enrich_review_with_planning_summaries(review, task_dir)
            planner_user_input = planner_state.get("userInput", "")
            ask_question = {
                "question": format_pre_implementation_ask_question(review_questions, review_for_ask, user_input=planner_user_input),
                "options": review_options or [
                    {"id": "confirm_recommended_direction", "label": "Confirm recommended direction", "impact": "Continue to refine Requirements/TestCases/Plan, run workflow-check, then implement."},
                    {"id": "revise_scope_assumptions", "label": "Revise scope/assumptions", "impact": "Update Brainstorm/Spec before downstream artifacts and code changes."},
                    {"id": "choose_alternative_approach", "label": "Choose alternative approach", "impact": "Regenerate downstream artifacts for a different technical path."},
                    {"id": "stop", "label": "Stop", "impact": "Do not implement this task."},
                ],
                "recommended": review.get("recommendedOption") or "confirm_recommended_direction",
                "source": "planner_pre_implementation_review",
                "brainstorm": rel_to_root(task_dir / "Brainstorm.md"),
                "requirements": rel_to_root(primary_requirements_path(task_dir)),
                "reason": review.get("reason") or "Planner marked needsUserInput=true.",
                "approvalScope": review.get("approvalScope") or "brainstorm_requirements_direction_scope_assumptions_approach_verification_direction",
            }
            policy_issues = pre_implementation_ask_user_policy_issues(task_dir, ask_question)
            if policy_issues:
                warn("Pre-implementation ask_user rejected by autonomy policy; routing back to Generator")
                for issue in policy_issues[:3]:
                    warn(f"  - {issue}")
                evaluation = {
                    "iteration": max(iteration, 1),
                    "result": "fail",
                    "summary": "Pre-implementation review requested a self-serviceable verification unblock; AutoMind must continue autonomously.",
                    "failedChecks": [{
                        "name": "pre_implementation_review_soft_pause",
                        "reason": policy_issues[0],
                        "category": "needs_replan",
                        "evidence": rel_to_root(task_dir / "Brainstorm.md"),
                    }],
                    "evidence": [{"type": "other", "path": rel_to_root(task_dir / "Brainstorm.md"), "note": "pre-implementation review soft pause rejected"}],
                    "nextAction": "retry_generator",
                    "warnings": ["pre_implementation_ask_user_not_whitelisted"],
                }
                update_runtime_state(
                    task_dir,
                    planner={
                        **planner_info,
                        "needsUserInput": False,
                        "preImplementationReview": {
                            **review,
                            "decision": "auto_proceed",
                            "needsUserInput": False,
                            "autoProceedReason": policy_issues[0],
                        },
                    },
                )
                write_evaluation_json(task_dir, evaluation)
                apply_evaluation_result(task_dir, evaluation)
            else:
                evaluation = {
                    "iteration": max(iteration, 1),
                    "result": "blocked",
                    "summary": "Pre-implementation review requires user input before code changes.",
                    "failedChecks": [{
                        "name": "pre_implementation_review",
                        "reason": ask_question["reason"],
                        "category": "needs_replan",
                        "evidence": rel_to_root(task_dir / "Brainstorm.md"),
                    }],
                    "evidence": [{"type": "other", "path": rel_to_root(task_dir / "Brainstorm.md"), "note": "pre-implementation review"}],
                    "nextAction": "ask_user",
                    "askUserQuestion": ask_question,
                }
                write_evaluation_json(task_dir, evaluation)
                apply_evaluation_result(task_dir, evaluation)
                finalize_task_records(task_code, "pre_implementation_ask_user")
                ensure_summary_generated(task_code, reason="pre_implementation_ask_user")
                return False

        if not skip_pre_loop_planning and not run_pre_build_workflow_gate(task_code, task_dir, agent=agent, mode=mode):
            finalize_task_records(task_code, "pre_build_workflow_gate")
            ensure_summary_generated(task_code, reason="pre_build_workflow_gate")
            return False

        if start_warm_build(task_dir):
            log("Warm build started in background; will wait for completion before Evaluator")

    while iteration < loop_limit:
        loop_derived = reconcile_task_state(task_dir, reason="run_harness_loop_iteration_start")
        loop_effective = loop_derived.get("effective") if isinstance(loop_derived, dict) else {}
        if isinstance(loop_effective, dict) and loop_effective.get("nextAction") == "finish" and loop_derived.get("terminalAllowed"):
            log("Task reached authoritative terminal pass; hard-stopping before next iteration.")
            ensure_summary_generated(task_code, reason="finish")
            return True
        if isinstance(loop_effective, dict) and loop_effective.get("nextAction") == "run_test_planner":
            log("Effective state requests Planner; running Planner instead of Generator.")
            planner_ok = run_ai_test_planner(task_dir, agent=agent, mode=mode)
            if not planner_ok:
                finalize_task_records(task_code, "planner_route_failed")
                ensure_summary_generated(task_code, reason="planner_route_failed")
                return False
            workflow_ok_after_plan, workflow_report_after_plan = check_workflow_consistency(task_code)
            record_workflow_check_state(task_dir, workflow_report_after_plan, "loop_planner_after_replan")
            if workflow_ok_after_plan:
                clear_replan_signal_after_planner(task_dir, iteration, reason="loop_planner_workflow_pass")
            update_runtime_state(task_dir, status="retry_pending", currentOwner="generator", nextAction="retry_generator")
        iteration += 1
        log(f"=== Iteration {iteration} ===")
        record_iteration(task_dir, iteration)
        # Long-run signals: bump heartbeat and append a progress entry every
        # iteration so external supervisors / `automind doctor` can detect
        # stalled runs without scanning iteration logs.
        update_heartbeat(task_dir, owner="loop", note=f"iter-{iteration}")
        append_progress_log(
            task_dir,
            f"iteration-start",
            iteration=iteration,
            owner="loop",
        )

        # \u786e\u4fdd\u65e5\u5fd7\u76ee\u5f55
        iter_log_dir = task_dir / "logs" / f"iter-{iteration}"
        ensure_dir(iter_log_dir)

        if evaluator_only:
            preflight_ok, preflight_evaluation, preflight_output = run_loop_preflight(task_dir, evaluator_only_kind, iteration)
            if not preflight_ok:
                evaluation = preflight_evaluation or {
                    "iteration": iteration,
                    "result": "blocked",
                    "summary": "Loop preflight failed",
                    "failedChecks": [{"name": "loop_preflight", "category": "mobile_device_unavailable", "reason": preflight_output[-1000:]}],
                    "nextAction": "replan",
                }
                evidence = evaluation.setdefault("evidence", [])
                evidence.append({"type": "log", "path": str(iter_log_dir / "loop-preflight.log")})
                evaluation["summary"] = "Loop preflight blocked evaluator: " + str(evaluation.get("summary", "preflight failed"))
                evaluation["nextAction"] = "ask_user" if evaluation.get("nextAction") == "ask_user" else "replan"
                write_evaluation_json(task_dir, evaluation)
                apply_evaluation_result(task_dir, evaluation)
                finalize_task_records(task_code, "loop_preflight_blocked")
                ensure_summary_generated(task_code, reason="loop_preflight_blocked")
                return False

        skip_generator_this_iteration = (
            skip_first_generator_for_recovery
            and iteration == recovery_iteration
            and (iter_log_dir / "generator.log").exists()
        )

        if not evaluator_only and not skip_generator_this_iteration:
            # ----- Generator Phase -----
            record_phase_start(task_dir, "generator")
            record_iter_phase_start(task_dir, iteration, "generator")
            update_runtime_state(task_dir,
                status="generating",
                iteration=iteration,
                currentOwner="generator",
                nextAction="run_generator"
            )
            refresh_phase_transition_summary(task_dir)
            update_heartbeat(task_dir, owner="generator", note=f"iter-{iteration}")
            generator_purpose = write_iteration_purpose(task_dir, iteration, "generator", iter_log_dir)
            append_progress_log(
                task_dir,
                f"generator-phase-start mode={generator_purpose.get('mode')} purpose={generator_purpose.get('purpose')}",
                iteration=iteration,
                owner="generator",
            )
            log("Generator Phase...")
            run_before_phase_hooks(task_dir, "generator", reason=f"before_generator_iter_{iteration}")
            generator_context = build_generator_context_pack(task_dir, iteration, iter_log_dir)
            update_runtime_state(task_dir,
                generatorContext={
                    "iteration": iteration,
                    "contextPack": rel_to_root(generator_context["markdownPath"]),
                    "contextPackJson": rel_to_root(generator_context["jsonPath"]),
                    "validationOk": generator_context.get("validationOk", False),
                    "validationIssues": generator_context.get("validationIssues", []),
                }
            )
            if not generator_context.get("validationOk", False):
                warn("Generator context pack has issues: " + "; ".join(generator_context.get("validationIssues", [])[:5]))
            generator_user_input = (read_runtime_state(task_dir) or {}).get("userInput", "")
            try:
                generator_prompt = render_prompt_template(
                    "generator_prompt.md",
                    task_dir=task_dir,
                    req_path=req_path,
                    val_path=val_path,
                    iteration=iteration,
                )
            except FileNotFoundError:
                generator_prompt = f"Implement according to {req_path}, use {val_path} to fix issues. Current iteration: {iteration}"
            generator_prompt = apply_runtime_language_instruction(generator_prompt, generator_user_input)
            answer_context = latest_answer_prompt_context(task_dir)
            message_context = pending_user_messages_prompt_context(task_dir)
            generator_prompt_with_answers = generator_prompt + answer_context + message_context if (answer_context or message_context) else generator_prompt
            write_rendered_prompt(iter_log_dir, "generator-prompt.md", generator_prompt_with_answers)

            _agent_start = time.time()
            code, output = run_agent(mode, agent, generator_prompt_with_answers, task_dir, phase="generator")
            _agent_duration = time.time() - _agent_start
            record_agent_call(task_dir, "generator", _agent_duration, exit_code=code)
            record_action(
                task_dir,
                iteration=iteration,
                phase="generator",
                action_type="agent_execution",
                target=f"{agent} ({mode})",
                result="success" if code == 0 else "failed",
                details={"exitCode": code, "durationMs": int(_agent_duration * 1000)},
            )
            if answer_context and code == 0:
                mark_latest_answer_applied(task_dir, applied_by="generator")
            if message_context and code == 0:
                mark_pending_user_messages_delivered(task_dir, mode="generator_prompt")

            # \u5199\u65e5\u5fd7
            (iter_log_dir / "generator.log").write_text(output)

            if should_pause_after_generator_for_human_input(task_dir, iteration):
                warn("Generator produced an ask_user gate; pausing before Evaluator.")
                run_after_phase_hooks(task_dir, "generator", payload={"iteration": iteration, "result": "ask_user"}, reason=f"after_generator_ask_user_iter_{iteration}")
                finalize_task_records(task_code, "generator_ask_user")
                ensure_summary_generated(task_code, reason="generator_ask_user")
                return False

            if code != 0:
                failure_category = classify_agent_execution_failure_with_log(output, iter_log_dir / "generator.log")
                next_action = "retry_generator"
                evaluation = {
                    "iteration": iteration,
                    "result": "blocked",
                    "summary": "Generator agent timed out before completing" if failure_category == "agent_timeout" else ("Generator agent context window was exhausted; retry with a fresh primary session" if failure_category == "agent_context_overflow" else "Generator agent preflight or execution failed"),
                    "failedChecks": [{
                        "name": "agent_execution",
                        "reason": output[:1000],
                        "category": failure_category
                    }],
                    "nextAction": next_action
                }
                if failure_category == "agent_context_overflow":
                    clear_task_primary_session(task_dir, reason="agent_context_overflow_start_fresh")
                if is_safe_auto_recovery_failure(failure_category):
                    evaluation["autoRecovery"] = {
                        "selected": "fresh_session_resume" if failure_category == "agent_context_overflow" else "resume_after_recovery",
                        "source": "generator_agent_failure",
                        "reason": "Agent context window is saturated; keep durable task artifacts and retry Generator in a fresh primary session." if failure_category == "agent_context_overflow" else "Safe non-destructive runtime recovery; keep task artifacts and retry the Generator phase without asking the user.",
                    }
                elif next_action == "ask_user":
                    evaluation["askUserQuestion"] = {
                        "question": "Generator agent/runtime did not complete. Should AutoMind resume after the runtime is available or adjust the agent/timeout?",
                        "reason": evaluation["summary"],
                        "options": [
                            {"id": "resume_after_recovery", "label": "Resume after recovery", "impact": "Keep task artifacts and rerun the Generator phase after CLI/auth/network/runtime is fixed."},
                            {"id": "increase_timeout_resume", "label": "Increase timeout and resume", "impact": "Set a larger AUTOMIND_AGENT_TIMEOUT/AUTOMIND_CMD_TIMEOUT and continue the same task."},
                            {"id": "switch_agent", "label": "Switch agent", "impact": "Continue with another configured agent runtime."},
                        ],
                        "recommended": "resume_after_recovery",
                        "source": "generator_agent_failure",
                        "defaultAction": "resume",
                    }
                write_agent_failure_iteration_records(
                    task_dir,
                    iteration,
                    iter_log_dir,
                    agent,
                    "generator",
                    output,
                    evaluation,
                )
                write_evaluation_json(task_dir, evaluation)
                append_validation_history(task_dir, iteration, "blocked", evaluation["summary"], next_action)
                apply_evaluation_result(task_dir, evaluation)
                warn("Generator execution failed; safe auto-recovery will retry/resume without user input")
                run_after_phase_hooks(task_dir, "generator", payload={"iteration": iteration, "result": "blocked", "nextAction": next_action, "failureCategory": failure_category}, reason=f"after_generator_failure_iter_{iteration}")
                finalize_task_records(task_code, "generator_agent_unavailable")
                ensure_summary_generated(task_code, reason="generator_agent_unavailable")
                return False

            # \u68c0\u67e5\u662f\u5426 Done
            if should_pause_after_generator_for_human_input(task_dir, iteration):
                warn("Generator produced an ask_user gate; pausing before Evaluator.")
                run_after_phase_hooks(task_dir, "generator", payload={"iteration": iteration, "result": "ask_user"}, reason=f"after_generator_ask_user_iter_{iteration}")
                finalize_task_records(task_code, "generator_ask_user")
                ensure_summary_generated(task_code, reason="generator_ask_user")
                return False

            if "DONE" in output or "FINISHED" in output:
                success("Generator marked completion")
            run_after_phase_hooks(
                task_dir,
                "generator",
                payload={"iteration": iteration, "exitCode": code, "mode": mode},
                reason=f"after_generator_iter_{iteration}",
            )
            record_phase_end(task_dir, "generator")
            record_iter_phase_end(task_dir, iteration, "generator")
        elif skip_generator_this_iteration:
            log(f"Generator Phase skipped: resuming interrupted Evaluator for iteration {iteration}; existing generator.log is present")
        else:
            log(f"Generator Phase skipped: evaluator-only task ({evaluator_only_kind}) already has concrete config")

        # ----- Evaluator Phase -----
        record_phase_start(task_dir, "evaluator")
        record_iter_phase_start(task_dir, iteration, "evaluator")
        update_runtime_state(task_dir,
            status="evaluating",
            iteration=iteration,
            currentOwner="evaluator",
            nextAction="run_evaluator"
        )
        refresh_phase_transition_summary(task_dir)
        update_heartbeat(task_dir, owner="evaluator", note=f"iter-{iteration}")
        evaluator_purpose = write_iteration_purpose(task_dir, iteration, "evaluator", iter_log_dir)
        append_progress_log(
            task_dir,
            f"evaluator-phase-start mode={evaluator_purpose.get('mode')} purpose={evaluator_purpose.get('purpose')}",
            iteration=iteration,
            owner="evaluator",
        )
        log("Evaluator Phase...")
        evaluator_context = build_evaluator_context_pack(task_dir, iteration, iter_log_dir)
        update_runtime_state(task_dir,
            evaluatorContext={
                "iteration": iteration,
                "contextPack": rel_to_root(evaluator_context["markdownPath"]),
                "contextPackJson": rel_to_root(evaluator_context["jsonPath"]),
                "inheritsGeneratorContext": False,
                "validationOk": evaluator_context.get("validationOk", False),
                "validationIssues": evaluator_context.get("validationIssues", []),
            }
        )

        if not evaluator_context.get("validationOk", False):
            evaluation = {
                "iteration": iteration,
                "result": "blocked",
                "summary": "Evaluator context pack failed completeness/non-redundancy validation",
                "failedChecks": [{
                    "name": "evaluator_context_pack",
                    "reason": "; ".join(evaluator_context.get("validationIssues", [])),
                    "category": "invalid_evaluation_output",
                    "evidence": rel_to_root(evaluator_context["jsonPath"]),
                }],
                "evidence": [{"type": "other", "path": rel_to_root(evaluator_context["jsonPath"]), "note": "evaluator-context-json"}],
                "nextAction": "stop",
            }
            write_evaluation_json(task_dir, evaluation)
            append_validation_history(task_dir, iteration, "blocked", evaluation["summary"], "stop")
            apply_evaluation_result(task_dir, evaluation)
            warn("Evaluator context pack validation failed; stopping before launching Evaluator agent")
            run_after_phase_hooks(task_dir, "evaluator", payload={"iteration": iteration, "result": "blocked", "nextAction": "stop", "reason": "context_invalid"}, reason=f"after_evaluator_context_invalid_iter_{iteration}")
            finalize_task_records(task_code, "evaluator_context_invalid")
            ensure_summary_generated(task_code, reason="evaluator_context_invalid")
            return False

        warm_build_status = wait_for_warm_build(task_dir)
        wait_for_ui_exploration(task_dir)

        if warm_build_status.get("status") == "completed":
            # Informational only: the actual iOS/Android build runs inside the
            # external runner scripts, which reuse the primed disk caches
            # (DerivedData / Gradle / Pods) automatically. This log surfaces the
            # incremental-vs-full decision for diagnostics; it does not itself
            # drive or alter the runner's build command.
            incremental_possible, incremental_reason = is_incremental_build_possible(task_dir)
            if incremental_possible:
                log(f"Incremental build possible: {incremental_reason}")
            else:
                log(f"Full build required: {incremental_reason}")

        if mock_sequence:
            mock_result = mock_sequence[min(iteration - 1, len(mock_sequence) - 1)]
            output = f"MOCK_EVALUATOR_RESULT={mock_result}"
            code = 0
            if mock_result == "pass":
                set_validation_status(task_dir, "Finished")
                write_evaluation_json(task_dir, add_test_results_from_declared_cases(
                    task_dir,
                    {
                        "iteration": iteration,
                        "result": "pass",
                        "summary": "Mock Evaluator passed",
                        "failedChecks": [],
                        "evidence": [{"type": "log", "path": rel_to_root(iter_log_dir / "evaluator.log"), "note": "mock evaluator"}],
                        "nextAction": "finish",
                    },
                    "pass",
                    [rel_to_root(iter_log_dir / "evaluator.log")],
                    "mock evaluator pass",
                ))
            else:
                set_validation_status(task_dir, "Fail")
                write_evaluation_json(task_dir, {
                    "iteration": iteration,
                    "result": "fail",
                    "summary": "Mock Evaluator failed",
                    "failedChecks": [{"name": "mock_evaluator", "reason": "mock fail", "category": "validation_failure"}],
                    "evidence": [{"type": "log", "path": rel_to_root(iter_log_dir / "evaluator.log"), "note": "mock evaluator"}],
                    "nextAction": "retry_generator",
                })
        else:
            run_before_phase_hooks(task_dir, "evaluator", reason=f"before_evaluator_iter_{iteration}")
            if is_android_probe_flow_task(task_dir):
                log("Android probe-flow Evaluator Phase: auto-generate/run probe-flow.android.json")
                code, output = run_android_probe_flow_evaluator(task_dir, iteration, iter_log_dir, retries=0)
            elif evaluator_only_kind == "script":
                log("Script command Evaluator Phase: run generic verification command")
                code, output = run_script_command_evaluator(task_dir, iteration, iter_log_dir)
                # evaluator writes a full reusable Validation.md entry itself;
                # avoid adding the generic short entry again.
                evaluator_recorded_validation = True
            elif evaluator_only_kind == "ios-probe-flow":
                log("iOS probe-flow Evaluator Phase: run minimal iOS probe-flow adapter")
                code, output = run_ios_probe_flow_evaluator(task_dir, iteration, iter_log_dir)
                evaluator_recorded_validation = True
            elif evaluator_only_kind == "ios-xcuitest":
                log("iOS XCUITest Evaluator Phase: run physical/simulator xcodebuild test adapter")
                code, output = run_ios_xcuitest_evaluator(task_dir, iteration, iter_log_dir)
                evaluator_recorded_validation = True
            else:
                evaluator_user_input = (read_runtime_state(task_dir) or {}).get("userInput", "")
                try:
                    evaluator_prompt = render_prompt_template(
                        "evaluator_prompt.md",
                        task_dir=task_dir,
                        req_path=req_path,
                        val_path=val_path,
                        delivery_path=task_dir / "Delivery.md",
                        evaluator_context_path=evaluator_context["markdownPath"],
                        evaluator_context_json_path=evaluator_context["jsonPath"],
                        iteration=iteration,
                    )
                except FileNotFoundError:
                    evaluator_prompt = f"You are the context-isolated Evaluator. Read only the evaluator context pack at {evaluator_context['markdownPath']} as orchestrator-provided task context, independently inspect code/run verification commands as needed, and update {val_path} plus evaluation.json. Current iteration: {iteration}"
                evaluator_prompt = apply_runtime_language_instruction(evaluator_prompt, evaluator_user_input)
                write_rendered_prompt(iter_log_dir, "evaluator-prompt.md", evaluator_prompt)

                _agent_start = time.time()
                code, output = run_agent(mode, agent, evaluator_prompt, task_dir, phase="evaluator")
                _agent_duration = time.time() - _agent_start
                record_agent_call(task_dir, "evaluator", _agent_duration, exit_code=code)

        # \u5199\u65e5\u5fd7
        (iter_log_dir / "evaluator.log").write_text(output)

        if code != 0:
            failure_category = classify_agent_execution_failure(output)
            next_action = "retry_generator"
            evaluation = {
                "iteration": iteration,
                "result": "blocked",
                "summary": "Evaluator agent timed out before completing" if failure_category == "agent_timeout" else "Evaluator agent preflight or execution failed",
                "failedChecks": [{
                    "name": "agent_execution",
                    "reason": output[:1000],
                    "category": failure_category
                }],
                "nextAction": next_action
            }
            if is_safe_auto_recovery_failure(failure_category):
                evaluation["autoRecovery"] = {
                    "selected": "resume_after_recovery",
                    "source": "evaluator_agent_failure",
                    "reason": "Safe non-destructive runtime recovery; keep task artifacts and retry verification without asking the user.",
                }
            elif next_action == "ask_user":
                evaluation["askUserQuestion"] = {
                    "question": "Evaluator agent/runtime did not complete. Should AutoMind resume verification after the runtime is available or switch to a deterministic verifier?",
                    "reason": evaluation["summary"],
                    "options": [
                        {"id": "resume_after_recovery", "label": "Resume evaluator", "impact": "Keep artifacts and rerun the isolated Evaluator after CLI/auth/network/runtime is fixed."},
                        {"id": "use_deterministic_verifier", "label": "Use deterministic verifier", "impact": "Switch to script-command/platform verifier when available."},
                        {"id": "increase_timeout_resume", "label": "Increase timeout and resume", "impact": "Set a larger timeout and retry the isolated Evaluator."},
                    ],
                    "recommended": "resume_after_recovery",
                    "source": "evaluator_agent_failure",
                    "defaultAction": "resume",
                }
            write_agent_failure_iteration_records(
                task_dir,
                iteration,
                iter_log_dir,
                agent,
                "evaluator",
                output,
                evaluation,
            )
            write_evaluation_json(task_dir, evaluation)
            append_validation_history(task_dir, iteration, "blocked", evaluation["summary"], next_action)
            apply_evaluation_result(task_dir, evaluation)
            warn("Evaluator execution failed; safe auto-recovery will retry/resume without user input")
            run_after_phase_hooks(task_dir, "evaluator", payload={"iteration": iteration, "result": "blocked", "nextAction": next_action, "failureCategory": failure_category}, reason=f"after_evaluator_failure_iter_{iteration}")
            finalize_task_records(task_code, "evaluator_agent_unavailable")
            ensure_summary_generated(task_code, reason="evaluator_agent_unavailable")
            return False

        # \u8bfb\u53d6\u5e76\u6821\u9a8c Evaluator \u751f\u6210\u7684 evaluation.json - Validation.md \u53ea\u4f5c\u4e3a\u4eba\u7c7b/Agent \u53ef\u8bfb\u5386\u53f2
        raw_evaluation = read_evaluation_json(task_dir)
        if raw_evaluation is None:
            warn("No valid evaluation.json found; using Validation.md compatibility fallback")
            raw_evaluation = fallback_evaluation_from_validation(task_dir, iteration)

        evaluation, validation_errors = normalize_evaluation(task_dir, raw_evaluation, iteration)
        evaluation = apply_tc_reflection_budget(task_dir, evaluation, iteration)
        if validation_errors:
            warn(f"evaluation.json validation warning: {', '.join(validation_errors)}")

        evaluation_already_recorded = bool(locals().pop("evaluator_recorded_validation", False))
        if should_try_platform_self_repair(task_dir, evaluation):
            log("Platform self-repair: try Android probe-flow repair-rerun before Generator retry")
            repaired, repaired_evaluation, repair_output = attempt_android_probe_flow_self_repair(task_dir)
            (iter_log_dir / "platform-self-repair.log").write_text(repair_output)
            if repaired and repaired_evaluation:
                evaluation, validation_errors = normalize_evaluation(task_dir, repaired_evaluation, int(repaired_evaluation.get("iteration", iteration) or iteration))
                iteration = int(evaluation.get("iteration", iteration) or iteration)
                evaluation = apply_tc_reflection_budget(task_dir, evaluation, iteration)
                evaluation_already_recorded = True
                success(f"Platform self-repair reran evaluator: result={evaluation.get('result')} nextAction={evaluation.get('nextAction')}")
            else:
                warn("Platform self-repair did not apply a safe patch; falling back to Generator retry")

        quality_evaluation, quality_output = run_quality_evaluator(task_dir, iteration)
        (iter_log_dir / "quality-check.log").write_text(quality_output)
        if quality_evaluation is not None:
            evaluation, quality_validation_errors = normalize_evaluation(task_dir, quality_evaluation, iteration)
            evaluation = apply_tc_reflection_budget(task_dir, evaluation, iteration)
            if quality_validation_errors:
                warn(f"evaluation.json validation warning after quality-check merge: {', '.join(quality_validation_errors)}")

        if evaluation.get("nextAction") == "finish":
            evaluation, completion_report = apply_completion_gate(
                task_dir,
                evaluation,
                # Strict final gate: finish must be supported by explicit
                # testcase results or adapter-owned coverage. A top-level pass
                # alone is not enough to prove all required TestCases ran.
                allow_synthesize_pass=False,
                fail_next_action="replan" if evaluator_only else "retry_generator",
            )
            record_gate(
                task_dir,
                iteration=iteration,
                phase="evaluator",
                gate_type="completion_gate",
                passed=completion_report.get("result") == "pass",
                message="Completion check " + ("passed" if completion_report.get("result") == "pass" else "failed"),
                details={"issues": completion_report.get("issues", []), "warnings": completion_report.get("warnings", [])},
            )
            if completion_report.get("result") == "pass":
                success("Completion check passed")
            else:
                warn("Completion check blocked finish: " + "; ".join(completion_report.get("issues", [])[:3]))

        if evaluation.get("testResults"):
            record_tc_attempts(task_dir, evaluation, source="evaluation")
        write_evaluation_json(task_dir, evaluation)
        if not evaluation_already_recorded:
            append_validation_history(
                task_dir,
                iteration,
                evaluation["result"],
                evaluation["summary"],
                evaluation["nextAction"],
            )
        apply_evaluation_result(task_dir, evaluation)
        run_after_phase_hooks(
            task_dir,
            "evaluator",
            payload={"iteration": iteration, "result": evaluation.get("result"), "nextAction": evaluation.get("nextAction")},
            reason=f"after_evaluator_iter_{iteration}",
        )
        record_phase_end(task_dir, "evaluator")
        record_iter_phase_end(task_dir, iteration, "evaluator")

        next_action = evaluation["nextAction"]
        if next_action == "finish":
            record_decision(
                task_dir,
                iteration=iteration,
                phase="evaluator",
                message="Task completed successfully",
                decision_type="finish",
                action="finish",
                risk_level="low",
            )
            success("Validation passed. Loop finished")
            reconcile_validation_status(task_dir)
            flush_metrics(task_dir)
            from orchestrator.audit import write_audit_report
            write_audit_report(task_dir)
            finalize_task_records(task_code, "finish")
            ensure_summary_generated(task_code, reason="finish")
            return True
        elif next_action == "retry_generator":
            record_decision(
                task_dir,
                iteration=iteration,
                phase="evaluator",
                message="Validation failed, retrying generator",
                decision_type="retry",
                action="retry_generator",
                reason=evaluation.get("summary"),
                risk_level="low",
            )
            if evaluator_only:
                warn(f"Validation failed; current task is evaluator-only ({evaluator_only_kind}); stopped and waiting for external fix or Generator attachment")
                finalize_task_records(task_code, "evaluator_only_retry_pending")
                ensure_summary_generated(task_code, reason="evaluator_only_retry_pending")
                return False
            warn("Validation failed; continuing to next iteration...")
        elif next_action == "replan":
            record_decision(
                task_dir,
                iteration=iteration,
                phase="evaluator",
                message="Replan required",
                decision_type="policy",
                action="replan",
                reason=evaluation.get("summary"),
                risk_level="high",
            )
            if evaluator_only:
                warn(f"Replan required; current task is evaluator-only ({evaluator_only_kind}); stopped for external replanning")
                finalize_task_records(task_code, "evaluator_only_replan")
                ensure_summary_generated(task_code, reason="evaluator_only_replan")
                return False
            if os.environ.get("AUTOMIND_SKIP_AI_PLANNER", "0") == "1":
                warn("Replan required but AUTOMIND_SKIP_AI_PLANNER=1; stopping for manual replanning")
                finalize_task_records(task_code, "replan_skipped_by_env")
                ensure_summary_generated(task_code, reason="replan_skipped_by_env")
                return False
            warn("Replan required; running Phase 2 Refiner automatically before continuing")
            update_runtime_state(task_dir, currentOwner="planner", nextAction="run_test_planner", status="replan_pending")
            planner_ok = run_ai_test_planner(task_dir, agent=agent, mode=mode)
            workflow_ok, workflow_report = check_workflow_consistency(task_code)
            update_runtime_state(
                task_dir,
                workflowCheck={
                    "ok": workflow_ok,
                    "reason": "auto_replan",
                    "checkedAt": datetime.now().isoformat(timespec="seconds"),
                    "issues": workflow_report.get("issues", []),
                    "warnings": workflow_report.get("warnings", []),
                }
            )
            planner_state = read_runtime_state(task_dir) or {}
            planner_info = planner_state.get("planner") if isinstance(planner_state.get("planner"), dict) else {}
            if planner_info.get("needsUserInput") is True:
                warn("Phase 2 Refiner requires human input; pausing loop")
                ask_question = {
                    "question": "Phase 2 Refiner requires user input before continuing.",
                    "options": [],
                    "source": "planner",
                    "brainstorm": rel_to_root(task_dir / "Brainstorm.md"),
                }
                replan_eval = {
                    "iteration": iteration,
                    "result": "blocked",
                    "summary": "Replan requires human input before continuing.",
                    "failedChecks": [{
                        "name": "auto_replan",
                        "reason": "planner.needsUserInput=true",
                        "category": "needs_replan",
                        "evidence": rel_to_root(task_dir / "Brainstorm.md"),
                    }],
                    "evidence": [{"type": "other", "path": rel_to_root(task_dir / "Brainstorm.md"), "note": "planner questions"}],
                    "nextAction": "ask_user",
                    "askUserQuestion": ask_question,
                }
                write_evaluation_json(task_dir, replan_eval)
                apply_evaluation_result(task_dir, replan_eval)
                finalize_task_records(task_code, "auto_replan_ask_user")
                ensure_summary_generated(task_code, reason="auto_replan_ask_user")
                return False
            if not planner_ok or not workflow_ok:
                warn("Auto replan did not produce coherent artifacts; stopping for manual replanning")
                replan_eval = {
                    "iteration": iteration,
                    "result": "blocked",
                    "summary": "Auto replan failed to produce coherent Phase 2 artifacts.",
                    "failedChecks": [{
                        "name": "auto_replan",
                        "reason": "; ".join(workflow_report.get("issues", [])[:10]) or "planner execution/artifact validation failed",
                        "category": "needs_replan",
                        "evidence": rel_to_root(task_dir / "logs" / "planner" / "planner.log"),
                    }],
                    "evidence": [{"type": "log", "path": rel_to_root(task_dir / "logs" / "planner" / "planner.log"), "note": "auto-replan planner log"}],
                    "nextAction": "ask_user",
                    "askUserQuestion": {
                        "question": "Auto replan could not produce coherent artifacts. Please review Brainstorm.md/Requirements.md/TestCases.md/Plan.md and resolve the listed approach, AC, testcase, and evidence issues.",
                        "options": [],
                        "issues": workflow_report.get("issues", []),
                    },
                }
                write_evaluation_json(task_dir, replan_eval)
                apply_evaluation_result(task_dir, replan_eval)
                finalize_task_records(task_code, "auto_replan_failed")
                ensure_summary_generated(task_code, reason="auto_replan_failed")
                return False
            success("Auto replan completed; continuing to next Generator iteration")
            if not run_pre_build_workflow_gate(task_code, task_dir, agent=agent, mode=mode):
                finalize_task_records(task_code, "pre_build_workflow_gate_after_auto_replan")
                ensure_summary_generated(task_code, reason="pre_build_workflow_gate_after_auto_replan")
                return False
            update_runtime_state(task_dir, status="retry_pending", currentOwner="generator", nextAction="retry_generator")
            continue
        elif next_action == "ask_user":
            record_decision(
                task_dir,
                iteration=iteration,
                phase="evaluator",
                message="Human input required",
                decision_type="policy",
                action="ask_user",
                reason=evaluation.get("summary"),
                risk_level="high",
            )
            warn("Human input required; answer askUserQuestion before continuing")
            finalize_task_records(task_code, "ask_user")
            ensure_summary_generated(task_code, reason="ask_user")
            return False
        else:
            record_decision(
                task_dir,
                iteration=iteration,
                phase="evaluator",
                message="Unknown nextAction, stopping",
                decision_type="policy",
                action=next_action,
                risk_level="medium",
            )
            warn("Validation failed and stopped")
            finalize_task_records(task_code, "stop")
            ensure_summary_generated(task_code, reason="stop")
            return False

    # \u8fbe\u5230\u6700\u5927\u8fed\u4ee3
    update_runtime_state(task_dir,
        status="human_input_pending",
        iteration=iteration,
        currentOwner="human",
        nextAction="ask_user",
        lastResult="max_iterations_reached",
        askUserQuestion={
            "question": f"AutoMind reached the maximum iteration limit ({loop_limit}) without proving completion. What should happen next?",
            "reason": "The loop stopped to avoid unbounded repeated attempts.",
            "options": [
                {
                    "id": "replan_strategy",
                    "label": "Replan strategy",
                    "impact": "Return to Phase 2, revise approach/TestCases/verification target, then continue.",
                },
                {
                    "id": "increase_or_resume",
                    "label": "Resume explicitly",
                    "impact": "Continue only after the user accepts more attempts or fixes the blocker.",
                },
                {
                    "id": "stop_task",
                    "label": "Stop",
                    "impact": "Keep the terminal evidence and do not continue automatically.",
                },
            ],
            "recommended": "replan_strategy",
            "source": "max_iterations_gate",
        },
    )
    warn(f"Reached max iterations ({loop_limit})")
    finalize_task_records(task_code, "max_iterations")
    ensure_summary_generated(task_code, reason="max_iterations")
    return False


# ============================================================
# \u603b\u7ed3
# ============================================================





# ============================================================
# CLI \u5165\u53e3
# ============================================================

def cmd_list():
    """\u5217\u51faTask"""
    tasks = list_tasks()
    if not tasks:
        log("No tasks yet")
    else:
        log(f"Total {len(tasks)} tasks:")
        for task in tasks:
            task_dir = get_task_dir(task)
            has_req = any(path.exists() for path in requirement_contract_paths(task_dir))
            has_val = (task_dir / "Validation.md").exists()
            status = "✓" if has_val else "○"
            print(f"  {status} {task}")


def scaffold_task_artifacts(user_input: str) -> tuple[str, Path]:
    """Create a task and deterministic AutoMind artifacts without launching an agent loop.

    This is used by current-session integrations such as slash commands: the
    host coding agent keeps using its current model/session, while AutoMind CLI
    only prepares the artifact container and later runs deterministic gates.
    """
    task_code, task_dir = create_task(user_input)

    # \u751f\u6210\u6587\u6863
    generate_brainstorm_md(task_dir, user_input)
    generate_requirements_md(task_dir, user_input)
    generate_testcases_md(task_dir, user_input)
    generate_plan_md(task_dir, user_input)
    write_workflow_contract(task_dir)
    generate_validation_md(task_dir)
    if can_run_evaluator_without_generator(task_dir):
        update_runtime_state(task_dir,
            status="planned",
            currentOwner="evaluator",
            nextAction="run_evaluator"
        )
    else:
        update_runtime_state(task_dir,
            status="planned",
            currentOwner="planner",
            nextAction="run_generator"
        )

    ok, issues = validate_planner_artifacts(task_dir)
    # Do not ask the user immediately after deterministic scaffold. The scaffold
    # may record hard/preflight questions (device/signing/safety), but Phase 2
    # Refiner must first inspect the requirement and project context, produce
    # soft implementation-quality questions, and then AutoMind asks one bundled
    # pre-implementation question if still needed. This keeps TUI and skill mode
    # aligned: scaffold -> model/current-agent refinement -> one-shot ask_user.
    update_runtime_state(
        task_dir,
        plannerArtifactCheck={
            "ok": ok,
            "checkedAt": datetime.now().isoformat(timespec="seconds"),
            "issues": issues,
        },
    )
    if not ok:
        warn("Initial scaffold has planner-artifact gaps; current-session planner should refine before implementation:")
        for issue in issues[:10]:
            warn(f"  - {issue}")
    return task_code, task_dir


def cmd_scaffold(user_input: str):
    """Create task artifacts for current-session slash-command/skill mode."""
    task_code, task_dir = scaffold_task_artifacts(user_input)
    # Skill-mode automation: persist active task code so Hook scripts and
    # subsequent CLI calls can pick it up via three-level fallback.
    write_current_task(task_code)
    success("AutoMind task scaffolded for current-session mode.")
    print(f"TASK_CODE={task_code}")
    print(f"TASK_DIR={task_dir}")
    state = read_runtime_state(task_dir) or {}
    log("Next: refine Brainstorm.md/Requirements.md/TestCases.md/Plan.md in the current agent session first; then ask one bundled pre-implementation question only if model/project-context review still needs user decisions.")


def cmd_context_pack(task_code: str, iteration: int | None = None):
    """Create an Evaluator context pack without launching an external agent."""
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        sys.exit(1)
    state = read_runtime_state(task_dir) or {}
    if iteration is None:
        iteration = int(state.get("iteration", 0) or 0) + 1
    iter_log_dir = task_dir / "logs" / f"iter-{iteration}"
    pack = build_evaluator_context_pack(task_dir, iteration, iter_log_dir)
    update_runtime_state(task_dir,
        status="evaluating",
        iteration=iteration,
        evaluatorContext={
            "iteration": iteration,
            "contextPack": rel_to_root(pack["markdownPath"]),
            "contextPackJson": rel_to_root(pack["jsonPath"]),
            "inheritsGeneratorContext": False,
            "validationOk": pack.get("validationOk", False),
            "validationIssues": pack.get("validationIssues", []),
        },
        currentOwner="evaluator",
        nextAction="run_evaluator",
    )
    if pack.get("validationOk"):
        success(f"Evaluator context pack created: {pack['markdownPath']}")
    else:
        warn(f"Evaluator context pack has issues: {pack.get('validationIssues', [])}")
    print(f"CONTEXT_PACK={pack['markdownPath']}")
    print(f"CONTEXT_PACK_JSON={pack['jsonPath']}")
    if not pack.get("validationOk"):
        sys.exit(1)



def cmd_mobile_review_gate_smoke():
    """Verify mobile-client tasks surface physical-device discovery before implementation."""
    old_mock = os.environ.get("AUTOMIND_MOCK_CONNECTED_DEVICES")
    try:
        os.environ["AUTOMIND_MOCK_CONNECTED_DEVICES"] = "[]"
        user_input = "Fix an iOS app launch crash and verify the app opens"
        questions = detect_brainstorm_questions(user_input)
        review = build_pre_implementation_review_state(user_input, questions)
        joined = "\n".join(review.get("questions", []))
        if review.get("decision") != "ask_user":
            error(f"mobile-review-gate smoke failed: expected ask_user, got {review.get('decision')}")
            sys.exit(1)
        if "No authorized connected physical device was detected" not in joined or "simulator/emulator" not in joined:
            error("mobile-review-gate smoke failed: missing no-device simulator/emulator fallback question")
            print(json.dumps(review, ensure_ascii=False, indent=2))
            sys.exit(1)
        if review.get("recommendedOption") != "use_simulator_emulator":
            error(f"mobile-review-gate smoke failed: no-device recommendation should be simulator/emulator, got {review.get('recommendedOption')}")
            print(json.dumps(review, ensure_ascii=False, indent=2))
            sys.exit(1)

        os.environ["AUTOMIND_MOCK_CONNECTED_DEVICES"] = json.dumps([{
            "platform": "ios",
            "id": "MOCK-UDID-001",
            "name": "QA iPhone",
            "detail": "17.5 wired DeveloperMode",
        }])
        with_device = build_pre_implementation_review_state(user_input, detect_brainstorm_questions(user_input))
        with_device_joined = "\n".join(with_device.get("questions", []))
        if "Detected connected physical device(s)" not in with_device_joined or "QA iPhone" not in with_device_joined:
            error("mobile-review-gate smoke failed: connected device was not surfaced to user")
            print(json.dumps(with_device, ensure_ascii=False, indent=2))
            sys.exit(1)
        if with_device.get("recommendedOption") != "use_real_device":
            error(f"mobile-review-gate smoke failed: detected-device recommendation should be real device, got {with_device.get('recommendedOption')}")
            print(json.dumps(with_device, ensure_ascii=False, indent=2))
            sys.exit(1)

        non_client = build_pre_implementation_review_state(
            "Update iOS markdown docs and verify spelling",
            detect_brainstorm_questions("Update iOS markdown docs and verify spelling"),
        )
        non_client_mobile = non_client.get("mobileVerification", {})
        if non_client_mobile.get("targetUnclear"):
            error("mobile-review-gate smoke failed: non-client iOS mention should not ask real-device/simulator target")
            print(json.dumps(non_client, ensure_ascii=False, indent=2))
            sys.exit(1)
    finally:
        if old_mock is None:
            os.environ.pop("AUTOMIND_MOCK_CONNECTED_DEVICES", None)
        else:
            os.environ["AUTOMIND_MOCK_CONNECTED_DEVICES"] = old_mock

    explicit = build_pre_implementation_review_state(
        "Fix an iOS app launch crash and verify on simulator",
        detect_brainstorm_questions("Fix an iOS app launch crash and verify on simulator"),
    )
    explicit_joined = "\n".join(explicit.get("questions", []))
    if "Mobile verification target is unclear" in explicit_joined:
        error("mobile-review-gate smoke failed: explicit simulator target still produced target-unclear question")
        print(json.dumps(explicit, ensure_ascii=False, indent=2))
        sys.exit(1)
    try:
        os.environ["AUTOMIND_MOCK_CONNECTED_DEVICES"] = json.dumps([{
            "platform": "ios",
            "id": "MOCK-UDID-002",
            "name": "Connected iPhone",
            "detail": "18.0 wired DeveloperMode",
        }])
        explicit_with_device = build_pre_implementation_review_state(
            "Fix an iOS app launch crash and verify on simulator",
            detect_brainstorm_questions("Fix an iOS app launch crash and verify on simulator"),
        )
        explicit_device_joined = "\n".join(explicit_with_device.get("questions", []))
        explicit_mobile = explicit_with_device.get("mobileVerification", {})
        explicit_bundle = explicit_with_device.get("decisionBundle", {})
        if explicit_with_device.get("decision") != "ask_user":
            error("mobile-review-gate smoke failed: non-trivial explicit simulator task should still ask the formal pre-implementation bundle")
            print(json.dumps(explicit_with_device, ensure_ascii=False, indent=2))
            sys.exit(1)
        if "Connected iPhone" in explicit_device_joined or "Mobile verification target is unclear" in explicit_device_joined:
            error("mobile-review-gate smoke failed: explicit simulator target should not re-ask device target even when a real device is connected")
            print(json.dumps(explicit_with_device, ensure_ascii=False, indent=2))
            sys.exit(1)
        if not explicit_mobile.get("simulatorOnlyRequestedWithConnectedDevice") or explicit_bundle.get("verificationTarget") != "simulator_emulator":
            error("mobile-review-gate smoke failed: explicit simulator target should be recorded as simulator/emulator verification")
            print(json.dumps(explicit_with_device, ensure_ascii=False, indent=2))
            sys.exit(1)
        if explicit_with_device.get("recommendedOption") == "use_real_device":
            error("mobile-review-gate smoke failed: explicit simulator target should not recommend switching to real device")
            print(json.dumps(explicit_with_device, ensure_ascii=False, indent=2))
            sys.exit(1)
    finally:
        if old_mock is None:
            os.environ.pop("AUTOMIND_MOCK_CONNECTED_DEVICES", None)
        else:
            os.environ["AUTOMIND_MOCK_CONNECTED_DEVICES"] = old_mock
    success("mobile-review-gate smoke passed")


def cmd_dependency_setup_smoke():
    """Verify optional helper dependency setup plans are complete and local-only."""
    expected = {
        "android": {
            "venvSuffix": ".venv-android-tools",
            "requirements": "requirements/android-tools.txt",
            "modules": {"adbutils", "uiautomator2"},
        },
        "ios": {
            "venvSuffix": ".venv-ios-tools",
            "requirements": "requirements/ios-tools.txt",
            "modules": {"pymobiledevice3"},
        },
        "visual": {
            "venvSuffix": ".venv-visual-tools",
            "requirements": "requirements/visual-tools.txt",
            "modules": {"PIL", "numpy", "imagehash"},
        },
    }
    for target, want in expected.items():
        profile = AUTOMATION_TOOL_PROFILES.get(target) or {}
        plan = automation_setup_command_plan(target)
        if not plan.get("requirementsExists"):
            error(f"dependency-setup smoke failed: missing requirements for {target}: {plan.get('requirements')}")
            sys.exit(1)
        if not str(plan.get("requirements", "")).endswith(want["requirements"]):
            error(f"dependency-setup smoke failed: unexpected requirements path for {target}: {plan.get('requirements')}")
            sys.exit(1)
        if want["venvSuffix"] not in str(plan.get("venv", "")):
            error(f"dependency-setup smoke failed: {target} venv is not project-local expected suffix {want['venvSuffix']}: {plan.get('venv')}")
            sys.exit(1)
        modules = set(profile.get("modules") or [])
        if not want["modules"].issubset(modules):
            error(f"dependency-setup smoke failed: {target} modules incomplete: {sorted(modules)}")
            sys.exit(1)
        commands = plan.get("commands") or []
        install_cmd = commands[2] if len(commands) >= 3 else []
        if "-r" not in install_cmd:
            error(f"dependency-setup smoke failed: {target} install command does not use requirements file: {install_cmd}")
            sys.exit(1)
        forbidden = " ".join(plan.get("willNotInstall", [])).lower()
        for marker in ["sdk", "xcode", "signing", "trust", "sudo"]:
            if marker not in forbidden:
                error(f"dependency-setup smoke failed: {target} missing will-not-install marker {marker}")
                sys.exit(1)

    with tempfile.TemporaryDirectory(prefix="automind-deps-smoke-") as tmp:
        root = Path(tmp)
        (root / "package.json").write_text(json.dumps({
            "scripts": {"build": "vite build", "test:e2e": "playwright test"},
            "dependencies": {"vite": "^5.0.0", "express": "^4.0.0"},
            "devDependencies": {"@playwright/test": "^1.0.0"},
        }))
        (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
        (root / "requirements.txt").write_text("fastapi==0.110.0\n")
        (root / "Dockerfile").write_text("FROM python:3.12-slim\n")
        report = build_project_dependency_report(root=root)
        if not report.get("readOnly"):
            error("dependency-setup smoke failed: project dependency check must be read-only")
            sys.exit(1)
        policy = report.get("policy", {})
        if not policy.get("doesNotAutoInstallTargetProjectDependencies") or not policy.get("targetProjectDepsUseProjectNativeCommands"):
            error("dependency-setup smoke failed: project dependency policy missing native/no-auto-install guard")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            sys.exit(1)
        plans = {item.get("id"): item for item in report.get("dependencyPlans", [])}
        js_plan = plans.get("js-package") or {}
        install_cmd = ((js_plan.get("install") or {}).get("command") or "")
        if "pnpm install --frozen-lockfile" not in install_cmd:
            error(f"dependency-setup smoke failed: JS lockfile install command not frozen/pnpm: {install_cmd}")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            sys.exit(1)
        categories = set(report.get("detectedCategories") or [])
        for category in ["web", "server"]:
            if category not in categories:
                error(f"dependency-setup smoke failed: missing detected category {category}: {sorted(categories)}")
                print(json.dumps(report, ensure_ascii=False, indent=2))
                sys.exit(1)
        high_impact = " ".join(sum([item.get("highImpactOrAskUser", []) for item in report.get("dependencyPlans", [])], []))
        for marker in ["private registry", "Docker", "system"]:
            if marker.lower() not in high_impact.lower():
                error(f"dependency-setup smoke failed: missing high-impact ask-user marker {marker}")
                print(json.dumps(report, ensure_ascii=False, indent=2))
                sys.exit(1)
    success("dependency-setup smoke passed")


def cmd_ui_action_capability_smoke():
    """Verify docs/prompts/export templates advertise real App UI action capability."""
    required = {
        "AGENTS.md": ["AutoMind can operate real apps", "android-probe-flow", "ios-xcuitest"],
        "docs/workflow.md": ["UI interaction as an available verification capability", "probe-flow.ios.json", "Do not random-click"],
        "docs/phase2-requirement.md": ["do not say AutoMind cannot operate the app", "android-probe-flow", "ios-probe-flow-materialize"],
        "docs/references/test-design-guide.md": ["Mobile App/UI automation capability is first-class", "tap_if_present", "Use coordinate taps only as a documented"],
        "docs/references/probe-flow-generation.md": ["UI action capability contract", "Android executes task-local `probe-flow.android.json`", "iOS executes through XCUITest"],
        "docs/references/verification-flow.md": ["External sink", "probe-flow", "tap_if_present", "Direct-route page load", "low-fidelity last resort"],
        "docs/references/verification-flow-ios.md": ["AutoMind can operate iOS apps through XCUITest/probe-flow", "ios-xcuitest", "tap_if_present"],
        "docs/references/verification-flow-android.md": ["Android probe-flow can install/launch", "uiautomator", "tap_if_present"],
        "templates/phase2_planner_prompt.md": ["Do not state that AutoMind cannot operate the app", "Mobile App/UI action policy", "Every action must have a post-action assertion"],
        "templates/evaluator_prompt.md": ["Treat in-app UI interaction as an available AutoMind verification capability", "Do not stop at", "android-probe-flow"],
        "templates/generator_prompt.md": ["implementation requires app interaction", "probe-flow.ios.json", "Do not claim AutoMind is unable to click"],
        "scripts/export_skill.py": ["AutoMind can operate real apps", "android-preflight", "ios-xcuitest"],
        "scripts/export_command.py": ["AutoMind verification is action-capable", "Android probe-flow", "iOS XCUITest"],
    }
    for rel, needles in required.items():
        content = (AUTOMIND_ROOT / rel).read_text(errors="ignore")
        missing = [item for item in needles if item not in content]
        if missing:
            error(f"ui-action-capability smoke failed: {rel} missing {missing}")
            sys.exit(1)
    forbidden_patterns = [
        ("scripts/ios_action_plan.py", "Do not directly tap real apps in v1"),
        ("scripts/ios_action_plan.py", "v1 does not directly tap physical devices"),
    ]
    for rel, phrase in forbidden_patterns:
        content = (AUTOMIND_ROOT / rel).read_text(errors="ignore")
        if phrase in content:
            error(f"ui-action-capability smoke failed: misleading phrase still present in {rel}: {phrase}")
            sys.exit(1)
    success("ui-action-capability smoke passed")


def cmd_delivery_gate_smoke():
    """Verify Generator output cannot enter final Verify without Delivery.md."""
    task_code = "delivery_gate_smoke"
    task_dir = TASKS_DIR / task_code
    if task_dir.exists():
        shutil.rmtree(task_dir)
    ensure_dir(task_dir)
    for name, text in {
        ".user_input.txt": "delivery gate smoke",
        "Brainstorm.md": "# Brainstorm\n\n## Assumptions / Questions\n- Assumption: fixture only.\n\n## Pre-implementation user review decision\n- decision: auto_proceed\n- reason: fixture only.\n",
        "Requirements.md": "# Requirements\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — Delivery handoff\n- **AC-001**: Missing Delivery.md after Generator output blocks verification handoff.\n  - Verification method: TC-001\n",
        "TestCases.md": "# Test Cases\n\nQuality coverage: not applicable for this fixture.\n\n| ID | Requirement/AC | Type | Runtime level | Preconditions / tools | Command / AutoMind command | Steps / verification method | Expected evidence/result | Dependency | Required? |\n|----|----------------|------|---------------|-----------------------|--------------------------|-----------------------------|--------------------------|------------|-----------|\n| TC-001 | R01 / AC-001 | Functional | runtime | Fixture generator output exists; logs/iter-1/generator.log is present; Delivery.md is intentionally missing. | automind workflow-check delivery_gate_smoke and automind context-pack delivery_gate_smoke 1 | Run workflow-check after Generator output; run context-pack; assert both fail before model Evaluator. | workflow-check issue and logs/iter-1/evaluator-context.json required-file issue. | - | yes |\n",
        "Plan.md": "# Plan\n\n## First functional batch\n- TC-001\n\n## Verification command\n- `automind workflow-check delivery_gate_smoke`\n- `automind context-pack delivery_gate_smoke 1`\n\n## Implementation Checklist\n| ID | Source | Status | Owner | Evidence | Notes |\n|----|--------|--------|-------|----------|-------|\n| T01 | R01 / AC-001 / TC-001 | done | generator | logs/iter-1/generator.log | Generator ran but omitted Delivery.md. |\n\n## Verification Checklist\n| ID | Required | Status | Owner | Evidence | Notes |\n|----|----------|--------|-------|----------|-------|\n| TC-001 | yes | todo | evaluator | logs/iter-1/evaluator-context.json | Gates should fail before model Evaluator. |\n",
        "Validation.md": "# Validation\n",
        "runtime-state.json": json.dumps({
            "taskId": task_code,
            "status": "evaluating",
            "iteration": 1,
            "currentOwner": "evaluator",
            "nextAction": "run_evaluator",
            "planner": {
                "needsUserInput": False,
                "preImplementationReview": {"decision": "auto_proceed", "questions": []},
            },
        }, ensure_ascii=False, indent=2),
    }.items():
        (task_dir / name).write_text(text)
    iter_log_dir = task_dir / "logs" / "iter-1"
    ensure_dir(iter_log_dir)
    (iter_log_dir / "generator.log").write_text("Generator ran but did not write Delivery.md.\n")

    workflow_ok, workflow_report = check_workflow_consistency(task_code)
    if workflow_ok:
        error("delivery-gate smoke failed: workflow-check passed without Delivery.md")
        sys.exit(1)
    if not any("Delivery.md missing or empty after Generator run" in issue for issue in workflow_report.get("issues", [])):
        error(f"delivery-gate smoke failed: expected Delivery.md workflow issue, got {workflow_report.get('issues', [])}")
        sys.exit(1)

    pack = build_evaluator_context_pack(task_dir, 1, iter_log_dir)
    if pack.get("validationOk"):
        error("delivery-gate smoke failed: evaluator context pack passed without Delivery.md")
        sys.exit(1)
    if not any("Delivery.md" in issue for issue in pack.get("validationIssues", [])):
        error(f"delivery-gate smoke failed: expected Delivery.md context-pack issue, got {pack.get('validationIssues', [])}")
        sys.exit(1)
    success("delivery-gate smoke passed")


def _safe_auto_recovery_signal(task_dir: Path) -> dict | None:
    """Return latest safe agent/runtime auto-recovery signal, if any.

    This is intentionally narrow: only durable Evaluator/Generator agent-runtime
    interruptions that already wrote `evaluation.autoRecovery` and a whitelisted
    failure category may be auto-resumed. Product/test failures, ask_user gates,
    unsafe decisions, and terminal states are not auto-resumed here.
    """
    state = read_runtime_state(task_dir) or {}
    if state.get("status") == "human_input_pending" or state.get("nextAction") == "ask_user":
        return None
    if str(state.get("status") or "") in {"finished", "completed", "aborted", "paused_by_user"}:
        return None
    evaluation = read_evaluation_json(task_dir)
    if not isinstance(evaluation, dict):
        return None
    auto_recovery = evaluation.get("autoRecovery")
    if not isinstance(auto_recovery, dict):
        return None
    category = _first_failed_check_category(evaluation)
    if not is_safe_auto_recovery_failure(category):
        return None
    selected = str(auto_recovery.get("selected") or "")
    if selected not in {"resume_after_recovery", "fresh_session_resume"}:
        return None
    next_action = str(evaluation.get("nextAction") or state.get("nextAction") or "")
    if next_action not in {"retry_generator", "run_generator", "run_evaluator", "resume_after_recovery"}:
        return None
    return {
        "category": category,
        "selected": selected,
        "source": auto_recovery.get("source") or "agent_runtime_failure",
        "reason": auto_recovery.get("reason") or evaluation.get("summary") or "safe agent/runtime recovery",
    }


def run_harness_loop_with_safe_auto_resume(
    task_code: str,
    *,
    agent: str = "auto",
    mode: Literal["cli", "llm"] = "cli",
    max_auto_resumes: int | None = None,
) -> bool:
    """Run the harness loop and automatically resume safe agent/runtime failures.

    `run_harness_loop()` records safe infrastructure interruptions as
    `evaluation.autoRecovery`. The supervisor consumes that signal so CLI/TUI
    owned modes do not require the user to manually run `automind resume` for
    retryable network/timeout/stall/context-window failures.
    """
    task_dir = get_task_dir(task_code)
    if max_auto_resumes is None:
        raw = os.environ.get("AUTOMIND_SAFE_AUTO_RESUME_MAX", "3")
        try:
            max_auto_resumes = max(0, int(raw))
        except (TypeError, ValueError):
            max_auto_resumes = 3
    attempts = 0
    while True:
        ok = run_harness_loop(task_code, agent=agent) if mode == "cli" else run_harness_loop(task_code, agent=agent, mode=mode)
        if ok:
            return True
        signal = _safe_auto_recovery_signal(task_dir)
        if not signal:
            return False
        if attempts >= max_auto_resumes:
            warn(f"Safe auto-resume limit reached ({attempts}/{max_auto_resumes}); leaving task for manual resume")
            append_event(
                task_dir,
                "safe_auto_resume_limit",
                f"Safe auto-resume limit reached category={signal.get('category')}",
                level="warn",
                source="automind",
                data={"attempts": attempts, "maxAutoResumes": max_auto_resumes, **signal},
            )
            update_runtime_state(
                task_dir,
                safeAutoResume={
                    "status": "limit_reached",
                    "attempts": attempts,
                    "maxAutoResumes": max_auto_resumes,
                    **signal,
                },
            )
            return False
        attempts += 1
        if signal.get("selected") == "fresh_session_resume":
            clear_task_primary_session(task_dir, reason="safe_auto_resume_fresh_session")
        update_runtime_state(
            task_dir,
            status="retry_pending",
            currentOwner="generator",
            nextAction="retry_generator",
            safeAutoResume={
                "status": "retrying",
                "attempt": attempts,
                "maxAutoResumes": max_auto_resumes,
                **signal,
            },
        )
        append_event(
            task_dir,
            "safe_auto_resume",
            f"Auto-resuming safe agent/runtime recovery {attempts}/{max_auto_resumes}: {signal.get('category')}",
            level="warn",
            source="automind",
            data={"attempt": attempts, "maxAutoResumes": max_auto_resumes, **signal},
        )
        warn(f"Auto-resuming safe agent/runtime recovery {attempts}/{max_auto_resumes}: {signal.get('category')}")


def cmd_ask(user_input: str, agent: str = "auto", tui: bool = False):
    """Create task and start CLI-owned loop."""
    task_code, task_dir = scaffold_task_artifacts(user_input)
    seed_task_primary_session_from_tui_chat(task_dir, agent=agent)
    # Skill-mode automation parity with scaffold: even though ask launches the
    # detached loop, host-agent Hook scripts still benefit from a stable
    # marker pointing at the active task in this workspace.
    write_current_task(task_code)

    success(f"Starting Harness Loop (agent={agent})...")
    success(f"Task directory: {task_dir}")

    if tui:
        run_tui_owned_loop(
            task_code,
            task_dir,
            agent=agent,
            run_loop=lambda: run_harness_loop_with_safe_auto_resume(task_code, agent=agent),
            ask_execution_policy=True,
        )
        return

    # Non-TTY / detached ask keeps historical background behavior for scripts.
    import threading

    def run_loop():
        result = run_harness_loop_with_safe_auto_resume(task_code, agent=agent)
        ensure_summary_generated(task_code, reason="ask_background_finish" if result else "ask_background_terminal")

    thread = threading.Thread(target=run_loop)
    thread.start()

    log("Loop started in the background. Use ./automind.sh logs to inspect progress.")

















def cmd_plan(task_code: str, agent: str = "auto"):
    """Run AI Phase 2 Refiner for an existing task without starting implementation."""
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        return
    ok = run_ai_test_planner(task_dir, agent=agent)
    if ok:
        success(f"Phase 2 Refiner refined task: {task_code}")
        if start_warm_build(task_dir):
            log("Warm build started in background; will wait for completion before Evaluator")
    else:
        warn(f"Phase 2 Refiner did not complete; deterministic scaffold remains: {task_code}")

































def has_unanswered_pending_question(task_dir: Path) -> bool:
    """Return True when task artifacts contain a pending ask_user without answer."""
    pending = normalize_pending_question(task_dir)
    if not pending:
        return False
    return latest_pending_answer_matches_question(task_dir, pending) is None


def normalize_unanswered_pending_question_state(task_dir: Path) -> None:
    """Park task at human_input_pending when a durable unanswered question exists."""
    pending = normalize_pending_question(task_dir)
    state = read_runtime_state(task_dir) or {}
    update_runtime_state(
        task_dir,
        status="human_input_pending",
        currentOwner="human",
        nextAction="ask_user",
        lastResult="blocked",
        askUserQuestion=state.get("askUserQuestion") or ((pending or {}).get("raw") if isinstance(pending, dict) else None),
    )

def render_pending_question_guidance(task_code: str, task_dir: Path, agent: str = "auto") -> str:
    """Render actionable CLI guidance for a pending ask_user gate."""
    pending = normalize_pending_question(task_dir) or {}
    question = str(pending.get("question") or "AutoMind needs user input before continuing.")
    options = pending.get("options") if isinstance(pending.get("options"), list) else []
    lines = [
        "Task is waiting for human input.",
        "",
        "Question:",
        question,
    ]
    if options:
        lines += ["", "Options:"]
        for idx, option in enumerate(options, 1):
            if isinstance(option, dict):
                option_id = option.get("id") or str(idx)
                label = option.get("label") or option_id
                impact = option.get("impact")
                suffix = f" — {impact}" if impact else ""
                lines.append(f"  {idx}. {option_id} - {label}{suffix}")
            else:
                lines.append(f"  {idx}. {option}")
    recommended = pending.get("recommended")
    if recommended:
        lines += ["", f"Recommended option: {recommended}"]
    lines += [
        "",
        "Next:",
        f"  automind answer {task_code} --option <option-id>",
        f"  automind resume {task_code} {agent}",
        "",
        "Or run interactively:",
        f"  automind resume {task_code} {agent} --tui",
    ]
    return "\n".join(lines)


def recover_task_bound_agent(state: dict) -> str | None:
    """Recover the coding agent a task was originally created/run with.

    A task records its bound agent in several places. When the user resumes
    without naming an agent, AutoMind must keep using that same agent instead of
    re-running codex-first `auto` discovery, which would silently switch e.g. a
    claude task onto codex. Precedence: live primary session -> execution policy
    -> planner. Returns a supported agent name, or None when nothing reusable is
    recorded (then the caller falls back to `auto`).
    """
    if not isinstance(state, dict):
        return None
    supported = set(AGENT_ADAPTERS.keys())
    candidates: list = []
    sessions = state.get("agentSessions")
    if isinstance(sessions, dict):
        primary = sessions.get("primary")
        if isinstance(primary, dict):
            candidates.append(primary.get("agent"))
    policy = state.get("agentExecutionPolicy")
    if isinstance(policy, dict):
        candidates.append(policy.get("agent"))
    planner = state.get("planner")
    if isinstance(planner, dict):
        candidates.append(planner.get("agent"))
    for candidate in candidates:
        name = str(candidate or "").strip().lower()
        if name in supported:
            return name
    return None


def cmd_resume(task_code: str, agent: str = "auto", tui: bool = False):
    """\u6839\u636e runtime-state.json task"""
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        return

    state = read_runtime_state(task_dir)
    if not state:
        error("Missing runtime-state.json; cannot resume")
        return

    reconcile_task_state(task_dir, reason="resume")
    refresh_phase_transition_summary(task_dir)
    state = read_runtime_state(task_dir) or state

    # When the user resumes without naming an agent, keep using the agent the
    # task was originally created/run with instead of codex-first `auto`
    # discovery, which would silently switch the task onto a different agent.
    # An explicit agent argument still overrides (intentional switch).
    if (agent or "auto").strip().lower() in {"", "auto"}:
        bound_agent = recover_task_bound_agent(state)
        if bound_agent:
            agent = bound_agent
            log(f"Resuming with task-bound coding agent: {bound_agent}")

    status = state.get("status")
    if status not in TASK_STATUSES:
        error(f"Unknown task status: {status}")
        return

    evaluation = read_evaluation_json(task_dir)
    if status != "human_input_pending" and isinstance(evaluation, dict) and evaluation.get("nextAction") == "ask_user":
        warn("Task evaluation already requires human input; normalizing runtime-state before resume.")
        apply_evaluation_result(task_dir, evaluation)
        status = "human_input_pending"

    terminalish_status = status in {"finished", "completed"}
    terminalish_next = str(state.get("nextAction") or "").strip().lower() in {"finish", "done", "completed"}
    if terminalish_status or terminalish_next:
        authoritative_pass = task_has_authoritative_terminal_pass(task_dir)
        verdict = state.get("completionVerdict") if isinstance(state.get("completionVerdict"), dict) else {}
        if authoritative_pass and status == "finished":
            warn("Task is already finished; no resume needed")
            return
        if authoritative_pass and status == "completed":
            warn("Task uses legacy completed status but completion is proven; normalizing to finished and closing resume")
            update_runtime_state(task_dir, status="finished", nextAction="finish", currentOwner="automind")
            return
        warn("Task has a terminal marker but completion-check is not authoritative; reopening loop for repair")
        update_runtime_state(
            task_dir,
            status="retry_pending",
            nextAction="retry_generator",
            currentOwner="automind",
            falseFinishRecovery={
                "reason": "finished_without_completion_pass",
                "previousStatus": state.get("status"),
                "previousNextAction": state.get("nextAction"),
                "completionCheck": state.get("completionCheck"),
                "completionVerdict": verdict or None,
                "authoritativeCompletionPass": authoritative_pass,
            },
        )
        status = "retry_pending"
    if status == "aborted":
        warn("Task is aborted; auto-resume is disabled")
        return
    if status == "paused_by_user":
        warn("Task was paused by user interrupt; resuming from safe loop boundary with a fresh primary agent session")
        cleared_primary = clear_task_primary_session(task_dir, reason="resume_after_user_interrupt")
        update_runtime_state(
            task_dir,
            status="retry_pending",
            nextAction="resume_after_user_interrupt",
            currentOwner="automind",
            interruptedByUser=False,
            resumeRecovery={
                "reason": "resume_after_user_interrupt",
                "clearedPrimaryAgentSession": cleared_primary,
            },
        )
        status = "retry_pending"

    if has_unanswered_pending_question(task_dir):
        warn("Task has an unanswered ask_user gate; opening answer prompt before resuming.")
        normalize_unanswered_pending_question_state(task_dir)
        status = "human_input_pending"

    if status == "human_input_pending":
        if tui or (sys.stdin.isatty() and sys.stdout.isatty()):
            warn("Task is waiting for human input; opening interactive answer prompt before resuming.")
            run_tui_owned_loop(task_code, task_dir, agent=agent, run_loop=lambda: run_harness_loop_with_safe_auto_resume(task_code, agent=agent))
        else:
            warn("Task is waiting for human input; answer askUserQuestion before resuming.")
            print(render_pending_question_guidance(task_code, task_dir, agent=agent))
        return

    if status in {"created", "planned", "ready", "retry_pending", "generating", "evaluating"}:
        success(f"Resuming from status {status} task: {task_code}")
        if tui:
            run_tui_owned_loop(task_code, task_dir, agent=agent, run_loop=lambda: run_harness_loop_with_safe_auto_resume(task_code, agent=agent))
        else:
            run_harness_loop_with_safe_auto_resume(task_code, agent=agent)
        return

    if status == "replan_pending":
        warn("Task requires replanning; running Phase 2 Refiner before the next loop attempt")
        def replan_then_loop() -> bool:
            run_ai_test_planner(task_dir, agent=agent)
            return run_harness_loop_with_safe_auto_resume(task_code, agent=agent)
        if tui:
            run_tui_owned_loop(task_code, task_dir, agent=agent, run_loop=replan_then_loop)
        else:
            replan_then_loop()
        return

    if status == "failed":
        evaluation = read_evaluation_json(task_dir)
        if evaluation:
            normalized, validation_errors = normalize_evaluation(
                task_dir,
                evaluation,
                int(state.get("iteration", evaluation.get("iteration", 0)) or evaluation.get("iteration", 0) or 0),
            )
            if validation_errors:
                warn("Resume normalized old evaluation policy: " + ", ".join(validation_errors))
            if normalized.get("nextAction") in {"retry_generator", "replan", "ask_user"}:
                write_evaluation_json(task_dir, normalized)
                apply_evaluation_result(task_dir, normalized)
                if normalized.get("nextAction") == "ask_user":
                    warn("Task now requires human input; answer askUserQuestion before resuming.")
                    return
                warn(f"Task failed under an older terminal policy; normalized nextAction={normalized.get('nextAction')} and resuming.")
                if tui:
                    run_tui_owned_loop(task_code, task_dir, agent=agent, run_loop=lambda: run_harness_loop_with_safe_auto_resume(task_code, agent=agent))
                else:
                    run_harness_loop_with_safe_auto_resume(task_code, agent=agent)
                return
        recovery_entry = build_resume_recovery_entry(task_dir, state, evaluation)
        if recovery_entry.get("recoverable"):
            warn(f"Task failed due a recoverable external interruption; stage-resuming: {recovery_entry.get('reason')}")
            if tui:
                run_tui_owned_loop(task_code, task_dir, agent=agent, run_loop=lambda: run_harness_loop_with_safe_auto_resume(task_code, agent=agent))
            else:
                run_harness_loop_with_safe_auto_resume(task_code, agent=agent)
            return
        warn("Task is failed; auto-resume is disabled unless failure category is a recoverable external interruption")
        return

    warn(f"Resume is not supported for current status: {status}")







































def cmd_synthesize_evidence(task_code: str):
    """Synthesize existing summary/result evidence into partial evaluation testResults."""
    task_dir = get_task_dir(task_code)
    if not task_dir.exists():
        error(f"Task does not exist: {task_code}")
        return
    evaluation = synthesize_evaluation_from_evidence(task_dir, reason="manual_command")
    if not evaluation:
        warn("No new evidence summaries found, or evaluation.json is already up to date.")
        return
    apply_evaluation_result(task_dir, evaluation)
    success(
        f"Updated evaluation.json from evidence summaries: iteration={evaluation.get('iteration')} "
        f"testResults={len(evaluation.get('testResults') or [])}"
    )


def _is_git_free_runtime_install(runtime_root: Path) -> bool:
    """Return True when runtime_root is an installer-managed git-free copy."""
    git_marker = runtime_root / ".git"
    if not git_marker.is_file():
        return False
    try:
        return "AutoMind runtime install is intentionally not a Git checkout" in git_marker.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def cmd_update(argv_tail: list[str] | None = None) -> None:
    """Update the installed AutoMind runtime and agent integrations."""
    argv_tail = argv_tail or []
    if any(arg in {"-h", "--help"} for arg in argv_tail):
        print("""
AutoMind update

Usage:
  automind update

What it does:
  - Prefer the bundled install-curl.sh bootstrap.
  - Update the installer git cache from AUTOMIND_REPO/AUTOMIND_BRANCH.
  - Sync the git-free runtime into AUTOMIND_HOME (default: ~/.automind/automind).
  - Refresh the automind CLI wrapper plus agent skill/command integrations.

Useful environment overrides:
  AUTOMIND_BRANCH=<ref>           Update to a branch/tag/ref. Default: main.
  AUTOMIND_HOME=<path>            Runtime install path. Default: ~/.automind/automind.
  AUTOMIND_INSTALL_AGENT=auto     Install skill/command into detected agent roots.
  AUTOMIND_INSTALL_COMMAND=0      Skip slash-command install.
  AUTOMIND_UPDATE=0               Reuse existing installer cache without fetching.
""".strip())
        return
    if argv_tail:
        error("update does not accept positional arguments; use environment variables such as AUTOMIND_BRANCH=<ref> for advanced options")
        sys.exit(1)

    bootstrap = AUTOMIND_ROOT / "install-curl.sh"
    installer = AUTOMIND_ROOT / "install.sh"
    env = os.environ.copy()
    env.setdefault("AUTOMIND_UPDATE", "1")
    if _is_git_free_runtime_install(AUTOMIND_ROOT):
        # Public/user installs are git-free runtime copies. When users run
        # `$AUTOMIND_HOME/automind.sh update` directly instead of the wrapper,
        # AUTOMIND_HOME may not be present in the shell environment. Bind the
        # bootstrap to the current runtime so install-curl.sh updates this
        # install, not the default path. Do not do this in a source checkout:
        # there `.git` is a directory, and update should refresh the installed
        # runtime rather than overwrite the development checkout.
        env.setdefault("AUTOMIND_HOME", str(AUTOMIND_ROOT))

    if bootstrap.exists():
        log(f"Updating AutoMind with bootstrap: {bootstrap}")
        subprocess.run(["bash", str(bootstrap)], check=True, env=env)
        success("AutoMind update complete")
        return

    if installer.exists():
        warn("install-curl.sh not found; refreshing from local install.sh without fetching remote updates")
        subprocess.run(["bash", str(installer)], check=True, env=env, cwd=str(AUTOMIND_ROOT))
        success("AutoMind local refresh complete")
        return

    error(f"No AutoMind installer found under runtime root: {AUTOMIND_ROOT}")
    sys.exit(1)


def cmd_help():
    """Show help."""
    print("""
AutoMind - evidence-driven harness loop for coding agents

Usage: python orchestrator.py <command> [args]

Commands:
  list                      List tasks
  shell                         Open AutoMind interactive command shell
  update                    Update AutoMind runtime, CLI wrapper, skill, and slash command
  ask <requirement> [agent] [--tui|--detached]  Create a task and start an AutoMind-owned CLI/TUI harness loop
                          agent options: auto (default) / codex / claude / trae / trae-cn
  scaffold <requirement>     Create task artifacts for current-session skill/slash-command mode
  context-pack <task-code> [iteration] Create Evaluator context pack without launching an agent
  status <task-code>        Show task status
  trace <task-code> [--json|--write] Show or write formal trace spans
  process-check <task-code> [--json|--soft] Evaluate whether the harness process followed required gates
  message <task-code> --text TEXT [--resume agent] Record natural-language user message for current task/session
  plan <task-code> [agent]  Run AI Phase 2 Refiner without implementation
  summary <task-code> [--ai agent] Generate deterministic summary, optionally AI-refined
  summary-refine <task-code> [agent] Run AI Summary Refiner and regenerate summary
  improve-suggestions [--limit N] Show suggestions from summary run cards
  workflow-check <task-code> Check Phase 2/3 artifact continuity
  workflow-contract <task-code> Materialize/validate workflow.json executable contract
  synthesize-evidence <task-code> Build partial evaluation.testResults from existing summary artifacts
  completion-check <task-code> Check required TestCases/AC coverage before finish
  record-check <task-code>  Check whether task records are reusable
  preloaded-check           Validate preloaded pack metadata and Reuse.md discovery
  reuse [limit]             Show local reuse index
  logs [task-code]          Show logs
  resume <task-code> [agent] [--tui|--detached] Resume a task from persisted runtime-state/evaluation/artifacts
  demo-retry                Run minimal fail -> retry -> pass demo
  smoke <name>              Run smoke test (offline-demo / planner-refiner / context-isolation / delivery-gate / mobile-review-gate / dependency-setup / ui-action-capability / unblock-gate / reuse-playbook / summary-refiner / resume-recovery / policy-guards / android-self-repair / android-probe-flow-self-repair)
  setup-automation-tools [android|ios|visual|all] Install project-local Python helpers for verification
  dependency-check [task-code] [iteration] Read-only web/client/server dependency plan
  android-project-probe <project> [task-code] Read-only probe for a real Android project
  ios-project-probe <project> [task-code] Read-only probe for a real iOS project
  ios-command-probe <workspace> [task-code] Read-only probe for iOS command surface
  android-probe-flow <task-code> [iteration] [--dry-run] Run Android dynamic probe-flow evaluator
  script-command <task-code> [iteration] Run generic script-command evaluator
  version                   Show AutoMind runtime version
  help                      Show help

Examples:
  python orchestrator.py ask "Build a calculator with add/subtract/multiply/divide"  # defaults to auto agent selection
  python orchestrator.py update
  python orchestrator.py scaffold "Fix login crash and verify"
  python orchestrator.py context-pack calculator_1230 1
  python orchestrator.py list
  python orchestrator.py status calculator_1230
  python orchestrator.py plan calculator_1230  # defaults to auto agent selection
  python orchestrator.py workflow-check calculator_1230
  python orchestrator.py workflow-contract calculator_1230
  python orchestrator.py completion-check calculator_1230
  python orchestrator.py summary calculator_1230
  python orchestrator.py summary-refine calculator_1230 codex
  python orchestrator.py logs calculator_1230
  python orchestrator.py smoke planner-refiner
  python orchestrator.py smoke reuse-playbook
  python orchestrator.py smoke summary-refiner
  python orchestrator.py smoke resume-recovery
  python orchestrator.py smoke android-self-repair
  python orchestrator.py setup-automation-tools visual --dry-run
  python orchestrator.py dependency-check
  python orchestrator.py android-project-probe /path/to/android-project
  python orchestrator.py ios-project-probe /path/to/ios-project
  python orchestrator.py ios-command-probe /path/to/ios-workspace
  python orchestrator.py android-probe-flow android_probe_flow_demo --dry-run
""")


# ============================================================
# \u4e3b\u5165\u53e3
# ============================================================



def parse_ask_args(argv_tail: list[str]) -> tuple[str, str, bool, bool]:
    """Parse `ask` free text plus optional agent/flags.

    Supports both quoted and unquoted natural language. If the final non-flag
    token is a supported agent name, it is treated as the agent; otherwise all
    non-flag tokens are joined into the user request.
    """
    force_tui = "--tui" in argv_tail
    force_detached = "--detached" in argv_tail or "--no-tui" in argv_tail
    clean = [arg for arg in argv_tail if arg not in {"--tui", "--detached", "--no-tui"}]
    if not clean:
        return "", "auto", force_tui, force_detached
    agent = "auto"
    supported_agents = set(AGENT_ADAPTERS.keys()) | {"auto"}
    if len(clean) > 1 and clean[-1].strip().lower() in supported_agents:
        agent = clean[-1].strip().lower()
        clean = clean[:-1]
    return " ".join(clean).strip(), agent, force_tui, force_detached

def main():
    if len(sys.argv) < 2:
        cmd_help()
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "shell":
        sys.exit(run_command_shell())

    if cmd == "version":
        print(automind_version_label())
        return

    if cmd == "update":
        cmd_update(sys.argv[2:])
        return

    if cmd == "list":
        cmd_list()

    elif cmd == "ask":
        if len(sys.argv) < 3:
            error("Please provide a requirement description")
            print("Example: python orchestrator.py ask \"Build a calculator\"")
            sys.exit(1)
        user_input, agent, force_tui, force_detached = parse_ask_args(sys.argv[2:])
        if not user_input:
            error("Please provide a requirement description")
            print('Example: python orchestrator.py ask "Build a calculator"')
            sys.exit(1)
        tui = force_tui or (not force_detached and sys.stdin.isatty() and sys.stdout.isatty())
        cmd_ask(user_input, agent, tui=tui)

    elif cmd == "scaffold":
        if len(sys.argv) < 3:
            error("Please provide a requirement description")
            print("Example: python orchestrator.py scaffold \"Build a calculator\"")
            sys.exit(1)
        user_input = sys.argv[2]
        cmd_scaffold(user_input)

    elif cmd == "context-pack":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            print("Example: python orchestrator.py context-pack calculator_1230 1")
            sys.exit(1)
        iteration = int(sys.argv[3]) if len(sys.argv) > 3 else None
        cmd_context_pack(sys.argv[2], iteration)

    elif cmd == "status":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            sys.exit(1)
        cmd_status(sys.argv[2])

    elif cmd == "plan":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            sys.exit(1)
        task_code = sys.argv[2]
        agent = sys.argv[3] if len(sys.argv) > 3 else "auto"
        cmd_plan(task_code, agent)

    elif cmd == "logs":
        task_code = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_logs(task_code)

    elif cmd == "report":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            print("Example: python orchestrator.py report <task-code>")
            sys.exit(1)
        cmd_report(sys.argv[2])

    elif cmd == "summary":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            sys.exit(1)
        ai_agent = None
        if "--ai" in sys.argv[3:]:
            idx = sys.argv.index("--ai")
            ai_agent = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else "auto"
        cmd_summary(sys.argv[2], ai_agent=ai_agent)

    elif cmd == "summary-refine":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            sys.exit(1)
        agent = sys.argv[3] if len(sys.argv) > 3 else "auto"
        warn(
            "`summary-refine` is deprecated; forwarding to `summary --ai <agent>`. "
            "Will be removed in a future release."
        )
        cmd_summary(sys.argv[2], ai_agent=agent)

    elif cmd == "improve-suggestions":
        cmd_improve_suggestions(sys.argv[2:])

    elif cmd == "workflow-check":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            sys.exit(1)
        cmd_workflow_check(sys.argv[2])

    elif cmd == "workflow-contract":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            sys.exit(1)
        cmd_workflow_contract(sys.argv[2])

    elif cmd == "synthesize-evidence":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            sys.exit(1)
        cmd_synthesize_evidence(sys.argv[2])

    elif cmd == "completion-check":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            sys.exit(1)
        cmd_completion_check(sys.argv[2])

    elif cmd == "continue":
        argv_tail = sys.argv[2:]
        task_code = argv_tail[0] if argv_tail else None
        cmd_continue(task_code)
    elif cmd == "phase-gate":
        argv_tail = sys.argv[2:]
        if len(argv_tail) < 1 or argv_tail[0] in {"-h", "--help"}:
            print("Usage: phase-gate <task-code> [auto|plan|build|verify|finish] [--soft]")
            return
        cmd_phase_gate(argv_tail[0], argv_tail[1:])
    elif cmd == "answer":
        argv_tail = sys.argv[2:]
        if len(argv_tail) < 1 or argv_tail[0] in {"-h", "--help"}:
            print_answer_usage()
            return
        cmd_answer(argv_tail[0], argv_tail[1:])
    elif cmd == "message":
        argv_tail = sys.argv[2:]
        if len(argv_tail) < 1:
            error("Usage: message <task-code> --text TEXT [--resume agent]")
            sys.exit(1)
        cmd_message(argv_tail[0], argv_tail[1:], resume_callback=cmd_resume)
    elif cmd == "event":
        argv_tail = sys.argv[2:]
        if len(argv_tail) < 1:
            error("Usage: event <task-code> [--type TYPE] [--message TEXT] [--phase PHASE] [--replace-key KEY]")
            sys.exit(1)
        cmd_event(argv_tail[0], argv_tail[1:])
    elif cmd == "trace":
        argv_tail = sys.argv[2:]
        if len(argv_tail) < 1:
            error("Usage: trace <task-code> [--json|--write]")
            sys.exit(1)
        cmd_trace(argv_tail[0], argv_tail[1:])

    elif cmd == "process-check":
        argv_tail = sys.argv[2:]
        if len(argv_tail) < 1:
            error("Usage: process-check <task-code> [--json|--soft|--no-write]")
            sys.exit(1)
        cmd_process_check(argv_tail[0], argv_tail[1:])

    elif cmd == "tui":
        argv_tail = sys.argv[2:]
        if len(argv_tail) < 1:
            error("Usage: tui <task-code> [--watch|--interactive]")
            sys.exit(1)
        cmd_tui(argv_tail[0], argv_tail[1:])

    elif cmd == "tick-iteration":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            print("Example: ./automind.sh tick-iteration calculator_1230 [phase]")
            sys.exit(1)
        phase = sys.argv[3] if len(sys.argv) > 3 else "generic"
        cmd_tick_iteration(sys.argv[2], phase)

    elif cmd == "preloaded-check":
        cmd_preloaded_check()

    elif cmd == "record-check":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            sys.exit(1)
        cmd_record_check(sys.argv[2])

    elif cmd == "notifications":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            sys.exit(1)
        notif_limit = None
        if "--limit" in sys.argv[3:]:
            idx = sys.argv.index("--limit")
            try:
                notif_limit = int(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 else None
            except ValueError:
                error("--limit requires an integer value")
                sys.exit(1)
        cmd_notifications(sys.argv[2], notif_limit)

    elif cmd == "doctor":
        # Two modes:
        #   1) doctor <task-code> [--stale-seconds N]  -> per-task JSON report
        #   2) doctor [--auto-resume] [--dry-run] [--stale-seconds N] [--agent A]
        #      -> scan all tasks and prompt y/n/all/skip for stalled ones
        argv_tail = sys.argv[2:]
        stale_seconds = 600
        if "--stale-seconds" in argv_tail:
            idx = argv_tail.index("--stale-seconds")
            try:
                stale_seconds = int(argv_tail[idx + 1]) if len(argv_tail) > idx + 1 else 600
            except ValueError:
                error("--stale-seconds requires an integer value")
                sys.exit(1)
        auto_resume = "--auto-resume" in argv_tail
        dry_run = "--dry-run" in argv_tail
        agent = "auto"
        if "--agent" in argv_tail:
            idx = argv_tail.index("--agent")
            if len(argv_tail) > idx + 1:
                agent = argv_tail[idx + 1]
        # Treat first non-flag arg as task code (back-compat).
        positional = [a for a in argv_tail if not a.startswith("--")]
        # Filter out values consumed by --stale-seconds / --agent.
        consumed: set[str] = set()
        for flag in ("--stale-seconds", "--agent"):
            if flag in argv_tail:
                idx = argv_tail.index(flag)
                if len(argv_tail) > idx + 1:
                    consumed.add(argv_tail[idx + 1])
        positional = [a for a in positional if a not in consumed]
        if positional:
            cmd_doctor(positional[0], stale_seconds)
        else:
            cmd_doctor_scan(stale_seconds=stale_seconds, auto_resume=auto_resume, dry_run=dry_run, agent=agent)

    elif cmd == "reuse":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 80
        cmd_reuse(limit)

    elif cmd == "reuse-ack":
        cmd_reuse_ack(sys.argv[2:])


    elif cmd == "knowledge":
        cmd_knowledge(sys.argv[2:])

    elif cmd == "summary-compact":
        keep_recent = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        max_chars = int(sys.argv[3]) if len(sys.argv) > 3 else 200_000
        cmd_summary_compact(keep_recent=keep_recent, max_chars=max_chars)

    elif cmd == "resume":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            sys.exit(1)
        argv_tail = sys.argv[2:]
        force_tui = "--tui" in argv_tail
        force_detached = "--detached" in argv_tail or "--no-tui" in argv_tail
        clean = [arg for arg in argv_tail if arg not in {"--tui", "--detached", "--no-tui"}]
        task_code = clean[0]
        agent = clean[1] if len(clean) > 1 else "auto"
        tui = force_tui or (not force_detached and sys.stdin.isatty() and sys.stdout.isatty())
        cmd_resume(task_code, agent, tui=tui)

    elif cmd == "demo-retry":
        cmd_demo_retry()

    elif cmd == "smoke":
        if len(sys.argv) < 3:
            error("Please provide a smoke test name")
            print("Example: python orchestrator.py smoke android-self-repair")
            sys.exit(1)
        cmd_smoke(sys.argv[2])

    elif cmd == "setup-automation-tools":
        if any(arg in {"-h", "--help"} for arg in sys.argv[2:]):
            print("Usage: python orchestrator.py setup-automation-tools [android|ios|visual|all] [--dry-run]")
            print("Creates/repairs project-local Python helper venvs. --help is help-only and does not install or modify anything.")
            return
        target = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "all"
        dry_run = "--dry-run" in sys.argv[2:]
        cmd_setup_automation_tools(target, dry_run)

    elif cmd == "dependency-check":
        task_code = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else ""
        iteration = None
        if len(sys.argv) > 3 and not sys.argv[3].startswith("--"):
            try:
                iteration = int(sys.argv[3])
            except ValueError:
                error("iteration must be a number")
                sys.exit(1)
        cmd_project_dependency_check(task_code, iteration)

    elif cmd == "android-project-probe":
        if len(sys.argv) < 3:
            error("Please provide an Android project path")
            print("Example: python orchestrator.py android-project-probe /path/to/android-project")
            sys.exit(1)
        task_code = sys.argv[3] if len(sys.argv) > 3 else "android_project_probe"
        cmd_android_project_probe(sys.argv[2], task_code)

    elif cmd == "ios-project-probe":
        if len(sys.argv) < 3:
            error("Please provide an iOS project path")
            print("Example: python orchestrator.py ios-project-probe /path/to/ios-project")
            sys.exit(1)
        task_code = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "ios_project_probe"
        extra_start = 4 if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else 3
        cmd_ios_project_probe(sys.argv[2], task_code, sys.argv[extra_start:])

    elif cmd == "ios-command-probe":
        if len(sys.argv) < 3:
            error("Please provide an iOS workspace path")
            print("Example: python orchestrator.py ios-command-probe /path/to/ios-workspace")
            sys.exit(1)
        task_code = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "ios_command_probe"
        extra_start = 4 if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else 3
        cmd_ios_command_probe(sys.argv[2], task_code, sys.argv[extra_start:])

    elif cmd == "android-probe-flow":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            print("Example: python orchestrator.py android-probe-flow android_probe_flow_demo --dry-run")
            sys.exit(1)
        task_code = sys.argv[2]
        dry_run = "--dry-run" in sys.argv[3:]
        retries = 0
        if "--retries" in sys.argv[3:]:
            idx = sys.argv.index("--retries")
            if len(sys.argv) > idx + 1:
                retries = int(sys.argv[idx + 1])
        iteration = None
        skip_next = False
        for arg in sys.argv[3:]:
            if skip_next:
                skip_next = False
                continue
            if arg in {"--dry-run", "--retries"}:
                skip_next = arg == "--retries"
                continue
            iteration = int(arg)
            break
        cmd_android_probe_flow(task_code, iteration, dry_run, retries=retries)

    elif cmd == "script-command":
        if len(sys.argv) < 3:
            error("Please provide a task code")
            print("Example: python orchestrator.py script-command generic_script_demo 1")
            sys.exit(1)
        task_code = sys.argv[2]
        iteration = int(sys.argv[3]) if len(sys.argv) > 3 else None
        task_dir = get_task_dir(task_code)
        if not task_dir.exists():
            error(f"Task does not exist: {task_code}")
            sys.exit(1)
        if iteration is None:
            state = read_runtime_state(task_dir) or {}
            iteration = int(state.get("iteration", 0) or 0) + 1
        iter_log_dir = task_dir / "logs" / f"iter-{iteration}"
        ensure_dir(iter_log_dir)
        code, output = run_script_command_evaluator(task_dir, iteration, iter_log_dir)
        evaluation = read_evaluation_json(task_dir) or {}
        apply_evaluation_result(task_dir, evaluation)
        print(json.dumps({
            "task": task_code,
            "iteration": iteration,
            "result": evaluation.get("result"),
            "nextAction": evaluation.get("nextAction"),
            "evaluation": str(task_dir / "evaluation.json"),
            "logDir": str(iter_log_dir),
            "outputTail": output[-2000:],
        }, ensure_ascii=False, indent=2))

    elif cmd == "script-command-parallel":
        error("script-command-parallel has been removed; use script-command instead")
        sys.exit(2)

    else:
        cmd_help()


if __name__ == "__main__":
    main()
