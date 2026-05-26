from __future__ import annotations

from importlib import resources


READING_PROMPT_RESOURCES = [
    "prompt_per_segment_extraction_v0.1.md",
    "prompt_unit_reading_finalization_v0.1.md",
]


def test_reading_prompt_resources_are_packaged() -> None:
    for resource_name in READING_PROMPT_RESOURCES:
        content = resources.files("tilusion.prompts").joinpath(resource_name).read_text(encoding="utf-8")
        assert "Return only one JSON object" in content
        assert "source" in content.lower()


def test_reading_prompts_keep_timeline_as_non_core_view() -> None:
    finalization = resources.files("tilusion.prompts").joinpath(
        "prompt_unit_reading_finalization_v0.1.md"
    ).read_text(encoding="utf-8")

    assert "Do not create `entity_records`" in finalization
    assert "top-level `timelines`" in finalization
    assert "derived_views" in finalization


def test_per_segment_extraction_prompt_distinguishes_source_grounded_and_synthesis() -> None:
    content = resources.files("tilusion.prompts").joinpath(
        "prompt_per_segment_extraction_v0.1.md"
    ).read_text(encoding="utf-8")

    assert "source_grounded" in content
    assert "synthesis" in content
    assert "do not pretend synthesis is direct evidence" in content.lower()
