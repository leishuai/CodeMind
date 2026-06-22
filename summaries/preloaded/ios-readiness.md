---
name: ios-readiness
description: "iOS screenshot/OCR readiness analyzer playbook for classifying common blockers such as privacy consent, permissions, login state, and loading screens without clicking."
use_when:
  - "a screenshot suggests the app is not ready for the target flow"
  - "privacy/permission/login/loading blockers may be present"
  - "deciding whether to ask the user or continue UI automation"
solves:
  - "classifies readiness blockers from screenshot OCR"
  - "prevents silent consent/permission clicking"
  - "turns unclear visual state into structured evaluation categories"
---
# iOS Readiness Analyzer Summary

`ios-readiness-analyze` classifies common readiness blockers from screenshot OCR. It does not click anything.

## Command

```bash
./automind.sh ios-readiness-analyze <task-code> \
  --image <screenshot.png> \
  --bundle-id <bundle-id>
```

## Current classifiers

- `privacy_consent_blocked`
- `permission_blocked`
- `login_state_blocked`
- `loading_state`
- `no_common_blocker_detected`
- `ocr_no_text`

## Policy

Privacy/terms consent is a user/test-policy decision. Do not tap agree automatically without explicit test intent or user confirmation.

## Example result shape

A blocker result should record the matched OCR phrases, category such as `privacy_consent_blocked`, and an `askUserQuestion` instead of clicking automatically.

Last updated: 2026-05-07

## OCR classifier precision note

Avoid single generic Chinese words as permission blockers when they can appear in normal content. Example: a generic “locate/location” word inside normal prose is not a location permission prompt.

Prefer context-bearing permission phrases such as explicit allow/deny phrasing, location-permission wording, `Don’t Allow`, `Would Like`, or system prompt structure.
