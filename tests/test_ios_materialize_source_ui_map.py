from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_SPEC = importlib.util.spec_from_file_location(
    "ios_probe_flow_materialize",
    _SCRIPTS / "ios_probe_flow_materialize.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


HIERARCHY_DUMP = """Application, 0x600000, {{0, 0}, {390, 844}}, label: 'ExampleApp'
  Window (Main), 0x600001, {{0, 0}, {390, 844}}
    Button, 0x600002, {{16, 780}, {44, 44}}, label: '暂停', identifier: 'player_pause'
    StaticText, 0x600003, {{70, 100}, {200, 30}}, label: '听书'
    Button, 0x600004, {{300, 780}, {44, 44}}, label: '下一首'
    Other, 0x600005, {{0, 0}, {390, 100}}
"""


def test_parse_ui_hierarchy_extracts_controls() -> None:
    controls = mod.parse_ui_hierarchy(HIERARCHY_DUMP)
    labels = {(c["type"], c["label"], c["identifier"]) for c in controls}
    assert ("Button", "暂停", "player_pause") in labels
    assert ("StaticText", "听书", "") in labels
    assert ("Button", "下一首", "") in labels


def test_derived_selector_candidates_prefers_identifier() -> None:
    ui_map = {"controls": mod.parse_ui_hierarchy(HIERARCHY_DUMP)}
    candidates = mod.derived_selector_candidates(ui_map)
    assert "identifier == 'player_pause'" in candidates
    assert "label == '听书'" in candidates


def test_collect_source_ui_map_reads_prior_iteration(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    prior = task_dir / "logs" / "iter-1"
    prior.mkdir(parents=True)
    (prior / "app-ui-hierarchy.txt").write_text(HIERARCHY_DUMP)
    ui_map = mod.collect_source_ui_map(task_dir, iteration=2)
    assert ui_map.get("controlCount", 0) >= 3
    # interactive Button controls rank before StaticText
    assert ui_map["controls"][0]["type"] == "Button"
    assert ui_map["sourceFiles"]
