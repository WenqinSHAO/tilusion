from __future__ import annotations

import json
from typing import Any

from .extraction import LLMBackend
from .reading_schema import READING_UNIT_SCHEMA_VERSION


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _last_nonempty_line(text: str) -> str:
    result = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            result = stripped
    return result


# ── Mock response functions ──────────────────────────────────────────────────


def mock_per_segment_extraction_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    unit_id = user_payload.get("unit_id", "unit-0001")
    segment = user_payload.get("segment", {})
    segment_id = segment.get("segment_id", "seg-0001")
    text = user_payload.get("text", "")
    first = _first_nonempty_line(text)
    last = _last_nonempty_line(text)

    span_id = f"span-{segment_id}-0001"
    block_id = f"block-{segment_id}-0001"
    mention_id = f"mention-{segment_id}-0001"
    group_id = f"group-{segment_id}-0001"
    link_id = f"link-{segment_id}-0001"

    return {
        "unit_id": unit_id,
        "segment_id": segment_id,
        "source_spans": [
            {
                "span_id": span_id,
                "unit_id": unit_id,
                "source_range": {"kind": "segment-local-quote", "quote": first},
                "quote": first,
                "provenance": {"created_by": "llm", "pass": "per_segment_extraction"},
            }
        ],
        "source_blocks": [
            {
                "block_id": block_id,
                "block_type": "paragraph",
                "span_refs": [span_id],
                "source_order": 1,
                "confidence": "medium",
            }
        ],
        "concept_mentions": [
            {
                "mention_id": mention_id,
                "surface": first[:40] if first else "",
                "concept_type": "other",
                "local_summary": f"Mock concept from {segment_id}.",
                "source_block_refs": [block_id],
                "source_span_refs": [span_id],
                "confidence": "low",
                "facets": [],
                "uncertainty": [],
            }
        ],
        "logical_groups": [
            {
                "group_id": group_id,
                "group_type": "other",
                "summary": f"Mock group covering {segment_id}.",
                "source_block_refs": [block_id],
                "concept_refs": [mention_id],
                "source_order_hints": {"first_block": block_id, "last_block": block_id},
                "confidence": "low",
                "uncertainty": [],
                "provenance": {"grounding": "source_grounded", "created_by": "llm"},
            }
        ],
        "links": [
            {
                "link_id": link_id,
                "source_ref": group_id,
                "target_ref": mention_id,
                "link_type": "mentions",
                "evidence_block_refs": [block_id],
                "confidence": "low",
                "rationale": "Mock link.",
                "grounding": "source_grounded",
            }
        ],
        "warnings": ["mock per-segment extraction: placeholder records"],
    }


def mock_unit_reading_finalization_response(user_payload: dict[str, Any]) -> dict[str, Any]:
    unit_id = user_payload.get("unit_id", "unit-0001")
    source = user_payload.get("source", {})
    source_spans = user_payload.get("source_spans", [])
    source_blocks = user_payload.get("source_blocks", [])
    concept_mentions = user_payload.get("concept_mentions", [])
    logical_groups = user_payload.get("logical_groups", [])
    links = user_payload.get("links", [])
    context_metadata = user_payload.get("context_metadata", {})

    # Re-index records to unit-level IDs for the final package
    def _reindex(records: list[dict[str, Any]], prefix: str, id_field: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for i, rec in enumerate(records):
            updated = dict(rec)
            updated[id_field] = f"{prefix}{i + 1:04d}"
            if "confidence" not in updated:
                updated["confidence"] = "low"
            result.append(updated)
        return result

    final_spans = _reindex(source_spans, "span-", "span_id")
    final_blocks = _reindex(source_blocks, "block-", "block_id")
    final_concepts = _reindex(concept_mentions, "mention-", "mention_id")
    final_groups = _reindex(logical_groups, "group-", "group_id")

    # Re-wire link refs using the new ids
    span_map = _id_map(source_spans, final_spans, "span_id")
    block_map = _id_map(source_blocks, final_blocks, "block_id")
    mention_map = _id_map(concept_mentions, final_concepts, "mention_id")
    group_map = _id_map(logical_groups, final_groups, "group_id")

    final_links: list[dict[str, Any]] = []
    for i, link in enumerate(links):
        updated = dict(link)
        updated["link_id"] = f"link-{i + 1:04d}"
        updated["source_ref"] = group_map.get(link.get("source_ref", ""), link.get("source_ref", ""))
        updated["target_ref"] = (
            group_map.get(link.get("target_ref", ""))
            or mention_map.get(link.get("target_ref", ""))
            or block_map.get(link.get("target_ref", ""))
            or link.get("target_ref", "")
        )
        updated["evidence_block_refs"] = [block_map.get(ref, ref) for ref in link.get("evidence_block_refs", [])]
        if "confidence" not in updated:
            updated["confidence"] = "low"
        final_links.append(updated)

    # Re-wire refs in groups
    for group in final_groups:
        group["concept_refs"] = [mention_map.get(ref, ref) for ref in group.get("concept_refs", [])]
        group["source_block_refs"] = [block_map.get(ref, ref) for ref in group.get("source_block_refs", [])]
        hints = group.get("source_order_hints", {})
        if isinstance(hints, dict):
            for key in ("first_block", "last_block"):
                if key in hints:
                    hints[key] = block_map.get(hints[key], hints[key])

    # Re-wire refs in concepts
    for concept in final_concepts:
        concept["source_block_refs"] = [block_map.get(ref, ref) for ref in concept.get("source_block_refs", [])]
        concept["source_span_refs"] = [span_map.get(ref, ref) for ref in concept.get("source_span_refs", [])]

    # Re-wire refs in blocks
    for block in final_blocks:
        block["span_refs"] = [span_map.get(ref, ref) for ref in block.get("span_refs", [])]

    return {
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "source": source,
        "source_spans": final_spans,
        "source_blocks": final_blocks,
        "concept_mentions": final_concepts,
        "logical_groups": final_groups,
        "links": final_links,
        "derived_views": [],
        "unresolved_items": [],
        "validation": {"mock": True, "warnings": ["mock finalization: records re-indexed to unit-level IDs"]},
        "context_metadata": context_metadata,
    }


def _id_map(
    source_records: list[dict[str, Any]],
    target_records: list[dict[str, Any]],
    id_field: str,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for src, tgt in zip(source_records, target_records):
        src_id = src.get(id_field)
        tgt_id = tgt.get(id_field)
        if isinstance(src_id, str) and isinstance(tgt_id, str):
            mapping[src_id] = tgt_id
    return mapping


# ── Mock backend ─────────────────────────────────────────────────────────────


class MockReadingBackend:
    model_identity = "mock-reading-v0"

    def complete_json(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        task = user_payload.get("task", "")

        if task == "per_segment_extraction":
            return json.dumps(mock_per_segment_extraction_response(user_payload), ensure_ascii=False)
        if task == "unit_reading_finalization":
            return json.dumps(mock_unit_reading_finalization_response(user_payload), ensure_ascii=False)

        raise ValueError(f"MockReadingBackend: unknown task {task!r}")
