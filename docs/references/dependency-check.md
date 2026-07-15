# Dependency and Preflight Checklist

This reference describes how CodeMind should check dependencies before validation. It is intentionally generic and should not contain machine-specific one-off results.

Use it with:

- [`../phase1-initialization.md`](../phase1-initialization.md) for workspace initialization;
- [`../phase3-verification.md`](../phase3-verification.md) for verification loop policy;
- [`verification-flow.md`](verification-flow.md) for concrete validation commands.

---

## Core rule

Do not classify missing tools, unavailable devices, signing problems, or permission blockers as product-code failures.

Separate dependency handling into three layers:

1. **CodeMind helper dependencies** — CodeMind-owned Python helper packages for
   Android/iOS/visual verification. These may be installed automatically into
   project-local `.venv-android-tools`, `.venv-ios-tools`, or
   `.venv-visual-tools` when required by the selected verifier.
2. **Target project dependencies** — web/client/server dependencies owned by the
   user's project. Use project-native package managers, lockfiles, and scripts;
   do not install arbitrary packages or rewrite lockfiles as a CodeMind helper
   action.
3. **System/high-impact dependencies** — Xcode, Android SDK, Node, Docker,
   database services, browser drivers, signing/certificates, device trust,
   private registry credentials, sudo services, or privileged daemons. Check and
   report them; ask the user before installing or changing them.

Use structured categories such as:

```text
tool_missing
environment_blocked
mobile_device_unavailable
permission_blocked
install_failure
launch_failure
```

If the human must decide or perform an external/sensitive action, ask a specific question instead of guessing.

---

## General tools

Check:

```bash
command -v python3
command -v git
command -v bash
```

Recommended evidence:

```bash
python3 --version
git --version
pwd
```

Record actual tool paths and versions when they affect validation.

---

## Project dependency discovery command

Use this optional read-only command during Plan/Verify only when the model,
project docs, CI files, lockfiles, or `Reuse.md` do not make the dependency path
clear, or when a dependency/tooling failure needs classification:

```bash
automind dependency-check [task-code] [iteration]
```

It writes `dependency-check.json` under the task log folder when `task-code` is
given, otherwise under `.automind/setup/dependency-check/`. The report detects
common markers and recommends project-native commands, but it does **not** run
dependency installation.

When used, its output can refine:

- `TestCases.md` preconditions and setup steps;
- `Plan.md` verification commands;
- `Validation.md` environment/preflight evidence.

If no markers are found, consult repository docs or ask the user before
inventing install commands.

---

## Web projects

Typical markers:

```text
package.json
pnpm-lock.yaml / yarn.lock / package-lock.json / bun.lock
vite.config.* / next.config.* / playwright.config.* / cypress.config.*
```

Lockfile-first install commands:

```bash
pnpm install --frozen-lockfile
yarn install --immutable        # Yarn Berry
yarn install --frozen-lockfile  # Yarn classic
npm ci
bun install --frozen-lockfile
```

Verification examples:

```bash
npm run build
npm test
pnpm test:e2e
yarn playwright test
```

Rules:

- use the package manager implied by the lockfile or `packageManager` field;
- prefer `npm ci` / frozen/immutable installs when lockfiles exist;
- ask before deleting `node_modules`, changing lockfiles, switching package
  managers, installing browser binaries/drivers, or fixing private registry
  authentication;
- for UI work, start the dev/test server and run browser/E2E checks when the
  testcase requires runtime evidence.

---

## Server projects

Common project-native commands:

| Ecosystem | Markers | Install/prep | Verify |
|---|---|---|---|
| Python | `requirements.txt`, `pyproject.toml`, `poetry.lock`, `uv.lock` | `python3 -m venv .venv && pip install -r requirements.txt`, `uv sync --frozen`, `poetry install --sync` | `pytest`, `python manage.py test`, project script |
| Node server | `package.json` with server deps/scripts | same lockfile-first JS commands | `npm test`, `npm run build`, API/integration script |
| JVM | `gradlew`, `build.gradle`, `pom.xml`, `mvnw` | Gradle/Maven resolves during build/test | `./gradlew test`, `./mvnw test` |
| Go | `go.mod`, `go.sum` | `go mod download` | `go test ./...` |
| Rust | `Cargo.toml`, `Cargo.lock` | `cargo fetch --locked` | `cargo test` |
| Docker/services | `Dockerfile`, `docker-compose.yml` | `docker compose config`, then build/up if approved | service health/API checks |

Rules:

- do not silently install databases, Docker Desktop, JDKs, language runtimes, or
  OS packages;
- do not silently start long-running services, expose ports, or run privileged
  containers;
- missing private package indexes, registry credentials, Docker daemon, database
  availability, or network/proxy access should be `environment_blocked` /
  `tool_missing` / `ask_user`, not product failure.

---

## Client projects

Client includes mobile, desktop, browser UI, and packaged app projects.

Use project-native dependency/setup commands:

- Android: Gradle wrapper (`./gradlew assembleDebug`, tests) plus
  CodeMind Android helper venv only for UI/device probing.
- iOS: Xcode/SPM/CocoaPods/Bundler commands plus CodeMind iOS helper venv only
  for screenshot/app-smoke helpers.
- Web UI: JS lockfile install, dev/test server, browser/E2E runner.
- Desktop/Electron/Tauri/React Native/Flutter: project-native package manager
  and runner documented by the repo.

Rules:

- ask before changing signing, provisioning, device trust, emulator/device
  state, system SDKs, or privileged services;
- for App/UI work, TestCases must specify build/install/deploy/start,
  launch/open, entry screen/page/route/activity/state, action sequence,
  assertions, and evidence;
- if runtime UI execution is blocked, record the blocker and route to
  `ask_user` or `replan` rather than passing a static-only check.

---

## iOS checklist

### Required or commonly used tools

```bash
command -v xcodebuild
command -v xcrun
command -v idevice_id || true
command -v xcodebuildmcp || true
command -v ios || true
```

Optional CodeMind screenshot helper setup, also auto-run by screenshot/app-smoke evaluators when required:

```bash
automind setup-automation-tools ios
```

This installs packages from the CodeMind runtime `requirements/ios-tools.txt` into the target workspace `.venv-ios-tools` only. It does not install Xcode, change signing material, trust devices, or start `tunneld`/sudo services.

## Visual fallback helpers

When no vision-capable model is available, CodeMind can still run deterministic
image checks for screenshot readability, dimensions, crop inspection, perceptual
hash, and baseline comparison:

```bash
automind setup-automation-tools visual
automind visual-inspect <task-code> --image logs/iter-1/screenshot.png
automind visual-inspect <task-code> --image logs/iter-1/screenshot.png --baseline references/baseline.png --max-rms 8
```

This installs packages from `requirements/visual-tools.txt` into
`.venv-visual-tools` only. It does not install OCR engines, browser drivers,
device SDKs, or any privileged services. If a testcase requires semantic image
understanding and no vision model, baseline, OCR, bounds/hierarchy, or human
confirmation is available, the correct result is `blocked` / `ask_user` /
`replan`, not `pass`.

`automind visual-inspect` automatically prefers the project-local
`.venv-visual-tools/bin/python` when it exists. If the current Python cannot
import Pillow, the wrapper may create/repair `.venv-visual-tools` with
`setup-automation-tools visual` before writing `logs/iter-N/visual-inspection.json`.

### Device readiness

Check:

```bash
xcrun xctrace list devices
xcrun devicectl list devices
xcodebuild -showdestinations -scheme <scheme>
```

Ask the human to confirm:

- iPhone has trusted this Mac;
- Developer Mode is enabled for iOS 16+ real-device work;
- device is unlocked and screen stays on;
- app signing team / provisioning profile can target the device;
- developer profile is trusted on device when using Personal Team / Apple Development.

### Classification hints

| Symptom | Category |
|---|---|
| Xcode not installed or `xcodebuild` missing | `tool_missing` |
| Device not visible | `mobile_device_unavailable` |
| Developer Mode disabled | `permission_blocked` |
| No signing team/profile | `permission_blocked` or `environment_blocked` |
| App install fails due to signature | `install_failure` / `permission_blocked` |
| Launch denied because developer profile is untrusted | `permission_blocked` |

---

## Android checklist

### Required or commonly used tools

```bash
command -v adb
adb version
adb devices -l
```

If `adb` is not in `PATH`, common macOS location:

```text
$HOME/Library/Android/sdk/platform-tools/adb
```

Recommended Python toolchain:

```bash
automind setup-automation-tools android
```

Android preflight/evaluator may run it automatically when required helper packages are missing; users can also run it up front. It installs packages from the CodeMind runtime `requirements/android-tools.txt` into the target workspace `.venv-android-tools` only; it does not install Android Studio, Android SDK/platform-tools, `adb`, or change device settings.

### Device readiness

Ask the human to confirm:

- USB debugging is enabled;
- this computer is authorized;
- device is unlocked and screen stays on;
- device is not on lock screen, notification shade, permission dialog, or SystemUI overlay;
- vendor-specific debug restrictions are disabled when required.

### Classification hints

| Symptom | Category |
|---|---|
| `adb` missing | `tool_missing` |
| `adb devices` empty | `mobile_device_unavailable` |
| device is `unauthorized` | `permission_blocked` |
| hierarchy/screenshot shows SystemUI instead of target app | `mobile_device_unavailable` or `permission_blocked` |
| APK install conflict | `install_failure` |
| launch activity not found | `launch_failure` or `needs_replan` |

---

## Script / generic project checklist

Check the command named by `scriptCommand` / `verifyCommand`:

```bash
bash -lc '<verify-command>'
```

Record:

- cwd;
- exact command;
- interpreter/package manager version;
- stdout/stderr;
- exit code;
- any required environment variables.

If the command is missing or ambiguous, classify as `tool_missing`, `environment_blocked`, or `needs_replan`.

---

## Evidence to record

Preflight should write enough evidence for the next agent to avoid rediscovery:

```text
logs/iter-N/env.json
logs/iter-N/commands.md
logs/iter-N/preflight.log
```

Recommended `env.json` fields:

```json
{
  "cwd": "...",
  "os": "...",
  "python": {"executable": "...", "version": "..."},
  "node": {"executable": "...", "version": "..."},
  "tools": {"adb": "...", "xcodebuild": "..."},
  "android": {"deviceSerial": "...", "sdk": "..."},
  "ios": {"deviceId": "...", "runtime": "..."}
}
```

Do not print or store secrets, certificates, tokens, or private signing material.
