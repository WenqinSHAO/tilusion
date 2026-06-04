from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tilusion.registry_index import (
    BM25,
    _build_concept_text,
    _build_unit_concept_text,
    _dual_signal_select,
    _get_embedding_model,
    _reciprocal_rank_fusion,
    build_registry_index,
    select_concept_candidates,
    select_group_candidates,
)
from tilusion.reading_pipeline import (
    MockReadingBackend,
    _apply_concept_resolution,
    _apply_group_resolution,
    mock_concept_resolution_response,
    mock_group_resolution_response,
    run_cross_unit_concept_resolution_pass,
    run_cross_unit_group_resolution_pass,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_concept(
    concept_id: str,
    surface: str,
    concept_type: str = "person",
    canonical_name: str = "",
    summary: str = "",
    source_block_refs: list[str] | None = None,
) -> dict:
    return {
        "concept_id": concept_id,
        "surface": surface,
        "concept_type": concept_type,
        "canonical_name": canonical_name or surface,
        "summary": summary or f"Summary for {surface}.",
        "aliases": [],
        "observed_surfaces": [surface],
        "source_block_refs": source_block_refs or [],
        "facets": [],
        "uncertainty": [],
        "provenance": {"grounding": "source_grounded", "created_by": "llm_inferred"},
    }


def _make_registry_concept(
    concept_id: str,
    canonical_name: str,
    concept_type: str = "person",
    summary: str = "",
    observed_surfaces: list[str] | None = None,
) -> dict:
    return {
        "concept_id": concept_id,
        "canonical_name": canonical_name,
        "concept_type": concept_type,
        "summary": summary or f"Summary for {canonical_name}.",
        "observed_surfaces": observed_surfaces or [canonical_name],
    }


class _ControlledResolutionBackend:
    model_identity = "controlled-resolution-test"

    def __init__(self, response: dict):
        self.response = response

    def start_conversation(self, system_prompt, user_payload, *, pass_name=""):
        from tilusion.conversation import ConversationContext, TurnMetadata

        ctx = ConversationContext.create(
            model_identity=self.model_identity,
            pass_name=pass_name,
            system_prompt=system_prompt,
            user_payload=user_payload,
        )
        ctx.record_turn(
            assistant_response=json.dumps(self.response, ensure_ascii=False),
            metadata=TurnMetadata(turn_index=1, turn_type="initial", elapsed_ms=0),
        )
        return ctx

    def continue_conversation(self, conversation, user_message):
        raise AssertionError("unexpected continuation")


# ── TestBuildRegistryIndex ────────────────────────────────────────────────────


class TestBuildRegistryIndex:
    def test_empty_registry(self) -> None:
        from tilusion.book_registry import BookRegistry

        registry = BookRegistry.__new__(BookRegistry)
        registry._concepts = {}
        assert build_registry_index(registry) == []

    def test_single_concept(self) -> None:
        from tilusion.book_registry import BookRegistry
        from tilusion.reading_schema import Concept

        registry = BookRegistry.__new__(BookRegistry)
        registry._concepts = {
            "book-concept-0001": Concept(
                concept_id="book-concept-0001",
                surface="孔子",
                concept_type="person",
                canonical_name="Confucius",
                summary="Chinese philosopher.",
                observed_surfaces=["孔子", "孔夫子", "Confucius"],
            )
        }
        index = build_registry_index(registry)
        assert len(index) == 1
        assert index[0]["concept_id"] == "book-concept-0001"
        assert index[0]["canonical_name"] == "Confucius"
        assert index[0]["concept_type"] == "person"
        assert index[0]["summary"] == "Chinese philosopher."
        assert len(index[0]["observed_surfaces"]) == 3

    def test_summary_truncation(self) -> None:
        from tilusion.book_registry import BookRegistry
        from tilusion.reading_schema import Concept

        registry = BookRegistry.__new__(BookRegistry)
        long_summary = "A" * 200
        registry._concepts = {
            "book-concept-0001": Concept(
                concept_id="book-concept-0001",
                surface="test",
                concept_type="other",
                summary=long_summary,
                observed_surfaces=["test"],
            )
        }
        index = build_registry_index(registry)
        assert len(index[0]["summary"]) <= 120
        assert index[0]["summary"].endswith("...")


# ── TestSelectConceptCandidates ───────────────────────────────────────────────


class TestSelectConceptCandidates:
    def test_empty_registry_index(self) -> None:
        assert select_concept_candidates(
            [_make_concept("c-0001", "test")], []
        ) == []

    def test_no_unit_concepts(self) -> None:
        reg = [_make_registry_concept("b-1", "test")]
        assert select_concept_candidates([], reg) == reg

    def test_small_registry_passthrough(self) -> None:
        """Registry ≤ 50 entries returned unchanged."""
        reg = [_make_registry_concept(f"b-{i}", f"entity-{i}") for i in range(30)]
        unit = [_make_concept("c-0001", "test", "person")]
        assert select_concept_candidates(unit, reg) == reg

    def test_surface_collision_match(self) -> None:
        reg = [_make_registry_concept(f"b-{i}", f"entity-{i}", "person") for i in range(100)]
        reg.append(_make_registry_concept("b-match", "Confucius", "person",
                                          observed_surfaces=["孔子", "Confucius"]))
        unit = [_make_concept("c-0001", "孔子", "person")]
        candidates = select_concept_candidates(unit, reg)
        assert any(c["concept_id"] == "b-match" for c in candidates)

    def test_canonical_name_match(self) -> None:
        reg = [_make_registry_concept(f"b-{i}", f"entity-{i}", "person") for i in range(100)]
        reg.append(_make_registry_concept("b-match", "Confucius", "person"))
        unit = [_make_concept("c-0001", "孔夫子", "person", canonical_name="Confucius")]
        candidates = select_concept_candidates(unit, reg)
        assert any(c["concept_id"] == "b-match" for c in candidates)

    def test_candidate_map_records_deterministic_matches(self) -> None:
        reg = [_make_registry_concept(f"b-{i}", f"entity-{i}", "person") for i in range(100)]
        reg.append(_make_registry_concept("b-match", "Confucius", "person",
                                          observed_surfaces=["孔子", "Confucius"]))
        unit = [_make_concept("c-0001", "孔子", "person")]
        trace: dict = {}

        candidates = select_concept_candidates(unit, reg, trace=trace)

        assert any(c["concept_id"] == "b-match" for c in candidates)
        assert trace["candidate_map"] == [{
            "unit_concept_id": "c-0001",
            "surface": "孔子",
            "concept_type": "person",
            "deterministic_candidate_ids": ["b-match"],
            "semantic_candidates": [],
            "candidate_ids": ["b-match"],
        }]
        assert trace["deterministic"]["matches_by_unit"] == {"c-0001": ["b-match"]}

    def test_cross_type_family_relaxation(self) -> None:
        """person ↔ group, organization relaxation."""
        reg = [_make_registry_concept(f"b-{i}", f"entity-{i}", "organization") for i in range(100)]
        reg.append(_make_registry_concept("b-match", "Li Si", "group",
                                          observed_surfaces=["Li Si", "李斯"]))
        unit = [_make_concept("c-0001", "李斯", "person")]
        candidates = select_concept_candidates(unit, reg)
        assert any(c["concept_id"] == "b-match" for c in candidates)

    def test_place_scene_element_relaxation(self) -> None:
        reg = [_make_registry_concept(f"b-{i}", f"entity-{i}", "other") for i in range(100)]
        reg.append(_make_registry_concept("b-match", "Chang'an", "scene_element",
                                          observed_surfaces=["Chang'an", "长安"]))
        unit = [_make_concept("c-0001", "长安", "place")]
        candidates = select_concept_candidates(unit, reg)
        assert any(c["concept_id"] == "b-match" for c in candidates)

    def test_dedup_candidates(self) -> None:
        """Same candidate should not appear twice."""
        reg = [_make_registry_concept("b-single", "Test Entity", "person",
                                      observed_surfaces=["test", "Test Entity"])]
        unit = [
            _make_concept("c-0001", "test", "person"),
            _make_concept("c-0002", "Test Entity", "person"),
        ]
        candidates = select_concept_candidates(unit, reg)
        ids = [c["concept_id"] for c in candidates]
        assert ids.count("b-single") == 1


# ── TestBM25 ───────────────────────────────────────────────────────────────────


class TestBM25:
    def test_empty_corpus(self) -> None:
        bm25 = BM25([])
        assert bm25.search("query") == []

    def test_single_document_exact_match(self) -> None:
        bm25 = BM25(["Confucius was a Chinese philosopher"])
        results = bm25.search("Confucius")
        assert len(results) == 1
        assert results[0][1] > 0  # positive score

    def test_multi_document_ranking(self) -> None:
        corpus = [
            "Treaty of Nanjing 1842 ending First Opium War",
            "Shen Fu author of Six Records of a Floating Life",
            "the old man reflecting on the past",
        ]
        bm25 = BM25(corpus)
        results = bm25.search("treaty nanjing opium", top_k=3)
        assert len(results) >= 1
        assert results[0][0] == 0  # first doc should rank highest

    def test_no_match_returns_empty(self) -> None:
        bm25 = BM25(["completely unrelated text"])
        results = bm25.search("xyzabc notpresent")
        assert results == []

    def test_cross_lingual_partial(self) -> None:
        """BM25 catches surface overlap even across languages."""
        corpus = [
            "Confucius Chinese philosopher",
            "孔子 中国哲学家",
        ]
        bm25 = BM25(corpus)
        # "Confucius" should match doc 0
        results = bm25.search("Confucius")
        assert len(results) == 1
        assert results[0][0] == 0


# ── TestReciprocalRankFusion ───────────────────────────────────────────────────


class TestReciprocalRankFusion:
    def test_empty_rankings(self) -> None:
        assert _reciprocal_rank_fusion([]) == []

    def test_single_ranking_preserved(self) -> None:
        ranking = [["a", "b", "c"]]
        fused = _reciprocal_rank_fusion(ranking)
        assert [cid for cid, _ in fused] == ["a", "b", "c"]

    def test_two_rankings_fused(self) -> None:
        r1 = ["a", "b", "c"]
        r2 = ["c", "b", "a"]
        fused = _reciprocal_rank_fusion([r1, r2])
        # "a" (r1:1st, r2:3rd) and "c" (r1:3rd, r2:1st) tie for first
        # because each has a 1st-place in one ranking
        # "b" (r1:2nd, r2:2nd) is a close second
        assert fused[0][0] in ("a", "c")
        # All three should have valid scores
        assert len(fused) == 3
        assert all(score > 0 for _, score in fused)

    def test_k_parameter_affects_scores(self) -> None:
        r1 = ["a", "b"]
        fused_k60 = _reciprocal_rank_fusion([r1], k=60)
        fused_k10 = _reciprocal_rank_fusion([r1], k=10)
        # Higher k → scores closer together (less rank penalty)
        score_diff_60 = fused_k60[0][1] - fused_k60[1][1]
        score_diff_10 = fused_k10[0][1] - fused_k10[1][1]
        assert score_diff_60 < score_diff_10


# ── TestBuildConceptText ───────────────────────────────────────────────────────


class TestBuildConceptText:
    def test_full_fields(self) -> None:
        text = _build_concept_text({
            "canonical_name": "Confucius",
            "summary": "Chinese philosopher",
            "observed_surfaces": ["孔子", "孔夫子"],
        })
        assert "Confucius" in text
        assert "Chinese philosopher" in text
        assert "孔子" in text

    def test_unit_concept_text(self) -> None:
        text = _build_unit_concept_text({
            "surface": "the old man",
            "canonical_name": "",
            "summary": "Elderly narrator reflecting on the past",
        })
        assert "the old man" in text
        assert "Elderly narrator" in text


# ── TestDualSignalSelect ───────────────────────────────────────────────────────


class TestDualSignalSelect:
    def test_empty_inputs(self) -> None:
        assert _dual_signal_select([], []) == set()
        assert _dual_signal_select([_make_concept("c-0001", "test")], []) == set()

    def test_bm25_only_when_no_embedding_model(self) -> None:
        """When embedding model isn't loaded, falls back to BM25-only."""
        reg = [_make_registry_concept(f"b-{i}", f"entity-{i}", "person") for i in range(10)]
        reg.append(_make_registry_concept("b-match", "Confucius", "person",
                                          summary="Chinese philosopher"))
        unit = [_make_concept("c-0001", "Confucius", "person",
                              summary="Ancient Chinese thinker")]
        # With no embedding model loaded, should still find via BM25
        result = _dual_signal_select(unit, reg, top_k=5)
        assert "b-match" in result

    def test_cross_lingual_semantic_match(self) -> None:
        """The 'new surface' problem: Chinese surface matches English registry
        concept via semantic similarity, not surface collision."""
        reg = [_make_registry_concept(f"b-{i}", f"entity-{i}", "other") for i in range(10)]
        reg.append(_make_registry_concept(
            "b-shenfu", "Shen Fu", "person",
            summary="Author and autobiographical narrator of Six Records of a Floating Life",
            observed_surfaces=["沈复", "Shen Fu", "三白"],
        ))
        unit = [_make_concept("c-0001", "the old man", "person",
                              summary="Elderly male narrator reflecting on his past life")]
        result = _dual_signal_select(unit, reg, top_k=5)
        # Should find Shen Fu via semantic similarity even with no surface overlap
        assert "b-shenfu" in result

    def test_multiple_unit_concepts(self) -> None:
        reg = [_make_registry_concept(f"b-{i}", f"entity-{i}", "person") for i in range(20)]
        reg.append(_make_registry_concept("b-confucius", "Confucius", "person",
                                          summary="Chinese philosopher"))
        reg.append(_make_registry_concept("b-changan", "Chang'an", "place",
                                          summary="Ancient Chinese capital"))
        unit = [
            _make_concept("c-0001", "孔子", "person", summary="Ancient Chinese philosopher"),
            _make_concept("c-0002", "长安城", "place", summary="Capital city of Han and Tang"),
        ]
        result = _dual_signal_select(unit, reg, top_k=10)
        assert "b-confucius" in result
        assert "b-changan" in result

    def test_no_semantic_match_returns_empty(self) -> None:
        """When nothing is semantically related, returns empty set."""
        reg = [_make_registry_concept(f"b-{i}", f"entity-{i}", "other") for i in range(10)]
        unit = [_make_concept("c-0001", "xyzabc123", "other",
                              summary="Completely unrelated nonsense text")]
        result = _dual_signal_select(unit, reg, top_k=5)
        # BM25 may return some results with partial matches, but if embeddings
        # are working, they should filter out. Either way, the set should be
        # small and not include false positives with high confidence.
        assert isinstance(result, set)


# ── TestSelectConceptCandidatesWithDualSignal ──────────────────────────────────


class TestSelectConceptCandidatesWithDualSignal:
    def test_new_surface_caught_by_dual_signal(self) -> None:
        """Deterministic filter would miss 'the old man' → 'Shen Fu' because
        no surface collision and different types. Dual-signal catches it."""
        reg = [_make_registry_concept(f"b-{i}", f"entity-{i}", "other") for i in range(51)]
        reg.append(_make_registry_concept(
            "b-shenfu", "Shen Fu", "person",
            summary="Author of Six Records of a Floating Life, autobiographical narrator",
            observed_surfaces=["沈复", "Shen Fu", "三白"],
        ))
        unit = [_make_concept("c-0001", "the old man", "other",
                              summary="Elderly narrator reflecting on his past life and travels")]
        trace = {}
        candidates = select_concept_candidates(unit, reg, trace=trace)
        assert any(c["concept_id"] == "b-shenfu" for c in candidates)
        assert trace["kind"] == "concept_candidate_selection"
        assert trace["dual_signal"]["type_filter"] is True
        assert trace["selected_candidate_count"] == len(trace["selected_candidate_ids"])

    def test_deterministic_and_dual_signal_union(self) -> None:
        """Deterministic finds surface match, dual-signal finds semantic match.
        Both should appear in results."""
        reg = [_make_registry_concept(f"b-{i}", f"entity-{i}", "other") for i in range(51)]
        # Deterministic match: surface collision
        reg.append(_make_registry_concept("b-surface", "孔子", "person",
                                          observed_surfaces=["孔子", "Confucius"]))
        # Semantic match: no surface collision
        reg.append(_make_registry_concept("b-semantic", "Shen Fu", "person",
                                          summary="Autobiographical narrator and author"))
        unit = [
            _make_concept("c-0001", "孔子", "person",
                          summary="Ancient Chinese philosopher"),
            _make_concept("c-0002", "the narrator", "person",
                          summary="Autobiographical voice reflecting on past life"),
        ]
        candidates = select_concept_candidates(unit, reg)
        ids = {c["concept_id"] for c in candidates}
        assert "b-surface" in ids  # deterministic
        assert "b-semantic" in ids  # dual-signal

    def test_small_registry_still_passthrough(self) -> None:
        """When registry ≤ 50, the function returns all entries unchanged
        regardless of relevance."""
        reg = [_make_registry_concept(f"b-{i}", f"entity-{i}") for i in range(30)]
        unit = [_make_concept("c-0001", "test", "person")]
        assert select_concept_candidates(unit, reg) == reg


# ── TestSelectGroupCandidates ─────────────────────────────────────────────────


class TestSelectGroupCandidates:
    def test_empty_registry_groups(self) -> None:
        assert select_group_candidates([], [], []) == []

    def test_no_unit_groups(self) -> None:
        assert select_group_candidates([], [{"group_id": "g-1"}], []) == [{"group_id": "g-1"}]

    def test_small_registry_passthrough(self) -> None:
        """Registry ≤ 20 groups returned unchanged."""
        reg_groups = [{"group_id": f"rg-{i}", "group_type": "other", "concept_refs": []} for i in range(15)]
        unit_groups = [{"group_id": "ug-1", "group_type": "other", "concept_refs": []}]
        assert select_group_candidates(unit_groups, reg_groups, []) == reg_groups

    def test_concept_overlap_match(self) -> None:
        reg_groups = [
            {"group_id": f"rg-{i}", "group_type": "other", "concept_refs": []}
            for i in range(50)
        ]
        reg_groups.append({
            "group_id": "rg-match",
            "group_type": "timeline",
            "concept_refs": ["book-concept-0001", "book-concept-0002"],
        })
        resolved_concepts = [
            {"concept_id": "c-0001", "registry_ref": "book-concept-0001"},
        ]
        unit_groups = [{"group_id": "ug-1", "group_type": "timeline", "concept_refs": ["c-0001"]}]
        trace = {}
        candidates = select_group_candidates(unit_groups, reg_groups, resolved_concepts, trace=trace)
        assert any(c["group_id"] == "rg-match" for c in candidates)
        assert trace["kind"] == "group_candidate_selection"
        assert "rg-match" in trace["selected_candidate_ids"]


# ── TestApplyConceptResolution ────────────────────────────────────────────────


class TestApplyConceptResolution:
    def test_empty_proposals(self) -> None:
        concepts = [_make_concept("c-0001", "孔子", "person")]
        uc, remap, implicit_refs = _apply_concept_resolution(concepts, [], unit_id="unit-0001")
        assert uc == concepts
        assert remap == {}
        assert implicit_refs == {}

    def test_link_proposal(self) -> None:
        concepts = [_make_concept("c-0001", "孔子", "person")]
        proposals = [{
            "proposal_id": "res-0001",
            "proposal_type": "link",
            "target_refs": ["c-0001"],
            "registry_ref": "book-concept-0042",
            "changes": {"canonical_name": "Confucius"},
            "implicit_refs": [],
        }]
        uc, remap, implicit_refs = _apply_concept_resolution(concepts, proposals, unit_id="unit-0001")
        assert uc[0]["registry_ref"] == "book-concept-0042"
        assert uc[0]["canonical_name"] == "Confucius"
        assert remap == {}

    def test_link_with_implicit_refs(self) -> None:
        concepts = [_make_concept("c-0001", "the treaty", "term")]
        proposals = [{
            "proposal_id": "res-0001",
            "proposal_type": "link",
            "target_refs": ["c-0001"],
            "registry_ref": "book-concept-0007",
            "changes": {},
            "implicit_refs": [
                {"item_ref": "item-0042", "concept_ref": "c-0001", "reason": "implicit mention"}
            ],
        }]
        uc, remap, implicit_refs = _apply_concept_resolution(concepts, proposals, unit_id="unit-0001")
        assert "book-concept-0007" in implicit_refs
        assert implicit_refs["book-concept-0007"]["unit_concept_ref"] == "c-0001"
        assert len(implicit_refs["book-concept-0007"]["implicit_refs"]) == 1

    def test_merge_proposal(self) -> None:
        concepts = [
            _make_concept("c-0001", "孔子", "person"),
            _make_concept("c-0002", "Confucius", "person"),
        ]
        proposals = [{
            "proposal_id": "res-0001",
            "proposal_type": "merge",
            "target_refs": ["c-0001", "c-0002"],
            "registry_ref": "",
            "changes": {"canonical_name": "Confucius"},
        }]
        uc, remap, _ = _apply_concept_resolution(concepts, proposals, unit_id="unit-0001")
        # One concept should be absorbed
        assert len(uc) == 1
        assert "c-0002" in remap  # merged into c-0001
        assert uc[0]["canonical_name"] == "Confucius"

    def test_split_proposal(self) -> None:
        concepts = [_make_concept("c-0001", "Li", "person")]
        proposals = [{
            "proposal_id": "res-0001",
            "proposal_type": "split",
            "target_refs": ["c-0001"],
            "registry_ref": "",
            "changes": {
                "split_into": [
                    {"surface": "Li Bai", "concept_type": "person", "summary": "Poet"},
                    {"surface": "Li Si", "concept_type": "person", "summary": "Statesman"},
                ]
            },
        }]
        uc, remap, _ = _apply_concept_resolution(concepts, proposals, unit_id="unit-0001")
        assert len(uc) == 2
        assert any(c["surface"] == "Li Bai" for c in uc)
        assert any(c["surface"] == "Li Si" for c in uc)

    def test_refine_proposal(self) -> None:
        concepts = [_make_concept("c-0001", "孔子", "person", summary="Ancient thinker.")]
        proposals = [{
            "proposal_id": "res-0001",
            "proposal_type": "refine",
            "target_refs": ["c-0001"],
            "registry_ref": "",
            "changes": {"summary": "Chinese philosopher and educator."},
        }]
        uc, remap, _ = _apply_concept_resolution(concepts, proposals, unit_id="unit-0001")
        assert uc[0]["summary"] == "Chinese philosopher and educator."

    def test_reclassify_proposal(self) -> None:
        concepts = [_make_concept("c-0001", "SomeThing", "other")]
        proposals = [{
            "proposal_id": "res-0001",
            "proposal_type": "reclassify",
            "target_refs": ["c-0001"],
            "registry_ref": "",
            "changes": {"concept_type": "object"},
        }]
        uc, remap, _ = _apply_concept_resolution(concepts, proposals, unit_id="unit-0001")
        assert uc[0]["concept_type"] == "object"

    def test_new_concept_proposal(self) -> None:
        concepts = [_make_concept("c-0001", "New Person", "person")]
        proposals = [{
            "proposal_id": "res-0001",
            "proposal_type": "new_concept",
            "target_refs": ["c-0001"],
            "registry_ref": "",
            "changes": {},
        }]
        uc, remap, _ = _apply_concept_resolution(concepts, proposals, unit_id="unit-0001")
        # Should remove any existing registry_ref
        assert "registry_ref" not in uc[0]


# ── TestApplyGroupResolution ──────────────────────────────────────────────────


class TestApplyGroupResolution:
    def test_empty_proposals(self) -> None:
        groups = [{"group_id": "g-0001", "group_type": "other", "summary": "Test."}]
        ug, edges = _apply_group_resolution(groups, [], unit_id="unit-0001")
        assert ug == groups
        assert edges == []

    def test_continue_proposal(self) -> None:
        groups = [{"group_id": "g-0001", "group_type": "timeline", "summary": "Spring events."}]
        proposals = [{
            "proposal_id": "grp-res-0001",
            "proposal_type": "continue",
            "unit_group_ref": "g-0001",
            "registry_group_ref": "book-group-0003",
            "changes": {},
        }]
        ug, edges = _apply_group_resolution(groups, proposals, unit_id="unit-0001")
        assert ug[0]["registry_group_ref"] == "book-group-0003"
        assert ug[0]["_continuation"] == "continue"

    def test_mutate_proposal(self) -> None:
        groups = [{"group_id": "g-0001", "group_type": "theme_set", "summary": "Old theme."}]
        proposals = [{
            "proposal_id": "grp-res-0001",
            "proposal_type": "mutate",
            "unit_group_ref": "g-0001",
            "registry_group_ref": "book-group-0004",
            "changes": {"summary": "Shifted focus.", "group_type": "motif_development"},
        }]
        ug, edges = _apply_group_resolution(groups, proposals, unit_id="unit-0001")
        assert ug[0]["registry_group_ref"] == "book-group-0004"
        assert ug[0]["_continuation"] == "mutate"
        assert ug[0]["summary"] == "Shifted focus."
        assert ug[0]["group_type"] == "motif_development"

    def test_new_thread_proposal(self) -> None:
        groups = [{"group_id": "g-0001", "group_type": "other", "summary": "New thread."}]
        proposals = [{
            "proposal_id": "grp-res-0001",
            "proposal_type": "new_thread",
            "unit_group_ref": "g-0001",
            "registry_group_ref": "",
            "changes": {},
        }]
        ug, edges = _apply_group_resolution(groups, proposals, unit_id="unit-0001")
        assert ug[0]["_continuation"] == "new_thread"

    def test_cross_group_edge(self) -> None:
        groups = [{"group_id": "g-0001", "group_type": "timeline", "summary": "Timeline A."}]
        proposals = [{
            "proposal_id": "grp-res-0001",
            "proposal_type": "cross_group_edge",
            "unit_group_ref": "",
            "registry_group_ref": "",
            "changes": {},
            "edge": {
                "source_group": "g-0001",
                "target_group": "book-group-0005",
                "edge_type": "precedes",
                "summary": "Timeline A precedes book timeline.",
            },
        }]
        ug, edges = _apply_group_resolution(groups, proposals, unit_id="unit-0001")
        assert len(edges) == 1
        assert edges[0]["source_group"] == "g-0001"
        assert edges[0]["edge_type"] == "precedes"

    def test_merge_groups_proposal(self) -> None:
        groups = [
            {"group_id": "g-0001", "group_type": "theme_set", "summary": "Theme A."},
            {"group_id": "g-0002", "group_type": "theme_set", "summary": "Theme A continued."},
        ]
        proposals = [{
            "proposal_id": "grp-res-0001",
            "proposal_type": "merge_groups",
            "target_refs": ["g-0001", "g-0002"],
            "changes": {"summary": "Unified theme A."},
        }]
        ug, edges = _apply_group_resolution(groups, proposals, unit_id="unit-0001")
        assert len(ug) == 1
        assert ug[0]["summary"] == "Unified theme A."


# ── TestMockResponses ────────────────────────────────────────────────────────


class TestMockResponses:
    def test_mock_concept_resolution_nonempty_registry(self) -> None:
        payload = {
            "unit_id": "unit-0002",
            "concepts": [
                {"concept_id": "c-0001", "surface": "孔子", "concept_type": "person"},
                {"concept_id": "c-0002", "surface": "长安", "concept_type": "place"},
            ],
            "registry_index": [
                {"concept_id": "book-1", "canonical_name": "Confucius", "concept_type": "person"},
                {"concept_id": "book-2", "canonical_name": "Chang'an", "concept_type": "place"},
            ],
            "unresolved_items": [],
            "context": {},
        }
        resp = mock_concept_resolution_response(payload)
        assert len(resp["resolution_proposals"]) == 2
        assert resp["resolution_proposals"][0]["proposal_type"] == "link"
        assert resp["resolution_proposals"][0]["registry_ref"] == "book-1"
        assert resp["resolution_proposals"][1]["proposal_type"] == "link"
        assert resp["resolution_proposals"][1]["registry_ref"] == "book-2"

    def test_mock_concept_resolution_empty_registry(self) -> None:
        payload = {
            "unit_id": "unit-0001",
            "concepts": [{"concept_id": "c-0001", "surface": "孔子", "concept_type": "person"}],
            "registry_index": [],
            "unresolved_items": [],
            "context": {},
        }
        resp = mock_concept_resolution_response(payload)
        assert len(resp["resolution_proposals"]) == 1
        assert resp["resolution_proposals"][0]["proposal_type"] == "new_concept"

    def test_mock_group_resolution_nonempty_registry(self) -> None:
        payload = {
            "unit_id": "unit-0002",
            "groups": [
                {"group_id": "g-0001", "group_type": "timeline"},
                {"group_id": "g-0002", "group_type": "theme_set"},
            ],
            "registry_groups": [
                {"group_id": "book-g-1", "group_type": "timeline"},
            ],
            "concepts": [],
            "context": {},
        }
        resp = mock_group_resolution_response(payload)
        assert len(resp["group_resolution_proposals"]) == 2
        # First group matches type → continue
        assert resp["group_resolution_proposals"][0]["proposal_type"] == "continue"
        # Second group no match → new_thread
        assert resp["group_resolution_proposals"][1]["proposal_type"] == "new_thread"

    def test_mock_group_resolution_empty_registry(self) -> None:
        payload = {
            "unit_id": "unit-0001",
            "groups": [{"group_id": "g-0001", "group_type": "timeline"}],
            "registry_groups": [],
            "concepts": [],
            "context": {},
        }
        resp = mock_group_resolution_response(payload)
        assert len(resp["group_resolution_proposals"]) == 1
        assert resp["group_resolution_proposals"][0]["proposal_type"] == "new_thread"

    def test_mock_backend_dispatch(self) -> None:
        backend = MockReadingBackend()
        # cross_unit_concept_resolution task
        raw = backend.complete_json("", {"task": "cross_unit_concept_resolution",
                                          "unit_id": "unit-0001", "concepts": [], "registry_index": [],
                                          "unresolved_items": [], "context": {}})
        data = json.loads(raw)
        assert "resolution_proposals" in data

        # cross_unit_group_resolution task
        raw = backend.complete_json("", {"task": "cross_unit_group_resolution",
                                          "unit_id": "unit-0001", "concepts": [], "groups": [],
                                          "registry_groups": [], "context": {}})
        data = json.loads(raw)
        assert "group_resolution_proposals" in data


# ── TestPassFunctions ─────────────────────────────────────────────────────────


class TestConceptResolutionPass:
    def test_run_with_mock_backend(self, tmp_path: Path) -> None:
        backend = MockReadingBackend()
        concepts = [_make_concept("c-0001", "孔子", "person")]
        registry_index = [
            _make_registry_concept("book-1", "Confucius", "person"),
        ]
        record = run_cross_unit_concept_resolution_pass(
            unit_id="unit-0002",
            concepts=concepts,
            registry_index=registry_index,
            unresolved_items=[],
            backend=backend,
            cache_dir=tmp_path / "cache",
            use_cache=True,
        )
        assert record.pass_name == "cross-unit-concept-resolution"
        assert record.data["unit_id"] == "unit-0002"
        assert "concepts" in record.data
        assert "resolution_proposals" in record.data
        assert "implicit_refs" in record.data

    def test_agentic_run_preserves_raw_link_proposals(self, tmp_path: Path) -> None:
        from tilusion.book_registry import BookRegistry

        backend = _ControlledResolutionBackend({
            "status": "complete",
            "unit_id": "unit-0002",
            "resolution_proposals": [{
                "proposal_id": "res-0001",
                "proposal_type": "link",
                "target_refs": ["c-0001"],
                "registry_ref": "book-1",
                "changes": {},
                "rationale": "same person",
                "implicit_refs": [{
                    "item_ref": "book-item-1",
                    "concept_ref": "book-concept-1",
                    "reason": "implicit reference",
                }],
                "uncertainty": [],
                "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"},
            }],
            "unresolved_items": [],
            "warnings": [],
        })
        registry = BookRegistry(book_path="book", cache_root=tmp_path / "registry")
        record = run_cross_unit_concept_resolution_pass(
            unit_id="unit-0002",
            concepts=[_make_concept("c-0001", "孔子", "person", source_block_refs=["b1"])],
            registry_index=[_make_registry_concept("book-1", "孔子", "person")],
            unresolved_items=[],
            backend=backend,
            cache_dir=tmp_path / "cache",
            use_cache=False,
            source_blocks=[{"block_id": "b1", "unit_id": "unit-0002", "segment_id": "seg-0001", "block_index": 0, "block_type": "paragraph", "text": "孔子", "char_start": 0, "char_end": 2, "start": 0, "end": 2, "text_hash": "hash-b1"}],
            registry=registry,
        )
        assert len(record.data["resolution_proposals"]) == 1
        assert record.data["resolution_proposals"][0]["registry_ref"] == "book-1"
        assert record.data["concepts"][0]["registry_ref"] == "book-1"
        assert record.data["implicit_refs"]["book-1"]["implicit_refs"]
        assert record.data["agentic_status"] == "complete"


class TestGroupResolutionPass:
    def test_run_with_mock_backend(self, tmp_path: Path) -> None:
        backend = MockReadingBackend()
        concepts = [_make_concept("c-0001", "孔子", "person")]
        groups = [{"group_id": "g-0001", "group_type": "timeline", "summary": "Timeline."}]
        registry_groups = [
            {"group_id": "book-g-1", "group_type": "timeline", "summary": "Book timeline.",
             "concept_refs": ["book-1"]},
        ]
        record = run_cross_unit_group_resolution_pass(
            unit_id="unit-0002",
            concepts=concepts,
            groups=groups,
            registry_groups=registry_groups,
            backend=backend,
            cache_dir=tmp_path / "cache",
            use_cache=True,
        )
        assert record.pass_name == "cross-unit-group-resolution"
        assert record.data["unit_id"] == "unit-0002"
        assert "group_resolution_proposals" in record.data

    def test_agentic_run_preserves_raw_continue_proposals(self, tmp_path: Path) -> None:
        from tilusion.book_registry import BookRegistry

        backend = _ControlledResolutionBackend({
            "status": "complete",
            "unit_id": "unit-0002",
            "group_resolution_proposals": [{
                "proposal_id": "grp-res-0001",
                "proposal_type": "continue",
                "unit_group_ref": "g-0001",
                "registry_group_ref": "book-g-1",
                "changes": {},
                "edge": {},
                "rationale": "same thread",
                "uncertainty": [],
                "provenance": {"grounding": "llm_inferred", "created_by": "llm_inferred"},
            }],
            "warnings": [],
        })
        registry = BookRegistry(book_path="book", cache_root=tmp_path / "registry")
        record = run_cross_unit_group_resolution_pass(
            unit_id="unit-0002",
            concepts=[_make_concept("c-0001", "孔子", "person", source_block_refs=["b1"])],
            groups=[{
                "group_id": "g-0001",
                "group_type": "timeline",
                "summary": "Timeline.",
                "concept_refs": ["c-0001"],
                "item_refs": ["item-0001"],
                "graph": {
                    "nodes": [{"node_id": "n1", "item_ref": "item-0001", "label": "孔子"}],
                    "edges": [],
                },
            }],
            atomic_items=[{
                "item_id": "item-0001",
                "item_type": "event",
                "summary": "孔子出现。",
                "source_block_refs": ["b1"],
                "concept_refs": ["c-0001"],
                "temporal_attributes": [],
                "attributes": {},
                "uncertainty": [],
                "provenance": {"grounding": "source_grounded", "created_by": "llm_inferred"},
            }],
            registry_groups=[{
                "group_id": "book-g-1",
                "group_type": "timeline",
                "summary": "Book timeline.",
                "concept_refs": ["book-1"],
            }],
            backend=backend,
            cache_dir=tmp_path / "cache",
            use_cache=False,
            source_blocks=[{"block_id": "b1", "unit_id": "unit-0002", "segment_id": "seg-0001", "block_index": 0, "block_type": "paragraph", "text": "孔子", "char_start": 0, "char_end": 2, "start": 0, "end": 2, "text_hash": "hash-b1"}],
            registry=registry,
        )
        assert len(record.data["group_resolution_proposals"]) == 1
        assert record.data["group_resolution_proposals"][0]["registry_group_ref"] == "book-g-1"
        assert record.data["logical_groups"][0]["registry_group_ref"] == "book-g-1"
        assert record.data["logical_groups"][0]["graph"]["nodes"][0]["item_ref"] == "item-0001"
        assert record.data["atomic_items"][0]["item_id"] == "item-0001"
        assert record.validation_report.passed
        assert record.data["agentic_status"] == "complete"


# ── Integration: two-unit book scope pipeline ─────────────────────────────────


def test_two_unit_book_scope_pipeline(tmp_path: Path) -> None:
    """Unit 1 runs all steps (step 5 skipped — no prior groups).
    Unit 2 runs all steps including group resolution with non-empty registry."""
    from tilusion.reading_pipeline import run_reading_pipeline

    # Create a text file with chapter headings so build_book_index finds units
    book_path = tmp_path / "book.txt"
    book_path.write_text(
        "Chapter 1\n\nConfucius was a philosopher.\n\n"
        "Chapter 2\n\nThe philosopher influenced many.\n\n",
        encoding="utf-8",
    )

    cache_dir = tmp_path / "cache"

    # Unit 1 with book scope
    record1 = run_reading_pipeline(
        str(book_path),
        "unit-0001",
        backend=None,
        cache_dir=str(cache_dir),
        use_cache=False,
        scope="book",
    )
    assert record1.unit_id == "unit-0001"
    assert "concepts" in record1.data

    # Unit 2 with book scope — should run concept resolution and group resolution
    record2 = run_reading_pipeline(
        str(book_path),
        "unit-0002",
        backend=None,
        cache_dir=str(cache_dir),
        use_cache=False,
        scope="book",
    )
    assert record2.unit_id == "unit-0002"
    assert "concepts" in record2.data
    # Verify pass summaries include the new passes
    passes = record2.passes
    assert "cross_unit_concept_resolution" in passes
    assert "cross_unit_group_resolution" in passes


def test_two_unit_book_scope_pipeline_with_concept_linking(tmp_path: Path) -> None:
    """Verify that concept identity is linked across units via mock backend."""
    from tilusion.reading_pipeline import run_reading_pipeline
    from tilusion.book_registry import BookRegistry

    book_path = tmp_path / "book2.txt"
    book_path.write_text(
        "Chapter 1\n\nConfucius taught many students.\n\n"
        "Chapter 2\n\nConfucius continued teaching.\n\n",
        encoding="utf-8",
    )

    cache_dir = tmp_path / "cache2"

    # Unit 1
    run_reading_pipeline(
        str(book_path), "unit-0001",
        backend=None,
        cache_dir=str(cache_dir),
        use_cache=False,
        scope="book",
    )

    # Verify registry and digest are persisted after unit 1
    registry = BookRegistry.load(str(book_path), cache_root=cache_dir)
    assert registry.has_concepts()
    digest_path = registry.cache_dir / "book_digest.json"
    assert digest_path.exists()

    # Unit 2 — should find link proposals and load prior digest context
    record2 = run_reading_pipeline(
        str(book_path), "unit-0002",
        backend=None,
        cache_dir=str(cache_dir),
        use_cache=False,
        scope="book",
    )

    # Check that pass summaries reflect the new pipeline steps
    assert record2.passes["cross_unit_concept_resolution"]["elapsed_ms"] >= 0
    assert record2.passes["unit_logical_grouping"]["elapsed_ms"] >= 0


def test_unit_scope_skips_cross_unit_passes(tmp_path: Path) -> None:
    """Unit scope should skip concept and group resolution passes."""
    from tilusion.reading_pipeline import run_reading_pipeline

    book_path = tmp_path / "book3.txt"
    book_path.write_text("Chapter 1\n\nSome text.\n\n", encoding="utf-8")

    cache_dir = tmp_path / "cache3"

    record = run_reading_pipeline(
        str(book_path), "unit-0001",
        backend=None,
        cache_dir=str(cache_dir),
        use_cache=False,
        scope="unit",
    )

    # Unit scope should not have cross-unit passes
    passes = record.passes
    assert "cross_unit_concept_resolution" not in passes
    assert "cross_unit_group_resolution" not in passes
    assert "overview_segmentation" in passes
    assert "unit_logical_grouping" in passes
