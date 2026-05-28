from __future__ import annotations

from typing import Any

import re

from .reading_schema import READING_UNIT_SCHEMA_VERSION


_CONCEPT_TYPE_NORMALIZATION = {
    "thing": "object",
    "substance": "object",
    "format": "object",
    "component": "technical_component",
    "technical_component": "technical_component",
    "work": "source",
    "collection": "source",
    "source_statement": "source",
    "condition": "theme",
    "phenomenon": "theme",
    "event_type": "theme",
    "concept": "theme",
    "role": "social_role",
    "relationship": "social_role",
}


def normalize_concept_type(value: Any) -> str:
    """Normalize known noisy concept type aliases into the coarse schema vocabulary."""
    raw = str(value or "").strip()
    if not raw:
        return "other"
    key = re.sub(r"[\s-]+", "_", raw.lower())
    return _CONCEPT_TYPE_NORMALIZATION.get(key, key)


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


def merge_segment_extraction_results(
    segment_results: list[dict[str, Any]],
    unit_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Flatten per-segment results, merge duplicate concepts, and stabilize IDs.

    Segment-local IDs are scoped to ``{segment_id}-concept-NNNN``, then
    concepts with the same (surface, normalized concept_type) are merged into a
    single unit-level concept with a clean sequential ``concept-NNNN`` ID.
    Atomic items get clean sequential ``item-NNNN`` IDs and their
    ``concept_refs`` are remapped to the merged concept IDs.

    Surfaces that appear with different types across segments are flagged
    in ``unresolved_items`` for later LLM review.
    """
    source_blocks: list[dict[str, Any]] = []
    concepts: list[dict[str, Any]] = []
    atomic_items: list[dict[str, Any]] = []
    unresolved_items: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_source_blocks: set[str] = set()

    # ── Phase 1: scope local IDs to segment-prefixed IDs ──
    for result in segment_results:
        segment_id = result.get("segment_id", "unknown-segment")
        concept_id_map: dict[str, str] = {}

        for block in _list(result.get("source_blocks")):
            block_id = block.get("block_id", "")
            if block_id and block_id not in seen_source_blocks:
                seen_source_blocks.add(block_id)
                source_blocks.append(dict(block))

        for w in result.get("warnings") or []:
            if isinstance(w, str) and w.strip():
                warnings.append(w)

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

    # ── Phase 2: group scoped concepts by (surface, normalized concept_type) ──
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for concept in concepts:
        normalized_type = normalize_concept_type(concept.get("concept_type", ""))
        concept["concept_type"] = normalized_type
        key = (concept.get("surface", ""), normalized_type)
        groups.setdefault(key, []).append(concept)

    # ── Phase 2.5: merge groups that share the same canonical_name ──
    # When the LLM assigns the same canonical_name to concepts with different
    # surface forms (e.g. 司马相如 appearing as 相如 and 长卿), the surface-group
    # key alone won't merge them. Canonical-name merging catches these.
    #
    # Build canonical_name → set of (surface, type) group keys.
    cname_to_keys: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for key, members in groups.items():
        for m in members:
            cname = m.get("canonical_name", "")
            if cname:
                cname_to_keys.setdefault((cname, key[1]), set()).add(key)

    for (_cname, _ctype), related_keys in cname_to_keys.items():
        if len(related_keys) > 1:
            sorted_keys = sorted(related_keys)
            primary = sorted_keys[0]
            for other_key in sorted_keys[1:]:
                if other_key in groups:
                    groups[primary].extend(groups.pop(other_key))

    # ── Phase 3: merge groups and detect ambiguous surfaces ──
    surfaces_to_types: dict[str, set[str]] = {}
    merged_concepts: list[dict[str, Any]] = []
    scoped_to_merged: dict[str, str] = {}
    concept_index = 0

    for (surface, concept_type), members in groups.items():
        concept_index += 1
        merged_id = f"concept-{concept_index:04d}"

        for member in members:
            scoped_to_merged[member["concept_id"]] = merged_id
            surfaces_to_types.setdefault(surface, set()).add(concept_type)

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

    # ── Phase 5: compute factual segment-merge counts ──
    concepts_before = len(concepts)
    concepts_after = len(merged_concepts)
    segment_merge_counts = {
        "source_blocks": len(source_blocks),
        "concepts_before_merge": concepts_before,
        "concepts_after_merge": concepts_after,
        "concept_merge_count": concepts_before - concepts_after,
        "atomic_items": len(stabilized_items),
        "unresolved_items": len(unresolved_items),
        "ambiguous_surface_count": sum(
            1 for u in unresolved_items if u.get("kind") == "ambiguous_concept_surface"
        ),
        "warning_count": len(warnings),
    }

    return {
        "source_blocks": source_blocks,
        "concepts": merged_concepts,
        "atomic_items": stabilized_items,
        "unresolved_items": unresolved_items,
        "warnings": warnings,
        "metrics": {"counts": {"segment_merge": segment_merge_counts}},
    }


def _pick_canonical_name(members: list[dict[str, Any]]) -> str:
    """Pick a deterministic canonical name from merged concept members.

    Returns the longest non-empty ``canonical_name`` across *members*,
    breaking ties alphabetically so the result does not depend on
    member list order (which is affected by Phase 2.5 set iteration).
    """
    candidates: set[str] = set()
    for m in members:
        v = m.get("canonical_name")
        if v:
            candidates.add(str(v))
    if not candidates:
        return ""
    # longest first, then alphabetically first on tie
    return sorted(candidates, key=lambda n: (-len(n), n))[0]


def _merge_concept_group(
    merged_id: str,
    surface: str,
    concept_type: str,
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge a group of concepts sharing the same (surface, normalized concept_type)."""
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

    canonical = _pick_canonical_name(members)

    return {
        "concept_id": merged_id,
        "surface": canonical or surface,
        "concept_type": concept_type,
        "canonical_name": canonical,
        "summary": _first_nonempty("summary"),
        "aliases": _union("aliases"),
        "observed_surfaces": _union("observed_surfaces"),
        "source_block_refs": _union("source_block_refs"),
        "facets": _union("facets"),
        "uncertainty": _union("uncertainty"),
        "merged_from": merged_from,
        "provenance": {"grounding": "synthesis", "created_by": "deterministic"},
    }


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
