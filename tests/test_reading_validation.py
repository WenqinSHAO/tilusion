from __future__ import annotations

from tilusion.reading_schema import (
    AtomicItem,
    Concept,
    ExtractionUnitPackage,
    GraphEdge,
    GraphNode,
    LogicalGroup,
    SourceBlock,
    TemporalAttribute,
)
from tilusion.reading_validation import (
    validate_extraction_unit_package,
    validate_registry_delta,
)


def _block(block_id: str = "seg-0001-block-0000", text: str = "Alice defines entropy.") -> SourceBlock:
    return SourceBlock(
        block_id=block_id,
        unit_id="unit-0001",
        segment_id="seg-0001",
        block_index=0,
        block_type="paragraph",
        start=0,
        end=len(text),
        text=text,
        text_hash="abc123",
        provenance={"created_by": "deterministic"},
    )


def _valid_package() -> ExtractionUnitPackage:
    block1 = _block("seg-0001-block-0000", "Alice defines entropy.")
    block2_text = "The later paragraph gives an example."
    block2_start = 23
    block2 = SourceBlock(
        block_id="seg-0001-block-0001",
        unit_id="unit-0001",
        segment_id="seg-0001",
        block_index=1,
        block_type="paragraph",
        start=block2_start,
        end=block2_start + len(block2_text),
        text=block2_text,
        text_hash="def456",
        provenance={"created_by": "deterministic"},
    )
    concept = Concept(
        concept_id="concept-0001",
        surface="entropy",
        concept_type="technical_component",
        source_block_refs=["seg-0001-block-0000"],
        observed_surfaces=["entropy"],
        provenance={"grounding": "source_grounded"},
    )
    item1 = AtomicItem(
        item_id="item-0001",
        item_type="definition",
        summary="The text defines entropy.",
        source_block_refs=["seg-0001-block-0000"],
        concept_refs=["concept-0001"],
        temporal_attributes=[
            TemporalAttribute(kind="none", source_block_ref="seg-0001-block-0000")
        ],
        provenance={"grounding": "source_grounded"},
    )
    item2 = AtomicItem(
        item_id="item-0002",
        item_type="example",
        summary="A later paragraph gives an example of the definition.",
        source_block_refs=["seg-0001-block-0000", "seg-0001-block-0001"],
        concept_refs=["concept-0001"],
        provenance={"grounding": "source_grounded"},
    )
    group = LogicalGroup(
        group_id="group-0001",
        group_type="discourse_graph",
        summary="Definition and example are linked.",
        item_refs=["item-0001", "item-0002"],
        concept_refs=["concept-0001"],
        graph={
            "nodes": [
                GraphNode(node_id="node-0001", item_ref="item-0001"),
                GraphNode(node_id="node-0002", item_ref="item-0002"),
            ],
            "edges": [
                GraphEdge(
                    source="node-0001",
                    target="node-0002",
                    edge_type="exemplified_by",
                    summary="The example elaborates the definition.",
                    source_block_refs=["seg-0001-block-0001"],
                    provenance={"grounding": "source_grounded"},
                )
            ],
        },
        provenance={"grounding": "synthesis"},
    )
    return ExtractionUnitPackage(
        unit_id="unit-0001",
        source={"book_path": "book.txt"},
        source_blocks=[block1, block2],
        concepts=[concept],
        atomic_items=[item1, item2],
        logical_groups=[group],
    )


def _codes(report) -> list[str]:
    return [issue.code for issue in report.issues]


def test_reading_validation_accepts_v03_package_with_shared_blocks_and_graph() -> None:
    report = validate_extraction_unit_package(_valid_package())

    assert report.passed
    assert report.to_dict()["issue_count"] == 0


def test_reading_validation_rejects_stale_v01_core_fields() -> None:
    data = _valid_package().to_dict()
    data["source_spans"] = []
    data["concept_mentions"] = []
    data["links"] = []
    data["derived_views"] = []
    data["timelines"] = []

    report = validate_extraction_unit_package(data)
    codes = _codes(report)

    assert not report.passed
    assert codes.count("stale_core_field") == 5


def test_reading_validation_rejects_missing_source_block_refs_for_concepts() -> None:
    data = _valid_package().to_dict()
    data["concepts"][0]["source_block_refs"] = []

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "missing_source_block_refs" in _codes(report)


def test_reading_validation_rejects_missing_source_block_refs_for_atomic_items() -> None:
    data = _valid_package().to_dict()
    data["atomic_items"][0]["source_block_refs"] = []

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "missing_source_block_refs" in _codes(report)


def test_reading_validation_warns_on_broken_concept_refs() -> None:
    data = _valid_package().to_dict()
    data["atomic_items"][0]["concept_refs"] = ["missing-concept"]

    report = validate_extraction_unit_package(data)

    assert report.passed
    assert "unknown_ref" in _codes(report)
    assert report.warning_count == 1


def test_reading_validation_warns_on_graph_edge_with_unknown_node() -> None:
    data = _valid_package().to_dict()
    data["logical_groups"][0]["graph"]["edges"][0]["target"] = "missing-node"

    report = validate_extraction_unit_package(data)

    assert report.passed
    assert "unknown_ref" in _codes(report)
    assert report.warning_count == 1


def test_reading_validation_requires_source_grounded_graph_edge_evidence() -> None:
    data = _valid_package().to_dict()
    edge = data["logical_groups"][0]["graph"]["edges"][0]
    edge["source_block_refs"] = []
    edge["provenance"] = {"grounding": "source_grounded"}

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "missing_source_grounded_evidence" in _codes(report)


def test_reading_validation_allows_synthesis_graph_edge_without_evidence() -> None:
    data = _valid_package().to_dict()
    edge = data["logical_groups"][0]["graph"]["edges"][0]
    edge["source_block_refs"] = []
    edge["provenance"] = {"grounding": "synthesis"}

    report = validate_extraction_unit_package(data)

    assert report.passed


def test_reading_validation_rejects_prior_context_as_current_source_ref() -> None:
    data = _valid_package().to_dict()
    data["concepts"][0]["source_block_refs"] = ["context:concept-0001"]

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "prior_context_used_as_evidence" in _codes(report)
    assert "missing_source_block_refs" in _codes(report)


def test_reading_validation_rejects_source_block_round_trip_mismatch_when_unit_text_present() -> None:
    data = _valid_package().to_dict()
    data["source"] = {"unit_text": "Completely different source text."}

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "source_block_round_trip_mismatch" in _codes(report)


def test_reading_validation_does_not_judge_uncited_source_blocks() -> None:
    data = _valid_package().to_dict()
    data["atomic_items"] = data["atomic_items"][:1]
    data["logical_groups"] = []

    report = validate_extraction_unit_package(data)

    assert report.passed
    assert report.warning_count == 0


def test_reading_validation_rejects_schema_version_mismatch() -> None:
    data = _valid_package().to_dict()
    data["schema_version"] = "reading-unit-v0.2"

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "schema_version_mismatch" in _codes(report)


def test_reading_validation_rejects_duplicate_object_ids() -> None:
    data = _valid_package().to_dict()
    data["concepts"].append(dict(data["concepts"][0]))

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "duplicate_object_id" in _codes(report)


def test_reading_validation_rejects_invalid_source_block_range() -> None:
    data = _valid_package().to_dict()
    data["source_blocks"][0]["start"] = 5
    data["source_blocks"][0]["end"] = 3

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "invalid_source_block_range" in _codes(report)


def test_reading_validation_rejects_source_block_range_length_mismatch() -> None:
    data = _valid_package().to_dict()
    data["source_blocks"][0]["end"] += 1

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "source_block_range_length_mismatch" in _codes(report)


def test_reading_validation_rejects_source_block_unit_id_mismatch() -> None:
    data = _valid_package().to_dict()
    data["source_blocks"][0]["unit_id"] = "unit-other"

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "unit_id_mismatch" in _codes(report)


def test_reading_validation_rejects_invalid_grounding() -> None:
    data = _valid_package().to_dict()
    data["logical_groups"][0]["provenance"] = {"grounding": "unsupported"}

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "invalid_grounding" in _codes(report)


def test_reading_validation_rejects_invalid_type_string() -> None:
    data = _valid_package().to_dict()
    data["atomic_items"][0]["item_type"] = ""

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "invalid_type_string" in _codes(report)


def test_reading_validation_does_not_judge_all_singleton_logical_groups() -> None:
    data = _valid_package().to_dict()
    data["logical_groups"] = [
        {
            "group_id": "group-0001",
            "group_type": "theme_set",
            "summary": "First singleton.",
            "item_refs": ["item-0001"],
            "concept_refs": [],
            "graph": {"nodes": [], "edges": []},
            "uncertainty": [],
            "provenance": {"grounding": "synthesis"},
        },
        {
            "group_id": "group-0002",
            "group_type": "theme_set",
            "summary": "Second singleton.",
            "item_refs": ["item-0002"],
            "concept_refs": [],
            "graph": {"nodes": [], "edges": []},
            "uncertainty": [],
            "provenance": {"grounding": "synthesis"},
        },
    ]

    report = validate_extraction_unit_package(data)

    assert report.passed
    assert report.warning_count == 0



def test_reading_validation_accepts_top_level_metrics_object() -> None:
    data = _valid_package().to_dict()
    data["metrics"] = {"validation_counts": {}, "counts": {}}

    report = validate_extraction_unit_package(data)

    assert report.passed


def test_reading_validation_rejects_non_object_metrics() -> None:
    data = _valid_package().to_dict()
    data["metrics"] = []

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "wrong_field_type" in _codes(report)

def test_reading_validation_allows_context_like_temporal_surface_without_source_ref() -> None:
    data = _valid_package().to_dict()
    data["atomic_items"][0]["temporal_attributes"] = [
        {
            "kind": "implicit",
            "surface": "context: the 18th century",
            "normalized_hint": "18th century",
            "source_block_ref": "",
            "uncertainty": [],
        }
    ]

    report = validate_extraction_unit_package(data)

    assert report.passed


def test_reading_validation_rejects_blank_string_list_items() -> None:
    data = _valid_package().to_dict()
    data["concepts"][0]["aliases"] = [""]

    report = validate_extraction_unit_package(data)

    assert not report.passed
    assert "empty_string_list_item" in _codes(report)


# RegistryDelta validation now happens at the BookRegistry API level
# (apply_registry_delta in registry_delta.py). No separate validation pass.


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
    codes = set(_codes(report))

    assert not report.passed
    assert "base_snapshot_mismatch" in codes
    assert "unsupported_delta_operation" in codes
    assert "destructive_auto_merge" in codes
    assert "prior_context_used_as_evidence" in codes


def test_reading_validation_rejects_legacy_block_ids_when_source_indexed() -> None:
    data = _valid_package().to_dict()
    data["context_metadata"] = {"source_index_id": "source-index-a"}
    data["source_blocks"][0]["block_id"] = "overview-segment-0001-block-0000"
    data["source_blocks"][0]["provenance"] = {"source_index_id": "source-index-a"}
    data["concepts"][0]["source_block_refs"] = ["overview-segment-0001-block-0000"]
    data["atomic_items"][0]["source_block_refs"] = ["overview-segment-0001-block-0000"]

    report = validate_extraction_unit_package(data)

    assert "legacy_source_block_id" in _codes(report)


def test_reading_validation_rejects_source_index_mismatch() -> None:
    data = _valid_package().to_dict()
    data["context_metadata"] = {"source_index_id": "source-index-a"}
    data["source_blocks"][0]["block_id"] = "block-000001"
    data["source_blocks"][0]["provenance"] = {"source_index_id": "source-index-b"}
    data["concepts"][0]["source_block_refs"] = ["block-000001"]
    data["atomic_items"][0]["source_block_refs"] = ["block-000001"]

    report = validate_extraction_unit_package(data)

    assert "source_index_id_mismatch" in _codes(report)
