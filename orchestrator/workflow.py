"""Workflow-check and Phase 2 gate validation helpers for CodeMind.

This module owns cross-artifact continuity checks such as Rxx -> AC -> TC,
planner pre-implementation review gates, evaluator-context wiring, and the
latest workflow-check state persisted in runtime-state.json. It deliberately
does not run the Generator/Evaluator loop; `main.py` remains the CLI and loop
coordinator.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


EXPLICIT_FULL_AUTO_KEYWORDS = [
    "一站到底", "全自动模式", "全自动", "不用问用户", "不用问我", "不用确认", "无需确认", "不要问", "直接实现",
    "full auto", "full-auto", "fully automatic", "no confirmation", "do not ask", "auto proceed",
]

from orchestrator.artifacts import (
    evidence_path_exists,
    extract_artifact_ids,
    extract_declared_testcases,
    extract_plan_checklist_rows,
    format_missing_ids,
    normalize_artifact_id,
    resolve_task_artifact_path,
)
from orchestrator.classification import has_negative_mobile_device_signal
from orchestrator.completion import validate_verification_unblock_changes
from orchestrator.config import PLANNER_PRE_IMPLEMENTATION_DECISIONS
from orchestrator.context_packs import validate_evaluator_context_pack
from orchestrator.phase_registry import EVALUATION_NEXT_ACTIONS
from orchestrator.runtime_paths import runtime_path_workflow_warnings
from orchestrator.state import get_task_dir, read_evaluation_json, read_runtime_state, task_has_authoritative_terminal_pass, update_runtime_state
from orchestrator.workflow_contract import ensure_workflow_contract, validate_workflow_contract




def has_confirmed_pre_implementation_review(task_dir: Path, review: dict) -> bool:
    """Return true when a formal PIR decision was already confirmed by the user.

    This prevents stale workflow-check issues from re-opening an ask_user gate
    after the Phase 2 Refiner has consumed a confirm_recommended_direction
    answer and rewritten pre-implementation-review to auto_proceed.
    """
    candidates: list[dict] = []
    approvals: list[dict] = []
    if isinstance(review, dict):
        candidates.append(review)
        bundle = review.get("decisionBundle")
        if isinstance(bundle, dict):
            candidates.append(bundle)
        approval = review.get("approval")
        if isinstance(approval, dict):
            approvals.append(approval)
    pir_path = task_dir / "pre-implementation-review.json"
    if pir_path.exists():
        try:
            pir = json.loads(pir_path.read_text(errors="ignore"))
        except Exception:
            pir = {}
        if isinstance(pir, dict):
            candidates.append(pir)
            pir_bundle = pir.get("decisionBundle")
            if isinstance(pir_bundle, dict):
                candidates.append(pir_bundle)
            pir_approval = pir.get("approval")
            if isinstance(pir_approval, dict):
                approvals.append(pir_approval)
    for item in candidates:
        selected = str(item.get("selectedOption") or item.get("option") or "").strip()
        if selected == "confirm_recommended_direction" and (item.get("answeredAt") or item.get("confirmedAt")):
            return True
    for approval in approvals:
        if approval.get("confirmedAt") and approval.get("confirmedBy"):
            return True
    answers_path = task_dir / "user-answers.json"
    if answers_path.exists():
        try:
            answers = json.loads(answers_path.read_text(errors="ignore"))
        except Exception:
            answers = []
        if isinstance(answers, list):
            for answer in reversed(answers):
                if not isinstance(answer, dict):
                    continue
                if str(answer.get("selectedOption") or "") == "confirm_recommended_direction":
                    return True
    return False


def is_single_file_protocol(task_dir: Path) -> bool:
    """Return True when Requirements.md is present as the authoritative contract."""
    path = task_dir / "Requirements.md"
    return path.exists() and bool(path.read_text(errors="ignore").strip())


def validate_planner_artifacts(task_dir: Path) -> tuple[bool, list[str]]:
    """Validate that the planner left coherent Phase 2 artifacts."""
    issues: list[str] = []
    single_file = True
    required = ["Brainstorm.md", "Requirements.md", "TestCases.md", "Plan.md"]
    texts: dict[str, str] = {}
    for name in required:
        path = task_dir / name
        text = path.read_text(errors="ignore") if path.exists() else ""
        texts[name] = text
        if not text.strip():
            issues.append(f"missing_or_empty:{name}")

    requirements_text = texts.get("Requirements.md", "")
    spec = requirements_text
    require_text = requirements_text
    testcases = texts.get("TestCases.md", "")
    plan = texts.get("Plan.md", "")
    brainstorm = texts.get("Brainstorm.md", "")

    if not re.search(r"\bR\d{2}\b", spec):
        issues.append("Requirements.md missing Rxx requirement units")
    if "Acceptance" not in require_text and "验收" not in require_text:
        issues.append("Requirements.md missing acceptance criteria section")
    if not re.search(r"\bAC[-_]?\d{2,3}\b", require_text, flags=re.IGNORECASE):
        issues.append("Requirements.md missing stable AC IDs")
    if "TC-" not in testcases:
        issues.append("TestCases.md missing testcase IDs")
    if "Key Path" not in testcases and "Functional" not in testcases and "TC-F" not in testcases:
        issues.append("TestCases.md missing functional/key-path coverage")
    if "Quality" not in testcases and "TC-Q" not in testcases and "not applicable" not in testcases.lower():
        issues.append("TestCases.md missing quality coverage decision")
    if "Expected" not in testcases and "Evidence" not in testcases and "evidence" not in testcases:
        issues.append("TestCases.md missing expected evidence/result")
    testcases_lower = testcases.lower()
    if "runtime level" not in testcases_lower and "runtime" not in testcases_lower and "运行" not in testcases:
        issues.append("TestCases.md missing runtime level for testcases")
    if "command" not in testcases_lower and "automind" not in testcases_lower and "命令" not in testcases:
        issues.append("TestCases.md missing executable command or CodeMind command")
    if "precondition" not in testcases_lower and "tools" not in testcases_lower and "tool" not in testcases_lower and "前置" not in testcases and "工具" not in testcases:
        issues.append("TestCases.md missing tool/precondition requirements")
    has_dynamic_signal = any(token in testcases_lower for token in [
        "unit", "integration", "runtime", "device", "script-command",
        "project-native", "pytest", "npm test", "xcodebuild", "gradle",
        "probe-flow", "xcuitest", "android", "ios", "api", "launch",
    ]) or any(token in testcases for token in ["运行", "启动", "真机", "模拟器", "单测", "集成"])
    has_static_only_signal = (
        ("static" in testcases_lower or "静态" in testcases)
        and not has_dynamic_signal
        and ("manual" not in testcases_lower and "blocked" not in testcases_lower and "ask_user" not in testcases_lower and "无法运行" not in testcases)
    )
    if not has_dynamic_signal:
        issues.append("TestCases.md missing dynamic/runtime verification path for required functional cases")
    if has_static_only_signal:
        issues.append("TestCases.md appears static-only; static inspection cannot be the only required functional evidence")
    declared_testcases = extract_declared_testcases(task_dir)
    required_functional_cases = [
        tc for tc in declared_testcases
        if tc.get("required")
        and not tc.get("quality")
        and (
            str(tc.get("id", "")).upper().startswith("TC-F")
            or "functional" in str(tc.get("type", "")).lower()
            or "key path" in str(tc.get("type", "")).lower()
            or "关键" in str(tc.get("type", ""))
            or not str(tc.get("type", "")).strip()
        )
    ]
    if not required_functional_cases:
        required_functional_cases = [
            tc for tc in declared_testcases
            if tc.get("required") and not tc.get("quality")
        ]

    def has_any(text: str, tokens: list[str]) -> bool:
        lower_text = text.lower()
        return any(token.lower() in lower_text for token in tokens)

    prep_terms = [
        "prepare", "preflight", "precondition", "dependency", "fixture", "setup",
        "cwd", "environment", "available", "created", "build", "install", "deploy",
        "start", "device", "simulator", "emulator", "python3", "node", "npm",
        "准备", "前置", "预置", "依赖", "环境", "工具", "可用", "已创建", "构建", "安装", "设备",
    ]
    run_terms = [
        "run", "start", "launch", "open", "execute", "call", "parse", "check",
        "generate", "build", "install", "deploy", "click", "tap", "input",
        "navigate", "request", "probe-flow", "xcuitest", "pytest", "gradle",
        "xcodebuild", "command", "script-command", "workflow-check", "record-check",
        "completion-check", "运行", "启动", "打开", "执行", "调用", "解析", "检查", "生成",
        "点击", "输入", "导航", "请求", "构建", "安装", "命令",
    ]
    assertion_terms = [
        "assert", "expect", "compare", "exit code", "pass", "result=", "visible",
        "exists", "exist", "match", "response", "output", "state", "log",
        "screenshot", "hierarchy", "report", "record-check passes", "completion check",
        "断言", "预期", "通过", "存在", "可见", "匹配", "结果", "输出", "状态",
        "日志", "截图", "报告", "验证",
    ]
    for tc in required_functional_cases:
        row_text = " ".join(
            str(tc.get(key, ""))
            for key in ["source", "preconditions", "command", "steps", "expectedEvidence"]
        )
        missing_parts: list[str] = []
        if not has_any(row_text, prep_terms):
            missing_parts.append("preparation/preflight/preconditions")
        if not has_any(row_text, run_terms):
            missing_parts.append("execution/action")
        if not has_any(row_text, assertion_terms):
            missing_parts.append("assertion/expected result")
        if missing_parts:
            issues.append(
                f"TestCases.md required functional case {tc.get('id')} missing concrete "
                + ", ".join(missing_parts)
            )

    plan_lower = plan.lower()
    state_for_classification = read_runtime_state(task_dir) or {}
    user_input = state_for_classification.get("userInput", "")
    if not user_input and (task_dir / ".user_input.txt").exists():
        user_input = (task_dir / ".user_input.txt").read_text(errors="ignore")
    task_type_for_classification = str(state_for_classification.get("taskType", "")).lower()
    classification_text = user_input + "\n" + require_text + "\n" + spec + "\n" + brainstorm
    classification_lower = classification_text.lower()
    mobile_negative_signal = has_negative_mobile_device_signal(classification_text)
    lexical_ui_signal = bool(re.search(
        r"\b(app|ui|screen|page|button|login|frontend|web|browser|desktop)\b",
        classification_lower,
    ))
    mobile_ui_signal = (
        task_type_for_classification in {"ios", "android", "dual"}
        or bool(re.search(r"\b(android|ios|mobile)\b", classification_lower))
    ) and not mobile_negative_signal
    chinese_ui_signal = any(token in classification_text for token in [
        "页面", "界面", "按钮", "登录", "前端", "客户端", "移动端", "安卓", "苹果", "浏览器", "桌面",
    ]) and not mobile_negative_signal
    app_ui_signal = lexical_ui_signal or mobile_ui_signal or chinese_ui_signal
    geometry_layout_signal = (
        bool(re.search(
            r"\b(layout|position|alignment|align|size|width|height|frame|bounds|bounding box|bbox|rect|coordinate|pixel|px|dp|pt|margin|padding|spacing|overlap|clipped|baseline|centered|constraint|autolayout|snapshot|visual regression)\b",
            classification_lower,
        ))
        or any(token in classification_text for token in [
            "布局", "位置", "坐标", "大小", "尺寸", "宽", "高度", "宽度",
            "对齐", "间距", "边距", "重叠", "裁剪", "居中", "约束",
            "截图对比", "像素", "容差", "阈值",
        ])
    )
    visual_semantic_signal = (
        bool(re.search(
            r"\b(visual|image|screenshot|icon|color|style|design|render|display|looks|appearance|snapshot|golden|ocr)\b",
            classification_lower,
        ))
        or any(token in classification_text for token in [
            "视觉", "图片", "图像", "截图", "图标", "颜色", "样式", "设计",
            "渲染", "展示", "显示", "外观", "看起来", "识别",
        ])
    )
    app_runtime_terms = [
        "build", "package", "install", "deploy", "start server", "dev server",
        "launch", "open", "ui flow", "user journey", "probe-flow", "xcuitest",
        "playwright", "browser", "screenshot", "ui hierarchy", "logcat",
        "启动", "安装", "打开", "构建", "打包", "部署", "服务", "流程", "截图",
    ]
    if app_ui_signal and not any(term in (testcases_lower + "\n" + plan_lower) for term in app_runtime_terms):
        issues.append("App/UI task missing build/install/start/launch/UI-flow verification decision in TestCases.md or Plan.md")
    if app_ui_signal:
        app_test_plan_text = testcases_lower + "\n" + plan_lower
        entry_terms = [
            "entry", "home", "login", "target route", "route", "screen", "page",
            "activity", "view", "controller", "deep link", "url", "selector",
            "feature flag", "fixture", "state", "入口", "首页", "登录页", "目标页",
            "页面", "屏幕", "路由", "activity", "视图", "状态",
        ]
        ui_action_terms = [
            "tap", "click", "input", "navigate", "scroll", "select", "call",
            "request", "background", "action sequence", "user journey", "ui flow",
            "perform", "exercise", "probe-flow", "xcuitest", "playwright",
            "点击", "输入", "导航", "滑动", "选择", "调用", "请求", "操作", "流程",
        ]
        ui_assertion_terms = [
            "assert", "expect", "visible", "exists", "text", "state", "log",
            "event", "api response", "screenshot", "ui hierarchy", "test report",
            "断言", "预期", "可见", "存在", "文本", "状态", "日志", "事件", "截图",
            "层级", "报告",
        ]
        if not has_any(app_test_plan_text, entry_terms):
            issues.append("App/UI task missing precise entry target/page/screen/route/activity/state in TestCases.md or Plan.md")
        if not has_any(app_test_plan_text, ui_action_terms):
            issues.append("App/UI task missing concrete UI/user action sequence in TestCases.md or Plan.md")
        if not has_any(app_test_plan_text, ui_assertion_terms):
            issues.append("App/UI task missing concrete UI/runtime assertions in TestCases.md or Plan.md")
        if geometry_layout_signal:
            geometry_assertion_terms = [
                "bounds", "frame", "bounding box", "bbox", "rect", "coordinate",
                "x=", "y=", "width", "height", "px", "dp", "pt", "pixel",
                "screenshot diff", "visual regression", "snapshot", "golden",
                "tolerance", "threshold", "getboundingclientrect", "boundingbox",
                "domrect", "xcui", "uiautomator", "ui hierarchy", "hierarchy",
                "layout inspector", "accessibility frame", "位置", "坐标", "宽",
                "高", "大小", "尺寸", "对齐", "间距", "边距", "重叠",
                "裁剪", "截图对比", "像素", "容差", "阈值", "层级",
            ]
            if not has_any(app_test_plan_text, geometry_assertion_terms):
                issues.append("UI geometry/layout task missing measurable position/size/layout assertion and evidence path in TestCases.md or Plan.md")
        if visual_semantic_signal:
            visual_proof_terms = [
                "ai visual review", "visual_review", "image understanding", "vision-capable",
                "multimodal", "screenshot diff", "visual regression", "snapshot",
                "golden", "baseline", "ocr", "image comparison", "pixel",
                "visual-inspect", "visual_inspect", "visual inspection",
                "visual-inspection", "visual_inspector", "deterministic visual",
                "human confirmation", "manual confirmation", "ask_user",
                "confirm_pass", "reject_fail", "provide_reference", "user confirmation",
                "screenshot confirmation", "screenshot-based confirmation",
                "bounds", "frame", "ui hierarchy", "accessibility", "视觉复核",
                "图片理解", "多模态", "截图对比", "视觉回归", "基线图",
                "视觉检查", "确定性视觉", "人工确认", "用户确认", "截图确认", "像素", "层级",
            ]
            if not has_any(app_test_plan_text, visual_proof_terms):
                issues.append("UI visual task missing a visual proof method such as screenshot diff, bounds/hierarchy, AI Visual Review, OCR, or explicit human confirmation")
            if has_any(app_test_plan_text, ["ai visual review", "visual_review", "image understanding", "vision-capable", "图片理解", "视觉复核", "多模态"]):
                fallback_terms = [
                    "fallback", "if unavailable", "if vision", "blocked", "ask_user",
                    "human confirmation", "manual confirmation", "screenshot diff",
                    "bounds", "frame", "ocr", "visual-inspect", "visual inspection",
                    "deterministic visual", "screenshot confirmation", "confirm_pass",
                    "provide_reference", "回退", "不可用", "阻塞",
                    "用户确认", "人工确认", "截图确认", "截图对比", "层级",
                ]
                if not has_any(app_test_plan_text, fallback_terms):
                    issues.append("AI Visual Review is planned but TestCases.md/Plan.md do not state fallback/blocker behavior when no vision-capable model is available")
    if "TC-" not in plan:
        issues.append("Plan.md missing concrete TestCase references")
    if "functional batch" not in plan.lower() and "first functional" not in plan.lower():
        issues.append("Plan.md missing first functional batch")
    if "command" not in plan_lower and "automind" not in plan_lower and "命令" not in plan:
        issues.append("Plan.md missing concrete verification command/tool path")
    implementation_checklist = extract_plan_checklist_rows(plan, "Implementation Checklist")
    verification_checklist = extract_plan_checklist_rows(plan, "Verification Checklist")
    if "implementation checklist" not in plan_lower or not implementation_checklist:
        issues.append("Plan.md missing Implementation Checklist with T* status rows")
    if "verification checklist" not in plan_lower or not verification_checklist:
        issues.append("Plan.md missing Verification Checklist with TC-* status rows")
    else:
        plan_verification_tc = {
            normalize_artifact_id(row.get("id", ""))
            for row in verification_checklist
            if str(row.get("id", "")).upper().startswith("TC")
        }
        declared_tc_norms = {tc.get("normalizedId") for tc in declared_testcases if tc.get("normalizedId")}
        missing_verification_rows = declared_tc_norms - plan_verification_tc
        if missing_verification_rows:
            issues.append(
                "Plan.md Verification Checklist missing TestCases: "
                + ", ".join(sorted(missing_verification_rows))
            )
    if "assumption" not in brainstorm.lower() and "question" not in brainstorm.lower() and "假设" not in brainstorm and "问题" not in brainstorm:
        issues.append("Brainstorm.md missing assumptions/questions section")
    if "pre-implementation" not in brainstorm.lower() and "implementation review" not in brainstorm.lower() and "动代码前" not in brainstorm:
        issues.append("Brainstorm.md missing pre-implementation user review decision")

    # A1-1 / A1-2 boundary hygiene: keep Plan-phase artifacts disjoint so each
    # one has a single source of truth and Refiner does not loop on the same
    # content across files.
    #   - Brainstorm.md owns: idea expansion, options, recommendation, decision
    #     bundle, assumptions/questions, pre-implementation review.
    #     It MUST NOT introduce stable Rxx/AC-xxx IDs (those belong to
    #     Requirements.md).
    #   - Requirements.md owns Rxx + inline AC-xxx; no TC-* (those belong to
    #     TestCases.md).
    if re.search(r"\bR\d{2}\b", brainstorm) or re.search(r"\bAC[-_]?\d{2,3}\b", brainstorm, flags=re.IGNORECASE):
        issues.append(
            "Brainstorm.md must not declare Rxx/AC-xxx IDs; move requirement units and acceptance criteria to Requirements.md"
        )
    if "TC-" in spec:
        issues.append(
            "Requirements.md must not declare TC-* testcases; move them to TestCases.md"
        )

    state = read_runtime_state(task_dir) or {}
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
    decision = review.get("decision") or planner.get("preImplementationDecision")
    if decision not in PLANNER_PRE_IMPLEMENTATION_DECISIONS:
        issues.append("runtime-state planner missing preImplementationReview.decision")
    user_input_text = str(state.get("userInput") or "")
    explicit_full_auto = any(keyword.lower() in user_input_text.lower() for keyword in EXPLICIT_FULL_AUTO_KEYWORDS)
    # "拦不拦截以用户诉求为准": if the user opted into 全自动 mode via the
    # pre-implementation ask_user question (either by picking the
    # confirm_full_auto_mode option or typing 全自动/一站到底 in the reply),
    # runtime-state.planner.preImplementationReview.fullAuto is True — honor it
    # as equivalent to the user having said 全自动 in the original request.
    explicit_full_auto = explicit_full_auto or bool(review.get("fullAuto"))
    non_trivial_impl_signal = bool(re.search(r"(实现|新增|修改|修复|开发|构建|创建|接入|重构|优化|完善|改进|implement|add|modify|change|fix|build|create|integrate|refactor|update|improve)", user_input_text.lower()))
    confirmed_pre_review = has_confirmed_pre_implementation_review(task_dir, review)
    if (
        non_trivial_impl_signal
        and not explicit_full_auto
        and decision != "ask_user"
        and planner.get("mode") != "deterministic_scaffold"
        and not confirmed_pre_review
    ):
        issues.append("pre-implementation review must ask_user once for non-trivial implementation unless the user explicitly requested full-auto/no-confirmation mode")
    # Refinement gate: a non-trivial implementation task must not enter Build
    # while its Phase 2 artifacts are still the deterministic scaffold. The
    # scaffold copies the user request almost verbatim and only weakly splits it
    # into Rxx units (see split_user_requirement_units); it does not decompose the
    # demand into fine-grained, separately testable requirements. Building on a
    # scaffold-level Requirements.md yields coarse requirements and therefore too
    # few TestCases, which sends the whole route off course. Require the AI Phase 2
    # Refiner to run (planner.mode advances away from "deterministic_scaffold" and
    # planner.artifactsRefined becomes true) before Build. Gate on the explicit
    # scaffold mode so a refined planner state that simply omits artifactsRefined
    # is not misclassified.
    planner_mode = planner.get("mode")
    scaffold_not_refined = planner_mode == "deterministic_scaffold" and planner.get("artifactsRefined") is not True
    if non_trivial_impl_signal and scaffold_not_refined:
        issues.append(
            "Phase 2 artifacts are still the deterministic scaffold (planner.mode=deterministic_scaffold, not refined); "
            "run the AI Phase 2 Refiner to decompose the user request into fine-grained Rxx/AC-xxx before Build, "
            "otherwise Requirements.md/TestCases.md stay too coarse and the implementation route can drift"
        )
    if planner.get("needsUserInput") is True and decision != "ask_user":
        issues.append("planner.needsUserInput=true but preImplementationReview.decision is not ask_user")
    if decision == "ask_user" and not (review.get("questions") or "question" in brainstorm.lower() or "问题" in brainstorm):
        issues.append("preImplementationReview decision ask_user requires explicit questions in Brainstorm.md")

    return len(issues) == 0, issues


def validate_workflow_evaluator_context_state(task_dir: Path, state: dict) -> tuple[list[str], list[str]]:
    """Validate existing evaluatorContext wiring during workflow-check.

    This is intentionally scoped: workflow-check should not require an
    evaluator context before verification starts, but if one is recorded in
    runtime-state it must prove isolation and point to a valid context pack.
    """
    issues: list[str] = []
    warnings: list[str] = []
    raw_context = state.get("evaluatorContext")
    if not raw_context:
        if state.get("status") == "evaluating" or state.get("nextAction") == "run_evaluator":
            warnings.append("runtime-state is evaluating/run_evaluator but evaluatorContext is missing; deterministic verifier may be okay, model Evaluator needs context-pack")
        return issues, warnings

    if not isinstance(raw_context, dict):
        return ["runtime-state evaluatorContext is not an object"], warnings

    if raw_context.get("inheritsGeneratorContext") is not False:
        issues.append("runtime-state evaluatorContext.inheritsGeneratorContext must be false")
    if raw_context.get("validationOk") is not True:
        issues.append("runtime-state evaluatorContext.validationOk must be true before model Evaluator")

    md_ref = str(raw_context.get("contextPack") or "")
    json_ref = str(raw_context.get("contextPackJson") or "")
    if not md_ref:
        issues.append("runtime-state evaluatorContext missing contextPack")
    elif not resolve_task_artifact_path(task_dir, md_ref):
        issues.append(f"runtime-state evaluatorContext.contextPack path does not exist: {md_ref}")

    if not json_ref:
        issues.append("runtime-state evaluatorContext missing contextPackJson")
        return issues, warnings

    json_path = resolve_task_artifact_path(task_dir, json_ref)
    if not json_path:
        issues.append(f"runtime-state evaluatorContext.contextPackJson path does not exist: {json_ref}")
        return issues, warnings

    try:
        pack = json.loads(json_path.read_text(errors="ignore"))
    except json.JSONDecodeError:
        issues.append(f"evaluatorContext contextPackJson invalid JSON: {json_ref}")
        return issues, warnings

    if pack.get("schema") != "automind.evaluator_context_pack.v1":
        issues.append(f"evaluatorContext contextPackJson has unexpected schema: {pack.get('schema')}")
    pack_ok, pack_issues = validate_evaluator_context_pack(pack)
    if not pack_ok:
        issues.extend(f"evaluatorContext pack invalid:{item}" for item in pack_issues)
    return issues, warnings


def generator_log_exists(task_dir: Path) -> bool:
    """Return True once a Generator round has left an auditable log."""
    logs_dir = task_dir / "logs"
    if not logs_dir.exists():
        return False
    return any(
        iter_dir.is_dir() and (iter_dir / "generator.log").exists()
        for iter_dir in logs_dir.glob("iter-*")
    )


def delivery_gate_applies(state: dict) -> bool:
    """Return True when the task is at or past the boundary into verification."""
    status = state.get("status")
    current_owner = state.get("currentOwner")
    next_action = state.get("nextAction")
    return (
        status in {"evaluating", "finished"}
        or current_owner == "evaluator"
        or next_action in {"run_evaluator", "finish"}
    )


def has_successful_path_reuse(reuse_text: str) -> bool:
    text = reuse_text or ""
    return any(token in text for token in [
        "Successful path:",
        "Top successful paths to consider",
        "Matched knowledge index entries",
        "phase-reuse/",
    ])


def plan_or_testcases_mentions_reuse(plan_text: str, testcases_text: str) -> bool:
    text = (plan_text or "") + "\n" + (testcases_text or "")
    return any(token in text.lower() for token in [
        "successful path", "reuse.md", "known-good", "known good",
        "reusable command", "known successful", "previously successful",
        "复用路径", "成功路径", "已验证路径", "已成功路径",
    ])


# Phases that must record an explicit reuse acknowledgement before running.
REUSE_GATE_PHASES = ("generator", "evaluator")


def _pending_gated_phase(state: dict) -> Optional[str]:
    """Return the gated phase the task is about to enter, if any.

    Generator gate applies when the loop is about to (re)run Generator;
    Evaluator gate applies when the task is at/past the verification boundary.
    """
    next_action = state.get("nextAction")
    current_owner = state.get("currentOwner")
    status = state.get("status")
    if next_action == "run_generator" or current_owner == "generator" or status == "generating":
        return "generator"
    if delivery_gate_applies(state):
        return "evaluator"
    return None


def check_reuse_gate(task_dir: Path, state: dict) -> tuple[list[str], list[str]]:
    """Hard reuse-acknowledgement gate for Generator/Evaluator entry.

    The operator's rule: each phase / retry iteration must record
    phaseReuseRead=true + reuseApplied[]/reuseIgnored[] before entering a gated
    phase, otherwise it must not proceed. Repeated-failure / signing / device /
    build classes must exhaust matched safe reuse paths before escalating to
    ask_user.
    """
    issues: list[str] = []
    warnings: list[str] = []
    # A task that already holds an authoritative terminal pass is finished, not
    # about to (re)enter a gated phase. delivery_gate_applies() treats
    # status=finished / nextAction=finish as "at/past the verification
    # boundary", which would otherwise demand an evaluator reuse-ack that a
    # completed task never records. If a false finish reopened verification, the
    # authoritative pass is already rejected (see
    # completion_pass_superseded_by_reopened_verification), so the gate still
    # fires in that case.
    if task_has_authoritative_terminal_pass(task_dir):
        return issues, warnings
    phase = _pending_gated_phase(state)
    if phase is None or phase not in REUSE_GATE_PHASES:
        return issues, warnings

    reuse_gate = state.get("reuseGate") if isinstance(state.get("reuseGate"), dict) else {}
    gate = reuse_gate.get(phase) if isinstance(reuse_gate.get(phase), dict) else None
    if not gate:
        issues.append(
            f"reuse_gate:{phase} not initialized; run before-phase hook and record "
            f"`automind reuse-ack <task> {phase} --read` before entering {phase}"
        )
        return issues, warnings

    ack = gate.get("acknowledgement") if isinstance(gate.get("acknowledgement"), dict) else None
    if not gate.get("acknowledged") or not ack or not ack.get("phaseReuseRead"):
        issues.append(
            f"reuse_gate:{phase} not acknowledged; agent must read {gate.get('phaseReusePath') or 'phase-reuse/' + phase + '.md'} "
            f"and record `automind reuse-ack <task> {phase} --read --applied/--ignored ...` before {phase} may run"
        )
        return issues, warnings

    applied = ack.get("reuseApplied") or []
    ignored = ack.get("reuseIgnored") or []
    if not applied and not ignored:
        warnings.append(
            f"reuse_gate:{phase} acknowledged but neither reuseApplied nor reuseIgnored recorded; "
            "state which matched reuse paths were used or deliberately skipped"
        )

    # Repeated-failure / signing / device / build: must exhaust safe reuse paths
    # before ask_user. Block when the loop is escalating to ask_user while safe
    # reuse paths exist that were neither applied nor explicitly ignored.
    repeated = gate.get("repeatedFailure") if isinstance(gate.get("repeatedFailure"), dict) else {}
    safe_paths = gate.get("safePaths") or []
    avoid_paths = gate.get("avoidPaths") or []
    escalating = state.get("nextAction") == "ask_user" or state.get("status") == "human_input_pending"
    if repeated.get("detected") and escalating and safe_paths and not applied:
        issues.append(
            f"reuse_gate:{phase} repeated_failure ({repeated.get('category')}) is escalating to ask_user "
            f"without applying any matched safe reuse path ({'; '.join(str(p) for p in safe_paths[:3])}); "
            "exhaust safe reuse paths or record why each was ignored before ask_user"
        )

    # P0-3: when the repeated failure matches a path that reuse/Delivery has
    # explicitly marked as avoid (same runner + same root cause already known to
    # be a dead end), re-asking the user is wasted human interrupt cost. Force
    # the loop to replan instead of repeatedly escalating to ask_user.
    if repeated.get("detected") and escalating and avoid_paths:
        category = str(repeated.get("category") or "").lower()
        same_key = str(repeated.get("sameProblemKey") or "").lower()
        avoid_blob = "\n".join(str(p) for p in avoid_paths).lower()
        matches_known_avoid = bool(
            (category and category in avoid_blob)
            or (same_key and same_key in avoid_blob)
        )
        ask_count = int(repeated.get("sameCategoryAskUserCount") or 0)
        if matches_known_avoid or ask_count >= 2:
            issues.append(
                f"reuse_gate:{phase} repeated_failure ({repeated.get('category')}) matches a known avoid-path "
                f"and is escalating to ask_user again (sameCategoryAskUserCount={ask_count}); "
                "this same runner/root-cause is already recorded as a dead end — set nextAction=replan "
                "instead of re-asking the user"
            )

    return issues, warnings


def check_workflow_consistency(task_code: str) -> tuple[bool, dict]:
    """Check cross-file workflow continuity, not final record completeness.

    Scope:
    - Phase 2 artifact shape and ID continuity: Rxx -> AC-xxx -> TC-* -> Plan.
    - State gates such as planner.needsUserInput.
    - Phase 3 control-signal sanity when evaluation.json exists.

    This intentionally differs from record-check: record-check validates final
    reusable records/logs; workflow-check validates that the task can move
    through CodeMind phases without requirement/test drift.
    """
    task_dir = get_task_dir(task_code)
    issues: list[str] = []
    warnings: list[str] = []
    workflow_contract: dict = {}
    if task_dir.exists():
        workflow_contract = ensure_workflow_contract(task_dir)
        contract_issues, contract_warnings = validate_workflow_contract(task_dir, workflow_contract)
        issues.extend(contract_issues)
        warnings.extend(contract_warnings)
    if not task_dir.exists():
        return False, {
            "task": task_code,
            "result": "fail",
            "issues": [f"Task does not exist: {task_code}"],
            "warnings": [],
        }

    single_file = True
    required = ["Brainstorm.md", "Requirements.md", "TestCases.md", "Plan.md", "runtime-state.json"]
    texts: dict[str, str] = {}
    for name in required:
        path = task_dir / name
        if not path.exists():
            issues.append(f"missing:{name}")
            texts[name] = ""
        else:
            texts[name] = path.read_text(errors="ignore")
            if name.endswith(".md") and not texts[name].strip():
                issues.append(f"empty:{name}")

    reuse_text = (task_dir / "Reuse.md").read_text(errors="ignore") if (task_dir / "Reuse.md").exists() else ""
    if has_successful_path_reuse(reuse_text) and not plan_or_testcases_mentions_reuse(
        texts.get("Plan.md", ""),
        texts.get("TestCases.md", ""),
    ):
        warnings.append(
            "Reuse.md has matched reuse/phase-reuse entries but Plan.md/TestCases.md do not state whether they were considered"
        )

    planner_ok, planner_issues = validate_planner_artifacts(task_dir)
    if not planner_ok:
        issues.extend(f"planner_artifact:{item}" for item in planner_issues)

    requirements_text = texts.get("Requirements.md", "")
    spec_r = extract_artifact_ids(requirements_text, "R")
    require_r_refs = spec_r
    require_ac = extract_artifact_ids(requirements_text, "AC")
    test_r_refs = extract_artifact_ids(texts.get("TestCases.md", ""), "R")
    test_ac_refs = extract_artifact_ids(texts.get("TestCases.md", ""), "AC")
    test_tc = extract_artifact_ids(texts.get("TestCases.md", ""), "TC")
    plan_tc_refs = extract_artifact_ids(texts.get("Plan.md", ""), "TC")

    if not spec_r:
        issues.append("Requirements.md missing requirement IDs (R01/R02/...)")
    if not require_ac:
        issues.append("Requirements.md missing acceptance criteria IDs (AC-001/AC-002/...)")
    if not test_tc:
        issues.append("TestCases.md missing testcase IDs (TC-F01/TC-001/...)")

    if spec_r:
        missing_test_r = set(spec_r) - set(test_r_refs)
        if missing_test_r == set(spec_r):
            warnings.append("TestCases.md does not reference Requirements.md Rxx IDs directly; ensure AC coverage is complete")
        elif missing_test_r:
            warnings.append(f"TestCases.md does not reference all Requirements: {format_missing_ids(spec_r, missing_test_r)}")

    if require_ac:
        missing_ac = set(require_ac) - set(test_ac_refs)
        if missing_ac:
            issues.append(f"TestCases.md does not cover acceptance criteria: {format_missing_ids(require_ac, missing_ac)}")

    if test_tc:
        if not plan_tc_refs:
            issues.append("Plan.md does not reference concrete TestCases")
        else:
            unknown_plan_tc = set(plan_tc_refs) - set(test_tc)
            if unknown_plan_tc:
                issues.append(f"Plan.md references TestCases not defined in TestCases.md: {format_missing_ids(plan_tc_refs, unknown_plan_tc)}")

    state = read_runtime_state(task_dir) or {}
    reuse_gate_issues, reuse_gate_warnings = check_reuse_gate(task_dir, state)
    issues.extend(reuse_gate_issues)
    warnings.extend(reuse_gate_warnings)
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    review = planner.get("preImplementationReview") if isinstance(planner.get("preImplementationReview"), dict) else {}
    decision = review.get("decision") or planner.get("preImplementationDecision")
    if decision not in PLANNER_PRE_IMPLEMENTATION_DECISIONS:
        issues.append("runtime-state planner missing preImplementationReview.decision")
    elif decision == "ask_user":
        if state.get("nextAction") != "ask_user" and state.get("status") != "human_input_pending":
            issues.append("planner preImplementationReview asks for user input but runtime-state is not waiting for ask_user/human_input_pending")
        issues.append("pre-implementation user review is unresolved; user must confirm Requirements/TestCases/workflow before Generator")
    if planner.get("needsUserInput") is True:
        if state.get("nextAction") != "ask_user" and state.get("status") != "human_input_pending":
            issues.append("planner.needsUserInput=true but runtime-state is not waiting for ask_user/human_input_pending")
        issues.append("planner.needsUserInput=true; do not enter Generator until Brainstorm.md questions and decision bundle are resolved")

    # One-shot DecisionBundle: warn-only validation. The bundle is a single
    # confirmation surface for the user; planner/evaluator should read it as
    # the source of truth instead of re-prompting per phase. Missing or
    # incomplete fields should not block workflow-check, but the user should
    # be reminded so they can confirm the bundle before Generator runs.
    decision_bundle = review.get("decisionBundle") if isinstance(review.get("decisionBundle"), dict) else None
    if decision_bundle is None:
        warnings.append(
            "runtime-state planner.preImplementationReview.decisionBundle is missing; "
            "Brainstorm should expose a one-shot decision bundle for user confirmation"
        )
    else:
        bundle_brainstorm = texts.get("Brainstorm.md", "")
        if "User Decision Bundle" not in bundle_brainstorm and "decisionBundle" not in bundle_brainstorm:
            warnings.append(
                "Brainstorm.md missing 'User Decision Bundle (one-shot confirmation)' section; "
                "user cannot confirm verificationTarget/AC/TC/evidence in one pass"
            )
        verification_target = str(decision_bundle.get("verificationTarget") or "").strip()
        if verification_target in {"", "unknown"}:
            warnings.append(
                "decisionBundle.verificationTarget is unknown; confirm real_device / simulator_emulator / both / not_applicable before Generator"
            )
        if not decision_bundle.get("confirmedAt") or not decision_bundle.get("confirmedBy"):
            issues.append(
                "decisionBundle.confirmedAt/confirmedBy are pending; user has not confirmed the one-shot bundle yet"
            )

        # Runtime-proof gate (real-device-first policy).
        # When the planner classifies the task as a client/app behavior task
        # (runtimeProofRequired == "yes"), CodeMind requires either:
        #   1) verificationTarget ∈ {real_device, simulator_emulator, both}
        #      (a runtime-capable target is locked in), OR
        #   2) runtimeDowngradeApproval is an approved object recording that
        #      the user explicitly approved skipping runtime proof
        #      (e.g. {"approvedBy": "...", "approvedAt": "...",
        #      "reason": "..."}).
        # Otherwise we treat this as an issue (not a warning), so Generator
        # cannot proceed silently to a static-only verification path.
        runtime_proof_required = str(
            decision_bundle.get("runtimeProofRequired") or ""
        ).strip().lower()
        downgrade_approval = decision_bundle.get("runtimeDowngradeApproval")
        runtime_targets = {"real_device", "simulator_emulator", "both"}
        downgrade_approved = (
            isinstance(downgrade_approval, dict)
            and bool(str(downgrade_approval.get("approvedBy") or downgrade_approval.get("signedBy") or "").strip())
            and bool(str(downgrade_approval.get("approvedAt") or downgrade_approval.get("signedAt") or "").strip())
        )
        if runtime_proof_required == "yes":
            if verification_target in runtime_targets:
                pass
            elif downgrade_approved:
                warnings.append(
                    "decisionBundle.runtimeDowngradeApproval is approved; "
                    "CodeMind will accept a non-runtime verificationTarget but completion-check still requires evidence."
                )
            else:
                issues.append(
                    "decisionBundle.runtimeProofRequired=yes but verificationTarget "
                    f"is '{verification_target or 'unset'}' and runtimeDowngradeApproval is missing/unapproved. "
                    "Real-device-first policy: pick real_device / simulator_emulator / both, "
                    "or record an approved runtimeDowngradeApproval (approvedBy + approvedAt + reason) before Generator."
                )

    evaluator_context_issues, evaluator_context_warnings = validate_workflow_evaluator_context_state(task_dir, state)
    issues.extend(evaluator_context_issues)
    warnings.extend(evaluator_context_warnings)

    delivery_path = task_dir / "Delivery.md"
    delivery = delivery_path.read_text(errors="ignore") if delivery_path.exists() else ""
    has_generator_log = generator_log_exists(task_dir)
    if has_generator_log and not delivery.strip():
        message = "Delivery.md missing or empty after Generator run; final Verify must not start"
        if delivery_gate_applies(state):
            issues.append(message)
        else:
            warnings.append(message)
    if delivery and test_tc:
        delivery_tc = extract_artifact_ids(delivery, "TC")
        if not delivery_tc:
            message = "Delivery.md does not reference TC IDs; Generator changes may be hard to map to tests"
            if has_generator_log and delivery_gate_applies(state):
                issues.append(message)
            else:
                warnings.append(message)

    evaluation = read_evaluation_json(task_dir)
    if evaluation is None:
        if state.get("status") == "finished":
            issues.append("runtime-state status=finished but evaluation.json is missing or invalid")
        else:
            warnings.append("evaluation.json is missing or invalid; Phase 3 may not have run yet")
    else:
        result = evaluation.get("result")
        next_action = evaluation.get("nextAction")
        if next_action not in EVALUATION_NEXT_ACTIONS:
            issues.append(f"evaluation.json nextAction is invalid: {next_action}")
        if result == "pass" and next_action != "finish":
            issues.append("evaluation.json result=pass must use nextAction=finish")
        if next_action == "finish" and result != "pass":
            issues.append("evaluation.json nextAction=finish requires result=pass")
        if next_action == "ask_user" and not evaluation.get("askUserQuestion"):
            issues.append("evaluation.json nextAction=ask_user requires askUserQuestion")

        unblock_issues, unblock_warnings = validate_verification_unblock_changes(task_dir, evaluation)
        issues.extend(unblock_issues)
        warnings.extend(unblock_warnings)
        warnings.extend(runtime_path_workflow_warnings(
            task_dir,
            evaluation,
            plan_text=texts.get("Plan.md", ""),
            delivery_text=delivery,
        ))

        failed_or_skipped = json.dumps(
            {
                "failedChecks": evaluation.get("failedChecks", []),
                "skippedChecks": evaluation.get("skippedChecks", []),
            },
            ensure_ascii=False,
        )
        if failed_or_skipped != '{"failedChecks": [], "skippedChecks": []}' and test_tc:
            eval_tc = extract_artifact_ids(failed_or_skipped, "TC")
            if not eval_tc and (evaluation.get("failedChecks") or evaluation.get("skippedChecks")):
                warnings.append("evaluation.json failed/skipped checks do not reference TC IDs")
            unknown_eval_tc = set(eval_tc) - set(test_tc)
            if unknown_eval_tc:
                warnings.append(f"evaluation.json references TC IDs not defined in TestCases.md: {format_missing_ids(eval_tc, unknown_eval_tc)}")

        for idx, item in enumerate(evaluation.get("evidence", []) or []):
            if isinstance(item, dict) and item.get("path") and not evidence_path_exists(task_dir, str(item.get("path"))):
                warnings.append(f"evaluation.evidence[{idx}] path does not exist: {item.get('path')}")

    current_state = read_runtime_state(task_dir) or {}
    state_status = str(current_state.get("status") or "").strip().lower()
    state_next = str(current_state.get("nextAction") or "").strip().lower()
    terminal_status = state_status in {"finished", "completed"}
    terminal_next = state_next in {"finish", "done", "completed"}
    completion_check = str(current_state.get("completionCheck") or "").strip().lower()
    completion_verdict = current_state.get("completionVerdict") if isinstance(current_state.get("completionVerdict"), dict) else {}
    authoritative_completion_pass = task_has_authoritative_terminal_pass(task_dir)
    terminal_guard = {
        "terminalStateObserved": terminal_status or terminal_next,
        "runtimeStateStatus": current_state.get("status"),
        "runtimeStateNextAction": current_state.get("nextAction"),
        "completionCheck": current_state.get("completionCheck"),
        "completionVerdict": completion_verdict or None,
        "authoritativeCompletionPass": authoritative_completion_pass,
        "allowTerminal": bool((terminal_status or terminal_next) and authoritative_completion_pass and not issues),
        "reason": "completion_check_pass" if ((terminal_status or terminal_next) and authoritative_completion_pass and not issues) else (
            "workflow_red_terminal_state" if (terminal_status or terminal_next) and issues else (
                "completion_not_authoritative" if (terminal_status or terminal_next) else "not_terminal_state"
            )
        ),
    }
    if terminal_guard["terminalStateObserved"] and not terminal_guard["allowTerminal"]:
        issues.append(
            "runtime-state terminal marker is present before completion is proven; treat this as false_finish and reopen the effective next phase"
        )

    report = {
        "task": task_code,
        "result": "pass" if not issues else "fail",
        "issues": issues,
        "warnings": warnings,
        "workflowState": {
            "result": "pass" if not issues else "fail",
            "issueCount": len(issues),
            "warningCount": len(warnings),
            "expectedNext": workflow_contract.get("expectedNext"),
            "target": workflow_contract.get("target"),
            "contractRefs": {
                "workflow": "workflow.json",
                "workflowControlState": "automind-workflow-state.json",
                "stageState": "stages/*-stage-state.json",
                "phaseSummary": "in-memory CLI guidance projection",
                "stateSummary": "runtime-state.json#stateSummary (obsolete fallback)",
            },
        },
        "terminalGuard": terminal_guard,
        "ids": {
            "requirements": list(spec_r.values()),
            "acceptanceCriteria": list(require_ac.values()),
            "testCases": list(test_tc.values()),
            "planTestCaseRefs": list(plan_tc_refs.values()),
        },
    }
    report["nextActionPrompt"] = build_workflow_next_action_prompt(report, task_code)
    return len(issues) == 0, report


def build_workflow_next_action_prompt(report: dict, task_code: str) -> str:
    """Skill-mode automation hint: turn workflow-check output into one imperative line.

    Host agents in Skill/Command mode do not run a daemon loop; they rely on
    structured prompts to decide the next step deterministically. Returning a
    single human/agent-readable instruction prevents early-stop drift ("I'll
    wait for further input") when the gate clearly says what to do next.
    """
    workflow_state = report.get("workflowState") if isinstance(report.get("workflowState"), dict) else {}
    expected = workflow_state.get("expectedNext") if isinstance(workflow_state.get("expectedNext"), list) else []
    expected_phase = expected[0].get("phase") if expected and isinstance(expected[0], dict) else None
    if report.get("result") == "pass":
        next_hint = f"Expected next phase: {expected_phase}. " if expected_phase else ""
        return (
            f"workflow-check passed for {task_code}. "
            + next_hint +
            "Proceed to Generator (implement T* per Plan.md) only when the pre-implementation review is resolved, then run "
            "context-pack and Evaluator. Do NOT stop until completion-check passes."
        )
    issues = report.get("issues") or []
    head = "; ".join(str(item) for item in issues[:3])
    return (
        f"workflow-check FAILED for {task_code}: {head}. "
        "Refine the artifact owning each issue (Brainstorm/Requirements/TestCases/Plan/pre-implementation review), "
        "then re-run workflow-check. Do NOT enter Generator until result=pass."
    )


def record_workflow_check_state(task_dir: Path, report: dict, reason: str) -> None:
    """Persist the latest workflow-check result in runtime-state and its runtime state."""
    update_runtime_state(
        task_dir,
        workflowCheck={
            "ok": report.get("result") == "pass",
            "reason": reason,
            "checkedAt": datetime.now().isoformat(timespec="seconds"),
            "issues": report.get("issues", []),
            "warnings": report.get("warnings", []),
        },
    )
