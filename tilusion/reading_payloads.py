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
    unresolved_items: list[dict[str, Any]] | None = None,
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
        "unresolved_items": unresolved_items or [],
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


def build_unit_logical_grouping_payload(
    *,
    unit_id: str,
    unit_text: str,
    source: dict[str, Any],
    segments: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    atomic_items: list[dict[str, Any]],
    unresolved_items: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the payload for the unit logical grouping + concept delta pass.

    The full *unit_text* is placed early in the payload so that re-runs
    of this pass share a KV-cache prefix.
    """
    return {
        "task": "unit_logical_grouping",
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "unit_text": unit_text,
        "source": source,
        "segments": segments,
        "concepts": concepts,
        "atomic_items": atomic_items,
        "unresolved_items": unresolved_items or [],
        "context": context or {},
    }


def flatten_and_stabilize_segment_results(
    segment_results: list[dict[str, Any]],
    unit_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Flatten per-segment results, merge duplicate concepts, and stabilize IDs.

    Segment-local IDs are scoped to ``{segment_id}-concept-NNNN``, then
    concepts with the same (surface, concept_type) are merged into a
    single unit-level concept with a clean sequential ``concept-NNNN`` ID.
    Atomic items get clean sequential ``item-NNNN`` IDs and their
    ``concept_refs`` are remapped to the merged concept IDs.

    Surfaces that appear with different types across segments are flagged
    in ``unresolved_items`` for later LLM review.
    """
    concepts: list[dict[str, Any]] = []
    atomic_items: list[dict[str, Any]] = []
    unresolved_items: list[dict[str, Any]] = []

    # ── Phase 1: scope local IDs to segment-prefixed IDs ──
    for result in segment_results:
        segment_id = result.get("segment_id", "unknown-segment")
        concept_id_map: dict[str, str] = {}

        for concept in _list(result.get("concepts")):
            local_id = concept.get("concept_id", "")
            scoped_id = f"{segment_id}-{local_id}" if local_id else local_id
            concept_id_map[local_id] = scoped_id
            scoped = dict(concept)
            scoped["concept_id"] = scoped_id
            concepts.append(scoped)

        for item in _list(result.get("atomic_items")):
            local_id = item.get("item_id", "")
            scoped_id = f"{segment_id}-{local_id}" if local_id else local_id
            scoped = dict(item)
            scoped["item_id"] = scoped_id
            scoped["concept_refs"] = [
                concept_id_map.get(ref, ref) for ref in _list(item.get("concept_refs"))
            ]
            atomic_items.append(scoped)

    # ── Phase 2: group scoped concepts by (surface, concept_type) ──
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for concept in concepts:
        key = (concept.get("surface", ""), concept.get("concept_type", ""))
        groups.setdefault(key, []).append(concept)

    # ── Phase 3: merge groups and detect ambiguous surfaces ──
    surfaces_to_types: dict[str, set[str]] = {}
    merged_concepts: list[dict[str, Any]] = []
    scoped_to_merged: dict[str, str] = {}
    concept_index = 0

    for (surface, concept_type), members in groups.items():
        surfaces_to_types.setdefault(surface, set()).add(concept_type)
        concept_index += 1
        merged_id = f"concept-{concept_index:04d}"

        for member in members:
            scoped_to_merged[member["concept_id"]] = merged_id

        merged = _merge_concept_group(merged_id, surface, concept_type, members)
        merged_concepts.append(merged)

    # ambiguous: same surface with different types
    for surface, types in surfaces_to_types.items():
        if len(types) > 1:
            scoped_concept_ids = [
                c["concept_id"]
                for c in concepts
                if c.get("surface") == surface and c.get("concept_type") in types
            ]
            unresolved_items.append(
                {
                    "item_id": f"unresolved-{len(unresolved_items) + 1:04d}",
                    "kind": "ambiguous_concept_surface",
                    "surface": surface,
                    "candidate_refs": scoped_concept_ids,
                    "candidate_types": sorted(types),
                    "summary": f"Surface '{surface}' appears with different types: {sorted(types)}.",
                }
            )

    # ── Phase 4: remap item concept_refs and reindex item IDs ──
    stabilized_items: list[dict[str, Any]] = []
    for i, item in enumerate(atomic_items):
        stabilized = dict(item)
        stabilized["concept_refs"] = [
            scoped_to_merged.get(ref, ref) for ref in _list(item.get("concept_refs"))
        ]
        stabilized["item_id"] = f"item-{i + 1:04d}"
        stabilized_items.append(stabilized)

    return {
        "concepts": merged_concepts,
        "atomic_items": stabilized_items,
        "unresolved_items": unresolved_items,
    }


def _merge_concept_group(
    merged_id: str,
    surface: str,
    concept_type: str,
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge a group of concepts sharing the same (surface, concept_type)."""
    merged_from = [m["concept_id"] for m in members]

    def _union(field: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for m in members:
            for v in _list(m.get(field)):
                if v not in seen:
                    seen.add(v)
                    result.append(v)
        return result

    def _first_nonempty(field: str, default: str = "") -> str:
        for m in members:
            v = m.get(field)
            if v:
                return v
        return default

    return {
        "concept_id": merged_id,
        "surface": surface,
        "concept_type": concept_type,
        "canonical_name": _first_nonempty("canonical_name"),
        "summary": _first_nonempty("summary"),
        "aliases": _union("aliases"),
        "observed_surfaces": _union("observed_surfaces"),
        "source_block_refs": _union("source_block_refs"),
        "facets": _union("facets"),
        "uncertainty": _union("uncertainty"),
        "merged_from": merged_from,
        "provenance": {"grounding": "synthesis", "created_by": "deterministic"},
    }


def _list(value: Any) -> list[dict[str, Any]]:
    return value if isinstance(value, list) else []
