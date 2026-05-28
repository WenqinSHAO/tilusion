from __future__ import annotations

import pytest

from tilusion.reading_schema import (
    READING_UNIT_SCHEMA_VERSION,
    AtomicItem,
    Concept,
    DocumentStateSnapshot,
    ExtractionUnitPackage,
    GraphEdge,
    GraphNode,
    LogicalGroup,
    RegistryDelta,
    SourceBlock,
    TemporalAttribute,
    is_open_type_string,
    is_recommended_concept_type,
    is_recommended_edge_type,
    is_recommended_group_type,
    is_recommended_item_type,
    is_recommended_provenance,
)


# ── Type validation helpers ──────────────────────────────────────────────────


def test_is_open_type_string():
    assert is_open_type_string("person")
    assert is_open_type_string("custom_ritual_object")
    assert not is_open_type_string("")
    assert not is_open_type_string(None)
    assert not is_open_type_string(123)


def test_recommended_concept_types_include_core_categories():
    for t in ("person", "place", "term", "theme", "time_anchor", "other"):
        assert is_recommended_concept_type(t)


def test_recommended_concept_types_reject_random():
    assert not is_recommended_concept_type("not_a_real_concept_type")


def test_recommended_item_types():
    for t in ("event", "claim", "observation", "other"):
        assert is_recommended_item_type(t)
    assert not is_recommended_item_type("not_an_item_type")


def test_recommended_group_types():
    for t in ("timeline", "discourse_graph", "theme_set", "other"):
        assert is_recommended_group_type(t)
    assert not is_recommended_group_type("not_a_group_type")


def test_recommended_edge_types():
    for t in ("precedes", "supports", "contradicts", "related_to", "other"):
        assert is_recommended_edge_type(t)
    assert not is_recommended_edge_type("not_an_edge_type")


def test_recommended_provenance():
    for v in ("source_grounded", "synthesis", "deterministic"):
        assert is_recommended_provenance(v)
    assert not is_recommended_provenance("imaginary")


# ── SourceBlock ──────────────────────────────────────────────────────────────


def test_source_block_fields():
    blk = SourceBlock(
        block_id="seg-0001-block-0000",
        unit_id="unit-0001",
        segment_id="seg-0001",
        block_index=0,
        block_type="paragraph",
        start=100,
        end=250,
        text="Some source text",
        text_hash="abc123",
        provenance={"created_by": "deterministic"},
    )
    assert blk.block_id == "seg-0001-block-0000"
    assert blk.start == 100
    assert blk.end == 250
    assert blk.text == "Some source text"


def test_source_block_to_dict():
    blk = SourceBlock(
        block_id="seg-0001-block-0000",
        unit_id="unit-0001",
        segment_id="seg-0001",
        block_index=0,
        block_type="paragraph",
        start=0,
        end=5,
        text="Hello",
        text_hash="abc",
    )
    d = blk.to_dict()
    assert d["block_id"] == "seg-0001-block-0000"
    assert d["block_type"] == "paragraph"
    assert "provenance" in d


# ── Concept ──────────────────────────────────────────────────────────────────


def test_concept_with_custom_type():
    c = Concept(
        concept_id="concept-0001",
        surface="Alice",
        concept_type="custom_character_role",
        source_block_refs=["seg-0001-block-0000"],
        canonical_name="Alice Liddell",
        summary="Protagonist",
        aliases=["A"],
        observed_surfaces=["Alice", "A"],
        facets=["behaves_like_person"],
    )
    d = c.to_dict()
    assert d["concept_type"] == "custom_character_role"
    assert d["source_block_refs"] == ["seg-0001-block-0000"]


def test_concept_minimal():
    c = Concept(concept_id="c-1", surface="X", concept_type="other")
    d = c.to_dict()
    assert d["concept_id"] == "c-1"
    assert d["summary"] == ""


def test_concept_source_block_refs_optional():
    c = Concept(concept_id="c-1", surface="X", concept_type="term")
    assert c.source_block_refs == []


# ── TemporalAttribute ────────────────────────────────────────────────────────


def test_temporal_attribute():
    ta = TemporalAttribute(
        kind="explicit",
        surface="1780-02-26",
        normalized_hint="1780-02-26",
        source_block_ref="seg-0001-block-0000",
    )
    d = ta.to_dict()
    assert d["kind"] == "explicit"
    assert d["normalized_hint"] == "1780-02-26"


# ── AtomicItem ───────────────────────────────────────────────────────────────


def test_atomic_item_with_temporal_attributes():
    item = AtomicItem(
        item_id="item-0001",
        item_type="event",
        summary="A significant event occurred.",
        source_block_refs=["seg-0001-block-0000"],
        concept_refs=["concept-0001"],
        temporal_attributes=[
            TemporalAttribute(kind="explicit", surface="1780", normalized_hint="1780")
        ],
        attributes={"narrative_role": "turning_point"},
    )
    d = item.to_dict()
    assert d["item_type"] == "event"
    assert len(d["temporal_attributes"]) == 1
    assert d["temporal_attributes"][0]["kind"] == "explicit"
    assert d["attributes"]["narrative_role"] == "turning_point"


def test_atomic_item_custom_type():
    item = AtomicItem(
        item_id="item-0001",
        item_type="custom_ritual_description",
        summary="A ritual is described.",
        source_block_refs=["seg-0001-block-0000"],
    )
    assert is_open_type_string(item.item_type)


# ── LogicalGroup ─────────────────────────────────────────────────────────────


def test_logical_group_bare():
    g = LogicalGroup(
        group_id="group-0001",
        group_type="open_thread_list",
        summary="Unresolved questions.",
        item_refs=["item-0001", "item-0002"],
        concept_refs=["concept-0001"],
    )
    d = g.to_dict()
    assert d["group_type"] == "open_thread_list"
    assert d["item_refs"] == ["item-0001", "item-0002"]
    assert d["graph"] == {"nodes": [], "edges": []}


def test_logical_group_with_graph():
    nodes = [
        GraphNode(node_id="n1", item_ref="item-0001", label="Start"),
        GraphNode(node_id="n2", item_ref="item-0002", label="End"),
    ]
    edges = [
        GraphEdge(
            source="n1",
            target="n2",
            edge_type="precedes",
            summary="Event A precedes event B.",
            source_block_refs=["seg-0001-block-0000"],
            provenance={"grounding": "source_grounded"},
        )
    ]
    g = LogicalGroup(
        group_id="group-0001",
        group_type="timeline",
        summary="A chronological sequence.",
        item_refs=["item-0001", "item-0002"],
        graph={"nodes": nodes, "edges": edges},
    )
    d = g.to_dict()
    assert d["graph"]["nodes"][0]["item_ref"] == "item-0001"
    assert d["graph"]["edges"][0]["edge_type"] == "precedes"
    assert d["graph"]["edges"][0]["source_block_refs"] == ["seg-0001-block-0000"]


def test_graph_edge_synthesis():
    edge = GraphEdge(
        source="n1",
        target="n2",
        edge_type="related_to",
        summary="Synthesised connection.",
        provenance={"grounding": "synthesis"},
    )
    assert edge.provenance["grounding"] == "synthesis"
    assert edge.source_block_refs == []


def test_graph_node():
    node = GraphNode(node_id="n1", item_ref="item-0001", label="Key event")
    d = node.to_dict()
    assert d["label"] == "Key event"


# ── ExtractionUnitPackage ────────────────────────────────────────────────────


def test_package_schema_version():
    pkg = ExtractionUnitPackage(
        unit_id="unit-0001",
        source={"book_path": "test.txt"},
    )
    d = pkg.to_dict()
    assert d["schema_version"] == READING_UNIT_SCHEMA_VERSION
    assert d["schema_version"] == "reading-unit-v0.3"


def test_package_top_level_keys():
    pkg = ExtractionUnitPackage(
        unit_id="unit-0001",
        source={"book_path": "test.txt"},
    )
    d = pkg.to_dict()
    expected_keys = {
        "schema_version",
        "unit_id",
        "source",
        "source_blocks",
        "concepts",
        "atomic_items",
        "logical_groups",
        "unresolved_items",
        "validation",
        "context_metadata",
        "metrics",
    }
    assert set(d.keys()) == expected_keys


def test_package_rejects_stale_keys():
    d = ExtractionUnitPackage(unit_id="unit-0001", source={}).to_dict()
    for stale in ("source_spans", "concept_mentions", "links", "derived_views",
                   "timelines", "entity_records", "location_records", "atom_records", "thread_records"):
        assert stale not in d


def test_package_with_all_filled():
    blk = SourceBlock(
        block_id="seg-0001-block-0000",
        unit_id="unit-0001",
        segment_id="seg-0001",
        block_index=0,
        block_type="paragraph",
        start=0,
        end=10,
        text="Full text.",
        text_hash="abc",
    )
    concept = Concept(
        concept_id="concept-0001",
        surface="Alice",
        concept_type="person",
        source_block_refs=["seg-0001-block-0000"],
    )
    item = AtomicItem(
        item_id="item-0001",
        item_type="event",
        summary="Something happened.",
        source_block_refs=["seg-0001-block-0000"],
        concept_refs=["concept-0001"],
    )
    group = LogicalGroup(
        group_id="group-0001",
        group_type="timeline",
        summary="Events over time.",
        item_refs=["item-0001"],
    )
    pkg = ExtractionUnitPackage(
        unit_id="unit-0001",
        source={"path": "test.txt"},
        source_blocks=[blk],
        concepts=[concept],
        atomic_items=[item],
        logical_groups=[group],
    )
    d = pkg.to_dict()
    assert len(d["source_blocks"]) == 1
    assert len(d["concepts"]) == 1
    assert len(d["atomic_items"]) == 1
    assert len(d["logical_groups"]) == 1


# ── Document state ───────────────────────────────────────────────────────────


def test_document_state_snapshot():
    concept = Concept(concept_id="c-1", surface="X", concept_type="person")
    snap = DocumentStateSnapshot(
        document_id="doc-001",
        snapshot_id="snap-001",
        canonical_concepts=[concept],
        reusable_item_summaries=[{"item_id": "item-0001", "summary": "Summary"}],
    )
    d = snap.to_dict()
    assert d["document_id"] == "doc-001"
    assert len(d["canonical_concepts"]) == 1
    assert len(d["reusable_item_summaries"]) == 1


def test_registry_delta():
    delta = RegistryDelta(
        delta_id="delta-001",
        base_snapshot_id="snap-001",
        unit_id="unit-0002",
        operations=[
            {
                "operation_type": "new_concept",
                "payload": {"concept_id": "concept-0002"},
                "provenance": {"grounding": "source_grounded"},
            }
        ],
    )
    d = delta.to_dict()
    assert d["schema_version"] == "registry-delta-v0.1"
    assert d["base_snapshot_id"] == "snap-001"
    assert len(d["operations"]) == 1
    assert d["operations"][0]["operation_type"] == "new_concept"


# ── Serialisation round-trips ────────────────────────────────────────────────


def test_package_to_dict_round_trip_is_stable():
    pkg = ExtractionUnitPackage(
        unit_id="unit-0001",
        source={"book_path": "test.txt"},
        source_blocks=[
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
            )
        ],
        concepts=[Concept(concept_id="c-1", surface="H", concept_type="other")],
        atomic_items=[
            AtomicItem(item_id="i-1", item_type="event", summary="X happened.")
        ],
        logical_groups=[
            LogicalGroup(group_id="g-1", group_type="other", summary="A group")
        ],
    )
    d1 = pkg.to_dict()
    d2 = pkg.to_dict()
    assert d1 == d2
