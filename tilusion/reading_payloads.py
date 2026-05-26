from __future__ import annotations

from typing import Any

from .reading_schema import READING_UNIT_SCHEMA_VERSION


def build_source_block_concept_payload(
    *,
    unit: dict[str, Any],
    region: dict[str, Any],
    text: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task": "source_block_concept",
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit": unit,
        "region": region,
        "context": context or {},
        "text": text,
    }


def build_logical_group_payload(
    *,
    unit_id: str,
    region_id: str,
    source_blocks: list[dict[str, Any]],
    concept_mentions: list[dict[str, Any]],
    text_window: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task": "logical_group",
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "region_id": region_id,
        "source_blocks": source_blocks,
        "concept_mentions": concept_mentions,
        "text_window": text_window,
        "context": context or {},
    }


def build_link_structure_payload(
    *,
    unit_id: str,
    scope_id: str,
    source_blocks: list[dict[str, Any]],
    concept_mentions: list[dict[str, Any]],
    logical_groups: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
    validation_notes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task": "link_structure",
        "schema_version": READING_UNIT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "scope_id": scope_id,
        "source_blocks": source_blocks,
        "concept_mentions": concept_mentions,
        "logical_groups": logical_groups,
        "context": context or {},
        "validation_notes": validation_notes or {},
    }


def build_unit_reading_finalization_payload(
    *,
    unit_id: str,
    source: dict[str, Any],
    regions: list[dict[str, Any]],
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
        "regions": regions,
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


def flatten_region_results(region_results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Flatten region-scoped reading pass results for unit finalization payloads."""
    source_spans: list[dict[str, Any]] = []
    source_blocks: list[dict[str, Any]] = []
    concept_mentions: list[dict[str, Any]] = []
    logical_groups: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for result in region_results:
        source_spans.extend(_list(result.get("source_spans")))
        source_blocks.extend(_list(result.get("source_blocks")))
        concept_mentions.extend(_list(result.get("concept_mentions")))
        logical_groups.extend(_list(result.get("logical_groups")))
        links.extend(_list(result.get("links")))
    return {
        "source_spans": source_spans,
        "source_blocks": source_blocks,
        "concept_mentions": concept_mentions,
        "logical_groups": logical_groups,
        "links": links,
    }


def _list(value: Any) -> list[dict[str, Any]]:
    return value if isinstance(value, list) else []
