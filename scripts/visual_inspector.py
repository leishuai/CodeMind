#!/usr/bin/env python3
"""Deterministic screenshot/image inspection for CodeMind.

This is the default fallback when a host model cannot inspect images. It does
not semantically understand a UI the way a vision model can; it provides bounded
technical checks: image readability, dimensions, optional crop bounds, perceptual
hash, and optional baseline comparison.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

Image = None
ImageChops = None
ImageStat = None
np = None
imagehash = None

from automind_paths import TASKS_DIR, rel_to_workspace, workspace_path


def load_visual_deps() -> str | None:
    """Load optional visual helper dependencies.

    Keep imports lazy so a missing Pillow installation can still produce the
    normal task-local `visual-inspection.json` blocked artifact instead of
    failing before CodeMind resolves the task directory.
    """
    global Image, ImageChops, ImageStat, np, imagehash
    try:
        from PIL import Image as _Image, ImageChops as _ImageChops, ImageStat as _ImageStat
    except Exception as exc:  # pragma: no cover - depends on host env
        return f"Pillow is not available: {exc}"
    Image = _Image
    ImageChops = _ImageChops
    ImageStat = _ImageStat
    try:
        import numpy as _np
    except Exception:  # pragma: no cover
        _np = None
    try:
        import imagehash as _imagehash
    except Exception:  # pragma: no cover
        _imagehash = None
    np = _np
    imagehash = _imagehash
    return None


def parse_bbox(raw: str | None) -> tuple[int, int, int, int] | None:
    if not raw:
        return None
    parts = [int(float(item.strip())) for item in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("--bbox must be x,y,width,height")
    x, y, w, h = parts
    if x < 0 or y < 0:
        raise ValueError("--bbox x/y must be non-negative")
    if w <= 0 or h <= 0:
        raise ValueError("--bbox width/height must be positive")
    return x, y, w, h


def rms_diff(a: Any, b: Any) -> float:
    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    return math.sqrt(sum(value ** 2 for value in stat.rms) / len(stat.rms))


def mse_diff(a: Any, b: Any) -> float | None:
    if np is None:
        return None
    arr_a = np.asarray(a).astype("float32")
    arr_b = np.asarray(b).astype("float32")
    return float(np.mean((arr_a - arr_b) ** 2))


def image_summary(path: Path, bbox: tuple[int, int, int, int] | None = None) -> tuple[Any, dict]:
    img = Image.open(path).convert("RGB")
    original_size = {"width": img.width, "height": img.height}
    inspected_region = None
    if bbox:
        x, y, w, h = bbox
        if x >= img.width or y >= img.height or x + w > img.width or y + h > img.height:
            raise ValueError(
                f"--bbox {x},{y},{w},{h} is outside image bounds {img.width}x{img.height}"
            )
        crop_box = (x, y, min(img.width, x + w), min(img.height, y + h))
        inspected_region = {"x": x, "y": y, "width": w, "height": h, "cropBox": list(crop_box)}
        img = img.crop(crop_box)
    stat = ImageStat.Stat(img)
    summary = {
        "path": rel_to_workspace(path),
        "originalSize": original_size,
        "inspectedSize": {"width": img.width, "height": img.height},
        "inspectedRegion": inspected_region,
        "meanRgb": [round(value, 2) for value in stat.mean],
        "extremaRgb": stat.extrema,
    }
    if imagehash is not None:
        summary["perceptualHash"] = str(imagehash.phash(img))
    return img, summary


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def blocked_report(task_code: str, iteration: int, out_path: Path, reason: str, checks: list[dict] | None = None) -> int:
    report = {
        "schema": "automind.visual_inspection.v1",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "taskCode": task_code,
        "iteration": iteration,
        "result": "blocked",
        "nextAction": "ask_user",
        "setup": "automind setup-automation-tools visual",
        "setupPolicy": "project-local Python virtualenv only; no SDKs/devices/privileged services",
        "checks": checks or [{"name": "visual_inspection", "result": "blocked", "reason": reason}],
        "qualityChecks": [{
            "id": "deterministic-visual-inspection",
            "category": "ux",
            "result": "blocked",
            "triageSource": "code_deterministic",
            "needsModelReview": False,
            "reason": reason,
            "evidence": rel_to_workspace(out_path),
            "source": "visual_inspector",
        }],
        "evidence": [{"type": "other", "path": rel_to_workspace(out_path), "note": "visual-inspection report"}],
    }
    write_json(out_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect screenshots/images with deterministic visual checks.")
    parser.add_argument("task_code")
    parser.add_argument("--iteration", type=int, default=None)
    parser.add_argument("--image", required=True, help="Screenshot/image path, absolute or workspace-relative")
    parser.add_argument("--baseline", help="Optional baseline/reference image path")
    parser.add_argument("--bbox", help="Optional crop region x,y,width,height")
    parser.add_argument("--max-rms", type=float, default=8.0, help="Maximum RMS channel difference for baseline comparison")
    parser.add_argument("--max-mse", type=float, default=None, help="Optional maximum MSE for baseline comparison")
    parser.add_argument("--min-width", type=int, default=1)
    parser.add_argument("--min-height", type=int, default=1)
    parser.add_argument(
        "--strict-size",
        action="store_true",
        help="Require the baseline and inspected image to have identical pixel size; otherwise the baseline is resized to match before comparison (design mockups rarely match device resolution).",
    )
    parser.add_argument("--output", help="Output JSON path; default logs/iter-N/visual-inspection.json")
    args = parser.parse_args()

    task_dir = TASKS_DIR / args.task_code
    iteration = args.iteration or 1
    out_path = workspace_path(args.output) if args.output else task_dir / "logs" / f"iter-{iteration}" / "visual-inspection.json"
    try:
        bbox = parse_bbox(args.bbox)
    except Exception as exc:
        return blocked_report(args.task_code, iteration, out_path, str(exc))

    image_path = workspace_path(args.image)
    checks: list[dict] = []
    evidence: list[dict] = []
    result = "pass"
    next_action = "finish"

    if not image_path.exists():
        return blocked_report(
            args.task_code,
            iteration,
            out_path,
            f"image not found: {args.image}",
            [{"name": "image_exists", "result": "blocked", "reason": f"image not found: {args.image}"}],
        )

    dep_error = load_visual_deps()
    if dep_error:
        return blocked_report(
            args.task_code,
            iteration,
            out_path,
            dep_error,
            [{"name": "visual_dependencies", "result": "blocked", "reason": dep_error}],
        )

    try:
        img, summary = image_summary(image_path, bbox)
    except Exception as exc:
        return blocked_report(
            args.task_code,
            iteration,
            out_path,
            f"image decode failed: {exc}",
            [{"name": "image_decode", "result": "blocked", "reason": str(exc)}],
        )

    evidence.append({"type": "screenshot", "path": rel_to_workspace(image_path), "note": "visual-inspection input"})
    if img.width < args.min_width or img.height < args.min_height:
        result = "fail"
        next_action = "retry_generator"
        checks.append({
            "name": "minimum_size",
            "result": "fail",
            "reason": f"inspected image size {img.width}x{img.height} is below minimum {args.min_width}x{args.min_height}",
        })
    else:
        checks.append({"name": "minimum_size", "result": "pass", "reason": f"inspected image size {img.width}x{img.height}"})

    comparison = None
    if args.baseline:
        baseline_path = workspace_path(args.baseline)
        evidence.append({"type": "screenshot", "path": rel_to_workspace(baseline_path), "note": "visual-inspection baseline"})
        if not baseline_path.exists():
            result = "blocked"
            next_action = "ask_user"
            checks.append({"name": "baseline_exists", "result": "blocked", "reason": f"baseline not found: {args.baseline}"})
        else:
            base_img, baseline_summary = image_summary(baseline_path, bbox)
            normalized_from = None
            if base_img.size != img.size:
                if args.strict_size:
                    result = "fail"
                    next_action = "retry_generator"
                    checks.append({
                        "name": "baseline_size_match",
                        "result": "fail",
                        "reason": f"image size {img.size} does not match baseline size {base_img.size} (strict-size)",
                    })
                    base_img = None
                else:
                    # Design mockups (e.g. Figma exports) rarely match the device/page
                    # screenshot resolution. Normalize the baseline to the inspected
                    # image size so RMS/MSE/phash comparison stays meaningful.
                    normalized_from = list(base_img.size)
                    base_img = base_img.resize(img.size)
                    checks.append({
                        "name": "baseline_size_match",
                        "result": "pass",
                        "reason": f"baseline resized from {normalized_from[0]}x{normalized_from[1]} to {img.width}x{img.height} for comparison",
                    })
            else:
                checks.append({"name": "baseline_size_match", "result": "pass", "reason": f"baseline size matches {img.width}x{img.height}"})
            if base_img is not None:
                rms = rms_diff(img, base_img)
                mse = mse_diff(img, base_img)
                comparison = {
                    "images": {"image": summary, "baseline": baseline_summary},
                    "normalizedBaselineFrom": normalized_from,
                    "rms": round(rms, 4),
                    "mse": round(mse, 4) if mse is not None else None,
                    "maxRms": args.max_rms,
                    "maxMse": args.max_mse,
                }
                pass_rms = rms <= args.max_rms
                pass_mse = True if args.max_mse is None or mse is None else mse <= args.max_mse
                if pass_rms and pass_mse:
                    checks.append({"name": "baseline_compare", "result": "pass", "reason": f"rms={rms:.4f}, mse={mse}"})
                else:
                    result = "fail"
                    next_action = "retry_generator"
                    checks.append({"name": "baseline_compare", "result": "fail", "reason": f"rms={rms:.4f}, mse={mse}"})

    qc_result = "pass" if result == "pass" else ("blocked" if result == "blocked" else "fail")
    report = {
        "schema": "automind.visual_inspection.v1",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "taskCode": args.task_code,
        "iteration": iteration,
        "result": result,
        "nextAction": next_action,
        "image": summary,
        "bbox": args.bbox,
        "checks": checks,
        "comparison": comparison,
        "qualityChecks": [{
            "id": "deterministic-visual-inspection",
            "category": "ux",
            "result": qc_result,
            "triageSource": "code_deterministic",
            "needsModelReview": False,
            "reason": "; ".join(check.get("reason", "") for check in checks[:3]),
            "evidence": rel_to_workspace(out_path),
            "source": "visual_inspector",
        }],
        "evidence": [*evidence, {"type": "other", "path": rel_to_workspace(out_path), "note": "visual-inspection report"}],
    }
    write_json(out_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if result == "pass" else 1 if result == "fail" else 2


if __name__ == "__main__":
    raise SystemExit(main())
