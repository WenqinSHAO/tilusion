from __future__ import annotations

from tilusion.reading_schema import (
    ConceptMention,
    DerivedStructure,
    ExtractionUnitPackage,
    GroupLink,
    LogicalGroup,
    RegistryDelta,
    SourceBlock,
    SourceSpan,
)
from tilusion.reading_validation import (
    validate_extraction_unit_package,
    validate_registry_delta,
)


def _valid_package() -> ExtractionUnitPackage:
    span1 = SourceSpan(
        span_id="span-0001",
        unit_id="unit-0001",
        source_range={"kind": "unit-char-span", "start": 0, "end": 20},
        quote="Alice defines entropy.",
    )
    span2 = SourceSpan(
        span_id="span-0002",
        unit_id="unit-0001",
        source_range={"kind": "unit-char-span", "start": 80, "end": 120},
        quote="The later paragraph gives an example.",
    )
    block1 = SourceBlock(
        block_id="block-0001",
        block_type="sentence",
        span_refs=["span-0001"],
        source_order=1,
        confidence="high",
    )
    block2 = SourceBlock(
        block_id="block-0002",
        block_type="paragraph",
        span_refs=["span-0002"],
        source_order=2,
        confidence="medium",
    )
    concept = ConceptMention(
        mention_id="mention-0001",
        surface="entropy",
        concept_type="technical_component",
        source_block_refs=["block-0001"],
        source_span_refs=["span-0001"],
        confidence="high",
    )
    group1 = LogicalGroup(
        group_id="group-0001",
        group_type="definition",
        summary="The text defines entropy.",
        source_block_refs=["block-0001"],
        concept_refs=["mention-0001"],
        confidence="high",
    )
    group2 = LogicalGroup(
        group_id="group-0002",
        group_type="custom_scattered_example",
        summary="A non-contiguous example clarifies the definition.",
        source_block_refs=["block-0001", "block-0002"],
        concept_refs=["mention-0001"],
        confidence="medium",
    )
    link = GroupLink(
        link_id="link-0001",
        source_ref="group-0002",
        target_ref="group-0001",
        link_type="exemplifies",
        evidence_block_refs=["block-0002"],
        confidence="medium",
    )
    view = DerivedStructure(
        view_id="view-0001",
        view_type="discourse_graph",
        input_group_refs=["group-0001", "group-0002"],
        input_link_refs=["link-0001"],
        structure={"nodes": ["group-0001", "group-0002"]},
        confidence="medium",
    )
    return ExtractionUnitPackage(
        unit_id="unit-0001",
        source={"book_path": "book.txt"},
        source_spans=[span1, span2],
        source_blocks=[block1, block2],
        concept_mentions=[concept],
        logical_groups=[group1, group2],
        links=[link],
        derived_views=[view],
    )


def test_reading_validation_accepts_non_contiguous_groups_and_shared_blocks() -> None:
    report = validate_extraction_unit_package(_valid_package())

    assert report.passed
    assert report.to_dict()["issue_count"] == 0


def test_reading_validation_rejects_stale_core_timeline_fields() -> None:
    data = _valid_package().to_dict()
    data["timelines"] = []
    data["entity_records"] = []

    report = validate_extraction_unit_package(data)
    codes = [issue.code for issue in report.issues]

    assert not report.passed
    assert codes.count("stale_core_field") == 2


def test_reading_validation_requires_source_grounded_link_evidence() -> None:
    data = _valid_package().to_dict()
    data["links"][0]["evidence_block_refs"] = []

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert [issue.code for issue in report.issues] == ["missing_source_grounded_evidence"]


def test_reading_validation_allows_synthesis_link_without_evidence() -> None:
    data = _valid_package().to_dict()
    data["links"][0]["grounding"] = "synthesis"
    data["links"][0]["evidence_block_refs"] = []

    report = validate_extraction_unit_package(data)

    assert report.passed


def test_reading_validation_rejects_prior_context_as_evidence() -> None:
    data = _valid_package().to_dict()
    data["concept_mentions"][0]["source_block_refs"] = ["context:concept-0001"]

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert [issue.code for issue in report.issues] == ["prior_context_used_as_evidence"]


def test_reading_validation_rejects_derived_view_as_source_of_truth() -> None:
    data = _valid_package().to_dict()
    data["derived_views"][0]["is_source_of_truth"] = True

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert [issue.code for issue in report.issues] == ["derived_view_marked_source_of_truth"]


def test_registry_delta_validation_accepts_safe_proposals() -> None:
    delta = RegistryDelta(
        delta_id="delta-0001",
        base_snapshot_id="snapshot-0001",
        unit_id="unit-0002",
        operations=[
            {
                "operation_id": "op-0001",
                "operation_type": "merge_proposal",
                "target_refs": ["concept-0001", "concept-0002"],
                "evidence_refs": [{"unit_id": "unit-0002", "source_block_ref": "block-0001"}],
            }
        ],
    )

    report = validate_registry_delta(delta, expected_base_snapshot_id="snapshot-0001")

    assert report.passed


def test_registry_delta_validation_rejects_stale_snapshot_and_context_evidence() -> None:
    delta = {
        "schema_version": "registry-delta-v0.1",
        "delta_id": "delta-0002",
        "base_snapshot_id": "snapshot-old",
        "unit_id": "unit-0002",
        "operations": [
            {
                "operation_id": "op-0001",
                "operation_type": "rewrite_snapshot",
                "evidence_refs": ["prior:unit-0001:block-0001"],
            }
        ],
        "validation": {},
    }

    report = validate_registry_delta(delta, expected_base_snapshot_id="snapshot-current")
    codes = {issue.code for issue in report.issues}

    assert not report.passed
    assert "base_snapshot_mismatch" in codes
    assert "unsupported_delta_operation" in codes
    assert "destructive_auto_merge" in codes
    assert "prior_context_used_as_evidence" in codes
