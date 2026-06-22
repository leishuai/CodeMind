"""Request D: iOS signing/provisioning failures should first try to reuse
signing material that already exists in the project/machine, and only escalate
to ask_user once that has been genuinely exhausted."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


def _load(module_name: str, rel_path: str):
    sys.path.insert(0, str(Path("scripts").resolve()))
    path = Path(rel_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _classifier():
    return _load("failure_classifier", "scripts/failure_classifier.py")


# --- build-phase team/profile failure -------------------------------------

def test_build_signing_failure_first_tries_existing_material() -> None:
    fc = _classifier()
    c = fc.classify("ios", "build", "error: No profiles for 'com.demo.app' were found")
    assert c.category == "provisioning_profile_blocked"
    assert c.nextAction == "retry_generator"
    assert c.sameProblemKey == "ios.signing.team_or_profile.use_existing"
    assert "ios-signing-preflight" in c.reason


def test_build_requires_dev_team_first_tries_existing_material() -> None:
    fc = _classifier()
    c = fc.classify("ios", "build", "Signing for 'App' requires a development team.")
    assert c.category == "provisioning_profile_blocked"
    assert c.nextAction == "retry_generator"


def test_build_signing_failure_asks_user_after_exhausted() -> None:
    fc = _classifier()
    c = fc.classify(
        "ios",
        "build",
        "error: No profiles for 'com.demo.app' were found",
        context={"signingMaterialExhausted": True},
    )
    assert c.nextAction == "ask_user"
    assert c.askUserQuestion is not None
    assert c.askUserQuestion.get("category") == "real_device_or_signing"


def test_build_signing_failure_asks_user_when_no_usable_material() -> None:
    fc = _classifier()
    c = fc.classify(
        "ios",
        "build",
        "Signing requires a development team.",
        context={"noUsableSigningMaterial": True},
    )
    assert c.nextAction == "ask_user"


# --- XCUITest runner code-signing failure ---------------------------------

def test_runner_signing_failure_first_tries_existing_material() -> None:
    fc = _classifier()
    c = fc.classify("ios", "test", "Code signing failed: errSecInternalComponent")
    assert c.category == "external_runner_signing_blocked"
    assert c.nextAction == "retry_generator"
    assert c.sameProblemKey == "ios.xcuitest.runner.signing_use_existing"


def test_runner_signing_failure_asks_user_after_exhausted() -> None:
    fc = _classifier()
    c = fc.classify(
        "ios",
        "test",
        "Code signing failed: no valid signing identities found",
        context={"signingRetryExhausted": True},
    )
    assert c.nextAction == "ask_user"
    assert c.sameProblemKey == "ios.xcuitest.runner.signing_blocked"
    assert c.askUserQuestion is not None
    assert c.askUserQuestion.get("category") == "real_device_or_signing"


# --- DTX / exit code 74 after the test session started --------------------

def test_dtx_disconnect_after_tests_started_is_not_signing() -> None:
    fc = _classifier()
    c = fc.classify(
        "ios",
        "test",
        "Running tests...\nLost connection to the test process. DTXConnection error.",
        exit_code=74,
    )
    assert c.category == "test_failure"
    assert c.nextAction == "retry_generator"
    assert c.sameProblemKey == "ios.xcuitest.session.dtx_disconnect_or_code74"


def test_code74_after_tests_started_even_with_signing_token_is_not_signing() -> None:
    """A DTX/code-74 failure must not be misrouted to signing just because the
    log happens to mention signing once the session has already started."""
    fc = _classifier()
    c = fc.classify(
        "ios",
        "test",
        "Signing Identity: Apple Development\nRunning tests...\n"
        "The connection was lost. errSecInternalComponent",
        exit_code=74,
    )
    assert c.category == "test_failure"
    assert c.nextAction == "retry_generator"
    assert "external_runner_signing_blocked" not in c.category


def test_signing_failure_before_tests_start_is_still_signing() -> None:
    """Before the session starts there is no 'Running tests...' marker, so a
    code-sign failure is still routed to the signing self-heal path."""
    fc = _classifier()
    c = fc.classify("ios", "test", "Code signing failed: errSecInternalComponent")
    assert c.category == "external_runner_signing_blocked"
    assert c.nextAction == "retry_generator"


# --- device-side link instability before the session starts ---------------

def test_device_link_lost_before_session_self_retries_first() -> None:
    """A Mac<->iPhone connection drop before 'Running tests...' is transient and
    not a signing/code/device-absence problem: self-retry first."""
    fc = _classifier()
    c = fc.classify("ios", "test", "Lost connection to the device. Could not connect to lockdown.")
    assert c.category == "mobile_device_unavailable"
    assert c.nextAction == "retry_generator"
    assert c.sameProblemKey == "ios.device.link_lost_before_session"


def test_device_link_lost_repeated_asks_user_to_recover_link() -> None:
    """After retries are exhausted, ask the user to physically recover the link
    (replug USB / restart Xcode / reboot phone), under a system dependency
    category rather than signing."""
    fc = _classifier()
    c = fc.classify(
        "ios",
        "test",
        "Lost connection to the device.",
        context={"deviceLinkRetryExhausted": True},
    )
    assert c.category == "mobile_device_unavailable"
    assert c.nextAction == "ask_user"
    assert c.sameProblemKey == "ios.device.link_lost_repeated"
    assert c.askUserQuestion is not None
    assert c.askUserQuestion.get("category") == "system_or_external_dependency"


def test_device_link_lost_after_session_is_test_failure_not_link() -> None:
    """If the connection drops AFTER 'Running tests...', it is a test-runtime
    DTX disconnect (retry_generator), not the pre-session link path."""
    fc = _classifier()
    c = fc.classify("ios", "test", "Running tests...\nLost connection to the device.")
    assert c.category == "test_failure"
    assert c.sameProblemKey == "ios.xcuitest.session.dtx_disconnect_or_code74"


# --- ios-signing-preflight --discover -------------------------------------

def test_discover_collect_all_profiles_and_xcode_scan(tmp_path) -> None:
    sp = _load("ios_signing_preflight", "scripts/ios_signing_preflight.py")
    # discover_xcode_signing should extract Team / style / specifier from pbxproj
    proj = tmp_path / "App.xcodeproj"
    proj.mkdir()
    (proj / "project.pbxproj").write_text(
        "DEVELOPMENT_TEAM = ABCDE12345;\n"
        "CODE_SIGN_STYLE = Automatic;\n"
        'PROVISIONING_PROFILE_SPECIFIER = "My Demo Profile";\n'
    )
    result = sp.discover_xcode_signing([tmp_path])
    assert "ABCDE12345" in result["developmentTeams"]
    assert "Automatic" in result["codeSignStyles"]
    assert "My Demo Profile" in result["provisioningProfileSpecifiers"]
    assert result["pbxprojCount"] == 1


def _run_discover_payload(sp, monkeypatch, identities, profiles, xcode, accounts=None):
    """Drive run_discover with stubbed scanners and capture its JSON payload."""
    import argparse
    import json as _json

    monkeypatch.setattr(sp, "list_identities", lambda: identities)
    monkeypatch.setattr(sp, "collect_all_profiles", lambda roots, dev: profiles)
    monkeypatch.setattr(sp, "discover_xcode_signing", lambda roots: xcode)
    monkeypatch.setattr(
        sp,
        "detect_xcode_accounts",
        lambda: accounts or {"signedIn": False, "accounts": [], "managedTeamIds": [], "teamDetails": []},
    )
    captured = {}
    monkeypatch.setattr(sp, "write_outputs", lambda *a, **k: Path("/tmp/x"))

    real_dumps = _json.dumps

    def _capture_dumps(obj, *a, **k):
        if isinstance(obj, dict) and obj.get("mode") == "discover":
            captured["payload"] = obj
        return real_dumps(obj, *a, **k)

    monkeypatch.setattr(sp.json, "dumps", _capture_dumps)
    args = argparse.Namespace(
        task_code="t", iteration=1, device_id="", profile_root=[], discover=True,
        bundle_id="", installed_team="", new_team="", destination_type="device",
    )
    sp.run_discover(args)
    return captured["payload"]


def test_discover_recommends_automatic_signing_with_identity_and_team(monkeypatch) -> None:
    """When a codesigning identity (certificate) and a Team exist, NO local
    .mobileprovision is present, AND a signed-in Apple ID manages that Team,
    --discover should consider Automatic signing viable and recommend
    -allowProvisioningUpdates."""
    sp = _load("ios_signing_preflight", "scripts/ios_signing_preflight.py")
    identities = [{"hash": "H", "name": "Apple Development: dev (ABCDE12345)", "teamId": "ABCDE12345"}]
    xcode = {
        "developmentTeams": ["ABCDE12345"],
        "codeSignStyles": [],
        "provisioningProfileSpecifiers": [],
        "exportTeamIds": [],
        "pbxprojCount": 1,
        "pbxprojFiles": [],
    }
    accounts = {"signedIn": True, "accounts": ["dev@example.com"], "managedTeamIds": ["ABCDE12345"], "teamDetails": []}
    payload = _run_discover_payload(sp, monkeypatch, identities, [], xcode, accounts=accounts)
    rec = payload["recommendation"]
    assert payload["result"] == "pass"
    assert rec["automaticSigningViable"] is True
    assert rec["recommendedTeam"] == "ABCDE12345"
    assert rec["recommendedCodeSignStyle"] == "Automatic"
    assert "-allowProvisioningUpdates" in rec["rebuildHint"]
    assert rec["canRetryWithExistingMaterial"] is True


def test_discover_blocked_when_apple_id_not_managing_team(monkeypatch) -> None:
    """Identity + Team but NO Apple ID managing that Team and no local profile =>
    Automatic cannot generate a profile => blocked (this is exactly the
    iter-26 real-device situation)."""
    sp = _load("ios_signing_preflight", "scripts/ios_signing_preflight.py")
    identities = [{"hash": "H", "name": "Apple Development: dev (ABCDE12345)", "teamId": "ABCDE12345"}]
    xcode = {
        "developmentTeams": ["ABCDE12345"],
        "codeSignStyles": [],
        "provisioningProfileSpecifiers": [],
        "exportTeamIds": [],
        "pbxprojCount": 1,
        "pbxprojFiles": [],
    }
    # Signed in but the account manages a DIFFERENT team.
    accounts = {"signedIn": True, "accounts": ["dev@example.com"], "managedTeamIds": ["OTHERTEAMX"], "teamDetails": []}
    payload = _run_discover_payload(sp, monkeypatch, identities, [], xcode, accounts=accounts)
    assert payload["result"] == "blocked"
    assert payload["category"] == "provisioning_profile_blocked"
    assert payload["recommendation"]["automaticSigningViable"] is False
    assert payload["recommendation"]["canRetryWithExistingMaterial"] is False


def test_discover_blocked_when_no_identity(monkeypatch) -> None:
    """No codesigning identity and no Apple ID => blocked."""
    sp = _load("ios_signing_preflight", "scripts/ios_signing_preflight.py")
    xcode = {
        "developmentTeams": [],
        "codeSignStyles": [],
        "provisioningProfileSpecifiers": [],
        "exportTeamIds": [],
        "pbxprojCount": 0,
        "pbxprojFiles": [],
    }
    payload = _run_discover_payload(sp, monkeypatch, [], [], xcode)
    assert payload["result"] == "blocked"
    assert payload["category"] == "signing_material_blocked"
    assert payload["recommendation"]["automaticSigningViable"] is False
    assert payload["recommendation"]["canRetryWithExistingMaterial"] is False


def test_discover_simulator_needs_no_signing(monkeypatch) -> None:
    """A simulator destination never needs signing, even with zero material."""
    sp = _load("ios_signing_preflight", "scripts/ios_signing_preflight.py")
    xcode = {"developmentTeams": [], "codeSignStyles": [], "provisioningProfileSpecifiers": [], "exportTeamIds": [], "pbxprojCount": 0, "pbxprojFiles": []}
    monkeypatch.setattr(sp, "list_identities", lambda: [])
    monkeypatch.setattr(sp, "collect_all_profiles", lambda roots, dev: [])
    monkeypatch.setattr(sp, "discover_xcode_signing", lambda roots: xcode)
    monkeypatch.setattr(sp, "detect_xcode_accounts", lambda: {"signedIn": False, "accounts": [], "managedTeamIds": [], "teamDetails": []})
    plan = sp.build_signing_plan(
        destination_type="simulator", identities=[], accounts={"signedIn": False, "managedTeamIds": []},
        usable_profiles=[], xcode=xcode, team="", target_team="", bundle_id="",
    )
    assert plan["strategy"] == "simulator_no_sign"
    assert plan["askUser"] is False
    assert "CODE_SIGNING_ALLOWED=NO" in plan["buildSettings"]


def test_recommend_team_prefers_project_team_backed_by_identity() -> None:
    sp = _load("ios_signing_preflight", "scripts/ios_signing_preflight.py")
    identities = [
        {"teamId": "TEAMAAAAAA"},
        {"teamId": "TEAMBBBBBB"},
    ]
    xcode = {"developmentTeams": ["TEAMBBBBBB"], "exportTeamIds": []}
    # Project declares TEAMB and an identity backs it -> TEAMB wins.
    assert sp.recommend_team(identities, xcode)[0] == "TEAMBBBBBB"


def test_recommend_team_empty_when_no_identity() -> None:
    sp = _load("ios_signing_preflight", "scripts/ios_signing_preflight.py")
    assert sp.recommend_team([], {"developmentTeams": ["TEAMAAAAAA"]}) == []


# --- multi-Team selection: don't give up on the seed Team ------------------

def _xcode(teams):
    return {
        "developmentTeams": list(teams),
        "codeSignStyles": [],
        "provisioningProfileSpecifiers": [],
        "exportTeamIds": [],
        "pbxprojCount": 1,
        "pbxprojFiles": [],
    }


def test_plan_switches_to_project_team_managed_by_apple_id(monkeypatch) -> None:
    """The real iter-26 case: the seed Team (a personal cert Team) cannot sign,
    but another PROJECT-declared Team is managed by the signed-in Apple ID =>
    pick Automatic via that Team instead of blocking. Bundle id is unchanged."""
    sp = _load("ios_signing_preflight", "scripts/ios_signing_preflight.py")
    # Project declares the real R9-like managed Team plus two unusable ones.
    xcode = _xcode(["UNUSABLE001", "MANAGED9999", "UNUSABLE002"])
    # Local cert is for an unrelated personal Team (the bad seed choice).
    identities = [{"teamId": "PERSONAL555"}]
    accounts = {"signedIn": True, "accounts": ["dev@corp.com"], "managedTeamIds": ["OTHERORG000", "MANAGED9999"], "teamDetails": []}
    plan = sp.build_signing_plan(
        destination_type="device",
        identities=identities,
        accounts=accounts,
        usable_profiles=[],
        xcode=xcode,
        team="PERSONAL555",
        target_team="PERSONAL555",
        bundle_id="com.example.app.dailybuild.inhouse",
    )
    assert plan["strategy"] == "automatic"
    assert plan["team"] == "MANAGED9999"
    assert plan["askUser"] is False
    assert f"DEVELOPMENT_TEAM=MANAGED9999" in plan["buildSettings"]
    # Automatic must clear any pbxproj-hardcoded Manual profile/identity so it
    # does not hit "conflicting provisioning settings".
    assert "PROVISIONING_PROFILE_SPECIFIER=" in plan["buildSettings"]
    assert "PROVISIONING_PROFILE=" in plan["buildSettings"]
    assert "CODE_SIGN_IDENTITY=Apple Development" in plan["buildSettings"]


def test_plan_does_not_switch_to_unrelated_account_team(monkeypatch) -> None:
    """An account-managed Team the PROJECT does not declare must NOT be silently
    adopted for Automatic signing."""
    sp = _load("ios_signing_preflight", "scripts/ios_signing_preflight.py")
    xcode = _xcode(["DECLARED111"])  # project declares only DECLARED111
    identities = [{"teamId": "DECLARED111"}]
    # Account manages an unrelated org Team, not DECLARED111.
    accounts = {"signedIn": True, "accounts": ["dev@corp.com"], "managedTeamIds": ["UNRELATED99"], "teamDetails": []}
    plan = sp.build_signing_plan(
        destination_type="device",
        identities=identities,
        accounts=accounts,
        usable_profiles=[],
        xcode=xcode,
        team="DECLARED111",
        target_team="DECLARED111",
        bundle_id="com.demo.app",
    )
    assert plan["strategy"] == "blocked"
    assert plan["askUser"] is True


def test_plan_manual_reuse_only_when_profile_matches_bundle(monkeypatch) -> None:
    """A candidate Team's profile is adopted for manual reuse only when it
    actually covers the current bundle id; the bundle id is never changed to fit
    a profile."""
    sp = _load("ios_signing_preflight", "scripts/ios_signing_preflight.py")
    # Profile + identity exist for OTHERTEAM00 but the profile is for a DIFFERENT
    # bundle id, so manual reuse must not be claimed.
    prof = sp.ProfileInfo(
        path="/p.mobileprovision", name="Other", team_ids=["OTHERTEAM00"],
        app_id="OTHERTEAM00.com.other.app", expires="", expired=False,
        device_count=1, includes_device=None, cert_count=1,
    )
    xcode = _xcode(["OTHERTEAM00"])
    identities = [{"teamId": "OTHERTEAM00"}]
    accounts = {"signedIn": False, "accounts": [], "managedTeamIds": []}
    plan = sp.build_signing_plan(
        destination_type="device",
        identities=identities,
        accounts=accounts,
        usable_profiles=[prof],
        xcode=xcode,
        team="OTHERTEAM00",
        target_team="OTHERTEAM00",
        bundle_id="com.example.app",  # does NOT match the profile's com.other.app
    )
    assert plan["strategy"] == "blocked"
    # But a profile that DOES match should be reused.
    prof_match = sp.ProfileInfo(
        path="/p2.mobileprovision", name="Match", team_ids=["OTHERTEAM00"],
        app_id="OTHERTEAM00.com.example.app", expires="", expired=False,
        device_count=1, includes_device=None, cert_count=1,
    )
    plan2 = sp.build_signing_plan(
        destination_type="device",
        identities=identities,
        accounts=accounts,
        usable_profiles=[prof, prof_match],
        xcode=xcode,
        team="OTHERTEAM00",
        target_team="OTHERTEAM00",
        bundle_id="com.example.app",
    )
    assert plan2["strategy"] == "manual_reuse"
    assert plan2["team"] == "OTHERTEAM00"
    assert "Match" in plan2["profileSpecifiers"]


# --- ios-xcuitest runner resolves a signing plan from existing material --

def test_runner_resolves_team_and_manual_plan_from_material(monkeypatch) -> None:
    runner = _load("ios_xcuitest_runner", "scripts/ios_xcuitest_runner.py")
    sp = _load("ios_signing_preflight", "scripts/ios_signing_preflight.py")
    prof = sp.ProfileInfo(
        path="/x.mobileprovision", name="Demo Profile", team_ids=["TEAMAAAAAA"],
        app_id="TEAMAAAAAA.com.demo.app", expires="", expired=False,
        device_count=1, includes_device=None, cert_count=1,
    )
    monkeypatch.setattr(sp, "list_identities", lambda: [{"teamId": "TEAMAAAAAA"}])
    monkeypatch.setattr(sp, "detect_xcode_accounts", lambda: {"signedIn": False, "accounts": [], "managedTeamIds": []})
    monkeypatch.setattr(sp, "collect_all_profiles", lambda roots, dev: [prof])
    monkeypatch.setattr(sp, "discover_xcode_signing", lambda roots: {"developmentTeams": ["TEAMAAAAAA"], "exportTeamIds": []})
    config = {"team": "", "bundle_id": "com.demo.app", "device_id": ""}
    plan = runner.resolve_signing_plan(config, "device")
    # Team was auto-filled, and an offline Manual reuse plan was chosen.
    assert config["team"] == "TEAMAAAAAA"
    assert plan["strategy"] == "manual_reuse"
    assert plan["codeSignStyle"] == "Manual"
    assert "CODE_SIGN_STYLE=Manual" in plan["buildSettings"]
    assert plan["askUser"] is False


def test_runner_plan_blocks_when_no_material(monkeypatch) -> None:
    runner = _load("ios_xcuitest_runner", "scripts/ios_xcuitest_runner.py")
    sp = _load("ios_signing_preflight", "scripts/ios_signing_preflight.py")
    monkeypatch.setattr(sp, "list_identities", lambda: [])
    monkeypatch.setattr(sp, "detect_xcode_accounts", lambda: {"signedIn": False, "accounts": [], "managedTeamIds": []})
    monkeypatch.setattr(sp, "collect_all_profiles", lambda roots, dev: [])
    monkeypatch.setattr(sp, "discover_xcode_signing", lambda roots: {"developmentTeams": [], "exportTeamIds": []})
    config = {"team": "", "bundle_id": "com.demo.app", "device_id": ""}
    plan = runner.resolve_signing_plan(config, "device")
    assert plan["strategy"] == "blocked"
    assert plan["askUser"] is True


# --- target app bundle id reaches the on-device runner --------------------

def _run_runner_main(runner, monkeypatch, tmp_path, extra_argv) -> Path:
    """Run ios_xcuitest_runner.main() with a fake xcodebuild that exits 0, and
    return the iter log dir so callers can inspect commands.md."""
    workspace = tmp_path / "ws"
    tasks = workspace / ".automind" / "tasks"
    tasks.mkdir(parents=True)
    monkeypatch.setattr(runner, "TASKS_DIR", tasks)
    monkeypatch.setattr(runner, "WORKSPACE_ROOT", workspace)

    def fake_run(cmd, cwd=None, stdout=None, stderr=None, text=True, **kwargs):
        if stdout is not None and hasattr(stdout, "write"):
            stdout.write("Running tests...\n** TEST SUCCEEDED **\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    # Stub signing resolution so the command-building tests are deterministic and
    # do not depend on this machine's certificates / Apple ID login.
    monkeypatch.setattr(
        runner,
        "resolve_signing_plan",
        lambda config, dest: {
            "attempted": True,
            "strategy": "automatic",
            "codeSignStyle": "Automatic",
            "askUser": False,
            "buildSettings": [f"DEVELOPMENT_TEAM={config.get('team','')}", "CODE_SIGN_STYLE=Automatic", "CODE_SIGNING_ALLOWED=YES"],
            "extraFlags": ["-allowProvisioningUpdates"],
            "summary": "stub automatic",
        },
    )
    argv = ["ios_xcuitest_runner.py", "tgt_task", "1",
            "--project-path", "/tmp/Demo.xcodeproj", "--scheme", "Demo",
            "--device-id", "FAKEUDID", "--team", "TEAMAAAAAA"] + extra_argv
    monkeypatch.setattr(sys, "argv", argv)
    runner.main()
    return tasks / "tgt_task" / "logs" / "iter-1"


def test_runner_injects_target_bundle_id_via_test_runner_prefix(monkeypatch, tmp_path) -> None:
    runner = _load("ios_xcuitest_runner", "scripts/ios_xcuitest_runner.py")
    iter_dir = _run_runner_main(runner, monkeypatch, tmp_path, ["--target-bundle-id", "com.example.app"])
    commands = (iter_dir / "commands.md").read_text()
    # The app-under-test bundle id must be forwarded through xcodebuild with the
    # TEST_RUNNER_ prefix so the on-device XCUITest process inherits it; a plain
    # shell env var would not be inherited.
    assert "TEST_RUNNER_AUTOMIND_TARGET_BUNDLE_ID=com.example.app" in commands


def test_runner_omits_target_bundle_id_when_not_set(monkeypatch, tmp_path) -> None:
    runner = _load("ios_xcuitest_runner", "scripts/ios_xcuitest_runner.py")
    iter_dir = _run_runner_main(runner, monkeypatch, tmp_path, [])
    commands = (iter_dir / "commands.md").read_text()
    assert "TEST_RUNNER_AUTOMIND_TARGET_BUNDLE_ID" not in commands
