from __future__ import annotations

from .extraction_prompts import PromptComposition, PromptPart, load_static_prompt_part
from .reading_schema import READING_UNIT_SCHEMA_VERSION


SOURCE_BLOCK_CONCEPT_PROMPT_VERSION = "source-block-concept-v0.1"
SOURCE_BLOCK_CONCEPT_PROMPT_RESOURCE = "prompt_source_block_concept_v0.1.md"
LOGICAL_GROUP_PROMPT_VERSION = "logical-group-v0.1"
LOGICAL_GROUP_PROMPT_RESOURCE = "prompt_logical_group_v0.1.md"
LINK_STRUCTURE_PROMPT_VERSION = "link-structure-v0.1"
LINK_STRUCTURE_PROMPT_RESOURCE = "prompt_link_structure_v0.1.md"
UNIT_READING_FINALIZATION_PROMPT_VERSION = "unit-reading-finalization-v0.1"
UNIT_READING_FINALIZATION_PROMPT_RESOURCE = "prompt_unit_reading_finalization_v0.1.md"


def build_source_block_concept_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "source-block-concept-contract",
            role="static_task_contract",
            resource_name=SOURCE_BLOCK_CONCEPT_PROMPT_RESOURCE,
            metadata={
                "prompt_version": SOURCE_BLOCK_CONCEPT_PROMPT_VERSION,
                "schema_version": READING_UNIT_SCHEMA_VERSION,
            },
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=SOURCE_BLOCK_CONCEPT_PROMPT_VERSION, parts=parts)


def build_logical_group_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "logical-group-contract",
            role="static_task_contract",
            resource_name=LOGICAL_GROUP_PROMPT_RESOURCE,
            metadata={
                "prompt_version": LOGICAL_GROUP_PROMPT_VERSION,
                "schema_version": READING_UNIT_SCHEMA_VERSION,
            },
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=LOGICAL_GROUP_PROMPT_VERSION, parts=parts)


def build_link_structure_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "link-structure-contract",
            role="static_task_contract",
            resource_name=LINK_STRUCTURE_PROMPT_RESOURCE,
            metadata={
                "prompt_version": LINK_STRUCTURE_PROMPT_VERSION,
                "schema_version": READING_UNIT_SCHEMA_VERSION,
            },
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=LINK_STRUCTURE_PROMPT_VERSION, parts=parts)


def build_unit_reading_finalization_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "unit-reading-finalization-contract",
            role="static_task_contract",
            resource_name=UNIT_READING_FINALIZATION_PROMPT_RESOURCE,
            metadata={
                "prompt_version": UNIT_READING_FINALIZATION_PROMPT_VERSION,
                "schema_version": READING_UNIT_SCHEMA_VERSION,
            },
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=UNIT_READING_FINALIZATION_PROMPT_VERSION, parts=parts)
