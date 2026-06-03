from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from .backend import sha256_json, sha256_text


# ── Prompt composition ──


@dataclass(slots=True)
class PromptPart:
    part_id: str
    role: str
    source: str
    content: str
    generated_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return sha256_text(self.content)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["content_hash"] = self.content_hash
        return data


@dataclass(slots=True)
class PromptComposition:
    composition_id: str
    parts: list[PromptPart]

    @property
    def content(self) -> str:
        if len(self.parts) == 1:
            return self.parts[0].content
        sections = []
        for part in self.parts:
            sections.append(f"<!-- prompt-part:{part.part_id} role:{part.role} -->\n{part.content}")
        return "\n\n".join(sections)

    @property
    def content_hash(self) -> str:
        return sha256_text(self.content)

    def to_dict(self) -> dict[str, Any]:
        return {
            "composition_id": self.composition_id,
            "content_hash": self.content_hash,
            "parts": [part.to_dict() for part in self.parts],
        }


def generated_prompt_part(
    part_id: str,
    *,
    role: str,
    content: str,
    generated_by: str,
    metadata: dict[str, Any] | None = None,
) -> PromptPart:
    return PromptPart(
        part_id=part_id,
        role=role,
        source="generated",
        content=content,
        generated_by=generated_by,
        metadata=metadata or {},
    )


def load_static_prompt_part(
    part_id: str,
    *,
    role: str,
    resource_name: str,
    metadata: dict[str, Any] | None = None,
) -> PromptPart:
    content = resources.files("tilusion.prompts").joinpath(resource_name).read_text(encoding="utf-8")
    return PromptPart(
        part_id=part_id,
        role=role,
        source=f"resource:tilusion.prompts/{resource_name}",
        content=content,
        metadata=metadata or {},
    )


# ── Pass cache ──


def build_pass_cache_key(
    *,
    pass_name: str,
    prompt: PromptComposition,
    user_payload: dict[str, Any],
    model_identity: str,
    cache_context: dict[str, Any] | None = None,
) -> str:
    key_payload = {
        "pass_name": pass_name,
        "prompt_composition": prompt.to_dict(),
        "user_payload_hash": sha256_json(user_payload),
        "model_identity": model_identity,
    }
    if cache_context:
        key_payload["cache_context"] = cache_context
    return sha256_json(key_payload)


# ── Artifact paths ──


def pass_artifact_paths(pass_dir: Path) -> dict[str, str]:
    return {
        "manifest": str(pass_dir / "manifest.json"),
        "prompt_composition": str(pass_dir / "prompt_composition.json"),
        "system_prompt": str(pass_dir / "system_prompt.md"),
        "request_payload": str(pass_dir / "request_payload.json"),
        "raw_response": str(pass_dir / "raw_response.txt"),
        "result": str(pass_dir / "result.json"),
        "validation_report": str(pass_dir / "validation_report.json"),
        "validated_result": str(pass_dir / "validated_result.json"),
        "conversation": str(pass_dir / "conversation.json"),
        "selection_trace": str(pass_dir / "selection_trace.json"),
        "agentic_trace": str(pass_dir / "agentic_trace.json"),
        "pre_fallback_conversation": str(pass_dir / "pre_fallback_conversation.json"),
    }


def json_pass_artifact_paths(pass_dir: Path) -> dict[str, str]:
    return {
        "manifest": str(pass_dir / "manifest.json"),
        "prompt_composition": str(pass_dir / "prompt_composition.json"),
        "system_prompt": str(pass_dir / "system_prompt.md"),
        "request_payload": str(pass_dir / "request_payload.json"),
        "raw_response": str(pass_dir / "raw_response.txt"),
        "result": str(pass_dir / "result.json"),
        "validation_report": str(pass_dir / "validation_report.json"),
    }


def text_length_stats(text: str) -> dict[str, int]:
    return {
        "chars": len(text),
        "utf8_bytes": len(text.encode("utf-8")),
        "lines": len(text.splitlines()),
        "nonempty_lines": sum(1 for line in text.splitlines() if line.strip()),
    }
