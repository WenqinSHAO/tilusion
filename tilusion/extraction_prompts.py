from __future__ import annotations

from .pass_utils import PromptComposition, PromptPart, load_static_prompt_part

OVERVIEW_PROMPT_VERSION = "overview-segmentation-v0.2"
OVERVIEW_PROMPT_RESOURCE = "overview_segmentation_v0.2.md"


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
