from __future__ import annotations

import pytest

from tilusion.pass_utils import generated_prompt_part
from tilusion.reading_payloads import (
    _source_block_meta,
    build_concept_resolution_payload,
    build_group_resolution_payload,
    build_language_policy,
    build_per_segment_extraction_payload,
    merge_segment_extraction_results,
    render_text_with_block_markers,
)
from tilusion.reading_schema import RECOMMENDED_CONCEPT_TYPES, SourceBlock, normalize_concept_type
from tilusion.reading_prompts import (
    build_concept_resolution_v0_2_composition,
    build_group_resolution_v0_2_composition,
    build_per_segment_extraction_composition,
    build_unit_logical_grouping_composition,
    build_unit_logical_grouping_v0_2_composition,
)


def test_per_segment_extraction_composition_loads_static_contract() -> None:
    generated = generated_prompt_part(
        "context-pack",
        role="document_context",
        content='{"guidance": "prior context is not evidence"}',
        generated_by="test",
    )

    composition = build_per_segment_extraction_composition([generated])

    assert composition.composition_id == "per-segment-extraction-v0.3"
    assert composition.parts[0].part_id == "per-segment-extraction-contract"
    assert composition.parts[-1].part_id == "context-pack"
    assert "Return only one JSON object" in composition.content
    assert "inline markers" in composition.content
    assert "atomic_items" in composition.content
    assert "Do not invent block IDs" in composition.content
    assert "Field-language policy" in composition.content
    assert "reader_language" in composition.content
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


def test_per_segment_prompt_advertises_coarse_concept_types() -> None:
    composition = build_per_segment_extraction_composition()
    content = composition.content

    # v0.3 prompt targets novels/essays; preferred types should appear
    narrative_preferred = {
        "person", "place", "time_anchor", "object", "term", "method",
        "theme", "motif", "emotion", "social_role", "symbol", "source", "other",
    }
    for concept_type in narrative_preferred:
        assert concept_type in content, f"preferred type '{concept_type}' missing from prompt"

    # These should NOT appear for novels/essays
    for dropped in {"dataset", "metric", "technical_component"}:
        assert dropped not in content, f"'{dropped}' should not appear in narrative prompt"

    old_fine_grained_shape = (
        "person|place|term|method|theme|time_anchor|event_type|object|"
        "organization|work|concept|phenomenon|condition|relationship|role|"
        "metric|component|format|substance|other|custom"
    )
    assert old_fine_grained_shape not in content
    assert "Prefer this concept vocabulary" in content
    assert "Also accepted when needed" in content


def test_unit_grouping_prompt_prefers_schema_concept_types() -> None:
    composition = build_unit_logical_grouping_composition()
    content = composition.content

    assert "work`/`collection` (use `source`)" in content
    assert "source`, `other`" in content
    assert "`work`/`collection`, `motif`" not in content


def test_unit_grouping_v0_3_prompt_defines_temporal_granularity_and_language_policy() -> None:
    composition = build_unit_logical_grouping_v0_2_composition()
    content = composition.content

    assert composition.composition_id == "unit-logical-grouping-v0.3"
    assert "Field-language policy" in content
    assert "reader_language" in content
    assert "`timeline`: coarse" in content
    assert "`temporal_sequence`: local" in content


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
    assert payload["language_policy"] == build_language_policy()
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
            block_type="paragraph",
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
        "{seg-0001-block-0001:paragraph}"
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



# ── merge_segment_extraction_results tests ────────────────────────────────


def test_segment_merge_merges_duplicate_concepts() -> None:
    """Same surface + same type across two segments → one merged concept."""
    result = merge_segment_extraction_results(
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
    assert c["surface"] == "沈复"  # canonical_name preferred over raw surface
    assert c["concept_type"] == "person"
    assert c["merged_from"] == ["seg-0001-concept-0001", "seg-0002-concept-0001"]
    assert set(c["source_block_refs"]) == {"b1", "b2"}
    assert c["canonical_name"] == "沈复"  # first non-empty
    assert c["summary"] == "narrator"  # first non-empty
    assert c["provenance"] == {"grounding": "synthesis", "created_by": "deterministic"}
    assert result["unresolved_items"] == []
    assert result["metrics"]["counts"]["segment_merge"] == {
        "source_blocks": 2,
        "concepts_before_merge": 2,
        "concepts_after_merge": 1,
        "concept_merge_count": 1,
        "atomic_items": 0,
        "unresolved_items": 0,
        "ambiguous_surface_count": 0,
        "warning_count": 0,
    }


def test_normalize_concept_type_collapses_known_noisy_aliases() -> None:
    assert normalize_concept_type("phenomenon") == "theme"
    assert normalize_concept_type("event type") == "theme"
    assert normalize_concept_type("relationship") == "social_role"
    assert normalize_concept_type("work") == "source"
    assert normalize_concept_type("technical component") == "technical_component"
    assert normalize_concept_type("") == "other"


def test_canonical_name_merge_across_different_surfaces() -> None:
    """Concepts with different surfaces but same canonical_name + type → merged."""
    result = merge_segment_extraction_results(
        [
            {
                "segment_id": "seg-0001",
                "source_blocks": [],
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "相如", "concept_type": "person",
                     "summary": "汉代辞赋家", "source_block_refs": ["b1"],
                     "aliases": [], "observed_surfaces": ["相如"], "facets": [],
                     "uncertainty": [], "canonical_name": "司马相如"}
                ],
                "atomic_items": [],
            },
            {
                "segment_id": "seg-0002",
                "source_blocks": [],
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "长卿", "concept_type": "person",
                     "summary": "字长卿", "source_block_refs": ["b2"],
                     "aliases": [], "observed_surfaces": ["长卿"], "facets": [],
                     "uncertainty": [], "canonical_name": "司马相如"}
                ],
                "atomic_items": [],
            },
        ],
        unit_id="unit-0001",
    )

    assert len(result["concepts"]) == 1
    c = result["concepts"][0]
    assert c["surface"] == "司马相如"
    assert c["canonical_name"] == "司马相如"
    assert c["concept_type"] == "person"
    assert len(c["merged_from"]) == 2
    assert "相如" in c["observed_surfaces"]
    assert "长卿" in c["observed_surfaces"]


def test_canonical_name_merge_across_normalized_equivalent_types() -> None:
    """Same canonical_name with noisy equivalent types merges after normalization."""
    result = merge_segment_extraction_results(
        [
            {
                "segment_id": "seg-0001",
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "病", "concept_type": "condition",
                     "summary": "illness as a recurring concern", "source_block_refs": ["b1"],
                     "aliases": [], "observed_surfaces": ["病"], "facets": [],
                     "uncertainty": [], "canonical_name": "疾病"}
                ],
                "atomic_items": [],
            },
            {
                "segment_id": "seg-0002",
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "疾病", "concept_type": "phenomenon",
                     "summary": "illness phenomenon", "source_block_refs": ["b2"],
                     "aliases": [], "observed_surfaces": ["疾病"], "facets": [],
                     "uncertainty": [], "canonical_name": "疾病"}
                ],
                "atomic_items": [],
            },
        ],
        unit_id="unit-0001",
    )

    assert len(result["concepts"]) == 1
    c = result["concepts"][0]
    assert c["concept_type"] == "theme"
    assert c["canonical_name"] == "疾病"
    assert set(c["source_block_refs"]) == {"b1", "b2"}


def test_segment_merge_preserves_different_types() -> None:
    """Same surface but different types → separate concepts."""
    result = merge_segment_extraction_results(
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


def test_segment_merge_remaps_item_concept_refs() -> None:
    """After merge, item concept_refs point to merged concept IDs."""
    result = merge_segment_extraction_results(
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


def test_segment_merge_empty_input() -> None:
    result = merge_segment_extraction_results([], unit_id="unit-0001")
    assert result["source_blocks"] == []
    assert result["concepts"] == []
    assert result["atomic_items"] == []
    assert result["unresolved_items"] == []
    assert result["metrics"]["counts"]["segment_merge"]["source_blocks"] == 0


def test_segment_merge_single_segment_no_merge_needed() -> None:
    """Single segment with one concept — gets clean ID, merged_from with single entry."""
    result = merge_segment_extraction_results(
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


def test_segment_merge_preserves_llm_inferred_grounding() -> None:
    """Single concept with llm_inferred grounding keeps it (not upgraded to synthesis)."""
    result = merge_segment_extraction_results(
        [
            {
                "segment_id": "seg-0001",
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "白泥", "concept_type": "object",
                     "source_block_refs": [],
                     "aliases": [], "observed_surfaces": [], "facets": [],
                     "uncertainty": [], "canonical_name": "", "summary": "inferred material",
                     "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"}}
                ],
                "atomic_items": [],
            },
        ],
        unit_id="unit-0001",
    )

    assert len(result["concepts"]) == 1
    assert result["concepts"][0]["provenance"]["grounding"] == "llm_inferred"
    assert result["concepts"][0]["source_block_refs"] == []


def test_segment_merge_llm_inferred_group_keeps_grounding() -> None:
    """Multiple llm_inferred concepts with same surface+type keep llm_inferred."""
    result = merge_segment_extraction_results(
        [
            {
                "segment_id": "seg-0001",
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "白泥", "concept_type": "object",
                     "source_block_refs": [], "summary": "a",
                     "aliases": [], "observed_surfaces": [], "facets": [],
                     "uncertainty": [], "canonical_name": "",
                     "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"}}
                ],
                "atomic_items": [],
            },
            {
                "segment_id": "seg-0002",
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "白泥", "concept_type": "object",
                     "source_block_refs": [], "summary": "b",
                     "aliases": [], "observed_surfaces": [], "facets": [],
                     "uncertainty": [], "canonical_name": "",
                     "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"}}
                ],
                "atomic_items": [],
            },
        ],
        unit_id="unit-0001",
    )

    assert len(result["concepts"]) == 1
    assert result["concepts"][0]["provenance"]["grounding"] == "llm_inferred"


def test_segment_merge_mixed_grounding_becomes_synthesis() -> None:
    """Mixing llm_inferred and source_grounded concepts → synthesis."""
    result = merge_segment_extraction_results(
        [
            {
                "segment_id": "seg-0001",
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "白泥", "concept_type": "object",
                     "source_block_refs": [], "summary": "a",
                     "aliases": [], "observed_surfaces": [], "facets": [],
                     "uncertainty": [], "canonical_name": "",
                     "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"}}
                ],
                "atomic_items": [],
            },
            {
                "segment_id": "seg-0002",
                "concepts": [
                    {"concept_id": "concept-0001", "surface": "白泥", "concept_type": "object",
                     "source_block_refs": ["b2"], "summary": "b",
                     "aliases": [], "observed_surfaces": [], "facets": [],
                     "uncertainty": [], "canonical_name": "",
                     "provenance": {"grounding": "source_grounded", "created_by": "llm_inferred"}}
                ],
                "atomic_items": [],
            },
        ],
        unit_id="unit-0001",
    )

    assert len(result["concepts"]) == 1
    assert result["concepts"][0]["provenance"]["grounding"] == "synthesis"
    assert set(result["concepts"][0]["source_block_refs"]) == {"b2"}


def test_concept_resolution_payload_includes_candidate_map() -> None:
    candidate_map = [{
        "unit_concept_id": "concept-0001",
        "deterministic_candidate_ids": ["book-concept-1"],
        "semantic_candidates": [],
        "candidate_ids": ["book-concept-1"],
    }]

    payload = build_concept_resolution_payload(
        unit_id="unit-0002",
        concepts=[{"concept_id": "concept-0001"}],
        registry_index=[{"concept_id": "book-concept-1"}],
        candidate_map=candidate_map,
        unresolved_items=[],
    )

    assert payload["candidate_map"] == candidate_map
    assert payload["language_policy"] == build_language_policy()


def test_agentic_concept_prompt_uses_candidate_map_first() -> None:
    composition = build_concept_resolution_v0_2_composition()
    content = composition.content

    assert "candidate_map" in content
    assert "primary screening structure" in content
    assert "Search only when needed" in content
    assert "search_concepts(query)` only" in content
    assert "Field-Language Policy" in content
    assert "mixed-language glosses" in content


def test_group_resolution_payload_and_prompt_include_language_policy() -> None:
    payload = build_group_resolution_payload(
        unit_id="unit-0002",
        concepts=[],
        groups=[],
        registry_groups=[],
    )
    composition = build_group_resolution_v0_2_composition()

    assert payload["language_policy"] == build_language_policy()
    assert composition.composition_id == "group-resolution-v0.3"
    assert "Field-Language Policy" in composition.content
    assert "may become part of a broader `timeline`" in composition.content
