from __future__ import annotations

import json
from pathlib import Path

from tilusion.overview import ResolvedOverviewSegment
from tilusion.extraction_quality import EvidenceLocation
from tilusion.reading_pipeline import (
    MockReadingBackend,
    ReadingPassRecord,
    ReadingPipelineRecord,
    _apply_concept_deltas,
    _build_merge_rejection_items,
    _classify_merge_risk,
    _compose_concept_remaps,
    _dedupe_equivalent_concepts,
    _validate_merge_deltas,
    mock_per_segment_extraction_response,
    mock_unit_logical_grouping_response,
    run_per_segment_extraction_pass,
    run_unit_logical_grouping_pass,
    write_reading_unit_package,
)
from tilusion.reading_schema import READING_UNIT_SCHEMA_VERSION
from tilusion.reading_validation import ReadingValidationReport


def test_mock_backend_dispatches_per_segment_extraction() -> None:
    backend = MockReadingBackend()
    payload = {
        "task": "per_segment_extraction",
        "unit_id": "unit-0001",
        "segment": {"segment_id": "seg-0001"},
        "source_blocks": [
            {"block_id": "seg-0001-block-0000", "block_type": "paragraph", "start": 0, "end": 33}
        ],
        "text": "A short segment.\nWith two lines.",
        "context": {},
    }

    raw = backend.complete_json("system prompt", payload)
    result = json.loads(raw)

    assert result["unit_id"] == "unit-0001"
    assert result["segment_id"] == "seg-0001"
    assert len(result["concepts"]) == 1
    assert result["concepts"][0]["concept_id"] == "concept-0001"
    assert result["concepts"][0]["source_block_refs"] == ["seg-0001-block-0000"]
    assert len(result["atomic_items"]) == 1
    assert result["atomic_items"][0]["concept_refs"] == ["concept-0001"]
    assert "mock per-segment extraction" in result["warnings"][0]


def test_mock_per_segment_extraction_handles_empty_text() -> None:
    result = mock_per_segment_extraction_response(
        {
            "unit_id": "unit-0001",
            "segment": {"segment_id": "seg-empty"},
            "source_blocks": [],
            "text": "",
        }
    )

    assert result["concepts"] == []
    assert result["atomic_items"] == []


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


def _source_block(text: str = "Test segment.") -> dict[str, object]:
    return {
        "block_id": "seg-0001-block-0000",
        "unit_id": "unit-0001",
        "segment_id": "seg-0001",
        "block_index": 0,
        "block_type": "paragraph",
        "start": 0,
        "end": len(text),
        "text": text,
        "text_hash": "sha256:test",
        "provenance": {"created_by": "deterministic"},
    }


def test_run_per_segment_extraction_pass_with_mock(tmp_path: Path) -> None:
    backend = MockReadingBackend()
    segment = _make_segment(text="Alice defines entropy and Bob disagrees.")

    record = run_per_segment_extraction_pass(
        unit_id="unit-0001",
        segment=segment,
        backend=backend,
        cache_dir=tmp_path / "cache",
        use_cache=True,
    )

    assert record.pass_name == "per-segment-extraction"
    assert record.cache_hit is False
    assert len(record.data["source_blocks"]) == 1
    assert record.data["metrics"]["counts"]["per_segment"]["source_blocks"] == 1
    assert record.data["metrics"]["counts"]["per_segment"]["concepts"] == 1
    assert len(record.data["concepts"]) == 1
    assert len(record.data["atomic_items"]) == 1
    assert record.validation_report.passed

    assert Path(record.artifact_paths["result"]).exists()
    assert Path(record.artifact_paths["manifest"]).exists()
    assert Path(record.artifact_paths["validation_report"]).exists()


def test_run_per_segment_extraction_pass_uses_preselected_source_index_blocks(tmp_path: Path) -> None:
    from tilusion.reading_schema import SourceBlock

    backend = MockReadingBackend()
    unit_text = "Alpha begins. Beta continues."
    loc = _fake_location()
    segment = ResolvedOverviewSegment(
        segment_id="seg-0001",
        title="Test segment",
        summary="A test segment.",
        start=6,
        end=18,
        text=unit_text[6:18],
        source={"kind": "unit-char-span", "start": 6, "end": 18},
        start_location=loc,
        end_location=loc,
    )
    blocks = [
        SourceBlock(
            block_id="block-000001",
            unit_id="unit-0001",
            segment_id="",
            block_index=1,
            block_type="paragraph",
            start=0,
            end=len(unit_text),
            text=unit_text,
            text_hash="hash",
            provenance={"source_index_id": "source-index-test"},
        )
    ]

    record = run_per_segment_extraction_pass(
        unit_id="unit-0001",
        segment=segment,
        backend=backend,
        cache_dir=tmp_path / "cache",
        use_cache=False,
        unit_text=unit_text,
        source_blocks=blocks,
        source_index_id="source-index-test",
    )

    assert record.data["source_blocks"][0]["block_id"] == "block-000001"
    assert record.data["concepts"][0]["source_block_refs"] == ["block-000001"]
    assert record.data["context_metadata"]["source_index_id"] == "source-index-test"
    assert record.validation_report.passed


def test_run_per_segment_extraction_pass_cache_hit(tmp_path: Path) -> None:
    backend = MockReadingBackend()
    segment = _make_segment(text="Cached segment text.")

    record1 = run_per_segment_extraction_pass(
        unit_id="unit-0001",
        segment=segment,
        backend=backend,
        cache_dir=tmp_path / "cache",
        use_cache=True,
    )
    assert record1.cache_hit is False

    record2 = run_per_segment_extraction_pass(
        unit_id="unit-0001",
        segment=segment,
        backend=backend,
        cache_dir=tmp_path / "cache",
        use_cache=True,
    )
    assert record2.cache_hit is True
    assert record2.data == record1.data


def test_write_reading_unit_package(tmp_path: Path) -> None:
    data = {
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit_id": "unit-0001",
        "source": {"book_path": "test.txt"},
        "source_blocks": [],
        "concepts": [],
        "atomic_items": [],
        "logical_groups": [],
        "unresolved_items": [],
        "validation": {},
        "context_metadata": {},
        "metrics": {"validation_counts": {}, "counts": {}},
    }
    run_dir = tmp_path / "packages" / "unit-0001" / "run-abc"
    package_path = write_reading_unit_package(
        unit_id="unit-0001",
        source={"book_path": "test.txt"},
        data=data,
        validation={"passed": True},
        passes={
            "overview_segmentation": {"cache_key": "abc123"},
            "per_segment_extraction": {"elapsed_ms": 42, "segment_cache_keys": ["seg1", "seg2"]},
            "unit_logical_grouping": {"cache_key": "def456"},
        },
        run_hash="run-abc",
        run_dir=run_dir,
    )

    assert Path(package_path) == run_dir / "unit_package.json"
    assert Path(package_path).exists()
    written = json.loads(Path(package_path).read_text(encoding="utf-8"))
    assert written["unit_id"] == "unit-0001"
    assert written["schema_version"] == READING_UNIT_SCHEMA_VERSION
    assert written["logical_groups"] == []
    assert written["passes"]["per_segment_extraction"]["elapsed_ms"] == 42
    assert written["metrics"] == {"validation_counts": {}, "counts": {}}
    assert "data" not in written
    assert written["run_hash"] == "run-abc"


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

    json_str = record.to_json()
    reloaded = json.loads(json_str)
    assert reloaded["pass_name"] == "per-segment-extraction"


def test_reading_pipeline_record_serialization() -> None:
    record = ReadingPipelineRecord(
        unit_id="unit-0001",
        elapsed_ms=1234,
        unit_package_path="/tmp/unit_package.json",
        passes={"overview": {"elapsed_ms": 100}},
        data={"source_blocks": []},
        validation={"passed": True},
    )

    d = record.to_dict()
    assert d["unit_id"] == "unit-0001"
    assert d["elapsed_ms"] == 1234
    assert d["passes"]["overview"]["elapsed_ms"] == 100


# ── Mock logical grouping response tests ────────────────────────────────────────


def test_mock_logical_grouping_response_builds_group_from_items() -> None:
    result = mock_unit_logical_grouping_response(
        {
            "task": "unit_logical_grouping",
            "unit_id": "unit-0001",
            "concepts": [
                {"concept_id": "concept-0001", "surface": "余", "concept_type": "person"}
            ],
            "atomic_items": [
                {"item_id": "item-0001", "item_type": "event", "summary": "An event."},
                {"item_id": "item-0002", "item_type": "observation", "summary": "A note."},
            ],
        }
    )

    assert result["unit_id"] == "unit-0001"
    assert result["concept_deltas"] == []
    assert len(result["logical_groups"]) == 1
    group = result["logical_groups"][0]
    assert group["group_id"] == "group-0001"
    assert group["item_refs"] == ["item-0001", "item-0002"]
    assert group["concept_refs"] == ["concept-0001"]
    assert group["group_type"] == "other"
    assert result["unresolved_items"] == []
    assert "mock unit logical grouping" in result["warnings"][0]


def test_mock_logical_grouping_empty_items() -> None:
    result = mock_unit_logical_grouping_response(
        {
            "task": "unit_logical_grouping",
            "unit_id": "unit-0001",
            "concepts": [],
            "atomic_items": [],
        }
    )
    assert result["logical_groups"] == []
    assert result["concept_deltas"] == []


def test_mock_backend_dispatches_unit_logical_grouping() -> None:
    backend = MockReadingBackend()
    payload = {
        "task": "unit_logical_grouping",
        "unit_id": "unit-0001",
        "concepts": [],
        "atomic_items": [],
    }
    raw = backend.complete_json("system prompt", payload)
    result = json.loads(raw)
    assert result["unit_id"] == "unit-0001"
    assert result["logical_groups"] == []


# ── Concept delta application tests ─────────────────────────────────────────────


def test_apply_concept_deltas_refine() -> None:
    concepts = [
        {"concept_id": "concept-0001", "surface": "余", "concept_type": "person",
         "canonical_name": "", "summary": "old summary"}
    ]
    deltas = [
        {"delta_type": "refine", "target_refs": ["concept-0001"],
         "changes": {"canonical_name": "沈复", "summary": "new summary"}}
    ]
    updated, remap = _apply_concept_deltas(concepts, deltas, unit_id="unit-0001")

    assert len(updated) == 1
    assert updated[0]["canonical_name"] == "沈复"
    assert updated[0]["summary"] == "new summary"
    assert updated[0]["concept_id"] == "concept-0001"
    assert remap == {}


def test_apply_concept_deltas_reclassify() -> None:
    concepts = [
        {"concept_id": "concept-0001", "surface": "芸", "concept_type": "other"}
    ]
    deltas = [
        {"delta_type": "reclassify", "target_refs": ["concept-0001"],
         "changes": {"concept_type": "person"}}
    ]
    updated, _remap = _apply_concept_deltas(concepts, deltas, unit_id="unit-0001")

    assert updated[0]["concept_type"] == "person"


def test_dedupe_equivalent_concepts_after_reclassify() -> None:
    concepts = [
        {
            "concept_id": "concept-0001",
            "surface": "病",
            "concept_type": "theme",
            "canonical_name": "疾病",
            "source_block_refs": ["b1"],
            "observed_surfaces": ["病"],
        },
        {
            "concept_id": "concept-0002",
            "surface": "疾病",
            "concept_type": "condition",
            "canonical_name": "疾病",
            "source_block_refs": ["b2"],
            "observed_surfaces": ["疾病"],
        },
    ]

    updated, remap = _dedupe_equivalent_concepts(concepts)

    assert len(updated) == 1
    assert updated[0]["concept_id"] == "concept-0001"
    assert updated[0]["concept_type"] == "theme"
    assert set(updated[0]["source_block_refs"]) == {"b1", "b2"}
    assert set(updated[0]["observed_surfaces"]) == {"病", "疾病"}
    assert updated[0]["merged_from"] == ["concept-0001", "concept-0002"]
    assert remap == {"concept-0002": "concept-0001"}


def test_compose_concept_remaps_chains_delta_and_dedupe_maps() -> None:
    first = {"concept-0003": "concept-0002"}
    second = {"concept-0002": "concept-0001"}

    assert _compose_concept_remaps(first, second) == {
        "concept-0002": "concept-0001",
        "concept-0003": "concept-0001",
    }


def test_apply_concept_deltas_merge_removes_secondary_and_preserves_evidence() -> None:
    concepts = [
        {"concept_id": "concept-0001", "surface": "沈复", "concept_type": "person",
         "source_block_refs": ["b1"], "aliases": [], "observed_surfaces": ["沈复"]},
        {"concept_id": "concept-0002", "surface": "三白", "concept_type": "person",
         "source_block_refs": ["b2"], "aliases": ["三白"], "observed_surfaces": ["三白"]},
    ]
    deltas = [
        {"delta_type": "merge", "target_refs": ["concept-0001", "concept-0002"],
         "changes": {"canonical_name": "沈复"}}
    ]
    updated, remap = _apply_concept_deltas(concepts, deltas, unit_id="unit-0001")

    assert len(updated) == 1
    assert updated[0]["concept_id"] == "concept-0001"
    assert updated[0]["canonical_name"] == "沈复"
    assert updated[0]["source_block_refs"] == ["b1", "b2"]
    assert updated[0]["aliases"] == ["三白"]
    assert remap == {"concept-0001": "concept-0001", "concept-0002": "concept-0001"}


def test_apply_concept_deltas_split() -> None:
    concepts = [
        {"concept_id": "concept-0001", "surface": "余", "concept_type": "person",
         "summary": "merged narrator"}
    ]
    deltas = [
        {"delta_type": "split", "target_refs": ["concept-0001"],
         "changes": {"split_into": [
             {"surface": "余", "concept_type": "person", "summary": "narrator ch1"},
             {"surface": "余", "concept_type": "person", "summary": "narrator ch3"},
         ]}}
    ]
    updated, remap = _apply_concept_deltas(concepts, deltas, unit_id="unit-0001")

    assert len(updated) == 2
    assert updated[0]["concept_id"] == "concept-0002"
    assert updated[0]["summary"] == "narrator ch1"
    assert updated[1]["concept_id"] == "concept-0003"
    assert updated[1]["summary"] == "narrator ch3"
    assert remap["concept-0001"] == "concept-0002"


def test_apply_concept_deltas_empty_deltas() -> None:
    concepts = [{"concept_id": "concept-0001"}]
    updated, remap = _apply_concept_deltas(concepts, [], unit_id="unit-0001")
    assert updated == concepts
    assert remap == {}


# ── Logical grouping pass tests ─────────────────────────────────────────────────


def test_run_unit_logical_grouping_pass_with_mock(tmp_path: Path) -> None:
    backend = MockReadingBackend()
    unit_text = "Test segment."
    source = {"book_path": "test.txt"}
    segments = [_make_segment("seg-0001", unit_text)]
    source_blocks = [_source_block(unit_text)]

    concepts = [
        {"concept_id": "concept-0001", "surface": "余", "concept_type": "person",
         "source_block_refs": ["seg-0001-block-0000"]}
    ]
    items = [
        {"item_id": "item-0001", "item_type": "event", "summary": "An event.",
         "source_block_refs": ["seg-0001-block-0000"],
         "concept_refs": ["concept-0001"],
         "temporal_attributes": [], "attributes": {}, "uncertainty": [],
         "provenance": {"grounding": "source_grounded", "created_by": "llm_inferred"}}
    ]

    record = run_unit_logical_grouping_pass(
        unit_id="unit-0001",
        unit_text=unit_text,
        source=source,
        segments=segments,
        source_blocks=source_blocks,
        concepts=concepts,
        atomic_items=items,
        unresolved_items=[],
        backend=backend,
        cache_dir=tmp_path / "cache",
        use_cache=True,
    )

    assert record.pass_name == "unit-logical-grouping-v0.2"
    assert record.cache_hit is False
    assert record.data["unit_id"] == "unit-0001"
    assert record.data["source_blocks"] == source_blocks
    assert record.data["metrics"]["counts"]["grouping"]["logical_groups"] == 1
    assert record.data["metrics"]["counts"]["grouping"]["atomic_items_grouped"] == 1
    assert len(record.data["logical_groups"]) == 1
    assert record.data["logical_groups"][0]["group_id"] == "group-0001"
    assert record.validation_report.passed

    assert Path(record.artifact_paths["result"]).exists()
    assert Path(record.artifact_paths["manifest"]).exists()
    assert Path(record.artifact_paths["validation_report"]).exists()


# ── Merge safety validation tests ────────────────────────────────────────────


def _concept(cid: str, surface: str, ctype: str, canonical: str = "") -> dict[str, Any]:
    return {
        "concept_id": cid,
        "surface": surface,
        "concept_type": ctype,
        "canonical_name": canonical,
        "source_block_refs": [],
    }


def _merge_delta(
    delta_id: str,
    target_refs: list[str],
    changes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "delta_id": delta_id,
        "delta_type": "merge",
        "target_refs": target_refs,
        "changes": changes or {},
        "rationale": "test delta",
        "uncertainty": [],
        "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"},
    }


# ── Rejection cases ──


def test_reject_merge_distinct_time_anchors() -> None:
    """Two different dates must not merge into one time_anchor."""
    concepts = [
        _concept("concept-0001", "乾隆甲寅年", "time_anchor"),
        _concept("concept-0002", "七月", "time_anchor"),
        _concept("concept-0003", "七夕", "time_anchor"),
    ]
    deltas = [
        _merge_delta(
            "delta-0001",
            ["concept-0001", "concept-0002", "concept-0003"],
            changes={"surface": "重要日期", "concept_type": "time_anchor", "summary": "key dates"},
        )
    ]
    safe, rejected = _validate_merge_deltas(deltas, concepts)
    assert len(safe) == 0
    assert len(rejected) == 1
    assert "time_anchor" in rejected[0]["reason"]
    assert rejected[0]["delta"]["delta_id"] == "delta-0001"


def test_reject_merge_distinct_places() -> None:
    """Two different places must not merge into a region/route concept."""
    concepts = [
        _concept("concept-0001", "沧浪亭", "place"),
        _concept("concept-0002", "拙政园", "place"),
    ]
    deltas = [
        _merge_delta(
            "delta-0001",
            ["concept-0001", "concept-0002"],
            changes={"surface": "苏州园林群", "concept_type": "place", "summary": "garden collection"},
        )
    ]
    safe, rejected = _validate_merge_deltas(deltas, concepts)
    assert len(safe) == 0
    assert len(rejected) == 1
    assert "place" in rejected[0]["reason"]


def test_reject_merge_distinct_sources() -> None:
    """Two named texts must not merge into one source concept."""
    concepts = [
        _concept("concept-0001", "《关雎》", "source"),
        _concept("concept-0002", "《诗经》", "source"),
    ]
    deltas = [
        _merge_delta(
            "delta-0001",
            ["concept-0001", "concept-0002"],
            changes={"surface": "古典文学", "concept_type": "source", "summary": "classical works"},
        )
    ]
    safe, rejected = _validate_merge_deltas(deltas, concepts)
    assert len(safe) == 0
    assert len(rejected) == 1
    assert "source" in rejected[0]["reason"]


def test_reject_merge_synthetic_collection_label() -> None:
    """A proposed merged name not matching any target name is rejected."""
    concepts = [
        _concept("concept-0001", "古文", "term"),
        _concept("concept-0002", "诗", "term"),
        _concept("concept-0003", "赋", "term"),
    ]
    deltas = [
        _merge_delta(
            "delta-0001",
            ["concept-0001", "concept-0002", "concept-0003"],
            changes={"surface": "文学与游戏术语", "concept_type": "term", "summary": "terminology group"},
        )
    ]
    safe, rejected = _validate_merge_deltas(deltas, concepts)
    assert len(safe) == 0
    assert len(rejected) == 1
    reason = rejected[0]["reason"]
    assert "synthetic" in reason or "attested" in reason


# ── Acceptance cases ──


def test_allow_merge_same_person_aliases() -> None:
    """Same person with different surface forms but shared canonical_name merges."""
    concepts = [
        _concept("concept-0001", "相如", "person", canonical="司马相如"),
        _concept("concept-0002", "长卿", "person", canonical="司马相如"),
    ]
    deltas = [
        _merge_delta(
            "delta-0001",
            ["concept-0001", "concept-0002"],
            changes={"surface": "司马相如", "canonical_name": "司马相如", "concept_type": "person"},
        )
    ]
    safe, rejected = _validate_merge_deltas(deltas, concepts)
    assert len(safe) == 1
    assert len(rejected) == 0


def test_allow_merge_same_surface_different_segments() -> None:
    """Same surface from different segments is a legitimate duplicate merge."""
    concepts = [
        _concept("concept-0001", "余", "person", canonical=""),
        _concept("concept-0002", "余", "person", canonical="沈复"),
    ]
    deltas = [
        _merge_delta(
            "delta-0001",
            ["concept-0001", "concept-0002"],
            changes={"surface": "沈复", "canonical_name": "沈复", "concept_type": "person"},
        )
    ]
    safe, rejected = _validate_merge_deltas(deltas, concepts)
    assert len(safe) == 1
    assert len(rejected) == 0


def test_allow_merge_same_source_variant_punctuation() -> None:
    """Same source title with punctuation variants can merge if canonical matches."""
    concepts = [
        _concept("concept-0001", "关雎", "source", canonical="关雎"),
        _concept("concept-0002", "《关雎》", "source", canonical="关雎"),
    ]
    deltas = [
        _merge_delta(
            "delta-0001",
            ["concept-0001", "concept-0002"],
            changes={"surface": "《关雎》", "canonical_name": "关雎", "concept_type": "source"},
        )
    ]
    safe, rejected = _validate_merge_deltas(deltas, concepts)
    assert len(safe) == 1
    assert len(rejected) == 0


def test_allow_merge_single_target_passthrough() -> None:
    """A single-target merge delta (no-op or self-merge) passes through."""
    concepts = [
        _concept("concept-0001", "余", "person"),
    ]
    deltas = [
        _merge_delta(
            "delta-0001",
            ["concept-0001"],
            changes={"surface": "沈复", "canonical_name": "沈复"},
        )
    ]
    safe, rejected = _validate_merge_deltas(deltas, concepts)
    assert len(safe) == 1
    assert len(rejected) == 0


# ── Mixed deltas ──


def test_mixed_safe_and_unsafe_merge_deltas() -> None:
    """Safe and unsafe deltas are correctly partitioned."""
    concepts = [
        _concept("concept-0001", "余", "person", canonical=""),
        _concept("concept-0002", "余", "person", canonical="沈复"),
        _concept("concept-0003", "乾隆甲寅年", "time_anchor"),
        _concept("concept-0004", "七月", "time_anchor"),
    ]
    deltas = [
        _merge_delta(
            "delta-0001",
            ["concept-0001", "concept-0002"],
            changes={"surface": "沈复", "canonical_name": "沈复"},
        ),
        _merge_delta(
            "delta-0002",
            ["concept-0003", "concept-0004"],
            changes={"surface": "重要日期", "concept_type": "time_anchor"},
        ),
    ]
    safe, rejected = _validate_merge_deltas(deltas, concepts)
    assert len(safe) == 1
    assert safe[0]["delta_id"] == "delta-0001"
    assert len(rejected) == 1
    assert rejected[0]["delta"]["delta_id"] == "delta-0002"


# ── Non-merge deltas always pass through ──


def test_non_merge_deltas_passthrough() -> None:
    """Reclassify, refine, and split deltas are not screened by merge validation."""
    concepts: list[dict[str, Any]] = []
    deltas = [
        {"delta_id": "delta-0001", "delta_type": "reclassify", "target_refs": ["concept-0001"], "changes": {"concept_type": "object"}},
        {"delta_id": "delta-0002", "delta_type": "refine", "target_refs": ["concept-0002"], "changes": {"summary": "better summary"}},
    ]
    safe, rejected = _validate_merge_deltas(deltas, concepts)
    assert len(safe) == 2
    assert len(rejected) == 0


# ── Rejection items building ──


def test_build_merge_rejection_items_formats_correctly() -> None:
    """Rejected merges become well-formed unresolved_items with merge_proposal_rejected kind."""
    rejected = [
        {
            "delta": _merge_delta(
                "delta-0001",
                ["concept-0001", "concept-0002"],
                changes={"surface": "重要日期", "concept_type": "time_anchor"},
            ),
            "reason": "merge_rejected: merging distinct time_anchor concepts",
        }
    ]
    items = _build_merge_rejection_items(rejected, [])
    assert len(items) == 1
    assert items[0]["kind"] == "merge_proposal_rejected"
    assert items[0]["target_refs"] == ["concept-0001", "concept-0002"]
    assert "time_anchor" in items[0]["rejection_reason"]
    assert "delta-0001" in items[0]["delta_id"]


def test_build_merge_rejection_items_offsets_from_existing() -> None:
    """Rejection item IDs continue from existing unresolved_items count."""
    existing = [{"item_id": "unresolved-0001"}, {"item_id": "unresolved-0002"}]
    rejected = [
        {
            "delta": _merge_delta("delta-0001", ["concept-0001"], changes={}),
            "reason": "test reason",
        }
    ]
    items = _build_merge_rejection_items(rejected, existing)
    assert items[0]["item_id"] == "unresolved-0003"


# ── classify_merge_risk edge cases ──


def test_classify_merge_risk_shared_canonical_is_safe() -> None:
    """Targets sharing the same non-empty canonical_name are safe to merge."""
    targets = [
        _concept("concept-0001", "相如", "person", canonical="司马相如"),
        _concept("concept-0002", "长卿", "person", canonical="司马相如"),
    ]
    assert _classify_merge_risk(targets, {}) is None


def test_classify_merge_risk_same_surface_is_safe() -> None:
    """Targets with identical surface are safe to merge (probable duplicates)."""
    targets = [
        _concept("concept-0001", "余", "person"),
        _concept("concept-0002", "余", "person", canonical="沈复"),
    ]
    assert _classify_merge_risk(targets, {}) is None


def test_classify_merge_risk_distinct_terms_no_identity_signal() -> None:
    """Different terms with no shared canonical_name or surface are rejected."""
    targets = [
        _concept("concept-0001", "古文", "term"),
        _concept("concept-0002", "诗", "term"),
    ]
    reason = _classify_merge_risk(targets, {})
    assert reason is not None
    assert "distinct surfaces" in reason


def test_overview_segment_count_prefers_current_key() -> None:
    from tilusion.reading_pipeline import _overview_segment_count

    data = {
        "overview_segments": [{"segment_id": "seg-0001"}, {"segment_id": "seg-0002"}],
        "segments": [{"segment_id": "legacy"}],
    }

    assert _overview_segment_count(data) == 2


def test_overview_segment_count_accepts_legacy_key() -> None:
    from tilusion.reading_pipeline import _overview_segment_count

    assert _overview_segment_count({"segments": [{"segment_id": "legacy"}]}) == 1
    assert _overview_segment_count({}) == 0


# ── Uncertainty normalization tests ──────────────────────────────────────────


def test_normalize_uncertainty_coerces_dict_to_json_string() -> None:
    """Dict in uncertainty list is coerced to JSON string."""
    from tilusion.reading_pipeline import _normalize_uncertainty_fields

    data = {
        "concepts": [
            {"concept_id": "concept-0001", "uncertainty": [
                "plain string",
                {"note": "ambiguous reference", "confidence": "low"},
            ]},
        ],
    }
    _normalize_uncertainty_fields(data)
    assert all(isinstance(v, str) for v in data["concepts"][0]["uncertainty"])
    assert data["concepts"][0]["uncertainty"][0] == "plain string"
    assert "ambiguous reference" in data["concepts"][0]["uncertainty"][1]


def test_normalize_uncertainty_coerces_nested_temporal_attr() -> None:
    """Non-string in temporal_attributes uncertainty is coerced."""
    from tilusion.reading_pipeline import _normalize_uncertainty_fields

    data = {
        "atomic_items": [
            {
                "item_id": "item-0001",
                "temporal_attributes": [
                    {"kind": "explicit", "uncertainty": [{"reason": "vague"}]},
                ],
            },
        ],
    }
    _normalize_uncertainty_fields(data)
    attr = data["atomic_items"][0]["temporal_attributes"][0]
    assert all(isinstance(v, str) for v in attr["uncertainty"])


def test_normalize_uncertainty_handles_missing_fields() -> None:
    """Normalization does not crash on missing optional fields."""
    from tilusion.reading_pipeline import _normalize_uncertainty_fields

    data: dict[str, Any] = {"unit_id": "unit-0001"}
    _normalize_uncertainty_fields(data)
    assert data["unit_id"] == "unit-0001"


def test_normalize_uncertainty_preserves_strings() -> None:
    """Already-string uncertainty items are unchanged."""
    from tilusion.reading_pipeline import _normalize_uncertainty_fields

    data = {
        "concepts": [{"concept_id": "concept-0001", "uncertainty": ["note 1", "note 2"]}],
    }
    _normalize_uncertainty_fields(data)
    assert data["concepts"][0]["uncertainty"] == ["note 1", "note 2"]
