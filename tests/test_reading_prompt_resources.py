from __future__ import annotations

from importlib import resources


READING_PROMPT_RESOURCES = [
    "prompt_per_segment_extraction_v0.2.md",
    "prompt_unit_reading_finalization_v0.1.md",
    "prompt_unit_logical_grouping_v0.1.md",
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


def test_per_segment_extraction_prompt_uses_deterministic_source_blocks() -> None:
    content = resources.files("tilusion.prompts").joinpath(
        "prompt_per_segment_extraction_v0.2.md"
    ).read_text(encoding="utf-8")

    assert "deterministic source blocks" in content
    assert "Do not invent block IDs" in content
    assert "inline block boundary markers" in content
    assert "concepts" in content
    assert "atomic_items" in content
    assert "logical groups" in content
    assert "schema-light" in content
