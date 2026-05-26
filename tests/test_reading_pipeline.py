from __future__ import annotations

import json

from tilusion.reading_pipeline import (
    MockReadingBackend,
    mock_per_segment_extraction_response,
    mock_unit_reading_finalization_response,
)
from tilusion.reading_schema import READING_UNIT_SCHEMA_VERSION
from tilusion.reading_validation import validate_extraction_unit_package


def test_mock_backend_dispatches_per_segment_extraction() -> None:
    backend = MockReadingBackend()
    payload = {
        "task": "per_segment_extraction",
        "unit_id": "unit-0001",
        "segment": {"segment_id": "seg-0001"},
        "text": "A short segment.\nWith two lines.",
        "context": {},
    }

    raw = backend.complete_json("system prompt", payload)
    result = json.loads(raw)

    assert result["unit_id"] == "unit-0001"
    assert result["segment_id"] == "seg-0001"
    assert len(result["source_spans"]) == 1
    assert result["source_spans"][0]["span_id"] == "span-seg-0001-0001"
    assert result["source_spans"][0]["quote"] == "A short segment."
    assert len(result["source_blocks"]) == 1
    assert len(result["concept_mentions"]) == 1
    assert len(result["logical_groups"]) == 1
    assert len(result["links"]) == 1
    assert result["links"][0]["grounding"] == "source_grounded"
    assert "mock per-segment extraction" in result["warnings"][0]


def test_mock_per_segment_extraction_handles_empty_text() -> None:
    result = mock_per_segment_extraction_response(
        {
            "unit_id": "unit-0001",
            "segment": {"segment_id": "seg-empty"},
            "text": "",
        }
    )

    assert result["source_spans"][0]["quote"] == ""


def test_mock_backend_dispatches_unit_reading_finalization() -> None:
    backend = MockReadingBackend()
    payload = {
        "task": "unit_reading_finalization",
        "unit_id": "unit-0001",
        "source": {"book_path": "test.txt"},
        "source_spans": [
            {"span_id": "span-seg-0001-0001", "unit_id": "unit-0001", "quote": "hello", "source_range": {}}
        ],
        "source_blocks": [
            {"block_id": "block-seg-0001-0001", "block_type": "paragraph", "span_refs": ["span-seg-0001-0001"]}
        ],
        "concept_mentions": [
            {
                "mention_id": "mention-seg-0001-0001",
                "surface": "hello",
                "concept_type": "other",
                "source_block_refs": ["block-seg-0001-0001"],
                "source_span_refs": ["span-seg-0001-0001"],
            }
        ],
        "logical_groups": [
            {
                "group_id": "group-seg-0001-0001",
                "group_type": "other",
                "summary": "Mock group.",
                "source_block_refs": ["block-seg-0001-0001"],
                "concept_refs": ["mention-seg-0001-0001"],
            }
        ],
        "links": [
            {
                "link_id": "link-seg-0001-0001",
                "source_ref": "group-seg-0001-0001",
                "target_ref": "mention-seg-0001-0001",
                "link_type": "mentions",
                "evidence_block_refs": ["block-seg-0001-0001"],
                "grounding": "source_grounded",
            }
        ],
        "context_metadata": {},
    }

    raw = backend.complete_json("system prompt", payload)
    result = json.loads(raw)

    assert result["schema_version"] == READING_UNIT_SCHEMA_VERSION
    assert result["unit_id"] == "unit-0001"

    # Unit-level IDs are re-indexed
    assert result["source_spans"][0]["span_id"] == "span-0001"
    assert result["source_blocks"][0]["block_id"] == "block-0001"
    assert result["concept_mentions"][0]["mention_id"] == "mention-0001"
    assert result["logical_groups"][0]["group_id"] == "group-0001"
    assert result["links"][0]["link_id"] == "link-0001"

    # Cross-references are re-wired to unit-level IDs
    assert result["links"][0]["source_ref"] == "group-0001"
    assert result["links"][0]["target_ref"] == "mention-0001"
    assert result["links"][0]["evidence_block_refs"] == ["block-0001"]
    assert result["logical_groups"][0]["concept_refs"] == ["mention-0001"]
    assert result["logical_groups"][0]["source_block_refs"] == ["block-0001"]
    assert result["concept_mentions"][0]["source_block_refs"] == ["block-0001"]
    assert result["concept_mentions"][0]["source_span_refs"] == ["span-0001"]
    assert result["source_blocks"][0]["span_refs"] == ["span-0001"]


def test_mock_finalization_passes_reading_validation() -> None:
    payload = {
        "task": "unit_reading_finalization",
        "unit_id": "unit-0001",
        "source": {"book_path": "test.txt"},
        "source_spans": [
            {"span_id": "span-seg-0001-0001", "unit_id": "unit-0001", "quote": "hello", "source_range": {}}
        ],
        "source_blocks": [
            {"block_id": "block-seg-0001-0001", "block_type": "paragraph", "span_refs": ["span-seg-0001-0001"]}
        ],
        "concept_mentions": [
            {
                "mention_id": "mention-seg-0001-0001",
                "surface": "hello",
                "concept_type": "other",
                "source_block_refs": ["block-seg-0001-0001"],
                "source_span_refs": ["span-seg-0001-0001"],
            }
        ],
        "logical_groups": [
            {
                "group_id": "group-seg-0001-0001",
                "group_type": "other",
                "summary": "Mock group.",
                "source_block_refs": ["block-seg-0001-0001"],
                "concept_refs": ["mention-seg-0001-0001"],
            }
        ],
        "links": [
            {
                "link_id": "link-seg-0001-0001",
                "source_ref": "group-seg-0001-0001",
                "target_ref": "mention-seg-0001-0001",
                "link_type": "mentions",
                "evidence_block_refs": ["block-seg-0001-0001"],
                "grounding": "source_grounded",
            }
        ],
        "context_metadata": {},
    }

    result = mock_unit_reading_finalization_response(payload)
    report = validate_extraction_unit_package(result)

    assert report.passed, f"Mock finalization should pass validation: {report.to_dict()['issues']}"


def test_mock_backend_raises_on_unknown_task() -> None:
    backend = MockReadingBackend()
    try:
        backend.complete_json("system", {"task": "unknown_task"})
        assert False, "Expected ValueError"
    except ValueError:
        pass
