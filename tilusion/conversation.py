from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .backend import sha256_json, sha256_text


@dataclass(slots=True)
class TurnMetadata:
    turn_index: int
    turn_type: str  # "initial" | "repair" | "full_retry"
    auto_fixes_applied: list[str] = field(default_factory=list)
    validation_report: dict[str, Any] | None = None
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_index": self.turn_index,
            "turn_type": self.turn_type,
            "auto_fixes_applied": list(self.auto_fixes_applied),
            "validation_report": self.validation_report,
            "elapsed_ms": self.elapsed_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TurnMetadata:
        return cls(
            turn_index=int(data.get("turn_index", 0)),
            turn_type=str(data.get("turn_type", "")),
            auto_fixes_applied=list(data.get("auto_fixes_applied", [])),
            validation_report=data.get("validation_report"),
            elapsed_ms=int(data.get("elapsed_ms", 0)),
        )


@dataclass(slots=True)
class ConversationContext:
    conversation_id: str
    model_identity: str
    pass_name: str
    messages: list[dict[str, str]]
    turn_count: int
    initial_system_prompt: str
    initial_payload: dict[str, Any]
    turn_metadata: list[TurnMetadata] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        model_identity: str,
        pass_name: str,
        system_prompt: str,
        user_payload: dict[str, Any],
    ) -> ConversationContext:
        conversation_id = sha256_json(
            {
                "model_identity": model_identity,
                "system_prompt_hash": sha256_text(system_prompt),
                "user_payload_hash": sha256_json(user_payload),
            }
        )
        return cls(
            conversation_id=conversation_id,
            model_identity=model_identity,
            pass_name=pass_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _serialize_payload(user_payload)},
            ],
            turn_count=0,
            initial_system_prompt=system_prompt,
            initial_payload=user_payload,
        )

    def record_turn(
        self,
        assistant_response: str,
        metadata: TurnMetadata,
    ) -> None:
        self.messages.append({"role": "assistant", "content": assistant_response})
        self.turn_count += 1
        self.turn_metadata.append(metadata)

    def append_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "model_identity": self.model_identity,
            "pass_name": self.pass_name,
            "messages": [dict(m) for m in self.messages],
            "turn_count": self.turn_count,
            "initial_system_prompt": self.initial_system_prompt,
            "initial_payload": self.initial_payload,
            "turn_metadata": [tm.to_dict() for tm in self.turn_metadata],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationContext:
        return cls(
            conversation_id=str(data.get("conversation_id", "")),
            model_identity=str(data.get("model_identity", "")),
            pass_name=str(data.get("pass_name", "")),
            messages=[dict(m) for m in data.get("messages", [])],
            turn_count=int(data.get("turn_count", 0)),
            initial_system_prompt=str(data.get("initial_system_prompt", "")),
            initial_payload=dict(data.get("initial_payload", {})),
            turn_metadata=[
                TurnMetadata.from_dict(tm) for tm in data.get("turn_metadata", [])
            ],
        )


def _serialize_payload(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)
