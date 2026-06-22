#!/usr/bin/env python3
"""Read-only iOS signing/install preflight.

Classifies whether a new build can preserve an installed app by signing with the
installed app's Team/application-identifier, or whether user decision is needed.
"""
from __future__ import annotations

import argparse
import json
import plistlib
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from automind_paths import RUNTIME_ROOT, TASKS_DIR, WORKSPACE_ROOT
from state_files import write_runtime_state

ROOT = RUNTIME_ROOT
TASKS = TASKS_DIR


@dataclass
class ProfileInfo:
    path: str
    name: str
    team_ids: list[str]
    app_id: str
    expires: str
    expired: bool
    device_count: int
    includes_device: bool | None
    cert_count: int


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as exc:
        return 124, "", repr(exc)


def parse_team_from_identity_line(line: str) -> dict[str, str] | None:
    # Example: 1) HASH "Apple Development: name (TEAMID)"
    m = re.search(r'\)\s+([A-F0-9]{40})\s+"(.+?)"', line)
    if not m:
        return None
    name = m.group(2)
    team = ""
    mt = re.search(r'\(([A-Z0-9]{10})\)"?$', name)
    if mt:
        team = mt.group(1)
    return {"hash": m.group(1), "name": name, "teamId": team}


def list_identities() -> list[dict[str, str]]:
    code, out, err = run(["security", "find-identity", "-v", "-p", "codesigning"])
    identities = []
    for line in (out + "\n" + err).splitlines():
        item = parse_team_from_identity_line(line)
        if item:
            identities.append(item)
    return identities


def detect_xcode_accounts() -> dict[str, Any]:
    """Detect whether Xcode is signed in with an Apple ID, and which Teams that
    account can manage.

    This is the missing piece that earlier preflight runs never checked: the
    old code only looked at codesigning identities (`security find-identity`)
    and `.mobileprovision` files, so it could not tell whether Automatic signing
    (`-allowProvisioningUpdates`) had any chance of working. Automatic signing
    REQUIRES a signed-in Apple ID, and it can only manage profiles for Teams
    the account actually belongs to.

    Source of truth: Xcode persists logged-in accounts and their Teams under the
    `IDEProvisioningTeams` key in `com.apple.dt.Xcode` defaults. We read it
    read-only via `defaults read` and parse account emails + team ids.

    Returns:
        signedIn: bool — at least one Apple ID account record exists in Xcode.
        accounts: list of account emails.
        managedTeamIds: sorted list of team ids the signed-in account(s) manage.
        teamDetails: per-team {teamID, teamName, teamType, isFreeProvisioningTeam}.
        raw: best-effort note when the store could not be read.
    """
    info: dict[str, Any] = {
        "signedIn": False,
        "accounts": [],
        "managedTeamIds": [],
        "teamDetails": [],
    }
    code, out, err = run(["defaults", "read", "com.apple.dt.Xcode", "IDEProvisioningTeams"])
    if code != 0 or not out.strip():
        # Key absent => no Apple ID configured in Xcode (or Xcode never run).
        info["raw"] = "IDEProvisioningTeams absent: no Apple ID account configured in Xcode."
        return info
    # The defaults output is the old-style NeXTSTEP plist. Parse pragmatically:
    # top-level keys are quoted account emails; each contains team dicts with
    # teamID / teamName / teamType / isFreeProvisioningTeam.
    accounts = re.findall(r'^\s*"([^"]+@[^"]+)"\s*=', out, re.MULTILINE)
    if accounts:
        info["accounts"] = sorted(set(accounts))
        info["signedIn"] = True
    teams: dict[str, dict[str, str]] = {}
    # Split into team-dict blocks and pull fields out of each.
    for block in re.split(r"\}\s*,?", out):
        m_team = re.search(r"teamID\s*=\s*\"?([A-Z0-9]{10})\"?", block)
        if not m_team:
            continue
        tid = m_team.group(1)
        name_m = re.search(r'teamName\s*=\s*"?([^";\n]+?)"?\s*;', block)
        type_m = re.search(r'teamType\s*=\s*"?([^";\n]+?)"?\s*;', block)
        free_m = re.search(r"isFreeProvisioningTeam\s*=\s*(\d)", block)
        teams[tid] = {
            "teamID": tid,
            "teamName": (name_m.group(1).strip() if name_m else ""),
            "teamType": (type_m.group(1).strip() if type_m else ""),
            "isFreeProvisioningTeam": bool(free_m and free_m.group(1) == "1"),
        }
    info["managedTeamIds"] = sorted(teams.keys())
    info["teamDetails"] = [teams[k] for k in sorted(teams.keys())]
    if teams and not info["signedIn"]:
        # Teams present without a parseable email still implies a configured account.
        info["signedIn"] = True
    return info


def load_profile(path: Path, device_id: str | None) -> ProfileInfo | None:
    code, out, err = run(["/usr/bin/security", "cms", "-D", "-i", str(path)], timeout=15)
    if code != 0 or not out:
        return None
    try:
        pl = plistlib.loads(out.encode())
    except Exception:
        return None
    ent = pl.get("Entitlements", {}) or {}
    app_id = str(ent.get("application-identifier", ""))
    team_ids = [str(x) for x in (pl.get("TeamIdentifier") or [])]
    devices = [str(x) for x in (pl.get("ProvisionedDevices") or [])]
    exp = pl.get("ExpirationDate")
    expired = False
    exp_s = ""
    if exp:
        if exp.tzinfo is None:
            now = datetime.now()
        else:
            now = datetime.now(exp.tzinfo)
        expired = exp < now
        exp_s = exp.isoformat()
    includes = None
    if device_id:
        includes = device_id in devices
    return ProfileInfo(
        path=str(path),
        name=str(pl.get("Name", "")),
        team_ids=team_ids,
        app_id=app_id,
        expires=exp_s,
        expired=expired,
        device_count=len(devices),
        includes_device=includes,
        cert_count=len(pl.get("DeveloperCertificates") or []),
    )


def appid_matches(profile_app_id: str, expected_app_id: str) -> bool:
    if profile_app_id == expected_app_id:
        return True
    if profile_app_id.endswith(".*"):
        prefix = profile_app_id[:-1]
        return expected_app_id.startswith(prefix)
    return False


def collect_profiles(search_roots: list[Path], team_id: str, expected_app_id: str, device_id: str | None) -> list[ProfileInfo]:
    profiles: list[ProfileInfo] = []
    seen: set[str] = set()
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.glob("*.mobileprovision"):
            rp = str(path.resolve())
            if rp in seen:
                continue
            seen.add(rp)
            info = load_profile(path, device_id)
            if not info:
                continue
            if team_id in info.team_ids or info.app_id.startswith(team_id + "."):
                if appid_matches(info.app_id, expected_app_id):
                    profiles.append(info)
    profiles.sort(key=lambda p: (p.expired, not bool(p.includes_device), p.expires), reverse=False)
    return profiles


def collect_all_profiles(search_roots: list[Path], device_id: str | None) -> list[ProfileInfo]:
    """Load every readable .mobileprovision under the search roots, unfiltered.

    Used by --discover so the generator can see *all* signing material that
    already exists, not only profiles that match a specific Team/bundle id.
    Searches each root recursively so profiles bundled deep inside a project
    tree (e.g. `<repo>/Pods/.../*.mobileprovision`) are also found.
    """
    profiles: list[ProfileInfo] = []
    seen: set[str] = set()
    for root in search_roots:
        if not root.exists():
            continue
        candidates = list(root.glob("*.mobileprovision"))
        if root.is_dir():
            candidates += list(root.rglob("*.mobileprovision"))
        for path in candidates:
            rp = str(path.resolve())
            if rp in seen:
                continue
            seen.add(rp)
            info = load_profile(path, device_id)
            if info:
                profiles.append(info)
    profiles.sort(key=lambda p: (p.expired, not bool(p.includes_device), p.expires), reverse=False)
    return profiles


def discover_xcode_signing(search_roots: list[Path]) -> dict[str, Any]:
    """Read-only scan of project files for already-configured signing settings.

    Greps `*.pbxproj` for DEVELOPMENT_TEAM / CODE_SIGN_STYLE /
    PROVISIONING_PROFILE_SPECIFIER and any ExportOptions plist for teamID,
    so re-signing can reuse exactly what the project already declares.
    """
    teams: set[str] = set()
    styles: set[str] = set()
    profile_specifiers: set[str] = set()
    pbxproj_files: list[str] = []
    export_team_ids: set[str] = set()
    seen: set[str] = set()
    for root in search_roots:
        if not root.exists() or not root.is_dir():
            continue
        for pbx in root.rglob("*.pbxproj"):
            rp = str(pbx.resolve())
            if rp in seen:
                continue
            seen.add(rp)
            pbxproj_files.append(rp)
            try:
                content = pbx.read_text(errors="replace")
            except Exception:
                continue
            for m in re.finditer(r"DEVELOPMENT_TEAM\s*=\s*\"?([A-Z0-9]{10})\"?", content):
                teams.add(m.group(1))
            for m in re.finditer(r"CODE_SIGN_STYLE\s*=\s*\"?(Automatic|Manual)\"?", content):
                styles.add(m.group(1))
            for m in re.finditer(r"PROVISIONING_PROFILE_SPECIFIER\s*=\s*\"?([^\";\n]+)\"?", content):
                spec = m.group(1).strip().strip('"')
                if spec:
                    profile_specifiers.add(spec)
        for plist in root.rglob("ExportOptions*.plist"):
            try:
                data = plistlib.loads(plist.read_bytes())
            except Exception:
                continue
            tid = data.get("teamID")
            if tid:
                export_team_ids.add(str(tid))
    return {
        "developmentTeams": sorted(teams),
        "codeSignStyles": sorted(styles),
        "provisioningProfileSpecifiers": sorted(profile_specifiers),
        "exportTeamIds": sorted(export_team_ids),
        "pbxprojCount": len(pbxproj_files),
        "pbxprojFiles": pbxproj_files[:20],
    }


def write_outputs(task_code: str, iteration: int, result: dict[str, Any]) -> Path:
    task_dir = TASKS / task_code
    log_dir = task_dir / "logs" / f"iter-{iteration}"
    log_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "Requirements.md").write_text("# Requirements - iOS Signing Preflight\n\n## Requirements with inline Acceptance Criteria\n\n### R01 — iOS signing preflight\n- **AC-001**: Inspect signing material read-only before iOS install.\n  - Verification method: ios-signing-preflight / TC-F01\n") if not (task_dir / "Requirements.md").exists() else None
    (task_dir / "Plan.md").write_text("# Plan\n\nInspect codesigning identities and provisioning profile metadata; do not modify keychain or devices.\n") if not (task_dir / "Plan.md").exists() else None
    val = task_dir / "Validation.md"
    if not val.exists():
        val.write_text("# Validation\n")
    out_json = log_dir / "ios-signing-preflight.json"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (log_dir / "env.json").write_text(json.dumps({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "taskCode": task_code,
        "iteration": iteration,
        "cwd": str(Path.cwd()),
    }, ensure_ascii=False, indent=2) + "\n")
    (log_dir / "commands.md").write_text("# Commands\n\n```bash\n" + " ".join(result.get("argv", [])) + "\n```\n")
    (log_dir / "evaluator.log").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    val.open("a").write(
        f"\n## Iteration {iteration} - iOS signing preflight\n\n"
        f"- Environment: cwd={Path.cwd()}; bundleId={result.get('bundleId')}; installedTeam={result.get('installedTeam')}; deviceId={result.get('deviceId') or '-'}\n"
        f"- Commands: see `logs/iter-{iteration}/commands.md`\n"
        f"- Result: {result['result'].upper()}\n"
        f"- Category: `{result['category']}`\n"
        f"- Summary: {result['summary']}\n"
        f"- Evidence: `logs/iter-{iteration}/ios-signing-preflight.json`\n"
        f"- Reusable findings: Before install, prefer matching the existing Team; only ask whether to uninstall when old Team material is unavailable.\n"
        f"- Avoid repeating: Do not repeatedly rebuild before signing material is confirmed; do not silently import p12 or uninstall apps.\n"
    )
    evaluation = {
        "iteration": iteration,
        "result": result["result"],
        "nextAction": "finish" if result["result"] == "pass" else "ask_user",
        "summary": result["summary"],
        "failedChecks": [] if result["result"] == "pass" else [{
            "name": "ios_signing_material",
            "category": result["category"],
            "reason": result["summary"],
            "evidence": f"logs/iter-{iteration}/ios-signing-preflight.json",
        }],
        "evidence": [{"type": "other", "note": "ios-signing-preflight", "path": f"logs/iter-{iteration}/ios-signing-preflight.json"}],
    }
    (task_dir / "evaluation.json").write_text(json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n")
    state = {
        "taskId": task_code,
        "taskType": "ios",
        "status": "finished" if result["result"] == "pass" else ("human_input_pending" if evaluation["nextAction"] == "ask_user" else "blocked"),
        "iteration": iteration,
        "nextAction": evaluation["nextAction"],
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    write_runtime_state(task_dir, state)
    return out_json


def recommend_team(identities: list[dict[str, str]], xcode: dict[str, Any]) -> list[str]:
    """Pick the Team(s) to re-sign with, preferring one the project declares
    AND that is backed by an available codesigning identity.

    Shared by --discover and by the xcuitest runner's auto-fill so both reuse
    the same selection rule (single source of truth).
    """
    identity_teams = {i.get("teamId") for i in identities if i.get("teamId")}
    project_teams = set(xcode.get("developmentTeams") or []) | set(xcode.get("exportTeamIds") or [])
    preferred = [t for t in (xcode.get("developmentTeams") or []) if t in identity_teams]
    if not preferred:
        preferred = sorted(project_teams & identity_teams)
    if not preferred:
        preferred = sorted(t for t in identity_teams if t)
    return preferred


def build_signing_plan(
    *,
    destination_type: str,
    identities: list[dict[str, str]],
    accounts: dict[str, Any],
    usable_profiles: list[ProfileInfo],
    xcode: dict[str, Any],
    team: str,
    target_team: str,
    bundle_id: str = "",
) -> dict[str, Any]:
    """Decide a concrete, executable signing strategy (the single source of
    truth consumed by the xcuitest runner).

    Decision ladder, in priority order:
      1. simulator            -> no signing at all (CODE_SIGNING_ALLOWED=NO).
      2. manual_reuse         -> a codesigning identity for the team exists AND a
                                 usable local profile for that SAME team (and the
                                 bundle, when known) covers the device; sign
                                 offline with Manual. No Apple ID required.
      3. automatic            -> Apple ID is signed in AND the account manages the
                                 build's target Team; let Xcode generate/manage
                                 the profile via -allowProvisioningUpdates.
      4. blocked (ask_user)   -> none of the above; a human must sign in / add the
                                 Team / import signing material.

    `target_team` is the Team the build *prefers* (installed/new team, falling
    back to the discovered preferred team), but it is only a seed: this function
    enumerates ALL candidate Teams the machine actually has signing material for
    and picks the best feasible strategy across them, instead of judging only the
    seed Team and giving up. Apple ID Automatic signing only works for Teams the
    signed-in account manages, so we check membership instead of assuming
    "signed in == Automatic works".

    Team enumeration / preference order:
      - seed Team (target_team or team) first, so an explicitly chosen Team wins
        ties;
      - then project-declared Teams (DEVELOPMENT_TEAM / ExportOptions teamID);
      - then Teams backed by a local codesigning identity;
      - then Teams owning a usable local profile;
      - then Teams the signed-in Apple ID manages.
    A candidate is adopted only when it can actually sign THIS build without
    changing the bundle id: for manual_reuse a local profile must match the
    current bundle under that Team; for automatic the account must manage that
    Team (Xcode then generates a profile for the same bundle under it).
    """
    signed_in = bool(accounts.get("signedIn"))
    managed_teams = set(accounts.get("managedTeamIds") or [])
    seed_team = target_team or team
    identity_teams = {i.get("teamId") for i in identities if i.get("teamId")}
    project_teams = list(xcode.get("developmentTeams") or []) + list(xcode.get("exportTeamIds") or [])
    profile_teams: list[str] = []
    for p in usable_profiles:
        for t in (p.team_ids or []):
            profile_teams.append(t)

    # Ordered, de-duplicated candidate Teams. Order encodes preference so that,
    # among equally-feasible Teams, the seed/project Team wins.
    candidates: list[str] = []
    for t in [seed_team, *project_teams, *sorted(identity_teams), *profile_teams, *sorted(managed_teams)]:
        if t and t not in candidates:
            candidates.append(t)

    def matching_profiles_for(t: str) -> list[ProfileInfo]:
        team_profiles = [p for p in usable_profiles if t in (p.team_ids or [])]
        if bundle_id:
            # Only adopt another Team's profile when it actually covers the
            # current bundle id (never change the bundle to fit a profile).
            return [p for p in team_profiles if appid_matches(p.app_id, f"{t}.{bundle_id}")]
        return team_profiles

    plan: dict[str, Any] = {
        "destinationType": destination_type,
        "team": seed_team,
        "appleIdSignedIn": signed_in,
        "appleIdAccounts": accounts.get("accounts") or [],
        "managedTeamIds": sorted(managed_teams),
        "candidateTeams": candidates,
        "usableProfileCount": len(usable_profiles),
    }

    # 1) Simulator: signing is irrelevant.
    if destination_type == "simulator":
        plan.update({
            "strategy": "simulator_no_sign",
            "codeSignStyle": "",
            "askUser": False,
            "category": "old_team_signing_available",
            "targetTeamManagedByAppleId": bool(seed_team) and seed_team in managed_teams,
            "hasIdentityForTeam": bool(seed_team) and seed_team in identity_teams,
            "teamProfileCount": 0,
            "matchingProfileCount": 0,
            "buildSettings": ["CODE_SIGNING_ALLOWED=NO"],
            "rebuildHint": "xcodebuild ... -destination 'platform=iOS Simulator,...' CODE_SIGNING_ALLOWED=NO",
            "automaticSigningViable": False,
            "summary": "Simulator destination: no code signing required (CODE_SIGNING_ALLOWED=NO).",
        })
        return plan

    # 2) Manual reuse (preferred): pick the first candidate Team that has a local
    # codesigning identity AND a local profile matching this bundle. Offline,
    # no Apple ID login required.
    for t in candidates:
        manual_profiles = matching_profiles_for(t)
        if t in identity_teams and manual_profiles:
            plan.update({
                "strategy": "manual_reuse",
                "team": t,
                "codeSignStyle": "Manual",
                "askUser": False,
                "category": "old_team_signing_available",
                "targetTeamManagedByAppleId": t in managed_teams,
                "hasIdentityForTeam": True,
                "teamProfileCount": len([p for p in usable_profiles if t in (p.team_ids or [])]),
                "matchingProfileCount": len(manual_profiles),
                "buildSettings": [
                    f"DEVELOPMENT_TEAM={t}",
                    "CODE_SIGN_STYLE=Manual",
                    "CODE_SIGNING_ALLOWED=YES",
                ],
                "profileSpecifiers": sorted({p.name for p in manual_profiles if p.name}),
                "matchingProfiles": [p.__dict__ for p in manual_profiles[:10]],
                "rebuildHint": (
                    f"xcodebuild ... DEVELOPMENT_TEAM={t} CODE_SIGN_STYLE=Manual "
                    f"PROVISIONING_PROFILE_SPECIFIER=<one of profileSpecifiers>"
                ),
                "automaticSigningViable": signed_in and (t in managed_teams),
                "summary": (
                    f"Manual signing viable offline for Team {t}: matching codesigning "
                    f"identity + {len(manual_profiles)} matching profile(s) "
                    f"({'bundle '+bundle_id if bundle_id else 'team-level'}). No Apple ID login required."
                    + ("" if t == seed_team else f" (selected over seed Team {seed_team or '<none>'} which had no reusable material).")
                ),
            })
            return plan

    # 3) Automatic: pick the first candidate Team the signed-in Apple ID manages.
    # Xcode generates/manages a profile for the SAME bundle under that Team via
    # -allowProvisioningUpdates; the bundle id never changes. To avoid silently
    # signing under an unrelated Team, only the seed Team or a Team the PROJECT
    # actually declares is eligible here (an account may manage many Teams the
    # app has nothing to do with). A project-declared + account-managed Team
    # (e.g. the real R9BHDB8GA5 case) is the legitimate switch target.
    if signed_in and identities:
        declared = set(project_teams)
        automatic_candidates = [
            t for t in candidates
            if t in managed_teams and (t == seed_team or t in declared)
        ]
        for t in automatic_candidates:
            plan.update({
                "strategy": "automatic",
                "team": t,
                "codeSignStyle": "Automatic",
                "askUser": False,
                "category": "old_team_signing_available",
                "targetTeamManagedByAppleId": True,
                "hasIdentityForTeam": t in identity_teams,
                "teamProfileCount": len([p for p in usable_profiles if t in (p.team_ids or [])]),
                "matchingProfileCount": len(matching_profiles_for(t)),
                "buildSettings": [
                    f"DEVELOPMENT_TEAM={t}",
                    "CODE_SIGN_STYLE=Automatic",
                    "CODE_SIGNING_ALLOWED=YES",
                    # Many projects hardcode a Manual profile/identity in the
                    # pbxproj (e.g. PROVISIONING_PROFILE_SPECIFIER="Cosign iOS
                    # with *" / CODE_SIGN_IDENTITY="Apple Development: ... (TEAM)").
                    # Leaving those set while switching to Automatic makes
                    # xcodebuild fail with "conflicting provisioning settings".
                    # Clear them so -allowProvisioningUpdates can manage signing
                    # for the chosen Team without changing the project files.
                    "PROVISIONING_PROFILE_SPECIFIER=",
                    "PROVISIONING_PROFILE=",
                    "CODE_SIGN_IDENTITY=Apple Development",
                ],
                "extraFlags": ["-allowProvisioningUpdates"],
                "rebuildHint": (
                    f"xcodebuild ... -allowProvisioningUpdates "
                    f"DEVELOPMENT_TEAM={t} CODE_SIGN_STYLE=Automatic "
                    f"PROVISIONING_PROFILE_SPECIFIER= PROVISIONING_PROFILE= "
                    f"'CODE_SIGN_IDENTITY=Apple Development'"
                ),
                "automaticSigningViable": True,
                "summary": (
                    f"Automatic signing viable: Apple ID {plan['appleIdAccounts']} is signed in and "
                    f"manages Team {t}; Xcode can generate/manage the profile via "
                    f"-allowProvisioningUpdates."
                    + ("" if t == seed_team else f" (selected over seed Team {seed_team or '<none>'} which the account does not manage / cannot reuse).")
                ),
            })
            return plan

    # 4) Blocked: no candidate Team could sign this build. Explain per the seed
    # Team (and note that other candidates were tried) so ask_user is actionable.
    seed_has_identity = bool(seed_team) and seed_team in identity_teams
    reasons = []
    if not candidates:
        reasons.append("no Team resolved from project, identities, profiles, or Apple ID")
    else:
        reasons.append(
            f"tried Teams {candidates}: none has both a local identity and a profile matching "
            f"{('bundle '+bundle_id) if bundle_id else 'the bundle'} (manual reuse), and none is "
            + (
                f"managed by the signed-in Apple ID ({', '.join(plan['appleIdAccounts'])} manages {sorted(managed_teams)})"
                if signed_in else "covered because Xcode has no signed-in Apple ID"
            )
            + " (automatic)"
        )
    category = (
        "provisioning_profile_blocked"
        if seed_has_identity
        else "signing_material_blocked"
    )
    plan.update({
        "strategy": "blocked",
        "team": seed_team,
        "codeSignStyle": "",
        "askUser": True,
        "category": category,
        "targetTeamManagedByAppleId": bool(seed_team) and seed_team in managed_teams,
        "hasIdentityForTeam": seed_has_identity,
        "teamProfileCount": len([p for p in usable_profiles if seed_team in (p.team_ids or [])]) if seed_team else 0,
        "matchingProfileCount": len(matching_profiles_for(seed_team)) if seed_team else 0,
        "buildSettings": [],
        "rebuildHint": "",
        "automaticSigningViable": False,
        "summary": (
            "Cannot sign automatically: " + "; ".join(reasons) + ". "
            "ask_user: sign in/add the correct Team in Xcode > Settings > Accounts, "
            "or import a matching certificate + provisioning profile (including the "
            "<bundle>.xctrunner profile for UI tests). Simulator verification needs no signing."
        ),
    })
    return plan


def run_discover(args: argparse.Namespace) -> int:
    """Scan project/machine for already-existing signing material.

    Read-only. Emits every codesigning identity, all readable provisioning
    profiles, the Xcode Apple ID login + managed Teams, and the signing settings
    the project already declares, plus an executable signingPlan, so a build that
    failed on signing/provisioning can retry with existing material before
    escalating to the user.
    """
    roots = [Path(x).expanduser() for x in args.profile_root]
    roots += [
        Path.cwd() / "TTReading" / "Provisions",
        Path.home() / "Library" / "MobileDevice" / "Provisioning Profiles",
        # Xcode 16/26 also persist managed profiles here; the old code missed it.
        Path.home() / "Library" / "Developer" / "Xcode" / "UserData" / "Provisioning Profiles",
    ]
    identities = list_identities()
    accounts = detect_xcode_accounts()
    profiles = collect_all_profiles(roots, args.device_id or None)
    usable_profiles = [p for p in profiles if not p.expired and (p.includes_device is not False)]
    xcode = discover_xcode_signing([Path.cwd()] + [Path(x).expanduser() for x in args.profile_root])

    # Prefer a Team that is both declared by the project and backed by a usable
    # identity, so the re-sign recommendation reuses what the project expects.
    preferred_teams = recommend_team(identities, xcode)
    team = preferred_teams[0] if preferred_teams else ""

    # Resolve the destination type. Simulator builds never need signing.
    destination_type = args.destination_type
    if destination_type == "auto":
        destination_type = "device" if (args.device_id or args.bundle_id) else "device"

    plan = build_signing_plan(
        destination_type=destination_type,
        identities=identities,
        accounts=accounts,
        usable_profiles=usable_profiles,
        xcode=xcode,
        team=team,
        target_team=(args.installed_team or args.new_team or team),
        bundle_id=args.bundle_id,
    )
    result = "pass" if not plan["askUser"] else "blocked"
    category = plan["category"]
    summary = plan["summary"]

    recommendation = {
        "preferredTeams": preferred_teams,
        "recommendedTeam": plan["team"],
        "recommendedCodeSignStyle": plan["codeSignStyle"],
        "recommendedProfileSpecifiers": xcode["provisioningProfileSpecifiers"],
        "automaticSigningViable": plan["automaticSigningViable"],
        "rebuildHint": plan["rebuildHint"],
        "canRetryWithExistingMaterial": result == "pass",
    }
    payload = {
        "argv": ["ios-signing-preflight"] + __import__("sys").argv[1:],
        "mode": "discover",
        "result": result,
        "category": category,
        "summary": summary,
        "destinationType": destination_type,
        "deviceId": args.device_id,
        "identities": identities,
        "xcodeAccounts": accounts,
        "profileCount": len(profiles),
        "usableProfileCount": len(usable_profiles),
        "profiles": [p.__dict__ for p in profiles[:30]],
        "xcodeSigning": xcode,
        "signingPlan": plan,
        "recommendation": recommendation,
        "policy": (
            "Simulator: no signing. Device: prefer Manual reuse of existing identity+profile "
            "(offline, no Apple ID needed); use Automatic only when Apple ID is signed in AND "
            "manages the target Team; otherwise ask_user."
        ),
    }
    out = write_outputs(args.task_code, args.iteration, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nWrote: {out}")
    return 0 if result == "pass" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_code")
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--bundle-id", default="")
    parser.add_argument("--installed-team", default="")
    parser.add_argument("--new-team", default="")
    parser.add_argument("--device-id", default="")
    parser.add_argument("--profile-root", action="append", default=[])
    parser.add_argument("--discover", action="store_true", help="Scan project/machine for existing signing material and emit a re-sign recommendation.")
    parser.add_argument(
        "--destination-type",
        choices=["device", "simulator", "auto"],
        default="auto",
        help="Target run destination. 'simulator' skips signing entirely (CODE_SIGNING_ALLOWED=NO). 'auto' infers device when a --device-id/--bundle-id is given.",
    )
    args = parser.parse_args()

    if args.discover:
        return run_discover(args)

    if not args.bundle_id or not args.installed_team:
        parser.error("--bundle-id and --installed-team are required unless --discover is used")

    expected_app_id = f"{args.installed_team}.{args.bundle_id}"
    identities = list_identities()
    matching_identities = [i for i in identities if i.get("teamId") == args.installed_team or args.installed_team in i.get("name", "")]
    roots = [Path(x).expanduser() for x in args.profile_root]
    roots += [Path.cwd() / "TTReading" / "Provisions", Path.home() / "Library" / "MobileDevice" / "Provisioning Profiles"]
    profiles = collect_profiles(roots, args.installed_team, expected_app_id, args.device_id or None)
    usable_profiles = [p for p in profiles if not p.expired and (p.includes_device is not False)]

    if matching_identities and usable_profiles:
        result = "pass"
        category = "old_team_signing_available"
        summary = f"Old-Team signing material appears available for {expected_app_id}; prefer rebuild/re-sign with {args.installed_team} to preserve app data."
    elif usable_profiles and not matching_identities:
        result = "blocked"
        category = "signing_material_blocked"
        summary = f"Profiles for {expected_app_id} exist, but no matching {args.installed_team} codesigning identity/private key is visible; ask user before importing certificates or fall back to uninstall."
    elif matching_identities and not usable_profiles:
        result = "blocked"
        category = "provisioning_profile_blocked"
        summary = f"A {args.installed_team} identity appears available, but no usable non-expired profile for {expected_app_id} including the target device was found."
    else:
        result = "blocked"
        category = "signing_material_blocked"
        summary = f"No usable {args.installed_team} identity/profile pair found for {expected_app_id}; ask user whether to provide signing material or uninstall old app."

    payload = {
        "argv": ["ios-signing-preflight"] + __import__("sys").argv[1:],
        "result": result,
        "category": category,
        "summary": summary,
        "bundleId": args.bundle_id,
        "installedTeam": args.installed_team,
        "newTeam": args.new_team,
        "expectedApplicationIdentifier": expected_app_id,
        "deviceId": args.device_id,
        "matchingIdentities": matching_identities,
        "profileCount": len(profiles),
        "usableProfileCount": len(usable_profiles),
        "profiles": [p.__dict__ for p in profiles[:30]],
        "policy": "Prefer old-Team signing to preserve app data; uninstall is explicit data-loss fallback.",
    }
    out = write_outputs(args.task_code, args.iteration, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nWrote: {out}")
    return 0 if result == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
