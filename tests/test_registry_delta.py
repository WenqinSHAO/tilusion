from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tilusion.book_registry import BookRegistry
from tilusion.reading_schema import Concept
from tilusion.registry_delta import (
    RegistryDeltaResult,
    apply_registry_delta,
    compute_registry_delta,
)


def _make_registry(tmp_path: Path) -> BookRegistry:
    book_path = tmp_path / "test.txt"
    book_path.write_text("test")
    return BookRegistry(book_path, cache_root=tmp_path / "cache")


def _make_concept_dict(
    concept_id: str,
    surface: str,
    concept_type: str = "person",
    *,
    canonical_name: str | None = None,
    summary: str = "",
    **kwargs,
) -> dict:
    return {
        "concept_id": concept_id,
        "surface": surface,
        "concept_type": concept_type,
        "canonical_name": canonical_name,
        "summary": summary,
        "source_block_refs": kwargs.get("source_block_refs", []),
        "aliases": kwargs.get("aliases", []),
        "observed_surfaces": kwargs.get("observed_surfaces", [surface]),
        "facets": kwargs.get("facets", []),
        "uncertainty": kwargs.get("uncertainty", []),
        "provenance": kwargs.get("provenance", {"grounding": "source_grounded", "created_by": "llm_inferred"}),
    }


class TestComputeRegistryDelta:
    def test_empty_registry_all_new(self, tmp_path: Path) -> None:
        reg = _make_registry(tmp_path)
        unit_data = {
            "concepts": [
                _make_concept_dict("c1", "Confucius", canonical_name="Confucius", summary="A philosopher"),
                _make_concept_dict("c2", "Mencius", canonical_name="Mencius", summary="Another philosopher"),
            ],
            "atomic_items": [],
            "logical_groups": [],
            "unresolved_items": [],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0001")
        assert delta.stats == {"add_concept": 2}
        assert len(delta.operations) == 2
        assert all(op["op_type"] == "add_concept" for op in delta.operations)
        assert len(delta.ambiguity_items) == 0

    def test_exact_match_merge(self, tmp_path: Path) -> None:
        reg = _make_registry(tmp_path)
        # Pre-populate registry with Confucius
        existing = Concept(
            concept_id="",
            surface="Confucius",
            concept_type="person",
            canonical_name="Confucius",
            summary="A Chinese philosopher",
            observed_surfaces=["Confucius"],
            provenance={"source_unit": "unit-0001"},
        )
        cid, _ = reg.add_concept(existing)

        unit_data = {
            "concepts": [
                _make_concept_dict("c1", "Confucius", canonical_name="Confucius",
                                   summary="Edited the Annals"),
            ],
            "atomic_items": [],
            "logical_groups": [],
            "unresolved_items": [],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0002")
        assert delta.stats == {"merge_concepts": 1}
        assert delta.operations[0]["op_type"] == "merge_concepts"
        assert delta.operations[0]["book_concept_id"] == cid
        assert delta.id_remap["c1"] == cid

    def test_surface_match_ambiguity(self, tmp_path: Path) -> None:
        reg = _make_registry(tmp_path)
        existing = Concept(
            concept_id="",
            surface="余",
            concept_type="person",
            canonical_name="",  # no canonical_name → can't exact_match
            observed_surfaces=["余"],
            provenance={"source_unit": "unit-0001"},
        )
        reg.add_concept(existing)

        unit_data = {
            "concepts": [
                _make_concept_dict("c1", "余", canonical_name="沈复",
                                   summary="The narrator"),
            ],
            "atomic_items": [],
            "logical_groups": [],
            "unresolved_items": [],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0002")
        assert delta.stats == {"ambiguity_item": 1}
        assert len(delta.operations) == 0
        assert len(delta.ambiguity_items) == 1
        assert delta.ambiguity_items[0]["match_reason"] == "surface_match"

    def test_alias_match_ambiguity(self, tmp_path: Path) -> None:
        reg = _make_registry(tmp_path)
        existing = Concept(
            concept_id="",
            surface="Kongzi",
            concept_type="person",
            canonical_name="Confucius",
            observed_surfaces=["Kongzi"],
            provenance={"source_unit": "unit-0001"},
        )
        reg.add_concept(existing)

        unit_data = {
            "concepts": [
                _make_concept_dict("c1", "Confucius", canonical_name="Confucius",
                                   summary="A sage"),
            ],
            "atomic_items": [],
            "logical_groups": [],
            "unresolved_items": [],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0002")
        # c1 surface=Confucius type=person vs existing surface=Kongzi type=person
        # surface_type_index key: (Confucius, person) != (Kongzi, person) → no surface match
        # canonical_name_index: Confucius matches → alias_match (cname matched but surface different)
        assert delta.stats == {"ambiguity_item": 1}
        assert delta.ambiguity_items[0]["match_reason"] == "alias_match"

    def test_no_collision_different_type(self, tmp_path: Path) -> None:
        reg = _make_registry(tmp_path)
        existing = Concept(
            concept_id="",
            surface="Confucius",
            concept_type="person",
            canonical_name="",
            observed_surfaces=["Confucius"],
            provenance={"source_unit": "unit-0001"},
        )
        reg.add_concept(existing)

        # Same surface but different type, no canonical_name overlap → no collision
        unit_data = {
            "concepts": [
                _make_concept_dict("c1", "Confucius", concept_type="place",
                                   summary="A location"),
            ],
            "atomic_items": [],
            "logical_groups": [],
            "unresolved_items": [],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0002")
        # Same surface ("Confucius") but different normalized type (person vs place)
        # → key in surface_type_index is (Confucius, person) vs (Confucius, place)
        # → no surface_type_index match. No canonical_name on either → no cname match
        # → no collision → add_concept
        assert delta.stats == {"add_concept": 1}

    def test_unresolved_items_carried_forward(self, tmp_path: Path) -> None:
        reg = _make_registry(tmp_path)
        unit_data = {
            "concepts": [],
            "atomic_items": [],
            "logical_groups": [],
            "unresolved_items": [
                {"item_id": "unresolved-0001", "kind": "ambiguous_concept_surface",
                 "summary": "Foo might be person or place"},
            ],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0001")
        assert len(delta.ambiguity_items) == 1
        assert delta.ambiguity_items[0]["source"] == "unit_unresolved"
        assert delta.ambiguity_items[0]["unit_id"] == "unit-0001"

    def test_unit_local_id_distinction(self, tmp_path: Path) -> None:
        """Unit 1 concept-0001 ≠ Unit 2 concept-0001 if identities differ."""
        reg = _make_registry(tmp_path)
        # Unit 1 adds Confucius
        existing = Concept(
            concept_id="",
            surface="Confucius",
            concept_type="person",
            canonical_name="Confucius",
            observed_surfaces=["Confucius"],
            provenance={"source_unit": "unit-0001"},
        )
        reg.add_concept(existing)

        # Unit 2 extracts concept-0001 as Mencius (same local ID, different identity)
        unit_data = {
            "concepts": [
                _make_concept_dict("concept-0001", "Mencius", canonical_name="Mencius",
                                   summary="Confucian thinker"),
            ],
            "atomic_items": [],
            "logical_groups": [],
            "unresolved_items": [],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0002")
        # Mencius != Confucius → no collision → add_concept
        assert delta.stats == {"add_concept": 1}

    def test_source_unit_injected(self, tmp_path: Path) -> None:
        """Verify source_unit is injected into provenance before collision check."""
        reg = _make_registry(tmp_path)
        unit_data = {
            "concepts": [
                _make_concept_dict("c1", "NewEntity", summary="A new concept"),
            ],
            "atomic_items": [],
            "logical_groups": [],
            "unresolved_items": [],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0005")
        assert delta.stats == {"add_concept": 1}
        # After applying, check the concept in registry has source_unit
        apply_registry_delta(reg, delta)
        for c in reg._concepts.values():
            assert c.provenance.get("source_unit") == "unit-0005"

    def test_items_and_groups_in_delta(self, tmp_path: Path) -> None:
        reg = _make_registry(tmp_path)
        unit_data = {
            "concepts": [
                _make_concept_dict("c1", "Confucius", canonical_name="Confucius"),
            ],
            "atomic_items": [
                {
                    "item_id": "item-0001",
                    "item_type": "observation",
                    "summary": "Confucius taught students",
                    "source_block_refs": ["seg-0001-block-0001"],
                    "concept_refs": ["c1"],
                    "temporal_attributes": [],
                    "attributes": {},
                    "uncertainty": [],
                    "provenance": {"grounding": "source_grounded"},
                }
            ],
            "logical_groups": [
                {
                    "group_id": "group-0001",
                    "group_type": "theme_set",
                    "summary": "Teachings of Confucius",
                    "item_refs": ["item-0001"],
                    "concept_refs": ["c1"],
                    "graph": {},
                    "uncertainty": [],
                    "provenance": {"grounding": "synthesis"},
                }
            ],
            "unresolved_items": [],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0001")
        assert "add_concept" in delta.stats
        assert "add_item" in delta.stats
        assert "add_group" in delta.stats


class TestApplyRegistryDelta:
    def test_apply_add_concept(self, tmp_path: Path) -> None:
        reg = _make_registry(tmp_path)
        unit_data = {
            "concepts": [
                _make_concept_dict("c1", "Confucius", canonical_name="Confucius", summary="A philosopher"),
                _make_concept_dict("c2", "Mencius", canonical_name="Mencius", summary="Another"),
            ],
            "atomic_items": [],
            "logical_groups": [],
            "unresolved_items": [],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0001")
        applied = apply_registry_delta(reg, delta)
        assert len(applied) == 2
        assert reg._next_concept_id == 3  # started at 1, allocated 2
        assert reg.has_concepts()
        assert reg.get_concept(applied[0]) is not None

    def test_apply_merge_concepts(self, tmp_path: Path) -> None:
        reg = _make_registry(tmp_path)
        # Pre-populate
        existing = Concept(
            concept_id="",
            surface="Confucius",
            concept_type="person",
            canonical_name="Confucius",
            summary="A sage from Lu",
            observed_surfaces=["Confucius"],
            provenance={"source_unit": "unit-0001"},
        )
        cid, _ = reg.add_concept(existing)

        # Unit 2 has same Confucius → merge
        unit_data = {
            "concepts": [
                _make_concept_dict("c1", "Confucius", canonical_name="Confucius",
                                   summary="Edited the Spring and Autumn Annals"),
            ],
            "atomic_items": [],
            "logical_groups": [],
            "unresolved_items": [],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0002")
        applied = apply_registry_delta(reg, delta)
        assert len(applied) == 1
        merged = reg.get_concept(applied[0])
        assert merged is not None
        # Summary should be concatenated with source_unit prefixes
        assert "[unit-0001]" in merged.summary
        assert "[unit-0002]" in merged.summary
        assert "A sage from Lu" in merged.summary
        assert "Spring and Autumn Annals" in merged.summary

    def test_apply_item_remaps_concept_refs(self, tmp_path: Path) -> None:
        reg = _make_registry(tmp_path)
        unit_data = {
            "concepts": [
                _make_concept_dict("c1", "Confucius", canonical_name="Confucius"),
            ],
            "atomic_items": [
                {
                    "item_id": "item-0001",
                    "item_type": "observation",
                    "summary": "Confucius taught",
                    "source_block_refs": ["seg-0001-block-0001"],
                    "concept_refs": ["c1"],  # unit-local → should be remapped
                    "temporal_attributes": [],
                    "attributes": {},
                    "uncertainty": [],
                    "provenance": {"grounding": "source_grounded"},
                }
            ],
            "logical_groups": [],
            "unresolved_items": [],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0001")
        apply_registry_delta(reg, delta)

        # Find the item and check its concept_refs are book-scope
        for item_dict in reg._items.values():
            refs = item_dict.get("concept_refs", [])
            assert all(ref.startswith("concept-") for ref in refs)
            assert "c1" not in refs  # unit-local ID should be remapped

    def test_round_trip_extract_delta_apply(self, tmp_path: Path) -> None:
        """Full round-trip: extract → compute delta → apply → verify registry."""
        reg = _make_registry(tmp_path)
        unit_data = {
            "concepts": [
                _make_concept_dict("c1", "Alpha", canonical_name="Alpha", summary="First concept"),
                _make_concept_dict("c2", "Beta", canonical_name="Beta", summary="Second concept"),
            ],
            "atomic_items": [
                {
                    "item_id": "item-0001",
                    "item_type": "observation",
                    "summary": "Alpha and Beta discussed",
                    "source_block_refs": ["seg-0001-block-0001"],
                    "concept_refs": ["c1", "c2"],
                    "temporal_attributes": [],
                    "attributes": {},
                    "uncertainty": [],
                    "provenance": {"grounding": "source_grounded"},
                }
            ],
            "logical_groups": [],
            "unresolved_items": [],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0001")
        apply_registry_delta(reg, delta)

        # Verify registry state
        assert len(reg._concepts) == 2
        assert len(reg._items) == 1
        # Verify index integrity
        for cid, c in reg._concepts.items():
            normalized = c.concept_type  # already normalized via _dict_to_concept
            # surface_type_index should have this entry
            st_entries = reg._surface_type_index.get((c.surface, normalized), [])
            assert cid in st_entries

    def test_apply_with_zero_concepts(self, tmp_path: Path) -> None:
        """Unit with zero concepts should not crash."""
        reg = _make_registry(tmp_path)
        unit_data = {
            "concepts": [],
            "atomic_items": [],
            "logical_groups": [],
            "unresolved_items": [],
        }
        delta = compute_registry_delta(unit_data, reg, unit_id="unit-0001")
        assert delta.stats == {}
        applied = apply_registry_delta(reg, delta)
        assert applied == []
