"""Regression: ios_project_probe parse_schemes/parse_targets must not be
poisoned by xcodebuild stderr noise (e.g. DVTProvisioningProfileManager
profile load failures) when stdout/stderr are concatenated, and the caller
must only feed stdout to the parser.

This pins the root cause observed in
.automind/tasks/app_0601003729/logs/iter-1/ios-project-probe.json where
25+ stderr log lines were captured as scheme names.
"""
from __future__ import annotations

from scripts.ios_project_probe import parse_schemes, parse_targets


XCODEBUILD_STDOUT = """Command line invocation:
    /Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild -list -project ExampleAppXcode.xcodeproj

Information about project "ExampleAppXcode":
    Targets:
        BazelDependencies
        ExampleApp
        TTReadingNotificationExtension
        TTReadingWidgetExtension

    Build Configurations:
        DailyBulid
        Debug
        Distribution
        Release

    If no build configuration is specified and -scheme is not passed then "Debug" is used.

    Schemes:
        TTReadingNotificationExtension
        TTReadingWidgetExtension
        ExampleApp

"""


XCODEBUILD_STDERR = (
    '2026-06-01 05:34:24.006 xcodebuild[32781:41851253]  DVTProvisioningProfileManager: '
    'Failed to load profile "/Users/x/Library/MobileDevice/Provisioning Profiles/abc.mobileprovision" '
    '(Error Domain=DVTProvisioningProfileProviderErrorDomain Code=1 "Failed to load profile." '
    'UserInfo={NSLocalizedDescription=Failed to load profile.})\n'
    '2026-06-01 05:34:24.006 xcodebuild[32781:41851254]  DVTProvisioningProfileManager: '
    'Failed to load profile "/Users/x/Library/MobileDevice/Provisioning Profiles/def.mobileprovision" '
    '(Error Domain=DVTProvisioningProfileProviderErrorDomain Code=1 "Failed to load profile.")\n'
)


def test_parse_schemes_clean_stdout():
    assert parse_schemes(XCODEBUILD_STDOUT) == [
        "TTReadingNotificationExtension",
        "TTReadingWidgetExtension",
        "ExampleApp",
    ]


def test_parse_targets_clean_stdout():
    assert parse_targets(XCODEBUILD_STDOUT) == [
        "BazelDependencies",
        "ExampleApp",
        "TTReadingNotificationExtension",
        "TTReadingWidgetExtension",
    ]


def test_parse_schemes_rejects_stderr_noise_when_concatenated():
    # Even if a future caller mistakenly concatenates stderr after stdout,
    # parser must not absorb DVTProvisioningProfileManager log lines.
    poisoned = XCODEBUILD_STDOUT + XCODEBUILD_STDERR
    schemes = parse_schemes(poisoned)
    assert schemes == [
        "TTReadingNotificationExtension",
        "TTReadingWidgetExtension",
        "ExampleApp",
    ]
    for line in schemes:
        assert "DVTProvisioningProfileManager" not in line
        assert "Failed to load profile" not in line
        assert not line.startswith("2026-")


def test_parse_targets_rejects_stderr_noise_when_concatenated():
    poisoned = XCODEBUILD_STDOUT + XCODEBUILD_STDERR
    targets = parse_targets(poisoned)
    assert targets == [
        "BazelDependencies",
        "ExampleApp",
        "TTReadingNotificationExtension",
        "TTReadingWidgetExtension",
    ]


def test_parse_schemes_handles_empty_or_missing_section():
    assert parse_schemes("") == []
    assert parse_schemes("Information about project: foo\n") == []


def test_parse_schemes_stops_at_followup_noise_without_blank_line():
    # If the stream is malformed and the first line after "Schemes:" is
    # already noise (no blank separator), parser must not capture it.
    text = (
        "    Schemes:\n"
        '2026-06-01 05:34:24.006 xcodebuild[32781:41851253]  DVTProvisioningProfileManager: '
        'Failed to load profile "x"\n'
    )
    assert parse_schemes(text) == []
