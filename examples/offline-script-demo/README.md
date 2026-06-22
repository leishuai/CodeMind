# Offline Script Demo

This is the easiest AutoMind example because it requires no Android or iOS device.

This folder is intentionally small: it does not store a pre-generated task. The
smoke command below creates fresh runtime artifacts in `.automind/tasks/` so the
example stays public-safe and does not ship local evidence logs.

Run it from the AutoMind checkout:

```bash
./automind.sh smoke offline-demo
```

The smoke writes a fresh local task under:

```text
.automind/tasks/offline_demo_smoke/
```

That task demonstrates the basic AutoMind loop:

```text
script command -> command/log evidence -> evaluation.json -> summary -> record-check
```

Useful follow-up commands:

```bash
./automind.sh status offline_demo_smoke
./automind.sh summary offline_demo_smoke
./automind.sh record-check offline_demo_smoke
```

Local `.automind/tasks/` folders are runtime evidence and are intentionally not stored in this example folder.
