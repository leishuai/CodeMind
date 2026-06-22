from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")

sys.path.insert(0, str(Path("scripts").resolve()))

from PIL import Image  # noqa: E402

import visual_inspector  # noqa: E402


def _make_image(path: Path, size: tuple[int, int]) -> None:
    Image.new("RGB", size, (120, 130, 140)).save(path)


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["visual_inspector.py", *argv])
    return visual_inspector.main()


def test_baseline_resized_by_default_when_size_differs(monkeypatch, tmp_path, capsys):
    shot = tmp_path / "shot.png"
    baseline = tmp_path / "baseline.png"
    out = tmp_path / "report.json"
    _make_image(shot, (100, 200))
    _make_image(baseline, (200, 400))  # design mockup at higher resolution

    code = _run(
        monkeypatch,
        ["VI-TEST", "--image", str(shot), "--baseline", str(baseline), "--output", str(out)],
    )
    capsys.readouterr()

    assert code == 0
    report = json.loads(out.read_text())
    assert report["result"] == "pass"
    size_check = next(c for c in report["checks"] if c["name"] == "baseline_size_match")
    assert size_check["result"] == "pass"
    assert report["comparison"]["normalizedBaselineFrom"] == [200, 400]


def test_strict_size_fails_when_size_differs(monkeypatch, tmp_path, capsys):
    shot = tmp_path / "shot.png"
    baseline = tmp_path / "baseline.png"
    out = tmp_path / "report.json"
    _make_image(shot, (100, 200))
    _make_image(baseline, (200, 400))

    code = _run(
        monkeypatch,
        [
            "VI-TEST",
            "--image",
            str(shot),
            "--baseline",
            str(baseline),
            "--strict-size",
            "--output",
            str(out),
        ],
    )
    capsys.readouterr()

    assert code == 1
    report = json.loads(out.read_text())
    assert report["result"] == "fail"
    assert report["nextAction"] == "retry_generator"
    assert report["comparison"] is None
    size_check = next(c for c in report["checks"] if c["name"] == "baseline_size_match")
    assert size_check["result"] == "fail"


def test_matching_size_compares_without_normalization(monkeypatch, tmp_path, capsys):
    shot = tmp_path / "shot.png"
    baseline = tmp_path / "baseline.png"
    out = tmp_path / "report.json"
    _make_image(shot, (120, 240))
    _make_image(baseline, (120, 240))

    code = _run(
        monkeypatch,
        ["VI-TEST", "--image", str(shot), "--baseline", str(baseline), "--output", str(out)],
    )
    capsys.readouterr()

    assert code == 0
    report = json.loads(out.read_text())
    assert report["result"] == "pass"
    assert report["comparison"]["normalizedBaselineFrom"] is None
