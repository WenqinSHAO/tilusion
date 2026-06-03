from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .book_registry import BookRegistry, MergeRejectedError
from .reading_schema import (
    AtomicItem,
    Concept,
    GraphEdge,
    GraphNode,
    LogicalGroup,
    TemporalAttribute,
    normalize_concept_type,
)


@dataclass
class RegistryDeltaResult:
    """Deterministic diff of a unit extraction against the current BookRegistry."""

    source_index_id: str = ""
    operations: list[dict[str, Any]] = field(default_factory=list)
    ambiguity_items: list[dict[str, Any]] = field(default_factory=list)
    id_remap: dict[str, str] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)


def compute_registry_delta(
    unit_data: dict[str, Any],
    registry: BookRegistry,
    *,
    unit_id: str,
    concept_resolution_proposals: list[dict[str, Any]] | None = None,
    group_resolution_proposals: list[dict[str, Any]] | None = None,
) -> RegistryDeltaResult:
    """Compare unit extraction result against current BookRegistry state.

    When ``concept_resolution_proposals`` is provided, ``link`` proposals
    override the deterministic exact-match-only rule — LLM-confirmed
    cross-unit identity allows merging concepts that share only surface
    or semantic similarity without exact canonical_name match.

    Args:
        unit_data: Unit package data with ``concepts``, ``atomic_items``,
                   ``logical_groups``, and ``unresolved_items`` keys.
        registry: Current BookRegistry state.
        unit_id: The unit being processed.
        concept_resolution_proposals: Optional LLM resolution proposals.
            ``link`` proposals override deterministic matching.
        group_resolution_proposals: Optional LLM group resolution proposals.
            ``continue`` proposals emit ``continue_group`` operations.

    Returns:
        ``RegistryDeltaResult`` with safe operations, ambiguity items,
        unit→book ID remapping, and operation counts.
    """
    result = RegistryDeltaResult(source_index_id=_source_index_id_from_unit_data(unit_data))
    if result.source_index_id:
        registry.ensure_source_index_id(result.source_index_id)
    stats: dict[str, int] = {}

    unit_concepts_dicts: list[dict[str, Any]] = unit_data.get("concepts", [])
    unit_items_dicts: list[dict[str, Any]] = unit_data.get("atomic_items", [])
    unit_groups_dicts: list[dict[str, Any]] = unit_data.get("logical_groups", [])
    unresolved: list[dict[str, Any]] = unit_data.get("unresolved_items", [])

    # Build link lookup: unit_concept_id → registry_concept_id
    llm_links: dict[str, str] = {}
    for prop in (concept_resolution_proposals or []):
        if prop.get("proposal_type") == "link" and prop.get("registry_ref"):
            for ref in prop.get("target_refs", []):
                llm_links[ref] = prop["registry_ref"]

    # Build group continuation lookup: unit_group_id → registry_group_id
    group_continues: dict[str, str] = {}
    group_mutates: dict[str, str] = {}
    for prop in (group_resolution_proposals or []):
        if prop.get("unit_group_ref") and prop.get("registry_group_ref"):
            if prop.get("proposal_type") == "continue":
                group_continues[prop["unit_group_ref"]] = prop["registry_group_ref"]
            elif prop.get("proposal_type") == "mutate":
                group_mutates[prop["unit_group_ref"]] = prop["registry_group_ref"]

    # ── Concepts: resolve identity against registry ──────────────────────
    for uc in unit_concepts_dicts:
        uc_id = uc.get("concept_id", "")

        # 1) LLM link override: confirmed cross-unit identity
        if uc_id in llm_links:
            existing_id = llm_links[uc_id]
            # Ensure the unit concept carries a canonical_name for the
            # deterministic merge validator. If the registry concept has none,
            # auto-populate from the unit concept so both share an identity
            # signal (fixes "merging distinct time_anchor concepts with
            # different surfaces" when the LLM correctly links same-entity
            # concepts whose registry entry lacks a canonical_name).
            uc_cname = uc.get("canonical_name", "")
            if uc_cname:
                reg_concept = registry.get_concept(existing_id)
                if reg_concept is not None and not reg_concept.canonical_name:
                    uc = dict(uc)
                    uc["canonical_name"] = uc_cname
            result.id_remap[uc_id] = existing_id
            stats["merge_concepts"] = stats.get("merge_concepts", 0) + 1
            result.operations.append({
                "op_type": "merge_concepts",
                "unit_concept": uc,
                "book_concept_id": existing_id,
                "match_reason": "llm_link_proposal",
                "unit_id": unit_id,
            })
            continue

        # 2) Deterministic exact match
        concept = _dict_to_concept(uc, source_unit=unit_id)
        collisions = registry.find_collisions(concept)
        exact_matches = [c for c in collisions if c.match_reason == "exact_match"]

        if exact_matches:
            existing_id = exact_matches[0].existing_concept_id
            result.id_remap[uc_id] = existing_id
            stats["merge_concepts"] = stats.get("merge_concepts", 0) + 1
            result.operations.append({
                "op_type": "merge_concepts",
                "unit_concept": uc,
                "book_concept_id": existing_id,
                "match_reason": "exact_match",
                "unit_id": unit_id,
            })
        elif collisions:
            result.id_remap[uc_id] = uc_id
            stats["ambiguity_item"] = stats.get("ambiguity_item", 0) + 1
            for col in collisions:
                result.ambiguity_items.append({
                    "kind": "identity_ambiguity",
                    "unit_id": unit_id,
                    "unit_concept_id": uc_id,
                    "unit_surface": uc.get("surface", ""),
                    "unit_canonical_name": uc.get("canonical_name", ""),
                    "unit_concept_type": uc.get("concept_type", ""),
                    "book_concept_id": col.existing_concept_id,
                    "match_reason": col.match_reason,
                    "match_details": col.match_details,
                })
        else:
            result.id_remap[uc_id] = None
            stats["add_concept"] = stats.get("add_concept", 0) + 1
            result.operations.append({
                "op_type": "add_concept",
                "concept": uc,
                "unit_id": unit_id,
            })

    # ── Items: always new, remap concept_refs ────────────────────────────
    for item in unit_items_dicts:
        stats["add_item"] = stats.get("add_item", 0) + 1
        result.operations.append({
            "op_type": "add_item",
            "item": item,
            "unit_id": unit_id,
        })

    # ── Groups: new or continued, remap concept_refs ──────────────────────
    for group in unit_groups_dicts:
        group_id = group.get("group_id", "")
        if group_id in group_continues:
            stats["continue_group"] = stats.get("continue_group", 0) + 1
            result.operations.append({
                "op_type": "continue_group",
                "group": group,
                "book_group_id": group_continues[group_id],
                "unit_id": unit_id,
            })
        elif group_id in group_mutates:
            stats["mutate_group"] = stats.get("mutate_group", 0) + 1
            result.operations.append({
                "op_type": "mutate_group",
                "group": group,
                "book_group_id": group_mutates[group_id],
                "unit_id": unit_id,
            })
        else:
            stats["add_group"] = stats.get("add_group", 0) + 1
            result.operations.append({
                "op_type": "add_group",
                "group": group,
                "unit_id": unit_id,
            })

    # ── Unresolved items: carry forward as ambiguity items ───────────────
    for ui in unresolved:
        result.ambiguity_items.append({
            **ui,
            "source": "unit_unresolved",
            "unit_id": unit_id,
        })

    result.stats = stats
    return result


def apply_registry_delta(
    registry: BookRegistry,
    delta: RegistryDeltaResult,
) -> list[str]:
    """Apply safe operations from a delta to the BookRegistry.

    Returns the list of op_ids (concept/item/group IDs) that were applied.
    Ambiguity items are not applied — they are informational only.

    For ``merge_concepts`` operations, the unit concept is first added via
    ``force=True`` (bypassing collision checks) then immediately merged
    with the existing book concept via ``DeterministicConceptMerger``.
    """
    if delta.source_index_id:
        registry.ensure_source_index_id(delta.source_index_id)

    applied_ids: list[str] = []

    for op in delta.operations:
        op_type = op["op_type"]

        if op_type == "merge_concepts":
            unit_concept_dict = op["unit_concept"]
            book_concept_id = op["book_concept_id"]
            unit_concept = _dict_to_concept(
                unit_concept_dict, source_unit=op.get("unit_id", "?")
            )
            # Add unit concept with force=True (bypasses collision check)
            new_id, _ = registry.add_concept(unit_concept, force=True)
            # Merge into existing book concept
            try:
                merged_id = registry.merge_concepts([book_concept_id, new_id])
            except MergeRejectedError:
                # Deterministic boundary check rejected the merge — keep the
                # force-added concept as a distinct entry.  The rejection is
                # already logged by _check_merge_boundary.
                import sys
                print(
                    f"  [registry-delta] merge_rejected: keeping unit concept "
                    f"{unit_concept_dict.get('concept_id', '?')} as distinct "
                    f"(book {book_concept_id} → new {new_id})",
                    file=sys.stderr,
                )
                delta.id_remap[unit_concept_dict["concept_id"]] = new_id
                delta.ambiguity_items.append({
                    "kind": "merge_rejected",
                    "unit_id": op.get("unit_id", "?"),
                    "unit_concept_id": unit_concept_dict.get("concept_id", ""),
                    "unit_surface": unit_concept_dict.get("surface", ""),
                    "book_concept_id": book_concept_id,
                    "new_concept_id": new_id,
                })
                delta.stats["merge_rejected"] = delta.stats.get("merge_rejected", 0) + 1
                applied_ids.append(new_id)
                continue
            delta.id_remap[unit_concept_dict["concept_id"]] = merged_id
            applied_ids.append(merged_id)

        elif op_type == "add_concept":
            concept_dict = op["concept"]
            concept = _dict_to_concept(
                concept_dict, source_unit=op.get("unit_id", "?")
            )
            new_id, collision = registry.add_concept(concept)
            if collision is not None:
                # Should not happen for new concepts, but handle gracefully
                delta.id_remap[concept_dict["concept_id"]] = collision.existing_concept_id
            else:
                delta.id_remap[concept_dict["concept_id"]] = new_id
            applied_ids.append(new_id)

        elif op_type == "add_item":
            item_dict = op["item"]
            item_dict["concept_refs"] = _remap_refs(
                item_dict.get("concept_refs", []), delta.id_remap
            )
            item = AtomicItem(
                item_id="",  # registry assigns
                item_type=item_dict.get("item_type", "other"),
                summary=item_dict.get("summary", ""),
                source_block_refs=item_dict.get("source_block_refs", []),
                concept_refs=item_dict["concept_refs"],
                temporal_attributes=[
                    _dict_to_temporal_attribute(ta)
                    for ta in item_dict.get("temporal_attributes", [])
                ],
                attributes=item_dict.get("attributes", {}),
                uncertainty=item_dict.get("uncertainty", []),
                provenance=item_dict.get("provenance", {}),
            )
            item_id = registry.add_item(item)
            applied_ids.append(item_id)

        elif op_type == "add_group":
            group_dict = op["group"]
            group_dict["concept_refs"] = _remap_refs(
                group_dict.get("concept_refs", []), delta.id_remap
            )
            group = _dict_to_logical_group(group_dict)
            group_id = registry.add_group(group)
            applied_ids.append(group_id)

        elif op_type == "continue_group":
            # LLM-confirmed continuation: add group with reference to book group
            group_dict = op["group"]
            group_dict["concept_refs"] = _remap_refs(
                group_dict.get("concept_refs", []), delta.id_remap
            )
            group = _dict_to_logical_group(group_dict)
            group_id = registry.add_group(group)
            applied_ids.append(group_id)

        elif op_type == "mutate_group":
            # LLM-confirmed mutation: add group with reference to book group
            group_dict = op["group"]
            group_dict["concept_refs"] = _remap_refs(
                group_dict.get("concept_refs", []), delta.id_remap
            )
            group = _dict_to_logical_group(group_dict)
            group_id = registry.add_group(group)
            applied_ids.append(group_id)

    return applied_ids


def _source_index_id_from_unit_data(unit_data: dict[str, Any]) -> str:
    context_metadata = unit_data.get("context_metadata")
    if isinstance(context_metadata, dict):
        return str(context_metadata.get("source_index_id") or "")
    return ""


def _dict_to_concept(d: dict[str, Any], *, source_unit: str) -> Concept:
    """Convert a unit concept dict to a Concept, injecting source_unit."""
    provenance = dict(d.get("provenance", {}))
    provenance.setdefault("source_unit", source_unit)
    return Concept(
        concept_id=d.get("concept_id", ""),
        surface=d.get("surface", ""),
        concept_type=normalize_concept_type(d.get("concept_type", "other")),
        source_block_refs=list(d.get("source_block_refs", [])),
        canonical_name=d.get("canonical_name"),
        summary=d.get("summary", ""),
        aliases=list(d.get("aliases", [])),
        observed_surfaces=list(d.get("observed_surfaces", [])),
        facets=list(d.get("facets", [])),
        uncertainty=list(d.get("uncertainty", [])),
        provenance=provenance,
    )


def _dict_to_temporal_attribute(value: Any) -> TemporalAttribute:
    """Convert serialized temporal attributes back to schema objects."""
    if isinstance(value, TemporalAttribute):
        return value
    if not isinstance(value, dict):
        return TemporalAttribute(kind="none", uncertainty=[str(value)])
    return TemporalAttribute(
        kind=value.get("kind", "none"),
        surface=value.get("surface", ""),
        normalized_hint=value.get("normalized_hint", ""),
        source_block_ref=value.get("source_block_ref", ""),
        uncertainty=list(value.get("uncertainty", [])),
    )


def _dict_to_logical_group(d: dict[str, Any]) -> LogicalGroup:
    return LogicalGroup(
        group_id="",  # registry assigns
        group_type=d.get("group_type", "other"),
        summary=d.get("summary", ""),
        item_refs=list(d.get("item_refs", [])),
        concept_refs=list(d.get("concept_refs", [])),
        graph=_dict_to_graph(d.get("graph", {})),
        uncertainty=list(d.get("uncertainty", [])),
        provenance=d.get("provenance", {}),
    )


def _dict_to_graph(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"nodes": [], "edges": []}
    return {
        "nodes": [_dict_to_graph_node(node) for node in value.get("nodes", [])],
        "edges": [_dict_to_graph_edge(edge) for edge in value.get("edges", [])],
    }


def _dict_to_graph_node(value: Any) -> GraphNode:
    if isinstance(value, GraphNode):
        return value
    if not isinstance(value, dict):
        return GraphNode(node_id="", item_ref="", label=str(value))
    return GraphNode(
        node_id=value.get("node_id", ""),
        item_ref=value.get("item_ref", ""),
        label=value.get("label", ""),
    )


def _dict_to_graph_edge(value: Any) -> GraphEdge:
    if isinstance(value, GraphEdge):
        return value
    if not isinstance(value, dict):
        return GraphEdge(source="", target="", edge_type="other", summary=str(value))
    return GraphEdge(
        source=value.get("source", ""),
        target=value.get("target", ""),
        edge_type=value.get("edge_type", "other"),
        summary=value.get("summary", ""),
        source_block_refs=list(value.get("source_block_refs", [])),
        provenance=value.get("provenance", {}),
        uncertainty=list(value.get("uncertainty", [])),
    )


def _remap_refs(refs: list[str], id_remap: dict[str, str]) -> list[str]:
    """Remap unit-local concept IDs to book-scope IDs.

    Ref IDs not found in the remap dict are kept as-is (should not happen
    for well-formed unit data, but safe fallback).
    """
    return [id_remap.get(ref, ref) for ref in refs]
