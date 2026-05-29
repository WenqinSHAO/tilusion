from __future__ import annotations

import json

from tilusion.reading_pipeline import MockReadingBackend
from tilusion.pass_utils import pass_artifact_paths
from pathlib import Path


def test_pass_artifact_paths_includes_conversation() -> None:
    paths = pass_artifact_paths(Path("/tmp/test-pass-dir"))
    assert "conversation" in paths
    assert paths["conversation"].endswith("conversation.json")


def test_mock_backend_start_conversation() -> None:
    backend = MockReadingBackend()
    payload = {
        "task": "per_segment_extraction",
        "unit_id": "unit-0001",
        "segment": {"segment_id": "seg-0001"},
        "source_blocks": [
            {"block_id": "seg-0001-block-0000", "block_type": "paragraph", "start": 0, "end": 33}
        ],
        "text": "Hello world.",
        "context": {},
    }

    ctx = backend.start_conversation(
        system_prompt="You are an extraction agent.",
        user_payload=payload,
        pass_name="per-segment-extraction",
    )

    assert ctx.turn_count == 1
    assert ctx.pass_name == "per-segment-extraction"
    assert len(ctx.messages) == 3  # system, user, assistant
    assert ctx.messages[0]["role"] == "system"
    assert ctx.messages[1]["role"] == "user"
    assert ctx.messages[2]["role"] == "assistant"

    # Verify the assistant response is valid JSON from the mock
    assistant_data = json.loads(ctx.messages[2]["content"])
    assert assistant_data["unit_id"] == "unit-0001"
    assert len(assistant_data["concepts"]) == 1
    assert len(ctx.turn_metadata) == 1
    assert ctx.turn_metadata[0].turn_type == "initial"
    assert ctx.turn_metadata[0].turn_index == 1


def test_mock_backend_continue_conversation() -> None:
    backend = MockReadingBackend()
    payload = {
        "task": "per_segment_extraction",
        "unit_id": "unit-0001",
        "segment": {"segment_id": "seg-0001"},
        "source_blocks": [
            {"block_id": "seg-0001-block-0000", "block_type": "paragraph", "start": 0, "end": 33}
        ],
        "text": "Hello world.",
        "context": {},
    }

    ctx = backend.start_conversation(
        system_prompt="You are an extraction agent.",
        user_payload=payload,
        pass_name="per-segment-extraction",
    )

    ctx = backend.continue_conversation(
        ctx,
        user_message=json.dumps(
            {"task": "repair_extraction", "errors": [{"code": "missing_source_block_refs"}]}
        ),
    )

    assert ctx.turn_count == 2
    assert len(ctx.messages) == 5  # system, user, assistant, user(repair), assistant(repair)
    assert ctx.messages[3]["role"] == "user"
    assert "missing_source_block_refs" in ctx.messages[3]["content"]

    # Verify mock repair response
    repair_data = json.loads(ctx.messages[4]["content"])
    assert "repairs" in repair_data
    assert len(ctx.turn_metadata) == 2
    assert ctx.turn_metadata[1].turn_type == "repair"
    assert ctx.turn_metadata[1].turn_index == 2


def test_mock_backend_conversation_preserves_initial_state() -> None:
    backend = MockReadingBackend()
    payload = {"task": "per_segment_extraction", "text": "Test.", "context": {}}

    ctx = backend.start_conversation(
        system_prompt="system-prompt",
        user_payload=payload,
        pass_name="test-pass",
    )

    assert ctx.initial_system_prompt == "system-prompt"
    assert ctx.initial_payload == payload
    assert ctx.model_identity == "mock-reading-v0"


def test_mock_backend_continue_conversation_preserves_kv_prefix() -> None:
    """After repair turns, the original system+user+assistant messages are unchanged."""
    backend = MockReadingBackend()
    payload = {"task": "per_segment_extraction", "text": "Test.", "context": {}}

    ctx = backend.start_conversation(
        system_prompt="system-prompt",
        user_payload=payload,
        pass_name="test-pass",
    )

    original_first_three = [dict(m) for m in ctx.messages[:3]]

    ctx = backend.continue_conversation(ctx, user_message="fix errors")

    # First 3 messages are byte-identical — KV-cache still hits
    assert ctx.messages[:3] == original_first_three
