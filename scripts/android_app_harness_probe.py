#!/usr/bin/env python3
"""Reusable Android app harness probe for AutoMind.

Validates an Android APK on a real device:
- install APK
- launch package/activity
- capture current app, screenshots, UI hierarchy
- assert initial visible texts/selectors
- tap a selector or text
- assert expected text after action
- stop app

Designed for AutoMind Evaluator usage. Writes JSON summary and artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", required=True, help="Path to APK")
    parser.add_argument("--package", required=True, dest="package_name", help="Android package name")
    parser.add_argument("--activity", required=True, help="Activity name, e.g. .MainActivity")
    parser.add_argument("--out", required=True, help="Artifact output directory")
    parser.add_argument("--serial", default=None, help="Android device serial; optional for single device")
    parser.add_argument("--initial-text", action="append", default=[], help="Text expected before action; repeatable")
    parser.add_argument("--tap-desc", default=None, help="content-desc selector to tap")
    parser.add_argument("--tap-text", default=None, help="text selector to tap")
    parser.add_argument("--tap-x", type=int, default=None, help="fallback x coordinate")
    parser.add_argument("--tap-y", type=int, default=None, help="fallback y coordinate")
    parser.add_argument("--expected-text", action="append", default=[], help="Text expected after action; repeatable")
    parser.add_argument("--keep-installed", action="store_true", help="Do not uninstall before install")
    parser.add_argument("--keep-running", action="store_true", help="Do not stop app at the end")
    parser.add_argument("--launch-wait", type=float, default=2.0)
    parser.add_argument("--action-wait", type=float, default=1.0)
    return parser.parse_args()


def add_check(summary: dict[str, Any], name: str, ok: bool, detail: str = "") -> None:
    summary["checks"].append({"name": name, "ok": bool(ok), "detail": detail})


def save_json(path: Path, data: Any, artifacts: list[str]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    artifacts.append(str(path))


def text_exists(xml: str, text: str) -> bool:
    if text in xml:
        return True
    # Android Button may transform text to uppercase.
    return text.upper() in xml


def main() -> int:
    args = parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    apk = Path(args.apk).resolve()

    from adbutils import adb
    import uiautomator2 as u2

    summary: dict[str, Any] = {
        "apk": str(apk),
        "package": args.package_name,
        "activity": args.activity,
        "checks": [],
        "artifacts": [],
    }

    d = adb.device(serial=args.serial) if args.serial else adb.device()
    u = u2.connect(args.serial or d.serial)

    # Best-effort device readiness cleanup. Human readiness is still required.
    for cmd in ["input keyevent KEYCODE_WAKEUP", "wm dismiss-keyguard", "cmd statusbar collapse"]:
        try:
            d.shell(cmd, timeout=3)
        except Exception:
            pass

    # Install
    try:
        if not args.keep_installed:
            try:
                d.app_stop(args.package_name)
            except Exception:
                pass
            try:
                d.uninstall(args.package_name)
            except Exception:
                pass
        d.install(str(apk), uninstall=False)
        add_check(summary, "apk.install", True, str(apk))
    except Exception as exc:
        add_check(summary, "apk.install", False, repr(exc))
        save_json(out / "android-app-harness-summary.json", summary, summary["artifacts"])
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1

    # Launch
    d.app_start(args.package_name, args.activity)
    time.sleep(args.launch_wait)
    current = d.app_current()
    current_info = {
        "package": current.package,
        "activity": current.activity,
        "pid": getattr(current, "pid", None),
    }
    save_json(out / "current-after-launch.json", current_info, summary["artifacts"])
    add_check(summary, "app.launch", current.package == args.package_name, json.dumps(current_info, ensure_ascii=False))

    # Before action artifacts
    xml_before = u.dump_hierarchy()
    (out / "app-before-action.xml").write_text(xml_before, encoding="utf-8")
    summary["artifacts"].append(str(out / "app-before-action.xml"))
    u.screenshot(str(out / "app-before-action.png"))
    summary["artifacts"].append(str(out / "app-before-action.png"))

    target_present = f'package="{args.package_name}"' in xml_before or any(text_exists(xml_before, t) for t in args.initial_text)
    add_check(summary, "ui.target_app_present", target_present, "target package or expected initial text present")

    texts = re.findall(r'text="([^"]*)"', xml_before)
    save_json(out / "texts-before-action.json", texts[:300], summary["artifacts"])

    for text in args.initial_text:
        add_check(summary, f"ui.initial_text:{text}", text_exists(xml_before, text), "initial text present")

    # Tap/action
    action_requested = bool(args.tap_desc or args.tap_text or (args.tap_x is not None and args.tap_y is not None))
    if action_requested:
        tapped = False
        errors: list[str] = []
        if args.tap_desc:
            try:
                u.xpath(f'//*[@content-desc="{args.tap_desc}"]').click(timeout=5)
                tapped = True
            except Exception as exc:
                errors.append(f"content-desc:{repr(exc)}")
        if not tapped and args.tap_text:
            for candidate in [args.tap_text, args.tap_text.upper()]:
                try:
                    u.xpath(f'//*[@text="{candidate}"]').click(timeout=5)
                    tapped = True
                    break
                except Exception as exc:
                    errors.append(f"text={candidate}:{repr(exc)}")
        if not tapped and args.tap_x is not None and args.tap_y is not None:
            try:
                u.click(args.tap_x, args.tap_y)
                tapped = True
            except Exception as exc:
                errors.append(f"coord:{repr(exc)}")
        add_check(summary, "ui.tap", tapped, "; ".join(errors))
        time.sleep(args.action_wait)

    # After action artifacts
    xml_after = u.dump_hierarchy()
    (out / "app-after-action.xml").write_text(xml_after, encoding="utf-8")
    summary["artifacts"].append(str(out / "app-after-action.xml"))
    u.screenshot(str(out / "app-after-action.png"))
    summary["artifacts"].append(str(out / "app-after-action.png"))

    for text in args.expected_text:
        add_check(summary, f"ui.expected_text:{text}", text_exists(xml_after, text), "expected text present after action")

    if not args.keep_running:
        try:
            d.app_stop(args.package_name)
            add_check(summary, "app.stop", True, args.package_name)
        except Exception as exc:
            add_check(summary, "app.stop", False, repr(exc))

    summary["result"] = "pass" if all(c["ok"] for c in summary["checks"]) else "fail"
    save_json(out / "android-app-harness-summary.json", summary, summary["artifacts"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
