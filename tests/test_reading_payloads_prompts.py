from __future__ import annotations

from tilusion.extraction_prompts import generated_prompt_part
from tilusion.reading_payloads import (
    build_per_segment_extraction_payload,
    build_unit_reading_finalization_payload,
    flatten_segment_results,
)
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

    assert composition.composition_id == "per-segment-extraction-v0.1"
    assert composition.parts[0].part_id == "per-segment-extraction-contract"
    assert composition.parts[-1].part_id == "context-pack"
    assert "Return only one JSON object" in composition.content
    assert composition.to_dict()["parts"][0]["metadata"]["schema_version"] == "reading-unit-v0.1"


def test_unit_finalization_composition_has_stable_id() -> None:
    assert build_unit_reading_finalization_composition().composition_id == "unit-reading-finalization-v0.1"


def test_per_segment_extraction_payload_keeps_context_separate_from_text() -> None:
    payload = build_per_segment_extraction_payload(
        unit_id="unit-0001",
        segment={"segment_id": "seg-0001"},
        text="Alice defines entropy.",
        context={"context_pack_id": "context-pack-1"},
    )

    assert payload["task"] == "per_segment_extraction"
    assert payload["schema_version"] == "reading-unit-v0.1"
    assert payload["context"]["context_pack_id"] == "context-pack-1"
    assert payload["text"] == "Alice defines entropy."


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


def test_flatten_segment_results_collects_reading_records() -> None:
    flat = flatten_segment_results(
        [
            {"source_spans": [{"span_id": "s1"}], "logical_groups": [{"group_id": "g1"}]},
            {"source_blocks": [{"block_id": "b1"}], "links": [{"link_id": "l1"}]},
        ]
    )

    assert flat["source_spans"] == [{"span_id": "s1"}]
    assert flat["source_blocks"] == [{"block_id": "b1"}]
    assert flat["logical_groups"] == [{"group_id": "g1"}]
    assert flat["links"] == [{"link_id": "l1"}]
