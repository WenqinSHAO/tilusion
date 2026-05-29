from __future__ import annotations

import json

from tilusion.repair import (
    apply_repair_patch,
    build_repair_message,
    run_agentic_pass,
)
from tilusion.reading_pipeline import MockReadingBackend
from tilusion.pass_utils import PromptComposition, PromptPart
from tilusion.reading_schema import READING_UNIT_SCHEMA_VERSION


def _mock_prompt() -> PromptComposition:
    return PromptComposition(
        composition_id="test-prompt",
        parts=[
            PromptPart(
                part_id="test",
                role="static_task_contract",
                source="test",
                content="You are a test extraction agent. Return only one JSON object.",
            )
        ],
    )


def _make_valid_subject_builder(source_blocks: list[dict[str, Any]]):
    """Return a validation_subject_builder that includes authoritative source_blocks."""
    def builder(llm_data: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": READING_UNIT_SCHEMA_VERSION,
            "unit_id": llm_data.get("unit_id", "unit-0001"),
            "source": {},
            "source_blocks": source_blocks,
            "concepts": llm_data.get("concepts", []),
            "atomic_items": llm_data.get("atomic_items", []),
            "logical_groups": [],
            "unresolved_items": [],
            "validation": {},
            "context_metadata": {},
        }
    return builder


def test_build_repair_message_is_compact() -> None:
    errors = [
        {
            "code": "missing_source_block_refs",
            "path": "concepts[64].source_block_refs",
            "message": "Must cite at least one source block.",
            "repair_hint": "Inherit from merged source concepts.",
        },
        {
            "code": "unknown_ref",
            "path": "concepts[2].source_block_refs[0]",
            "message": "Reference does not resolve.",
            "repair_hint": "",
        },
    ]
    msg = build_repair_message(errors)
    data = json.loads(msg)

    assert data["task"] == "repair_extraction"
    assert len(data["errors"]) == 2
    assert data["errors"][0]["code"] == "missing_source_block_refs"
    assert "instruction" in data
    # Should be compact — not a full regeneration
    assert "concepts" not in msg.lower() or "instruction" in msg


def test_apply_repair_patch_replace() -> None:
    data = {"concepts": [{"concept_id": "c1", "source_block_refs": []}]}
    repairs = [
        {
            "path": "concepts[0].source_block_refs",
            "operation": "replace",
            "value": ["seg-0003-block-0001"],
        }
    ]
    apply_repair_patch(data, repairs)
    assert data["concepts"][0]["source_block_refs"] == ["seg-0003-block-0001"]


def test_apply_repair_patch_append() -> None:
    data = {"concepts": [{"aliases": ["Alice"]}]}
    repairs = [
        {"path": "concepts[0].aliases", "operation": "append", "value": "Alicia"}
    ]
    apply_repair_patch(data, repairs)
    assert data["concepts"][0]["aliases"] == ["Alice", "Alicia"]


def test_apply_repair_patch_remove_from_list() -> None:
    data = {"concepts": [{"source_block_refs": ["b1", "bad-ref", "b2"]}]}
    repairs = [
        {"path": "concepts[0].source_block_refs[1]", "operation": "remove"}
    ]
    apply_repair_patch(data, repairs)
    assert data["concepts"][0]["source_block_refs"] == ["b1", "b2"]


def test_apply_repair_patch_unknown_path_does_not_crash() -> None:
    data: dict[str, Any] = {"concepts": []}
    repairs = [{"path": "concepts[99].field", "operation": "replace", "value": "x"}]
    # Should not raise
    apply_repair_patch(data, repairs)


def _mock_source_blocks() -> list[dict[str, Any]]:
    """Return validation-passing source block dicts."""
    text = "Hello world."
    return [
        {
            "block_id": "seg-0001-block-0000",
            "unit_id": "unit-0001",
            "segment_id": "seg-0001",
            "block_index": 0,
            "block_type": "paragraph",
            "start": 0,
            "end": len(text),
            "text": text,
            "text_hash": "abc123",
            "provenance": {"grounding": "source_grounded", "created_by": "deterministic"},
        }
    ]


def test_run_agentic_pass_valid_data_no_repair_needed() -> None:
    backend = MockReadingBackend()
    prompt = _mock_prompt()
    blocks = _mock_source_blocks()
    payload = {
        "task": "per_segment_extraction",
        "unit_id": "unit-0001",
        "segment": {"segment_id": "seg-0001"},
        "source_blocks": [
            {"block_id": b["block_id"], "block_type": b["block_type"], "start": b["start"], "end": b["end"]}
            for b in blocks
        ],
        "text": blocks[0]["text"],
        "context": {},
    }

    data, conversation, report = run_agentic_pass(
        backend=backend,
        prompt=prompt,
        payload=payload,
        validation_subject_builder=_make_valid_subject_builder(blocks),
        pass_name="test-pass",
    )

    assert data["unit_id"] == "unit-0001"
    assert conversation.turn_count == 1  # No repair turns needed
    assert report.passed


def test_run_agentic_pass_preserves_conversation_context() -> None:
    backend = MockReadingBackend()
    prompt = _mock_prompt()
    blocks = _mock_source_blocks()
    payload = {
        "task": "per_segment_extraction",
        "unit_id": "unit-0001",
        "segment": {"segment_id": "seg-0001"},
        "source_blocks": [
            {"block_id": b["block_id"], "block_type": b["block_type"], "start": b["start"], "end": b["end"]}
            for b in blocks
        ],
        "text": blocks[0]["text"],
        "context": {},
    }

    _data, conversation, _report = run_agentic_pass(
        backend=backend,
        prompt=prompt,
        payload=payload,
        validation_subject_builder=_make_valid_subject_builder(blocks),
        pass_name="test-pass",
    )

    # Verify conversation has the expected shape
    assert conversation.initial_system_prompt == prompt.content
    assert conversation.initial_payload == payload
    assert len(conversation.messages) >= 3  # system, user, assistant
    assert conversation.turn_metadata[0].turn_type == "initial"
    assert conversation.turn_metadata[0].validation_report is not None
    assert conversation.turn_metadata[0].validation_report["passed"] is True


def test_run_agentic_pass_with_repair_triggered() -> None:
    """When validation fails, the repair loop should attempt auto-fix and LLM repair."""
    backend = MockReadingBackend()
    prompt = _mock_prompt()
    blocks = _mock_source_blocks()
    payload = {
        "task": "per_segment_extraction",
        "unit_id": "unit-0001",
        "segment": {"segment_id": "seg-0001"},
        "source_blocks": [
            {"block_id": b["block_id"], "block_type": b["block_type"], "start": b["start"], "end": b["end"]}
            for b in blocks
        ],
        "text": blocks[0]["text"],
        "context": {},
    }

    data, conversation, report = run_agentic_pass(
        backend=backend,
        prompt=prompt,
        payload=payload,
        validation_subject_builder=_make_valid_subject_builder(blocks),
        pass_name="test-pass",
    )

    assert report.passed
    assert conversation.turn_count == 1  # Mock data is valid, no repairs
