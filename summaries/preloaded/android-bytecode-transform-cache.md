---
name: android-bytecode-transform-build-cache
description: "Android bytecode transform transform incremental-cache diagnostics + how a startup logging framework/hook (here ProjectLogger) can route the native log channel, why android.util.Log/logcat visibility depends on the logger's syslog/mirror switch (the logger's debug mirror switch -> logcat works on debug), and how to force a correct full rebuild."
use_when:
  - "an Android project applies bytecode transform gradle plugins (bytecode-transform.gradle, security_aop / securityAopPlugin, method_call_opt) during the build"
  - "freshly edited module/KMP code does not seem to take effect in the installed APK"
  - "Log.v/d/i/w/e output is missing at runtime even though logging code exists"
  - "the app initializes a logging framework/hook at startup (ProjectLogger/ProjectLogger, Logan, Xlog/Mars, Timber, custom Log shim) and you are unsure whether android.util.Log should appear in logcat"
solves:
  - "explains the bytecode transform transform incremental-cache trap that drops module/KMP changes from the final APK"
  - "explains why android.util.Log/logcat visibility depends on the takeover logger's syslog/mirror switch (for ProjectLogger, setDebug(true) mirrors to logcat)"
  - "gives a deterministic clean + full-rebuild recovery path so changes actually enter the APK"
---
# Android bytecode transform Transform / Build-Cache + Log Evidence

Reusable Android playbook for projects that run bytecode transform gradle plugins
(`gradle/bytecode-transform.gradle`, `security_aop` / `securityAopPlugin`, `method_call_opt`)
and a startup logging framework/hook. Two recurring traps:

1. **Stale bytecode transform transform cache** silently keeps an old processed result, so
   freshly edited module/KMP code never reaches the APK (no compile error — the
   change just is not there).
2. **A startup logger/hook owns the native log channel**, so whether
   `android.util.Log` reaches logcat depends on that logger's syslog/mirror
   switch. With the switch off, runtime evidence looks "impossible" even though
   the code ran.

The logger in this project is a project-specific logger; its mirror switch is
`the logger's debug mirror switch` (ON on debug builds -> logcat works). Other projects use
different loggers (Logan, Xlog/Mars, Timber, custom `Log` shims) with their own
switches — the habit (check the mirror switch before blaming code or device)
matters more than the specific library.

## Quick playbook

When an Android change "has no effect" or a log/event "does not show up":

1. **Make sure the change is really in the APK first.** Clean the bytecode transform cache and
   full-rebuild (see below) before debugging any runtime symptom. Most
   "impossible" results are stale-cache results.
2. **Do not over-trust OR over-dismiss `android.util.Log` / logcat.** If the app
   installs a logger/hook at startup, check its syslog/mirror switch. On debug
   builds it is usually ON (for ProjectLogger, `the logger's debug mirror switch`), so `android.util.Log`
   AND the project wrapper DO reach logcat. A missing logcat tag only proves
   takeover when syslog is OFF; otherwise keep debugging ordinary causes (wrong
   tag/filter, change not in APK, branch not hit).
3. **Capture evidence on a non-logcat channel** (see recipes). A `filesDir` debug
   file or the real analytics sink is the robust proof; logcat is only a link
   sanity check.

## Make the change actually enter the APK (bytecode transform cache reset)

```bash
rm -rf app/build/bytecode transform                              # drop stale transform cache
./gradlew :app:assembleDebug --no-build-cache       # force full reprocess
```

Capture the build log + install/runtime evidence so the path is reusable.

## How to capture solid evidence

`adb` assumes it is on PATH (else use `$ANDROID_HOME/platform-tools/adb`).
Replace `<pkg>` with the application id (e.g. `com.example.app`). Prefer these channels
in order; stop at the first that proves the behavior.

1. **`filesDir` debug file — most deterministic, recommended default.** Append a
   structured line from the code under test, then read it back; independent of
   ProjectLogger/logcat. Gate behind `BuildConfig.DEBUG`, remove or hard-gate before
   finishing, and record under `evaluation.json.verificationUnblockChanges` if
   temporary.

   ```bash
   adb shell run-as <pkg> cat files/automind_probe.txt   # needs a debuggable build
   ```

2. **Real analytics sink — proves the reporting path, not just a log.** Use the
   project's event-verify/debug toggle (`EventVerify` / `EventsSenderUtils` via
   `DebugManager`), capture the upload request, or read the persisted analytics
   DB/queue. Solid evidence = event name + key params tied to your action.

   ```bash
   adb shell run-as <pkg> ls -R databases files | grep -iE "event|report|applog|track"
   adb shell run-as <pkg> ls -R files | grep -iE "alog|agilelog|log"  # ProjectLogger files
   ```

3. **Confirm the change is in the APK** (separates "code did not run" from "wrong
   channel"): `adb shell pm path <pkg>` -> pull base.apk -> grep the dex for the
   probe symbol.

4. **Lifecycle proof** (app reached the target screen/state):

   ```bash
   adb shell am force-stop <pkg>; adb shell am start -n <pkg>/<launch-activity>
   adb shell dumpsys activity activities | grep -E 'mResumedActivity|topResumedActivity'
   ```

5. **logcat only as a channel-sanity check.** Write a device-side `SANITY` line
   (`adb shell log -p e -t SANITY ...`) and read it back. If `SANITY` is visible
   but your app tag is not, check the syslog switch + ordinary causes before
   concluding takeover.

**Minimum "solid evidence" bar for an analytics/runtime TC:** lifecycle proof +
the event observed on a non-logcat channel (recipe 1 or 2), with the exact
captured line/payload attached. A bare "expected logcat tag missing" is NOT a
pass and NOT a hard product failure when a logger/hook may own the log channel.

## Why (root-cause notes)

- **bytecode transform cache staleness:** the bytecode transform transform chain (incl. `securityAopPlugin`)
  caches under `app/build/bytecode transform`. When it fails to invalidate an upstream
  module's changed artifact, it reuses old output and the new code never enters
  the APK. Classify as a **build-cache/transform staleness blocker**, not a
  product bug; do not blame one named plugin without evidence. Fix with the cache
  reset above.
- **Log-channel takeover:** a startup logger (here `ProjectLogger.init()`) reroutes
  `android.util.Log` into its own files/sink. Whether it ALSO reaches logcat is
  the syslog mirror: `the logger's debug mirror switch` (debug default) -> mirrored to logcat;
  off -> only in ProjectLogger files. Proven on a real debug build: with syslog ON both
  native `Log` and `LogWrapper -> ProjectLogger` reached logcat. So treat "missing tag" as
  takeover only after confirming syslog is OFF.
- **`method_call_opt` log stripping** is gated by `enableInDebug false`, so a
  standard debug build does NOT strip `android.util.Log.*`. Only suspect it when
  the running variant has `enableInDebug true`.

## Avoid paths

- Do not trust an incremental build right after a KMP/shared-module change; clean
  `app/build/bytecode transform` first.
- Do not classify "my change has no effect" as a product bug before a
  `--no-build-cache` full rebuild.
- Do not conclude "device ROM blocks my logs" or "logger took over" before
  checking the syslog/mirror switch and ruling out ordinary causes (wrong
  tag/filter, change not in APK, branch not hit).
- Do not assume `method_call_opt` stripped debug logs (gated by `enableInDebug`).

Last updated: 2026-06-23
