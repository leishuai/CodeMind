from pathlib import Path


def test_evaluator_prompt_requires_progress_and_exclusion_fields() -> None:
    text = Path("templates/evaluator_prompt.md").read_text()
    assert "progressKind" in text
    assert "hypothesis" in text
    assert "ruledOut" in text
    assert "remainingHypotheses" in text
    assert "Do not add artificial attempt budgets" in text


def test_test_design_guide_documents_exclusion_progress_fields() -> None:
    text = Path("docs/references/test-design-guide.md").read_text()
    assert "use exclusion" in text.lower()
    assert "progressKind" in text
    assert "remainingHypotheses" in text
    assert "partial" in text
