#!/usr/bin/env python3
"""Android dynamic probe flow runner for CodeAutonomy.

Executes a probe-flow.json generated from Requirements.md acceptance criteria.
This runner is intentionally small and explicit: it maps generic steps to
adbutils/uiautomator2 actions and records artifacts/checks.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
    from ui_overlay_policy import (
        classify_overlay_candidate,
        policy_from_flow,
        rank_overlay_candidates,
        sensitive_overlay_candidates,
        summarize_policy,
    )
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ui_overlay_policy import (  # type: ignore[no-redef]
        classify_overlay_candidate,
        policy_from_flow,
        rank_overlay_candidates,
        sensitive_overlay_candidates,
        summarize_policy,
    )

try:
    from probe_flow_screenshots import ensure_per_tc_default_screenshots
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from probe_flow_screenshots import ensure_per_tc_default_screenshots  # type: ignore[no-redef]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow", required=True, help="Path to probe-flow.json")
    parser.add_argument("--out", required=True, help="Artifact output directory")
    parser.add_argument("--serial", default=None, help="Android device serial")
    parser.add_argument("--dry-run", action="store_true", help="Validate and summarize the flow without Android device access")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def save_text(path: Path, text: str, artifacts: list[str]) -> None:
    path.write_text(text, encoding="utf-8")
    artifacts.append(str(path))


def save_json(path: Path, data: Any, artifacts: list[str]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    artifacts.append(str(path))


def add_check(summary: dict[str, Any], name: str, ok: bool, detail: str = "", evidence: str | None = None) -> None:
    item = {"name": name, "ok": bool(ok), "detail": detail}
    if evidence:
        item["evidence"] = evidence
    summary["checks"].append(item)


def evidence_type_for_path(path: str) -> str:
    lower = str(path or "").lower()
    if lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "screenshot"
    if lower.endswith((".xml", ".json")) and "hierarchy" in lower:
        return "hierarchy"
    if lower.endswith(".jsonl") or "trace" in lower:
        return "trace"
    if lower.endswith(".json") and ("summary" in lower or "result" in lower):
        return "summary"
    if lower.endswith((".log", ".txt")):
        return "log"
    return "other"


def string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(string_list(item))
        dedup: list[str] = []
        for item in result:
            if item and item not in dedup:
                dedup.append(item)
        return dedup
    text = str(value).strip()
    return [text] if text else []


def step_tc_hint(step: dict[str, Any]) -> str | list[str] | None:
    values = string_list(step.get("tc") or step.get("testCaseId") or step.get("testCaseIds"))
    if not values:
        return None
    return values[0] if len(values) == 1 else values


def add_evidence_index_entry(summary: dict[str, Any], path: str | None, *, ev_type: str | None = None, tc: Any = None, signal: Any = None) -> None:
    if not path:
        return
    entry: dict[str, Any] = {"path": str(path)}
    entry["type"] = ev_type or evidence_type_for_path(str(path))
    tc_values = string_list(tc)
    if tc_values:
        entry["tc"] = tc_values[0] if len(tc_values) == 1 else tc_values
    signal_values = string_list(signal)
    if signal_values:
        entry["signal"] = signal_values[0] if len(signal_values) == 1 else signal_values
    evidence_index = summary.setdefault("evidenceIndex", [])
    if entry not in evidence_index:
        evidence_index.append(entry)


def finalize_evidence_index(summary: dict[str, Any], summary_path: Path) -> None:
    add_evidence_index_entry(summary, str(summary_path), ev_type="summary", signal="probe_flow_summary")
    overlay = summary.get("uiUnblock")
    if isinstance(overlay, dict):
        add_evidence_index_entry(summary, overlay.get("evidence"), ev_type="summary", signal="overlay_unblock_summary")
        for attempt in overlay.get("attempts") or []:
            if isinstance(attempt, dict):
                add_evidence_index_entry(summary, attempt.get("beforeScreenshot"), ev_type="screenshot", signal="overlay_before")
                add_evidence_index_entry(summary, attempt.get("beforeHierarchy"), ev_type="hierarchy", signal="overlay_before_hierarchy")
                add_evidence_index_entry(summary, attempt.get("afterScreenshot"), ev_type="screenshot", signal="overlay_after")
                add_evidence_index_entry(summary, attempt.get("afterHierarchy"), ev_type="hierarchy", signal="overlay_after_hierarchy")
    for step in summary.get("stepResults") or []:
        if not isinstance(step, dict):
            continue
        signal = step.get("signal") or step.get("type") or step.get("name")
        add_evidence_index_entry(summary, step.get("evidence"), tc=step.get("tc"), signal=signal)
        add_evidence_index_entry(summary, step.get("screenshotAfter"), ev_type="screenshot", tc=step.get("tc"), signal="action_after")
        add_evidence_index_entry(summary, step.get("hierarchyAfter"), ev_type="hierarchy", tc=step.get("tc"), signal="action_after_hierarchy")
    for artifact in summary.get("artifacts") or []:
        if str(artifact).endswith("action-trace.jsonl"):
            add_evidence_index_entry(summary, str(artifact), ev_type="trace", signal="critical_action_trace")


PROOF_ACTION_TYPES = {"tap", "tap_if_present", "tap_nth", "input", "swipe", "scroll", "scroll_until_text", "extract_text", "extract_tags", "assert_state_change", "keyevent", "stop"}


def has_proof_action(steps: list[Any]) -> bool:
    """Return whether a flow contains real user/business actions beyond startup discovery."""
    return any(isinstance(step, dict) and step.get("type") in PROOF_ACTION_TYPES for step in steps)


def evaluate_strong_post_checks(post_checks: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Evaluate strong postChecks from recorded observedSignals.

    The runner keeps this lightweight: it does not invent how to satisfy a
    postCheck, but if a strong postCheck declares expected signals, the summary
    may only pass when those signals are actually observed and recorded.
    """
    rows: list[dict[str, Any]] = []
    missing_all: list[str] = []
    for item in post_checks:
        if not isinstance(item, dict):
            continue
        if str(item.get("strength") or "").strip().lower() != "strong":
            continue
        expected = [str(x).strip() for x in (item.get("expectedSignals") or []) if str(x).strip()]
        observed = [str(x).strip() for x in (item.get("observedSignals") or []) if str(x).strip()]
        missing = [signal for signal in expected if signal not in observed]
        status = "proved" if expected and not missing else "missing"
        row = {
            "type": str(item.get("type") or "strong_post_check"),
            "strength": "strong",
            "status": status,
            "expectedSignals": expected,
            "observedSignals": observed,
            "missingSignals": missing,
        }
        rows.append(row)
        missing_all.extend(missing or ([] if expected else [row["type"]]))
    return rows, missing_all


def finalize_summary_result(summary: dict[str, Any], flow: dict[str, Any]) -> None:
    """Apply lightweight semantic gates after raw step execution.

    Raw steps can prove that a device/app is reachable. Required TC proof needs
    stronger evidence: real proof actions for non-discovery flows and satisfied
    strong postChecks when declared.
    """
    steps = flow.get("steps") if isinstance(flow.get("steps"), list) else []
    post_checks = summary.get("postChecks") if isinstance(summary.get("postChecks"), list) else []
    strong_rows, missing_signals = evaluate_strong_post_checks(post_checks)
    if strong_rows:
        summary["strongPostChecks"] = strong_rows
    summary["flowKind"] = "proof" if has_proof_action(steps) else "discovery"

    raw_pass = all(item.get("ok") for item in summary.get("checks", []))
    if not raw_pass:
        summary["result"] = "fail"
        return
    if missing_signals:
        summary["result"] = "partial"
        summary["semanticVerdict"] = {
            "result": "partial",
            "reason": "strong postCheck evidence is missing; continue refining/executing the proof flow",
            "missingEvidence": missing_signals,
        }
        add_check(
            summary,
            "strong postChecks",
            False,
            "missing strong postCheck evidence: " + ", ".join(missing_signals[:8]),
        )
        return
    if summary.get("flowKind") == "discovery" and post_checks:
        summary["result"] = "partial"
        summary["semanticVerdict"] = {
            "result": "partial",
            "reason": "startup/discovery flow succeeded, but no proof action path was executed",
            "missingEvidence": ["proof action path"],
        }
        add_check(summary, "proof action path", False, "discovery-only flow cannot prove a required runtime TC")
        return
    summary["result"] = "pass"


def text_exists(xml: str, text: str) -> bool:
    return text in xml or text.upper() in xml


def selector_to_xpath(selector: dict[str, Any]) -> str | None:
    if not selector:
        return None
    if selector.get("xpath"):
        return selector["xpath"]
    if selector.get("desc"):
        return f'//*[@content-desc="{selector["desc"]}"]'
    if selector.get("resource_id"):
        return f'//*[@resource-id="{selector["resource_id"]}"]'
    if selector.get("text"):
        return f'//*[@text="{selector["text"]}"]'
    return None


def analyze_hierarchy(xml: str, app_package: str) -> dict[str, Any]:
    """Return a small accessibility-tree health summary for target UI checks."""
    result: dict[str, Any] = {
        "appPackage": app_package,
        "totalNodes": 0,
        "appNodes": 0,
        "systemNodes": 0,
        "visibleAppTexts": [],
        "visibleAppResourceIds": [],
        "packages": {},
    }
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        result["parseError"] = str(exc)
        return result

    for node in root.iter("node"):
        result["totalNodes"] += 1
        pkg = node.attrib.get("package") or ""
        if pkg:
            result["packages"][pkg] = result["packages"].get(pkg, 0) + 1
        if pkg == app_package:
            result["appNodes"] += 1
            text = (node.attrib.get("text") or "").strip()
            rid = (node.attrib.get("resource-id") or "").strip()
            if text and len(result["visibleAppTexts"]) < 20:
                result["visibleAppTexts"].append(text)
            if rid and len(result["visibleAppResourceIds"]) < 20:
                result["visibleAppResourceIds"].append(rid)
        elif pkg.startswith("com.android.systemui") or pkg == "android":
            result["systemNodes"] += 1
    return result


def parse_bounds(value: str) -> list[int] | None:
    """Parse Android bounds like [0,1][2,3]."""
    if not value:
        return None
    import re
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", value)
    if not m:
        return None
    return [int(m.group(i)) for i in range(1, 5)]


def bounds_center(bounds: list[int] | None) -> dict[str, int] | None:
    if not bounds or len(bounds) != 4:
        return None
    return {"x": int((bounds[0] + bounds[2]) / 2), "y": int((bounds[1] + bounds[3]) / 2)}


def node_summary(node: ET.Element) -> dict[str, Any]:
    bounds = parse_bounds(node.attrib.get("bounds", ""))
    return {
        "text": node.attrib.get("text") or "",
        "resource_id": node.attrib.get("resource-id") or "",
        "resourceId": node.attrib.get("resource-id") or "",
        "content_desc": node.attrib.get("content-desc") or "",
        "contentDesc": node.attrib.get("content-desc") or "",
        "class": node.attrib.get("class") or "",
        "className": node.attrib.get("class") or "",
        "package": node.attrib.get("package") or "",
        "clickable": node.attrib.get("clickable") or "",
        "enabled": node.attrib.get("enabled") or "",
        "bounds": bounds,
        "center": bounds_center(bounds),
    }


def selector_matches_node(node: ET.Element, selector: dict[str, Any]) -> bool:
    if not selector:
        return False
    if selector.get("resource_id") and node.attrib.get("resource-id") == selector.get("resource_id"):
        return True
    if selector.get("id") and node.attrib.get("resource-id") == selector.get("id"):
        return True
    if selector.get("text") and node.attrib.get("text") == selector.get("text"):
        return True
    if selector.get("textContains") and str(selector.get("textContains")) in (node.attrib.get("text") or ""):
        return True
    if selector.get("desc") and node.attrib.get("content-desc") == selector.get("desc"):
        return True
    if selector.get("descContains") and str(selector.get("descContains")) in (node.attrib.get("content-desc") or ""):
        return True
    if selector.get("class") and node.attrib.get("class") == selector.get("class"):
        return True
    if selector.get("clickable") is not None and str(node.attrib.get("clickable") or "").lower() == str(selector.get("clickable")).lower():
        return True
    return False


def resolve_node_from_xml(xml: str, selector: dict[str, Any]) -> dict[str, Any] | None:
    if not xml or not selector:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    for node in root.iter("node"):
        if selector_matches_node(node, selector):
            return node_summary(node)
    return None


def resolve_nodes_from_xml(xml: str, selector: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    rows: list[dict[str, Any]] = []
    for node in root.iter("node"):
        if not selector or selector_matches_node(node, selector):
            rows.append(node_summary(node))
    return rows


def overlay_candidate_nodes_from_xml(xml: str, app_package: str) -> list[dict[str, Any]]:
    """Extract actionable Android UI nodes for safe overlay-unblock scanning."""
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    rows: list[dict[str, Any]] = []
    for node in root.iter("node"):
        summary = node_summary(node)
        if not summary.get("center"):
            continue
        if str(summary.get("enabled") or "").lower() == "false":
            continue
        # SystemUI and app-owned dialogs are both relevant. Avoid unrelated
        # third-party package nodes unless the package is missing.
        pkg = str(summary.get("package") or "")
        if pkg and app_package and pkg not in {app_package, "android"} and not pkg.startswith("com.android."):
            continue
        values = [
            str(summary.get("text") or "").strip(),
            str(summary.get("content_desc") or "").strip(),
            str(summary.get("resource_id") or "").strip(),
        ]
        if not any(values):
            continue
        rows.append(summary)
    return rows


def extract_visible_texts(xml: str) -> list[str]:
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    values: list[str] = []
    seen: set[str] = set()
    for node in root.iter("node"):
        for attr in ("text", "content-desc"):
            value = (node.attrib.get(attr) or "").strip()
            if value and value not in seen:
                seen.add(value)
                values.append(value)
    return values


def extract_texts_near_anchor(xml: str, anchor_text: str = "", *, below_only: bool = False) -> list[str]:
    nodes = resolve_nodes_from_xml(xml)
    if not anchor_text:
        return [text for n in nodes for text in [str(n.get("text") or n.get("content_desc") or "").strip()] if text]
    anchor_bottom: int | None = None
    for n in nodes:
        text = str(n.get("text") or "")
        desc = str(n.get("content_desc") or "")
        if anchor_text in text or anchor_text in desc:
            bounds = n.get("bounds")
            if isinstance(bounds, list) and len(bounds) == 4:
                anchor_bottom = int(bounds[3])
                break
    if anchor_text and anchor_bottom is None:
        return []
    values: list[str] = []
    seen: set[str] = set()
    for n in nodes:
        value = str(n.get("text") or n.get("content_desc") or "").strip()
        if not value or value == anchor_text or value in seen:
            continue
        if below_only and anchor_bottom is not None:
            bounds = n.get("bounds")
            if not (isinstance(bounds, list) and len(bounds) == 4 and int(bounds[1]) >= anchor_bottom):
                continue
        seen.add(value)
        values.append(value)
    return values


def direction_to_swipe(direction: str, width: int = 1080, height: int = 1920) -> tuple[int, int, int, int]:
    direction = (direction or "up").lower()
    cx = int(width / 2)
    cy = int(height / 2)
    if direction == "down":
        return cx, int(height * 0.30), cx, int(height * 0.78)
    if direction == "left":
        return int(width * 0.78), cy, int(width * 0.22), cy
    if direction == "right":
        return int(width * 0.22), cy, int(width * 0.78), cy
    return cx, int(height * 0.78), cx, int(height * 0.30)


def classify_tags(values: list[str]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    stop_words = {"Overview", "目录", "章节", "Play", "全部", "展开", "收起"}
    for raw in values:
        value = raw.strip()
        if not value or value in stop_words or value in seen:
            continue
        # Keep short label-like strings. This intentionally avoids long intro
        # paragraphs while retaining Chinese genre/status tags.
        if 1 <= len(value) <= 12 and "。" not in value and "，" not in value and "\n" not in value:
            seen.add(value)
            tags.append(value)
    return tags


def find_node_at_point(xml: str, x: int, y: int) -> dict[str, Any] | None:
    if not xml:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    for node in root.iter("node"):
        bounds = parse_bounds(node.attrib.get("bounds", ""))
        if not bounds:
            continue
        if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]:
            area = max(1, (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]))
            summary = node_summary(node)
            if best is None or area < best[0]:
                best = (area, summary)
    return best[1] if best else None


def is_critical_action(step_type: str | None, step: dict[str, Any]) -> bool:
    if step.get("critical") is True:
        return True
    if step.get("screenshotAfter") is True or step.get("evidence", {}).get("screenshotAfter") is True:
        return True
    return step_type in {"launch", "tap", "tap_if_present", "input", "swipe", "keyevent"}


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")


def auto_unblock_overlay(
    *,
    u: Any,
    out: Path,
    summary: dict[str, Any],
    trace_path: Path,
    package: str,
    policy: dict[str, Any],
    dump_fn,
    screenshot_fn,
    reason: str,
    max_attempts_remaining: int,
) -> tuple[dict[str, Any], int]:
    """Safely dismiss common overlays and record auditable evidence.

    The runner only clicks candidates classified as safe by the shared policy.
    Sensitive or ambiguous controls are recorded but not clicked.
    """
    result: dict[str, Any] = {
        "enabled": bool(policy.get("enabled")),
        "reason": reason,
        "result": "disabled",
        "attempts": [],
    }
    if not policy.get("enabled") or max_attempts_remaining <= 0:
        return result, 0

    attempts_used = 0
    for attempt in range(1, max_attempts_remaining + 1):
        attempts_used += 1
        prefix = f"overlay-unblock-{reason}-{int(time.time() * 1000)}-{attempt}"
        errors: list[str] = []
        before_xml = safe_capture(f"{prefix}-before-hierarchy", dump_fn, errors) or ""
        before_shot = safe_capture(f"{prefix}-before", screenshot_fn, errors)
        elements = overlay_candidate_nodes_from_xml(before_xml, package)
        visible_texts = extract_visible_texts(before_xml)[:40]
        context_policy = {**policy, "contextTexts": visible_texts}
        safe_candidates = rank_overlay_candidates(elements, context_policy)
        sensitive_candidates = sensitive_overlay_candidates(elements, context_policy)
        attempt_row: dict[str, Any] = {
            "attempt": attempt,
            "reason": reason,
            "beforeHierarchy": str(out / f"{prefix}-before-hierarchy.xml") if before_xml else None,
            "beforeScreenshot": before_shot,
            "visibleTexts": visible_texts,
            "safeCandidates": [
                {
                    "text": item.get("text"),
                    "contentDesc": item.get("content_desc") or item.get("contentDesc"),
                    "resourceId": item.get("resource_id") or item.get("resourceId"),
                    "className": item.get("class") or item.get("className"),
                    "package": item.get("package"),
                    "bounds": item.get("bounds"),
                    "center": item.get("center"),
                    "classification": item.get("classification"),
                }
                for item in safe_candidates[:5]
            ],
            "sensitiveCandidates": [
                {
                    "text": item.get("text"),
                    "contentDesc": item.get("content_desc") or item.get("contentDesc"),
                    "resourceId": item.get("resource_id") or item.get("resourceId"),
                    "classification": item.get("classification"),
                }
                for item in sensitive_candidates[:5]
            ],
        }
        if errors:
            attempt_row["captureWarnings"] = errors

        chosen = safe_candidates[0] if safe_candidates else None
        if not chosen:
            attempt_row["result"] = "blocked_sensitive" if sensitive_candidates else "no_safe_overlay"
            result["attempts"].append(attempt_row)
            result["result"] = attempt_row["result"]
            if sensitive_candidates:
                result["sensitiveCandidates"] = attempt_row["sensitiveCandidates"]
            break

        center = chosen.get("center") if isinstance(chosen.get("center"), dict) else {}
        try:
            x = int(center["x"])
            y = int(center["y"])
            u.click(x, y)
            time.sleep(0.6)
            after_xml = safe_capture(f"{prefix}-after-hierarchy", dump_fn, errors) or ""
            after_shot = safe_capture(f"{prefix}-after", screenshot_fn, errors)
            attempt_row.update({
                "result": "dismissed",
                "chosen": {
                    "text": chosen.get("text"),
                    "contentDesc": chosen.get("content_desc") or chosen.get("contentDesc"),
                    "resourceId": chosen.get("resource_id") or chosen.get("resourceId"),
                    "className": chosen.get("class") or chosen.get("className"),
                    "bounds": chosen.get("bounds"),
                    "center": center,
                    "classification": chosen.get("classification") or classify_overlay_candidate(chosen, policy),
                },
                "tapPoint": {"x": x, "y": y},
                "afterHierarchy": str(out / f"{prefix}-after-hierarchy.xml") if after_xml else None,
                "afterScreenshot": after_shot,
                "afterHierarchyAnalysis": analyze_hierarchy(after_xml, package) if after_xml else {},
            })
            trace_entry = {
                "stepIndex": f"overlay-{len(summary.get('actionTrace', [])) + 1}",
                "name": "Auto-unblock safe overlay",
                "type": "overlay_unblock",
                "intent": f"Dismiss safe non-destructive overlay before {reason}",
                "critical": False,
                "platform": "android",
                "backend": "uiautomator2",
                "policy": summarize_policy(policy),
                "classification": attempt_row["chosen"].get("classification"),
                "tapPoint": attempt_row["tapPoint"],
                "evidenceBefore": {
                    "hierarchy": attempt_row.get("beforeHierarchy"),
                    "screenshot": attempt_row.get("beforeScreenshot"),
                },
                "evidenceAfter": {
                    "hierarchy": attempt_row.get("afterHierarchy"),
                    "screenshot": attempt_row.get("afterScreenshot"),
                },
                "ok": True,
                "detail": "safe overlay dismissed",
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            append_jsonl(trace_path, trace_entry)
            result["attempts"].append(attempt_row)
            result["result"] = "dismissed"
            # Try another round only when configured to handle stacked overlays.
            if not policy.get("betweenActions"):
                break
        except Exception as exc:
            attempt_row["result"] = "tap_failed"
            attempt_row["error"] = repr(exc)
            result["attempts"].append(attempt_row)
            result["result"] = "tap_failed"
            break
    return result, attempts_used


def safe_capture(label: str, capture_fn, errors: list[str]) -> str | None:
    try:
        return capture_fn(label)
    except Exception as exc:
        errors.append(f"capture {label} failed: {exc!r}")
        return None


def find_adb_binary() -> str | None:
    candidates = [
        shutil.which("adb"),
        str(Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / "adb"),
        str(Path.home() / "Android" / "Sdk" / "platform-tools" / "adb"),
    ]
    for item in candidates:
        if item and Path(item).exists():
            return item
    return None


def adb_shell_command(adb_bin: str | None, serial: str | None, shell_args: list[str], timeout: float = 10) -> dict[str, Any]:
    if not adb_bin:
        return {"ok": False, "cmd": "adb <missing>", "returncode": None, "stdout": "", "stderr": "adb binary not found"}
    cmd = [adb_bin]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(["shell", *shell_args])
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        return {
            "ok": proc.returncode == 0,
            "cmd": " ".join(cmd),
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "cmd": " ".join(cmd),
            "returncode": None,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": f"timeout after {timeout}s",
        }


def launch_app_with_fallbacks(
    d: Any,
    package: str,
    activity: str | None,
    serial: str | None,
    artifacts_dir: Path,
    artifacts: list[str],
    wait_seconds: float = 2,
) -> tuple[bool, dict[str, Any], str]:
    """Launch app robustly and record each launch strategy.

    uiautomator2/adbutils app_start can be flaky on some vendor ROMs. Fall back
    to explicit am start and monkey launcher intent before declaring launcher
    failure. These are non-destructive foreground launch operations.
    """
    attempts: list[dict[str, Any]] = []

    def current_app() -> dict[str, Any]:
        try:
            cur = d.app_current()
            return {"package": cur.package, "activity": cur.activity, "pid": getattr(cur, "pid", None)}
        except Exception as exc:
            return {"package": "", "activity": "", "pid": None, "error": str(exc)}

    def record(method: str, action) -> bool:
        row: dict[str, Any] = {"method": method}
        try:
            result = action()
            if isinstance(result, dict):
                row.update(result)
            else:
                row["result"] = result
        except Exception as exc:
            row["error"] = str(exc)
        time.sleep(wait_seconds)
        row["currentApp"] = current_app()
        row["ok"] = row["currentApp"].get("package") == package
        attempts.append(row)
        return bool(row["ok"])

    if record("adbutils.app_start", lambda: d.app_start(package, activity)):
        path = artifacts_dir / "launch-attempts.json"
        save_json(path, attempts, artifacts)
        return True, attempts[-1]["currentApp"], str(path)

    adb_bin = find_adb_binary()
    if activity:
        component = activity if "/" in activity else f"{package}/{activity}"
        if record("adb.am_start.component", lambda: adb_shell_command(adb_bin, serial, ["am", "start", "-W", "-n", component], timeout=15)):
            path = artifacts_dir / "launch-attempts.json"
            save_json(path, attempts, artifacts)
            return True, attempts[-1]["currentApp"], str(path)

    if record("adb.monkey.launcher", lambda: adb_shell_command(adb_bin, serial, ["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"], timeout=15)):
        path = artifacts_dir / "launch-attempts.json"
        save_json(path, attempts, artifacts)
        return True, attempts[-1]["currentApp"], str(path)

    path = artifacts_dir / "launch-attempts.json"
    save_json(path, attempts, artifacts)
    return False, attempts[-1]["currentApp"] if attempts else current_app(), str(path)


def page_condition_matches(node: ET.Element, condition: dict[str, Any]) -> bool:
    """Match a minimal page-signature condition against one Android node."""
    if not isinstance(condition, dict):
        return False
    mappings = {
        "resource_id": "resource-id",
        "text": "text",
        "desc": "content-desc",
        "content_desc": "content-desc",
        "class": "class",
        "package": "package",
    }
    for key, attr in mappings.items():
        expected = condition.get(key)
        if expected is not None and node.attrib.get(attr) != str(expected):
            return False
    contains = condition.get("textContains") or condition.get("containsText")
    if contains is not None and str(contains) not in (node.attrib.get("text") or ""):
        return False
    desc_contains = condition.get("descContains") or condition.get("contentDescContains")
    if desc_contains is not None and str(desc_contains) not in (node.attrib.get("content-desc") or ""):
        return False
    return any(key in condition for key in [*mappings, "textContains", "containsText", "descContains", "contentDescContains"])


def evaluate_page_signature(xml: str, signature: dict[str, Any], app_package: str = "") -> dict[str, Any]:
    """Evaluate minimal Page/State Signature against Android hierarchy XML.

    Contract:
    - required: all conditions must match at least one node
    - anyOf: at least minAnyOf conditions must match
    - forbidden: no condition may match
    """
    signature = signature or {}
    required = signature.get("required") or []
    any_of = signature.get("anyOf") or signature.get("any_of") or []
    forbidden = signature.get("forbidden") or []
    min_any = int(signature.get("minAnyOf") or signature.get("min_any_of") or (1 if any_of else 0))
    try:
        root = ET.fromstring(xml or "")
        nodes = list(root.iter("node"))
    except ET.ParseError as exc:
        return {"ok": False, "error": f"invalid hierarchy XML: {exc}", "required": [], "anyOf": [], "forbidden": []}

    def eval_conditions(items: list[Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in items:
            cond = item if isinstance(item, dict) else {"text": str(item)}
            matches = [node_summary(node) for node in nodes if page_condition_matches(node, cond)]
            rows.append({"condition": cond, "matched": bool(matches), "matches": matches[:3]})
        return rows

    required_rows = eval_conditions(required)
    any_rows = eval_conditions(any_of)
    forbidden_rows = eval_conditions(forbidden)
    required_ok = all(row["matched"] for row in required_rows)
    any_count = sum(1 for row in any_rows if row["matched"])
    any_ok = any_count >= min_any
    forbidden_ok = not any(row["matched"] for row in forbidden_rows)
    analysis = analyze_hierarchy(xml, app_package) if xml else {}
    ok = required_ok and any_ok and forbidden_ok
    return {
        "ok": ok,
        "name": signature.get("name") or signature.get("page") or signature.get("state") or "page",
        "requiredOk": required_ok,
        "anyOfOk": any_ok,
        "forbiddenOk": forbidden_ok,
        "anyOfMatched": any_count,
        "minAnyOf": min_any,
        "required": required_rows,
        "anyOf": any_rows,
        "forbidden": forbidden_rows,
        "hierarchy": analysis,
    }


def main() -> int:
    args = parse_args()
    flow_path = Path(args.flow)
    flow = load_json(flow_path)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if flow.get("platform") != "android":
        raise SystemExit(f"android_probe_flow_runner only supports platform=android, got {flow.get('platform')}")

    # Default: each runtime/UI testcase referenced by the flow captures at least
    # one screenshot, so report.html can show per-TC screenshot evidence.
    ensure_per_tc_default_screenshots(flow.get("steps") if isinstance(flow.get("steps"), list) else [])

    app = flow.get("app", {})
    package = app.get("package")
    activity = app.get("activity")
    apk = app.get("apk")
    if not package or not activity:
        raise SystemExit("flow.app.package and flow.app.activity are required for Android")

    if args.dry_run:
        summary: dict[str, Any] = {
            "flow": str(flow_path),
            "platform": "android",
            "app": app,
            "dryRun": True,
            "checks": [],
            "artifacts": [],
            "evidenceIndex": [],
            "stepResults": [],
            "postChecks": flow.get("postChecks") if isinstance(flow.get("postChecks"), list) else [],
        }
        for index, step in enumerate(flow.get("steps", []), start=1):
            step_type = step.get("type")
            step_name = step.get("name") or f"{index}:{step_type}"
            detail = f"dry-run accepted step type={step_type}"
            add_check(summary, step_name, True, detail)
            summary["stepResults"].append({"index": index, "type": step_type, "name": step_name, "ok": True, "detail": detail})
        finalize_summary_result(summary, flow)
        finalize_evidence_index(summary, out / "probe-flow-summary.json")
        save_json(out / "probe-flow-summary.json", summary, summary["artifacts"])
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["result"] == "pass" else 1

    from adbutils import adb
    import uiautomator2 as u2

    d = adb.device(serial=args.serial) if args.serial else adb.device()
    u = u2.connect(args.serial or d.serial)
    try:
        info = u.window_size()
        screen_width, screen_height = int(info[0]), int(info[1])
    except Exception:
        screen_width, screen_height = 1080, 1920

    summary: dict[str, Any] = {
        "flow": str(flow_path),
        "platform": "android",
        "app": app,
        "checks": [],
        "artifacts": [],
        "evidenceIndex": [],
        "stepResults": [],
        "actionTrace": [],
        "postChecks": flow.get("postChecks") if isinstance(flow.get("postChecks"), list) else [],
    }
    trace_path = out / "action-trace.jsonl"
    trace_path.write_text("")
    summary["artifacts"].append(str(trace_path))

    last_xml = ""
    ui_unblock_policy = policy_from_flow(flow)
    summary["uiUnblock"] = {
        **summarize_policy(ui_unblock_policy),
        "result": "not_run" if ui_unblock_policy.get("enabled") else "disabled",
        "attempts": [],
    }
    ui_unblock_attempts_used = 0

    def dump(label: str) -> str:
        xml = u.dump_hierarchy()
        path = out / f"{label}.xml"
        save_text(path, xml, summary["artifacts"])
        return xml

    def screenshot(label: str) -> str:
        path = out / f"{label}.png"
        u.screenshot(str(path))
        summary["artifacts"].append(str(path))
        return str(path)

    # Best effort readiness cleanup.
    for cmd in ["input keyevent KEYCODE_WAKEUP", "wm dismiss-keyguard", "cmd statusbar collapse"]:
        try:
            d.shell(cmd, timeout=3)
        except Exception:
            pass

    def maybe_auto_unblock(reason: str) -> dict[str, Any] | None:
        nonlocal ui_unblock_attempts_used
        if not ui_unblock_policy.get("enabled"):
            return None
        if str(ui_unblock_policy.get("mode") or "runner").strip().casefold() != "runner":
            result = {
                "enabled": True,
                "mode": ui_unblock_policy.get("mode"),
                "reason": reason,
                "result": "planned_not_executed",
                "detail": "uiUnblock.mode is not 'runner'; Android runner records the policy but does not click overlays.",
                "attempts": [],
            }
            summary["uiUnblock"]["result"] = result["result"]
            summary["uiUnblock"].setdefault("attempts", [])
            evidence_path = out / "overlay-unblock.json"
            summary["uiUnblock"]["evidence"] = str(evidence_path)
            evidence_path.write_text(json.dumps(summary["uiUnblock"], ensure_ascii=False, indent=2, default=str))
            if str(evidence_path) not in summary["artifacts"]:
                summary["artifacts"].append(str(evidence_path))
            return result
        remaining = int(ui_unblock_policy.get("maxAttempts") or 0) - ui_unblock_attempts_used
        if remaining <= 0:
            summary["uiUnblock"]["result"] = "attempt_budget_exhausted"
            return None
        result, used = auto_unblock_overlay(
            u=u,
            out=out,
            summary=summary,
            trace_path=trace_path,
            package=package,
            policy=ui_unblock_policy,
            dump_fn=dump,
            screenshot_fn=screenshot,
            reason=reason,
            max_attempts_remaining=remaining,
        )
        ui_unblock_attempts_used += used
        if result:
            summary["uiUnblock"]["result"] = result.get("result", summary["uiUnblock"].get("result"))
            summary["uiUnblock"].setdefault("attempts", []).extend(result.get("attempts") or [])
            if result.get("sensitiveCandidates"):
                summary["uiUnblock"]["sensitiveCandidates"] = result.get("sensitiveCandidates")
            evidence_path = out / "overlay-unblock.json"
            summary["uiUnblock"]["evidence"] = str(evidence_path)
            evidence_path.write_text(json.dumps(summary["uiUnblock"], ensure_ascii=False, indent=2, default=str))
            if str(evidence_path) not in summary["artifacts"]:
                summary["artifacts"].append(str(evidence_path))
        return result

    for index, step in enumerate(flow.get("steps", []), start=1):
        step_type = step.get("type")
        step_name = step.get("name") or f"{index}:{step_type}"
        ok = True
        detail = ""
        evidence = None
        action_errors: list[str] = []
        critical = is_critical_action(step_type, step)
        trace_entry: dict[str, Any] = {
            "stepIndex": index,
            "name": step_name,
            "type": step_type,
            "intent": step.get("intent") or step.get("goal") or step_name,
            "selector": step.get("selector"),
            "critical": critical,
            "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        before_xml = ""
        if critical:
            before_xml = safe_capture(f"action-{index:02d}-before-hierarchy", dump, action_errors) or ""
            before_shot = safe_capture(f"action-{index:02d}-before", screenshot, action_errors)
            trace_entry["evidenceBefore"] = {
                "hierarchy": str(out / f"action-{index:02d}-before-hierarchy.xml") if before_xml else None,
                "screenshot": before_shot,
            }
            if before_xml and isinstance(step.get("selector"), dict):
                trace_entry["resolvedNodeBefore"] = resolve_node_from_xml(before_xml, step.get("selector") or {})

        try:
            if step_type == "install":
                if not apk:
                    raise ValueError("install step requires flow.app.apk")
                try:
                    d.app_stop(package)
                except Exception:
                    pass
                # Destructive device actions must be opt-in. Reinstalling with
                # `-r` is safe for the normal path; uninstalling may clear app
                # data and should only happen when the flow explicitly asks for
                # it, usually after human authorization.
                if step.get("uninstall", False):
                    try:
                        d.uninstall(package)
                    except Exception:
                        pass
                d.install(str(Path(apk).resolve()), uninstall=False)
                detail = apk

            elif step_type == "launch":
                if step.get("forceStop") is True or step.get("stopBeforeLaunch") is True:
                    try:
                        d.app_stop(package)
                        trace_entry["stopBeforeLaunch"] = True
                        time.sleep(float(step.get("stopWait", 0.8)))
                    except Exception as exc:
                        trace_entry["stopBeforeLaunchError"] = str(exc)
                ok, data, launch_evidence = launch_app_with_fallbacks(
                    d,
                    package,
                    activity,
                    args.serial or d.serial,
                    out,
                    summary["artifacts"],
                    wait_seconds=float(step.get("wait", step.get("seconds", 2))),
                )
                evidence = str(out / f"step-{index}-current-app.json")
                save_json(Path(evidence), data, summary["artifacts"])
                trace_entry["launchAttempts"] = launch_evidence
                detail = json.dumps(data, ensure_ascii=False)
                if ok and ui_unblock_policy.get("afterLaunch"):
                    unblock_result = maybe_auto_unblock(f"after-launch-step-{index}")
                    if unblock_result:
                        trace_entry["autoUnblock"] = {
                            "result": unblock_result.get("result"),
                            "attempts": len(unblock_result.get("attempts") or []),
                        }

            elif step_type == "current_app":
                current = d.app_current()
                data = {"package": current.package, "activity": current.activity, "pid": getattr(current, "pid", None)}
                evidence = str(out / f"step-{index}-current-app.json")
                save_json(Path(evidence), data, summary["artifacts"])
                expected = step.get("expected") or package
                ok = data["package"] == expected
                detail = json.dumps(data, ensure_ascii=False)

            elif step_type == "dump_hierarchy":
                last_xml = dump(f"step-{index}-hierarchy")
                evidence = str(out / f"step-{index}-hierarchy.xml")
                detail = f"chars={len(last_xml)}"

            elif step_type == "screenshot":
                label = step.get("output") or f"step-{index}-screenshot"
                evidence = screenshot(label.replace("/", "_"))
                detail = evidence

            elif step_type == "assert_text":
                text = step.get("text") or step.get("expected")
                if not text:
                    raise ValueError("assert_text requires text or expected")
                timeout = float(step.get("timeout", 1))
                interval = float(step.get("interval", 1))
                deadline = time.time() + max(timeout, 0)
                attempt = 0
                while True:
                    attempt += 1
                    last_xml = dump(f"step-{index}-assert-text-attempt-{attempt}")
                    ok = text_exists(last_xml, text)
                    if ok or time.time() >= deadline:
                        break
                    time.sleep(interval)
                evidence = str(out / f"step-{index}-assert-text-attempt-{attempt}.xml")
                detail = f"{text}; attempts={attempt}"

            elif step_type == "assert_selector":
                selector = step.get("selector", {})
                xpath = selector_to_xpath(selector)
                if not xpath:
                    raise ValueError("assert_selector requires selector")
                timeout = float(step.get("timeout", 1))
                interval = float(step.get("interval", 1))
                deadline = time.time() + max(timeout, 0)
                attempt = 0
                while True:
                    attempt += 1
                    last_xml = dump(f"step-{index}-assert-selector-attempt-{attempt}")
                    ok = bool(u.xpath(xpath).exists)
                    if ok or time.time() >= deadline:
                        break
                    time.sleep(interval)
                evidence = str(out / f"step-{index}-assert-selector-attempt-{attempt}.xml")
                detail = f"{xpath}; attempts={attempt}"

            elif step_type == "assert_app_hierarchy":
                min_nodes = int(step.get("minNodes", 1))
                timeout = float(step.get("timeout", 1))
                interval = float(step.get("interval", 1))
                deadline = time.time() + max(timeout, 0)
                attempts: list[dict[str, Any]] = []
                analysis: dict[str, Any] = {}
                attempt = 0
                while True:
                    attempt += 1
                    last_xml = dump(f"step-{index}-assert-app-hierarchy-attempt-{attempt}")
                    analysis = analyze_hierarchy(last_xml, package)
                    attempts.append(analysis)
                    ok = analysis.get("appNodes", 0) >= min_nodes
                    if ok or time.time() >= deadline:
                        break
                    time.sleep(interval)
                evidence = str(out / f"step-{index}-app-hierarchy-analysis.json")
                save_json(Path(evidence), {"attempts": attempts, "final": analysis}, summary["artifacts"])
                detail = json.dumps({
                    "appNodes": analysis.get("appNodes", 0),
                    "totalNodes": analysis.get("totalNodes", 0),
                    "packages": analysis.get("packages", {}),
                    "minNodes": min_nodes,
                    "attempts": len(attempts),
                }, ensure_ascii=False)

            elif step_type == "assert_page":
                signature = step.get("pageSignature") or step.get("signature") or step.get("expect") or {}
                if not isinstance(signature, dict):
                    raise ValueError("assert_page requires pageSignature/signature object")
                timeout = float(step.get("timeout", 1))
                interval = float(step.get("interval", 1))
                deadline = time.time() + max(timeout, 0)
                attempts: list[dict[str, Any]] = []
                attempt = 0
                page_eval: dict[str, Any] = {}
                while True:
                    attempt += 1
                    last_xml = dump(f"step-{index}-assert-page-attempt-{attempt}")
                    page_eval = evaluate_page_signature(last_xml, signature, package)
                    attempts.append(page_eval)
                    ok = bool(page_eval.get("ok"))
                    if ok or time.time() >= deadline:
                        break
                    time.sleep(interval)
                evidence = str(out / f"step-{index}-page-signature.json")
                save_json(Path(evidence), {"attempts": attempts, "final": page_eval}, summary["artifacts"])
                detail = json.dumps({
                    "page": page_eval.get("name"),
                    "requiredOk": page_eval.get("requiredOk"),
                    "anyOfOk": page_eval.get("anyOfOk"),
                    "forbiddenOk": page_eval.get("forbiddenOk"),
                    "anyOfMatched": page_eval.get("anyOfMatched"),
                    "minAnyOf": page_eval.get("minAnyOf"),
                    "attempts": len(attempts),
                }, ensure_ascii=False)

            elif step_type in {"tap", "tap_if_present"}:
                if ui_unblock_policy.get("beforeActions"):
                    unblock_result = maybe_auto_unblock(f"before-step-{index}-{step_type}")
                    if unblock_result:
                        trace_entry["autoUnblock"] = {
                            "result": unblock_result.get("result"),
                            "attempts": len(unblock_result.get("attempts") or []),
                        }
                    if before_xml:
                        before_xml = safe_capture(f"action-{index:02d}-before-post-unblock-hierarchy", dump, action_errors) or before_xml
                        trace_entry["evidenceBeforePostUnblock"] = {
                            "hierarchy": str(out / f"action-{index:02d}-before-post-unblock-hierarchy.xml"),
                        }
                selector = step.get("selector", {})
                xpath = selector_to_xpath(selector)
                tapped = False
                skipped = False
                errors: list[str] = []
                timeout = float(step.get("timeout", 5 if step_type == "tap" else 1))
                if xpath:
                    candidates = [xpath]
                    if selector.get("text"):
                        candidates.append(f'//*[@text="{selector["text"].upper()}"]')
                    for candidate in candidates:
                        try:
                            if step_type == "tap_if_present" and not u.xpath(candidate).exists:
                                errors.append(f"{candidate}: not present")
                                continue
                            resolved = trace_entry.get("resolvedNodeBefore") if isinstance(trace_entry.get("resolvedNodeBefore"), dict) else None
                            u.xpath(candidate).click(timeout=timeout)
                            tapped = True
                            detail = candidate
                            trace_entry["matchedSelector"] = candidate
                            if resolved:
                                trace_entry["resolvedNode"] = resolved
                                trace_entry["tapPoint"] = resolved.get("center")
                            break
                        except Exception as exc:
                            errors.append(f"{candidate}: {exc!r}")
                if not tapped and "x" in selector and "y" in selector:
                    # Coordinate fallback is only safe for mandatory tap; optional
                    # tap-if-present requires a selector to determine presence.
                    if step_type == "tap":
                        x = int(selector["x"])
                        y = int(selector["y"])
                        u.click(x, y)
                        tapped = True
                        detail = f"coord=({selector['x']},{selector['y']})"
                        trace_entry["tapPoint"] = {"x": x, "y": y}
                        if before_xml:
                            trace_entry["resolvedNode"] = find_node_at_point(before_xml, x, y)
                    else:
                        errors.append("coordinate fallback skipped for tap_if_present")
                if step_type == "tap_if_present" and not tapped:
                    skipped = True
                    ok = True
                    detail = "optional target not present; skipped" + ("; " + "; ".join(errors) if errors else "")
                else:
                    ok = tapped
                    if not ok:
                        detail = "; ".join(errors)
                time.sleep(float(step.get("wait", step.get("seconds", 1))))

            elif step_type == "input":
                if ui_unblock_policy.get("beforeActions"):
                    unblock_result = maybe_auto_unblock(f"before-step-{index}-{step_type}")
                    if unblock_result:
                        trace_entry["autoUnblock"] = {
                            "result": unblock_result.get("result"),
                            "attempts": len(unblock_result.get("attempts") or []),
                        }
                selector = step.get("selector", {})
                xpath = selector_to_xpath(selector)
                text = step.get("text", "")
                if xpath:
                    u.xpath(xpath).set_text(text)
                    trace_entry["matchedSelector"] = xpath
                    if isinstance(trace_entry.get("resolvedNodeBefore"), dict):
                        trace_entry["resolvedNode"] = trace_entry.get("resolvedNodeBefore")
                else:
                    u.send_keys(text)
                detail = text

            elif step_type == "swipe":
                if ui_unblock_policy.get("beforeActions"):
                    unblock_result = maybe_auto_unblock(f"before-step-{index}-{step_type}")
                    if unblock_result:
                        trace_entry["autoUnblock"] = {
                            "result": unblock_result.get("result"),
                            "attempts": len(unblock_result.get("attempts") or []),
                        }
                u.swipe(int(step["sx"]), int(step["sy"]), int(step["ex"]), int(step["ey"]), duration=step.get("duration"))
                detail = f"({step['sx']},{step['sy']}) -> ({step['ex']},{step['ey']})"
                trace_entry["swipe"] = {"start": {"x": int(step["sx"]), "y": int(step["sy"])}, "end": {"x": int(step["ex"]), "y": int(step["ey"])}}

            elif step_type == "scroll":
                if ui_unblock_policy.get("beforeActions"):
                    unblock_result = maybe_auto_unblock(f"before-step-{index}-{step_type}")
                    if unblock_result:
                        trace_entry["autoUnblock"] = {
                            "result": unblock_result.get("result"),
                            "attempts": len(unblock_result.get("attempts") or []),
                        }
                direction = str(step.get("direction") or "up")
                sx, sy, ex, ey = direction_to_swipe(direction, screen_width, screen_height)
                u.swipe(sx, sy, ex, ey, duration=step.get("duration", 0.4))
                detail = f"direction={direction}; ({sx},{sy}) -> ({ex},{ey})"
                trace_entry["swipe"] = {"direction": direction, "start": {"x": sx, "y": sy}, "end": {"x": ex, "y": ey}}
                time.sleep(float(step.get("wait", step.get("seconds", 1))))

            elif step_type == "scroll_until_text":
                if ui_unblock_policy.get("beforeActions"):
                    unblock_result = maybe_auto_unblock(f"before-step-{index}-{step_type}")
                    if unblock_result:
                        trace_entry["autoUnblock"] = {
                            "result": unblock_result.get("result"),
                            "attempts": len(unblock_result.get("attempts") or []),
                        }
                text = str(step.get("text") or step.get("expected") or "").strip()
                if not text:
                    raise ValueError("scroll_until_text requires text or expected")
                direction = str(step.get("direction") or "up")
                max_swipes = int(step.get("maxSwipes") or 5)
                found = False
                attempts: list[dict[str, Any]] = []
                for attempt in range(1, max_swipes + 1):
                    last_xml = dump(f"step-{index}-scroll-until-text-attempt-{attempt}")
                    found = text_exists(last_xml, text)
                    attempts.append({"attempt": attempt, "found": found})
                    if found:
                        break
                    sx, sy, ex, ey = direction_to_swipe(direction, screen_width, screen_height)
                    u.swipe(sx, sy, ex, ey, duration=step.get("duration", 0.4))
                    time.sleep(float(step.get("interval", step.get("wait", 0.8))))
                ok = found
                evidence = str(out / f"step-{index}-scroll-until-text.json")
                save_json(Path(evidence), {"text": text, "direction": direction, "attempts": attempts}, summary["artifacts"])
                detail = f"text={text}; found={found}; attempts={len(attempts)}"

            elif step_type == "tap_nth":
                if ui_unblock_policy.get("beforeActions"):
                    unblock_result = maybe_auto_unblock(f"before-step-{index}-{step_type}")
                    if unblock_result:
                        trace_entry["autoUnblock"] = {
                            "result": unblock_result.get("result"),
                            "attempts": len(unblock_result.get("attempts") or []),
                        }
                    if before_xml:
                        before_xml = safe_capture(f"action-{index:02d}-before-post-unblock-hierarchy", dump, action_errors) or before_xml
                selector = step.get("selector", {})
                nth = int(step.get("index") or 0)
                last_xml = before_xml or dump(f"step-{index}-tap-nth-before-hierarchy")
                nodes = resolve_nodes_from_xml(last_xml, selector if isinstance(selector, dict) else {})
                clickable_nodes = [n for n in nodes if str(n.get("enabled") or "").lower() != "false" and n.get("center")]
                if nth >= len(clickable_nodes):
                    raise ValueError(f"tap_nth index {nth} out of range for {len(clickable_nodes)} matched nodes")
                node = clickable_nodes[nth]
                center = node.get("center") or {}
                u.click(int(center["x"]), int(center["y"]))
                detail = f"index={nth}; center=({center['x']},{center['y']}); text={node.get('text') or node.get('content_desc') or ''}"
                trace_entry["resolvedNode"] = node
                trace_entry["tapPoint"] = center
                time.sleep(float(step.get("wait", step.get("seconds", 1))))

            elif step_type in {"extract_text", "extract_tags"}:
                label = str(step.get("output") or f"step-{index}-{step_type}").replace("/", "_")
                last_xml = dump(f"{label}-hierarchy")
                scope = str(step.get("scope") or "visible")
                near = str(step.get("nearText") or (step.get("selector") or {}).get("nearText") or "")
                values = extract_visible_texts(last_xml)
                if near:
                    values = extract_texts_near_anchor(last_xml, near, below_only=scope in {"below", "below_text", "bottom", "bottom_visible"})
                extracted: dict[str, Any] = {"scope": scope, "nearText": near, "texts": values}
                if step_type == "extract_tags":
                    extracted["tags"] = classify_tags(values)
                evidence = str(out / f"{label}.json")
                save_json(Path(evidence), extracted, summary["artifacts"])
                trace_entry["extracted"] = extracted
                detail = json.dumps({k: extracted[k] for k in extracted if k in {"scope", "nearText", "tags"}}, ensure_ascii=False)

            elif step_type == "assert_state_change":
                signal = str(step.get("signal") or step.get("expected") or "").strip()
                if not signal:
                    raise ValueError("assert_state_change requires signal/expected")
                # Lightweight generic state-change assertion: compare visible
                # text/resource evidence before/after the previous action. More
                # domain-specific signals (audio progress/media session/logs) are
                # recorded by stronger postChecks or project-native verifiers.
                after_xml = dump(f"step-{index}-state-change-hierarchy")
                before_values = set(extract_visible_texts(before_xml)) if before_xml else set()
                after_values = set(extract_visible_texts(after_xml))
                added = sorted(after_values - before_values)
                removed = sorted(before_values - after_values)
                expected_text = str(step.get("text") or "")
                ok = bool(added or removed or (expected_text and expected_text in after_values))
                evidence = str(out / f"step-{index}-state-change.json")
                save_json(Path(evidence), {"signal": signal, "addedTexts": added, "removedTexts": removed, "expectedText": expected_text}, summary["artifacts"])
                detail = f"signal={signal}; added={added[:5]}; removed={removed[:5]}"

            elif step_type == "wait":
                seconds = float(step.get("seconds", 1))
                time.sleep(seconds)
                detail = f"{seconds}s"

            elif step_type == "keyevent":
                key = str(step.get("key") or step.get("code") or "BACK")
                if key.upper() == "BACK":
                    shell_key = "KEYCODE_BACK"
                elif key.upper().startswith("KEYCODE_"):
                    shell_key = key.upper()
                else:
                    shell_key = key
                d.shell(f"input keyevent {shell_key}", timeout=3)
                detail = shell_key
                trace_entry["keyevent"] = shell_key
                time.sleep(float(step.get("wait", step.get("seconds", 1))))

            elif step_type == "stop":
                d.app_stop(package)
                detail = package

            else:
                raise ValueError(f"Unsupported step type: {step_type}")

        except Exception as exc:
            ok = False
            detail = repr(exc)

        if not ok and step.get("optional") is True:
            ok = True
            detail = f"optional step did not pass; treated as skipped: {detail}"
        if critical:
            after_xml = safe_capture(f"action-{index:02d}-after-hierarchy", dump, action_errors) or ""
            after_shot = safe_capture(f"action-{index:02d}-after", screenshot, action_errors)
            trace_entry["evidenceAfter"] = {
                "hierarchy": str(out / f"action-{index:02d}-after-hierarchy.xml") if after_xml else None,
                "screenshot": after_shot,
            }
            if after_shot:
                evidence = evidence or after_shot
            if after_xml:
                trace_entry["afterHierarchyAnalysis"] = analyze_hierarchy(after_xml, package)
        if not ok:
            try:
                failure_shot = screenshot(f"step-{index}-failure-screenshot")
                if evidence:
                    detail = f"{detail}; failureScreenshot={failure_shot}"
                else:
                    evidence = failure_shot
            except Exception:
                pass
        soft_failure = (not ok and step.get("continueOnFail") is True)
        trace_entry["ok"] = ok
        trace_entry["detail"] = detail
        trace_entry["evidence"] = evidence
        if soft_failure:
            trace_entry["softFailure"] = True
            trace_entry["ruledOut"] = step.get("ruledOut") or [step_name]
            trace_entry["remainingHypotheses"] = step.get("remainingHypotheses") or []
        if action_errors:
            trace_entry["captureWarnings"] = action_errors
        trace_entry["finishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if critical:
            append_jsonl(trace_path, trace_entry)
            summary["actionTrace"].append(trace_entry)
        check_ok = True if soft_failure else ok
        check_detail = ("continueOnFail soft-failed; " + detail) if soft_failure else detail
        add_check(summary, step_name, check_ok, check_detail, evidence)
        step_result = {"index": index, "type": step_type, "name": step_name, "ok": ok, "detail": detail, "evidence": evidence}
        tc_hint = step_tc_hint(step)
        if tc_hint:
            step_result["tc"] = tc_hint
        if soft_failure:
            step_result["softFailure"] = True
        if critical:
            step_result["actionTrace"] = str(trace_path)
            step_result["screenshotAfter"] = (trace_entry.get("evidenceAfter") or {}).get("screenshot")
            step_result["hierarchyAfter"] = (trace_entry.get("evidenceAfter") or {}).get("hierarchy")
        summary["stepResults"].append(step_result)
        if not ok and step.get("continueOnFail") is not True:
            break

    finalize_summary_result(summary, flow)
    finalize_evidence_index(summary, out / "probe-flow-summary.json")
    save_json(out / "probe-flow-summary.json", summary, summary["artifacts"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
