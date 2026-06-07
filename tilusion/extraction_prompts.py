from __future__ import annotations

from importlib import resources

from .pass_utils import PromptComposition, PromptPart
from .prompt_contracts import OVERVIEW_CONTRACT, apply_contract_to_prompt

OVERVIEW_PROMPT_VERSION = "overview-segmentation-v0.3"
OVERVIEW_PROMPT_RESOURCE = "overview_segmentation_v0.3.md"


def build_overview_composition(
    generated_prompt_parts: list[PromptPart] | None = None,
) -> PromptComposition:
    content = (
        resources.files("tilusion.prompts")
        .joinpath(OVERVIEW_PROMPT_RESOURCE)
        .read_text(encoding="utf-8")
    )
    content = apply_contract_to_prompt(content, OVERVIEW_CONTRACT)
    parts = [
        PromptPart(
            part_id="overview-segmentation-contract",
            role="static_task_contract",
            source=f"resource:tilusion.prompts/{OVERVIEW_PROMPT_RESOURCE}",
            content=content,
            generated_by="prompt_contracts.apply_contract_to_prompt",
            metadata={"prompt_version": OVERVIEW_PROMPT_VERSION},
        )
    ]
    parts.extend(generated_prompt_parts or [])
    return PromptComposition(composition_id=OVERVIEW_PROMPT_VERSION, parts=parts)
