from __future__ import annotations

from tilusion.conversation import ConversationContext, TurnMetadata


def test_turn_metadata_round_trip() -> None:
    original = TurnMetadata(
        turn_index=1,
        turn_type="repair",
        auto_fixes_applied=["duplicate_object_id", "empty_string_list_item"],
        validation_report={"passed": False, "error_count": 3},
        elapsed_ms=1234,
    )
    restored = TurnMetadata.from_dict(original.to_dict())

    assert restored.turn_index == 1
    assert restored.turn_type == "repair"
    assert restored.auto_fixes_applied == ["duplicate_object_id", "empty_string_list_item"]
    assert restored.validation_report == {"passed": False, "error_count": 3}
    assert restored.elapsed_ms == 1234


def test_turn_metadata_defaults() -> None:
    tm = TurnMetadata(turn_index=0, turn_type="initial")
    assert tm.auto_fixes_applied == []
    assert tm.validation_report is None
    assert tm.elapsed_ms == 0


def test_conversation_context_create() -> None:
    ctx = ConversationContext.create(
        model_identity="deepseek:test:v1",
        pass_name="per-segment-extraction",
        system_prompt="You are an extraction agent.",
        user_payload={"task": "extract", "text": "Hello world."},
    )

    assert len(ctx.messages) == 2
    assert ctx.messages[0]["role"] == "system"
    assert ctx.messages[0]["content"] == "You are an extraction agent."
    assert ctx.messages[1]["role"] == "user"
    assert "Hello world." in ctx.messages[1]["content"]
    assert ctx.turn_count == 0
    assert ctx.pass_name == "per-segment-extraction"
    assert ctx.model_identity == "deepseek:test:v1"
    assert ctx.initial_system_prompt == "You are an extraction agent."
    assert ctx.initial_payload == {"task": "extract", "text": "Hello world."}
    assert len(ctx.conversation_id) == 64  # SHA256 hex digest


def test_conversation_context_record_turn() -> None:
    ctx = ConversationContext.create(
        model_identity="deepseek:test:v1",
        pass_name="test-pass",
        system_prompt="system",
        user_payload={"task": "test"},
    )

    ctx.record_turn(
        assistant_response='{"result": "ok"}',
        metadata=TurnMetadata(
            turn_index=1,
            turn_type="initial",
            auto_fixes_applied=[],
            validation_report={"passed": True},
            elapsed_ms=500,
        ),
    )

    assert ctx.turn_count == 1
    assert len(ctx.messages) == 3
    assert ctx.messages[2]["role"] == "assistant"
    assert ctx.messages[2]["content"] == '{"result": "ok"}'
    assert len(ctx.turn_metadata) == 1
    assert ctx.turn_metadata[0].turn_type == "initial"


def test_conversation_context_append_user_message() -> None:
    ctx = ConversationContext.create(
        model_identity="deepseek:test:v1",
        pass_name="test-pass",
        system_prompt="system",
        user_payload={"task": "test"},
    )
    ctx.record_turn(
        assistant_response='{"result": "ok"}',
        metadata=TurnMetadata(turn_index=1, turn_type="initial"),
    )

    ctx.append_user_message("Please fix errors.")

    assert len(ctx.messages) == 4
    assert ctx.messages[3]["role"] == "user"
    assert ctx.messages[3]["content"] == "Please fix errors."


def test_conversation_context_full_round_trip() -> None:
    ctx = ConversationContext.create(
        model_identity="deepseek:test:v1",
        pass_name="per-segment-extraction",
        system_prompt="You are an extraction agent.",
        user_payload={"task": "extract", "text": "Hello."},
    )
    ctx.record_turn(
        assistant_response='{"concepts": []}',
        metadata=TurnMetadata(
            turn_index=1,
            turn_type="initial",
            validation_report={"passed": False, "error_count": 1},
            elapsed_ms=800,
        ),
    )
    ctx.append_user_message('{"task": "repair", "errors": [{"code": "missing_source_block_refs"}]}')
    ctx.record_turn(
        assistant_response='{"repairs": [{"path": "concepts[0].source_block_refs", "operation": "replace", "value": ["b1"]}]}',
        metadata=TurnMetadata(
            turn_index=2,
            turn_type="repair",
            auto_fixes_applied=[],
            validation_report={"passed": True, "error_count": 0},
            elapsed_ms=400,
        ),
    )

    restored = ConversationContext.from_dict(ctx.to_dict())

    assert restored.conversation_id == ctx.conversation_id
    assert restored.model_identity == "deepseek:test:v1"
    assert restored.pass_name == "per-segment-extraction"
    assert restored.turn_count == 2
    assert len(restored.messages) == 5
    assert restored.messages[0]["role"] == "system"
    assert restored.messages[4]["role"] == "assistant"
    assert restored.initial_system_prompt == "You are an extraction agent."
    assert restored.initial_payload == {"task": "extract", "text": "Hello."}
    assert len(restored.turn_metadata) == 2
    assert restored.turn_metadata[0].turn_type == "initial"
    assert restored.turn_metadata[1].turn_type == "repair"
    assert restored.turn_metadata[1].auto_fixes_applied == []


def test_conversation_id_is_deterministic() -> None:
    payload = {"task": "extract", "text": "Hello."}
    ctx1 = ConversationContext.create(
        model_identity="deepseek:test:v1",
        pass_name="test-pass",
        system_prompt="system",
        user_payload=payload,
    )
    ctx2 = ConversationContext.create(
        model_identity="deepseek:test:v1",
        pass_name="test-pass",
        system_prompt="system",
        user_payload=payload,
    )

    assert ctx1.conversation_id == ctx2.conversation_id


def test_conversation_id_differs_on_payload_change() -> None:
    ctx1 = ConversationContext.create(
        model_identity="deepseek:test:v1",
        pass_name="test-pass",
        system_prompt="system",
        user_payload={"task": "extract", "text": "A"},
    )
    ctx2 = ConversationContext.create(
        model_identity="deepseek:test:v1",
        pass_name="test-pass",
        system_prompt="system",
        user_payload={"task": "extract", "text": "B"},
    )

    assert ctx1.conversation_id != ctx2.conversation_id
