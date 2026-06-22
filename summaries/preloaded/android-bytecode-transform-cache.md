---
name: android-bytecode-transform-build-cache
description: "Android bytecode transform transform incremental-cache diagnostics + how a startup logging framework/hook (here ProjectLogger) can route the native log channel, why android.util.Log/logcat visibility depends on the logger's syslog/mirror switch (the logger's debug mirror switch -> logcat works on debug), and how to force a correct full rebuild."
use_when:
  - "an Android project applies bytecode transform gradle plugins (bytecode-transform.gradle, security_aop / securityAopPlugin, method_call_opt) during the build"
  - "freshly edited code in a module (especially a KMP / appinterval / shared module) does not seem to take effect in the installed APK"
  - "Log.v/d/i/w/e output is missing at runtime even though logging code exists or is visible in the APK"
  - "the app initializes a logging framework/hook at startup (e.g. a project-specific logger, Logan, Xlog/Mars, Timber, a custom Log shim) and you are unsure whether android.util.Log should appear in logcat"
  - "verification keeps failing after rebuild and you suspect stale build output rather than a product-code bug"
solves:
  - "explains the bytecode transform transform incremental-cache trap that drops module/KMP changes from the final APK"
  - "explains why android.util.Log/logcat visibility depends on the takeover logger's syslog/mirror switch (for ProjectLogger, setDebug(true) mirrors to logcat; with it off, output only lands in ProjectLogger files)"
  - "separates 'logs are stripped by method_call_opt' from 'logs disappear because of stale cache, log-channel routing with syslog off, or ordinary tag/filter/branch causes'"
  - "gives a deterministic clean + full-rebuild recovery path so changes actually enter the APK"
---
# Android bytecode transform Transform / Build-Cache + Log Evidence

Reusable Android playbook for projects that run bytecode transform gradle plugins
(`gradle/bytecode-transform.gradle`, `security_aop` / `securityAopPlugin`, `method_call_opt`)
and a startup logging framework/hook. Two recurring traps: (1) the bytecode transform
transform cache silently keeps a stale result so freshly edited module code never
reaches the APK, and (2) a logging framework/hook initialized at startup routes
the native log channel, so whether `android.util.Log` reaches logcat depends on
that logger's syslog/mirror switch — making runtime evidence look "impossible"
when the switch is off. The logger here is a project-specific logger; its mirror switch is
`the logger's debug mirror switch` (ON in debug -> logcat works). Other projects use different
loggers/hooks (Logan, Xlog/Mars, Timber, custom `Log` shims) with their own
switches — the defensive habit (check the mirror switch before blaming code or
the device) matters more than the specific library.

**Part A (How to do) comes first — it is what you act on next time. Part B (Why)
explains the root causes and is kept for when you need to justify the actions.**

---

# Part A — How to do (action first)

## A1. Quick playbook

When an Android code change "has no effect" or a log/event "does not show up":

1. **Make sure the change is really in the APK.** Clean the bytecode transform cache and do a
   full rebuild (A2) before debugging anything runtime. Most "impossible" results
   are stale-cache results.
2. **Do not blindly trust OR blindly dismiss `android.util.Log` / logcat.** Many
   apps install a logging framework or hook at startup (here a project-specific logger initialized at startup; elsewhere Logan, Timber trees, Xlog/Mars, custom `Log` shims,
   `System.out` redirection, etc.) that can reroute the native log channel.
   BUT such loggers usually have a "mirror to system log / syslog" switch that is
   ON in debug builds — for ProjectLogger, `the logger's debug mirror switch` turns syslog on, so on a
   debug build `android.util.Log.*` AND the project wrapper (`LogWrapper -> ProjectLogger`)
   DO reach logcat (proved on device). So: a missing logcat tag is only conclusive
   takeover evidence when syslog is OFF; otherwise keep debugging the ordinary
   causes (wrong tag/filter, change not in APK, branch not hit) and do not jump to
   "code did not run" or "device-ROM blocks logs".
3. **Capture evidence on a non-logcat channel (A3).** Prefer a `filesDir` debug
   file or the real analytics sink. logcat is only a link-sanity check.
4. **Meet the minimum evidence bar (A3).** Lifecycle proof + the event observed
   on a non-logcat channel, with the exact captured line/payload attached.

## A2. Make the change actually enter the APK (bytecode transform cache reset)

```bash
# 1. Remove the stale bytecode transform transform cache (and, if needed, module build output).
rm -rf app/build/bytecode transform

# 2. Force a full, non-incremental rebuild so the transform reprocesses everything.
./gradlew :app:assembleDebug --no-build-cache
```

After this, the module/KMP change should be present in the APK. Capture the
build log and the install/runtime evidence so the path is reusable.

## A3. How to capture solid evidence (recommended recipes)

`adb` below assumes it is on PATH; otherwise use the SDK path
(`$ANDROID_HOME/platform-tools/adb` or `~/Library/Android/sdk/platform-tools/adb`).
Replace `<pkg>` with the application id (e.g. `com.example.app`).

Prefer evidence channels in this order. Stop at the first one that proves the
behavior; do not keep re-running logcat probes against `android.util.Log`.

1. **`filesDir` debug file — most deterministic, recommended default.**
   Have the code under test append a single structured line to an app-private
   file, then read it back. This does not depend on ProjectLogger/logcat at all.

   ```kotlin
   // debug-only diagnostic write, gate behind BuildConfig.DEBUG / a debug switch
   File(context.filesDir, "automind_probe.txt").appendText(
       "evt=playback_stop reason=user_pause ts=${System.currentTimeMillis()}\n"
   )
   ```

   ```bash
   adb shell run-as <pkg> cat files/automind_probe.txt
   # solid evidence = the exact line(s) you wrote, with expected fields/values
   ```

   Notes: `run-as` requires a debuggable build. Remove or hard-gate the probe
   write before finishing the task; record it under
   `evaluation.json.verificationUnblockChanges` if it is temporary.

2. **Real analytics sink — proves the actual reporting path, not just a log.**
   Verify the event truly reached the reporting layer. Pick whichever the
   project supports:
   - Event-verify / debug toggle (these projects expose `EventVerify` /
     `EventsSenderUtils` via `DebugManager`); enable it, trigger the action, and
     capture the verified event payload.
   - Capture the upload request (charles/mitmproxy or the in-app request log) and
     assert event name + key params.
   - Read the local analytics DB/queue if the SDK persists events before upload.

   ```bash
   # discover the analytics DB/files the app persists
   adb shell run-as <pkg> ls -R databases files | grep -iE "event|report|applog|track"
   ```

   Solid evidence = event name + key parameters observed at the sink, tied to the
   action you performed.

   You can also read the ProjectLogger files themselves (with syslog OFF this is the only
   place `android.util.Log` and `LogWrapper` output lands; with syslog ON it is
   mirrored to both logcat and the ProjectLogger files):

   ```bash
   adb shell run-as <pkg> ls -R files | grep -iE "alog|agilelog|log"
   # pull the alog dir, then decode with the project's ProjectLogger decoder if encrypted
   ```

3. **Confirm the change is in the APK before trusting any runtime result.**
   This separates "code did not run" from "evidence channel was wrong".

   ```bash
   # bytecode presence in the installed APK
   adb shell pm path <pkg>                      # find base.apk path
   adb pull <base.apk path> /tmp/base.apk
   # then grep the dex via your decompiler of choice for the probe string/symbol
   ```

4. **App actually ran the target screen/state (lifecycle proof).**

   ```bash
   adb shell am force-stop <pkg>
   adb shell am start -n <pkg>/<launch-activity>
   sleep 6
   adb shell dumpsys activity activities | grep -E 'mResumedActivity|topResumedActivity'
   adb shell pidof <pkg>
   ```

5. **logcat only as a channel-sanity check, never as the analytics proof.**
   Use it to prove the logcat link itself is alive, then stop relying on
   `android.util.Log` for product truth.

   ```bash
   adb logcat -c
   adb shell log -p e -t SANITY "host-smoke-$(date +%s)"   # device-side write
   adb logcat -d -v time -s SANITY:E                       # should be visible
   adb logcat -d -v time --pid=$(adb shell pidof <pkg> | tr -d '\r') | tail -50
   ```

   If the `SANITY` device-side write is visible but your app's `android.util.Log`
   tag is not, do NOT immediately conclude takeover: first check the syslog switch
   (debug builds usually have `the logger's debug mirror switch` -> logcat works) and the
   ordinary causes (tag/filter, change not in APK, branch not hit). Only if syslog
   is OFF is the missing tag explained by log-channel takeover (Part B) — then
   switch to recipe 1 or 2.

**Minimum "solid evidence" bar for an analytics/runtime TC:** lifecycle proof
(recipe 4) + the event observed on a non-logcat channel (recipe 1 or 2), with
the exact captured line/payload attached. A bare "expected logcat tag missing"
is NOT a pass and NOT a hard product failure when a logging framework/hook may
have taken over the native log channel (ProjectLogger here).

### Channel matrix (debug build with ProjectLogger)

The takeover logger usually has a "mirror to system log" switch. For ProjectLogger it is
`the logger's debug mirror switch` (and `Builder.setSyslog(true)` at init), which drives a
native `nativeSetSyslog(ref, true)`. **debug builds typically call
`the logger's debug mirror switch`, so syslog is ON by default and logcat DOES receive a
copy.** Whether a channel reaches logcat therefore depends on the syslog state:

| Channel | syslog ON (`setDebug(true)`) | syslog OFF | Why |
| --- | --- | --- | --- |
| `android.util.Log.e` | Yes (proved) | No | ProjectLogger mirrors native log to logcat only when syslog is on |
| `LogWrapper -> ProjectLogger` | Yes (proved) | No | same syslog mirror; routed via ProjectLogger |
| File write (`filesDir`) | Yes (via `run-as cat`) | Yes | independent of ProjectLogger/logcat |
| Analytics DB (`ReportManager.onReport`) | Yes (sink/DB evidence) | Yes | independent of ProjectLogger/logcat |

Proved on a real debug build (HRY_AL00a, `setDebug(true)`): native
`android.util.Log.d/i` reached logcat, and a `LogWrapper.e("ViewPreLoadManager", …)`
line surfaced as `E/ViewPreLoadManager LaunchCommonModule(pid)` (the
` LaunchCommonModule` suffix is appended by the wrapper layer, so it is uniquely
attributable to `LogWrapper`, not native `Log`).

So on a debug build with syslog on, logcat IS usable. Treat "missing logcat tag"
as conclusive takeover only after confirming syslog is OFF; otherwise keep
debugging (wrong tag/filter, change not in APK, branch not hit). When in doubt,
the `filesDir` / analytics-sink channels (A3) are still the most robust because
they do not depend on the syslog switch at all.

## A4. Avoid paths

- Do not rely on incremental builds right after a KMP / shared-module change; the
  bytecode transform transform cache may keep stale output. Clean `app/build/bytecode transform` first.
- Do not classify "my code change has no effect" as a product/logic bug before
  ruling out the bytecode transform transform cache with a `--no-build-cache` full rebuild.
- Do not assume `method_call_opt` stripped your debug logs: it is gated by
  `enableInDebug false`, so standard debug builds keep `android.util.Log.*`.
- Do not conclude "device ROM blocks my logs" before checking the syslog/mirror
  switch and ruling out ordinary causes. Many apps install a logger/hook at
  startup (here `ProjectLogger.init()`; elsewhere Logan, Xlog/Mars, Timber, a custom `Log`
  shim, etc.), but on debug builds it usually mirrors to logcat
  (`the logger's debug mirror switch`), so `android.util.Log.*` and the wrapper DO appear.
  Treat "missing logcat tag" as takeover only after confirming syslog is OFF.
- Do not assume a wrapper that routes into the takeover logger (e.g.
  `LogWrapper -> ProjectLogger`) never reaches logcat: with syslog ON it is mirrored. With
  syslog OFF, prefer `filesDir` debug files, the analytics sink, or the logger's
  own files instead.
- Do not blame one named plugin (e.g. `securityAopPlugin`) for the dropped change
  without evidence; treat it as a bytecode transform transform-layer cache issue.

---

# Part B — Why (root cause explanation)

## B1. Why module/KMP changes get dropped from the APK

Symptom: you change code in a module (commonly a KMP / shared / `appinterval`
style module), rebuild incrementally, install, and the new behavior is simply
not there — no compile error, no obvious failure, the change just did not make
it into the APK.

Root cause: the bytecode transform transform chain (the bytecode-processing stage, including
plugins such as `securityAopPlugin`) keeps an aggressive incremental / transform
cache under `app/build/bytecode transform`. When the cache does not correctly invalidate for
an upstream module's changed artifact, the transform reuses the old processed
output and the new code never enters the final APK.

Do not attribute this to a single plugin by name without evidence. The cache
sits at the bytecode transform transform layer; a specific plugin (e.g. `securityAopPlugin`)
is just one stage in that chain. Classify it as a **build-cache / transform
staleness blocker**, not a product-code failure. Fix it with A2.

## B2. Why `android.util.Log` is missing — log-channel takeover vs. strip vs. cache vs. wrapper

When runtime logs do not show up, there are several different causes. Do not
jump straight to "bytecode transform deleted my logs" or "the device ROM blocks logs". The
single most important class to be aware of is a **logging framework / hook that
globally takes over the native log channel at startup**:

- **General pattern (be aware of this on any app): a startup-initialized logger
  or hook redirects `android.util.Log` away from logcat.** Many production apps
  install their own logging pipeline that captures the native log output (for
  buffering, encryption, file persistence, sampling, or perf). After it is
  initialized, even raw `android.util.Log.e(tag, msg)` may never reach logcat —
  it is rerouted into the framework's own files/sink. Concrete examples vary by
  project: a project-specific logging backend, Logan, Tencent Xlog/Mars, Timber trees,
  a custom `Log` shim, or `System.out`/`System.err` redirection. So before
  blaming code or the device, ask: *does this app install a logger/hook at
  startup that could own the log channel?*
- **CONFIRMED instance in this project, and its real switch: `ProjectLogger` owns the
  native log channel, gated by a syslog/mirror flag.** `ProjectLogger.init(config)` routes
  `android.util.Log` output through ProjectLogger. Whether it ALSO reaches logcat is
  controlled by ProjectLogger's syslog switch: `the logger's debug mirror switch` ->
  `Log.setSyslog(true)` + `Alog.setSyslog(true)` -> native
  `nativeSetSyslog(ref, true)`. Bytecode-verified call chain. Proven on a real
  debug build/device (HRY_AL00a):
  - **syslog ON** (`the logger's debug mirror switch`, the normal debug default): native
    `android.util.Log.d/i` reached logcat, and `LogWrapper.e("ViewPreLoadManager", …)`
    surfaced as `E/ViewPreLoadManager LaunchCommonModule(pid)` — both proved
    visible.
  - **syslog OFF**: a `android.util.Log.e("FUA_LOG_E_TEST", …)` line did NOT reach
    logcat while ProjectLogger was initialized; only after disabling ProjectLogger init did it
    appear. The earlier "comment out `ProjectLogger.init` -> the line appears" diagnosis is
    reconciled by this: the controlling variable is the syslog mirror, not the
    mere existence of `ProjectLogger.init`.

  Takeaway: on a debug build with syslog on, logcat IS a valid channel for both
  native `Log` and `LogWrapper -> ProjectLogger`. A missing logcat tag is only explained by
  log-channel takeover when syslog is OFF — otherwise debug the ordinary causes
  first.
- **`method_call_opt` strips logs in release-style builds.** In `bytecode-transform.gradle`
  the `method_call_opt` block lists `android/util/Log#v/d/i/w/e/println` (plus
  `Logger.*` / `LogWrapper.*` variants) for removal to reduce size and improve
  security. **But it is gated by `enableInDebug false`**, so a standard debug
  build does NOT strip `android.util.Log.*`. If you are on a debug build, missing
  logs are usually NOT caused by `method_call_opt`.
- **Stale bytecode transform cache (separate issue, see B1).** If your logging code is in a
  module/KMP change that never entered the APK, the log lines cannot run. Verify
  the change is in the APK first.
- **A logging wrapper routes through the takeover logger too.** A project wrapper
  (here `LogWrapper.e/i/...`) that only calls the framework (`ProjectLogger.*`) follows the
  same syslog rule as native `Log`: mirrored to logcat when syslog is ON, written
  only to the framework's files when syslog is OFF.

Triage order on a debug build with missing `android.util.Log`:

1. Check the syslog/mirror switch first. If the app initializes a logger/hook
   (here ProjectLogger / `ProjectLogger.init()`) AND syslog is ON (debug default
   `the logger's debug mirror switch`), logcat should work — so a missing tag points to an
   ordinary cause (wrong tag/filter, change not in APK, branch not hit), not
   takeover. Only if syslog is OFF is logcat absence EXPECTED takeover; confirm
   with a controlled on/off experiment on the initializer (temporary local
   diagnosis only, never a shipped change) if you must.
2. Confirm the change is actually in the APK (clean bytecode transform cache + full rebuild).
3. Switch verification to a channel the logger does NOT intercept (see A3).
4. Only then consider `method_call_opt` — and only if `enableInDebug` is true for
   the build variant you are running.

## B3. Evidence to record and how to classify

Record in `Validation.md`, `evaluation.json.evidence[]`, and `summary.md`:

```text
- exact gradle command and cwd (e.g. ./gradlew :app:assembleDebug --no-build-cache)
- whether app/build/bytecode transform (and which module build dirs) were removed
- build log path and the install/runtime evidence proving the change is in the APK
- which logging API was used and the relevant bytecode transform config (enableInDebug)
- if `android.util.Log` is used: APK bytecode proof, app foreground/activity proof,
  logcat smoke proof (`adb shell log -p`), app-pid logcat proof, and whether the
  expected tag was still absent
```

Classify precisely:

- change missing after incremental build, present after bytecode transform clean + full
  rebuild -> build-cache/transform staleness blocker (not product-code failure);
- `android.util.Log` tag absent on a project that initializes a logging
  framework/hook (here `ProjectLogger.init()`) AND has syslog/mirror OFF -> EXPECTED
  log-channel takeover, not a product failure and not a device-ROM issue; use
  file / report-sink / logger-file evidence instead. With syslog ON
  (`the logger's debug mirror switch`, debug default) logcat works, so a missing tag is an
  ordinary bug to keep debugging, not takeover;
- logs stripped because `enableInDebug true` on the running variant -> expected
  bytecode transform behavior, switch variant or use a wrapper that survives;
- logs routed through `LogWrapper`/`ProjectLogger` -> mirrored to logcat when syslog is ON,
  written only to ProjectLogger files when syslog is OFF.

Last updated: 2026-06-17
