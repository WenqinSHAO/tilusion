from __future__ import annotations

from tilusion.extraction_prompts import generated_prompt_part
from tilusion.reading_payloads import (
    build_link_structure_payload,
    build_logical_group_payload,
    build_source_block_concept_payload,
    build_unit_reading_finalization_payload,
    flatten_region_results,
)
from tilusion.reading_prompts import (
    build_link_structure_composition,
    build_logical_group_composition,
    build_source_block_concept_composition,
    build_unit_reading_finalization_composition,
)


def test_reading_prompt_compositions_load_static_contracts() -> None:
    generated = generated_prompt_part(
        "context-pack",
        role="document_context",
        content='{"guidance": "prior context is not evidence"}',
        generated_by="test",
    )

    composition = build_source_block_concept_composition([generated])

    assert composition.composition_id == "source-block-concept-v0.1"
    assert composition.parts[0].part_id == "source-block-concept-contract"
    assert composition.parts[-1].part_id == "context-pack"
    assert "Return only one JSON object" in composition.content
    assert composition.to_dict()["parts"][0]["metadata"]["schema_version"] == "reading-unit-v0.1"


def test_all_reading_prompt_compositions_have_stable_ids() -> None:
    assert build_logical_group_composition().composition_id == "logical-group-v0.1"
    assert build_link_structure_composition().composition_id == "link-structure-v0.1"
    assert build_unit_reading_finalization_composition().composition_id == "unit-reading-finalization-v0.1"


def test_reading_payload_builders_keep_context_separate_from_text() -> None:
    payload = build_source_block_concept_payload(
        unit={"id": "unit-0001"},
        region={"region_id": "region-0001"},
        text="Alice defines entropy.",
        context={"context_pack_id": "context-pack-1"},
    )

    assert payload["task"] == "source_block_concept"
    assert payload["schema_version"] == "reading-unit-v0.1"
    assert payload["context"]["context_pack_id"] == "context-pack-1"
    assert payload["text"] == "Alice defines entropy."


def test_logical_group_and_link_payloads_are_schema_versioned() -> None:
    group_payload = build_logical_group_payload(
        unit_id="unit-0001",
        region_id="region-0001",
        source_blocks=[{"block_id": "block-0001"}],
        concept_mentions=[{"mention_id": "mention-0001"}],
    )
    link_payload = build_link_structure_payload(
        unit_id="unit-0001",
        scope_id="region-0001",
        source_blocks=[],
        concept_mentions=[],
        logical_groups=[],
    )

    assert group_payload["task"] == "logical_group"
    assert link_payload["task"] == "link_structure"
    assert group_payload["schema_version"] == link_payload["schema_version"] == "reading-unit-v0.1"


def test_finalization_payload_declares_forbidden_legacy_core_fields() -> None:
    payload = build_unit_reading_finalization_payload(
        unit_id="unit-0001",
        source={"book_path": "book.txt"},
        regions=[],
        source_spans=[],
        source_blocks=[],
        concept_mentions=[],
        logical_groups=[],
        links=[],
    )

    assert payload["task"] == "unit_reading_finalization"
    assert "timelines" in payload["expected_output"]["forbidden_core_fields"]
    assert "logical_groups" in payload["expected_output"]["core_fields"]


def test_flatten_region_results_collects_reading_records() -> None:
    flat = flatten_region_results(
        [
            {"source_spans": [{"span_id": "s1"}], "logical_groups": [{"group_id": "g1"}]},
            {"source_blocks": [{"block_id": "b1"}], "links": [{"link_id": "l1"}]},
        ]
    )

    assert flat["source_spans"] == [{"span_id": "s1"}]
    assert flat["source_blocks"] == [{"block_id": "b1"}]
    assert flat["logical_groups"] == [{"group_id": "g1"}]
    assert flat["links"] == [{"link_id": "l1"}]
