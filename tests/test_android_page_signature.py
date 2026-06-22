from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_runner():
    path = Path("scripts/android_probe_flow_runner.py")
    spec = importlib.util.spec_from_file_location("android_probe_flow_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_android_page_signature_required_anyof_forbidden() -> None:
    runner = _load_runner()
    xml = '''<hierarchy>
      <node package="com.example.app" class="android.widget.TextView" resource-id="com.example.app:id/title" text="Home" content-desc="" bounds="[0,0][100,50]" />
      <node package="com.example.app" class="android.widget.EditText" resource-id="com.example.app:id/search_bar" text="" content-desc="Search" bounds="[0,50][300,100]" />
      <node package="com.example.app" class="android.widget.TextView" resource-id="com.example.app:id/tab_music" text="Media" content-desc="" bounds="[0,100][100,150]" />
    </hierarchy>'''
    result = runner.evaluate_page_signature(xml, {
        "name": "home",
        "required": [{"resource_id": "com.example.app:id/search_bar"}],
        "anyOf": [{"text": "Home"}, {"text": "听书"}, {"text": "Media"}],
        "minAnyOf": 2,
        "forbidden": [{"text": "Privacy notice"}],
    }, "com.example.app")
    assert result["ok"] is True
    assert result["requiredOk"] is True
    assert result["anyOfMatched"] == 2
    assert result["forbiddenOk"] is True


def test_android_page_signature_fails_for_forbidden_dialog() -> None:
    runner = _load_runner()
    xml = '''<hierarchy>
      <node package="com.example.app" class="android.widget.TextView" resource-id="com.example.app:id/search_bar" text="" content-desc="Search" bounds="[0,0][100,50]" />
      <node package="com.example.app" class="android.widget.TextView" resource-id="com.example.app:id/dialog" text="Privacy notice" content-desc="" bounds="[0,50][100,100]" />
    </hierarchy>'''
    result = runner.evaluate_page_signature(xml, {
        "name": "home",
        "required": [{"resource_id": "com.example.app:id/search_bar"}],
        "forbidden": [{"text": "Privacy notice"}],
    }, "com.example.app")
    assert result["ok"] is False
    assert result["forbiddenOk"] is False
