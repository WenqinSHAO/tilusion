from __future__ import annotations

import pytest

from tilusion.extraction_prompts import generated_prompt_part
from tilusion.reading_payloads import (
    _source_block_meta,
    build_per_segment_extraction_payload,
    build_unit_reading_finalization_payload,
    flatten_segment_results,
    render_text_with_block_markers,
)
from tilusion.reading_schema import SourceBlock
from tilusion.reading_prompts import (
    build_per_segment_extraction_composition,
    build_unit_reading_finalization_composition,
)


def test_per_segment_extraction_composition_loads_static_contract() -> None:
    generated = generated_prompt_part(
        "context-pack",
        role="document_context",
        content='{"guidance": "prior context is not evidence"}',
        generated_by="test",
    )

    composition = build_per_segment_extraction_composition([generated])

    assert composition.composition_id == "per-segment-extraction-v0.2"
    assert composition.parts[0].part_id == "per-segment-extraction-contract"
    assert composition.parts[-1].part_id == "context-pack"
    assert "Return only one JSON object" in composition.content
    assert "inline block boundary markers" in composition.content
    assert "atomic_items" in composition.content
    assert "Do not invent block IDs" in composition.content
    assert composition.to_dict()["parts"][0]["metadata"]["schema_version"] == "reading-unit-v0.3"


def test_unit_finalization_composition_has_stable_id() -> None:
    assert build_unit_reading_finalization_composition().composition_id == "unit-reading-finalization-v0.1"


def test_per_segment_extraction_payload_includes_source_blocks_and_marked_text() -> None:
    blocks = [
        SourceBlock(
            block_id="seg-0001-block-0000",
            unit_id="unit-0001",
            segment_id="seg-0001",
            block_index=0,
            block_type="paragraph",
            start=0,
            end=22,
            text="Alice defines entropy.",
            text_hash="abc",
        )
    ]
    payload = build_per_segment_extraction_payload(
        unit_id="unit-0001",
        segment={"segment_id": "seg-0001"},
        text="Alice defines entropy.",
        source_blocks=blocks,
        segment_offset=0,
        context={"context_pack_id": "context-pack-1"},
    )

    assert payload["task"] == "per_segment_extraction"
    assert payload["schema_version"] == "reading-unit-v0.3"
    assert payload["context"]["context_pack_id"] == "context-pack-1"
    assert payload["text"] == "{seg-0001-block-0000:paragraph}Alice defines entropy.{/seg-0001-block-0000}"
    assert payload["source_blocks"] == [
        {"block_id": "seg-0001-block-0000", "block_type": "paragraph", "start": 0, "end": 22}
    ]


def test_render_text_with_block_markers_multiple_blocks() -> None:
    text = "First paragraph.\n\nShort line."
    blocks = [
        SourceBlock(
            block_id="seg-0001-block-0000",
            unit_id="unit-0001",
            segment_id="seg-0001",
            block_index=0,
            block_type="paragraph",
            start=10,
            end=28,
            text="First paragraph.\n\n",
            text_hash="abc",
        ),
        SourceBlock(
            block_id="seg-0001-block-0001",
            unit_id="unit-0001",
            segment_id="seg-0001",
            block_index=1,
            block_type="line",
            start=28,
            end=39,
            text="Short line.",
            text_hash="def",
        ),
    ]
    marked = render_text_with_block_markers(text, blocks, segment_offset=10)

    expected = (
        "{seg-0001-block-0000:paragraph}"
        "First paragraph.\n\n"
        "{/seg-0001-block-0000}"
        "{seg-0001-block-0001:line}"
        "Short line."
        "{/seg-0001-block-0001}"
    )
    assert marked == expected


def test_render_text_with_block_markers_guards_overlap() -> None:
    import pytest

    blocks = [
        SourceBlock(
            block_id="seg-0001-block-0000",
            unit_id="unit-0001",
            segment_id="seg-0001",
            block_index=0,
            block_type="paragraph",
            start=0,
            end=5,
            text="Hello",
            text_hash="abc",
        ),
        SourceBlock(
            block_id="seg-0001-block-0001",
            unit_id="unit-0001",
            segment_id="seg-0001",
            block_index=1,
            block_type="paragraph",
            start=3,  # overlaps previous
            end=10,
            text="lo world",
            text_hash="def",
        ),
    ]
    with pytest.raises(ValueError, match="overlaps"):
        render_text_with_block_markers("Hello world", blocks, segment_offset=0)


def test_render_text_with_block_markers_guards_out_of_bounds() -> None:
    import pytest

    blocks = [
        SourceBlock(
            block_id="seg-0001-block-0000",
            unit_id="unit-0001",
            segment_id="seg-0001",
            block_index=0,
            block_type="paragraph",
            start=0,
            end=5,
            text="Hello",
            text_hash="abc",
        ),
        SourceBlock(
            block_id="seg-0001-block-0001",
            unit_id="unit-0001",
            segment_id="seg-0001",
            block_index=1,
            block_type="paragraph",
            start=5,
            end=100,  # exceeds segment length
            text=" world...",
            text_hash="def",
        ),
    ]
    with pytest.raises(ValueError, match="exceeds segment bounds"):
        render_text_with_block_markers("Hello world", blocks, segment_offset=0)


def test_source_block_meta_drops_text_and_hash() -> None:
    block = SourceBlock(
        block_id="seg-0001-block-0000",
        unit_id="unit-0001",
        segment_id="seg-0001",
        block_index=0,
        block_type="paragraph",
        start=0,
        end=5,
        text="Hello",
        text_hash="abc123",
    )
    meta = _source_block_meta(block)

    assert meta == {"block_id": "seg-0001-block-0000", "block_type": "paragraph", "start": 0, "end": 5}
    assert "text" not in meta
    assert "text_hash" not in meta


def test_finalization_payload_declares_forbidden_legacy_core_fields() -> None:
    payload = build_unit_reading_finalization_payload(
        unit_id="unit-0001",
        source={"book_path": "book.txt"},
        segments=[],
        source_spans=[],
        source_blocks=[],
        concept_mentions=[],
        logical_groups=[],
        links=[],
    )

    assert payload["task"] == "unit_reading_finalization"
    assert "timelines" in payload["expected_output"]["forbidden_core_fields"]
    assert "logical_groups" in payload["expected_output"]["core_fields"]


def test_flatten_segment_results_scopes_ids() -> None:
    flat = flatten_segment_results(
        [
            {
                "segment_id": "seg-0001",
                "concepts": [{"concept_id": "concept-0001", "surface": "a"}],
                "atomic_items": [
                    {"item_id": "item-0001", "concept_refs": ["concept-0001"]}
                ],
            },
            {
                "segment_id": "seg-0002",
                "concepts": [{"concept_id": "concept-0001", "surface": "b"}],
                "atomic_items": [
                    {"item_id": "item-0001", "concept_refs": ["concept-0001"]}
                ],
            },
        ]
    )

    # Both segments had local concept-0001 — now scoped to unit-unique IDs
    assert flat["concepts"][0]["concept_id"] == "seg-0001-concept-0001"
    assert flat["concepts"][1]["concept_id"] == "seg-0002-concept-0001"
    assert flat["atomic_items"][0]["item_id"] == "seg-0001-item-0001"
    assert flat["atomic_items"][1]["item_id"] == "seg-0002-item-0001"

    # concept_refs rewritten to match scoped concept IDs
    assert flat["atomic_items"][0]["concept_refs"] == ["seg-0001-concept-0001"]
    assert flat["atomic_items"][1]["concept_refs"] == ["seg-0002-concept-0001"]
