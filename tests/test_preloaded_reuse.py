from pathlib import Path

from orchestrator.reuse import (
    build_preloaded_context,
    preloaded_prefixes_for_task_type,
    preloaded_summary_files_for_task_type,
)


def test_preloaded_prefixes_by_task_type():
    assert preloaded_prefixes_for_task_type("script") == ["common-"]
    assert preloaded_prefixes_for_task_type("ios") == ["common-", "client-", "ios-"]
    assert preloaded_prefixes_for_task_type("android") == ["common-", "client-", "android-"]
    assert preloaded_prefixes_for_task_type("dual") == ["common-", "client-", "ios-", "android-"]


def test_ios_preloaded_uses_prefixes_and_skips_unmatched():
    files = preloaded_summary_files_for_task_type("ios")
    names = [p.stem for p in files]
    assert "common-build-verification-playbook" in names
    assert "client-ui-repair" in names
    assert any(name.startswith("ios-") for name in names)
    assert all(name.startswith(("common-", "client-", "ios-")) for name in names)
    assert not any(name.startswith("android-") for name in names)


def test_preloaded_context_is_progressive_index_not_full_pack():
    context = build_preloaded_context("ios", limit_chars=4000)
    assert "Preloaded seed" not in context  # caller adds the section title
    assert "Path: `summaries/preloaded/" in context
    assert "read this file on demand" in context
    assert "common-build-verification-playbook" in context
    assert "client-ui-repair" in context
    # Full pack body should not be copied wholesale into Reuse.md.
    assert "A reusable successful path should include:" not in context


def test_every_preloaded_pack_has_frontmatter_metadata():
    root = Path("summaries/preloaded")
    for summary in sorted(p for p in root.glob("*.md") if p.name != "README.md"):
        text = summary.read_text()
        assert text.startswith("---\n"), f"{summary} missing frontmatter"
        head = text.split("\n---\n", 1)[0]
        assert "description:" in head, f"{summary} missing description"
        assert "use_when:" in head, f"{summary} missing use_when"
        assert "solves:" in head, f"{summary} missing solves"


def test_preloaded_context_uses_frontmatter_description_as_one_line_summary():
    context = build_preloaded_context("ios", limit_chars=4000)
    assert "- Summary:" in context
    assert "- Load: read this file on demand" in context
    assert "- Use when:" not in context
    assert "- Solves:" not in context
    assert "Generic build, test, and verification reuse playbook" in context


def test_preloaded_check_cli_passes(capsys):
    from orchestrator.main import cmd_preloaded_check

    cmd_preloaded_check()
    out = capsys.readouterr().out
    assert '"result": "pass"' in out
    assert '"ios"' in out
    assert 'common-build-verification-playbook' in out
