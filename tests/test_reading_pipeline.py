from __future__ import annotations

import json
from pathlib import Path

from tilusion.extraction_pipeline import ResolvedOverviewSegment
from tilusion.extraction_quality import EvidenceLocation
from tilusion.reading_pipeline import (
    MockReadingBackend,
    ReadingPassRecord,
    ReadingPipelineRecord,
    mock_per_segment_extraction_response,
    mock_unit_reading_finalization_response,
    run_per_segment_extraction_pass,
    run_reading_finalization_pass,
    write_reading_unit_package,
)
from tilusion.reading_schema import READING_UNIT_SCHEMA_VERSION
from tilusion.reading_validation import (
    ReadingValidationReport,
    validate_extraction_unit_package,
)


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


# ── Pass function tests ──────────────────────────────────────────────────────


def _fake_location() -> EvidenceLocation:
    return EvidenceLocation(
        evidence_id="ev-0001",
        status="exact",
        strategy="exact",
        quote="",
        start=0,
        end=0,
    )


def _make_segment(segment_id: str = "seg-0001", text: str = "A test segment.") -> ResolvedOverviewSegment:
    loc = _fake_location()
    return ResolvedOverviewSegment(
        segment_id=segment_id,
        title="Test segment",
        summary="A test segment.",
        start=0,
        end=len(text),
        text=text,
        source={"kind": "unit-char-span", "start": 0, "end": len(text)},
        start_location=loc,
        end_location=loc,
    )


def test_run_per_segment_extraction_pass_with_mock(tmp_path: Path) -> None:
    backend = MockReadingBackend()
    segment = _make_segment(text="Alice defines entropy.\nBob disagrees.")

    record = run_per_segment_extraction_pass(
        unit_id="unit-0001",
        segment=segment,
        backend=backend,
        cache_dir=tmp_path / "cache",
        use_cache=True,
    )

    assert record.pass_name == "per-segment-extraction"
    assert record.cache_hit is False
    assert len(record.data["source_spans"]) == 1
    assert len(record.data["source_blocks"]) == 1
    assert len(record.data["concept_mentions"]) == 1
    assert len(record.data["logical_groups"]) == 1
    assert len(record.data["links"]) == 1
    assert record.validation_report.passed

    # Verify artifacts were written
    assert Path(record.artifact_paths["result"]).exists()
    assert Path(record.artifact_paths["manifest"]).exists()
    assert Path(record.artifact_paths["validation_report"]).exists()


def test_run_per_segment_extraction_pass_cache_hit(tmp_path: Path) -> None:
    backend = MockReadingBackend()
    segment = _make_segment(text="Cached segment text.")

    # First run — cache miss
    record1 = run_per_segment_extraction_pass(
        unit_id="unit-0001",
        segment=segment,
        backend=backend,
        cache_dir=tmp_path / "cache",
        use_cache=True,
    )
    assert record1.cache_hit is False

    # Second run with same params — cache hit
    record2 = run_per_segment_extraction_pass(
        unit_id="unit-0001",
        segment=segment,
        backend=backend,
        cache_dir=tmp_path / "cache",
        use_cache=True,
    )
    assert record2.cache_hit is True
    assert record2.data == record1.data


def test_run_reading_finalization_pass_with_mock(tmp_path: Path) -> None:
    backend = MockReadingBackend()
    segments = [_make_segment("seg-0001", "Alice defines entropy.")]

    # First extract a segment
    seg_record = run_per_segment_extraction_pass(
        unit_id="unit-0001",
        segment=segments[0],
        backend=backend,
        cache_dir=tmp_path / "seg_cache",
        use_cache=False,
    )

    record = run_reading_finalization_pass(
        unit_id="unit-0001",
        source={"book_path": "test.txt"},
        segments=segments,
        segment_records=[seg_record],
        backend=backend,
        cache_dir=tmp_path / "finalization_cache",
        use_cache=True,
    )

    assert record.pass_name == "unit-reading-finalization"
    assert record.cache_hit is False
    assert record.data["schema_version"] == READING_UNIT_SCHEMA_VERSION
    assert record.data["unit_id"] == "unit-0001"
    assert len(record.data["source_spans"]) == 1
    assert record.validation_report.passed

    # IDs should be unit-level (re-indexed)
    assert record.data["source_spans"][0]["span_id"] == "span-0001"


def test_write_reading_unit_package(tmp_path: Path) -> None:
    package_path = write_reading_unit_package(
        unit_id="unit-0001",
        source={"book_path": "test.txt"},
        data={
            "schema_version": READING_UNIT_SCHEMA_VERSION,
            "unit_id": "unit-0001",
            "source": {},
            "source_spans": [],
            "source_blocks": [],
            "concept_mentions": [],
            "logical_groups": [],
            "links": [],
            "derived_views": [],
            "unresolved_items": [],
            "validation": {},
            "context_metadata": {},
        },
        validation={"passed": True},
        passes={"per_segment": {"elapsed_ms": 42}},
        cache_root=tmp_path / "packages",
    )

    assert Path(package_path).exists()
    written = json.loads(Path(package_path).read_text(encoding="utf-8"))
    assert written["unit_id"] == "unit-0001"
    assert written["schema_version"] == READING_UNIT_SCHEMA_VERSION
    assert written["data"]["logical_groups"] == []
    assert written["passes"]["per_segment"]["elapsed_ms"] == 42


def test_reading_pass_record_serialization() -> None:
    report = ReadingValidationReport(subject_id="unit-0001", issues=[])
    record = ReadingPassRecord(
        pass_name="per-segment-extraction",
        cache_key="abc123",
        cache_dir="/tmp/cache",
        cache_hit=False,
        raw_response='{"key": "val"}',
        data={"key": "val"},
        validation_report=report,
        artifact_paths={"result": "/tmp/result.json"},
    )

    d = record.to_dict()
    assert d["pass_name"] == "per-segment-extraction"
    assert d["cache_hit"] is False
    assert d["validation_report"]["passed"] is True

    # JSON round-trip
    json_str = record.to_json()
    reloaded = json.loads(json_str)
    assert reloaded["pass_name"] == "per-segment-extraction"


def test_reading_pipeline_record_serialization() -> None:
    record = ReadingPipelineRecord(
        unit_id="unit-0001",
        elapsed_ms=1234,
        unit_package_path="/tmp/unit_package.json",
        passes={"overview": {"elapsed_ms": 100}},
        data={"source_spans": []},
        validation={"passed": True},
    )

    d = record.to_dict()
    assert d["unit_id"] == "unit-0001"
    assert d["elapsed_ms"] == 1234
    assert d["passes"]["overview"]["elapsed_ms"] == 100
