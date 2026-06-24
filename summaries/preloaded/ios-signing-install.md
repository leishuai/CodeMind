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

Common iOS policy for installing development builds onto physical devices when an
app with the same bundle id may already be installed.

## Default policy: preserve app data first

When installing over an existing app with the same bundle id:

1. Inspect the installed app identity if possible (bundle id, Team ID,
   application-identifier entitlement).
2. Prefer signing the new build with the **same Team/application-identifier** as
   the installed app; rebuild or re-sign with that Team to preserve local data.
3. If matching signing material is missing/invalid, classify as
   `signing_material_blocked` / `permission_blocked` and ask the user.
4. Only after explicit user confirmation, uninstall the old app and install the
   new-Team build. Uninstall is destructive/data-loss — a fallback, not default.

iOS rejects cross-Team upgrades even when the bundle id matches. Typical error
(`MismatchedApplicationIdentifierEntitlement` — installed `<oldTeam>.<bundleId>`
vs new `<newTeam>.<bundleId>`). This is **not** a compile failure and is **not**
solved by repeated rebuilds with the same new Team.

## Signing-material preflight (before old-Team preservation)

Verify: a valid certificate/private key for the old Team in keychain; a
non-expired provisioning profile for the old Team and target bundle id/wildcard
including the device UDID; matching profiles/entitlements for app extensions
under the same Team; consistent re-sign across app, appex, frameworks, dylibs,
and entitlements. Do **not** import p12/certificates, modify keychain, or reveal
secrets without explicit user confirmation.

## Read-only device/UDID preflight (safe, not sensitive)

When signing/provisioning/install/runtime needs a device UDID, run read-only
discovery before asking the user:

```bash
idevice_id -l
ideviceinfo -u <traditional-udid>
xcrun devicectl list devices
xcrun xctrace list devices
```

Use it when a profile must include the device UDID, a command reports "device not
found" (wrong ID type), mapping a CoreDevice id to traditional UDID, or deciding
whether the blocker is truly user-actionable (unplugged/locked/untrusted phone,
Developer Mode off, missing signing material). Record device name, CoreDevice ID,
traditional UDID, OS version, and source command in `logs/iter-N/env.json`; later
phases reuse this before asking again.

## Decision wording when matching old Team is not possible

```text
An app with bundle id <bundleId> is already installed and belongs to old Team
<oldTeam>; the current build belongs to new Team <newTeam>. iOS does not allow
cross-Team overwrite. Recommended: rebuild/re-sign with the old-Team
certificate/profile first to preserve data. This machine does not have, or has
not confirmed, matching old-Team signing material. Do you authorize uninstalling
the old device app and installing the current build? This deletes its local data.
```

## AutoMind preflight commands

```bash
# read-only check before installing over an existing app when Teams may differ
./automind.sh ios-signing-preflight <task-code> --bundle-id <bundle-id> \
  --installed-team <old-team-id> --new-team <new-team-id> --device-id <udid>

# read-only discovery to self-heal signing/provisioning build failures
./automind.sh ios-signing-preflight <task-code> --discover \
  [--bundle-id <bundle>] [--installed-team <team>] [--device-id <udid>] \
  [--destination-type device|simulator]
```

Both write `logs/iter-N/ios-signing-preflight.json` and `evaluation.json`.
`--discover` reports: every codesigning identity; whether Xcode is signed in with
an Apple ID and which Teams it manages (`IDEProvisioningTeams`); readable
`.mobileprovision` profiles (project tree, `TTReading/Provisions`,
`~/Library/MobileDevice/Provisioning Profiles`, and Xcode 16/26
`~/Library/Developer/Xcode/UserData/Provisioning Profiles`); the
`DEVELOPMENT_TEAM`/`CODE_SIGN_STYLE`/`PROVISIONING_PROFILE_SPECIFIER` already in
the `*.pbxproj` / `ExportOptions*.plist`; and an executable `signingPlan` (the
single source of truth the `ios-xcuitest` runner consumes) + a `recommendation`.

Preflight categories: `old_team_signing_available` (prefer old-Team
rebuild/re-sign), `signing_material_blocked`, `provisioning_profile_blocked`. For
`signing_material_blocked`, ask whether to provide/import material or allow
uninstall — never import p12 or uninstall automatically.

## Signing strategy ladder (no hardcoded Automatic)

When a build fails on signing/provisioning (`requires a development team`,
`No profiles for '<bundle>' were found`, runner code-sign `errSecInternalComponent`),
do NOT jump straight to `ask_user`. The runner builds `xcodebuild` settings from
`signingPlan.strategy`, decided in priority order:

1. `simulator_no_sign` — simulator needs no signing (`CODE_SIGNING_ALLOWED=NO`).
2. `manual_reuse` — identity for the build's Team exists AND a non-expired local
   profile for that **same Team** (and bundle id when known) is present. Sign
   offline: `CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM=<team>
   PROVISIONING_PROFILE_SPECIFIER=<name>`. **No Apple ID login required.**
3. `automatic` — only when an Apple ID is signed in AND manages the build's Team.
   Use `-allowProvisioningUpdates DEVELOPMENT_TEAM=<team> CODE_SIGN_STYLE=Automatic`.
   Signed-in is necessary but not sufficient: the account must belong to that Team
   (`targetTeamManagedByAppleId`).
4. `blocked` — none of the above; `askUser=True`, stating exactly what is missing
   (no identity for Team / no usable profile incl. `<bundle>.xctrunner` for UI
   tests / not signed in / signed in but does not manage the Team).

If not `blocked`, rebuild/re-sign with the plan's settings and retry; the
classifier routes signing build failures to `retry_generator`
(`ios.signing.team_or_profile.use_existing` /
`ios.xcuitest.runner.signing_use_existing`) so the loop self-heals. Only on
`blocked` (or a re-sign still failing) record `signingMaterialExhausted` /
`signingRetryExhausted` / `noUsableSigningMaterial`; the classifier escalates to
`ask_user` (`real_device_or_signing`). Simulator verification never needs signing
— prefer it when real-device signing is genuinely blocked and device coverage is
not required.

## Project-native XCUITest success path

When the target project ships its own UI test target (unlike the external-runner
case in `summaries/preloaded/ios-external-xcuitest-runner.md`), run it directly:

```bash
xcodebuild test -project <proj>.xcodeproj -scheme <scheme> \
  -destination id=<traditional-UDID> \
  DEVELOPMENT_TEAM=<TEAM_ID> CODE_SIGN_STYLE=Automatic -allowProvisioningUpdates
```

- Use the **traditional UDID** for `-destination id=` (CoreDevice ID is for
  `devicectl`). The signing recipe is not hardcoded — it follows `signingPlan`
  (the line above is the `automatic` branch for a Team the logged-in account
  manages).
- Success markers: `Running tests...` -> each test passes ->
  `Executed N tests, with 0 failures` -> `** TEST SUCCEEDED **`.

### Two first-install device blockers that are NOT code failures

Build + signing can fully succeed and still hit these at install/launch. Both are
policy blockers (classifier `permission_blocked`, `ask_user`), not compile or
device-capability failures:

1. **Free developer profile app limit** (`...maximum number of installed apps
   using a free developer profile`; `ios.signing.personal_team.app_limit`).
   Resolve by reusing an existing demo bundle id, or with user approval
   uninstalling unneeded Personal-Team apps to free quota:

   ```bash
   xcrun devicectl device info apps --device <CoreDeviceID> | grep -iE 'openclaw|automind'
   xcrun devicectl device uninstall app --device <CoreDeviceID> <bundle-id>
   ```

2. **Developer App Certificate not trusted** (`ios.permission.developer_profile.not_trusted`).
   First install of a Personal-Team/Apple-Development build may refuse to launch
   until the user trusts the profile on the iPhone (Settings -> General -> VPN &
   Device Management). One-time per developer identity, not per-test.

### External runner vs project-native: which on-device prompt to expect

- **External UI runner** (drives an already-installed app by bundle id): may
  prompt for the **iPhone passcode** mid-session as part of the success path.
- **Project-native UITest**: the gate is **trusting/installing the app** — trust
  once, then re-run; no passcode each time.

Prefer the project-native UI test target when one exists (richer `.xcresult`,
fewer moving parts); fall back to the external runner only when there is no
usable UI test target/scheme.

Last updated: 2026-06-23
