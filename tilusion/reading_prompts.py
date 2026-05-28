from __future__ import annotations

from .pass_utils import PromptComposition, PromptPart, load_static_prompt_part
from .reading_schema import READING_UNIT_SCHEMA_VERSION


PER_SEGMENT_EXTRACTION_PROMPT_VERSION = "per-segment-extraction-v0.2"
PER_SEGMENT_EXTRACTION_PROMPT_RESOURCE = "prompt_per_segment_extraction_v0.2.md"
UNIT_LOGICAL_GROUPING_PROMPT_VERSION = "unit-logical-grouping-v0.1"
UNIT_LOGICAL_GROUPING_PROMPT_RESOURCE = "prompt_unit_logical_grouping_v0.1.md"


def build_per_segment_extraction_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    parts = [
        load_static_prompt_part(
            "per-segment-extraction-contract",
            role="static_task_contract",
            resource_name=PER_SEGMENT_EXTRACTION_PROMPT_RESOURCE,
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
