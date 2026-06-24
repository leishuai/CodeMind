import json
import sys
from pathlib import Path


def test_ios_readiness_privacy_consent_auto_unblocks(monkeypatch, tmp_path: Path) -> None:
    sys.path.insert(0, str(Path.cwd() / "scripts"))
    from scripts import ios_readiness_analyzer as analyzer

    image = tmp_path / "screen.png"
    image.write_bytes(b"not-a-real-png")
    task_dir = tmp_path / ".automind" / "tasks" / "ios_privacy"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(analyzer, "TASKS", tmp_path / ".automind" / "tasks")
    monkeypatch.setattr(analyzer, "run_ocr", lambda image_path, log_dir: ("个人信息保护 同意 不同意", {"engine": "fake"}))
    monkeypatch.setattr(analyzer, "classify", lambda text: ("blocked", "privacy_consent_blocked", ["个人信息保护", "同意"]))

    monkeypatch.setattr(sys, "argv", [
        "ios_readiness_analyzer.py",
        "ios_privacy",
        "--image",
        str(image),
        "--bundle-id",
        "com.example.app",
    ])
    rc = analyzer.main()

    assert rc == 2
    evaluation = json.loads((task_dir / "evaluation.json").read_text())
    assert evaluation["nextAction"] == "retry_generator"
    assert "askUserQuestion" not in evaluation
    assert evaluation["autoUnblock"]["allowed"] is True
    assert evaluation["autoUnblock"]["category"] == "positive_privacy_or_terms_consent"


def test_evaluator_prompt_documents_privacy_consent_auto_unblock() -> None:
    prompt = Path("templates/evaluator_prompt.md").read_text()
    assert "app-internal privacy/terms Agree/Allow/Continue and OS/app permission Allow" in prompt
    assert "auto-unblock" in prompt
    assert "reject/deny" in prompt
