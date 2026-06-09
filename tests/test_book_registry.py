from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from tilusion.book_registry import (
    BookRegistry,
    CollisionInfo,
    DeterministicConceptMerger,
    KeepExistingConceptMerger,
    MergeRejectedError,
    find_registry_duplicates,
)
from tilusion.reading_payloads import _merge_concept_group, _pick_canonical_name
from tilusion.reading_schema import (
    AtomicItem,
    Concept,
    LogicalGroup,
    normalize_concept_type,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _dict_to_concept(d: dict) -> Concept:
    return Concept(
        concept_id=d.get("concept_id", ""),
        surface=d.get("surface", ""),
        concept_type=d.get("concept_type", ""),
        canonical_name=d.get("canonical_name") or None,
        summary=d.get("summary", ""),
        aliases=list(d.get("aliases", [])),
        observed_surfaces=list(d.get("observed_surfaces", [])),
        source_block_refs=list(d.get("source_block_refs", [])),
        facets=list(d.get("facets", [])),
        uncertainty=list(d.get("uncertainty", [])),
        provenance=dict(d.get("provenance", {})),
    )


def _make_registry(book_path: str | None = None) -> BookRegistry:
    if book_path is None:
        book_path = tempfile.mkdtemp(prefix="test_book_")
    cache_root = tempfile.mkdtemp(prefix="test_cache_")
    return BookRegistry(book_path, cache_root=cache_root)


def _cleanup(reg: BookRegistry) -> None:
    shutil.rmtree(str(reg._cache_dir), ignore_errors=True)
    # book_path is a temp dir, clean it too if it looks like one
    bp = str(reg._book_path)
    if "/test_book_" in bp or "/tmp/" in bp:
        shutil.rmtree(bp, ignore_errors=True)


# ── DeterministicConceptMerger tests ───────────────────────────────────────


class TestDeterministicConceptMerger:
    def test_merge_single_returns_same(self) -> None:
        c = Concept(
            concept_id="concept-0001", surface="Confucius",
            concept_type="person", canonical_name="Confucius",
            summary="A philosopher",
        )
        result = DeterministicConceptMerger.merge([c])
        assert result.concept_id == "concept-0001"
        assert result.surface == "Confucius"

    def test_merge_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            DeterministicConceptMerger.merge([])

    def test_first_write_wins_canonical_name(self) -> None:
        c1 = Concept(
            concept_id="c1", surface="X", concept_type="person",
            canonical_name="Stable",
        )
        c2 = Concept(
            concept_id="c2", surface="X", concept_type="person",
            canonical_name="Overwrite",
        )
        result = DeterministicConceptMerger.merge([c1, c2])
        # First-write-wins: c1's cname preserved, c2's cname does not overwrite
        assert result.canonical_name == "Stable"
        assert result.surface == c1.surface

    def test_fallback_to_first_nonempty_canonical_name(self) -> None:
        c1 = Concept(
            concept_id="c1", surface="X", concept_type="person",
            canonical_name="",
        )
        c2 = Concept(
            concept_id="c2", surface="X", concept_type="person",
            canonical_name="Second",
        )
        result = DeterministicConceptMerger.merge([c1, c2])
        # First member has no cname → fallback to first non-empty
        assert result.canonical_name == "Second"

    def test_first_nonempty_summary(self) -> None:
        c1 = Concept(
            concept_id="c1", surface="X", concept_type="person",
            summary="",
        )
        c2 = Concept(
            concept_id="c2", surface="X", concept_type="person",
            summary="Second summary",
        )
        c3 = Concept(
            concept_id="c3", surface="X", concept_type="person",
            summary="Third summary",
        )
        result = DeterministicConceptMerger.merge([c1, c2, c3])
        assert result.summary == "Second summary"

    def test_union_of_list_fields(self) -> None:
        c1 = Concept(
            concept_id="c1", surface="X", concept_type="person",
            aliases=["a1"], facets=["f1"], source_block_refs=["b1"],
            observed_surfaces=["X"], uncertainty=["low"],
        )
        c2 = Concept(
            concept_id="c2", surface="X", concept_type="person",
            aliases=["a2"], facets=["f1", "f2"], source_block_refs=["b2"],
            observed_surfaces=["Y"], uncertainty=[],
        )
        result = DeterministicConceptMerger.merge([c1, c2])
        assert result.aliases == ["a1", "a2"]
        assert result.facets == ["f1", "f2"]
        assert set(result.source_block_refs) == {"b1", "b2"}
        assert set(result.observed_surfaces) == {"X", "Y"}

    def test_provenance_deterministic_when_groundings_agree(self) -> None:
        c1 = Concept(
            concept_id="c1", surface="X", concept_type="person",
            provenance={"grounding": "source_grounded"},
        )
        c2 = Concept(
            concept_id="c2", surface="X", concept_type="person",
            provenance={"grounding": "source_grounded"},
        )
        result = DeterministicConceptMerger.merge([c1, c2])
        assert result.provenance["grounding"] == "source_grounded"

    def test_provenance_synthesis_when_groundings_differ(self) -> None:
        c1 = Concept(
            concept_id="c1", surface="X", concept_type="person",
            provenance={"grounding": "source_grounded"},
        )
        c2 = Concept(
            concept_id="c2", surface="X", concept_type="person",
            provenance={"grounding": "llm_inferred"},
        )
        result = DeterministicConceptMerger.merge([c1, c2])
        assert result.provenance["grounding"] == "synthesis"

    def test_merged_from_populated(self) -> None:
        c1 = Concept(concept_id="c1", surface="X", concept_type="person")
        c2 = Concept(concept_id="c2", surface="X", concept_type="person")
        result = DeterministicConceptMerger.merge([c1, c2])
        assert result.provenance["merged_from"] == ["c1", "c2"]


# ── KeepExistingConceptMerger tests ────────────────────────────────────────


class TestKeepExistingConceptMerger:
    def test_returns_first(self) -> None:
        c1 = Concept(concept_id="c1", surface="A", concept_type="person")
        c2 = Concept(concept_id="c2", surface="B", concept_type="person")
        result = KeepExistingConceptMerger.merge([c1, c2])
        assert result.concept_id == "c1"


# ── Merge parity with _merge_concept_group ──────────────────────────────────


class TestMergeParity:
    """DeterministicConceptMerger produces identical output to
    _merge_concept_group for the same inputs."""

    def test_parity_basic_merge(self) -> None:
        members = [
            {
                "concept_id": "concept-0001", "surface": "余",
                "concept_type": "person", "summary": "narrator",
                "source_block_refs": ["b1"], "aliases": [],
                "observed_surfaces": ["余"], "facets": [],
                "uncertainty": [], "canonical_name": "",
            },
            {
                "concept_id": "concept-0002", "surface": "余",
                "concept_type": "person", "summary": "husband",
                "source_block_refs": ["b2"], "aliases": [],
                "observed_surfaces": ["余"], "facets": [],
                "uncertainty": [], "canonical_name": "沈复",
            },
        ]
        # Old way
        old_result = _merge_concept_group(
            "merged-1", "余", "person", members,
        )
        # New way
        concepts = [_dict_to_concept(m) for m in members]
        new_result = DeterministicConceptMerger.merge(concepts)

        # canonical_name: first member empty, fallback to "沈复" (matches old)
        assert new_result.canonical_name == old_result["canonical_name"]
        # surface: preserved from first member (registry-first in cross-unit).
        # Old behavior picked longest cname as surface; new preserves source form.
        assert new_result.surface == "余"
        # Summary concatenation: when all members have nonempty summaries,
        # DeterministicConceptMerger concatenates with source-unit prefix
        # rather than picking first-nonempty (which is what the old dict-based
        # _merge_concept_group does). Verify both original summaries appear.
        assert "narrator" in new_result.summary
        assert "husband" in new_result.summary
        assert new_result.provenance["merged_from"] == old_result["merged_from"]
        assert new_result.provenance["grounding"] == old_result["provenance"]["grounding"]

    def test_parity_canonical_name_merge(self) -> None:
        """Cross-surface merge via shared canonical_name."""
        members = [
            {
                "concept_id": "concept-0001", "surface": "相如",
                "concept_type": "person", "summary": "汉代辞赋家",
                "source_block_refs": ["b1"], "aliases": [],
                "observed_surfaces": ["相如"], "facets": [],
                "uncertainty": [], "canonical_name": "司马相如",
            },
            {
                "concept_id": "concept-0002", "surface": "长卿",
                "concept_type": "person", "summary": "字长卿",
                "source_block_refs": ["b2"], "aliases": ["司马长卿"],
                "observed_surfaces": ["长卿"], "facets": [],
                "uncertainty": [], "canonical_name": "司马相如",
            },
        ]
        old_result = _merge_concept_group(
            "merged-1", "相如", "person", members,
        )
        concepts = [_dict_to_concept(m) for m in members]
        new_result = DeterministicConceptMerger.merge(concepts)

        assert new_result.canonical_name == old_result["canonical_name"]
        assert new_result.aliases == old_result["aliases"]
        assert new_result.provenance["merged_from"] == old_result["merged_from"]


# ── BookRegistry CRUD tests ────────────────────────────────────────────────


class TestBookRegistryCRUD:
    @pytest.fixture(autouse=True)
    def _setup_teardown(self) -> None:
        self.reg = _make_registry()
        yield
        _cleanup(self.reg)

    def _add_person(self, surface: str, cname: str | None = None,
                    **kwargs) -> tuple[str, CollisionInfo | None]:
        return self.reg.add_concept(Concept(
            concept_id="", surface=surface, concept_type="person",
            canonical_name=cname, **kwargs,
        ))

    def test_add_and_get_concept(self) -> None:
        cid, collision = self._add_person("Confucius", "Confucius",
                                           summary="A philosopher")
        assert cid == "concept-0001"
        assert collision is None

        c = self.reg.get_concept(cid)
        assert c is not None
        assert c.surface == "Confucius"
        assert c.concept_type == "person"

    def test_add_concepts_batch(self) -> None:
        concepts = [
            Concept(concept_id="", surface=f"Entity_{i}",
                    concept_type="person")
            for i in range(3)
        ]
        results = self.reg.add_concepts(concepts)
        assert len(results) == 3
        assert [r[0] for r in results] == [
            "concept-0001", "concept-0002", "concept-0003",
        ]

    def test_get_nonexistent_concept(self) -> None:
        assert self.reg.get_concept("concept-9999") is None

    def test_collision_same_surface_and_type(self) -> None:
        self._add_person("Confucius", "Confucius")
        _, collision = self._add_person("Confucius", "Confucius")
        assert collision is not None
        assert collision.match_reason == "exact_match"
        assert collision.existing_concept_id == "concept-0001"

    def test_collision_same_cname_different_surface(self) -> None:
        self._add_person("Confucius", "Confucius")
        _, collision = self.reg.add_concept(Concept(
            concept_id="", surface="Kongzi", concept_type="person",
            canonical_name="Confucius",
        ))
        assert collision is not None
        assert collision.match_reason == "alias_match"

    def test_no_collision_different_everything(self) -> None:
        self._add_person("Confucius", "Confucius")
        _, collision = self._add_person("Mencius", "Mencius")
        assert collision is None

    def test_find_collisions_multiple_matches(self) -> None:
        self._add_person("Confucius", "Confucius")
        self.reg.add_concept(Concept(
            concept_id="", surface="Kongzi", concept_type="person",
            canonical_name="Confucius",
        ), force=True)

        collisions = self.reg.find_collisions(Concept(
            concept_id="", surface="Confucius", concept_type="person",
            canonical_name="Confucius",
        ))
        reasons = {c.match_reason for c in collisions}
        assert "exact_match" in reasons
        assert "alias_match" in reasons

    def test_get_by_surface(self) -> None:
        self._add_person("Confucius", "Confucius")
        results = self.reg.get_by_surface("Confucius")
        assert len(results) == 1
        assert results[0].surface == "Confucius"

    def test_get_by_canonical_name(self) -> None:
        self._add_person("Confucius", "Confucius")
        results = self.reg.get_by_canonical_name("Confucius")
        assert len(results) == 1

    def test_get_by_surface_nonexistent(self) -> None:
        assert self.reg.get_by_surface("Nobody") == []

    def test_get_by_canonical_name_nonexistent(self) -> None:
        assert self.reg.get_by_canonical_name("Nobody") == []


# ── BookRegistry merge tests ───────────────────────────────────────────────


class TestBookRegistryMerge:
    @pytest.fixture(autouse=True)
    def _setup_teardown(self) -> None:
        self.reg = _make_registry()
        yield
        _cleanup(self.reg)

    def _add(self, surface: str, cname: str | None = None,
              ctype: str = "person", **kwargs) -> str:
        cid, _ = self.reg.add_concept(Concept(
            concept_id="", surface=surface, concept_type=ctype,
            canonical_name=cname, **kwargs,
        ))
        return cid

    def _force_add(self, surface: str, cname: str | None = None,
                   ctype: str = "person", **kwargs) -> str:
        cid, _ = self.reg.add_concept(Concept(
            concept_id="", surface=surface, concept_type=ctype,
            canonical_name=cname, **kwargs,
        ), force=True)
        return cid

    def test_merge_with_shared_canonical_name(self) -> None:
        cid1 = self._add("Confucius", "Confucius")
        cid2 = self._force_add("Kongzi", "Confucius")
        merged_id = self.reg.merge_concepts([cid1, cid2])
        # First ID is the stable target (book concept) — preserved
        assert merged_id == cid1
        # Source ID is absorbed and removed
        assert self.reg.get_concept(cid2) is None
        merged = self.reg.get_concept(merged_id)
        assert merged is not None
        assert merged.canonical_name == "Confucius"

    def test_merge_with_same_surface(self) -> None:
        cid1 = self._add("Confucius", "Confucius")
        cid2 = self._force_add("Confucius", "")  # same surface, no cname
        merged_id = self.reg.merge_concepts([cid1, cid2])
        merged = self.reg.get_concept(merged_id)
        assert merged is not None
        assert merged.surface == "Confucius"

    def test_merge_rejects_distinct_places(self) -> None:
        cid1 = self._add("Beijing", ctype="place")
        cid2 = self._force_add("Shanghai", ctype="place")
        with pytest.raises(MergeRejectedError, match="place"):
            self.reg.merge_concepts([cid1, cid2])

    def test_merge_rejects_distinct_time_anchors(self) -> None:
        cid1 = self._add("2020", ctype="time_anchor")
        cid2 = self._force_add("2021", ctype="time_anchor")
        with pytest.raises(MergeRejectedError, match="time_anchor"):
            self.reg.merge_concepts([cid1, cid2])

    def test_merge_rejects_different_types_no_shared_cname(self) -> None:
        cid1 = self._add("Confucius", "Confucius", ctype="person")
        cid2 = self._force_add("Confucianism", ctype="theme")
        with pytest.raises(MergeRejectedError):
            self.reg.merge_concepts([cid1, cid2])

    def test_merge_rejects_generic_alias_as_only_identity_signal(self) -> None:
        cid1 = self._add("沈复", ctype="person", aliases=["余"])
        cid2 = self._force_add("某友", ctype="person", aliases=["余"])
        with pytest.raises(MergeRejectedError, match="identity signal"):
            self.reg.merge_concepts([cid1, cid2])

    def test_merge_requires_two_distinct_ids(self) -> None:
        cid1 = self._add("Confucius", "Confucius")
        with pytest.raises(ValueError, match="distinct"):
            self.reg.merge_concepts([cid1, cid1])

    def test_merge_preserves_indices(self) -> None:
        cid1 = self._add("Confucius", "Confucius")
        cid2 = self._force_add("Kongzi", "Confucius")
        merged_id = self.reg.merge_concepts([cid1, cid2])

        # Target ID (first arg) stays in indices; source IDs are removed
        key = ("Confucius", normalize_concept_type("person"))
        assert merged_id in self.reg._surface_type_index.get(key, [])
        assert cid2 not in self.reg._canonical_name_index.get("Confucius", set())
        assert merged_id in self.reg._canonical_name_index.get("Confucius", set())


# ── Item and Group tests ────────────────────────────────────────────────────


class TestItemsAndGroups:
    @pytest.fixture(autouse=True)
    def _setup_teardown(self) -> None:
        self.reg = _make_registry()
        yield
        _cleanup(self.reg)

    def test_add_and_get_item(self) -> None:
        item = AtomicItem(
            item_id="", item_type="event", summary="A thing happened",
            concept_refs=["concept-0001"],
        )
        iid = self.reg.add_item(item)
        assert iid == "item-0001"
        stored = self.reg.get_item(iid)
        assert stored is not None
        assert stored["item_type"] == "event"
        assert stored["summary"] == "A thing happened"

    def test_get_nonexistent_item(self) -> None:
        assert self.reg.get_item("item-9999") is None

    def test_add_and_get_group(self) -> None:
        group = LogicalGroup(
            group_id="", group_type="theme", summary="A theme group",
        )
        gid = self.reg.add_group(group)
        assert gid == "group-0001"
        stored = self.reg.get_group(gid)
        assert stored is not None
        assert stored["group_type"] == "theme"

    def test_get_nonexistent_group(self) -> None:
        assert self.reg.get_group("group-9999") is None

    def test_sequential_ids(self) -> None:
        iid1 = self.reg.add_item(AtomicItem(
            item_id="", item_type="event", summary="First"))
        iid2 = self.reg.add_item(AtomicItem(
            item_id="", item_type="event", summary="Second"))
        assert iid1 == "item-0001"
        assert iid2 == "item-0002"


# ── Persistence tests ──────────────────────────────────────────────────────


class TestPersistence:
    def test_save_and_load_round_trip(self) -> None:
        reg = _make_registry()
        try:
            cid, _ = reg.add_concept(Concept(
                concept_id="", surface="Confucius", concept_type="person",
                canonical_name="Confucius", summary="A philosopher",
                aliases=["Kong Qiu"],
            ))
            reg.add_item(AtomicItem(
                item_id="", item_type="event", summary="Born",
                concept_refs=[cid],
            ))
            reg.add_group(LogicalGroup(
                group_id="", group_type="theme",
                summary="Chinese philosophy",
            ))

            reg.save()
            cache_root = reg._cache_dir.parent
            loaded = BookRegistry.load(
                reg._book_path, cache_root=cache_root,
            )

            assert loaded.get_concept(cid) is not None
            assert loaded.get_concept(cid).summary == "A philosopher"
            assert loaded.get_concept(cid).aliases == ["Kong Qiu"]
            assert loaded.get_item("item-0001") is not None
            assert loaded.get_group("group-0001") is not None
            assert loaded._next_concept_id == reg._next_concept_id
        finally:
            _cleanup(reg)

    def test_save_multiple_times(self) -> None:
        reg = _make_registry()
        try:
            reg.add_concept(Concept(
                concept_id="", surface="First", concept_type="person"))
            h1 = reg.save()
            assert h1

            reg.add_concept(Concept(
                concept_id="", surface="Second", concept_type="person"))
            h2 = reg.save()
            assert h2
            assert h2 != h1
        finally:
            _cleanup(reg)

    def test_load_nonexistent_raises(self) -> None:
        with pytest.raises(FileNotFoundError, match="No registry found"):
            BookRegistry.load("/nonexistent/path", cache_root="/tmp/nonexistent")

    def test_rollback(self) -> None:
        reg = _make_registry()
        try:
            c1, _ = reg.add_concept(Concept(
                concept_id="", surface="Keep", concept_type="person"))
            reg.save()

            c2, _ = reg.add_concept(Concept(
                concept_id="", surface="Remove", concept_type="person"))
            reg.save()
            assert reg.get_concept(c2) is not None

            # Rollback to first save
            reg.rollback("HEAD~1")
            assert reg.get_concept(c1) is not None
            assert reg.get_concept(c2) is None
        finally:
            _cleanup(reg)

    def test_rollback_preserves_id_counters(self) -> None:
        reg = _make_registry()
        try:
            reg.add_concept(Concept(
                concept_id="", surface="A", concept_type="person"))
            reg.add_concept(Concept(
                concept_id="", surface="B", concept_type="person"))
            reg.save()
            assert reg._next_concept_id == 3

            reg.rollback("HEAD")
            assert reg._next_concept_id == 3
        finally:
            _cleanup(reg)


# ── Edge cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_force_add_bypasses_collision(self) -> None:
        reg = _make_registry()
        try:
            cid1, _ = reg.add_concept(Concept(
                concept_id="", surface="X", concept_type="person",
                canonical_name="X",
            ))
            cid2, _ = reg.add_concept(Concept(
                concept_id="", surface="X", concept_type="person",
                canonical_name="X",
            ), force=True)
            assert cid1 != cid2
            assert len(reg._concepts) == 2
        finally:
            _cleanup(reg)

    def test_merge_updates_surface_lookup(self) -> None:
        reg = _make_registry()
        try:
            cid1, _ = reg.add_concept(Concept(
                concept_id="", surface="Confucius", concept_type="person",
                canonical_name="Confucius", aliases=["Kong Qiu"],
            ))
            cid2, _ = reg.add_concept(Concept(
                concept_id="", surface="Kongzi", concept_type="person",
                canonical_name="Confucius",
            ), force=True)

            merged_id = reg.merge_concepts([cid1, cid2])
            # First ID is the stable target — preserved and re-indexed
            assert merged_id == cid1
            assert merged_id in reg._surface_lookup.get("Confucius", set())
            assert merged_id in reg._surface_lookup.get("Kong Qiu", set())
            # Source ID removed from surface lookup
            assert cid2 not in reg._surface_lookup.get("Confucius", set())
        finally:
            _cleanup(reg)

    def test_concept_type_normalization_in_index(self) -> None:
        reg = _make_registry()
        try:
            cid, _ = reg.add_concept(Concept(
                concept_id="", surface="X", concept_type="phenomenon",
            ))
            # "phenomenon" normalizes to "theme"
            key = ("X", "theme")
            assert cid in reg._surface_type_index.get(key, [])
        finally:
            _cleanup(reg)

    def test_merge_same_type_with_overlapping_surfaces(self) -> None:
        """Two concepts same type, no shared cname, but share an alias."""
        reg = _make_registry()
        try:
            cid1, _ = reg.add_concept(Concept(
                concept_id="", surface="Beijing", concept_type="place",
                aliases=["Peking"],
            ))
            cid2, _ = reg.add_concept(Concept(
                concept_id="", surface="Peking", concept_type="place",
            ), force=True)
            # Same type + surface overlap via alias 'Peking' → safe
            merged_id = reg.merge_concepts([cid1, cid2])
            assert merged_id is not None
        finally:
            _cleanup(reg)


# ── Load-or-init and introspection ──────────────────────────────────────────


class TestLoadOrInit:
    def test_load_or_init_creates_new(self, tmp_path: Path) -> None:
        book_path = tmp_path / "new_book.txt"
        book_path.write_text("test")
        cache_root = tmp_path / "cache"
        reg = BookRegistry.load_or_init(book_path, cache_root=cache_root)
        assert isinstance(reg, BookRegistry)
        assert not reg.has_concepts()

    def test_load_or_init_loads_existing(self, tmp_path: Path) -> None:
        book_path = tmp_path / "existing_book.txt"
        book_path.write_text("test")
        cache_root = tmp_path / "cache"

        reg1 = BookRegistry(book_path, cache_root=cache_root)
        reg1.add_concept(Concept(
            concept_id="", surface="Test", concept_type="other",
            observed_surfaces=["Test"],
        ))
        reg1.save()

        reg2 = BookRegistry.load_or_init(book_path, cache_root=cache_root)
        assert reg2.has_concepts()

    def test_has_concepts_empty(self, tmp_path: Path) -> None:
        book_path = tmp_path / "empty.txt"
        book_path.write_text("test")
        reg = BookRegistry(book_path, cache_root=tmp_path / "cache")
        assert not reg.has_concepts()

    def test_has_concepts_populated(self, tmp_path: Path) -> None:
        book_path = tmp_path / "populated.txt"
        book_path.write_text("test")
        reg = BookRegistry(book_path, cache_root=tmp_path / "cache")
        reg.add_concept(Concept(
            concept_id="", surface="X", concept_type="other",
            observed_surfaces=["X"],
        ))
        assert reg.has_concepts()


class TestSummaryConcatenation:
    def test_all_nonempty_summaries_concatenated(self) -> None:
        c1 = Concept(concept_id="c1", surface="A", concept_type="person",
                     summary="Summary 1", provenance={"source_unit": "unit-0001"})
        c2 = Concept(concept_id="c2", surface="A", concept_type="person",
                     summary="Summary 2", provenance={"source_unit": "unit-0003"})
        merged = DeterministicConceptMerger.merge([c1, c2])
        assert "[unit-0001]: Summary 1" in merged.summary
        assert "[unit-0003]: Summary 2" in merged.summary

    def test_one_empty_summary_falls_back_to_first_nonempty(self) -> None:
        c1 = Concept(concept_id="c1", surface="A", concept_type="person",
                     summary="", provenance={"source_unit": "unit-0001"})
        c2 = Concept(concept_id="c2", surface="A", concept_type="person",
                     summary="Only summary", provenance={"source_unit": "unit-0002"})
        merged = DeterministicConceptMerger.merge([c1, c2])
        assert merged.summary == "Only summary"

    def test_no_source_unit_uses_question_mark(self) -> None:
        c1 = Concept(concept_id="c1", surface="A", concept_type="person",
                     summary="Alpha", provenance={})
        c2 = Concept(concept_id="c2", surface="A", concept_type="person",
                     summary="Beta", provenance={})
        merged = DeterministicConceptMerger.merge([c1, c2])
        assert "[?]: Alpha" in merged.summary
        assert "[?]: Beta" in merged.summary


# ── Import sanity (verified at import time) ─────────────────────────────────

def test_book_registry_imports_do_not_reference_reading_pipeline() -> None:
    """book_registry.py must not import from reading_pipeline.py."""
    import inspect
    from tilusion import book_registry

    source = inspect.getsource(book_registry)
    # Check for actual import, not a comment mention
    import re
    import_lines = [
        line for line in source.splitlines()
        if re.match(r"^\s*(from|import)\s", line)
    ]
    for line in import_lines:
        assert "reading_pipeline" not in line, (
            f"book_registry.py imports from reading_pipeline: {line}"
        )


def test_registry_source_index_id_is_bound_once(tmp_path: Path) -> None:
    book_path = tmp_path / "book.txt"
    book_path.write_text("test", encoding="utf-8")
    reg = BookRegistry(book_path, cache_root=tmp_path / "cache")

    reg.ensure_source_index_id("source-index-a")
    reg.ensure_source_index_id("source-index-a")

    assert reg.source_index_id() == "source-index-a"
    with pytest.raises(ValueError, match="source_index_id mismatch"):
        reg.ensure_source_index_id("source-index-b")


def test_registry_source_index_id_persists(tmp_path: Path) -> None:
    book_path = tmp_path / "book.txt"
    book_path.write_text("test", encoding="utf-8")
    cache_root = tmp_path / "cache"
    reg = BookRegistry(book_path, cache_root=cache_root)
    reg.ensure_source_index_id("source-index-a")
    reg.save()

    loaded = BookRegistry.load(book_path, cache_root=cache_root)

    assert loaded.source_index_id() == "source-index-a"


# ── find_registry_duplicates ──────────────────────────────────────────────────


class TestFindRegistryDuplicates:
    def test_empty(self) -> None:
        assert find_registry_duplicates({}) == []

    def test_single_concept_no_duplicates(self) -> None:
        c = Concept(concept_id="concept-0001", surface="沈复", concept_type="person")
        assert find_registry_duplicates({"concept-0001": c}) == []

    def test_same_surface_same_type_merges(self) -> None:
        c1 = Concept(concept_id="concept-0001", surface="芸娘", concept_type="person",
                     canonical_name="芸娘")
        c2 = Concept(concept_id="concept-0032", surface="芸娘", concept_type="person",
                     canonical_name="芸娘")
        pairs = find_registry_duplicates({"concept-0001": c1, "concept-0032": c2})
        assert len(pairs) == 1
        assert pairs[0][0] == "concept-0001"
        assert pairs[0][1] == "concept-0032"
        assert "same surface" in pairs[0][2]

    def test_shared_alias_same_type_merges(self) -> None:
        c1 = Concept(concept_id="concept-0001", surface="沈复", concept_type="person",
                     canonical_name="沈复", aliases=["沈复", "三白", "余"])
        c2 = Concept(concept_id="concept-0031", surface="沈三白", concept_type="person",
                     canonical_name="沈三白", aliases=["沈复", "沈三白"])
        pairs = find_registry_duplicates({"concept-0001": c1, "concept-0031": c2})
        assert len(pairs) == 1
        assert pairs[0][0] == "concept-0001"
        assert pairs[0][1] == "concept-0031"
        assert "shared alias" in pairs[0][2]

    def test_shared_generic_alias_same_type_does_not_merge(self) -> None:
        c1 = Concept(concept_id="concept-0001", surface="沈复", concept_type="person",
                     aliases=["余"])
        c2 = Concept(concept_id="concept-0031", surface="某友", concept_type="person",
                     aliases=["余"])
        assert find_registry_duplicates({"concept-0001": c1, "concept-0031": c2}) == []

    def test_different_type_no_merge(self) -> None:
        c1 = Concept(concept_id="concept-0001", surface="沈复", concept_type="person")
        c2 = Concept(concept_id="concept-0002", surface="沈复", concept_type="place")
        assert find_registry_duplicates({"concept-0001": c1, "concept-0002": c2}) == []

    def test_different_surface_no_alias_no_merge(self) -> None:
        c1 = Concept(concept_id="concept-0001", surface="沈复", concept_type="person")
        c2 = Concept(concept_id="concept-0002", surface="先生", concept_type="person")
        assert find_registry_duplicates({"concept-0001": c1, "concept-0002": c2}) == []

    def test_older_absorbs_newer(self) -> None:
        c1 = Concept(concept_id="concept-0100", surface="芸娘", concept_type="person")
        c2 = Concept(concept_id="concept-0005", surface="芸娘", concept_type="person")
        pairs = find_registry_duplicates({"concept-0100": c1, "concept-0005": c2})
        assert len(pairs) == 1
        # Older (lower ID) absorbs newer
        assert pairs[0][0] == "concept-0005"
        assert pairs[0][1] == "concept-0100"

    def test_multiple_pairs(self) -> None:
        c1 = Concept(concept_id="concept-0001", surface="芸娘", concept_type="person")
        c2 = Concept(concept_id="concept-0002", surface="芸娘", concept_type="person")
        c3 = Concept(concept_id="concept-0003", surface="沈复", concept_type="person",
                     aliases=["三白"])
        c4 = Concept(concept_id="concept-0004", surface="沈三白", concept_type="person",
                     aliases=["沈复", "三白"])
        pairs = find_registry_duplicates(
            {"concept-0001": c1, "concept-0002": c2, "concept-0003": c3, "concept-0004": c4}
        )
        assert len(pairs) == 2
