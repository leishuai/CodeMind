#!/usr/bin/env python3
"""Read-only iOS project probe for AutoMind."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
from typing import Any


def run(cmd: list[str], cwd: pathlib.Path, timeout: int = 120) -> dict[str, Any]:
    try:
        p = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=timeout)
        return {"command": cmd, "exitCode": p.returncode, "stdoutTail": p.stdout[-12000:], "stderrTail": p.stderr[-12000:]}
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = exc.stderr if isinstance(exc.stderr, str) else ""
        return {"command": cmd, "exitCode": 124, "timeout": True, "stdoutTail": out[-12000:], "stderrTail": err[-12000:]}


def rel(root: pathlib.Path, path: pathlib.Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path)


# Xcode scheme / target identifiers are alphanumerics, dot, dash, underscore,
# plus, and space-joined words. They never contain quotes, brackets, equals,
# slashes, or look like timestamps. Use this to defend against stderr noise
# (e.g. DVTProvisioningProfileManager logs) that may end up adjacent to the
# parsed list region if a caller accidentally concatenates streams.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.+\- ]{0,120}$")
_TIMESTAMP_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\b")
_NOISE_TOKENS = (
    "DVTProvisioningProfileManager",
    "Failed to load",
    "Error Domain=",
    "UserInfo=",
    "xcodebuild[",
)


def _is_identifier_line(stripped: str) -> bool:
    if not stripped:
        return False
    if _TIMESTAMP_PREFIX_RE.match(stripped):
        return False
    if any(token in stripped for token in _NOISE_TOKENS):
        return False
    return bool(_IDENT_RE.match(stripped))


def _parse_section(text: str, header: str) -> list[str]:
    items: list[str] = []
    capture = False
    blanks = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == header:
            capture = True
            blanks = 0
            continue
        if not capture:
            continue
        if not stripped:
            blanks += 1
            # Two consecutive blank lines reliably end the section even if the
            # next non-blank line is not a "Section:" header (e.g. when stderr
            # is concatenated after stdout).
            if blanks >= 2:
                break
            continue
        blanks = 0
        if stripped.endswith(":"):
            break
        if not _is_identifier_line(stripped):
            # Hit a noise line (e.g. stderr leak); stop capturing rather than
            # poisoning the list with logs.
            break
        if stripped not in items:
            items.append(stripped)
    return items


def parse_schemes(text: str) -> list[str]:
    return _parse_section(text, "Schemes:")


def parse_targets(text: str) -> list[str]:
    return _parse_section(text, "Targets:")


def extract_build_settings(text: str) -> dict[str, list[str]]:
    keys = [
        "PRODUCT_BUNDLE_IDENTIFIER",
        "DEVELOPMENT_TEAM",
        "CODE_SIGN_STYLE",
        "CODE_SIGN_IDENTITY",
        "PROVISIONING_PROFILE_SPECIFIER",
        "IPHONEOS_DEPLOYMENT_TARGET",
        "SUPPORTED_PLATFORMS",
        "SDKROOT",
    ]
    result = {k: [] for k in keys}
    for line in text.splitlines():
        for key in keys:
            m = re.match(rf"\s*{re.escape(key)}\s*=\s*(.*)", line)
            if m:
                value = m.group(1).strip()
                if value not in result[key]:
                    result[key].append(value)
    return result


def classify_settings(settings: dict[str, list[str]]) -> list[dict[str, str]]:
    issues = []
    if not any(v for v in settings.get("PRODUCT_BUNDLE_IDENTIFIER", [])):
        issues.append({"category": "needs_replan", "reason": "No PRODUCT_BUNDLE_IDENTIFIER found in selected scheme build settings."})
    teams = [v for v in settings.get("DEVELOPMENT_TEAM", []) if v]
    if not teams:
        issues.append({"category": "permission_blocked", "reason": "No DEVELOPMENT_TEAM found; physical device signing may be blocked."})
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", help="Path to iOS project root, .xcodeproj, or .xcworkspace")
    parser.add_argument("--out", required=True)
    parser.add_argument("--scheme", default="")
    parser.add_argument("--device-id", default="")
    parser.add_argument("--show-build-settings", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--xcodebuild-list", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    input_path = pathlib.Path(args.project).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"project path not found: {input_path}")
    root = input_path if input_path.is_dir() and input_path.suffix not in {".xcodeproj", ".xcworkspace"} else input_path.parent
    explicit_project = input_path if input_path.suffix == ".xcodeproj" else None
    explicit_workspace = input_path if input_path.suffix == ".xcworkspace" else None
    if explicit_project:
        projects = [input_path]
        workspaces = []
    elif explicit_workspace:
        projects = []
        workspaces = [input_path]
    else:
        # Avoid scanning huge Pods/bazel/DerivedData trees when possible.
        projects = []
        workspaces = []
        for child in sorted(root.iterdir()):
            if child.name in {"Pods", "DerivedData", "bazel_build"} or child.name.startswith("bazel-"):
                continue
            if child.suffix == ".xcodeproj":
                projects.append(child)
            elif child.suffix == ".xcworkspace":
                workspaces.append(child)
            elif child.is_dir() and child.name not in {"Pods", "DerivedData", "bazel_build"}:
                projects.extend(sorted(child.glob("*.xcodeproj"))[:10])
                workspaces.extend(sorted(child.glob("*.xcworkspace"))[:10])
        projects = projects[:40]
        workspaces = workspaces[:40]

    result: dict[str, Any] = {
        "projectRoot": str(root),
        "readOnly": True,
        "xcodeproj": [rel(root, p) for p in projects],
        "xcworkspace": [rel(root, p) for p in workspaces],
        "schemes": [],
        "targets": [],
        "selected": {},
        "buildSettings": {},
        "issues": [],
        "recommendation": [],
        "commandHints": [],
    }

    # Read-only command hint discovery for custom workspace wrapper/Bazel/Pod based repos.
    hint_names = [".custom_workspace_wrapperconfig", "custom workspace wrapper.dependencies.yml", "Podfile", "Podfile.lock", "WORKSPACE", "MODULE.bazel", "BUILD", "BUILD.bazel", ".bazelrc", "project.yml", "Rockfile"]
    hint_dirs = [root, root.parent]
    seen_hints = set()
    for hd in hint_dirs:
        if not hd.exists() or "Pods" in hd.parts or "DerivedData" in hd.parts:
            continue
        for hint_file in hint_names:
            path = hd / hint_file
            if path.exists():
                r = rel(root, path)
                if r not in seen_hints:
                    result["commandHints"].append(r)
                    seen_hints.add(r)

    # Scheme file discovery must avoid scanning huge Pods/bazel/DerivedData trees.
    scheme_roots = []
    if explicit_project:
        scheme_roots.append(explicit_project)
    elif explicit_workspace:
        scheme_roots.append(explicit_workspace)
    else:
        scheme_roots.extend(projects[:5])
        scheme_roots.extend(workspaces[:5])
    scheme_files = []
    for sr in scheme_roots:
        for sub in ["xcshareddata/xcschemes", "xcuserdata"]:
            base = sr / sub
            if base.exists():
                scheme_files.extend(sorted(base.glob("**/*.xcscheme"))[:80])
    result["schemeFiles"] = [rel(root, p) for p in scheme_files[:240]]
    file_schemes = []
    for path in scheme_files:
        name = path.stem
        if name not in file_schemes:
            file_schemes.append(name)

    selected_workspace = explicit_workspace or (workspaces[0] if workspaces else None)
    selected_project = explicit_project or (projects[0] if projects else None)
    if explicit_project:
        list_cmd = ["xcodebuild", "-list", "-project", str(explicit_project)]
        selected_kind = "project"
        selected_path = explicit_project
    elif selected_workspace:
        list_cmd = ["xcodebuild", "-list", "-workspace", str(selected_workspace)]
        selected_kind = "workspace"
        selected_path = selected_workspace
    elif selected_project:
        list_cmd = ["xcodebuild", "-list", "-project", str(selected_project)]
        selected_kind = "project"
        selected_path = selected_project
    else:
        result["issues"].append({"category": "needs_replan", "reason": "No .xcodeproj or .xcworkspace found."})
        selected_kind = "none"
        selected_path = None
        list_cmd = []

    if list_cmd and args.xcodebuild_list:
        list_run = run(list_cmd, root, args.timeout)
        result["xcodebuildList"] = list_run
        # Only parse stdout. xcodebuild's stderr can carry unrelated logs
        # (e.g. DVTProvisioningProfileManager profile load failures) which
        # otherwise leak into the schemes/targets lists when concatenated.
        text = list_run["stdoutTail"]
        result["schemes"] = parse_schemes(text)
        result["targets"] = parse_targets(text)
        if list_run["exitCode"] != 0:
            result["issues"].append({"category": "build_failure", "reason": "xcodebuild -list failed."})
    elif list_cmd and not args.xcodebuild_list:
        result["xcodebuildList"] = {"skipped": True, "reason": "--no-xcodebuild-list"}
        result["schemes"] = file_schemes
        result["targets"] = []

    def rank_scheme(name: str) -> tuple[int, int, str]:
        lower = name.lower()
        penalty = 0
        for bad in ["extension", "widget", "notification", "watch", "test", "demo"]:
            if bad in lower:
                penalty += 10
        # Prefer main app-looking short schemes without hardcoding a product.
        bonus = -2 if lower in {"app", "main"} else 0
        return (penalty + bonus, len(name), name)

    suggested_schemes = sorted(result["schemes"], key=rank_scheme)
    scheme = args.scheme or (suggested_schemes[0] if suggested_schemes else "")
    result["schemeSuggestions"] = suggested_schemes[:10]
    result["selected"] = {"kind": selected_kind, "path": rel(root, selected_path) if selected_path else "", "scheme": scheme}

    if selected_path and scheme and args.show_build_settings:
        cmd = ["xcodebuild"]
        if selected_kind == "workspace":
            cmd += ["-workspace", str(selected_path)]
        else:
            cmd += ["-project", str(selected_path)]
        cmd += ["-scheme", scheme, "-showBuildSettings"]
        if args.device_id:
            cmd += ["-destination", f"id={args.device_id}"]
        # Build settings can be long; capture full output for parsing, but keep tails in JSON.
        try:
            proc = subprocess.run(cmd, cwd=str(root), text=True, capture_output=True, timeout=args.timeout)
            full_settings_text = (proc.stdout or "") + "\n" + (proc.stderr or "")
            settings_run = {"command": cmd, "exitCode": proc.returncode, "stdoutTail": (proc.stdout or "")[-12000:], "stderrTail": (proc.stderr or "")[-12000:]}
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, str) else ""
            err = exc.stderr if isinstance(exc.stderr, str) else ""
            full_settings_text = out + "\n" + err
            settings_run = {"command": cmd, "exitCode": 124, "timeout": True, "stdoutTail": out[-12000:], "stderrTail": err[-12000:]}
        result["showBuildSettingsRun"] = settings_run
        result["buildSettings"] = extract_build_settings(full_settings_text)
        result["issues"].extend(classify_settings(result["buildSettings"]))
        if settings_run["exitCode"] != 0:
            result["issues"].append({"category": "build_failure", "reason": "xcodebuild -showBuildSettings failed for selected scheme."})

    elif selected_path and scheme and not args.show_build_settings:
        result["showBuildSettingsRun"] = {"skipped": True, "reason": "--no-show-build-settings"}

    test_targets = [t for t in result.get("targets", []) if "test" in t.lower()]
    if test_targets:
        result["recommendation"].append("Test targets found; prefer ios-xcuitest evaluator if scheme supports tests.")
    else:
        result["recommendation"].append("No obvious test target found; use build/preflight first, or add/identify a test target.")
    if result["issues"]:
        result["recommendation"].append("Resolve issues or generate AskUserQuestion before running physical evaluator.")
    else:
        result["recommendation"].append("Project discovery looks usable; generate iosApp config and run ios-preflight/ios-xcuitest or ios-probe-flow.")

    out = pathlib.Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
