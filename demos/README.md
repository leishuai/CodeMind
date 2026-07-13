# CodeAutonomy Demos

`demos/` contains runnable platform demo projects and smoke fixtures for the full
CodeAutonomy checkout. These demos are for CodeAutonomy maintainers and users who want to
verify platform-specific capabilities locally.

For the public-safe, no-device starter examples, use [`../examples/`](../examples/)
instead.

## What belongs here

- Minimal Android projects/APK builders for probe-flow, self-repair, and agent
  repair experiments.
- Minimal iOS simulator or external XCUITest runner projects.
- Small fixtures used by CodeAutonomy platform smoke tests.

## What does not belong here

- Private/customer/business app code.
- Real device identifiers, signing teams, certificates, provisioning profiles,
  or local machine paths.
- Large build outputs, `.xcresult` bundles, APK/IPA artifacts, or raw task logs.
- New-user documentation that should live in `README.md`, `docs/`, or
  `examples/`.

## Current demo groups

### Android demos

```text
android-minimal-demo/
android-self-repair-demo/
android-self-repair-smoke/
android-probe-flow-self-repair-smoke/
android-agent-repair-demo/
```

Purpose:

- exercise APK build/install/launch paths;
- validate Android probe-flow shape and self-repair behavior;
- provide a small target for Android adapter development.

Typical requirements:

- Android SDK / platform tools when actually building or running;
- a simulator/emulator or physical device for dynamic execution;
- project-local CodeAutonomy helper venv only when the selected verifier needs it.

### iOS demos

```text
ios-simulator-demo/
ios-external-ui-runner/
```

Purpose:

- verify simulator build/install/launch/screenshot paths;
- validate XCUITest/probe-flow/action-plan execution strategy;
- provide a small external UI runner fixture.

Typical requirements:

- Xcode command line tools;
- an available simulator, or a configured physical-device environment for
  real-device experiments;
- signing/team/device trust only when the selected path requires it.

## Demo artifacts

Some demo folders may contain tiny checked-in sample artifacts such as a screenshot
or short log to explain expected output. They are not task runtime evidence and
should not contain private data. Fresh runtime evidence should be generated under
`.automind/tasks/<task-code>/` when a demo smoke runs.

## Export boundary

The default CodeAutonomy skill export intentionally omits `demos/`. Skill users get
lightweight examples from `examples/`; full runtime users can inspect or run
`demos/` from the repository checkout when they have the required platform tools.
