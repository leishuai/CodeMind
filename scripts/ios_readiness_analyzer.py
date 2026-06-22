#!/usr/bin/env python3
"""Analyze iOS screenshot OCR for generic readiness blockers.

v1 classifies common blockers (privacy/permission/login/loading) from OCR text.
It does not click anything.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from automind_paths import RUNTIME_ROOT, TASKS_DIR, WORKSPACE_ROOT
from state_files import write_runtime_state

ROOT = RUNTIME_ROOT
TASKS = TASKS_DIR

BLOCKERS = {
    "privacy_consent_blocked": ["\u9690\u79c1\u653f\u7b56", "\u7528\u6237\u534f\u8bae", "\u4e2a\u4eba\u4fe1\u606f\u4fdd\u62a4", "\u540c\u610f", "\u4e0d\u540c\u610f", "Privacy Policy", "User Agreement"],
    "permission_blocked": ["Would Like", "Notifications", "Allow", "Don’t Allow", "Don't Allow", "\u4e0d\u5141\u8bb8", "\u662f\u5426\u5141\u8bb8", "\u9700\u8981\u6743\u9650", "\u76f8\u673a\u6743\u9650", "\u9ea6\u514b\u98ce\u6743\u9650", "\u7167\u7247\u6743\u9650", "\u5b9a\u4f4d\u6743\u9650"],
    "login_state_blocked": ["\u767b\u5f55", "\u624b\u673a\u53f7", "\u9a8c\u8bc1\u7801", "\u5fae\u4fe1\u767b\u5f55", "Apple \u767b\u5f55", "Sign in", "Login"],
    "loading_state": ["\u52a0\u8f7d\u4e2d", "\u6b63\u5728\u52a0\u8f7d", "Loading"],
}


def run_ocr(image: Path, log_dir: Path) -> tuple[str, dict[str, Any]]:
    out_base = log_dir / "ocr"
    lang_cmd = subprocess.run(["tesseract", "--list-langs"], text=True, capture_output=True)
    langs = lang_cmd.stdout + lang_cmd.stderr
    lang = "chi_sim+eng" if "chi_sim" in langs else "eng"
    proc = subprocess.run(["tesseract", str(image), str(out_base), "-l", lang, "--psm", "6"], text=True, capture_output=True, timeout=120)
    text_path = out_base.with_suffix(".txt")
    text = text_path.read_text(errors="replace") if text_path.exists() else ""
    (log_dir / "ocr.log").write_text(proc.stdout + proc.stderr)
    return text, {"lang": lang, "exitCode": proc.returncode, "textPath": str(text_path)}


def classify(text: str) -> tuple[str, str, list[str]]:
    hits: list[str] = []
    for category, keys in BLOCKERS.items():
        matched = [k for k in keys if k.lower() in text.lower()]
        if matched:
            hits.extend(matched)
            if category == "privacy_consent_blocked":
                return "blocked", category, hits
            if category == "permission_blocked":
                return "blocked", category, hits
            if category == "login_state_blocked":
                return "blocked", category, hits
            if category == "loading_state":
                return "in_progress", category, hits
    if text.strip():
        return "pass", "no_common_blocker_detected", []
    return "blocked", "ocr_no_text", []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("task_code")
    ap.add_argument("--iteration", type=int, default=1)
    ap.add_argument("--image", required=True)
    ap.add_argument("--bundle-id", default="")
    args = ap.parse_args()

    task_dir = TASKS / args.task_code
    log_dir = task_dir / "logs" / f"iter-{args.iteration}"
    log_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    image = Path(args.image)
    if not image.is_absolute():
        image = (WORKSPACE_ROOT / image).resolve()

    if not (task_dir / "Requirements.md").exists():
        (task_dir / "Requirements.md").write_text("# Requirements - iOS Readiness Analyzer\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — iOS readiness analysis\n- **AC-001**: Analyze screenshot OCR for common readiness blockers without clicking.\n  - Verification method: ios-readiness-analyzer / TC-F01\n")
    if not (task_dir / "Plan.md").exists():
        (task_dir / "Plan.md").write_text("# Plan\n\nRun OCR on screenshot, classify privacy/permission/login/loading blockers, and write structured evaluation.\n")
    if not (task_dir / "Validation.md").exists():
        (task_dir / "Validation.md").write_text("# Validation\n")

    if not image.exists():
        text, ocr_meta = "", {"error": f"image not found: {image}"}
        result, category, hits = "blocked", "missing_screenshot", []
    else:
        text, ocr_meta = run_ocr(image, log_dir)
        result, category, hits = classify(text)

    if category == "privacy_consent_blocked":
        summary = "Readiness blocked by privacy/terms consent screen; explicit authorization is required before tapping Agree/Allow/Continue."
        ask = {
            "category": "unauthorized_destructive_or_sensitive",
            "question": "A privacy/terms consent screen is blocking iOS verification. May AutoMind tap the positive Agree/Allow/Continue control for this app and this verification run only?",
            "options": [
                {"id": "authorize_positive_consent", "label": "Authorize consent tap", "recommended": True},
                {"id": "manual_handle", "label": "I will handle it manually"},
                {"id": "stop", "label": "Stop verification"},
            ],
        }
        auto_unblock = {
            "allowed": False,
            "category": "positive_privacy_or_terms_consent",
            "scope": "Do not tap Agree/Allow/Continue, reject/deny, login, payment, delete/reset, external upload, or permission/account-grant controls unless the pre-implementation review explicitly authorized that exact action; otherwise use ask_user.",
        }
    elif result == "pass":
        summary = "No common OCR blocker detected; screenshot appears ready for the next task-specific assertions."
        ask = None
        auto_unblock = None
    else:
        summary = f"Readiness classified as {category}; see OCR evidence."
        ask = None
        auto_unblock = None

    payload = {
        "iteration": args.iteration,
        "result": result,
        "category": category,
        "summary": summary,
        "bundleId": args.bundle_id,
        "image": str(image),
        "ocr": ocr_meta,
        "matchedKeywords": hits,
        "textExcerpt": text[:2000],
        "askUserQuestion": ask,
        "autoUnblock": auto_unblock,
    }
    (log_dir / "ocr.txt").write_text(text)
    (log_dir / "ios-readiness-summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    (log_dir / "evaluator.log").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    (log_dir / "env.json").write_text(json.dumps({"timestamp": datetime.now().isoformat(timespec="seconds"), "taskCode": args.task_code, "image": str(image), "bundleId": args.bundle_id}, ensure_ascii=False, indent=2) + "\n")
    (log_dir / "commands.md").write_text(f"# Commands\n\n```bash\n./automind.sh ios-readiness-analyze {args.task_code} --image {args.image} --bundle-id {args.bundle_id}\n```\n")
    failed = [] if result == "pass" else [{"name": "readiness", "category": category, "reason": summary, "evidence": f"logs/iter-{args.iteration}/ios-readiness-summary.json"}]
    next_action = "finish" if result == "pass" else ("retry_generator" if auto_unblock else "ask_user")
    if ask:
        next_action = "ask_user"
    evaluation = {"iteration": args.iteration, "result": result, "nextAction": next_action, "summary": summary, "failedChecks": failed, "evidence": [{"type":"other", "note":"ocr", "path": f"logs/iter-{args.iteration}/ocr.txt"}, {"type":"other", "note":"readiness-summary", "path": f"logs/iter-{args.iteration}/ios-readiness-summary.json"}], "autoUnblock": auto_unblock}
    if ask:
        evaluation["askUserQuestion"] = ask
    (task_dir / "evaluation.json").write_text(json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n")
    write_runtime_state(task_dir, {"taskId": args.task_code, "taskType": "ios", "status": "finished" if result == "pass" else ("human_input_pending" if evaluation["nextAction"] == "ask_user" else "blocked"), "iteration": args.iteration, "nextAction": evaluation["nextAction"], "updatedAt": datetime.now().isoformat(timespec="seconds")})
    (task_dir / "Validation.md").open("a").write(f"\n## Iteration {args.iteration} - iOS readiness analyzer\n\n- Environment: image={image}; bundleId={args.bundle_id}\n- Commands: see `logs/iter-{args.iteration}/commands.md`\n- Result: {result.upper()}\n- Category: `{category}`\n- Summary: {summary}\n- Evidence: `logs/iter-{args.iteration}/ocr.txt`, `ios-readiness-summary.json`\n- Reusable findings: Screenshot/OCR can identify privacy, permission, login-state, and other readiness blockers; safe close/skip/later/dismiss overlays may auto-unblock, while privacy/terms agree/allow, reject/deny/login/payment/delete/external-upload/account-grant actions require explicit authorization or ask_user.\n- Avoid repeating: Do not treat privacy consent blockers as target-screen failure; route consent to explicit authorization/ask_user and use auto-unblock only for safe dismiss overlays.\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
