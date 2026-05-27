from __future__ import annotations

from typing import Any

from .reading_schema import READING_UNIT_SCHEMA_VERSION


def render_text_with_block_markers(
    segment_text: str,
    source_blocks: list[Any],
    segment_offset: int,
) -> str:
    """Render segment text with inline block boundary markers.

    Each block's text is wrapped as ``{block_id:block_type}...{/block_id}``.
    Blocks must be contiguous and cover the full segment text.
    """
    parts: list[str] = []
    cursor = 0

    for block in source_blocks:
        # block.start/end are unit-level offsets; convert to segment-local
        local_start = block.start - segment_offset
        local_end = block.end - segment_offset

        if local_start < 0 or local_end > len(segment_text):
            raise ValueError(
                f"Block {block.block_id} range [{local_start}, {local_end}) "
                f"exceeds segment bounds [0, {len(segment_text)})"
            )

        # Text between previous block and this one (should be empty for contiguous blocks)
        if local_start < cursor:
            raise ValueError(
                f"Block {block.block_id} overlaps with previous block at offset {local_start}"
            )
        if local_start > cursor:
            parts.append(segment_text[cursor:local_start])

        parts.append(f"{{{block.block_id}:{block.block_type}}}")
        parts.append(segment_text[local_start:local_end])
        parts.append(f"{{/{block.block_id}}}")
        cursor = local_end

    if cursor < len(segment_text):
        parts.append(segment_text[cursor:])

    return "".join(parts)


def _source_block_meta(block: Any) -> dict[str, Any]:
    """Extract metadata-only dict from a SourceBlock for LLM payloads."""
    return {
        "block_id": block.block_id,
        "block_type": block.block_type,
        "start": block.start,
        "end": block.end,
    }


def build_per_segment_extraction_payload(
    *,
    unit_id: str,
    segment: dict[str, Any],
    text: str,
    source_blocks: list[Any] | None = None,
    segment_offset: int = 0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blocks = source_blocks or []
    marked_text = (
        render_text_with_block_markers(text, blocks, segment_offset)
        if blocks
        else text
    )
    return {
        "task": "per_segment_extraction",
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "segment": segment,
        "source_blocks": [_source_block_meta(b) for b in blocks],
        "text": marked_text,
        "context": context or {},
    }


def build_unit_reading_finalization_payload(
    *,
    unit_id: str,
    source: dict[str, Any],
    segments: list[dict[str, Any]],
    source_spans: list[dict[str, Any]],
    source_blocks: list[dict[str, Any]],
    concept_mentions: list[dict[str, Any]],
    logical_groups: list[dict[str, Any]],
    links: list[dict[str, Any]],
    validation_reports: list[dict[str, Any]] | None = None,
    repair_hints: dict[str, Any] | None = None,
    context_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task": "unit_reading_finalization",
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "source": source,
        "segments": segments,
        "source_spans": source_spans,
        "source_blocks": source_blocks,
        "concept_mentions": concept_mentions,
        "logical_groups": logical_groups,
        "links": links,
        "validation_reports": validation_reports or [],
        "repair_hints": repair_hints or {},
        "context_metadata": context_metadata or {},
        "expected_output": {
            "schema_version": READING_UNIT_SCHEMA_VERSION,
            "core_fields": [
                "source_spans",
                "source_blocks",
                "concept_mentions",
                "logical_groups",
                "links",
                "derived_views",
                "unresolved_items",
            ],
            "forbidden_core_fields": [
                "entity_records",
                "location_records",
                "atom_records",
                "thread_records",
                "timelines",
            ],
        },
    }


def flatten_segment_results(segment_results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Flatten per-segment extraction results for unit finalization payloads."""
    concepts: list[dict[str, Any]] = []
    atomic_items: list[dict[str, Any]] = []
    for result in segment_results:
        concepts.extend(_list(result.get("concepts")))
        atomic_items.extend(_list(result.get("atomic_items")))
    return {
        "concepts": concepts,
        "atomic_items": atomic_items,
    }


def _list(value: Any) -> list[dict[str, Any]]:
    return value if isinstance(value, list) else []
