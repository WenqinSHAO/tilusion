from __future__ import annotations

from tilusion.reading_schema import (
    ConceptMention,
    DerivedStructure,
    ExtractionUnitPackage,
    GroupLink,
    LogicalGroup,
    SourceBlock,
    SourceSpan,
    is_open_type_string,
    is_recommended_concept_type,
    is_recommended_group_type,
    is_recommended_link_type,
)


def test_reading_schema_accepts_recommended_and_custom_types() -> None:
    assert is_recommended_concept_type("person")
    assert is_recommended_group_type("claim")
    assert is_recommended_link_type("supports")

    assert is_open_type_string("custom_ritual_object")
    assert is_open_type_string("domain_specific_relation")
    assert not is_open_type_string("")
    assert not is_open_type_string(None)


def test_extraction_unit_package_has_stable_outer_shape_and_open_types() -> None:
    span = SourceSpan(
        span_id="span-0001",
        unit_id="unit-0001",
        source_range={"kind": "unit-char-span", "start": 0, "end": 12},
        quote="Alice argues",
        relocation={"status": "exact", "strategy": "exact"},
        provenance={"created_by": "deterministic"},
    )
    block = SourceBlock(
        block_id="block-0001",
        block_type="sentence",
        span_refs=["span-0001"],
        source_order=1,
        confidence="high",
    )
    concept = ConceptMention(
        mention_id="mention-0001",
        surface="Alice",
        concept_type="custom_reader_role",
        canonical_name="Alice",
        local_summary="Speaker in this sentence.",
        source_block_refs=["block-0001"],
        source_span_refs=["span-0001"],
        confidence="medium",
        facets=["behaves_like_person"],
    )
    group = LogicalGroup(
        group_id="group-0001",
        group_type="claim",
        summary="Alice makes an argument.",
        source_block_refs=["block-0001"],
        concept_refs=["mention-0001"],
        confidence="medium",
    )
    link = GroupLink(
        link_id="link-0001",
        source_ref="group-0001",
        target_ref="mention-0001",
        link_type="mentions",
        evidence_block_refs=["block-0001"],
        confidence="high",
    )
    view = DerivedStructure(
        view_id="view-0001",
        view_type="timeline",
        input_group_refs=["group-0001"],
        structure={"nodes": []},
        generated_by="deterministic",
    )

    package = ExtractionUnitPackage(
        unit_id="unit-0001",
        source={"book_path": "book.txt"},
        source_spans=[span],
        source_blocks=[block],
        concept_mentions=[concept],
        logical_groups=[group],
        links=[link],
        derived_views=[view],
    )
    data = package.to_dict()

    assert data["schema_version"] == "reading-unit-v0.1"
    assert data["concept_mentions"][0]["concept_type"] == "custom_reader_role"
    assert data["logical_groups"][0]["group_type"] == "claim"
    assert data["links"][0]["grounding"] == "source_grounded"
    assert data["derived_views"][0]["is_source_of_truth"] is False
    assert "timelines" not in data
    assert "entity_records" not in data
    assert "location_records" not in data
    assert "atom_records" not in data


def test_synthesis_link_can_be_marked_without_evidence_blocks() -> None:
    link = GroupLink(
        link_id="link-0002",
        source_ref="group-0001",
        target_ref="group-0002",
        link_type="custom_long_range_echo",
        grounding="synthesis",
        rationale="A later synthesis pass found a thematic echo.",
    )

    data = link.to_dict()

    assert data["grounding"] == "synthesis"
    assert data["evidence_block_refs"] == []
    assert data["link_type"] == "custom_long_range_echo"
