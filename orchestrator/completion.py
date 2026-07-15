"""Completion gate and verification-unblock validation for CodeMind.

This module owns the final acceptance ledger logic: declared TestCases, AC
coverage, evidence existence, and temporary verification unblock rules.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from orchestrator.artifacts import (
    evidence_path_exists,
    extract_artifact_ids,
    extract_declared_testcases,
    normalize_artifact_id,
    read_requirements_contract_text,
    normalize_evidence_refs,
    normalize_test_result_value,
    test_result_is_acceptable,
)
from orchestrator.console import warn
from orchestrator.state import read_evaluation_json, read_runtime_state, rel_to_root, update_runtime_state
from orchestrator.workflow_state import emit_workflow_event
from orchestrator.tc_attempts import read_tc_attempts
from orchestrator.workflow_contract import ensure_workflow_contract, RUNTIME_LEVELS, _normalize_runtime_level


# Module-level guard so each deprecation warning is emitted at most once per
# process. Avoids spamming the console when many records are normalized.
_DEPRECATION_WARNED: set[str] = set()


SCREENSHOT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _evidence_path_exists_for_screenshot(task_dir: Path, value: object) -> bool:
    if not value:
        return False
    path = task_dir / str(value)
    return path.exists() and path.is_file()


def _is_screenshot_path(value: object) -> bool:
    if not value:
        return False
    return Path(str(value)).suffix.lower() in SCREENSHOT_SUFFIXES


def _extract_screenshot_refs_from_result(item: dict) -> list[str]:
    refs: list[str] = []

    def add(value: object) -> None:
        if isinstance(value, str) and _is_screenshot_path(value):
            refs.append(value)
        elif isinstance(value, dict):
            for key in ["path", "image", "screenshot", "screenshotAfter", "beforeScreenshot", "afterScreenshot", "evidence", "artifact"]:
                add(value.get(key))
        elif isinstance(value, list):
            for entry in value:
                add(entry)

    add(item.get("evidence"))
    assessment = item.get("evidenceAssessment") if isinstance(item.get("evidenceAssessment"), dict) else {}
    add(assessment.get("machineAnchor"))
    add(assessment.get("artifacts"))
    for metric in assessment.get("hardMetrics") or []:
        if isinstance(metric, dict):
            add(metric.get("evidence") or metric.get("evidencePath") or metric.get("artifact"))
    ui = item.get("uiExploration") if isinstance(item.get("uiExploration"), dict) else {}
    add(ui)
    return refs


def _has_existing_screenshot_for_result(task_dir: Path, item: dict) -> bool:
    return any(_evidence_path_exists_for_screenshot(task_dir, ref) for ref in _extract_screenshot_refs_from_result(item))


def _result_mentions_no_screenshot_reason(item: dict) -> bool:
    text_parts = []
    for key in ["reason", "summary", "retryAdvice", "screenshotNote", "noScreenshotReason"]:
        value = item.get(key)
        if value:
            text_parts.append(str(value))
    assessment = item.get("evidenceAssessment") if isinstance(item.get("evidenceAssessment"), dict) else {}
    for key in ["reason", "noScreenshotReason", "screenshotNote"]:
        value = assessment.get(key)
        if value:
            text_parts.append(str(value))
    text = "\n".join(text_parts).lower()
    return any(token in text for token in ["no screenshot", "screenshot cannot", "screenshot unavailable", "xcresult", "截图不可用", "无法截图", "没有截图"])


def _warn_once(key: str, message: str) -> None:
    if key in _DEPRECATION_WARNED:
        return
    _DEPRECATION_WARNED.add(key)
    warn(message)


BLOCKER_CATEGORIES = {
    "environment_blocked",
    "mobile_device_unavailable",
    "permission_blocked",
    "tool_missing",
    "tool_limitation",
    "signing_material_blocked",
    "provisioning_profile_blocked",
    "external_runner_capability_blocked",
    "external_runner_signing_blocked",
    "agent_unavailable",
    "agent_timeout",
}


# A1-3 Generator/Evaluator boundary contract.
#
# Evaluator owns verification, evidence, classification. It may only modify
# verifier / probe-flow / test-harness / evidence files. Any product/runtime
# code repair must be routed to the Generator via nextAction=retry_generator.
#
# Files matching these prefixes/suffixes are considered Evaluator-allowed.
# Anything else declared in evaluatorChanges[] is treated as a boundary
# violation by validate_evaluator_boundary.
_EVALUATOR_ALLOWED_PATH_PREFIXES = (
    ".automind/tasks/",
    "logs/",
    "tests/",
    "test/",
    "__tests__/",
    "spec/",
    "specs/",
    "fixtures/",
    "verifier/",
    "verifiers/",
    "probe-flow/",
    "test-harness/",
    "harness/",
)

_EVALUATOR_ALLOWED_PATH_SUFFIXES = (
    "/Validation.md",
    "/evaluation.json",
    "/Delivery.md",
    "/VerificationLedger.json",
    "/probe-flow.android.json",
    "/probe-flow.ios.json",
    "/action-plan.android.json",
    "/action-plan.ios.json",
)

_EVALUATOR_ALLOWED_PATH_SUBSTRINGS = (
    "/probe-flow.",
    "/action-plan.",
    "/.automind/tasks/",
    "/test_",
    "/_test.",
    ".test.",
    ".spec.",
    "/fixtures/",
    "/visual-baselines/",
)

# Categories that are inherently Evaluator-allowed regardless of path. Any
# extra category must still satisfy the path heuristic.
_EVALUATOR_ALLOWED_CATEGORIES = {
    "verifier_self_repair",
    "probe_flow_repair",
    "test_harness_fix",
    "evidence_only",
}


def normalize_evaluator_changes(evaluation: dict) -> list[dict]:
    """Return Evaluator-declared file changes for boundary validation."""
    raw = evaluation.get("evaluatorChanges")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return [{"id": "ECG-invalid", "category": "product_code", "files": [], "reason": "evaluatorChanges is not an array"}]
    out: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
        else:
            out.append({"id": "ECG-invalid", "category": "product_code", "files": [], "reason": "evaluatorChanges item is not an object"})
    return out


def _path_is_evaluator_allowed(path: str) -> bool:
    """Heuristic check: is the path verifier/probe-flow/test-harness/evidence?"""
    if not path:
        return False
    norm = path.replace("\\", "/").lstrip("./").lstrip("/")
    norm_with_prefix = "/" + norm
    for prefix in _EVALUATOR_ALLOWED_PATH_PREFIXES:
        if norm.startswith(prefix):
            return True
    for suffix in _EVALUATOR_ALLOWED_PATH_SUFFIXES:
        if norm_with_prefix.endswith(suffix):
            return True
    for substring in _EVALUATOR_ALLOWED_PATH_SUBSTRINGS:
        if substring in norm_with_prefix:
            return True
    return False


def validate_evaluator_boundary(evaluation: dict) -> tuple[list[str], list[str], list[dict]]:
    """Validate that Evaluator did not cross into product/runtime-code repair.

    Returns (issues, warnings, violations). Violations is a list of dicts
    suitable for completion-gate routing: each dict carries the offending
    change id, category, and offending file paths.

    The contract is: Evaluator-declared changes must use one of the allowed
    categories AND every file path must look like a verifier/probe-flow/
    test-harness/evidence asset. Anything else is a boundary violation that
    the completion gate must turn into nextAction=retry_generator so the
    Generator owns product code repair.
    """
    issues: list[str] = []
    warnings: list[str] = []
    violations: list[dict] = []
    changes = normalize_evaluator_changes(evaluation)
    for idx, item in enumerate(changes):
        prefix = f"evaluatorChanges[{idx}]"
        category = str(item.get("category", "")).strip().lower()
        files = item.get("files") if isinstance(item.get("files"), list) else []
        files = [str(f) for f in files if str(f).strip()]
        if not category:
            issues.append(f"{prefix} missing category")
            violations.append({"id": item.get("id"), "category": "missing", "files": files})
            continue
        if category == "product_code":
            issues.append(
                f"{prefix} declares product_code change; Evaluator must route to retry_generator and not repair product code"
            )
            violations.append({"id": item.get("id"), "category": category, "files": files})
            continue
        if category not in _EVALUATOR_ALLOWED_CATEGORIES:
            issues.append(f"{prefix} category={category} is not allowed for Evaluator self-repair")
            violations.append({"id": item.get("id"), "category": category, "files": files})
            continue
        if not files:
            issues.append(f"{prefix} missing files list")
            continue
        offending = [path for path in files if not _path_is_evaluator_allowed(path)]
        if offending:
            issues.append(
                f"{prefix} category={category} touched non-evaluator files: " + ", ".join(offending[:5])
            )
            violations.append({"id": item.get("id"), "category": category, "files": offending})
        if not item.get("reason"):
            warnings.append(f"{prefix} missing reason")
    return issues, warnings, violations


_ASK_USER_ALLOWED_CATEGORIES = {
    "unauthorized_destructive_or_sensitive",
    "system_or_external_dependency",
    "real_device_or_signing",
    "manual_visual_confirmation",
    "repeated_same_failure",
}

# Long-running work (full builds, compiles, long test/install runs) is NEVER a
# valid reason to interrupt the autonomous loop with ask_user. CodeMind's design
# goal is maximum automation; any duration/scope authorization must be settled
# once during pre-implementation, not re-asked before each expensive step. These
# markers detect an ask_user whose intent is merely "this will take a while /
# please authorize me to start the long build", so the gate can reject it even
# when it is mislabeled with an otherwise-whitelisted category.
_LONG_RUNNING_ASK_MARKERS = (
    "long compile",
    "long build",
    "long-running",
    "long running",
    "take a long time",
    "takes a long time",
    "time-consuming",
    "time consuming",
    "full compile",
    "full build",
    "full rebuild",
    "clean build will take",
    "start the build",
    "start compile",
    "start compiling",
    "start the compile",
    "start_compile",
    "begin the build",
    "authorize the build",
    "authorize compile",
    "authorize compilation",
    "permission to build",
    "permission to compile",
    "proceed with the long",
    "耗时",
    "长时间编译",
    "开始编译",
    "是否开始编译",
    "是否继续编译",
)


def _ask_user_is_long_running_authorization(question: dict) -> str | None:
    """Return a reason string when the ask_user is really a long-running /
    compile-duration authorization, else None.

    Such a question must not pause the loop: the long build should just run.
    """
    blob = _ask_user_blob(question)
    if not blob:
        return None
    for marker in _LONG_RUNNING_ASK_MARKERS:
        if marker in blob:
            return marker
    return None


# Making a minimal, reversible verification-unblock edit to code/scripts/build
# config/generated wrappers (then re-running the verification once) is CodeMind's
# OWN job, not a human decision. AGENTS.md already authorizes "minimal reversible
# verification unblock changes ... after checkpointing or recording a diff" and
# tracks them in `verificationUnblockChanges`. An ask_user whose intent is merely
# "may I make a temporary patch / tweak the script / wrapper / build config and
# retry?" is therefore a non-whitelisted soft pause: CodeMind should just attempt
# it (record + restore/promote) instead of interrupting the autonomous loop.
#
# This also covers runtime-proof verification unblock work: when runtime proof is
# blocked by runner / destination / test-target environment mismatch and a local,
# reversible, auditable compatible/external runner path exists, Generator must try
# that path and record a VUC instead of asking the user. ask_user is reserved for
# runtime downgrade, user-provided device/environment, sudo/tunneld,
# uninstall/delete/reset, external upload, account login, payment, or other
# irreversible/high-impact operations.
#
# These markers detect that intent generically (not tied to any one script) so
# the gate can rewrite it back to retry_generator. Genuinely sensitive actions
# (delete/reset, login, external upload, payment, privilege escalation) carry
# their own destructive/sensitive markers and are NOT matched here, so they
# still pause the loop. Signing/certificate/keychain-for-signing is intentionally
# NOT treated as sensitive: CodeMind is allowed to re-sign with the user's own
# certificates / automatic signing as part of unblocking verification.
_TEMP_UNBLOCK_ASK_MARKERS = (
    "temporary patch",
    "temporary fix",
    "temporary unblock",
    "temporary wrapper",
    "temporary code change",
    "temporarily patch",
    "temporarily modify",
    "temporarily change",
    "patch the wrapper",
    "patch the generated",
    "patch the script",
    "patch the build",
    "patch and rerun",
    "patch and retry",
    "modify the script",
    "modify the wrapper",
    "modify the build script",
    "modify the generated",
    "edit the script",
    "tweak the script",
    "verification unblock",
    "unblock the build",
    "unblock verification",
    "runner unblock",
    "compatible runner",
    "external runner",
    "verification runner",
    "test target",
    "test-target",
    "deployment target",
    "destination mismatch",
    "runner/destination",
    "runner / destination",
    "runtime proof strategy",
    "runtime proof is blocked",
    "runtime proof blocked",
    "authorize a temporary",
    "authorize the patch",
    "permission to patch",
    "临时补丁",
    "临时修改",
    "临时改",
    "临时脚本",
    "临时代码",
    "临时验证",
    "临时 unblock",
    "改脚本",
    "改 wrapper",
    "修改脚本",
    "修改 wrapper",
    "修改生成的",
    "补丁并重跑",
    "补丁并重试",
    "补丁并只重跑",
    "wrapper 补丁",
    "unblock 补丁",
    "验证 unblock",
    "解除构建阻塞",
    "解除验证阻塞",
    "兼容 runner",
    "外部 runner",
    "验证 runner",
    "授权兼容 runner",
    "运行时证明策略",
    "runtime 证明策略",
    "runtime proof",
)

# Hard sensitive/destructive signals. Even when temporary-unblock wording is
# present, if any of these appear the ask_user is a genuine sensitive action and
# must still pause the loop (it is NOT a self-serviceable verification unblock).
_TEMP_UNBLOCK_SENSITIVE_GUARDS = (
    "delete",
    "remove",
    "reset",
    "uninstall",
    "wipe",
    "overwrite install",
    "login",
    "log in",
    "sign in",
    "account",
    "password",
    "credential",
    "secret",
    "token",
    "upload",
    "publish",
    "payment",
    "purchase",
    "sudo",
    "tunneld",
    "privilege",
    "删除",
    "卸载",
    "重置",
    "覆盖安装",
    "登录",
    "账号",
    "密码",
    "密钥",
    "凭证",
    "上传",
    "发布",
    "支付",
)


def _ask_user_blob(question: dict) -> str:
    """Return searchable text for ask_user classification.

    Include structured options because planner/pre-implementation asks often put
    the actionable soft-pause signal (for example approve_compatible_runner) in
    the option id/label/impact while the top-level question stays generic.
    """
    parts = [
        str(question.get(key) or "")
        for key in ("question", "reason", "intent", "summary")
    ]
    options = question.get("options")
    if isinstance(options, list):
        for opt in options:
            if isinstance(opt, dict):
                parts.extend(str(opt.get(key) or "") for key in ("id", "label", "impact", "description"))
            else:
                parts.append(str(opt))
    return " ".join(parts).lower()


def _ask_user_is_temporary_unblock_authorization(question: dict) -> str | None:
    """Return a marker when the ask_user is merely requesting authorization to
    make a temporary verification-unblock code/script/build/wrapper patch and
    retry, else None.

    Such a question must not pause the loop: CodeMind should just make the
    minimal reversible change (recording it in verificationUnblockChanges and
    restoring/promoting before finish). Genuinely destructive/sensitive intents
    are detected via the sensitive guards and are not suppressed here.
    """
    blob = _ask_user_blob(question)
    if not blob:
        return None
    if any(guard in blob for guard in _TEMP_UNBLOCK_SENSITIVE_GUARDS):
        return None
    for marker in _TEMP_UNBLOCK_ASK_MARKERS:
        if marker in blob:
            return marker
    return None


# Operating a connected, authorized device for verification (play, skip/next,
# trigger error, interrupt/pause, navigate, capture logcat/log) is CodeMind's own
# job via probe-flow/XCUITest. A `real_device_or_signing` ask that merely says "I
# cannot operate your physical device, please confirm the verification approach"
# is a non-whitelisted soft pause: it delegates CodeMind's own UI-driving work
# back to the human even though the device is reachable. These markers detect that
# delegation so the gate can rewrite it back to retry_generator.
_DEVICE_OPERATION_DELEGATION_MARKERS = (
    "cannot operate your",
    "cannot operate the",
    "can not operate your",
    "can't operate your",
    "unable to operate",
    "cannot control your device",
    "cannot drive your device",
    "please confirm the verification approach",
    "please confirm how to verify",
    "confirm the verification execution",
    "please perform the",
    "please manually perform",
    "operate the phone for me",
    "operate the device for me",
    "无法操控",
    "无法操作真机",
    "无法操作你的真机",
    "无法操作您的真机",
    "请确认验证执行方式",
    "请确认验证方式",
    "请你在手机上",
    "请在手机上操作",
    "请帮我在手机上",
)

# Genuine human/system device gates. When any of these is present the ask_user is
# a real physical/permission gate (no device, locked, trust/Developer-Mode/USB
# prompt, signing/provisioning, UI-Automation permission) and must NOT be treated
# as delegation, even if delegation-style wording also appears.
_REAL_DEVICE_HARD_GATE_MARKERS = (
    "no device",
    "not connected",
    "no real device",
    "device unavailable",
    "state=device",
    "not in state=device",
    "unlock",
    "locked",
    "developer mode",
    "usb debugging",
    "trust",
    "untrusted",
    "signing",
    "provisioning",
    "code sign",
    "certificate",
    "keychain",
    "ui automation permission",
    "automation permission",
    "permission denied",
    "未连接",
    "没有设备",
    "未授权",
    "解锁",
    "开发者模式",
    "调试授权",
    "信任",
    "签名",
    "描述文件",
)


def _ask_user_delegates_device_operation(question: dict) -> str | None:
    """Return a marker when a `real_device_or_signing` ask is really delegating
    CodeMind's own device-driving work back to the human, else None.

    Returns None when a genuine hard-gate signal is present, so real device/
    signing/permission gates still pause the loop.
    """
    blob = _ask_user_blob(question)
    if not blob:
        return None
    if any(gate in blob for gate in _REAL_DEVICE_HARD_GATE_MARKERS):
        return None
    for marker in _DEVICE_OPERATION_DELEGATION_MARKERS:
        if marker in blob:
            return marker
    return None


# Model-trust self-assessment (asymmetric design).
#
# CodeMind biases hard toward automation: for non-enterprise users, over-cautious
# pauses cost far more than the rare false self-service. So the Evaluator's own
# structured self-assessment is the PRIMARY judge of whether an ask_user is a
# self-serviceable soft pause or a genuine hard gate:
#
#   askUserQuestion.riskTier:
#     - "safe_self_service": CodeMind can resolve it itself (long build, minimal
#       reversible verification-unblock patch, in-app device driving, retryable
#       env tweak). The gate trusts the model and rewrites the ask_user back to
#       retry_generator -- UNLESS the text contains a strong irreversible/account
#       -security signal that contradicts the self-service claim (anti
#       self-contradiction safety net only).
#     - "sensitive_hard_gate": a genuine human decision (destructive/irreversible,
#       account/keychain/signing, payment, real device/permission gate). The gate
#       trusts the model and lets the loop pause.
#   askUserQuestion.reversible: optional bool reinforcing the self-assessment.
#
# When riskTier is ABSENT (older Evaluator output), the gate falls back to the
# deterministic keyword vetos below so behavior stays safe and unchanged.
_RISK_TIER_SAFE = "safe_self_service"
_RISK_TIER_SENSITIVE = "sensitive_hard_gate"
_RISK_TIER_VALUES = {_RISK_TIER_SAFE, _RISK_TIER_SENSITIVE}

# Strong irreversible / account-security signals. These are the ONLY signals that
# can override a model's `safe_self_service` self-label (the model said "safe" but
# the text screams a hard, non-reversible or account-security action -- a self
# contradiction we must not auto-trust). Deliberately narrow per product owner:
# `production` is NOT here (treating "production" as a blanket safety keyword
# over-pauses automation), and signing/certificate/keychain-for-signing is NOT
# here either (CodeMind may re-sign with the user's own certificates / automatic
# signing); only genuinely irreversible/account-security verbs are.
_STRONG_IRREVERSIBLE_SIGNALS = (
    "delete",
    "uninstall",
    "wipe",
    "reset",
    "force-push",
    "force push",
    "drop database",
    "drop table",
    "rm -rf",
    "factory reset",
    "credential",
    "password",
    "secret key",
    "private key",
    "rotate key",
    "login",
    "sign in",
    "payment",
    "purchase",
    "charge",
    "transfer funds",
    "删除",
    "卸载",
    "抹除",
    "重置",
    "强制推送",
    "密钥",
    "私钥",
    "凭证",
    "密码",
    "登录",
    "支付",
    "付款",
)


def _ask_user_risk_tier(question: dict) -> str | None:
    """Return the normalized model-declared risk tier, or None when absent/invalid."""
    raw = str(question.get("riskTier") or "").strip().lower()
    if raw in _RISK_TIER_VALUES:
        return raw
    return None


def read_user_autonomy_intent(task_dir: Path | None) -> dict:
    """Read the user's stated autonomy intent from runtime-state.

    "拦不拦截以用户诉求为准": whether to pause is ultimately the user's call, not
    a property baked into our risk model. We surface two ground-truth signals
    from the pre-implementation decision the user already made:

    - ``fullAuto``: the user explicitly requested no-confirmation/full-auto mode
      (一站到底/全自动/不用确认/full-auto). This authoritatively overrides a
      model-declared ``sensitive_hard_gate`` so the loop is not interrupted.
    - ``destructiveActionsAllowList``: concrete sensitive actions the user has
      pre-authorized during pre-implementation. A model that asserts (via
      ``askUserQuestion.userAuthorized=true``) the pending action is already on
      this list is trusted to proceed.

    Returns a dict with ``fullAuto: bool`` and ``preAuthorizedActions: list``.
    Deliberately generic: we do NOT keyword-match the request here; the model's
    own ``riskTier`` / ``userAuthorized`` self-assessment is the primary judge,
    and these signals only widen what the user already chose to allow.
    """
    intent = {"fullAuto": False, "preAuthorizedActions": []}
    if task_dir is None:
        return intent
    try:
        state = read_runtime_state(task_dir) or {}
    except Exception:
        return intent
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
    if review.get("fullAuto") is True:
        intent["fullAuto"] = True
    bundle = review.get("decisionBundle") if isinstance(review.get("decisionBundle"), dict) else {}
    allow_list = bundle.get("destructiveActionsAllowList")
    if isinstance(allow_list, list):
        intent["preAuthorizedActions"] = [str(item) for item in allow_list if str(item).strip()]
    return intent


def _ask_user_is_user_preauthorized(question: dict, intent: dict) -> bool:
    """Trust the model's claim that the pending sensitive action is pre-authorized.

    The Evaluator can set ``askUserQuestion.userAuthorized=true`` when, per the
    pre-implementation ``decisionBundle.destructiveActionsAllowList``, the action
    it would otherwise pause for is already authorized by the user. We require a
    non-empty allow list as corroboration so a model cannot fabricate consent out
    of thin air. We do NOT keyword-match the action text -- the model owns the
    semantic match against what the user authorized.
    """
    if question.get("userAuthorized") is not True:
        return False
    return bool(intent.get("preAuthorizedActions"))


def _ask_user_has_strong_irreversible_signal(question: dict) -> str | None:
    """Return a matched strong irreversible/account-security signal, else None.

    Used only as an anti self-contradiction safety net: a model claiming
    `safe_self_service` while the text clearly describes an irreversible or
    account-security action should not be auto-trusted.
    """
    blob = _ask_user_blob(question)
    if not blob:
        return None
    for signal in _STRONG_IRREVERSIBLE_SIGNALS:
        if signal in blob:
            return signal
    return None


def validate_ask_user_category(evaluation: dict, user_intent: dict | None = None) -> tuple[list[str], list[str]]:
    """Constrain `nextAction=ask_user` to the hard-interrupt category whitelist.

    Returns (issues, warnings). When `nextAction != ask_user`, this returns
    empty lists. When ask_user is used, the evaluation MUST include
    `askUserQuestion.category` from the schema-level whitelist; otherwise the
    completion gate rewrites the path to keep the autonomous loop running
    (retry_generator/replan) instead of paying the human-interrupt cost for a
    non-whitelisted reason.

    `user_intent` (from `read_user_autonomy_intent`) carries the user's stated
    autonomy decision. "拦不拦截以用户诉求为准": when the user explicitly chose
    full-auto/no-confirmation, or pre-authorized this very action, even a
    model-declared `sensitive_hard_gate` is rewritten back to retry_generator so
    the loop is not interrupted against the user's wishes.
    """
    intent = user_intent or {}
    issues: list[str] = []
    warnings: list[str] = []
    if str(evaluation.get("nextAction") or "").strip().lower() != "ask_user":
        return issues, warnings
    question = evaluation.get("askUserQuestion")
    if not isinstance(question, dict):
        issues.append("nextAction=ask_user requires askUserQuestion object")
        return issues, warnings
    category = str(question.get("category") or "").strip().lower()
    if not category:
        issues.append(
            "nextAction=ask_user requires askUserQuestion.category to one of the hard-interrupt whitelist"
        )
        return issues, warnings
    if category not in _ASK_USER_ALLOWED_CATEGORIES:
        issues.append(
            "askUserQuestion.category=" + category + " is not in the hard-interrupt whitelist; "
            "valid values: " + ", ".join(sorted(_ASK_USER_ALLOWED_CATEGORIES))
        )
        return issues, warnings

    # User-intent override ("拦不拦截以用户诉求为准"). Whether to pause is the
    # user's decision, not a fixed property of our risk model. Two ground-truth
    # signals from the pre-implementation decision authoritatively rewrite even a
    # model-declared sensitive_hard_gate back to retry_generator:
    #   1. fullAuto: the user explicitly chose no-confirmation/full-auto mode.
    #   2. userAuthorized + a non-empty destructiveActionsAllowList: the model
    #      asserts this very action was already authorized by the user.
    # This only RELEASES what the user already allowed; it never auto-approves an
    # un-authorized sensitive action.
    if intent.get("fullAuto") is True:
        issues.append(
            "ask_user rejected: the user explicitly requested full-auto/no-confirmation "
            "mode (preImplementationReview.fullAuto=true), so CodeMind must not interrupt "
            "the loop. Proceed autonomously and record the decision in the run log."
        )
        return issues, warnings
    if _ask_user_is_user_preauthorized(question, intent):
        issues.append(
            "ask_user rejected: askUserQuestion.userAuthorized=true and the action is "
            "covered by the pre-implementation destructiveActionsAllowList, so the user "
            "already authorized it. Proceed autonomously instead of re-asking."
        )
        return issues, warnings

    # Asymmetric model-trust path. When the Evaluator declared a structured
    # `riskTier`, that self-assessment is the PRIMARY judge -- CodeMind biases
    # toward automation and trusts the model rather than re-guessing intent from
    # brittle keyword lists.
    risk_tier = _ask_user_risk_tier(question)
    if risk_tier is not None:
        if risk_tier == _RISK_TIER_SENSITIVE:
            # Genuine hard gate per the model: let the loop pause. No keyword
            # second-guessing.
            if not question.get("question"):
                warnings.append("askUserQuestion.question is empty")
            options = question.get("options") if isinstance(question.get("options"), list) else None
            if not options:
                warnings.append("askUserQuestion.options should list at least one explicit choice")
            return issues, warnings
        # risk_tier == safe_self_service: the model itself says CodeMind can
        # resolve this without a human. Trust it and rewrite the ask_user back to
        # retry_generator so the loop keeps running -- UNLESS a strong
        # irreversible/account-security signal contradicts the safe label (anti
        # self-contradiction safety net only; deliberately narrow).
        contradiction = _ask_user_has_strong_irreversible_signal(question)
        if contradiction is not None:
            warnings.append(
                "askUserQuestion.riskTier=safe_self_service but text contains a strong "
                f"irreversible/account-security signal ('{contradiction}'); honoring the "
                "hard-interrupt pause instead of auto-trusting the safe label."
            )
            return issues, warnings
        issues.append(
            "ask_user rejected: askUserQuestion.riskTier=safe_self_service means CodeMind "
            "can resolve this itself (long build, minimal reversible verification-unblock "
            "patch, re-signing with the user's own certificates / automatic signing, in-app "
            "device driving, retryable env tweak). Just attempt it and continue the "
            "autonomous loop; reserve ask_user for riskTier=sensitive_hard_gate (genuinely "
            "irreversible/destructive such as delete/wipe/reset, account/credential/keychain "
            "login, payment, or a real device/permission gate)."
        )
        return issues, warnings

    # Fallback for older Evaluator output without a structured riskTier: keep the
    # deterministic keyword vetos so behavior stays safe and unchanged.
    long_running_marker = _ask_user_is_long_running_authorization(question)
    if long_running_marker is not None:
        issues.append(
            "ask_user rejected: long-running/compile-duration authorization "
            f"(matched '{long_running_marker}') is not a valid hard-interrupt reason. "
            "CodeMind should just run the long build/test; settle any scope/duration "
            "authorization once during pre-implementation, not before each expensive step."
        )
        return issues, warnings
    temp_unblock_marker = _ask_user_is_temporary_unblock_authorization(question)
    if temp_unblock_marker is not None:
        issues.append(
            "ask_user rejected: authorizing a temporary verification-unblock "
            f"code/script/build/wrapper patch (matched '{temp_unblock_marker}') is not a "
            "valid hard-interrupt reason. Making a minimal reversible unblock change and "
            "re-running the verification once is CodeMind's own job: just attempt it, "
            "record it in verificationUnblockChanges, and restore/promote before finish. "
            "Only escalate to ask_user when the change is genuinely destructive/sensitive "
            "(delete/reset/uninstall, login/account, external upload, payment, or privilege "
            "escalation)."
        )
        return issues, warnings
    if category == "real_device_or_signing":
        delegation_marker = _ask_user_delegates_device_operation(question)
        if delegation_marker is not None:
            issues.append(
                "ask_user rejected: delegating device operation back to the human "
                f"(matched '{delegation_marker}') is not a valid hard-interrupt reason "
                "when the device is reachable. Driving in-app actions (play/skip/error/"
                "interrupt/navigate) and capturing logcat/.xcresult is CodeMind's own job "
                "via probe-flow/XCUITest. Encode and run the actions; only ask_user for a "
                "genuine device/signing/permission gate (no device in state=device, "
                "unlock/Developer-Mode/USB-debugging/trust unresolved, missing signing, or "
                "denied UI Automation permission)."
            )
            return issues, warnings
    if not question.get("question"):
        warnings.append("askUserQuestion.question is empty")
    options = question.get("options") if isinstance(question.get("options"), list) else None
    if not options:
        warnings.append("askUserQuestion.options should list at least one explicit choice")
    return issues, warnings


def normalize_verification_unblock_changes(evaluation: dict) -> list[dict]:
    """Return recorded temporary verification unblock changes.

    The canonical field is `verificationUnblockChanges`. A legacy/alternate
    spelling is accepted so older task records can still be audited by
    completion-check.
    """
    raw = evaluation.get("verificationUnblockChanges")
    if raw is None:
        raw = evaluation.get("temporaryVerificationChanges")
        if raw is not None:
            _warn_once(
                "temporaryVerificationChanges",
                "`temporaryVerificationChanges` is deprecated; rename to `verificationUnblockChanges`. Will be removed in a future release.",
            )
    if raw is None:
        return []
    if not isinstance(raw, list):
        return [{"id": "VUC-invalid", "status": "invalid", "reason": "verificationUnblockChanges is not an array"}]
    return [item if isinstance(item, dict) else {"id": "VUC-invalid", "status": "invalid", "reason": "unblock change item is not an object"} for item in raw]


def verification_unblock_mentioned_without_record(task_dir: Path, evaluation: dict) -> bool:
    """Detect likely temporary unblock work that lacks structured records."""
    if normalize_verification_unblock_changes(evaluation):
        return False
    candidates = [
        str(evaluation.get("summary", "")),
        json.dumps(evaluation.get("evidence", []), ensure_ascii=False),
        json.dumps(evaluation.get("failedChecks", []), ensure_ascii=False),
    ]
    for name in ["Delivery.md", "Validation.md"]:
        path = task_dir / name
        if path.exists():
            candidates.append(path.read_text(errors="ignore"))
    text = "\n".join(candidates).lower()
    if not text.strip():
        return False
    patterns = [
        "temporary harness",
        "temporary workspace",
        "temporary stub",
        "临时 workspace",
        "临时workspace",
        "临时 stub",
        "临时stub",
        "临时修改",
        "临时改动",
        "临时文件",
        "绕过环境",
        "testability anchor",
        "test anchor",
        "testability hook",
        "automation anchor",
        "ui automation anchor",
        "测试锚点",
        "测试桩",
        "自动化锚点",
    ]
    return any(pattern in text for pattern in patterns)


# Signals that an unblock change is test-instrumentation (a testability hook
# added only so automated verification can locate/drive the UI), not a real
# product fix. Such instrumentation must be removed (status=restored) before
# delivery; it must never be promoted into product code.
TEST_INSTRUMENTATION_KEYWORDS = (
    "accessibilityidentifier",
    "accessibility identifier",
    "accessibilitylabel",
    "isaccessibilityelement",
    "testability anchor",
    "test anchor",
    "testability hook",
    "test hook",
    "debug hook",
    "ui automation anchor",
    "automation anchor",
    "instrumentation",
    "testtag",
    "test tag",
    "contentdescription for test",
    "测试锚点",
    "测试桩",
    "自动化锚点",
    "埋点用于测试",
)


def _unblock_change_is_test_instrumentation(item: dict) -> bool:
    """Heuristically detect a test-instrumentation unblock change.

    Honors an explicit `category` field first (deterministic intent from the
    Evaluator), then falls back to keyword signals in scope/reason/files so
    older records without the field are still caught.
    """
    category = str(item.get("category", "")).strip().lower()
    if category in {"test_instrumentation", "test-instrumentation", "testability"}:
        return True
    if category in {"build_unblock", "build-unblock", "config", "dependency", "other"}:
        return False
    haystack_parts = [str(item.get("scope", "")), str(item.get("reason", ""))]
    files = item.get("files")
    if isinstance(files, list):
        haystack_parts.extend(str(f) for f in files)
    haystack = "\n".join(haystack_parts).lower()
    return any(keyword in haystack for keyword in TEST_INSTRUMENTATION_KEYWORDS)


def validate_verification_unblock_changes(task_dir: Path, evaluation: dict) -> tuple[list[str], list[str]]:
    """Validate temporary verification unblock records for completion gating."""
    issues: list[str] = []
    warnings: list[str] = []
    changes = normalize_verification_unblock_changes(evaluation)
    if verification_unblock_mentioned_without_record(task_dir, evaluation):
        issues.append("temporary verification unblock changes are mentioned but evaluation.json.verificationUnblockChanges is missing")

    for idx, item in enumerate(changes):
        prefix = f"verificationUnblockChanges[{idx}]"
        status = str(item.get("status", "")).strip().lower()
        if status not in {"restored", "promoted", "active"}:
            issues.append(f"{prefix} status must be restored/promoted/active before completion")
        elif status == "active":
            issues.append(f"{prefix} is still active; restore or promote it before finish")
        if status in {"promoted", "active"} and _unblock_change_is_test_instrumentation(item):
            issues.append(
                f"{prefix} is test instrumentation (testability anchor/hook) and must be removed "
                f"(status=restored) before finish; test instrumentation cannot be promoted into product code"
            )
        if not item.get("reason"):
            issues.append(f"{prefix} missing reason")
        files = item.get("files")
        if not isinstance(files, list) or not files:
            issues.append(f"{prefix} missing files list")
        snapshot_refs = [
            ("checkpoint", item.get("checkpoint")),
            ("diff", item.get("diff")),
            ("snapshot", item.get("snapshot")),
        ]
        present_snapshot_refs = [(name, ref) for name, ref in snapshot_refs if ref]
        if not present_snapshot_refs:
            issues.append(f"{prefix} missing checkpoint/diff/snapshot reference")
        else:
            for name, ref in present_snapshot_refs:
                if not evidence_path_exists(task_dir, str(ref)):
                    issues.append(f"{prefix} {name} path does not exist: {ref}")
        if status == "restored":
            restore_evidence = item.get("restoreEvidence")
            if not restore_evidence:
                issues.append(f"{prefix} status=restored requires restoreEvidence")
            elif not evidence_path_exists(task_dir, str(restore_evidence)):
                issues.append(f"{prefix} restoreEvidence path does not exist: {restore_evidence}")
        if status in {"restored", "promoted"}:
            verification_evidence = item.get("verificationEvidence")
            if not verification_evidence:
                issues.append(f"{prefix} status={status} requires verificationEvidence")
            elif not evidence_path_exists(task_dir, str(verification_evidence)):
                issues.append(f"{prefix} verificationEvidence path does not exist: {verification_evidence}")
        if not item.get("scope"):
            warnings.append(f"{prefix} missing scope")
        if not item.get("risk"):
            warnings.append(f"{prefix} missing risk")
    return issues, warnings


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_evidence_assessment(item: dict) -> dict:
    """Normalize evaluator/model judgement about whether evidence proves a TC.

    CodeMind intentionally does not parse arbitrary build/runtime logs with
    fixed success strings. The Evaluator owns semantic judgement and records it
    here; completion-check only validates structure, evidence paths, and that a
    required TC was not passed from a blocker classification.
    """
    raw = item.get("evidenceAssessment")
    if raw is None:
        if "evidenceReview" in item and item.get("evidenceReview") is not None:
            raw = item.get("evidenceReview")
            _warn_once(
                "evidenceReview",
                "`evidenceReview` is a deprecated alias for `evidenceAssessment`; rename to `evidenceAssessment`. Will be removed in a future release.",
            )
        elif "proof" in item and item.get("proof") is not None:
            raw = item.get("proof")
            _warn_once(
                "proof",
                "`proof` is a deprecated alias for `evidenceAssessment`; rename to `evidenceAssessment`. Will be removed in a future release.",
            )
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return {"verdict": raw}
    return {"verdict": "invalid", "reason": "evidenceAssessment must be an object or string"}


def assessment_verdict(item: dict) -> str:
    assessment = normalize_evidence_assessment(item)
    return str(
        assessment.get("verdict")
        or assessment.get("result")
        or assessment.get("conclusion")
        or ""
    ).strip().lower()


def evidence_assessment_is_sufficient(item: dict) -> bool:
    """Return whether the evaluator/model says evidence proves the testcase."""
    human = item.get("humanConfirmation")
    if human is True or human == "confirmed" or (isinstance(human, dict) and human.get("status") == "confirmed"):
        return True
    return assessment_verdict(item) in {"proved", "manual_confirmed"}


def _hard_metrics_passed(assessment: dict) -> list[dict]:
    """Return hardMetrics entries with passed=true (or value matching expected)."""
    raw = assessment.get("hardMetrics") if isinstance(assessment, dict) else None
    if not isinstance(raw, list):
        return []
    passed: list[dict] = []
    for metric in raw:
        if not isinstance(metric, dict):
            continue
        if metric.get("passed") is True:
            passed.append(metric)
            continue
        if "expected" in metric and metric.get("value") == metric.get("expected"):
            passed.append(metric)
    return passed


def _secondary_assessment_independent_proved(assessment: dict) -> bool:
    """Return True iff secondaryAssessment is structurally independent and proved."""
    secondary = assessment.get("secondaryAssessment") if isinstance(assessment, dict) else None
    if not isinstance(secondary, dict):
        return False
    sec_verdict = str(secondary.get("verdict") or "").strip().lower()
    if sec_verdict not in {"proved", "manual_confirmed"}:
        return False
    primary_assessor = str(assessment.get("assessor") or "").strip().lower()
    secondary_assessor = str(secondary.get("assessor") or "").strip().lower()
    if not secondary_assessor:
        return False
    independent_flag = secondary.get("independent")
    if independent_flag is True:
        return True
    if independent_flag is False:
        return False
    # Default: independent iff assessor identifiers differ.
    if primary_assessor and secondary_assessor == primary_assessor:
        return False
    return True


def evidence_assessment_proved_anchor_missing(item: dict) -> tuple[bool, str]:
    """For verdict=proved required pass rows, demand a machine anchor.

    Returns (missing, reason) where missing=True means the proved verdict has
    neither hardMetrics nor an independent secondaryAssessment proving it.
    manual_confirmed rows are exempt because they carry explicit human evidence.
    """
    if normalize_test_result_value(item.get("result", "")) != "pass":
        return False, ""
    verdict = assessment_verdict(item)
    if verdict != "proved":
        return False, ""
    assessment = normalize_evidence_assessment(item)
    if _hard_metrics_passed(assessment):
        return False, ""
    if _secondary_assessment_independent_proved(assessment):
        return False, ""
    return True, (
        "verdict=proved requires either hardMetrics[].passed=true or an independent "
        "secondaryAssessment with verdict=proved/manual_confirmed"
    )


def evidence_assessment_metric_evidence_missing(task_dir: Path, item: dict) -> tuple[bool, str]:
    """For proved pass rows, ensure hard metric anchors point at real evidence.

    A screenshot/log/trace is only precisely consumable when the passed metric
    that claims the proof references an existing artifact. Top-level evidence is
    useful for reports, but it does not explain which metric/assertion it proves.
    """
    if normalize_test_result_value(item.get("result", "")) != "pass":
        return False, ""
    verdict = assessment_verdict(item)
    if verdict != "proved":
        return False, ""
    assessment = normalize_evidence_assessment(item)
    if _secondary_assessment_independent_proved(assessment):
        return False, ""
    assessment_refs = normalize_evidence_refs(
        assessment.get("machineAnchor")
        or assessment.get("evidence")
        or assessment.get("evidencePath")
        or assessment.get("evidencePaths")
    )
    if assessment_refs and any(evidence_path_exists(task_dir, ref) for ref in assessment_refs):
        return False, ""
    passed_metrics = _hard_metrics_passed(assessment)
    if not passed_metrics:
        return False, ""
    for metric in passed_metrics:
        refs = normalize_evidence_refs(
            metric.get("evidence")
            or metric.get("evidencePath")
            or metric.get("evidencePaths")
            or metric.get("artifact")
            or metric.get("artifacts")
        )
        if refs and any(evidence_path_exists(task_dir, ref) for ref in refs):
            return False, ""
    return True, "proved hardMetrics must reference at least one existing evidence artifact"


# Markers that mean a piece of evidence text was cut off, so a value that
# appears near such a marker may be incomplete and must not be trusted as a
# precise runtime assertion anchor. Covers CodeMind's own digest/tail markers.
_TRUNCATION_MARKERS = (
    "[truncated",
    "truncated for",
    "compact excerpt: omitted",
    "characters truncated",
    "some characters truncated",
    "output truncated",
    "log truncated",
    "…",
)

# Evidence-assessment / metric fields by which the Evaluator can explicitly
# declare that the runtime assertion value came from a truncated/incomplete log
# line (so the proof actually rests on source-code inference, not observation).
_TRUNCATION_DECLARATION_KEYS = (
    "assertionEvidenceTruncated",
    "runtimeAssertionFromTruncatedEvidence",
    "truncatedAssertionField",
    "assertionFieldTruncated",
)

_SCREENSHOT_OR_BINARY_SUFFIXES = SCREENSHOT_SUFFIXES | {".xcresult", ".mov", ".mp4", ".zip"}


def _declares_assertion_truncation(item: dict) -> bool:
    assessment = item.get("evidenceAssessment") if isinstance(item.get("evidenceAssessment"), dict) else {}
    for source in (item, assessment):
        for key in _TRUNCATION_DECLARATION_KEYS:
            if bool(source.get(key)):
                return True
    for metric in assessment.get("hardMetrics") or []:
        if isinstance(metric, dict):
            for key in _TRUNCATION_DECLARATION_KEYS:
                if bool(metric.get(key)):
                    return True
    return False


def _evidence_line_is_truncated(task_dir: Path, ref: object, keyword: object) -> bool:
    """Return True iff the line proving ``keyword`` in evidence ``ref`` is cut off.

    Only inspects textual log/json evidence; screenshots/xcresult are skipped.
    Without a keyword we conservatively do not flag (avoids false positives on
    large logs whose tail is legitimately a digest marker).
    """
    if not ref or not keyword:
        return False
    suffix = Path(str(ref)).suffix.lower()
    if suffix in _SCREENSHOT_OR_BINARY_SUFFIXES:
        return False
    path = Path(str(ref)) if Path(str(ref)).is_absolute() else (task_dir / str(ref))
    try:
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    token = str(keyword).strip()
    if not token:
        return False
    for line in text.splitlines():
        if token in line and any(marker in line for marker in _TRUNCATION_MARKERS):
            return True
    return False


def evidence_assessment_truncated_proof_unbacked(task_dir: Path, item: dict) -> tuple[bool, str]:
    """Forbid verdict=proved when the runtime assertion rests on a truncated line.

    Two signals trip this gate for a proved required pass row:

    * The Evaluator explicitly declared the assertion value came from a
      truncated/incomplete log line (model-first triage declaration), or
    * a passed log/keyword hardMetric's keyword only appears inside an evidence
      line that carries a truncation marker.

    Either way the proof reduces to source-code inference of an unobserved
    value. The row may only stay proved if it is independently backed by a
    secondaryAssessment (manual_confirmed/proved by a different assessor) or a
    recorded humanConfirmation; otherwise it must be re-verified with an
    untruncated channel (single-field log line, packet capture, xcresult dump).
    """
    if normalize_test_result_value(item.get("result", "")) != "pass":
        return False, ""
    if assessment_verdict(item) != "proved":
        return False, ""
    assessment = normalize_evidence_assessment(item)
    # An independent second judge or human confirmation is an acceptable backing.
    if _secondary_assessment_independent_proved(assessment):
        return False, ""
    human = item.get("humanConfirmation") if isinstance(item.get("humanConfirmation"), dict) else {}
    if str(human.get("status") or "").strip().lower() == "confirmed":
        return False, ""

    declared = _declares_assertion_truncation(item)
    marker_truncated = False
    for metric in _hard_metrics_passed(assessment):
        name = str(metric.get("name") or "").strip().lower()
        if not any(tok in name for tok in ("log", "keyword", "grep", "regex", "string", "ocr")):
            continue
        keyword = metric.get("expected")
        if keyword is None or isinstance(keyword, bool):
            keyword = metric.get("value")
        refs = normalize_evidence_refs(
            metric.get("evidence")
            or metric.get("evidencePath")
            or metric.get("evidencePaths")
            or metric.get("artifact")
            or metric.get("artifacts")
        ) or normalize_evidence_refs(assessment.get("machineAnchor"))
        for ref in refs:
            if _evidence_line_is_truncated(task_dir, ref, keyword):
                marker_truncated = True
                break
        if marker_truncated:
            break

    if not declared and not marker_truncated:
        return False, ""
    return True, (
        "verdict=proved but the runtime assertion value rests on a truncated/incomplete "
        "evidence line (source-code inference, not observation). Re-verify via an untruncated "
        "channel (dedicated single-field log line, packet capture, or xcresult attachment dump), "
        "or back the row with an independent secondaryAssessment / recorded humanConfirmation."
    )


# Markers that identify a probe-flow / UI runner result as a dry-run, i.e. it
# only validated action *intent* and never drove the real device/runtime.
_DRY_RUN_METRIC_MARKERS = ("dry_run", "dryrun", "dry-run")


def _metric_is_dry_run(metric: dict) -> bool:
    if not isinstance(metric, dict):
        return False
    name = str(metric.get("name") or "").strip().lower()
    return any(marker in name for marker in _DRY_RUN_METRIC_MARKERS)


def _load_json_evidence(task_dir: Path, ref) -> Optional[dict]:
    """Best-effort load a JSON evidence artifact referenced by a TC."""
    if not ref:
        return None
    ref_str = str(ref)
    if not ref_str.lower().endswith(".json"):
        return None
    path = Path(ref_str) if Path(ref_str).is_absolute() else (task_dir / ref_str)
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _anchor_file_marks_dry_run(task_dir: Path, assessment: dict, item: dict) -> bool:
    """Return True iff a referenced JSON evidence artifact has dryRun=true."""
    refs = list(normalize_evidence_refs(
        assessment.get("machineAnchor")
        or assessment.get("evidence")
        or assessment.get("evidencePath")
        or assessment.get("evidencePaths")
    ))
    refs.extend(normalize_evidence_refs(item.get("evidence")))
    for ref in refs:
        data = _load_json_evidence(task_dir, ref)
        if isinstance(data, dict) and data.get("dryRun") is True:
            return True
    return False


def evidence_is_dry_run_only(task_dir: Path, item: dict) -> bool:
    """Return True iff a passed TC is backed only by dry-run probe-flow evidence.

    A probe-flow dry-run validates action intent (selectors/order/assertions)
    but never installs, launches, or drives the real device/runtime. It must not
    satisfy a runtime/device-level runtime-proof requirement. An independent
    non-dry-run secondaryAssessment or any non-dry-run passed hardMetric rescues
    the row (there is real proof beyond intent validation).
    """
    if normalize_test_result_value(item.get("result", "")) != "pass":
        return False
    assessment = normalize_evidence_assessment(item)
    if _secondary_assessment_independent_proved(assessment):
        return False
    if _anchor_file_marks_dry_run(task_dir, assessment, item):
        return True
    passed_metrics = _hard_metrics_passed(assessment)
    if any(not _metric_is_dry_run(metric) for metric in passed_metrics):
        # At least one passed hard metric is real proof, not a dry-run signal.
        return False
    if passed_metrics and all(_metric_is_dry_run(metric) for metric in passed_metrics):
        return True
    return _anchor_file_marks_dry_run(task_dir, assessment, item)


def evidence_assessment_missing_or_negative(item: dict) -> bool:
    if normalize_test_result_value(item.get("result", "")) != "pass":
        return False
    return not evidence_assessment_is_sufficient(item)


def evidence_assessment_has_blocker(item: dict) -> bool:
    """Detect a passed row whose own structured assessment says blocker."""
    if normalize_test_result_value(item.get("result", "")) != "pass":
        return False
    item_category = str(item.get("category", "")).strip().lower()
    if item_category in BLOCKER_CATEGORIES:
        return True
    assessment = normalize_evidence_assessment(item)
    values = []
    for key in ["blockers", "blockerCategories", "categories", "failedCategories"]:
        values.extend(_as_list(assessment.get(key)))
    for key in ["blockerCategory", "category", "reason"]:
        if assessment.get(key):
            values.append(assessment.get(key))
    normalized = {str(value).strip().lower() for value in values if str(value).strip()}
    return bool(normalized & BLOCKER_CATEGORIES) or assessment_verdict(item) == "blocked"


def build_test_results_from_evaluation(
    task_dir: Path,
    evaluation: dict,
    testcases: list[dict],
    allow_synthesize_pass: bool = False,
) -> tuple[list[dict], list[str]]:
    """Return normalized `testResults[]` and warnings.

    Explicit `evaluation.testResults[]` is preferred. Failed/skipped checks may
    override or supplement explicit rows. For legacy adapter pass results, the
    caller may allow synthesizing pass rows from declared required testcases and
    top-level evidence.
    """
    warnings: list[str] = []
    testcase_by_norm = {tc["normalizedId"]: tc for tc in testcases}
    results_by_norm: dict[str, dict] = {}

    def upsert_result(
        testcase_id: str,
        result: str,
        reason: str = "",
        evidence=None,
        acceptance=None,
        source: str = "evaluation",
        category: str = "",
        human_confirmation=None,
        evidence_assessment=None,
        extra_fields: dict | None = None,
    ):
        tc_ids = extract_artifact_ids(str(testcase_id), "TC")
        if not tc_ids:
            return
        display_id = next(iter(tc_ids.values()))
        norm_id = normalize_artifact_id(display_id)
        tc = testcase_by_norm.get(norm_id, {})
        refs = normalize_evidence_refs(evidence)
        ac_values: list[str] = []
        if isinstance(acceptance, list):
            ac_values.extend(str(item) for item in acceptance)
        elif isinstance(acceptance, str):
            ac_values.extend(extract_artifact_ids(acceptance, "AC").values())
        if not ac_values:
            ac_values.extend(tc.get("acceptanceCriteria") or [])

        existing = results_by_norm.get(norm_id)
        normalized_result = normalize_test_result_value(result)
        item = existing or {
            "testCaseId": tc.get("id") or display_id,
            "required": bool(tc.get("required", False)),
            "quality": bool(tc.get("quality", False)),
            "acceptanceCriteria": ac_values,
            "evidence": [],
            "sources": [],
        }
        if ac_values:
            item["acceptanceCriteria"] = sorted(set([*item.get("acceptanceCriteria", []), *ac_values]))
        if refs:
            item["evidence"] = sorted(set([*item.get("evidence", []), *refs]))
        if reason:
            item["reason"] = reason
        if category:
            item["category"] = category
        if human_confirmation is not None:
            item["humanConfirmation"] = human_confirmation
        if evidence_assessment is not None:
            item["evidenceAssessment"] = evidence_assessment
        if extra_fields:
            for key in ["noScreenshotReason", "screenshotNote", "runtimePath", "failureClass", "observedSignals", "retryAdvice", "shouldRetry"]:
                if key in extra_fields and extra_fields.get(key) not in (None, "", []):
                    item[key] = extra_fields.get(key)
        item.setdefault("sources", [])
        if source not in item["sources"]:
            item["sources"].append(source)

        # Failure-like results override pass/warn, skipped/not_run override only
        # missing/unknown, and explicit pass wins over unknown.
        old = item.get("result", "unknown")
        priority = {"fail": 5, "blocked": 5, "partial": 5, "skipped": 4, "not_run": 4, "pass": 3, "warn": 2, "unknown": 1}
        if priority.get(normalized_result, 1) >= priority.get(old, 1):
            item["result"] = normalized_result
        results_by_norm[norm_id] = item

    explicit_results = evaluation.get("testResults")
    if isinstance(explicit_results, list):
        for raw_item in explicit_results:
            if not isinstance(raw_item, dict):
                warnings.append("evaluation.testResults item is not an object")
                continue
            testcase_id = raw_item.get("testCaseId") or raw_item.get("id") or raw_item.get("name")
            if not testcase_id:
                ids = extract_artifact_ids(json.dumps(raw_item, ensure_ascii=False), "TC")
                testcase_id = next(iter(ids.values()), "")
            if not testcase_id:
                warnings.append("evaluation.testResults item missing testCaseId")
                continue
            upsert_result(
                str(testcase_id),
                str(raw_item.get("result", "unknown")),
                str(raw_item.get("reason", raw_item.get("summary", "")) or ""),
                raw_item.get("evidence") or raw_item.get("evidencePaths"),
                raw_item.get("acceptanceCriteria") or raw_item.get("ac"),
                "testResults",
                str(raw_item.get("category", "") or ""),
                raw_item.get("humanConfirmation"),
                raw_item.get("evidenceAssessment") or raw_item.get("evidenceReview") or raw_item.get("proof"),
                raw_item,
            )

    for raw_item in evaluation.get("qualityChecks", []) or []:
        if not isinstance(raw_item, dict) or not raw_item.get("testCaseId"):
            continue
        upsert_result(
            str(raw_item.get("testCaseId")),
            str(raw_item.get("result", "unknown")),
            str(raw_item.get("reason", "")),
            raw_item.get("evidence"),
            raw_item.get("acceptanceCriteria"),
            "qualityChecks",
            str(raw_item.get("category", "") or ""),
            raw_item.get("humanConfirmation"),
            raw_item.get("evidenceAssessment") or raw_item.get("evidenceReview") or raw_item.get("proof"),
        )

    for raw_item in evaluation.get("failedChecks", []) or []:
        if not isinstance(raw_item, dict):
            continue
        ids = extract_artifact_ids(str(raw_item.get("testCaseId", "")) + " " + json.dumps(raw_item, ensure_ascii=False), "TC")
        for testcase_id in ids.values():
            upsert_result(
                testcase_id,
                "blocked" if raw_item.get("category") in {"environment_blocked", "mobile_device_unavailable", "permission_blocked", "tool_missing"} else "fail",
                str(raw_item.get("reason", "")),
                raw_item.get("evidence"),
                raw_item.get("acceptanceCriteria"),
                "failedChecks",
                str(raw_item.get("category", "") or ""),
            )

    for raw_item in evaluation.get("skippedChecks", []) or []:
        if not isinstance(raw_item, dict):
            continue
        ids = extract_artifact_ids(str(raw_item.get("testCaseId", "")) + " " + json.dumps(raw_item, ensure_ascii=False), "TC")
        for testcase_id in ids.values():
            category = str(raw_item.get("category", "skipped"))
            upsert_result(
                testcase_id,
                "not_run" if category in {"not_selected", "not_applicable"} else "skipped",
                str(raw_item.get("reason", "")),
                raw_item.get("evidence"),
                raw_item.get("acceptanceCriteria"),
                "skippedChecks",
            )

    top_level_evidence = normalize_evidence_refs(evaluation.get("evidence", []))
    if allow_synthesize_pass and evaluation.get("result") == "pass":
        missing_required = [tc for tc in testcases if tc.get("required") and tc["normalizedId"] not in results_by_norm]
        if missing_required:
            warnings.append("completion_check_synthesized_required_test_results_from_pass_evaluation_legacy_unused")
        for tc in missing_required:
            upsert_result(
                tc["id"],
                "pass",
                "Synthesized by completion gate because the selected evaluator returned pass and provided top-level evidence.",
                top_level_evidence,
                tc.get("acceptanceCriteria"),
                "completion_synthesis_legacy_unused",
            )

    # Lightweight TC attempt ledger can prove a TC was attempted even when an
    # adapter/probe wrote its result before the latest evaluation. Use it to
    # distinguish not_run from attempted-but-failed/partial without changing the
    # dynamic iteration schedule.
    tc_attempts = read_tc_attempts(task_dir)
    latest_attempts = tc_attempts.get("latest") if isinstance(tc_attempts.get("latest"), dict) else {}
    for tc in testcases:
        if tc["normalizedId"] in results_by_norm:
            continue
        attempt = latest_attempts.get(tc["id"])
        if not isinstance(attempt, dict):
            continue
        upsert_result(
            tc["id"],
            str(attempt.get("result") or "unknown"),
            str(attempt.get("summary") or "Recorded in tc-attempts ledger."),
            attempt.get("evidence"),
            tc.get("acceptanceCriteria"),
            "tc-attempts",
            "attempted_not_proved" if str(attempt.get("result") or "").lower() != "pass" else "",
            evidence_assessment={
                "verdict": "proved" if str(attempt.get("result") or "").lower() == "pass" else "not_proved",
                "assessor": "tc-attempts",
                "reason": str(attempt.get("summary") or "Recorded in tc-attempts ledger."),
            },
        )

    # Fill in declared testcases not mentioned by evaluation/attempt ledger as
    # not_run so the ledger gives a complete matrix.
    for tc in testcases:
        if tc["normalizedId"] not in results_by_norm:
            results_by_norm[tc["normalizedId"]] = {
                "testCaseId": tc["id"],
                "result": "not_run",
                "required": bool(tc.get("required", False)),
                "quality": bool(tc.get("quality", False)),
                "acceptanceCriteria": tc.get("acceptanceCriteria") or [],
                "evidence": [],
                "reason": "No test result was reported for this declared testcase.",
                "sources": [],
            }

    ordered = []
    for tc in testcases:
        item = results_by_norm.get(tc["normalizedId"])
        if item:
            ordered.append(item)
    return ordered, warnings


def build_completion_report(
    task_dir: Path,
    evaluation: Optional[dict] = None,
    allow_synthesize_pass: bool = False,
) -> tuple[dict, dict]:
    """Build completion report and enriched evaluation without mutating files."""
    evaluation = dict(evaluation or read_evaluation_json(task_dir) or {})
    issues: list[str] = []
    warnings: list[str] = []
    testcases = extract_declared_testcases(task_dir)
    testcase_by_norm = {tc["normalizedId"]: tc for tc in testcases}

    if not testcases:
        issues.append("TestCases.md missing declared TC-* cases; cannot prove completion")

    if not evaluation:
        issues.append("evaluation.json missing_or_invalid")
        evaluation = {
            "iteration": 0,
            "result": "blocked",
            "summary": "Missing evaluation.json",
            "failedChecks": [],
            "nextAction": "stop",
        }

    test_results, result_warnings = build_test_results_from_evaluation(task_dir, evaluation, testcases, allow_synthesize_pass)
    warnings.extend(result_warnings)
    unblock_issues, unblock_warnings = validate_verification_unblock_changes(task_dir, evaluation)
    issues.extend(unblock_issues)
    warnings.extend(unblock_warnings)
    boundary_issues, boundary_warnings, boundary_violations = validate_evaluator_boundary(evaluation)
    issues.extend(boundary_issues)
    warnings.extend(boundary_warnings)
    ask_user_issues, ask_user_warnings = validate_ask_user_category(evaluation, read_user_autonomy_intent(task_dir))
    issues.extend(ask_user_issues)
    warnings.extend(ask_user_warnings)
    result_by_norm = {normalize_artifact_id(item.get("testCaseId", "")): item for item in test_results}

    require_text = read_requirements_contract_text(task_dir)
    required_ac = extract_artifact_ids(require_text, "AC")

    passed_required: list[str] = []
    failed_required: list[str] = []
    skipped_required: list[str] = []
    not_run_required: list[str] = []
    evidence_missing: list[str] = []
    blocker_pass_required: list[str] = []
    evidence_assessment_missing: list[str] = []
    proved_anchor_missing: list[dict] = []
    metric_evidence_missing: list[dict] = []
    truncated_proof_unbacked: list[dict] = []
    covered_ac: set[str] = set()

    top_level_evidence = normalize_evidence_refs(evaluation.get("evidence", []))
    for tc in testcases:
        item = result_by_norm.get(tc["normalizedId"])
        if not item:
            continue
        result = normalize_test_result_value(item.get("result", "unknown"))
        refs = normalize_evidence_refs(item.get("evidence")) or top_level_evidence
        has_existing_evidence = any(evidence_path_exists(task_dir, ref) for ref in refs)
        if tc.get("required"):
            if test_result_is_acceptable(tc, result):
                passed_required.append(tc["id"])
                for ac in item.get("acceptanceCriteria") or tc.get("acceptanceCriteria") or []:
                    covered_ac.add(normalize_artifact_id(ac))
                if not has_existing_evidence:
                    evidence_missing.append(tc["id"])
                if evidence_assessment_has_blocker(item):
                    blocker_pass_required.append(tc["id"])
                if evidence_assessment_missing_or_negative(item):
                    evidence_assessment_missing.append(tc["id"])
                anchor_missing, anchor_reason = evidence_assessment_proved_anchor_missing(item)
                if anchor_missing:
                    proved_anchor_missing.append({
                        "id": tc["id"],
                        "reason": anchor_reason,
                    })
                metric_missing, metric_reason = evidence_assessment_metric_evidence_missing(task_dir, item)
                if metric_missing:
                    metric_evidence_missing.append({
                        "id": tc["id"],
                        "reason": metric_reason,
                    })
                trunc_missing, trunc_reason = evidence_assessment_truncated_proof_unbacked(task_dir, item)
                if trunc_missing:
                    truncated_proof_unbacked.append({
                        "id": tc["id"],
                        "reason": trunc_reason,
                    })
            elif result in {"skipped"}:
                skipped_required.append(tc["id"])
            elif result in {"not_run", "unknown"}:
                not_run_required.append(tc["id"])
            else:
                failed_required.append(tc["id"])

    for testcase_id in failed_required:
        issues.append(f"required testcase failed or blocked: {testcase_id}")
    for testcase_id in skipped_required:
        issues.append(f"required testcase skipped: {testcase_id}")
    for testcase_id in not_run_required:
        issues.append(f"required testcase not_run: {testcase_id}")
    for testcase_id in evidence_missing:
        issues.append(f"required testcase missing existing evidence path: {testcase_id}")
    for testcase_id in blocker_pass_required:
        issues.append(f"required testcase passed with blocker evidence assessment instead of proven evidence: {testcase_id}")
    for testcase_id in evidence_assessment_missing:
        issues.append(f"required testcase missing positive evidenceAssessment verdict: {testcase_id}")
    for entry in proved_anchor_missing:
        issues.append(
            f"required testcase verdict=proved lacks machine anchor: {entry['id']} ({entry['reason']})"
        )
    for entry in metric_evidence_missing:
        issues.append(
            f"required testcase proved metric lacks existing evidence artifact: {entry['id']} ({entry['reason']})"
        )
    for entry in truncated_proof_unbacked:
        issues.append(
            f"required testcase verdict=proved rests on truncated evidence line: {entry['id']} ({entry['reason']})"
        )

    open_ac_norms = set(required_ac.keys()) - covered_ac
    for norm in sorted(open_ac_norms, key=lambda item: required_ac[item]):
        issues.append(f"acceptance criterion not covered by passed required testcase: {required_ac[norm]}")

    # Runtime-proof completion gate (real-device-first policy).
    # When the planner classified the task as a client/app behavior task
    # (decisionBundle.runtimeProofRequired == "yes"), at least one required
    # testcase with runtimeLevel ∈ {runtime, device} must have passed with
    # acceptable evidence. Otherwise the task may not finish unless the user
    # has explicitly approved a runtimeDowngradeApproval. This guards against the
    # known failure mode where Evaluator marks runtime TC as not_run due to
    # an environment blocker and the run still claims pass.
    runtime_proof_required = ""
    runtime_downgrade_approved = False
    runtime_downgrade_reason = ""
    runtime_proof_tcs: list[str] = []
    runtime_proof_optional_tcs: list[str] = []
    runtime_proof_passed_tcs: list[str] = []
    workflow_contract = ensure_workflow_contract(task_dir)
    if workflow_contract:
        runtime_proof_required = "yes" if workflow_contract.get("runtimeProofRequired") else str(workflow_contract.get("runtimeProofRequiredRaw") or "").strip().lower()
        runtime_downgrade_approved = bool(workflow_contract.get("runtimeDowngradeApprovalApproved") or workflow_contract.get("runtimeDowngradeApprovalSigned"))
        downgrade_obj = workflow_contract.get("runtimeDowngradeApproval")
        if isinstance(downgrade_obj, dict):
            runtime_downgrade_reason = str(downgrade_obj.get("reason") or "").strip()

    if not runtime_proof_required:
        state_for_runtime = read_runtime_state(task_dir) or {}
        planner_for_runtime = state_for_runtime.get("planner") if isinstance(state_for_runtime.get("planner"), dict) else {}
        review_for_runtime = (
            planner_for_runtime.get("preImplementationReview")
            if isinstance(planner_for_runtime.get("preImplementationReview"), dict)
            else {}
        )
        decision_bundle_for_runtime = (
            review_for_runtime.get("decisionBundle")
            if isinstance(review_for_runtime.get("decisionBundle"), dict)
            else None
        )
        if decision_bundle_for_runtime is not None:
            runtime_proof_required = str(
                decision_bundle_for_runtime.get("runtimeProofRequired") or ""
            ).strip().lower()
            downgrade_obj = decision_bundle_for_runtime.get("runtimeDowngradeApproval")
            if isinstance(downgrade_obj, dict):
                runtime_downgrade_approved = (
                    bool(str(downgrade_obj.get("approvedBy") or downgrade_obj.get("signedBy") or "").strip())
                    and bool(str(downgrade_obj.get("approvedAt") or downgrade_obj.get("signedAt") or "").strip())
                )
                runtime_downgrade_reason = str(downgrade_obj.get("reason") or "").strip()

    passed_required_set = set(passed_required)
    runtime_proof_dry_run_only: list[str] = []
    runtime_ui_passed_without_screenshot: list[str] = []
    for tc in testcases:
        # Normalize compound runtimeLevel cells such as "device/runtime" or
        # "static/runtime" to the canonical level so a runtime/device-capable
        # required TC is not missed by an exact-match check.
        level = _normalize_runtime_level(tc.get("runtimeLevel"))
        if level in RUNTIME_LEVELS:
            if tc.get("required"):
                runtime_proof_tcs.append(tc["id"])
                if tc["id"] in passed_required_set:
                    # A dry-run probe-flow only validates action intent; it does
                    # not drive the real device/runtime, so it cannot satisfy a
                    # runtime/device-level runtime-proof requirement.
                    item = result_by_norm.get(tc["normalizedId"])
                    if item is not None and evidence_is_dry_run_only(task_dir, item):
                        runtime_proof_dry_run_only.append(tc["id"])
                    else:
                        runtime_proof_passed_tcs.append(tc["id"])
                        if item is not None and not _has_existing_screenshot_for_result(task_dir, item) and not _result_mentions_no_screenshot_reason(item):
                            runtime_ui_passed_without_screenshot.append(tc["id"])
            else:
                runtime_proof_optional_tcs.append(tc["id"])

    if runtime_proof_required == "yes":
        if runtime_proof_passed_tcs:
            pass
        elif runtime_proof_dry_run_only and not runtime_downgrade_approved:
            issues.append(
                "runtimeProofRequired=yes but the only runtime/device-level required TC(s) "
                f"({', '.join(runtime_proof_dry_run_only)}) passed on probe-flow dry-run evidence "
                "(dryRun=true / *_dry_run metric), which validates action intent only and never "
                "drives the real device/runtime. Re-run the runtime/device TC against the real "
                "device (e.g. build-for-testing + devicectl install + test-without-building), "
                "or record an approved runtimeDowngradeApproval before finish."
            )
        elif runtime_downgrade_approved:
            warnings.append(
                "runtimeProofRequired=yes but no runtime/device-level required TC passed; "
                "accepting pass because runtimeDowngradeApproval is approved"
                + (f" (reason: {runtime_downgrade_reason})" if runtime_downgrade_reason else "")
            )
        else:
            if runtime_proof_tcs:
                issues.append(
                    "runtimeProofRequired=yes: no runtime/device-level required TC passed "
                    f"(candidates: {', '.join(runtime_proof_tcs)}). "
                    "Real-device-first policy: re-run runtime/device TC, "
                    "or record an approved runtimeDowngradeApproval before finish."
                )
            elif runtime_proof_optional_tcs:
                issues.append(
                    "runtimeProofRequired=yes but runtime/device TC was demoted to optional "
                    f"({', '.join(runtime_proof_optional_tcs)}). Required runtime proof cannot be skipped "
                    "without approved runtimeDowngradeApproval."
                )
            else:
                issues.append(
                    "runtimeProofRequired=yes but TestCases.md declares no required TC with "
                    "runtimeLevel in {runtime, device}. Add a runtime/device-level required TC, "
                    "or record an approved runtimeDowngradeApproval before finish."
                )
    elif runtime_proof_dry_run_only and not runtime_proof_passed_tcs and not runtime_downgrade_approved:
        # Even when runtimeProofRequired is "auto"/unset, a required TC declared
        # at runtimeLevel runtime/device inherently needs real runtime proof. A
        # dry-run probe-flow (action-intent only) must not satisfy it.
        issues.append(
            "runtime/device-level required TC(s) "
            f"({', '.join(runtime_proof_dry_run_only)}) passed on probe-flow dry-run evidence "
            "(dryRun=true / *_dry_run metric), which validates action intent only and never "
            "drives the real device/runtime. Re-run the runtime/device TC against the real "
            "device (e.g. build-for-testing + devicectl install + test-without-building), "
            "or record an approved runtimeDowngradeApproval before finish."
        )

    if runtime_ui_passed_without_screenshot:
        # Hard gate: a runtime/device-level required TC that passed must carry a
        # screenshot by default. Pure-backend / no-screenshot-capability cases
        # stay unblocked by declaring an explicit noScreenshotReason (those TCs
        # were already filtered out above via _result_mentions_no_screenshot_reason).
        issues.append(
            "runtime/device-level required TC passed without screenshot evidence and without an "
            "explicit noScreenshotReason: "
            + ", ".join(runtime_ui_passed_without_screenshot)
            + ". Capture a per-TC/page screenshot (or attach xcresult/UI artifact), or record a "
            "noScreenshotReason when the verification surface genuinely cannot produce a screenshot."
        )

    if evaluation.get("result") != "pass":
        issues.append(f"evaluation result is not pass: {evaluation.get('result')}")
    if evaluation.get("nextAction") != "finish":
        warnings.append(f"evaluation nextAction is not finish: {evaluation.get('nextAction')}")
    if evaluation.get("failedChecks"):
        issues.append("evaluation has failedChecks")

    coverage = {
        "requiredTestCases": [tc["id"] for tc in testcases if tc.get("required")],
        "requiredTestCasesPassed": passed_required,
        "requiredTestCasesFailed": failed_required,
        "requiredTestCasesSkipped": skipped_required,
        "requiredTestCasesNotRun": not_run_required,
        "evidenceAssessmentMissing": evidence_assessment_missing,
        "provedAnchorMissing": proved_anchor_missing,
        "truncatedProofUnbacked": truncated_proof_unbacked,
        "acceptanceCriteriaRequired": list(required_ac.values()),
        "acceptanceCriteriaCovered": [required_ac[norm] for norm in required_ac.keys() if norm in covered_ac],
        "acceptanceCriteriaOpen": [required_ac[norm] for norm in sorted(open_ac_norms, key=lambda item: required_ac[item])],
        "evaluatorBoundaryViolations": boundary_violations,
        "runtimeProofRequired": runtime_proof_required or "auto",
        "runtimeProofCandidates": runtime_proof_tcs,
        "runtimeProofOptionalCandidates": runtime_proof_optional_tcs,
        "runtimeProofPassed": runtime_proof_passed_tcs,
        "runtimeProofDryRunOnly": runtime_proof_dry_run_only,
        "runtimeUiPassedWithoutScreenshot": runtime_ui_passed_without_screenshot,
        "runtimeDowngradeApprovalApproved": runtime_downgrade_approved,
        "completionCheck": "pass" if not issues else "fail",
    }

    strong_post_checks = evaluation.get("strongPostChecks") if isinstance(evaluation.get("strongPostChecks"), list) else []
    strong_post_check_failures: list[dict] = []
    for item in strong_post_checks:
        if not isinstance(item, dict):
            continue
        if str(item.get("strength") or "").strip().lower() != "strong":
            continue
        status = str(item.get("status") or "").strip().lower()
        if status not in {"proved", "pass", "passed", "ok", "satisfied"}:
            strong_post_check_failures.append({
                "type": item.get("type") or "strong_post_check",
                "status": status or "missing",
                "missingSignals": item.get("missingSignals") or [],
            })
    for item in strong_post_check_failures:
        issues.append(
            "strong post-check not proved: "
            + str(item.get("type"))
            + (
                f" (missing: {', '.join(str(x) for x in item.get('missingSignals')[:6])})"
                if item.get("missingSignals")
                else ""
            )
        )
    if strong_post_check_failures:
        coverage["strongPostChecks"] = strong_post_checks
        coverage["strongPostChecksFailed"] = strong_post_check_failures
        coverage["completionCheck"] = "fail"

    enriched = {
        **evaluation,
        "testResults": test_results,
        "coverage": coverage,
    }

    final_result = "pass" if not issues else "fail"
    raw_evaluation_claim = {
        "result": evaluation.get("result"),
        "nextAction": evaluation.get("nextAction"),
        "iteration": evaluation.get("iteration"),
    }
    if runtime_proof_required == "yes":
        runtime_proof_satisfied = bool(runtime_proof_passed_tcs) or runtime_downgrade_approved
    elif runtime_proof_dry_run_only and not runtime_proof_passed_tcs and not runtime_downgrade_approved:
        runtime_proof_satisfied = False
    else:
        runtime_proof_satisfied = True

    report = {
        "task": task_dir.name,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "result": final_result,
        "issues": issues,
        "warnings": warnings,
        "rawEvaluationClaim": raw_evaluation_claim,
        "completionVerdict": {
            "result": final_result,
            "overridesRawEvaluation": (
                raw_evaluation_claim.get("result") != final_result
                or (raw_evaluation_claim.get("nextAction") == "finish" and final_result != "pass")
            ),
            "reason": "; ".join(issues[:3]) if issues else "completion-check passed",
        },
        "testCases": [
            {
                "id": tc["id"],
                "required": tc.get("required", False),
                "quality": tc.get("quality", False),
                "runtimeLevel": tc.get("runtimeLevel", ""),
                "acceptanceCriteria": tc.get("acceptanceCriteria", []),
            }
            for tc in testcases
        ],
        "testResults": test_results,
        "coverage": coverage,
        "completion": {
            "allRequiredPassed": not failed_required and not skipped_required and not not_run_required,
            "allAcceptanceCriteriaCovered": not open_ac_norms,
            "requiredEvidencePresent": not evidence_missing,
            "noRequiredPassViaBlockerClassification": not blocker_pass_required,
            "requiredEvidenceAssessmentPresent": not evidence_assessment_missing,
            "requiredProvedAnchorPresent": not proved_anchor_missing,
            "runtimeAssertionProofUntruncated": not truncated_proof_unbacked,
            "runtimeUiScreenshotPresent": not runtime_ui_passed_without_screenshot,
            "strongPostChecksSatisfied": not strong_post_check_failures,
            "runtimeProofSatisfied": runtime_proof_satisfied,
        },
    }
    report["nextActionPrompt"] = build_completion_next_action_prompt(report, task_dir.name)
    return report, enriched


def build_completion_next_action_prompt(report: dict, task_code: str) -> str:
    """Skill-mode automation hint for completion-check.

    Mirrors workflow-check's nextActionPrompt: a single imperative line that
    tells the host agent what the next concrete step is, so Skill/Command-mode
    sessions do not early-stop after a fail and do not loop without progress
    after a pass.
    """
    if report.get("result") == "pass":
        return (
            f"completion-check passed for {task_code}. "
            "Run `automind summary {task} --ai <agent>` to refine and persist "
            "summary/Reuse memory, then handoff is complete."
        ).format(task=task_code)
    issues = report.get("issues") or []
    head = "; ".join(str(item) for item in issues[:3])
    coverage = report.get("coverage") or {}
    failed_required = coverage.get("failedRequired") or []
    open_ac = coverage.get("openAcceptanceCriteria") or []
    hints: list[str] = []
    if failed_required:
        hints.append(
            "Failed required TC: " + ", ".join(str(x) for x in failed_required[:5])
            + ". Generator must repair product/runtime code, then re-verify."
        )
    if open_ac:
        hints.append(
            "Uncovered AC: " + ", ".join(str(x) for x in open_ac[:5])
            + ". Add concrete TC* runbooks in TestCases.md and re-run Evaluator."
        )
    if not hints:
        hints.append(
            "Refine the failing artifact/evidence indicated above, "
            "then continue Generator -> Evaluator until completion-check passes."
        )
    return (
        f"completion-check FAILED for {task_code}: {head}. "
        + " ".join(hints)
        + " Do NOT stop the loop unless ask_user is required (5-category whitelist)."
    )


def write_completion_ledger(task_dir: Path, report: dict) -> Path:
    """Write final completion artifacts from one authoritative report.

    Historically CodeMind wrote ``VerificationLedger.json`` while leaving
    ``completion-report.json`` stale/not_run in some paths. That let
    runtime-state says completionCheck=pass while the phase sidecar still claimed
    completion had not run. Keep both artifacts in sync here: the ledger is the
    detailed coverage matrix, and completion-report.json is the phase sidecar
    consumed by workflow/report/status tooling.
    """
    path = task_dir / "VerificationLedger.json"
    completion_path = task_dir / "completion-report.json"
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    path.write_text(payload)
    completion_path.write_text(payload)
    updates = {
        "verificationLedger": str(path),
        "completionReport": str(completion_path),
        "completionCheck": report.get("result"),
        "completionVerdict": report.get("completionVerdict"),
        "rawEvaluationClaim": report.get("rawEvaluationClaim"),
    }
    if str(report.get("result") or "").strip().lower() == "pass":
        updates.update({
            "status": "finished",
            "currentOwner": "automind",
            "nextAction": "finish",
            "lastResult": "pass",
            "terminalAuthoritative": {
                "authority": "completion-check-final",
                "terminalAuthoritative": True,
                "finalizedAt": datetime.now().isoformat(timespec="seconds"),
            },
        })
    update_runtime_state(task_dir, **updates)
    emit_workflow_event(task_dir, {
        "type": "completion_report_written",
        "phase": "completion",
        "action": "finish" if str(report.get("result") or "").strip().lower() == "pass" else "generate_summary",
        "nextAction": "finish" if str(report.get("result") or "").strip().lower() == "pass" else "generate_summary",
        "nextPhase": "completion",
        "status": "completed" if str(report.get("result") or "").strip().lower() == "pass" else "running",
        "iteration": (read_runtime_state(task_dir) or {}).get("iteration", 0),
        "completion": {
            "result": report.get("result"),
            "completionVerdict": report.get("completionVerdict"),
            "blockers": report.get("blockers") if isinstance(report.get("blockers"), list) else [],
            "issues": report.get("issues") if isinstance(report.get("issues"), list) else [],
            "warnings": report.get("warnings") if isinstance(report.get("warnings"), list) else [],
            "rawEvaluationClaim": report.get("rawEvaluationClaim"),
            "verificationEvidence": report.get("verificationEvidence"),
            "coverage": report.get("coverage"),
            "rawEvaluationClaim": report.get("rawEvaluationClaim"),
            "testResults": report.get("testResults") if isinstance(report.get("testResults"), list) else [],
        },
    })
    return path


def apply_completion_gate(
    task_dir: Path,
    evaluation: dict,
    allow_synthesize_pass: bool = False,
    fail_next_action: str = "retry_generator",
) -> tuple[dict, dict]:
    """Enrich evaluation with coverage and block false `finish` results."""
    report, enriched = build_completion_report(task_dir, evaluation, allow_synthesize_pass=allow_synthesize_pass)
    ledger_path = write_completion_ledger(task_dir, report)
    enriched.setdefault("coverage", {})
    enriched["coverage"]["ledgerPath"] = rel_to_root(ledger_path)
    enriched.setdefault("evidence", [])
    if isinstance(enriched["evidence"], list):
        ledger_evidence = {"type": "other", "path": rel_to_root(ledger_path), "note": "verification-ledger"}
        if ledger_evidence not in enriched["evidence"]:
            enriched["evidence"].append(ledger_evidence)

    if evaluation.get("nextAction") == "finish" and report.get("result") != "pass":
        failed_checks = enriched.get("failedChecks") if isinstance(enriched.get("failedChecks"), list) else []
        issues = report.get("issues", []) if isinstance(report.get("issues"), list) else []
        coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
        # The model claimed `finish`, but coverage/evidence disproves it. This is
        # precisely a spot where the model's own self-assessment was wrong, so do
        # not stop at a template reason: hand root-cause analysis back to the
        # model. Tagging the entry with triageSource/needsModelReview makes the
        # next round's context pack render it under "Model-Review Attention
        # Signals", instructing the model to re-read the ledger and decide what
        # to actually fix (missing required TC, uncovered AC, dry-run/blocker
        # masquerading as pass, missing screenshot evidence, ...) rather than
        # blindly retrying. This reuses the existing model-review channel; it
        # adds no new LLM call here.
        failed_checks.append({
            "name": "completion_check",
            "category": "validation_failure",
            "triageSource": "requires_model_review",
            "needsModelReview": True,
            "sameProblemKey": "completion.gate.false_finish_claim",
            "reason": "; ".join(issues[:8]) or "Completion check failed",
            "recoveryAction": (
                "You declared nextAction=finish but the completion gate disproved it. Re-read the verification "
                "ledger and the failing rows below, diagnose the ROOT CAUSE of each blocker (e.g. a required TC "
                "never ran, an AC has no covering TC, a blocker/dry-run was reported as pass, or runtime evidence "
                "is missing), then act on that cause instead of re-claiming finish. Failed required: "
                + (", ".join(str(x) for x in (coverage.get("failedRequired") or [])[:5]) or "none")
                + "; uncovered AC: "
                + (", ".join(str(x) for x in (coverage.get("openAcceptanceCriteria") or [])[:5]) or "none")
                + "."
            ),
            "evidence": rel_to_root(ledger_path),
        })
        enriched["failedChecks"] = failed_checks
        enriched["result"] = "fail"
        enriched["summary"] = "Completion gate blocked finish: " + (report.get("issues", ["unknown"])[0])
        enriched["nextAction"] = fail_next_action
        enriched.setdefault("warnings", [])
        if isinstance(enriched["warnings"], list):
            enriched["warnings"].append("completion_gate_blocked_finish")
    elif report.get("warnings"):
        enriched.setdefault("warnings", [])
        if isinstance(enriched["warnings"], list):
            enriched["warnings"].extend(report["warnings"])

    # State-machine consistency: whenever this gate rewrites nextAction away from
    # ask_user, the residual askUserQuestion MUST be cleared. Otherwise the
    # skill-mode tick gate (normalize_pending_question -> cmd_tick_iteration)
    # keeps seeing an unanswered question and exits 2 every turn, while the
    # harness loop happily proceeds on the rewritten nextAction. That mismatch is
    # the "asked but no chance to answer, yet keeps running" deadlock. Clearing
    # the pending question here keeps both views consistent.
    def _clear_pending_ask_user_question() -> None:
        enriched["askUserQuestion"] = None
        if isinstance(enriched.get("coverage"), dict):
            enriched["coverage"].pop("pendingQuestion", None)

    # A3-2 hard-interrupt whitelist: classify the ask_user up-front so a genuine
    # hard-interrupt question is not silently swallowed by other rewrites (e.g.
    # a boundary violation). A non-whitelisted ask_user is rewritten to
    # retry_generator so the autonomous loop continues.
    ask_user_selected = str(enriched.get("nextAction") or "").strip().lower() == "ask_user"
    ask_user_check_issues: list[str] = []
    ask_user_legitimate = False
    if ask_user_selected:
        ask_user_check_issues, _ = validate_ask_user_category(enriched, read_user_autonomy_intent(task_dir))
        ask_user_legitimate = not ask_user_check_issues
    if ask_user_selected and ask_user_check_issues:
        failed_checks = enriched.get("failedChecks") if isinstance(enriched.get("failedChecks"), list) else []
        failed_checks.append({
            "name": "ask_user_whitelist",
            "category": "validation_failure",
            "reason": (
                "ask_user is reserved for hard-interrupt categories "
                "(unauthorized_destructive_or_sensitive / system_or_external_dependency / "
                "real_device_or_signing / manual_visual_confirmation / repeated_same_failure). "
                + "; ".join(ask_user_check_issues[:3])
            ),
            "evidence": rel_to_root(ledger_path),
        })
        enriched["failedChecks"] = failed_checks
        enriched["result"] = "fail"
        enriched["nextAction"] = "retry_generator"
        enriched["summary"] = (
            "ask_user rejected (no hard-interrupt category): "
            + ask_user_check_issues[0]
        )
        enriched.setdefault("warnings", [])
        if isinstance(enriched["warnings"], list):
            enriched["warnings"].append("ask_user_not_whitelisted")
        _clear_pending_ask_user_question()

    # A1-3 boundary enforcement: an Evaluator boundary violation must route to
    # retry_generator so the Generator owns product-code repair -- UNLESS a
    # genuine hard-interrupt ask_user is still pending. A legitimate ask_user
    # outranks the boundary rewrite: the human decision is gathered first, and
    # the boundary violation is recorded so it is repaired once the loop resumes.
    # This prevents the boundary gate from silently swallowing a valid ask_user.
    boundary_violations = (enriched.get("coverage") or {}).get("evaluatorBoundaryViolations") or []
    if boundary_violations:
        offending_files: list[str] = []
        for violation in boundary_violations:
            offending_files.extend(str(f) for f in (violation.get("files") or []))
        failed_checks = enriched.get("failedChecks") if isinstance(enriched.get("failedChecks"), list) else []
        failed_checks.append({
            "name": "evaluator_boundary",
            "category": "validation_failure",
            "reason": (
                "Evaluator declared changes outside the verifier/probe-flow/test-harness/evidence scope; "
                "product-code repair must be routed to Generator. Offending files: "
                + ", ".join(offending_files[:5])
            ),
            "evidence": rel_to_root(ledger_path),
        })
        enriched["failedChecks"] = failed_checks
        enriched["result"] = "fail"
        enriched.setdefault("warnings", [])
        if isinstance(enriched["warnings"], list):
            enriched["warnings"].append("evaluator_boundary_violation")
        keep_ask_user = ask_user_legitimate and (
            str(enriched.get("nextAction") or "").strip().lower() == "ask_user"
        )
        if not keep_ask_user:
            enriched["nextAction"] = "retry_generator"
            enriched["summary"] = (
                "Evaluator boundary violation: " + ", ".join(offending_files[:3])
                if offending_files
                else "Evaluator boundary violation"
            )
            _clear_pending_ask_user_question()
    return enriched, report
