#!/usr/bin/env python3
"""Android device validation probe for CodeMind.

This script validates the lightweight Android backend:
- adbutils device discovery/info/current app/screenshot/logcat/hierarchy
- uiautomator2 connect/info/screenshot/hierarchy/basic key action

It writes artifacts to the provided output directory and prints a JSON summary.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="Artifact output directory")
    parser.add_argument("--serial", default=None, help="Android device serial; optional for single device")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from adbutils import adb
    import uiautomator2 as u2

    summary: dict = {
        "checks": [],
        "artifacts": [],
    }

    def check(name: str, ok: bool, detail: str = ""):
        summary["checks"].append({"name": name, "ok": ok, "detail": detail})

    infos = adb.list(extended=True)
    device_rows = [
        {"serial": i.serial, "state": i.state, "transport_id": i.transport_id}
        for i in infos
    ]
    (out / "adb-devices.json").write_text(json.dumps(device_rows, ensure_ascii=False, indent=2))
    summary["artifacts"].append(str(out / "adb-devices.json"))
    check("adbutils.device_discovery", bool(device_rows), json.dumps(device_rows, ensure_ascii=False))

    d = adb.device(serial=args.serial) if args.serial else adb.device()
    device_info = {
        "serial": d.serial,
        "state": d.get_state(),
        "brand": d.getprop("ro.product.brand"),
        "model": d.getprop("ro.product.model"),
        "sdk": d.getprop("ro.build.version.sdk"),
        "release": d.getprop("ro.build.version.release"),
    }
    (out / "device-info.json").write_text(json.dumps(device_info, ensure_ascii=False, indent=2))
    summary["artifacts"].append(str(out / "device-info.json"))
    check("adbutils.device_info", device_info["state"] == "device", json.dumps(device_info, ensure_ascii=False))

    current = d.app_current()
    current_info = {
        "package": current.package,
        "activity": current.activity,
        "pid": getattr(current, "pid", None),
    }
    (out / "current-app.json").write_text(json.dumps(current_info, ensure_ascii=False, indent=2))
    summary["artifacts"].append(str(out / "current-app.json"))
    check("adbutils.current_app", bool(current.package), json.dumps(current_info, ensure_ascii=False))

    shot = out / "adbutils-screenshot.png"
    img = d.screenshot()
    img.save(shot)
    summary["artifacts"].append(str(shot))
    check("adbutils.screenshot", shot.exists() and shot.stat().st_size > 0, f"{shot} {img.size}")

    log = out / "adbutils-logcat.log"
    evt = d.logcat(log, clear=True, command="logcat -v time")
    time.sleep(2)
    evt.stop()
    summary["artifacts"].append(str(log))
    check("adbutils.logcat", log.exists() and log.stat().st_size > 0, f"{log} bytes={log.stat().st_size}")

    xml_path = out / "adbutils-hierarchy.xml"
    xml = d.dump_hierarchy()
    xml_path.write_text(xml, encoding="utf-8")
    summary["artifacts"].append(str(xml_path))
    check("adbutils.dump_hierarchy", xml.startswith("<?xml") and len(xml) > 100, f"{xml_path} chars={len(xml)}")

    u = u2.connect(args.serial or d.serial)
    u_info = {
        "serial": u.serial,
        "info": u.info,
        "device_info": u.device_info,
        "current": u.app_current(),
        "window_size": u.window_size(),
    }
    (out / "uiautomator2-info.json").write_text(json.dumps(u_info, ensure_ascii=False, indent=2, default=str))
    summary["artifacts"].append(str(out / "uiautomator2-info.json"))
    check("uiautomator2.connect", bool(u.serial), u.serial)

    u_shot = out / "uiautomator2-screenshot.png"
    u.screenshot(str(u_shot))
    summary["artifacts"].append(str(u_shot))
    check("uiautomator2.screenshot", u_shot.exists() and u_shot.stat().st_size > 0, f"{u_shot} bytes={u_shot.stat().st_size}")

    u_xml_path = out / "uiautomator2-hierarchy.xml"
    u_xml = u.dump_hierarchy()
    u_xml_path.write_text(u_xml, encoding="utf-8")
    summary["artifacts"].append(str(u_xml_path))
    check("uiautomator2.dump_hierarchy", u_xml.startswith("<?xml") and len(u_xml) > 100, f"{u_xml_path} chars={len(u_xml)}")

    u.press("home")
    after_home = u.app_current()
    (out / "after-home.json").write_text(json.dumps(after_home, ensure_ascii=False, indent=2, default=str))
    summary["artifacts"].append(str(out / "after-home.json"))
    check("uiautomator2.press_home", bool(after_home.get("package")), json.dumps(after_home, ensure_ascii=False))

    passed = all(c["ok"] for c in summary["checks"])
    summary["result"] = "pass" if passed else "fail"
    (out / "probe-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
