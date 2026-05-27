from __future__ import annotations

import pytest

from tilusion.extraction_prompts import generated_prompt_part
from tilusion.reading_payloads import (
    _source_block_meta,
    build_per_segment_extraction_payload,
    flatten_and_stabilize_segment_results,
    render_text_with_block_markers,
)
from tilusion.reading_schema import SourceBlock
from tilusion.reading_prompts import (
    build_per_segment_extraction_composition,
    build_unit_logical_grouping_composition,
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



def test_unit_logical_grouping_composition_loads_static_contract() -> None:
    composition = build_unit_logical_grouping_composition()

    assert composition.composition_id == "unit-logical-grouping-v0.1"
    assert composition.parts[0].part_id == "unit-logical-grouping-contract"
    assert "Return only one JSON object" in composition.content
    assert "concept_deltas" in composition.content
    assert "logical_groups" in composition.content
    assert "merge|split|refine|reclassify" in composition.content
    assert "unresolved_items" in composition.content


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



# ── flatten_and_stabilize_segment_results tests ────────────────────────────────


def test_stabilize_merges_duplicate_concepts() -> None:
    """Same surface + same type across two segments → one merged concept."""
    result = flatten_and_stabilize_segment_results(
        [
            {
                "segment_id": "seg-0001",
                "source_blocks": [{"block_id": "b1", "text": "block 1"}],
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "余", "concept_type": "person",
                     "summary": "narrator", "source_block_refs": ["b1"],
                     "aliases": [], "observed_surfaces": ["余"], "facets": [],
                     "uncertainty": [], "canonical_name": ""}
                ],
                "atomic_items": [],
            },
            {
                "segment_id": "seg-0002",
                "source_blocks": [{"block_id": "b2", "text": "block 2"}],
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "余", "concept_type": "person",
                     "summary": "husband", "source_block_refs": ["b2"],
                     "aliases": [], "observed_surfaces": ["余"], "facets": [],
                     "uncertainty": [], "canonical_name": "沈复"}
                ],
                "atomic_items": [],
            },
        ],
        unit_id="unit-0001",
    )

    # One merged concept
    assert result["source_blocks"] == [{"block_id": "b1", "text": "block 1"}, {"block_id": "b2", "text": "block 2"}]
    assert len(result["concepts"]) == 1
    c = result["concepts"][0]
    assert c["concept_id"] == "concept-0001"
    assert c["surface"] == "余"
    assert c["concept_type"] == "person"
    assert c["merged_from"] == ["seg-0001-concept-0001", "seg-0002-concept-0001"]
    assert set(c["source_block_refs"]) == {"b1", "b2"}
    assert c["canonical_name"] == "沈复"  # first non-empty
    assert c["summary"] == "narrator"  # first non-empty
    assert c["provenance"] == {"grounding": "synthesis", "created_by": "deterministic"}
    assert result["unresolved_items"] == []


def test_stabilize_preserves_different_types() -> None:
    """Same surface but different types → separate concepts."""
    result = flatten_and_stabilize_segment_results(
        [
            {
                "segment_id": "seg-0001",
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "芸", "concept_type": "person",
                     "source_block_refs": ["b1"],
                     "aliases": [], "observed_surfaces": [], "facets": [],
                     "uncertainty": [], "canonical_name": "", "summary": ""}
                ],
                "atomic_items": [],
            },
            {
                "segment_id": "seg-0002",
                "concepts": [
                    {"concept_id": "concept-0002", "surface": "芸", "concept_type": "plant",
                     "source_block_refs": ["b2"],
                     "aliases": [], "observed_surfaces": [], "facets": [],
                     "uncertainty": [], "canonical_name": "", "summary": ""}
                ],
                "atomic_items": [],
            },
        ],
        unit_id="unit-0001",
    )

    assert len(result["concepts"]) == 2
    types = {c["concept_type"] for c in result["concepts"]}
    assert types == {"person", "plant"}

    # Unresolved item for ambiguous surface
    assert len(result["unresolved_items"]) == 1
    u = result["unresolved_items"][0]
    assert u["kind"] == "ambiguous_concept_surface"
    assert u["surface"] == "芸"
    assert set(u["candidate_types"]) == {"person", "plant"}


def test_stabilize_remaps_item_concept_refs() -> None:
    """After merge, item concept_refs point to merged concept IDs."""
    result = flatten_and_stabilize_segment_results(
        [
            {
                "segment_id": "seg-0001",
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "余", "concept_type": "person",
                     "source_block_refs": ["b1"],
                     "aliases": [], "observed_surfaces": [], "facets": [],
                     "uncertainty": [], "canonical_name": "", "summary": ""}
                ],
                "atomic_items": [
                    {"item_id": "item-0001", "concept_refs": ["concept-0001"],
                     "summary": "event", "item_type": "event",
                     "source_block_refs": ["b1"],
                     "temporal_attributes": [], "attributes": {}, "uncertainty": [],
                     "provenance": {"grounding": "source_grounded", "created_by": "llm_inferred"}}
                ],
            },
            {
                "segment_id": "seg-0002",
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "余", "concept_type": "person",
                     "source_block_refs": ["b2"],
                     "aliases": [], "observed_surfaces": [], "facets": [],
                     "uncertainty": [], "canonical_name": "", "summary": ""}
                ],
                "atomic_items": [
                    {"item_id": "item-0001", "concept_refs": ["concept-0001"],
                     "summary": "another event", "item_type": "event",
                     "source_block_refs": ["b2"],
                     "temporal_attributes": [], "attributes": {}, "uncertainty": [],
                     "provenance": {"grounding": "source_grounded", "created_by": "llm_inferred"}}
                ],
            },
        ],
        unit_id="unit-0001",
    )

    # Concept merged, items reindexed
    assert result["concepts"][0]["concept_id"] == "concept-0001"
    assert result["atomic_items"][0]["item_id"] == "item-0001"
    assert result["atomic_items"][1]["item_id"] == "item-0002"

    # Both items' concept_refs point to the merged concept ID
    assert result["atomic_items"][0]["concept_refs"] == ["concept-0001"]
    assert result["atomic_items"][1]["concept_refs"] == ["concept-0001"]


def test_stabilize_empty_input() -> None:
    result = flatten_and_stabilize_segment_results([], unit_id="unit-0001")
    assert result["source_blocks"] == []
    assert result["concepts"] == []
    assert result["atomic_items"] == []
    assert result["unresolved_items"] == []


def test_stabilize_single_segment_no_merge_needed() -> None:
    """Single segment with one concept — gets clean ID, merged_from with single entry."""
    result = flatten_and_stabilize_segment_results(
        [
            {
                "segment_id": "seg-0001",
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "余", "concept_type": "person",
                     "source_block_refs": ["b1"],
                     "aliases": [], "observed_surfaces": [], "facets": [],
                     "uncertainty": [], "canonical_name": "", "summary": ""}
                ],
                "atomic_items": [
                    {"item_id": "item-0001", "concept_refs": ["concept-0001"],
                     "summary": "event", "item_type": "event",
                     "source_block_refs": ["b1"],
                     "temporal_attributes": [], "attributes": {}, "uncertainty": [],
                     "provenance": {"grounding": "source_grounded", "created_by": "llm_inferred"}}
                ],
            },
        ],
        unit_id="unit-0001",
    )

    assert len(result["concepts"]) == 1
    assert result["concepts"][0]["concept_id"] == "concept-0001"
    assert result["concepts"][0]["merged_from"] == ["seg-0001-concept-0001"]
    assert result["atomic_items"][0]["item_id"] == "item-0001"
    assert result["atomic_items"][0]["concept_refs"] == ["concept-0001"]
    assert result["unresolved_items"] == []
