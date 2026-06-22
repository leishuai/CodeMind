import sys
from pathlib import Path


def test_android_apk_probe_has_bounded_default_timeout(monkeypatch) -> None:
    sys.path.insert(0, str(Path.cwd() / "scripts"))
    from scripts import android_apk_probe

    monkeypatch.delenv("AUTOMIND_ANDROID_APK_PROBE_TIMEOUT", raising=False)
    monkeypatch.delenv("AUTOMIND_CMD_TIMEOUT", raising=False)

    assert android_apk_probe.runtime_timeout("AUTOMIND_ANDROID_APK_PROBE_TIMEOUT") == 300


def test_android_preflight_orchestrator_uses_bounded_default_timeout() -> None:
    source = open("orchestrator/main.py").read()
    assert 'runtime_timeout("AUTOMIND_PREFLIGHT_TIMEOUT", 300)' in source
