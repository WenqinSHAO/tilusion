from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib import resources
from typing import Any

from .extraction import (
    LOCAL_BUNDLE_SYSTEM_PROMPT,
    PROMPT_RESOURCE,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    sha256_text,
)


OVERVIEW_PROMPT_VERSION = "overview-segmentation-v0.1"
OVERVIEW_PROMPT_RESOURCE = "overview_segmentation_v0.1.md"
UNIT_FINALIZATION_PROMPT_VERSION = "unit-finalization-v0.3"
UNIT_FINALIZATION_PROMPT_RESOURCE = "unit_finalization_v0.3.md"
UNIT_REPAIR_PROMPT_VERSION = "unit-repair-v0.1"
UNIT_REPAIR_PROMPT_RESOURCE = "unit_repair_v0.1.md"
UNIT_TIMELINE_PROMPT_VERSION = "unit-timeline-v0.3"
UNIT_TIMELINE_PROMPT_RESOURCE = "unit_timeline_v0.3.md"
UNIT_TIMELINE_REPAIR_PROMPT_VERSION = "unit-timeline-repair-v0.1"
UNIT_TIMELINE_REPAIR_PROMPT_RESOURCE = "unit_timeline_repair_v0.1.md"


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


def build_segment_extraction_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        PromptPart(
            part_id="segment-extraction-contract",
            role="static_task_contract",
            source=f"resource:tilusion.prompts/{PROMPT_RESOURCE}",
            content=LOCAL_BUNDLE_SYSTEM_PROMPT,
            metadata={"prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION},
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=PROMPT_VERSION, parts=parts)


def build_overview_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "overview-segmentation-contract",
            role="static_task_contract",
            resource_name=OVERVIEW_PROMPT_RESOURCE,
            metadata={"prompt_version": OVERVIEW_PROMPT_VERSION},
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=OVERVIEW_PROMPT_VERSION, parts=parts)


def build_unit_finalization_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "unit-finalization-contract",
            role="static_task_contract",
            resource_name=UNIT_FINALIZATION_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_FINALIZATION_PROMPT_VERSION},
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=UNIT_FINALIZATION_PROMPT_VERSION, parts=parts)


def build_unit_repair_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "unit-finalization-contract",
            role="static_task_contract",
            resource_name=UNIT_FINALIZATION_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_FINALIZATION_PROMPT_VERSION},
        ),
        load_static_prompt_part(
            "unit-repair-instructions",
            role="generated_repair_instructions",
            resource_name=UNIT_REPAIR_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_REPAIR_PROMPT_VERSION},
        ),
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=UNIT_REPAIR_PROMPT_VERSION, parts=parts)


def build_unit_timeline_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "unit-finalization-contract",
            role="static_task_contract",
            resource_name=UNIT_FINALIZATION_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_FINALIZATION_PROMPT_VERSION},
        ),
        load_static_prompt_part(
            "unit-repair-instructions",
            role="generated_repair_instructions",
            resource_name=UNIT_REPAIR_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_REPAIR_PROMPT_VERSION},
        ),
        load_static_prompt_part(
            "unit-timeline-instructions",
            role="generated_timeline_instructions",
            resource_name=UNIT_TIMELINE_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_TIMELINE_PROMPT_VERSION},
        ),
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=UNIT_TIMELINE_PROMPT_VERSION, parts=parts)


def build_unit_timeline_repair_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "unit-finalization-contract",
            role="static_task_contract",
            resource_name=UNIT_FINALIZATION_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_FINALIZATION_PROMPT_VERSION},
        ),
        load_static_prompt_part(
            "unit-repair-instructions",
            role="generated_repair_instructions",
            resource_name=UNIT_REPAIR_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_REPAIR_PROMPT_VERSION},
        ),
        load_static_prompt_part(
            "unit-timeline-instructions",
            role="generated_timeline_instructions",
            resource_name=UNIT_TIMELINE_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_TIMELINE_PROMPT_VERSION},
        ),
        load_static_prompt_part(
            "unit-timeline-repair-instructions",
            role="generated_timeline_repair_instructions",
            resource_name=UNIT_TIMELINE_REPAIR_PROMPT_RESOURCE,
            metadata={"prompt_version": UNIT_TIMELINE_REPAIR_PROMPT_VERSION},
        ),
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=UNIT_TIMELINE_REPAIR_PROMPT_VERSION, parts=parts)


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
