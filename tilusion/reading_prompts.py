from __future__ import annotations

from importlib import resources

from .pass_utils import PromptComposition, PromptPart, load_static_prompt_part
from .prompt_contracts import (
    CONCEPT_RESOLUTION_CONTRACT,
    EXTRACTION_CONTRACT,
    GROUP_RESOLUTION_CONTRACT,
    GROUPING_CONTRACT,
    PassContract,
    apply_contract_to_prompt,
)
from .reading_schema import READING_UNIT_SCHEMA_VERSION


PER_SEGMENT_EXTRACTION_PROMPT_VERSION = "per-segment-extraction-v0.3"
PER_SEGMENT_EXTRACTION_PROMPT_RESOURCE = "prompt_per_segment_extraction_v0.3.md"
UNIT_LOGICAL_GROUPING_PROMPT_VERSION = "unit-logical-grouping-v0.1"
UNIT_LOGICAL_GROUPING_PROMPT_RESOURCE = "prompt_unit_logical_grouping_v0.1.md"
UNIT_LOGICAL_GROUPING_PROMPT_V0_2_VERSION = "unit-logical-grouping-v0.3"
UNIT_LOGICAL_GROUPING_PROMPT_V0_2_RESOURCE = "prompt_unit_grouping_v0.3.md"
CONCEPT_RESOLUTION_PROMPT_VERSION = "concept-resolution-v0.1"
CONCEPT_RESOLUTION_PROMPT_RESOURCE = "prompt_concept_resolution_v0.1.md"
GROUP_RESOLUTION_PROMPT_VERSION = "group-resolution-v0.1"
GROUP_RESOLUTION_PROMPT_RESOURCE = "prompt_group_resolution_v0.1.md"
CONCEPT_RESOLUTION_PROMPT_V0_2_VERSION = "concept-resolution-v0.3"
CONCEPT_RESOLUTION_PROMPT_V0_2_RESOURCE = "prompt_concept_resolution_v0.3.md"
GROUP_RESOLUTION_PROMPT_V0_2_VERSION = "group-resolution-v0.3"
GROUP_RESOLUTION_PROMPT_V0_2_RESOURCE = "prompt_group_resolution_v0.3.md"


def _load_with_contract(
    part_id: str,
    *,
    role: str,
    resource_name: str,
    contract: PassContract,
    metadata: dict | None = None,
) -> PromptPart:
    """Load a static prompt resource and substitute contract placeholders."""
    content = (
        resources.files("tilusion.prompts")
        .joinpath(resource_name)
        .read_text(encoding="utf-8")
    )
    content = apply_contract_to_prompt(content, contract)
    return PromptPart(
        part_id=part_id,
        role=role,
        source=f"resource:tilusion.prompts/{resource_name}",
        content=content,
        generated_by="prompt_contracts.apply_contract_to_prompt",
        metadata=metadata or {},
    )


def build_per_segment_extraction_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        _load_with_contract(
            "per-segment-extraction-contract",
            role="static_task_contract",
            resource_name=PER_SEGMENT_EXTRACTION_PROMPT_RESOURCE,
            contract=EXTRACTION_CONTRACT,
            metadata={
                "prompt_version": PER_SEGMENT_EXTRACTION_PROMPT_VERSION,
                "schema_version": READING_UNIT_SCHEMA_VERSION,
            },
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=PER_SEGMENT_EXTRACTION_PROMPT_VERSION, parts=parts)


def build_unit_logical_grouping_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "unit-logical-grouping-contract",
            role="static_task_contract",
            resource_name=UNIT_LOGICAL_GROUPING_PROMPT_RESOURCE,
            metadata={
                "prompt_version": UNIT_LOGICAL_GROUPING_PROMPT_VERSION,
                "schema_version": READING_UNIT_SCHEMA_VERSION,
            },
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=UNIT_LOGICAL_GROUPING_PROMPT_VERSION, parts=parts)


def build_unit_logical_grouping_v0_2_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        _load_with_contract(
            "unit-logical-grouping-contract",
            role="static_task_contract",
            resource_name=UNIT_LOGICAL_GROUPING_PROMPT_V0_2_RESOURCE,
            contract=GROUPING_CONTRACT,
            metadata={
                "prompt_version": UNIT_LOGICAL_GROUPING_PROMPT_V0_2_VERSION,
                "schema_version": READING_UNIT_SCHEMA_VERSION,
            },
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=UNIT_LOGICAL_GROUPING_PROMPT_V0_2_VERSION, parts=parts)


def build_concept_resolution_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "concept-resolution-contract",
            role="static_task_contract",
            resource_name=CONCEPT_RESOLUTION_PROMPT_RESOURCE,
            metadata={
                "prompt_version": CONCEPT_RESOLUTION_PROMPT_VERSION,
                "schema_version": READING_UNIT_SCHEMA_VERSION,
            },
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=CONCEPT_RESOLUTION_PROMPT_VERSION, parts=parts)


def build_group_resolution_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "group-resolution-contract",
            role="static_task_contract",
            resource_name=GROUP_RESOLUTION_PROMPT_RESOURCE,
            metadata={
                "prompt_version": GROUP_RESOLUTION_PROMPT_VERSION,
                "schema_version": READING_UNIT_SCHEMA_VERSION,
            },
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=GROUP_RESOLUTION_PROMPT_VERSION, parts=parts)


def build_concept_resolution_v0_2_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    from .pass_utils import generated_prompt_part
    from .registry_tools import render_tool_definitions_markdown

    parts = [
        _load_with_contract(
            "concept-resolution-contract",
            role="static_task_contract",
            resource_name=CONCEPT_RESOLUTION_PROMPT_V0_2_RESOURCE,
            contract=CONCEPT_RESOLUTION_CONTRACT,
            metadata={
                "prompt_version": CONCEPT_RESOLUTION_PROMPT_V0_2_VERSION,
                "schema_version": READING_UNIT_SCHEMA_VERSION,
            },
        )
    ]
    tool_md = render_tool_definitions_markdown(
        ["get_concept", "search_concepts", "get_source_block", "get_book_summary"]
    )
    parts.append(
        generated_prompt_part(
            "tool-definitions",
            role="tool_definitions",
            content=tool_md,
            generated_by="registry_tools.render_tool_definitions_markdown",
        )
    )
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(
        composition_id=CONCEPT_RESOLUTION_PROMPT_V0_2_VERSION, parts=parts
    )


def build_group_resolution_v0_2_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    from .pass_utils import generated_prompt_part
    from .registry_tools import render_tool_definitions_markdown

    parts = [
        _load_with_contract(
            "group-resolution-contract",
            role="static_task_contract",
            resource_name=GROUP_RESOLUTION_PROMPT_V0_2_RESOURCE,
            contract=GROUP_RESOLUTION_CONTRACT,
            metadata={
                "prompt_version": GROUP_RESOLUTION_PROMPT_V0_2_VERSION,
                "schema_version": READING_UNIT_SCHEMA_VERSION,
            },
        )
    ]
    tool_md = render_tool_definitions_markdown(
        [
            "get_concept",
            "get_group",
            "search_concepts",
            "search_groups",
            "get_source_block",
            "get_book_summary",
        ]
    )
    parts.append(
        generated_prompt_part(
            "tool-definitions",
            role="tool_definitions",
            content=tool_md,
            generated_by="registry_tools.render_tool_definitions_markdown",
        )
    )
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(
        composition_id=GROUP_RESOLUTION_PROMPT_V0_2_VERSION, parts=parts
    )
