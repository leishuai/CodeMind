---
name: ios-signing-install
description: "iOS signing and install playbook for preserving app data, diagnosing cross-Team overwrite failures, and deciding when uninstall requires explicit user approval."
use_when:
  - "installing a development build over an existing app"
  - "MismatchedApplicationIdentifierEntitlement or Team ID mismatch appears"
  - "checking whether old-Team signing material can preserve device app data"
solves:
  - "separates signing/install blockers from compile failures"
  - "prioritizes same-Team re-sign/rebuild before uninstall"
  - "defines data-loss and keychain/certificate approval boundaries"
---
# iOS Signing / Install Summary

This is the common iOS policy for installing development builds onto physical devices when an app with the same bundle id may already be installed.

## Default policy: preserve app data first

When installing an iOS build over an existing app with the same bundle id:

1. Inspect the installed app identity if possible:
   - bundle id
   - Team ID
   - application-identifier entitlement
2. Prefer signing the new build with the **same Team/application-identifier** as the installed app.
3. If matching signing material exists, rebuild or re-sign with that Team and install over the existing app to preserve local data.
4. If matching signing material is missing or invalid, classify as `signing_material_blocked` / `permission_blocked` and ask the user.
5. Only after explicit user confirmation, uninstall the old app and install the new Team build.

Uninstall is a destructive/data-loss action. It is a fallback, not the default.

## Why

iOS rejects cross-Team upgrades even when the bundle id is the same. Typical error:

```text
MismatchedApplicationIdentifierEntitlement
Upgrade's application-identifier entitlement string (<newTeam>.<bundleId>) does not match installed application's application-identifier string (<oldTeam>.<bundleId>); rejecting upgrade.
```

This is not a compile failure and not solved by repeated rebuilds with the same new Team.

## Required signing-material preflight

Before attempting old-Team preservation, verify:

- A valid certificate/private key for the old Team exists in keychain.
- A non-expired provisioning profile exists for the old Team and target bundle id or wildcard.
- The profile includes the target device UDID for development builds.
- App extensions have matching profiles/entitlements under the same Team.
- Post-build re-sign, if used, signs app, appex, frameworks, dylibs, and entitlements consistently.

Do not import p12/certificates, modify keychain, or reveal secrets without explicit user confirmation.

## Read-only device/UDID preflight

When signing, provisioning, install, or runtime verification needs a physical
device UDID, first run read-only discovery before asking the user. This is safe
and should not be treated as a sensitive action.

```bash
idevice_id -l
ideviceinfo -u <traditional-udid>
xcrun devicectl list devices
xcrun xctrace list devices
```

Use this when:

- a provisioning profile must include the connected device UDID;
- a command reports "device not found" and may be using the wrong ID type;
- mapping a CoreDevice identifier to the traditional UDID;
- deciding whether the blocker is truly user-actionable, such as unplugged
  device, locked/untrusted phone, disabled Developer Mode, or missing signing
  material.

Record the discovered device name, CoreDevice ID, traditional UDID, OS version,
and source command in `logs/iter-N/env.json` / command logs. Later phases should
reuse this evidence before asking the user again.

## Decision wording for agents

When matching old Team is not currently possible, ask:

```text
An app with bundle id <bundleId> is already installed on the device and belongs to old Team <oldTeam>; the current build belongs to new Team <newTeam>. iOS does not allow cross-Team overwrite.

Recommended path: rebuild or re-sign with the old Team certificate/profile first to preserve data.
The current machine does not have, or has not confirmed, matching old-Team signing material. Do you authorize uninstalling the old device app and installing the current build? This will delete the old app's local data.
```

## Evidence to record

- Installed app identity / install error.
- New app signing identity and embedded profile.
- Whether old-Team certificate/profile exists.
- User decision if uninstall is chosen.
- Final install/launch result.

Last updated: 2026-06-22 (Apple ID login detection + signingPlan strategy ladder; runner no longer hardcodes Automatic)

## AutoMind preflight command

Use this read-only command before installing over an existing app when old/new Team may differ:

```bash
./automind.sh ios-signing-preflight <task-code> \
  --bundle-id <bundle-id> \
  --installed-team <old-team-id> \
  --new-team <new-team-id> \
  --device-id <udid>
```

The command writes:

```text
.automind/tasks/<task-code>/logs/iter-N/ios-signing-preflight.json
.automind/tasks/<task-code>/evaluation.json
```

Possible categories:

- `old_team_signing_available`: prefer old-Team rebuild/re-sign to preserve data.
- `signing_material_blocked`: no matching old-Team certificate/private key and usable profile pair found.
- `provisioning_profile_blocked`: identity exists but no usable profile for bundle/device.

For `signing_material_blocked`, ask whether the user wants to provide/import signing material or allow uninstall. Do not import p12 or uninstall automatically.

## Self-heal signing/provisioning build failures with existing material

When an iOS build fails on signing/provisioning (`requires a development team`,
`No profiles for '<bundle>' were found`, or an XCUITest runner code-sign error
such as `errSecInternalComponent`), do **not** jump straight to `ask_user`.
First reuse signing material that already exists in the project/machine:

```bash
./automind.sh ios-signing-preflight <task-code> --discover [--bundle-id <bundle>] [--installed-team <team>] [--device-id <udid>] [--destination-type device|simulator]
```

`--discover` is read-only and reports:

- every codesigning identity (`security find-identity -v -p codesigning`);
- whether **Xcode is signed in with an Apple ID** and which Teams that account manages (`xcodeAccounts.signedIn` / `accounts` / `managedTeamIds`, read from `defaults read com.apple.dt.Xcode IDEProvisioningTeams`);
- all readable `.mobileprovision` profiles under the project tree, `TTReading/Provisions`, `~/Library/MobileDevice/Provisioning Profiles`, **and** `~/Library/Developer/Xcode/UserData/Provisioning Profiles` (Xcode 16/26 managed-profile location);
- the `DEVELOPMENT_TEAM` / `CODE_SIGN_STYLE` / `PROVISIONING_PROFILE_SPECIFIER` the project already declares in its `*.pbxproj` and any `ExportOptions*.plist` `teamID`;
- an executable `signingPlan` (the single source of truth the `ios-xcuitest` runner consumes) and a `recommendation`.

### Apple ID / signing strategy ladder (verified 2026-06-22)

`signingPlan.strategy` is decided in this priority order. The runner builds
`xcodebuild` settings straight from it — there is **no** hardcoded
`CODE_SIGN_STYLE=Automatic` anymore (that old default fought projects shipping
Manual profiles and silently failed when no Apple ID managed the Team):

1. `simulator_no_sign` — simulator destination needs **no signing at all**: `CODE_SIGNING_ALLOWED=NO`, no Apple ID, no certificate, no profile.
2. `manual_reuse` — a codesigning identity for the build's Team exists **and** a non-expired local profile for that **same Team** (and bundle id, when known) is present. Sign offline with `CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM=<team> PROVISIONING_PROFILE_SPECIFIER=<name>`. **No Apple ID login required.**
3. `automatic` — only when an Apple ID **is signed in AND manages the build's target Team**. Use `-allowProvisioningUpdates DEVELOPMENT_TEAM=<team> CODE_SIGN_STYLE=Automatic` so Xcode generates/manages the profile. Apple requires a signed-in Apple ID for Automatically-manage-signing; being signed in is necessary but **not sufficient** — the account must actually belong to that Team.
4. `blocked` — none of the above; `askUser=True`. The summary states precisely what is missing (no identity for Team / no usable profile incl. the `<bundle>.xctrunner` profile for UI tests / not signed in / signed in but does NOT manage the build's Team).

Key correctness traps fixed:

- **Detect Apple ID, do not assume.** Earlier preflight never read `IDEProvisioningTeams`, so a machine that *was* signed in still reported nothing and runs blindly forced Automatic for 26 iterations. Always populate `xcodeAccounts` from the defaults store.
- **`signed in` ≠ `Automatic works`.** Automatic can only generate profiles for Teams the account manages. Compare `targetTeamManagedByAppleId = chosenTeam in managedTeamIds`, not just `signedIn`.
- **Count only profiles for the build's Team.** Having an identity plus dozens of unrelated profiles does NOT make Manual viable. Filter to `team_profiles` (profile `TeamIdentifier` contains the chosen Team) and, when known, the bundle id, before declaring `manual_reuse`.

If the plan is not `blocked`, rebuild/re-sign with its `buildSettings`/`extraFlags`
and retry; the failure classifier routes signing build failures to
`retry_generator` (`ios.signing.team_or_profile.use_existing` /
`ios.xcuitest.runner.signing_use_existing`) so the loop self-heals without a
human interrupt. Only when the plan is `blocked` (or a re-sign attempt still
fails) record `signingMaterialExhausted` / `signingRetryExhausted` /
`noUsableSigningMaterial`; the classifier then escalates to `ask_user`
(category `real_device_or_signing`). Simulator verification never needs signing,
so prefer it when real-device signing is genuinely blocked and device coverage
is not required.

## Verified project-native XCUITest success path (re-confirmed 2026-06-12)

When the target project already ships its own UI test target (unlike the
external-runner case in `summaries/preloaded/ios-external-xcuitest-runner.md`),
run that native target directly. Re-confirmed on a connected iPhone with
`demos/ios-simulator-demo`:

```bash
xcodebuild test \
  -project demos/ios-simulator-demo/AutoMindIOSDemo.xcodeproj \
  -scheme AutoMindIOSDemo \
  -destination id=<UDID> \
  DEVELOPMENT_TEAM=<TEAM_ID> CODE_SIGN_STYLE=Automatic -allowProvisioningUpdates
```

- Target project ships `AutoMindIOSDemo` (app) + `AutoMindIOSDemoUITests` — no external runner needed.
- Signing recipe is no longer hardcoded: the runner consumes `signingPlan` (Manual reuse offline when the Team's identity+profile exist; Automatic + `-allowProvisioningUpdates` only when a signed-in Apple ID manages the Team). The recipe above is the `automatic` branch for a Personal/Apple-Development team the logged-in account manages; Xcode then generates both `ai.openclaw.automind.demo` and `...uitests.xctrunner` profiles.
- Use the **traditional UDID** for `-destination id=`; the CoreDevice ID is for `devicectl`.
- Success markers: `Running tests...` -> `testInputEchoFlow` / `testProbeButtonChangesStateToCompleted` / `testScrollToBottomItem` each pass -> `Executed 3 tests, with 0 failures` -> `** TEST SUCCEEDED **`. These exercise real project-native tap / input / state-assert / scroll on the device.

### Two first-install device blockers that are NOT code failures

Both are signing/install/trust policy blockers (classifier `permission_blocked`, `ask_user`), not compile or device-capability failures. Build + signing can fully succeed and still hit these at install/launch time:

1. **Free developer profile app limit.** Error: `Unable to Install ... This device has reached the maximum number of installed apps using a free developer profile`. The classifier matches it to `ios.signing.personal_team.app_limit`. Resolve by reusing an existing demo bundle id, or (with user approval) uninstalling unneeded Personal-Team demo apps to free quota:

   ```bash
   xcrun devicectl device info apps --device <CoreDeviceID> | grep -iE 'openclaw|automind'
   xcrun devicectl device uninstall app --device <CoreDeviceID> <bundle-id>
   ```

   Uninstall is destructive, so confirm scope with the user first. Re-confirmed 2026-06-12: clearing the leftover external-runner demo apps freed quota and the native test then installed and ran to `** TEST SUCCEEDED **`.
2. **Developer App Certificate not trusted.** On first install of a Personal-Team / Apple Development build, the device may refuse to launch until the user trusts the profile on the iPhone (Settings -> General -> VPN & Device Management -> trust the developer app). The classifier matches it to `ios.permission.developer_profile.not_trusted`. This is a **one-time** trust per developer identity, not a per-test action; once trusted, subsequent UI test runs proceed straight into XCTest with no prompt.

### external runner vs project-native: which on-device prompt to expect

- **External UI runner** (drives an already-installed app by bundle id): the run may prompt for the **iPhone passcode** mid-session as part of the success path.
- **Project-native UITest** (project's own UI test target): the gate is **trusting/installing the app** — trust once on the device, then re-run; no passcode needed each time.

Prefer the project-native UI test target when the project already has one (richer `.xcresult`, fewer moving parts); fall back to the external runner only when there is no usable UI test target/scheme.
