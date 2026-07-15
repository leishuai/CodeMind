# Phase 1: Environment Initialization

## When to run

Run environment initialization once when CodeMind is first used in a project, or whenever the local platform/tooling state may have changed.

---

## Steps

### 1. Check operating system

CodeMind currently targets macOS (Darwin).

```bash
uname -s
# Expected output: Darwin
```

### 2. Check required tools

Check tools based on the task type.

#### General tools

| Tool | Command | Purpose |
|------|------|------|
| git | `git --version` | Version control |
| python3 | `python3 --version` | Script execution |

#### iOS development

| Tool | Command | Purpose |
|------|------|------|
| xcodebuild | `xcodebuild -version` | iOS builds |
| xcrun | `xcrun --version` | iOS simulator/device tooling |

#### Android development

| Tool | Command | Purpose |
|------|------|------|
| gradle | `gradle --version` | Android builds |
| adb | `adb version` | Android devices |

#### Installation hints

```bash
# Xcode command line tools
xcode-select --install

# Android Studio includes adb and Gradle support
# Download: https://developer.android.com/studio
```

### 3. Create runtime directories

```bash
mkdir -p .automind/tasks
mkdir -p .automind/summary
```

### 4. Create `.automind/summary/lessons-learned.md`

```markdown
# Lessons Learned - General

<!-- Append reusable lessons by date. -->
```

---

## Quick checklist

- [ ] macOS system
- [ ] `git` available
- [ ] `python3` available
- [ ] Build tools required by the task are available

---

## Next step

After initialization, wait for the user request and continue to [Phase 2: Requirements](phase2-requirement.md).
