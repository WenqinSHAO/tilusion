from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tilusion.book_registry import BookRegistry
from tilusion.reading_schema import Concept, ExtractionUnitPackage
from tilusion.registry_index import (
    CompactGroup,
    _build_group_text,
    _build_unit_group_text,
    _dual_signal_select_groups,
    build_group_index,
    build_registry_index,
    select_group_candidates,
)
from tilusion.registry_tools import (
    TOOL_DEFINITIONS,
    ToolDefinition,
    execute_tool_call,
    render_tool_definitions_markdown,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_registry(concepts=None, groups=None) -> BookRegistry:
    """Create a BookRegistry with optional pre-populated concepts and groups."""
    reg = BookRegistry(book_path="test-book", cache_root=Path("/tmp/test-reg"))
    if concepts:
        for c in concepts:
            reg.add_concept(c, force=True)
    if groups:
        for g in groups:
            reg.add_group(g)
    return reg


def _concept(cid: str, surface: str, ctype: str = "person", summary: str = ""):
    return Concept(
        concept_id=cid,
        surface=surface,
        concept_type=ctype,
        summary=summary,
        canonical_name=surface,
        observed_surfaces=[surface],
    )


# ── CompactGroup ─────────────────────────────────────────────────────────────


class TestCompactGroup:
    def test_creation(self):
        cg = CompactGroup(
            group_id="g1",
            group_type="timeline",
            summary="A test group",
            key_concept_ids=["c1", "c2"],
            item_count=5,
        )
        assert cg.group_id == "g1"
        assert cg.group_type == "timeline"
        assert cg.item_count == 5
        assert cg.key_concept_ids == ["c1", "c2"]


# ── build_group_index ────────────────────────────────────────────────────────


class TestBuildGroupIndex:
    def test_empty_registry(self):
        reg = _make_registry()
        assert build_group_index(reg) == []

    def test_single_group(self):
        from tilusion.reading_schema import LogicalGroup

        reg = _make_registry()
        assigned_id = reg.add_group(LogicalGroup(
            group_id="g1",
            group_type="timeline",
            summary="Events in order",
            item_refs=["i1", "i2", "i3"],
            concept_refs=["c1", "c2"],
        ))
        index = build_group_index(reg)
        assert len(index) == 1
        cg = index[0]
        assert cg.group_id == assigned_id
        assert cg.group_type == "timeline"
        assert cg.item_count == 3
        assert cg.key_concept_ids == ["c1", "c2"]

    def test_summary_truncation(self):
        from tilusion.reading_schema import LogicalGroup

        reg = _make_registry()
        reg.add_group(LogicalGroup(
            group_id="g1",
            group_type="timeline",
            summary="A" * 200,
            item_refs=[],
            concept_refs=[],
        ))
        cg = build_group_index(reg)[0]
        assert len(cg.summary) == 120
        assert cg.summary.endswith("...")

    def test_key_concept_ids_limited_to_5(self):
        from tilusion.reading_schema import LogicalGroup

        reg = _make_registry()
        reg.add_group(LogicalGroup(
            group_id="g1",
            group_type="timeline",
            summary="Test",
            item_refs=[],
            concept_refs=["c1", "c2", "c3", "c4", "c5", "c6", "c7"],
        ))
        cg = build_group_index(reg)[0]
        assert len(cg.key_concept_ids) == 5
        assert cg.key_concept_ids == ["c1", "c2", "c3", "c4", "c5"]


# ── Group text builders ──────────────────────────────────────────────────────


class TestBuildGroupText:
    def test_registry_group_text(self):
        cg = CompactGroup(
            group_id="g1",
            group_type="timeline",
            summary="Opium War events",
            key_concept_ids=[],
            item_count=3,
        )
        text = _build_group_text(cg)
        assert "Opium War events" in text
        assert "timeline" in text

    def test_unit_group_text(self):
        ug = {"summary": "A set of economic themes", "group_type": "theme_set"}
        text = _build_unit_group_text(ug)
        assert "A set of economic themes" in text
        assert "theme_set" in text


# ── dual_signal_select_groups ────────────────────────────────────────────────


class TestDualSignalSelectGroups:
    def test_empty_inputs(self):
        assert _dual_signal_select_groups([], []) == set()
        cg = CompactGroup("g1", "timeline", "Test", [], 1)
        assert _dual_signal_select_groups([], [cg]) == set()

    def test_bm25_match(self):
        cg = CompactGroup("g1", "timeline", "Opium War events 1839-1842", [], 5)
        ug = {"summary": "Opium War timeline", "group_type": "timeline"}
        result = _dual_signal_select_groups([ug], [cg], top_k=10)
        assert "g1" in result


# ── select_group_candidates extended ─────────────────────────────────────────


class TestSelectGroupCandidatesExtended:
    def test_small_registry_passthrough(self):
        rg = [
            {"group_id": "g1", "group_type": "timeline", "summary": "T1",
             "concept_refs": ["c1"], "item_refs": []}
        ]
        result = select_group_candidates([{"summary": "X"}], rg, [])
        assert result == rg

    def test_concept_overlap_preserved(self):
        rg = [{"group_id": f"g{i}", "group_type": "timeline", "summary": f"T{i}",
               "concept_refs": [f"c{i}"], "item_refs": []} for i in range(60)]
        resolved = [{"registry_ref": "c0"}, {"registry_ref": "c1"}]
        result = select_group_candidates(
            [{"summary": "X", "group_type": "timeline", "concept_refs": ["c0"]}],
            rg, resolved,
        )
        # The concept-overlap groups (g0, g1) should be included
        overlap_ids = {g["group_id"] for g in result}
        assert "g0" in overlap_ids or "g1" in overlap_ids

    def test_fallback_to_type(self, monkeypatch):
        """When dual-signal returns no candidates, fall back to group_type matching."""
        rg = [{"group_id": f"g{i}", "group_type": "timeline", "summary": f"T{i}",
               "concept_refs": [], "item_refs": []} for i in range(60)]
        # Add one theme_set group so fallback has something to match
        rg.append({"group_id": "g-theme", "group_type": "theme_set",
                    "summary": "A theme", "concept_refs": [], "item_refs": []})

        # Force embedding model to None so only BM25 runs (no token overlap → empty)
        import tilusion.registry_index as ri
        monkeypatch.setattr(ri, "_get_embedding_model", lambda: None)

        result = select_group_candidates(
            [{"summary": "X", "group_type": "theme_set"}],
            rg, [],
        )
        # Falls back to type matching: only the theme_set group is returned
        assert len(result) >= 1
        for g in result:
            assert g["group_type"] == "theme_set"


# ── Tool execution ───────────────────────────────────────────────────────────


class TestToolExecution:
    @pytest.fixture
    def ctx(self):
        reg = _make_registry(concepts=[_concept("bc-1", "Alice", "person", "Protagonist")])
        from tilusion.reading_schema import LogicalGroup

        reg.add_group(LogicalGroup(
            group_id="bg-1", group_type="timeline", summary="Events",
            item_refs=["i1"], concept_refs=["bc-1"],
        ))
        # Registry normalizes IDs; resolve the actual assigned IDs
        assigned_concept_id = reg.list_concepts()[0].concept_id
        assigned_group_id = next(iter(reg._groups.keys()))
        return {
            "registry": reg,
            "source_blocks": [{"block_id": "b1", "text": "Hello"}],
            "book_summary": "A test book",
            "_assigned_concept_id": assigned_concept_id,
            "_assigned_group_id": assigned_group_id,
        }

    def test_get_concept_returns_full_record(self, ctx):
        result = execute_tool_call(
            {"action": "get_concept", "args": {"concept_id": ctx["_assigned_concept_id"]}}, ctx
        )
        assert result["tool"] == "get_concept"
        assert result["result"]["concept_id"] == ctx["_assigned_concept_id"]
        assert result["result"]["surface"] == "Alice"

    def test_get_concept_not_found(self, ctx):
        result = execute_tool_call(
            {"action": "get_concept", "args": {"concept_id": "nonexistent"}}, ctx
        )
        assert "error" in result

    def test_get_concept_missing_id(self, ctx):
        result = execute_tool_call(
            {"action": "get_concept", "args": {}}, ctx
        )
        assert "error" in result

    def test_get_group_returns_full_record(self, ctx):
        result = execute_tool_call(
            {"action": "get_group", "args": {"group_id": ctx["_assigned_group_id"]}}, ctx
        )
        assert result["tool"] == "get_group"
        assert result["result"]["group_id"] == ctx["_assigned_group_id"]

    def test_get_group_not_found(self, ctx):
        result = execute_tool_call(
            {"action": "get_group", "args": {"group_id": "nonexistent"}}, ctx
        )
        assert "error" in result

    def test_search_concepts(self, ctx):
        result = execute_tool_call(
            {"action": "search_concepts", "args": {"query": "Alice"}}, ctx
        )
        assert result["tool"] == "search_concepts"
        assert isinstance(result["result"], list)

    def test_search_groups(self, ctx):
        result = execute_tool_call(
            {"action": "search_groups", "args": {"query": "Events"}}, ctx
        )
        assert result["tool"] == "search_groups"
        assert isinstance(result["result"], list)

    def test_get_source_block_found(self, ctx):
        result = execute_tool_call(
            {"action": "get_source_block", "args": {"block_id": "b1"}}, ctx
        )
        assert result["tool"] == "get_source_block"
        assert result["result"]["text"] == "Hello"

    def test_get_source_block_not_found(self, ctx):
        result = execute_tool_call(
            {"action": "get_source_block", "args": {"block_id": "nonexistent"}}, ctx
        )
        assert "error" in result

    def test_get_book_summary(self, ctx):
        result = execute_tool_call(
            {"action": "get_book_summary", "args": {}}, ctx
        )
        assert result["tool"] == "get_book_summary"
        assert result["result"] == "A test book"

    def test_unknown_tool(self, ctx):
        result = execute_tool_call(
            {"action": "nonexistent_tool", "args": {}}, ctx
        )
        assert "error" in result
        assert "Unknown tool" in result["error"]


# ── render_tool_definitions_markdown ─────────────────────────────────────────


class TestRenderToolDefinitions:
    def test_all_tools_rendered(self):
        md = render_tool_definitions_markdown()
        assert "## Available Tools" in md
        for name in TOOL_DEFINITIONS:
            assert f"### {name}" in md

    def test_subset_rendered(self):
        md = render_tool_definitions_markdown(["get_concept"])
        assert "### get_concept" in md
        assert "### get_group" not in md

    def test_unknown_tool_skipped(self):
        md = render_tool_definitions_markdown(["nonexistent"])
        assert "### nonexistent" not in md


# ── Agentic resolution pass (integration) ────────────────────────────────────


class TestAgenticResolutionPass:
    """Tests for the multi-turn agentic resolution pass using mock backends."""

    @pytest.fixture
    def registry(self):
        return _make_registry(concepts=[
            _concept("bc-1", "entropy", "technical_component", "A measure of disorder"),
            _concept("bc-2", "Alice", "person", "Protagonist"),
        ])

    def _make_mock_agentic_backend(self, initial_response, second_response=None):
        """Create a mock backend that returns controlled responses per turn."""
        class ControlledBackend:
            model_identity = "mock-agentic-test"

            def __init__(self):
                self.turn = 0
                self.initial = initial_response
                self.second = second_response
                self._last_conversation = None

            def start_conversation(self, system_prompt, user_payload, *, pass_name=""):
                from tilusion.conversation import ConversationContext, TurnMetadata

                ctx = ConversationContext.create(
                    model_identity=self.model_identity,
                    pass_name=pass_name,
                    system_prompt=system_prompt,
                    user_payload=user_payload,
                )
                ctx.record_turn(
                    assistant_response=json.dumps(self.initial, ensure_ascii=False),
                    metadata=TurnMetadata(turn_index=1, turn_type="initial", elapsed_ms=0),
                )
                self._last_conversation = ctx
                self.turn = 1
                return ctx

            def continue_conversation(self, conversation, user_message):
                from tilusion.conversation import TurnMetadata

                conversation.append_user_message(user_message)
                self.turn += 1
                resp = self.second if self.second else {"status": "complete", "warnings": []}
                conversation.record_turn(
                    assistant_response=json.dumps(resp, ensure_ascii=False),
                    metadata=TurnMetadata(
                        turn_index=self.turn, turn_type="tool_result_response", elapsed_ms=0,
                    ),
                )
                return conversation

        return ControlledBackend()

    def test_completes_immediately_when_no_tool_calls(self, registry, tmp_path):
        """When response has no tool_calls, loop exits immediately."""
        from tilusion.reading_pipeline import run_agentic_resolution_pass
        from tilusion.pass_utils import PromptComposition, PromptPart

        prompt = PromptComposition(
            composition_id="test",
            parts=[PromptPart(part_id="p1", role="static", source="test", content="Test prompt")],
        )
        payload = {"task": "cross_unit_concept_resolution", "concepts": []}

        def _build_subject(data):
            from tilusion.reading_schema import READING_UNIT_SCHEMA_VERSION
            return {
                "schema_version": READING_UNIT_SCHEMA_VERSION,
                "unit_id": "u1",
                "source": {},
                "source_blocks": [],
                "concepts": [],
                "atomic_items": [],
                "logical_groups": [],
                "unresolved_items": [],
                "validation": {},
                "context_metadata": {},
            }

        backend = self._make_mock_agentic_backend(
            initial_response={
                "status": "complete",
                "unit_id": "u1",
                "resolution_proposals": [],
                "unresolved_items": [],
                "warnings": [],
            }
        )

        result = run_agentic_resolution_pass(
            backend=backend,
            prompt=prompt,
            payload=payload,
            tool_context={"registry": registry},
            validation_subject_builder=_build_subject,
            pass_name="test",
        )
        assert result.validation_report.passed
        assert result.raw_data["resolution_proposals"] == []
        assert result.applied_subject["unit_id"] == "u1"

    def test_single_tool_call_then_complete(self, registry, tmp_path):
        """Turn 1: tool_calls, Turn 2: status=complete with proposals."""
        from tilusion.reading_pipeline import run_agentic_resolution_pass
        from tilusion.pass_utils import PromptComposition, PromptPart

        prompt = PromptComposition(
            composition_id="test",
            parts=[PromptPart(part_id="p1", role="static", source="test", content="Test")],
        )
        payload = {"task": "cross_unit_concept_resolution", "concepts": [
            {"concept_id": "c1", "surface": "entropy", "concept_type": "technical_component"}
        ]}

        def _build_subject(data):
            from tilusion.reading_schema import READING_UNIT_SCHEMA_VERSION
            return {
                "schema_version": READING_UNIT_SCHEMA_VERSION,
                "unit_id": "u1",
                "source": {},
                "source_blocks": [],
                "concepts": [],
                "atomic_items": [],
                "logical_groups": [],
                "unresolved_items": [],
                "validation": {},
                "context_metadata": {},
            }

        backend = self._make_mock_agentic_backend(
            initial_response={
                "tool_calls": [
                    {"action": "get_concept", "args": {"concept_id": "bc-1"}}
                ],
            },
            second_response={
                "status": "complete",
                "unit_id": "u1",
                "resolution_proposals": [],
                "unresolved_items": [],
                "warnings": [],
            },
        )

        result = run_agentic_resolution_pass(
            backend=backend,
            prompt=prompt,
            payload=payload,
            tool_context={"registry": registry},
            validation_subject_builder=_build_subject,
            pass_name="test",
        )
        assert result.validation_report.passed
        assert result.raw_data["resolution_proposals"] == []
        assert result.turns_used == 2
        assert result.agentic_trace is not None
        assert result.agentic_trace["turns"][0]["assistant"]["tool_call_count"] == 1
        tool_trace = result.agentic_trace["turns"][0]["tool_calls"][0]
        assert tool_trace["action"] == "get_concept"
        assert "Concept 'bc-1' not found" in tool_trace["error"]
        assert "elapsed_ms" in tool_trace

    def test_max_turns_exhausted_fallback(self, registry, tmp_path):
        """When max_turns reached, falls back to single-pass."""
        from tilusion.reading_pipeline import run_agentic_resolution_pass
        from tilusion.pass_utils import PromptComposition, PromptPart

        prompt = PromptComposition(
            composition_id="test",
            parts=[PromptPart(part_id="p1", role="static", source="test", content="Test")],
        )
        payload = {"task": "cross_unit_concept_resolution", "concepts": []}

        def _build_subject(data):
            from tilusion.reading_schema import READING_UNIT_SCHEMA_VERSION
            return {
                "schema_version": READING_UNIT_SCHEMA_VERSION,
                "unit_id": "u1",
                "source": {},
                "source_blocks": [],
                "concepts": [],
                "atomic_items": [],
                "logical_groups": [],
                "unresolved_items": [],
                "validation": {},
                "context_metadata": {},
            }

        # Backend that always returns tool_calls (infinite loop)
        backend = self._make_mock_agentic_backend(
            initial_response={
                "tool_calls": [
                    {"action": "get_concept", "args": {"concept_id": "bc-1"}}
                ],
            },
            second_response={
                "tool_calls": [
                    {"action": "get_concept", "args": {"concept_id": "bc-1"}}
                ],
            },
        )

        result = run_agentic_resolution_pass(
            backend=backend,
            prompt=prompt,
            payload=payload,
            tool_context={"registry": registry},
            validation_subject_builder=_build_subject,
            max_turns=3,
            pass_name="test",
        )
        assert result.exhausted
        assert "max turns exhausted" in result.failure_reason
        assert "tool_calls" in result.raw_data
        assert result.agentic_trace is not None
        assert result.agentic_trace["fallback_used"] is False
        assert "max turns exhausted" in result.agentic_trace["failure_reason"]


# ── BookRegistry helpers ─────────────────────────────────────────────────────


class TestBookRegistryHelpers:
    def test_has_groups_empty(self):
        reg = _make_registry()
        assert not reg.has_groups()

    def test_has_groups_with_groups(self):
        from tilusion.reading_schema import LogicalGroup

        reg = _make_registry()
        reg.add_group(LogicalGroup(group_id="g1", group_type="timeline", summary="Test"))
        assert reg.has_groups()

    def test_list_concepts(self):
        reg = _make_registry(concepts=[
            _concept("c1", "Alice"), _concept("c2", "Bob")
        ])
        concepts = reg.list_concepts()
        assert len(concepts) == 2
